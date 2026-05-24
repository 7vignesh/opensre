"""Vercel deployment method for opensre deploy --target vercel.

Deploys the OpenSRE webapp (FastAPI health app) as a set of Vercel serverless
functions. Suitable for chat and lightweight workloads only — long-running
investigations will exceed Vercel's execution time limits.

Platform limits (documented clearly per issue #273):
- Serverless function timeout: 10s (Hobby) / 60s (Pro) / 900s (Enterprise)
- Request body size: 4.5 MB
- Response body size: 4.5 MB
- No persistent background processes
- Cold starts on infrequent traffic

These constraints make Vercel appropriate for:
- /health and /ok status endpoints
- Short chat interactions
- Lightweight webhook receivers

NOT appropriate for:
- Deep RCA investigations (can run 30s–5min+)
- Streaming SSE connections
- Long-running background tasks
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VERCEL_API_BASE = "https://api.vercel.com"
_DEFAULT_PROJECT_NAME = "opensre"
_DEPLOY_POLL_INTERVAL = 5
_DEPLOY_MAX_WAIT_ATTEMPTS = 120


@dataclass(frozen=True)
class VercelDeployResult:
    """Result of a Vercel deployment."""

    deployment_id: str
    url: str
    project_name: str
    state: str
    elapsed_seconds: float
    protection_disabled: bool = True


@dataclass(frozen=True)
class VercelDeployError:
    """Structured error from a failed Vercel deployment."""

    message: str
    suggestion: str


# --- Serverless function sources ---

_HEALTH_HANDLER = """\
from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({
            "status": "ok",
            "service": "opensre",
            "deployment": "vercel",
            "ok": True,
        })
        self.wfile.write(body.encode())
"""

_OK_HANDLER = """\
from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({"ok": True, "service": "opensre", "deployment": "vercel"})
        self.wfile.write(body.encode())
"""


def _build_headers(api_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }


def _team_params(team_id: str) -> dict[str, str]:
    if team_id:
        return {"teamId": team_id}
    return {}


def _disable_deployment_protection(
    client: httpx.Client,
    project_id: str,
    headers: dict[str, str],
    params: dict[str, str],
) -> bool:
    """Disable Vercel's SSO/Deployment Protection so endpoints are publicly accessible.

    Team-scoped projects have deployment protection enabled by default, which
    causes health check requests to receive 401/403 instead of reaching the
    serverless function.

    Returns:
        True if protection was successfully disabled (or not present).
        False if the request failed due to permissions or network errors.
    """
    try:
        resp = client.patch(
            f"{_VERCEL_API_BASE}/v9/projects/{project_id}",
            headers=headers,
            json={"ssoProtection": None},
            params=params,
        )
        if resp.status_code == 200:
            logger.debug("Deployment protection disabled for project %s", project_id)
            return True
        if resp.status_code in (401, 403):
            logger.warning(
                "Could not disable deployment protection for project %s "
                "(HTTP %d — token may lack project-patch permissions). "
                "The health check may fail with 401/403 for team-scoped projects.",
                project_id,
                resp.status_code,
            )
            return False
        logger.debug(
            "Deployment protection disable returned HTTP %d for project %s",
            resp.status_code,
            project_id,
        )
        # Non-200 but not a clear auth failure — treat as uncertain
        return resp.status_code < 400
    except httpx.HTTPError as exc:
        logger.warning(
            "Network error disabling deployment protection for project %s: %s. "
            "The health check may fail for team-scoped projects.",
            project_id,
            exc,
        )
        return False


def _ensure_project(
    client: httpx.Client,
    project_name: str,
    headers: dict[str, str],
    params: dict[str, str],
) -> tuple[str | None, bool]:
    """Create the Vercel project if it doesn't exist.

    Returns:
        Tuple of (project_id, protection_disabled). project_id is None on failure.
    """
    resp = client.get(
        f"{_VERCEL_API_BASE}/v9/projects/{project_name}",
        headers=headers,
        params=params,
    )
    if resp.status_code == 200:
        project_id: str = resp.json()["id"]
        protection_ok = _disable_deployment_protection(client, project_id, headers, params)
        return project_id, protection_ok

    if resp.status_code != 404:
        resp.raise_for_status()
        return None, False

    resp_create = client.post(
        f"{_VERCEL_API_BASE}/v10/projects",
        headers=headers,
        json={"name": project_name, "framework": None},
        params=params,
    )
    if resp_create.status_code in (200, 201):
        created_id: str = resp_create.json()["id"]
        protection_ok = _disable_deployment_protection(client, created_id, headers, params)
        return created_id, protection_ok

    if resp_create.status_code == 403:
        return None, False

    resp_create.raise_for_status()
    return None, False


def _create_deployment(
    client: httpx.Client,
    project_name: str,
    headers: dict[str, str],
    params: dict[str, str],
) -> dict[str, Any]:
    """Create a Vercel deployment with the OpenSRE serverless functions."""
    payload: dict[str, Any] = {
        "name": project_name,
        "files": [
            {"file": "api/health.py", "data": _HEALTH_HANDLER},
            {"file": "api/ok.py", "data": _OK_HANDLER},
        ],
        "builds": [
            {"src": "api/health.py", "use": "@vercel/python"},
            {"src": "api/ok.py", "use": "@vercel/python"},
        ],
        "routes": [
            {"src": "/health", "dest": "/api/health.py"},
            {"src": "/api/health", "dest": "/api/health.py"},
            {"src": "/ok", "dest": "/api/ok.py"},
            {"src": "/api/ok", "dest": "/api/ok.py"},
        ],
        "target": "production",
    }

    resp = client.post(
        f"{_VERCEL_API_BASE}/v13/deployments",
        headers=headers,
        json=payload,
        params=params,
    )
    return {"status_code": resp.status_code, "body": resp.json() if resp.status_code < 500 else {}}


def _wait_for_ready(
    client: httpx.Client,
    deployment_id: str,
    headers: dict[str, str],
    params: dict[str, str],
    max_attempts: int = _DEPLOY_MAX_WAIT_ATTEMPTS,
    poll_interval: float = _DEPLOY_POLL_INTERVAL,
) -> str:
    """Poll until deployment reaches READY, ERROR, or CANCELED."""
    for attempt in range(max_attempts):
        resp = client.get(
            f"{_VERCEL_API_BASE}/v13/deployments/{deployment_id}",
            headers=headers,
            params=params,
        )
        if resp.status_code == 200:
            state: str = resp.json().get("readyState", "UNKNOWN")
            if state == "READY":
                return state
            if state in ("ERROR", "CANCELED"):
                return state
        elif resp.status_code in (401, 403, 404):
            # Fast-fail on permanent client errors — retrying won't help.
            return f"HTTP_{resp.status_code}"

        if attempt < max_attempts - 1:
            time.sleep(poll_interval)

    return "TIMEOUT"


def deploy_to_vercel(
    *,
    api_token: str,
    team_id: str = "",
    project_name: str = _DEFAULT_PROJECT_NAME,
    dry_run: bool = False,
) -> VercelDeployResult | VercelDeployError:
    """Deploy OpenSRE to Vercel as serverless functions.

    Args:
        api_token: Vercel API token (from VERCEL_API_TOKEN).
        team_id: Optional Vercel team ID for team-scoped deployments.
        project_name: Vercel project name (default: "opensre").
        dry_run: If True, validate config without deploying.

    Returns:
        VercelDeployResult on success, VercelDeployError on failure.
    """
    if not api_token or not api_token.strip():
        return VercelDeployError(
            message="VERCEL_API_TOKEN is required.",
            suggestion="Set VERCEL_API_TOKEN in your environment or .env file. "
            "Create a token at https://vercel.com/account/tokens with "
            "Read/Write access to Projects and Deployments.",
        )

    api_token = api_token.strip()
    headers = _build_headers(api_token)
    params = _team_params(team_id.strip())

    if dry_run:
        # Validate credentials by listing projects
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    f"{_VERCEL_API_BASE}/v9/projects",
                    headers=headers,
                    params={**params, "limit": "1"},
                )
                if resp.status_code == 200:
                    return VercelDeployResult(
                        deployment_id="",
                        url="",
                        project_name=project_name,
                        state="DRY_RUN_OK",
                        elapsed_seconds=0.0,
                    )
                if resp.status_code in (401, 403):
                    return VercelDeployError(
                        message=f"Vercel API returned {resp.status_code}: invalid or expired token.",
                        suggestion="Check your VERCEL_API_TOKEN. Generate a new one at "
                        "https://vercel.com/account/tokens.",
                    )
                return VercelDeployError(
                    message=f"Vercel API returned unexpected status {resp.status_code}.",
                    suggestion="Check your network connection and Vercel API status.",
                )
        except httpx.HTTPError as exc:
            return VercelDeployError(
                message=f"Failed to reach Vercel API: {exc}",
                suggestion="Check your network connection.",
            )

    # Full deployment
    started = time.monotonic()
    try:
        with httpx.Client(timeout=60) as client:
            # 1. Ensure project exists
            project_id, protection_disabled = _ensure_project(client, project_name, headers, params)
            if project_id is None:
                return VercelDeployError(
                    message="Failed to create or access Vercel project.",
                    suggestion="Ensure your token has Read/Write access to Projects. "
                    "Create a new token at https://vercel.com/account/tokens.",
                )

            # 2. Create deployment
            result = _create_deployment(client, project_name, headers, params)
            if result["status_code"] == 403:
                error_msg = result["body"].get("error", {}).get("message", "permission denied")
                return VercelDeployError(
                    message=f"Deployment permission denied: {error_msg}",
                    suggestion="Ensure your token has Read/Write access to Deployments.",
                )
            if result["status_code"] >= 400:
                return VercelDeployError(
                    message=f"Vercel API returned {result['status_code']} during deployment.",
                    suggestion="Check the Vercel dashboard for details.",
                )

            deployment_id: str = result["body"].get("id", "")
            deployment_url_host: str = result["body"].get("url", "")

            if not deployment_id or not deployment_url_host:
                return VercelDeployError(
                    message="Vercel API response missing deployment ID or URL.",
                    suggestion="The API returned an unexpected response shape. "
                    "Check the Vercel dashboard for deployment status.",
                )

            deployment_url = f"https://{deployment_url_host}"

            # 3. Wait for READY
            state = _wait_for_ready(client, deployment_id, headers, params)

    except httpx.HTTPError as exc:
        return VercelDeployError(
            message=f"Network error during deployment: {exc}",
            suggestion="Check your network connection and try again.",
        )

    elapsed = time.monotonic() - started

    if state == "READY":
        return VercelDeployResult(
            deployment_id=deployment_id,
            url=deployment_url,
            project_name=project_name,
            state=state,
            elapsed_seconds=elapsed,
            protection_disabled=protection_disabled,
        )

    if state == "TIMEOUT":
        return VercelDeployError(
            message=f"Deployment {deployment_id} did not become ready within the timeout.",
            suggestion="Check the Vercel dashboard for build logs.",
        )

    return VercelDeployError(
        message=f"Deployment {deployment_id} entered state: {state}.",
        suggestion="Check the Vercel dashboard for build errors.",
    )
