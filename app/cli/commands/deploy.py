"""CLI command: opensre deploy --target <provider>.

Currently supports:
- vercel: Deploy as Vercel serverless functions (chat and lightweight workloads).
"""

from __future__ import annotations

import os

import click

from app.cli.interactive_shell.ui.theme import DIM, ERROR, HIGHLIGHT, SECONDARY, WARNING


@click.command("deploy")
@click.option(
    "--target",
    "-t",
    type=click.Choice(["vercel"], case_sensitive=False),
    required=True,
    help="Deployment target platform.",
)
@click.option(
    "--project",
    "-p",
    default="opensre",
    show_default=True,
    help="Project name on the target platform.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate configuration without deploying.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def deploy_command(
    target: str,
    project: str,
    dry_run: bool,
    yes: bool,
) -> None:
    """Deploy OpenSRE to a supported platform.

    \b
    Supported targets:
      vercel  — Serverless functions for chat and lightweight workloads.
                NOT suitable for deep RCA investigations.

    \b
    Platform limits (Vercel):
      • Serverless function timeout: 10s (Hobby) / 60s (Pro)
      • No persistent background processes or streaming SSE
      • Cold starts on infrequent traffic
      • Deep investigations (30s–5min+) will exceed execution limits

    \b
    Examples:
      opensre deploy --target vercel
      opensre deploy --target vercel --dry-run
      opensre deploy --target vercel --project my-sre-bot
    """
    if target == "vercel":
        _deploy_vercel(project=project, dry_run=dry_run, yes=yes)


def _deploy_vercel(*, project: str, dry_run: bool, yes: bool) -> None:
    """Handle Vercel deployment."""
    from rich.console import Console

    from app.deployment.methods.vercel import (
        VercelDeployError,
        deploy_to_vercel,
    )
    from app.deployment.operations.health import poll_deployment_health

    console = Console(highlight=False)

    api_token = os.environ.get("VERCEL_API_TOKEN", "")
    team_id = os.environ.get("VERCEL_TEAM_ID", "")

    if not api_token:
        console.print(
            f"[{ERROR}]VERCEL_API_TOKEN is not set.[/]\n"
            f"[{SECONDARY}]Create a token at https://vercel.com/account/tokens "
            f"with Read/Write access to Projects and Deployments.[/]"
        )
        raise SystemExit(1)

    if dry_run:
        with console.status("Validating Vercel configuration...", spinner="dots"):
            result = deploy_to_vercel(
                api_token=api_token,
                team_id=team_id,
                project_name=project,
                dry_run=True,
            )
        if isinstance(result, VercelDeployError):
            console.print(f"[{ERROR}]{result.message}[/]")
            console.print(f"[{SECONDARY}]{result.suggestion}[/]")
            raise SystemExit(1)
        console.print(f"[{HIGHLIGHT}]Vercel configuration is valid.[/]")
        console.print(f"[{DIM}]Project: {project} | Team: {team_id or '(personal)'}[/]")
        return

    # Show platform limits before deploying
    console.print()
    console.print("[bold]Deploying OpenSRE to Vercel[/bold]")
    console.print(f"[{DIM}]Project: {project} | Team: {team_id or '(personal)'}[/]")
    console.print()
    console.print(f"[{WARNING}]Platform limits:[/]")
    console.print(
        f"[{SECONDARY}]  • Function timeout: 10s (Hobby) / 60s (Pro) / 900s (Enterprise)[/]"
    )
    console.print(f"[{SECONDARY}]  • No persistent background processes[/]")
    console.print(f"[{SECONDARY}]  • Deep RCA investigations will exceed execution limits[/]")
    console.print(f"[{SECONDARY}]  • Suitable for: health checks, short chat, webhooks[/]")
    console.print()

    if not yes and not click.confirm("Proceed with deployment?"):
        console.print("Cancelled.")
        return

    with console.status("Deploying to Vercel...", spinner="dots"):
        result = deploy_to_vercel(
            api_token=api_token,
            team_id=team_id,
            project_name=project,
        )

    if isinstance(result, VercelDeployError):
        console.print(f"[{ERROR}]{result.message}[/]")
        console.print(f"[{SECONDARY}]{result.suggestion}[/]")
        raise SystemExit(1)

    console.print()
    console.print(f"[{HIGHLIGHT}]Deployment successful.[/]")
    console.print(f"  URL: {result.url}")
    console.print(f"  Deployment ID: {result.deployment_id}")
    console.print(f"  State: {result.state}")
    console.print(f"  Elapsed: {result.elapsed_seconds:.1f}s")
    console.print()

    # Health check
    with console.status("Verifying health endpoint...", spinner="dots"):
        try:
            health = poll_deployment_health(
                result.url,
                max_attempts=12,
                interval_seconds=5.0,
            )
            console.print(
                f"[{HIGHLIGHT}]Health check passed: {health.url} "
                f"(status {health.status_code}, {health.elapsed_seconds:.1f}s)[/]"
            )
        except TimeoutError:
            if not result.protection_disabled:
                console.print(
                    f"[{WARNING}]Health check failed — Vercel Deployment Protection "
                    f"is likely still active.[/]"
                )
                console.print(
                    f"[{SECONDARY}]Your token lacks permission to disable SSO/Deployment "
                    f"Protection. Team-scoped projects block unauthenticated requests "
                    f"(401/403) by default.[/]"
                )
                console.print(
                    f"[{SECONDARY}]Fix: disable Deployment Protection manually in "
                    f"Vercel Dashboard → Project Settings → Deployment Protection, "
                    f"or use a token with project-patch permissions.[/]"
                )
            else:
                console.print(
                    f"[{WARNING}]Health check did not pass within 60s. "
                    f"The deployment may still be warming up.[/]"
                )
            console.print(f"[{SECONDARY}]Try: curl {result.url}/health[/]")

    console.print()
    console.print(
        f"[{DIM}]Note: This deployment serves lightweight workloads only. "
        f"Deep RCA investigations require a persistent runtime (EC2, Railway).[/]"
    )
