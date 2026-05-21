"""Shared error-telemetry helper for service-client except blocks."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import requests

from app.utils.errors import report_exception


def _is_transient_vendor_error(exc: BaseException) -> bool:
    """Return True for transient vendor errors (429 rate-limit, 5xx server errors).

    Supports httpx.HTTPStatusError, requests.HTTPError, and any exception with
    a ``resp`` attribute carrying a ``status`` field (e.g. googleapiclient.errors.HttpError).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        sc = exc.response.status_code
        return sc == 429 or sc >= 500

    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        sc = exc.response.status_code
        return sc == 429 or sc >= 500

    # googleapiclient.errors.HttpError and similar: exc.resp.status
    resp = getattr(exc, "resp", None)
    if resp is not None:
        status = getattr(resp, "status", None)
        if isinstance(status, int):
            return status == 429 or status >= 500

    return False


def capture_service_error(
    exc: BaseException,
    *,
    logger: logging.Logger,
    integration: str,
    method: str,
    extras: dict[str, Any] | None = None,
) -> None:
    severity = "warning" if _is_transient_vendor_error(exc) else "error"
    merged_extras: dict[str, Any] = dict(extras) if extras else {}
    merged_extras.pop("surface", None)
    merged_extras["method"] = method
    report_exception(
        exc,
        logger=logger,
        message=f"[{integration}] {method} failed",
        severity=severity,
        tags={"surface": "service_client", "integration": integration},
        extras=merged_extras,
    )
