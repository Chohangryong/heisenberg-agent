"""SyncJob repository — sync_jobs is the sole authority for sync status.

Lock acquire uses atomic UPDATE WHERE (compare-and-set):
  UPDATE sync_jobs SET locked_at = :now
  WHERE id = :id AND (locked_at IS NULL OR locked_at < :stale_cutoff)

SQLite 3.35+ supports UPDATE ... RETURNING. Python 3.11 bundles 3.39+.
Fallback for non-RETURNING engines: execute UPDATE, check rowcount == 1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from heisenberg_agent.storage.models import Article, ArticleEvent, SyncJob
from heisenberg_agent.utils.dt import now_utc
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()

STALE_LOCK_MINUTES = 10
MAX_VECTOR_ATTEMPTS = 5
MAX_NOTION_ATTEMPTS = 10


# ---------------------------------------------------------------------------
# Ensure sync jobs exist
# ---------------------------------------------------------------------------


def ensure_sync_jobs(
    session: Session,
    article: Article,
    enabled_targets: list[str],
    embedding_version: str,
) -> None:
    """Ensure sync_jobs exist for each enabled target.

    Creates pending jobs if missing.
    Re-arms failed jobs if payload would change (new analysis or embedding version).
    Resets succeeded jobs to pending if current_analysis_id changed.

    Change detection is based on current_analysis_id vs synced_analysis_id.
    Payload changes WITHOUT a current_analysis_id change (e.g. annotation edits,
    tag changes) are not detected here — that is outside Phase 3 scope.
    Future phases may add payload_hash pre-comparison or annotation change tracking.

    Args:
        session: DB session.
        article: Article with current_analysis_id set.
        enabled_targets: ["vector", "notion"] based on settings.
        embedding_version: Current embedding version from settings.
    """
    for target in enabled_targets:
        job = _find_job(session, article.id, target)

        if job is None:
            session.add(SyncJob(
                article_id=article.id,
                target=target,
                status="pending",
            ))
            continue

        if job.status == "failed":
            # Re-arm if analysis changed or embedding changed
            if article.current_analysis_id != job.synced_analysis_id:
                _rearm(job)
            elif target == "vector" and job.embedding_version != embedding_version:
                _rearm(job)

        elif job.status == "succeeded":
            # Re-arm if the article was re-analyzed (new payload expected)
            if article.current_analysis_id != job.synced_analysis_id:
                job.status = "pending"
            elif target == "vector" and job.embedding_version != embedding_version:
                job.status = "pending"
            # else: same analysis + same embedding → stay succeeded (noop)

    session.commit()


def _rearm(job: SyncJob) -> None:
    job.status = "pending"
    job.attempt_count = 0
    job.next_retry_at = None
    job.locked_at = None


# ---------------------------------------------------------------------------
# Query pending jobs
# ---------------------------------------------------------------------------


def find_pending_jobs(
    session: Session,
    target: str,
    max_attempts: int | None = None,
) -> list[SyncJob]:
    """Find jobs ready for processing.

    Returns jobs where:
    - target matches
    - status in (pending, failed)
    - next_retry_at is null or <= now
    - not locked (or lock is stale)
    - attempt_count < max_attempts
    """
    now = now_utc()
    stale_cutoff = now - timedelta(minutes=STALE_LOCK_MINUTES)

    if max_attempts is None:
        max_attempts = MAX_VECTOR_ATTEMPTS if target == "vector" else MAX_NOTION_ATTEMPTS

    stmt = (
        select(SyncJob)
        .where(
            SyncJob.target == target,
            SyncJob.status.in_(["pending", "failed"]),
            SyncJob.attempt_count < max_attempts,
        )
        .where(
            (SyncJob.next_retry_at == None) | (SyncJob.next_retry_at <= now)  # noqa: E711
        )
        .where(
            (SyncJob.locked_at == None) | (SyncJob.locked_at < stale_cutoff)  # noqa: E711
        )
    )
    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Lock acquire / release (atomic CAS)
# ---------------------------------------------------------------------------


def try_lock(session: Session, job_id: int) -> bool:
    """Atomically acquire lock on a sync job.

    Uses UPDATE WHERE (compare-and-set) for atomicity.
    SQLite 3.35+ supports RETURNING; fallback uses rowcount.

    Returns:
        True if lock acquired, False if already locked.
    """
    # SQLite stores datetimes as naive — use naive for comparison
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(minutes=STALE_LOCK_MINUTES)

    result = session.execute(
        update(SyncJob)
        .where(
            SyncJob.id == job_id,
            (SyncJob.locked_at == None) | (SyncJob.locked_at < stale_cutoff),  # noqa: E711
        )
        .values(locked_at=now)
    )
    session.commit()
    return result.rowcount == 1


def unlock(session: Session, job: SyncJob) -> None:
    """Release lock on a sync job."""
    job.locked_at = None
    session.commit()


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def mark_succeeded(
    session: Session,
    job: SyncJob,
    *,
    payload_hash: str,
    external_id: str,
    embedding_version: str | None = None,
    synced_analysis_id: int | None = None,
) -> None:
    """Mark job as succeeded after sync."""
    job.status = "succeeded"
    job.payload_hash = payload_hash
    job.external_id = external_id
    job.locked_at = None
    job.last_error_code = None
    job.last_error_message = None

    if embedding_version is not None:
        job.embedding_version = embedding_version
    if synced_analysis_id is not None:
        job.synced_analysis_id = synced_analysis_id

    session.add(ArticleEvent(
        article_id=job.article_id,
        stage="sync",
        event_type=f"sync.{job.target}.succeeded",
    ))
    session.commit()


def mark_failed(
    session: Session,
    job: SyncJob,
    *,
    error_code: str,
    error_message: str,
    retry_after_seconds: int | None = None,
) -> None:
    """Mark job as failed with retry scheduling."""
    job.status = "failed"
    job.attempt_count = (job.attempt_count or 0) + 1
    job.last_error_code = error_code
    job.last_error_message = error_message[:500]
    job.locked_at = None

    if retry_after_seconds is not None:
        job.next_retry_at = now_utc() + timedelta(seconds=retry_after_seconds)
    else:
        # Exponential backoff: 5min * 2^(attempt-1)
        backoff = min(300 * (2 ** (job.attempt_count - 1)), 3600)
        job.next_retry_at = now_utc() + timedelta(seconds=backoff)

    session.add(ArticleEvent(
        article_id=job.article_id,
        stage="sync",
        event_type=f"sync.{job.target}.failed",
    ))
    session.commit()


def record_noop(session: Session, job: SyncJob) -> None:
    """Record noop skip. Job stays succeeded, only event is logged."""
    job.locked_at = None
    session.add(ArticleEvent(
        article_id=job.article_id,
        stage="sync",
        event_type=f"sync.{job.target}.skipped_noop",
    ))
    session.commit()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _find_job(session: Session, article_id: int, target: str) -> SyncJob | None:
    stmt = select(SyncJob).where(
        SyncJob.article_id == article_id,
        SyncJob.target == target,
    )
    return session.execute(stmt).scalar_one_or_none()
