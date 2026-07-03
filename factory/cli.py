"""Factory command-line interface.

Commands: init, run, heal, dashboard, scheduler, routine.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@click.group()
def main() -> None:
    """Self-building software factory."""


@main.command()
def init() -> None:
    """Interactive bootstrap: writes factory.yaml and .env."""
    from scripts.init import run_init

    run_init()


@main.command()
@click.argument("task_text", required=False)
@click.option("--issue", type=int, default=None, help="Create the task from a GitHub issue.")
def run(task_text: str | None, issue: int | None) -> None:
    """Run one task through the full pipeline (plan -> code -> gates -> PR)."""
    if not task_text and issue is None:
        raise click.UsageError("provide a task description or --issue <n>")
    from factory.pipeline import Pipeline

    pipeline = Pipeline.from_repo(Path.cwd())
    if issue is not None:
        task = pipeline.intake_issue(issue)
    else:
        task = pipeline.intake_text(task_text or "")
    outcome = pipeline.run(task)
    click.echo(f"task {task.id}: {outcome.summary}")
    sys.exit(0 if outcome.ok else 2)


@main.command()
@click.option("--run-id", type=int, default=None, help="Review one failed Actions run.")
def heal(run_id: int | None) -> None:
    """Review failed GitHub Actions runs and self-heal."""
    from factory.selfheal.ci_review import CIReviewer

    reviewer = CIReviewer.from_repo(Path.cwd())
    results = reviewer.review(run_id=run_id)
    for line in results:
        click.echo(line)


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8700, type=int)
@click.option("--with-scheduler", is_flag=True, help="Embed the routine scheduler.")
def dashboard(host: str, port: int, with_scheduler: bool) -> None:
    """Serve the metrics/traces dashboard."""
    import uvicorn

    from factory.dashboard.app import create_app

    uvicorn.run(create_app(with_scheduler=with_scheduler), host=host, port=port)


@main.command()
def scheduler() -> None:
    """Run the cron routine daemon in the foreground."""
    from factory.scheduler.runner import run_daemon

    run_daemon()


@main.group()
def routine() -> None:
    """Manage configured routines."""


@routine.command("list")
def routine_list() -> None:
    """List configured routines."""
    from factory.config import load_config

    config = load_config()
    for name, spec in config.schedules.items():
        state = "on " if spec.enabled else "off"
        click.echo(f"[{state}] {name}: cron '{spec.cron}' -> {spec.action.type}:{spec.action.ref}")


@routine.command("run")
@click.argument("name")
def routine_run(name: str) -> None:
    """Run one routine immediately."""
    from factory.scheduler.routines import run_routine_by_name

    result = run_routine_by_name(name)
    click.echo(str(result))
    sys.exit(0 if result.ok else 2)


@routine.command("generate-ci")
def routine_generate_ci() -> None:
    """Write .github/workflows/routines.yml from configured schedules."""
    from factory.config import load_config
    from factory.scheduler.routines import generate_routines_workflow

    config = load_config()
    target = Path(".github/workflows/routines.yml")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generate_routines_workflow(config), encoding="utf-8")
    click.echo(f"wrote {target}")


if __name__ == "__main__":
    main()
