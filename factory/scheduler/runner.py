"""Routine scheduler daemon (APScheduler).

Routines are declared in factory.yaml ``schedules:`` - the config is the
source of truth, so jobs are rebuilt from it at every daemon start (run
history is persisted to SQLite by the executor). The global
``scheduler.runner`` switch guarantees a routine executes in exactly one
place: ``local`` here, ``ci`` via the generated routines.yml cron.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from factory.config import FactoryConfig, load_config
from factory.scheduler.routines import RoutineExecutor

logger = logging.getLogger(__name__)

MISFIRE_GRACE_SECONDS = 3600


def build_scheduler(repo_root: Path, config: FactoryConfig) -> BackgroundScheduler:
    """Build (not start) a scheduler with one job per enabled routine."""
    executor = RoutineExecutor(repo_root, config)
    scheduler = BackgroundScheduler(timezone=config.scheduler.timezone)
    for name, routine in config.schedules.items():
        if not routine.enabled:
            continue
        trigger = CronTrigger.from_crontab(
            routine.cron, timezone=routine.timezone or config.scheduler.timezone
        )
        scheduler.add_job(
            executor.run,
            trigger=trigger,
            args=[name],
            id=name,
            name=name,
            max_instances=1,          # overlap policy: skip if still running
            coalesce=True,            # collapse missed runs into one
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        logger.info("routine scheduled", extra={"operation": "scheduler",
                                                "routine": name, "cron": routine.cron})
    return scheduler


def start_embedded(repo_root: Path, config: FactoryConfig) -> BackgroundScheduler | None:
    """Start the scheduler inside another process (dashboard --with-scheduler)."""
    if config.scheduler.runner != "local":
        logger.warning(
            "scheduler.runner is %r - local scheduler not started (routines run in CI)",
            config.scheduler.runner,
        )
        return None
    scheduler = build_scheduler(repo_root, config)
    scheduler.start()
    return scheduler


def run_daemon() -> None:
    """Run the scheduler in the foreground (``factory scheduler``)."""
    repo_root = Path.cwd()
    config = load_config(repo_root)
    if config.scheduler.runner != "local":
        raise SystemExit(
            "scheduler.runner is set to 'ci' - routines run via the generated "
            "routines.yml workflow. Set scheduler.runner: local to run them here."
        )
    scheduler = build_scheduler(repo_root, config)
    scheduler.start()
    logger.info("scheduler daemon running", extra={"operation": "scheduler"})
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
