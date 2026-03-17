"""APScheduler-based pipeline scheduler.

Runs Pipeline.run() on a cron schedule.
Optionally starts a manual trigger HTTP server.

Session lifecycle:
- Each job execution creates a fresh session + agents via pipeline_factory.
- Session is closed in finally after every run.
- Scheduler holds only the factory, never a long-lived session.

Lock interaction:
- APScheduler max_instances=1 prevents overlapping cron jobs.
- Pipeline.run() acquires FileLock for OS-level dedup (vs --mode pipeline).
- LockError in job → logged, scheduler continues.
"""

from __future__ import annotations

import signal
import sys
from typing import Any, Callable, Protocol

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from heisenberg_agent.orchestrator.pipeline import Pipeline
from heisenberg_agent.runtime.locks import LockError
from heisenberg_agent.utils.dt import now_kst
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


# Type for pipeline factory: returns (pipeline, cleanup_fn)
PipelineFactory = Callable[[], tuple[Pipeline, Callable[[], None]]]


def _run_pipeline_job(factory: PipelineFactory) -> None:
    """Create a fresh pipeline, run it, then clean up.

    Each invocation gets its own session and agents.
    LockError: another instance running → log warning, skip.
    Other exceptions: log error, skip. Scheduler keeps running.
    """
    pipeline = None
    cleanup = None
    try:
        pipeline, cleanup = factory()
        run_id = pipeline.run()
        logger.info("scheduler.job_completed", run_id=run_id)
    except LockError:
        logger.warning("scheduler.skipped_locked")
    except Exception as e:
        logger.error("scheduler.job_failed", error=str(e))
    finally:
        if cleanup:
            try:
                cleanup()
            except Exception as e:
                logger.error("scheduler.cleanup_failed", error=str(e))


def start_scheduler(
    pipeline_factory: PipelineFactory,
    settings: Any,
) -> None:
    """Start the blocking scheduler with cron jobs and optional manual trigger.

    Args:
        pipeline_factory: Callable that returns (Pipeline, cleanup_fn).
            Called once per job execution — each run gets a fresh session.
        settings: AppSettings with scheduler/trigger config.
    """
    timezone = getattr(settings, "timezone", "Asia/Seoul")
    scheduler = BlockingScheduler(timezone=timezone)

    # Cron job
    cron_hours = _get_cron_hours(settings)
    scheduler.add_job(
        func=_run_pipeline_job,
        args=[pipeline_factory],
        trigger=CronTrigger(hour=",".join(str(h) for h in cron_hours), timezone=timezone),
        id="pipeline_cron",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info("scheduler.cron_registered", hours=cron_hours)

    # Optional manual trigger server
    trigger_server = None
    trigger_token = getattr(settings, "manual_trigger_token", "")
    if trigger_token:
        from heisenberg_agent.runtime.manual_trigger import TriggerServer

        bind = getattr(settings, "manual_trigger_bind", "127.0.0.1")
        port = getattr(settings, "manual_trigger_port", 8321)

        trigger_server = TriggerServer(
            scheduler=scheduler,
            run_pipeline_fn=lambda: _run_pipeline_job(pipeline_factory),
            get_now=now_kst,
            token=trigger_token,
            bind=bind,
            port=port,
        )
        trigger_server.start()

    # Signal handler for graceful shutdown
    def _shutdown(signum: int, frame: Any) -> None:
        logger.info("scheduler.shutdown_signal", signal=signum)
        if trigger_server:
            trigger_server.shutdown()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start blocking loop
    logger.info("scheduler.starting")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if trigger_server:
            trigger_server.shutdown()
        logger.info("scheduler.stopped")


def _get_cron_hours(settings: Any) -> list[int]:
    """Extract cron hours from settings. Default [8, 13, 19]."""
    scheduler_cfg = getattr(settings, "scheduler", None)
    if scheduler_cfg:
        hours = getattr(scheduler_cfg, "cron_hours", None)
        if hours:
            return list(hours)
    return [8, 13, 19]
