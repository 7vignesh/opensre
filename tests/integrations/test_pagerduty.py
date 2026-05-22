"""PagerDuty integration tests.

Covers config resolution, verification, service client behavior, tool
availability/outputs, and catalog classification.
"""

from __future__ import annotations

import pytest

from app.integrations.config_models import PagerDutyIntegrationConfig
from app.integrations.probes import ProbeResult
from app.services.pagerduty import PagerDutyClient, PagerDutyConfig, make_pagerduty_client

# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------


class TestPagerDutyIntegrationConfig:
    def test_valid_config(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="test-key")
        assert config.api_key == "test-key"
        assert config.base_url == "https://api.pagerduty.com"

    def test_custom_base_url(self) -> None:
        config = PagerDutyIntegrationConfig(
            api_key="test-key", base_url="https://custom.pagerduty.com"
        )
        assert config.base_url == "https://custom.pagerduty.com"

    def test_empty_base_url_defaults(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="test-key", base_url="")
        assert config.base_url == "https://api.pagerduty.com"

    def test_headers_format(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="my-api-key")
        headers = config.headers
        assert headers["Authorization"] == "Token token=my-api-key"
        assert headers["Content-Type"] == "application/json"
        assert "application/vnd.pagerduty+json;version=2" in headers["Accept"]

    def test_model_validate(self) -> None:
        config = PagerDutyIntegrationConfig.model_validate(
            {"api_key": "key-123", "base_url": "https://api.pagerduty.com"}
        )
        assert config.api_key == "key-123"

    def test_api_key_stripped(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="  spaced-key  ")
        assert config.api_key == "spaced-key"


# ---------------------------------------------------------------------------
# Service client tests
# ---------------------------------------------------------------------------


class TestPagerDutyClient:
    def test_is_configured_with_key(self) -> None:
        config = PagerDutyConfig(api_key="test-key")
        client = PagerDutyClient(config)
        assert client.is_configured is True

    def test_is_configured_without_key(self) -> None:
        config = PagerDutyConfig(api_key="")
        client = PagerDutyClient(config)
        assert client.is_configured is False

    def test_probe_access_missing_key(self) -> None:
        config = PagerDutyConfig(api_key="")
        client = PagerDutyClient(config)
        result = client.probe_access()
        assert result.status == "missing"
        assert "Missing API key" in result.detail

    def test_probe_access_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = PagerDutyConfig(api_key="valid-key")
        client = PagerDutyClient(config)

        monkeypatch.setattr(
            client,
            "list_incidents",
            lambda **_kwargs: {"success": True, "incidents": [], "total": 0},
        )

        result = client.probe_access()
        assert result.status == "passed"
        assert "Connected to PagerDuty" in result.detail

    def test_probe_access_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = PagerDutyConfig(api_key="bad-key")
        client = PagerDutyClient(config)

        monkeypatch.setattr(
            client,
            "list_incidents",
            lambda **_kwargs: {"success": False, "error": "HTTP 401: unauthorized"},
        )

        result = client.probe_access()
        assert result.status == "failed"
        assert "401" in result.detail

    def test_context_manager(self) -> None:
        config = PagerDutyConfig(api_key="test-key")
        with PagerDutyClient(config) as client:
            assert client.is_configured


class TestMakePagerDutyClient:
    def test_returns_client_with_valid_key(self) -> None:
        client = make_pagerduty_client("valid-key")
        assert client is not None
        assert isinstance(client, PagerDutyClient)

    def test_returns_none_with_empty_key(self) -> None:
        assert make_pagerduty_client("") is None
        assert make_pagerduty_client(None) is None
        assert make_pagerduty_client("   ") is None

    def test_custom_base_url(self) -> None:
        client = make_pagerduty_client("key", "https://custom.pd.com")
        assert client is not None
        assert client.config.base_url == "https://custom.pd.com"


# ---------------------------------------------------------------------------
# Verification adapter tests
# ---------------------------------------------------------------------------


class TestVerifyPagerDuty:
    def test_verify_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations._verification_adapters import _verify_pagerduty

        monkeypatch.setattr(
            PagerDutyClient,
            "probe_access",
            lambda _self: ProbeResult.passed("Connected to PagerDuty; API key accepted."),
        )

        result = _verify_pagerduty(
            "local env",
            {"api_key": "valid-key", "base_url": "https://api.pagerduty.com"},
        )

        assert result["status"] == "passed"
        assert result["service"] == "pagerduty"

    def test_verify_fails_on_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations._verification_adapters import _verify_pagerduty

        monkeypatch.setattr(
            PagerDutyClient,
            "probe_access",
            lambda _self: ProbeResult.failed("HTTP 401: unauthorized"),
        )

        result = _verify_pagerduty(
            "local env",
            {"api_key": "bad-key", "base_url": ""},
        )

        assert result["status"] == "failed"
        assert "401" in result["detail"]

    def test_verify_missing_key(self) -> None:
        from app.integrations._verification_adapters import _verify_pagerduty

        result = _verify_pagerduty("local env", {"api_key": "", "base_url": ""})

        assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# Catalog classification tests
# ---------------------------------------------------------------------------


class TestCatalogClassification:
    def test_classify_pagerduty_with_valid_key(self) -> None:
        from app.integrations._catalog_impl import _classify_service_instance

        flat, key = _classify_service_instance(
            "pagerduty",
            {"api_key": "pd-key-123", "base_url": ""},
            record_id="pd-1",
        )

        assert key == "pagerduty"
        assert flat is not None
        assert flat["api_key"] == "pd-key-123"

    def test_classify_pagerduty_without_key(self) -> None:
        from app.integrations._catalog_impl import _classify_service_instance

        flat, key = _classify_service_instance(
            "pagerduty",
            {"api_key": "", "base_url": ""},
            record_id="pd-1",
        )

        assert flat is None
        assert key is None

    def test_env_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.verify import resolve_effective_integrations

        monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
        monkeypatch.setenv("PAGERDUTY_API_KEY", "env-pd-key")
        monkeypatch.setenv("PAGERDUTY_API_BASE_URL", "https://api.pagerduty.com")

        effective = resolve_effective_integrations()

        pd = effective.get("pagerduty")
        assert pd is not None
        assert pd["config"]["api_key"] == "env-pd-key"
        assert pd["source"] == "local env"

    def test_env_loading_skips_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.verify import resolve_effective_integrations

        monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
        monkeypatch.delenv("PAGERDUTY_API_KEY", raising=False)

        effective = resolve_effective_integrations()

        assert "pagerduty" not in effective

    def test_store_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.verify import resolve_effective_integrations

        monkeypatch.setattr(
            "app.integrations.catalog.load_integrations",
            lambda: [
                {
                    "id": "pagerduty-prod",
                    "service": "pagerduty",
                    "status": "active",
                    "credentials": {
                        "api_key": "store-pd-key",
                        "base_url": "https://api.pagerduty.com",
                    },
                }
            ],
        )

        effective = resolve_effective_integrations()

        pd = effective.get("pagerduty")
        assert pd is not None
        assert pd["config"]["api_key"] == "store-pd-key"
        assert pd["source"] == "local store"


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------


class TestPagerDutyIncidentsTool:
    def test_tool_metadata(self) -> None:
        from app.tools.PagerDutyIncidentsTool import PagerDutyIncidentsTool

        tool = PagerDutyIncidentsTool()
        assert tool.name == "pagerduty_incidents"
        assert tool.source == "pagerduty"
        assert "api_key" in tool.requires

    def test_is_available_true(self) -> None:
        from app.tools.PagerDutyIncidentsTool import PagerDutyIncidentsTool

        tool = PagerDutyIncidentsTool()
        sources = {"pagerduty": {"connection_verified": True}}
        assert tool.is_available(sources) is True

    def test_is_available_false(self) -> None:
        from app.tools.PagerDutyIncidentsTool import PagerDutyIncidentsTool

        tool = PagerDutyIncidentsTool()
        sources = {"pagerduty": {"connection_verified": False}}
        assert tool.is_available(sources) is False

    def test_run_not_configured(self) -> None:
        from app.tools.PagerDutyIncidentsTool import PagerDutyIncidentsTool

        tool = PagerDutyIncidentsTool()
        result = tool.run(api_key="")
        assert result["available"] is False
        assert "not configured" in result["error"]

    def test_run_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.tools.PagerDutyIncidentsTool import PagerDutyIncidentsTool

        tool = PagerDutyIncidentsTool()

        fake_incidents = [
            {"id": "P1", "status": "triggered", "urgency": "high", "title": "DB down"},
            {"id": "P2", "status": "resolved", "urgency": "low", "title": "Old issue"},
        ]

        monkeypatch.setattr(
            "app.services.pagerduty.client.PagerDutyClient.list_incidents",
            lambda _self, **_kwargs: {"success": True, "incidents": fake_incidents, "total": 2},
        )

        result = tool.run(api_key="test-key")
        assert result["available"] is True
        assert result["total"] == 2
        assert len(result["open_incidents"]) == 1
        assert result["open_incidents"][0]["id"] == "P1"

    def test_run_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.tools.PagerDutyIncidentsTool import PagerDutyIncidentsTool

        tool = PagerDutyIncidentsTool()

        monkeypatch.setattr(
            "app.services.pagerduty.client.PagerDutyClient.list_incidents",
            lambda _self, **_kwargs: {"success": False, "error": "HTTP 401: unauthorized"},
        )

        result = tool.run(api_key="bad-key")
        assert result["available"] is False
        assert "401" in result["error"]


class TestPagerDutyIncidentDetailTool:
    def test_tool_metadata(self) -> None:
        from app.tools.PagerDutyIncidentDetailTool import PagerDutyIncidentDetailTool

        tool = PagerDutyIncidentDetailTool()
        assert tool.name == "pagerduty_incident_detail"
        assert tool.source == "pagerduty"
        assert "incident_id" in tool.requires

    def test_run_missing_incident_id(self) -> None:
        from app.tools.PagerDutyIncidentDetailTool import PagerDutyIncidentDetailTool

        tool = PagerDutyIncidentDetailTool()
        result = tool.run(api_key="test-key", incident_id="")
        assert result["available"] is False
        assert "incident_id is required" in result["error"]

    def test_run_not_configured(self) -> None:
        from app.tools.PagerDutyIncidentDetailTool import PagerDutyIncidentDetailTool

        tool = PagerDutyIncidentDetailTool()
        result = tool.run(api_key="", incident_id="P123")
        assert result["available"] is False
        assert "not configured" in result["error"]

    def test_run_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.tools.PagerDutyIncidentDetailTool import PagerDutyIncidentDetailTool

        tool = PagerDutyIncidentDetailTool()

        fake_incident = {
            "id": "P123",
            "title": "Database connection timeout",
            "status": "triggered",
        }
        fake_log_entries = [
            {"id": "L1", "type": "trigger_log_entry", "summary": "Triggered"},
        ]
        fake_notes = [
            {"id": "N1", "content": "Investigating DB pool exhaustion"},
        ]

        monkeypatch.setattr(
            "app.services.pagerduty.client.PagerDutyClient.get_incident",
            lambda _self, _incident_id: {"success": True, "incident": fake_incident},
        )
        monkeypatch.setattr(
            "app.services.pagerduty.client.PagerDutyClient.get_incident_log_entries",
            lambda _self, _incident_id, limit=25: {  # noqa: ARG005
                "success": True,
                "log_entries": fake_log_entries,
                "total": 1,
            },
        )
        monkeypatch.setattr(
            "app.services.pagerduty.client.PagerDutyClient.get_incident_notes",
            lambda _self, _incident_id: {"success": True, "notes": fake_notes, "total": 1},
        )

        result = tool.run(api_key="test-key", incident_id="P123")
        assert result["available"] is True
        assert result["incident"]["id"] == "P123"
        assert len(result["log_entries"]) == 1
        assert len(result["notes"]) == 1


# ---------------------------------------------------------------------------
# Evidence source tests
# ---------------------------------------------------------------------------


class TestEvidenceSource:
    def test_pagerduty_in_evidence_source(self) -> None:
        from app.types.evidence import EvidenceSource

        # Verify "pagerduty" is a valid EvidenceSource literal value
        # by checking it's in the type's __args__
        assert "pagerduty" in EvidenceSource.__args__  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_pagerduty_in_integration_specs(self) -> None:
        from app.integrations.registry import INTEGRATION_SPECS

        services = [spec.service for spec in INTEGRATION_SPECS]
        assert "pagerduty" in services

    def test_pagerduty_in_supported_verify_services(self) -> None:
        from app.integrations.registry import SUPPORTED_VERIFY_SERVICES

        assert "pagerduty" in SUPPORTED_VERIFY_SERVICES

    def test_pagerduty_has_verifier(self) -> None:
        from app.integrations.registry import INTEGRATION_SPECS

        pd_spec = next(s for s in INTEGRATION_SPECS if s.service == "pagerduty")
        assert pd_spec.verifier is not None
        assert pd_spec.direct_effective is True
