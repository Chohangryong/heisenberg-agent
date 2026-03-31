"""SyncJob repository — sync_jobs is the sole authority for sync status.

Lock acquire uses atomic UPDATE WHERE (compare-and-set):
  UPDATE sync_jobs SET locked_at = :now
  WHERE id = :id AND (locked_at IS NULL OR locked_at < :stale_cutoff)

SQLite 3.35+ supports UPDATE ... RETURNING. Python 3.11 bundles 3.39+.
Fallback for non-RETURNING engines: execute UPDATE, check rowcount == 1.
"""

from __future__ import annotations

import json
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

# Maximum length for error_message stored in ArticleEvent.payload_json.
# Full messages go to structlog only.
_EVENT_ERROR_MSG_LIMIT = 200


def _now_naive_utc() -> datetime:
    """Current UTC time as naive datetime for SQLite boundary.

    App/domain code uses aware UTC (``now_utc()``).  This repository talks
    directly to SQLite which stores datetimes as text without timezone —
    so every comparison/storage at the DB boundary goes through this helper.
    """
    return now_utc().replace(tzinfo=None)


def _truncate(msg: str, limit: int = _EVENT_ERROR_MSG_LIMIT) -> tuple[str, bool]:
    """Truncate a message and indicate whether it was truncated."""
    if len(msg) <= limit:
        return msg, False
    return msg[:limit] + "...(truncated)", True


def _max_attempts_for(target: str) -> int:
    return MAX_VECTOR_ATTEMPTS if target == "vector" else MAX_NOTION_ATTEMPTS


# ---------------------------------------------------------------------------
# Ensure sync jobs exist
# ---------------------------------------------------------------------------


def ensure_sync_jobs(
    session: Session,
    article: Article,
    enabled_targets: list[str],
    embedding_version: str,
    *,
    current_vector_hash: str | None = None,
    current_notion_hash: str | None = None,
) -> None:
    """Ensure sync_jobs exist for each enabled target.

    Creates pending jobs if missing.
    Re-arms failed/exhausted jobs when payload would change.
    Resets succeeded jobs to pending when payload would change.

    Re-arm conditions (target-specific):
      notion:  current_analysis_id changed OR payload_hash changed
      vector:  current_analysis_id changed OR embedding_version changed
               OR payload_hash changed

    Args:
        session: DB session.
        article: Article with current_analysis_id set.
        enabled_targets: ["vector", "notion"] based on settings.
        embedding_version: Current embedding version from settings.
        current_vector_hash: Pre-computed hash from build_vector_payload.
        current_notion_hash: Pre-computed hash from build_notion_payload.
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

        current_hash = (
            current_vector_hash if target == "vector" else current_notion_hash
        )
        should_rearm = _should_rearm(
            job, article, target, embedding_version, current_hash,
        )

        if job.status in ("failed", "exhausted"):
            if should_rearm:
                _rearm(job)

        elif job.status == "succeeded":
            if should_rearm:
                job.status = "pending"

    session.commit()


def _should_rearm(
    job: SyncJob,
    article: Article,
    target: str,
    embedding_version: str,
    current_hash: str | None,
) -> bool:
    """Determine whether a job should be re-armed.

    Target-specific rules:
      notion:  analysis_id changed OR payload_hash changed
      vector:  analysis_id changed OR embedding_version changed
               OR payload_hash changed
    """
    if article.current_analysis_id != job.synced_analysis_id:
        return True

    if current_hash is not None:
        if job.payload_hash is None or current_hash != job.payload_hash:
            return True

    if target == "vector" and job.embedding_version != embedding_version:
        return True

    return False


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
    - status in (pending, failed)  — exhausted jobs are excluded
    - next_retry_at is null or <= now
    - not locked (or lock is stale)
    - attempt_count < max_attempts
    """
    now = _now_naive_utc()
    stale_cutoff = now - timedelta(minutes=STALE_LOCK_MINUTES)

    if max_attempts is None:
        max_attempts = _max_attempts_for(target)

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
        .order_by(SyncJob.created_at.asc())
    )
    return list(session.execute(stmt).scalars().all())


def find_pending_jobs_for_article(
    session: Session,
    target: str,
    article_id: int,
    max_attempts: int | None = None,
) -> list[SyncJob]:
    """Find pending jobs for a specific article and target."""
    now = _now_naive_utc()
    stale_cutoff = now - timedelta(minutes=STALE_LOCK_MINUTES)

    if max_attempts is None:
        max_attempts = _max_attempts_for(target)

    stmt = (
        select(SyncJob)
        .where(
            SyncJob.target == target,
            SyncJob.article_id == article_id,
            SyncJob.status.in_(["pending", "failed"]),
            SyncJob.attempt_count < max_attempts,
        )
        .where(
            (SyncJob.next_retry_at == None) | (SyncJob.next_retry_at <= now)  # noqa: E711
        )
        .where(
            (SyncJob.locked_at == None) | (SyncJob.locked_at < stale_cutoff)  # noqa: E711
        )
        .order_by(SyncJob.created_at.asc())
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
    now = _now_naive_utc()
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


def force_unlock(session: Session, job_id: int) -> None:
    """Release lock via direct UPDATE — safe after rollback.

    Use when session.refresh(job) fails on a detached instance.
    """
    session.execute(
        update(SyncJob)
        .where(SyncJob.id == job_id)
        .values(locked_at=None)
    )
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
    """Mark job as succeeded after sync.

    payload_hash is updated ONLY here — never on partial success.
    This ensures that any incomplete sync (e.g. body written but properties
    update failed) will be retried on the next run because the stored
    payload_hash won't match the new hash.
    """
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
    error_type: str = "unknown",
    retryable: bool = False,
    retry_after_seconds: int | None = None,
) -> None:
    """Mark job as failed with retry scheduling.

    If attempt_count reaches max_attempts, transitions to 'exhausted'
    instead of 'failed'. Exhausted jobs are excluded from find_pending_jobs
    but can be re-armed by ensure_sync_jobs when payload changes.
    """
    job.attempt_count = (job.attempt_count or 0) + 1
    job.last_error_code = error_code
    job.last_error_message = error_message[:500]
    job.locked_at = None

    max_attempts = _max_attempts_for(job.target)

    # Exhausted check
    if job.attempt_count >= max_attempts:
        job.status = "exhausted"
        job.next_retry_at = None

        msg_trunc, truncated = _truncate(error_message)
        event_payload: dict[str, Any] = {
            "target": job.target,
            "error_type": error_type,
            "error_code": error_code,
            "last_error_retryable": retryable,
            "exhausted": True,
            "attempt_count": job.attempt_count,
            "error_message": msg_trunc,
        }
        if truncated:
            event_payload["error_message_truncated"] = True

        session.add(ArticleEvent(
            article_id=job.article_id,
            stage="sync",
            event_type=f"sync.{job.target}.exhausted",
            payload_json=json.dumps(event_payload, ensure_ascii=False),
        ))

        logger.error(
            "sync.job_exhausted",
            target=job.target,
            error_type=error_type,
            error_code=error_code,
            last_error_retryable=retryable,
            exhausted=True,
            attempt_count=job.attempt_count,
            max_attempts=max_attempts,
            article_id=job.article_id,
            job_id=job.id,
        )
        session.commit()
        return

    # Normal failure
    job.status = "failed"

    if retry_after_seconds is not None:
        job.next_retry_at = _now_naive_utc() + timedelta(seconds=retry_after_seconds)
    else:
        # Exponential backoff: 5min * 2^(attempt-1)
        backoff = min(300 * (2 ** (job.attempt_count - 1)), 3600)
        job.next_retry_at = _now_naive_utc() + timedelta(seconds=backoff)

    msg_trunc, truncated = _truncate(error_message)
    event_payload = {
        "target": job.target,
        "error_type": error_type,
        "error_code": error_code,
        "retryable": retryable,
        "attempt_count": job.attempt_count,
        "error_message": msg_trunc,
    }
    if truncated:
        event_payload["error_message_truncated"] = True

    session.add(ArticleEvent(
        article_id=job.article_id,
        stage="sync",
        event_type=f"sync.{job.target}.failed",
        payload_json=json.dumps(event_payload, ensure_ascii=False),
    ))
    session.commit()


def defer_for_rate_limit(
    session: Session,
    job: SyncJob,
    retry_after_seconds: int,
) -> None:
    """Defer a job due to target-level rate limit without incrementing attempt_count.

    Used by the circuit breaker: when one job hits 429, remaining jobs in
    the same target are deferred without penalty since no API call was made.
    """
    job.next_retry_at = _now_naive_utc() + timedelta(seconds=retry_after_seconds)
    job.locked_at = None
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
