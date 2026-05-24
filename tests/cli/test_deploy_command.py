"""Tests for the opensre deploy CLI command.

Covers argument parsing, --dry-run, missing token, and the Vercel target path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from app.cli.commands.deploy import deploy_command
from app.deployment.methods.vercel import VercelDeployError, VercelDeployResult


@patch.dict("os.environ", {}, clear=True)
def test_deploy_requires_target() -> None:
    """The --target option is required."""
    runner = CliRunner()
    result = runner.invoke(deploy_command, [])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "required" in result.output.lower()


@patch.dict("os.environ", {}, clear=True)
def test_deploy_rejects_unknown_target() -> None:
    """Unknown targets are rejected by Click's Choice validation."""
    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "heroku"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid" in result.output.lower()


@patch.dict("os.environ", {}, clear=True)
def test_deploy_vercel_missing_token() -> None:
    """Deploy fails with a clear message when VERCEL_API_TOKEN is not set."""
    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--yes"])
    assert result.exit_code != 0
    assert "VERCEL_API_TOKEN" in result.output


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_dry_run_success(mock_deploy: MagicMock) -> None:
    """Dry-run with valid config prints success."""
    mock_deploy.return_value = VercelDeployResult(
        deployment_id="",
        url="",
        project_name="opensre",
        state="DRY_RUN_OK",
        elapsed_seconds=0.0,
    )
    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--dry-run"])
    assert result.exit_code == 0
    assert "valid" in result.output.lower() or "DRY_RUN_OK" in result.output


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_dry_run_failure(mock_deploy: MagicMock) -> None:
    """Dry-run with invalid config prints error and exits non-zero."""
    mock_deploy.return_value = VercelDeployError(
        message="Token expired.",
        suggestion="Regenerate at vercel.com.",
    )
    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--dry-run"])
    assert result.exit_code != 0
    assert "Token expired" in result.output


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.operations.health.poll_deployment_health")
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_full_success(mock_deploy: MagicMock, mock_health: MagicMock) -> None:
    """Full deploy with --yes prints URL and runs health check."""
    mock_deploy.return_value = VercelDeployResult(
        deployment_id="dpl_123",
        url="https://opensre.vercel.app",
        project_name="opensre",
        state="READY",
        elapsed_seconds=12.5,
    )
    from app.deployment.operations.health import HealthPollStatus

    mock_health.return_value = HealthPollStatus(
        url="https://opensre.vercel.app/health",
        attempts=1,
        status_code=200,
        elapsed_seconds=2.0,
    )

    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--yes"])
    assert result.exit_code == 0
    assert "opensre.vercel.app" in result.output
    assert "dpl_123" in result.output


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_full_failure(mock_deploy: MagicMock) -> None:
    """Full deploy failure prints error and exits non-zero."""
    mock_deploy.return_value = VercelDeployError(
        message="Build failed.",
        suggestion="Check Vercel dashboard.",
    )
    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--yes"])
    assert result.exit_code != 0
    assert "Build failed" in result.output


@patch.dict(
    "os.environ",
    {"VERCEL_API_TOKEN": "tok_test", "VERCEL_TEAM_ID": "team_xyz"},
    clear=True,
)
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_passes_team_id(mock_deploy: MagicMock) -> None:
    """Team ID from env is passed to the deploy function."""
    mock_deploy.return_value = VercelDeployError(message="test", suggestion="test")
    runner = CliRunner()
    runner.invoke(deploy_command, ["--target", "vercel", "--dry-run"])
    mock_deploy.assert_called_once_with(
        api_token="tok_test",
        team_id="team_xyz",
        project_name="opensre",
        dry_run=True,
    )


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_custom_project_name(mock_deploy: MagicMock) -> None:
    """Custom --project is passed to the deploy function."""
    mock_deploy.return_value = VercelDeployError(message="test", suggestion="test")
    runner = CliRunner()
    runner.invoke(deploy_command, ["--target", "vercel", "--dry-run", "--project", "my-bot"])
    mock_deploy.assert_called_once_with(
        api_token="tok_test",
        team_id="",
        project_name="my-bot",
        dry_run=True,
    )


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.operations.health.poll_deployment_health")
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_health_timeout_is_warning(
    mock_deploy: MagicMock, mock_health: MagicMock
) -> None:
    """Health check timeout is a warning, not a failure."""
    mock_deploy.return_value = VercelDeployResult(
        deployment_id="dpl_123",
        url="https://opensre.vercel.app",
        project_name="opensre",
        state="READY",
        elapsed_seconds=10.0,
        protection_disabled=True,
    )
    mock_health.side_effect = TimeoutError("timed out")

    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--yes"])
    # Deploy itself succeeded, health timeout is just a warning
    assert result.exit_code == 0
    assert "warming up" in result.output.lower() or "60s" in result.output


@patch.dict("os.environ", {"VERCEL_API_TOKEN": "tok_test"}, clear=True)
@patch("app.deployment.operations.health.poll_deployment_health")
@patch("app.deployment.methods.vercel.deploy_to_vercel")
def test_deploy_vercel_health_timeout_protection_active(
    mock_deploy: MagicMock, mock_health: MagicMock
) -> None:
    """Health check timeout with active protection shows actionable diagnostic."""
    mock_deploy.return_value = VercelDeployResult(
        deployment_id="dpl_123",
        url="https://opensre.vercel.app",
        project_name="opensre",
        state="READY",
        elapsed_seconds=10.0,
        protection_disabled=False,
    )
    mock_health.side_effect = TimeoutError("timed out")

    runner = CliRunner()
    result = runner.invoke(deploy_command, ["--target", "vercel", "--yes"])
    # Deploy itself succeeded, but user gets actionable protection warning
    assert result.exit_code == 0
    assert "Deployment Protection" in result.output
    assert "project-patch permissions" in result.output or "Dashboard" in result.output
