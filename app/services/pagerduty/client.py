"""PagerDuty REST API client.

Wraps the PagerDuty Incidents API endpoints used for incident investigation and triage.
Credentials come from the user's PagerDuty integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.integrations.config_models import PagerDutyIntegrationConfig
from app.integrations.probes import ProbeResult
from app.services._error_helpers import capture_service_error

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
PagerDutyConfig = PagerDutyIntegrationConfig


class PagerDutyClient:
    """Synchronous client for querying the PagerDuty REST API."""

    def __init__(self, config: PagerDutyConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def probe_access(self) -> ProbeResult:
        """Validate PagerDuty credentials with a minimal incident list call."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API key.")

        with self:
            result = self.list_incidents(limit=1)
        if not result.get("success"):
            return ProbeResult.failed(
                f"Incident list check failed: {result.get('error', 'unknown error')}",
            )

        return ProbeResult.passed(
            f"Connected to PagerDuty; API key accepted ({self.config.base_url}).",
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> PagerDutyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_incidents(
        self,
        *,
        statuses: list[str] | None = None,
        urgencies: list[str] | None = None,
        service_ids: list[str] | None = None,
        since: str = "",
        until: str = "",
        limit: int = 25,
    ) -> dict[str, Any]:
        """List PagerDuty incidents, optionally filtered by status/urgency/service."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if statuses:
            params["statuses[]"] = statuses
        if urgencies:
            params["urgencies[]"] = urgencies
        if service_ids:
            params["service_ids[]"] = service_ids
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        try:
            resp = self._get_client().get("/incidents", params=params)
            resp.raise_for_status()
            data = resp.json()

            incidents = []
            for inc in data.get("incidents", []):
                incidents.append(self._normalize_incident(inc))

            return {
                "success": True,
                "incidents": incidents,
                "total": len(incidents),
                "has_more": data.get("more", False),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_incidents",
                extras={"statuses": statuses},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_incidents",
                extras={"statuses": statuses},
            )
            return {"success": False, "error": str(exc)}

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch full details for a specific PagerDuty incident."""
        try:
            resp = self._get_client().get(f"/incidents/{incident_id}")
            resp.raise_for_status()
            data = resp.json().get("incident", {})

            return {"success": True, "incident": self._normalize_incident_detail(data)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident",
                extras={"incident_id": incident_id},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident",
                extras={"incident_id": incident_id},
            )
            return {"success": False, "error": str(exc)}

    def get_incident_log_entries(self, incident_id: str, limit: int = 25) -> dict[str, Any]:
        """Fetch log entries for a specific PagerDuty incident."""
        params: dict[str, Any] = {"limit": min(limit, 100)}

        try:
            resp = self._get_client().get(f"/incidents/{incident_id}/log_entries", params=params)
            resp.raise_for_status()
            data = resp.json()

            log_entries = []
            for entry in data.get("log_entries", []):
                log_entries.append(
                    {
                        "id": entry.get("id", ""),
                        "type": entry.get("type", ""),
                        "summary": entry.get("summary", ""),
                        "created_at": entry.get("created_at", ""),
                        "agent": _extract_reference(entry.get("agent")),
                        "channel": entry.get("channel", {}),
                        "note": entry.get("note", ""),
                    }
                )

            return {
                "success": True,
                "log_entries": log_entries,
                "total": len(log_entries),
                "has_more": data.get("more", False),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident_log_entries",
                extras={"incident_id": incident_id},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident_log_entries",
                extras={"incident_id": incident_id},
            )
            return {"success": False, "error": str(exc)}

    def get_incident_notes(self, incident_id: str) -> dict[str, Any]:
        """Fetch notes for a specific PagerDuty incident."""
        try:
            resp = self._get_client().get(f"/incidents/{incident_id}/notes")
            resp.raise_for_status()
            data = resp.json()

            notes = []
            for note in data.get("notes", []):
                notes.append(
                    {
                        "id": note.get("id", ""),
                        "content": note.get("content", ""),
                        "created_at": note.get("created_at", ""),
                        "user": _extract_reference(note.get("user")),
                    }
                )

            return {"success": True, "notes": notes, "total": len(notes)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident_notes",
                extras={"incident_id": incident_id},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident_notes",
                extras={"incident_id": incident_id},
            )
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _normalize_incident(inc: dict[str, Any]) -> dict[str, Any]:
        """Normalize a PagerDuty incident from the list endpoint."""
        return {
            "id": inc.get("id", ""),
            "incident_number": inc.get("incident_number", 0),
            "title": inc.get("title", ""),
            "status": inc.get("status", ""),
            "urgency": inc.get("urgency", ""),
            "priority": _extract_priority(inc.get("priority")),
            "service": _extract_reference(inc.get("service")),
            "assigned_to": [
                _extract_reference(a.get("assignee")) for a in inc.get("assignments", [])
            ],
            "escalation_policy": _extract_reference(inc.get("escalation_policy")),
            "created_at": inc.get("created_at", ""),
            "updated_at": inc.get("updated_at", ""),
            "html_url": inc.get("html_url", ""),
        }

    @staticmethod
    def _normalize_incident_detail(inc: dict[str, Any]) -> dict[str, Any]:
        """Normalize a PagerDuty incident from the detail endpoint."""
        return {
            "id": inc.get("id", ""),
            "incident_number": inc.get("incident_number", 0),
            "title": inc.get("title", ""),
            "description": inc.get("description", ""),
            "status": inc.get("status", ""),
            "urgency": inc.get("urgency", ""),
            "priority": _extract_priority(inc.get("priority")),
            "service": _extract_reference(inc.get("service")),
            "assigned_to": [
                _extract_reference(a.get("assignee")) for a in inc.get("assignments", [])
            ],
            "escalation_policy": _extract_reference(inc.get("escalation_policy")),
            "teams": [_extract_reference(t) for t in inc.get("teams", [])],
            "acknowledgements": [
                {
                    "at": ack.get("at", ""),
                    "acknowledger": _extract_reference(ack.get("acknowledger")),
                }
                for ack in inc.get("acknowledgements", [])
            ],
            "last_status_change_at": inc.get("last_status_change_at", ""),
            "last_status_change_by": _extract_reference(inc.get("last_status_change_by")),
            "resolve_reason": inc.get("resolve_reason"),
            "alert_counts": inc.get("alert_counts", {}),
            "created_at": inc.get("created_at", ""),
            "updated_at": inc.get("updated_at", ""),
            "html_url": inc.get("html_url", ""),
        }


def _extract_reference(ref: dict[str, Any] | None) -> dict[str, str]:
    """Extract a PagerDuty object reference (id + summary)."""
    if not ref:
        return {}
    return {
        "id": ref.get("id", ""),
        "summary": ref.get("summary", ""),
    }


def _extract_priority(priority: dict[str, Any] | None) -> dict[str, str]:
    """Extract priority info from a PagerDuty incident."""
    if not priority:
        return {}
    return {
        "id": priority.get("id", ""),
        "name": priority.get("summary", priority.get("name", "")),
    }


def make_pagerduty_client(
    api_key: str | None, base_url: str | None = None
) -> PagerDutyClient | None:
    """Create a PagerDutyClient if a valid API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        return PagerDutyClient(PagerDutyConfig(api_key=token, base_url=base_url or ""))
    except (ValueError, TypeError):
        return None
