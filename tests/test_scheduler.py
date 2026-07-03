"""Scheduler and routine action tests."""

import sys

import pytest

from factory.config import FactoryConfig, Routine, RoutineAction
from factory.scheduler.routines import RoutineExecutor, generate_routines_workflow
from factory.scheduler.runner import build_scheduler


def make_config(**schedules) -> FactoryConfig:
    cfg = FactoryConfig()
    cfg.schedules = schedules
    return cfg


def command_routine(argv: list[str]) -> Routine:
    return Routine(cron="0 6 * * *",
                   action=RoutineAction(type="command", params={"argv": argv}))


def test_unknown_routine(git_repo):
    executor = RoutineExecutor(git_repo, make_config())
    outcome = executor.run("nope")
    assert not outcome.ok
    assert "not configured" in outcome.detail


def test_disabled_routine_refused(git_repo):
    routine = Routine(cron="0 6 * * *", enabled=False,
                      action=RoutineAction(type="report"))
    executor = RoutineExecutor(git_repo, make_config(r=routine))
    assert not executor.run("r").ok


def test_allowlisted_command_runs(git_repo):
    argv = [sys.executable, "-c", "print('routine-ok')"]
    cfg = make_config(cmd=command_routine(argv))
    cfg.scheduler.command_allowlist = [argv]
    executor = RoutineExecutor(git_repo, cfg)

    outcome = executor.run("cmd")

    assert outcome.ok
    assert "routine-ok" in outcome.detail


def test_non_allowlisted_command_refused(git_repo):
    argv = [sys.executable, "-c", "print('nope')"]
    cfg = make_config(cmd=command_routine(argv))  # allowlist stays empty
    executor = RoutineExecutor(git_repo, cfg)

    outcome = executor.run("cmd")

    assert not outcome.ok
    assert "command_allowlist" in outcome.detail


def test_similar_command_not_matched_by_prefix(git_repo):
    allowed = ["git", "gc"]
    cfg = make_config(cmd=command_routine(["git", "gc", "--aggressive"]))
    cfg.scheduler.command_allowlist = [allowed]
    executor = RoutineExecutor(git_repo, cfg)

    assert not executor.run("cmd").ok  # exact argv match required


def test_stale_branch_maintenance(git_repo):
    from tests.conftest import run_git

    run_git(["branch", "factory/task-1/unit-x", "main"], git_repo)
    routine = Routine(cron="0 5 * * 0",
                      action=RoutineAction(type="maintenance", ref="stale_branches"))
    executor = RoutineExecutor(git_repo, make_config(cleanup=routine))

    outcome = executor.run("cleanup")

    assert outcome.ok
    assert "deleted 1" in outcome.detail


def test_build_scheduler_registers_enabled_jobs(git_repo):
    cfg = make_config(
        a=Routine(cron="0 6 * * *", action=RoutineAction(type="report")),
        b=Routine(cron="0 7 * * *", enabled=False, action=RoutineAction(type="report")),
    )
    scheduler = build_scheduler(git_repo, cfg)
    jobs = {job.id for job in scheduler.get_jobs()}
    assert jobs == {"a"}


def test_invalid_cron_rejected_at_config_time():
    with pytest.raises(ValueError):
        Routine(cron="every day", action=RoutineAction(type="report"))


def test_generate_ci_workflow():
    cfg = make_config(
        daily_log_review=Routine(cron="0 6 * * *",
                                 action=RoutineAction(type="maintenance",
                                                      ref="log_review")),
    )
    yaml_text = generate_routines_workflow(cfg)
    assert 'cron: "0 6 * * *"' in yaml_text
    assert "factory routine run daily_log_review" in yaml_text
    assert "permissions:" in yaml_text
    assert "contents: write" in yaml_text
