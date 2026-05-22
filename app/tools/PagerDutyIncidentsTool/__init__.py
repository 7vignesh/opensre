"""PagerDuty incident listing and search investigation tool."""

from __future__ import annotations

from typing import Any

from app.services.pagerduty import make_pagerduty_client
from app.tools.base import BaseTool

_OPEN_STATUSES = {"triggered", "acknowledged"}


class PagerDutyIncidentsTool(BaseTool):
    """List and search PagerDuty incidents to surface active incidents and their triage state."""

    name = "pagerduty_incidents"
    source = "pagerduty"
    description = (
        "Search PagerDuty incidents to find active incidents, identify unacknowledged high-urgency "
        "incidents, and correlate incident context with errors from other observability sources."
    )
    use_cases = [
        "Listing open PagerDuty incidents for an ongoing investigation",
        "Finding unacknowledged high-urgency incidents",
        "Correlating a PagerDuty incident with errors in Datadog or Sentry",
        "Checking recent incident history for a service",
    ]
    requires = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "PagerDuty REST API key"},
            "base_url": {
                "type": "string",
                "default": "",
                "description": "PagerDuty API base URL (defaults to https://api.pagerduty.com)",
            },
            "statuses": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by status: triggered, acknowledged, resolved",
            },
            "urgencies": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by urgency: high, low",
            },
            "service_ids": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by PagerDuty service IDs",
            },
            "since": {
                "type": "string",
                "default": "",
                "description": "Start of date range (ISO 8601 format)",
            },
            "until": {
                "type": "string",
                "default": "",
                "description": "End of date range (ISO 8601 format)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of incidents to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "incidents": "List of incidents with status, urgency, service, and timestamps",
        "open_incidents": "Subset of incidents in triggered or acknowledged state",
        "total": "Total number of incidents returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "statuses": [],
            "urgencies": [],
            "service_ids": [],
            "since": "",
            "until": "",
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        statuses: list[str] | None = None,
        urgencies: list[str] | None = None,
        service_ids: list[str] | None = None,
        since: str = "",
        until: str = "",
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return {
                "source": "pagerduty",
                "available": False,
                "error": "PagerDuty integration is not configured.",
                "incidents": [],
                "open_incidents": [],
                "total": 0,
            }

        with client:
            result = client.list_incidents(
                statuses=statuses or None,
                urgencies=urgencies or None,
                service_ids=service_ids or None,
                since=since,
                until=until,
                limit=limit,
            )

        if not result.get("success"):
            return {
                "source": "pagerduty",
                "available": False,
                "error": result.get("error", "unknown error"),
                "incidents": [],
                "open_incidents": [],
                "total": 0,
            }

        incidents = result.get("incidents", [])
        open_incidents = [i for i in incidents if i.get("status", "").lower() in _OPEN_STATUSES]
        return {
            "source": "pagerduty",
            "available": True,
            "incidents": incidents,
            "open_incidents": open_incidents,
            "total": result.get("total", len(incidents)),
            "has_more": result.get("has_more", False),
        }


pagerduty_incidents = PagerDutyIncidentsTool()
