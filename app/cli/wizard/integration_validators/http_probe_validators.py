"""HTTP-probe onboarding integration validators."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.integrations.models import GoogleDocsIntegrationConfig, SlackWebhookConfig

from .shared import IntegrationHealthResult


def validate_slack_webhook(*, webhook_url: str) -> IntegrationHealthResult:
    """Validate Slack webhook format and do a non-posting reachability probe."""
    try:
        slack_config = SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    try:
        response = httpx.get(
            slack_config.webhook_url,
            timeout=10,
            follow_redirects=False,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Slack webhook validation failed: {err}")

    if response.status_code == 404:
        return IntegrationHealthResult(
            ok=False, detail="Slack webhook returned 404; the URL looks invalid."
        )
    if response.status_code in {200, 400, 403, 405}:
        return IntegrationHealthResult(
            ok=True,
            detail=f"Slack webhook endpoint reachable (HTTP {response.status_code}) using a non-posting probe.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Slack webhook probe returned unexpected HTTP {response.status_code}.",
    )


def validate_notion_integration(*, api_key: str, database_id: str) -> IntegrationHealthResult:
    """Validate Notion connectivity by querying the target database."""
    try:
        resp = httpx.get(
            f"https://api.notion.com/v1/databases/{database_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return IntegrationHealthResult(
                ok=True, detail="Notion database reachable and token valid."
            )
        if resp.status_code == 401:
            return IntegrationHealthResult(ok=False, detail="Notion API key is invalid or expired.")
        if resp.status_code == 404:
            return IntegrationHealthResult(
                ok=False,
                detail="Notion database not found. Check the database ID and sharing settings.",
            )
        return IntegrationHealthResult(
            ok=False, detail=f"Notion returned unexpected status {resp.status_code}."
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Notion validation failed: {err}")


def validate_google_docs_integration(
    *,
    credentials_file: str,
    folder_id: str,
) -> IntegrationHealthResult:
    """Validate Google Docs credentials and folder access."""
    from app.services.google_docs import GoogleDocsClient

    try:
        config = GoogleDocsIntegrationConfig.model_validate(
            {
                "credentials_file": credentials_file,
                "folder_id": folder_id,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    if not config.credentials_file or not config.folder_id:
        return IntegrationHealthResult(ok=False, detail="Missing credentials_file or folder_id.")

    if not Path(config.credentials_file).exists():
        return IntegrationHealthResult(
            ok=False, detail=f"Credentials file not found: {config.credentials_file}"
        )

    try:
        client = GoogleDocsClient(config)
        result = client.validate_access()
    except Exception as exc:
        return IntegrationHealthResult(ok=False, detail=f"Google API validation failed: {exc}")

    if not result.get("success"):
        return IntegrationHealthResult(
            ok=False, detail=f"Folder access check failed: {result.get('error', 'unknown error')}"
        )

    return IntegrationHealthResult(
        ok=True,
        detail=f"Connected to Drive folder {config.folder_id} ({result.get('file_count', 0)} items).",
    )


def validate_jira_integration(
    *, base_url: str, email: str, api_token: str, project_key: str
) -> IntegrationHealthResult:
    """Validate Jira connectivity and project key accessibility."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/rest/api/3/myself",
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            display = data.get("displayName") or data.get("emailAddress") or email

            project_resp = httpx.get(
                f"{base_url.rstrip('/')}/rest/api/3/project/{project_key}",
                auth=(email, api_token),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if project_resp.status_code == 404:
                return IntegrationHealthResult(
                    ok=False, detail=f"Project '{project_key}' not found. Check the project key."
                )
            if project_resp.status_code != 200:
                return IntegrationHealthResult(
                    ok=False,
                    detail=f"Could not verify project '{project_key}': HTTP {project_resp.status_code}.",
                )

            return IntegrationHealthResult(
                ok=True, detail=f"Jira connected as {display}, project '{project_key}' verified."
            )
        if resp.status_code == 401:
            return IntegrationHealthResult(
                ok=False, detail="Jira credentials invalid. Check email and API token."
            )
        if resp.status_code == 404:
            return IntegrationHealthResult(
                ok=False, detail="Jira base URL not found. Check the URL."
            )
        return IntegrationHealthResult(
            ok=False, detail=f"Jira returned unexpected status {resp.status_code}."
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Jira validation failed: {err}")


def validate_discord_bot(*, bot_token: str) -> IntegrationHealthResult:
    """Validate a Discord bot token by calling the /users/@me endpoint."""
    try:
        resp = httpx.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Discord API unreachable: {err}")

    if resp.status_code == 200:
        username = resp.json().get("username", "unknown")
        return IntegrationHealthResult(ok=True, detail=f"Discord bot authenticated as @{username}.")
    if resp.status_code == 401:
        return IntegrationHealthResult(ok=False, detail="Discord bot token is invalid or revoked.")
    return IntegrationHealthResult(
        ok=False, detail=f"Discord API returned unexpected HTTP {resp.status_code}."
    )
