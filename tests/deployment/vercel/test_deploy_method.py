"""Unit tests for app.deployment.methods.vercel deploy logic.

These tests exercise the deploy_to_vercel function with mocked HTTP responses,
covering config validation, dry-run, success, and error paths without needing
a real Vercel API token.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.deployment.methods.vercel import (
    VercelDeployError,
    VercelDeployResult,
    deploy_to_vercel,
)


class TestDeployToVercelValidation:
    """Config validation before any network call."""

    def test_empty_token_returns_error(self) -> None:
        result = deploy_to_vercel(api_token="")
        assert isinstance(result, VercelDeployError)
        assert "VERCEL_API_TOKEN" in result.message

    def test_whitespace_only_token_returns_error(self) -> None:
        result = deploy_to_vercel(api_token="   \t\n")
        assert isinstance(result, VercelDeployError)
        assert "VERCEL_API_TOKEN" in result.message

    def test_error_includes_suggestion(self) -> None:
        result = deploy_to_vercel(api_token="")
        assert isinstance(result, VercelDeployError)
        assert "vercel.com/account/tokens" in result.suggestion


class TestDeployToVercelDryRun:
    """Dry-run mode validates credentials without deploying."""

    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_dry_run_success(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"projects": []}
        mock_client.get.return_value = mock_resp

        result = deploy_to_vercel(api_token="valid-token", dry_run=True)
        assert isinstance(result, VercelDeployResult)
        assert result.state == "DRY_RUN_OK"
        assert result.deployment_id == ""

    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_dry_run_invalid_token(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client.get.return_value = mock_resp

        result = deploy_to_vercel(api_token="bad-token", dry_run=True)
        assert isinstance(result, VercelDeployError)
        assert "401" in result.message

    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_dry_run_network_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        result = deploy_to_vercel(api_token="valid-token", dry_run=True)
        assert isinstance(result, VercelDeployError)
        assert "reach Vercel API" in result.message

    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_dry_run_passes_team_id(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"projects": []}
        mock_client.get.return_value = mock_resp

        deploy_to_vercel(api_token="tok", team_id="team_abc", dry_run=True)

        call_kwargs = mock_client.get.call_args
        assert "teamId" in call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))


class TestDeployToVercelFull:
    """Full deployment flow with mocked HTTP."""

    @patch("app.deployment.methods.vercel.time.sleep", return_value=None)
    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_successful_deployment(
        self, mock_client_cls: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # _ensure_project: project exists
        project_resp = MagicMock()
        project_resp.status_code = 200
        project_resp.json.return_value = {"id": "prj_123"}

        # _create_deployment: success
        deploy_resp = MagicMock()
        deploy_resp.status_code = 200
        deploy_resp.json.return_value = {"id": "dpl_abc", "url": "opensre-xyz.vercel.app"}

        # _wait_for_ready: READY on first poll
        ready_resp = MagicMock()
        ready_resp.status_code = 200
        ready_resp.json.return_value = {"readyState": "READY"}

        mock_client.get.side_effect = [project_resp, ready_resp]
        mock_client.post.return_value = deploy_resp

        result = deploy_to_vercel(api_token="tok_valid", project_name="my-project")
        assert isinstance(result, VercelDeployResult)
        assert result.deployment_id == "dpl_abc"
        assert result.url == "https://opensre-xyz.vercel.app"
        assert result.state == "READY"
        assert result.project_name == "my-project"

    @patch("app.deployment.methods.vercel.time.sleep", return_value=None)
    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_deployment_permission_denied(
        self, mock_client_cls: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # _ensure_project: project exists
        project_resp = MagicMock()
        project_resp.status_code = 200
        project_resp.json.return_value = {"id": "prj_123"}

        # _create_deployment: 403
        deploy_resp = MagicMock()
        deploy_resp.status_code = 403
        deploy_resp.json.return_value = {"error": {"message": "not allowed"}}

        mock_client.get.return_value = project_resp
        mock_client.post.return_value = deploy_resp

        result = deploy_to_vercel(api_token="tok_limited")
        assert isinstance(result, VercelDeployError)
        assert "permission denied" in result.message.lower()

    @patch("app.deployment.methods.vercel.time.sleep", return_value=None)
    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_deployment_build_error(
        self, mock_client_cls: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # _ensure_project: project exists
        project_resp = MagicMock()
        project_resp.status_code = 200
        project_resp.json.return_value = {"id": "prj_123"}

        # _create_deployment: success
        deploy_resp = MagicMock()
        deploy_resp.status_code = 200
        deploy_resp.json.return_value = {"id": "dpl_fail", "url": "opensre-fail.vercel.app"}

        # _wait_for_ready: ERROR state
        error_resp = MagicMock()
        error_resp.status_code = 200
        error_resp.json.return_value = {"readyState": "ERROR"}

        mock_client.get.side_effect = [project_resp, error_resp]
        mock_client.post.return_value = deploy_resp

        result = deploy_to_vercel(api_token="tok_valid")
        assert isinstance(result, VercelDeployError)
        assert "ERROR" in result.message

    @patch("app.deployment.methods.vercel.time.sleep", return_value=None)
    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_project_creation_failure(
        self, mock_client_cls: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # _ensure_project: project doesn't exist, creation returns 403
        not_found_resp = MagicMock()
        not_found_resp.status_code = 404

        create_resp = MagicMock()
        create_resp.status_code = 403
        create_resp.json.return_value = {"error": {"message": "forbidden"}}

        mock_client.get.return_value = not_found_resp
        mock_client.post.return_value = create_resp

        result = deploy_to_vercel(api_token="tok_valid")
        assert isinstance(result, VercelDeployError)
        assert "project" in result.message.lower()

    @patch("app.deployment.methods.vercel.httpx.Client")
    def test_network_error_during_deployment(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.get.side_effect = httpx.ConnectError("timeout")

        result = deploy_to_vercel(api_token="tok_valid")
        assert isinstance(result, VercelDeployError)
        assert "Network error" in result.message
