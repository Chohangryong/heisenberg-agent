"""Unit tests for sync_jobs repository — CRUD, lock, state transitions."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from heisenberg_agent.storage.models import Article, ArticleEvent, SyncJob
from heisenberg_agent.storage.repositories import sync_jobs as sync_repo
from heisenberg_agent.utils.dt import now_utc


def _create_article(session: Session, slug: str = "test") -> Article:
    article = Article(
        source_site="heisenberg.kr",
        slug=slug,
        url=f"https://heisenberg.kr/{slug}/",
        title=f"Title {slug}",
        collected_at=now_utc(),
        collect_status="SUCCEEDED",
        analyze_status="SUCCEEDED",
        content_hash="hash123",
    )
    session.add(article)
    session.commit()
    return article


# ---------------------------------------------------------------------------
# ensure_sync_jobs
# ---------------------------------------------------------------------------


def test_ensure_creates_jobs(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector", "notion"], "embed.v1")

    jobs = db_session.query(SyncJob).filter_by(article_id=article.id).all()
    assert len(jobs) == 2
    targets = {j.target for j in jobs}
    assert targets == {"vector", "notion"}
    assert all(j.status == "pending" for j in jobs)


def test_ensure_idempotent(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")

    jobs = db_session.query(SyncJob).filter_by(article_id=article.id, target="vector").all()
    assert len(jobs) == 1


def test_ensure_respects_enabled_targets(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")

    jobs = db_session.query(SyncJob).filter_by(article_id=article.id).all()
    assert len(jobs) == 1
    assert jobs[0].target == "vector"


# ---------------------------------------------------------------------------
# ensure_sync_jobs — re-arm with payload_hash
# ---------------------------------------------------------------------------


def test_rearm_failed_on_payload_hash_change(db_session: Session):
    """Failed job re-arms when payload_hash changes."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="notion", status="failed",
        payload_hash="old_hash", synced_analysis_id=article.current_analysis_id,
        attempt_count=3,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.ensure_sync_jobs(
        db_session, article, ["notion"], "embed.v1",
        current_notion_hash="new_hash",
    )

    db_session.refresh(job)
    assert job.status == "pending"
    assert job.attempt_count == 0


def test_rearm_exhausted_on_payload_hash_change(db_session: Session):
    """Exhausted job re-arms when payload_hash changes."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="vector", status="exhausted",
        payload_hash="old_hash", synced_analysis_id=article.current_analysis_id,
        embedding_version="embed.v1",
        attempt_count=5,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.ensure_sync_jobs(
        db_session, article, ["vector"], "embed.v1",
        current_vector_hash="new_hash",
    )

    db_session.refresh(job)
    assert job.status == "pending"
    assert job.attempt_count == 0


def test_rearm_vector_on_embedding_version_change(db_session: Session):
    """Vector job re-arms when embedding_version changes."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="vector", status="failed",
        payload_hash="hash_a", synced_analysis_id=article.current_analysis_id,
        embedding_version="embed.v1",
        attempt_count=2,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.ensure_sync_jobs(
        db_session, article, ["vector"], "embed.v2",
        current_vector_hash="hash_a",
    )

    db_session.refresh(job)
    assert job.status == "pending"
    assert job.attempt_count == 0


def test_no_rearm_when_nothing_changed(db_session: Session):
    """Failed job stays failed when nothing changed."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="notion", status="failed",
        payload_hash="same_hash", synced_analysis_id=article.current_analysis_id,
        attempt_count=2,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.ensure_sync_jobs(
        db_session, article, ["notion"], "embed.v1",
        current_notion_hash="same_hash",
    )

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt_count == 2


def test_rearm_when_job_payload_hash_is_none(db_session: Session):
    """Job with payload_hash=None re-arms when current_hash is provided."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="notion", status="succeeded",
        payload_hash=None, synced_analysis_id=article.current_analysis_id,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.ensure_sync_jobs(
        db_session, article, ["notion"], "embed.v1",
        current_notion_hash="any_hash",
    )

    db_session.refresh(job)
    assert job.status == "pending"


def test_succeeded_rearms_on_payload_hash_change(db_session: Session):
    """Succeeded job goes pending when payload_hash changes."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="notion", status="succeeded",
        payload_hash="old_hash", synced_analysis_id=article.current_analysis_id,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.ensure_sync_jobs(
        db_session, article, ["notion"], "embed.v1",
        current_notion_hash="new_hash",
    )

    db_session.refresh(job)
    assert job.status == "pending"


# ---------------------------------------------------------------------------
# find_pending_jobs
# ---------------------------------------------------------------------------


def test_find_pending_jobs(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector", "notion"], "embed.v1")

    vector_jobs = sync_repo.find_pending_jobs(db_session, "vector")
    assert len(vector_jobs) == 1

    notion_jobs = sync_repo.find_pending_jobs(db_session, "notion")
    assert len(notion_jobs) == 1


def test_find_pending_jobs_ordered_by_created_at(db_session: Session):
    """Jobs are returned oldest-first (created_at ASC)."""
    a1 = _create_article(db_session, slug="old")
    a2 = _create_article(db_session, slug="mid")
    a3 = _create_article(db_session, slug="new")

    # Create jobs with explicit created_at to guarantee ordering
    j_new = SyncJob(
        article_id=a3.id, target="vector", status="pending",
        created_at=now_utc() + timedelta(minutes=10),
    )
    j_old = SyncJob(
        article_id=a1.id, target="vector", status="pending",
        created_at=now_utc() - timedelta(minutes=10),
    )
    j_mid = SyncJob(
        article_id=a2.id, target="vector", status="pending",
        created_at=now_utc(),
    )
    # Insert in non-chronological order to verify ORDER BY, not insert order
    db_session.add_all([j_new, j_old, j_mid])
    db_session.commit()

    jobs = sync_repo.find_pending_jobs(db_session, "vector")
    assert len(jobs) == 3
    assert jobs[0].article_id == a1.id  # oldest
    assert jobs[1].article_id == a2.id  # middle
    assert jobs[2].article_id == a3.id  # newest


def test_find_pending_skips_future_retry(db_session: Session):
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="vector", status="failed",
        next_retry_at=now_utc() + timedelta(hours=1),
    )
    db_session.add(job)
    db_session.commit()

    jobs = sync_repo.find_pending_jobs(db_session, "vector")
    assert len(jobs) == 0


def test_find_pending_skips_max_attempts(db_session: Session):
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="vector", status="failed",
        attempt_count=10,
    )
    db_session.add(job)
    db_session.commit()

    jobs = sync_repo.find_pending_jobs(db_session, "vector", max_attempts=5)
    assert len(jobs) == 0


def test_find_pending_excludes_exhausted(db_session: Session):
    """Exhausted jobs are not returned by find_pending_jobs."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="vector", status="exhausted",
        attempt_count=5,
    )
    db_session.add(job)
    db_session.commit()

    jobs = sync_repo.find_pending_jobs(db_session, "vector")
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Lock acquire / release
# ---------------------------------------------------------------------------


def test_try_lock_success(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    assert sync_repo.try_lock(db_session, job.id) is True

    db_session.refresh(job)
    assert job.locked_at is not None


def test_try_lock_fails_if_already_locked(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    sync_repo.try_lock(db_session, job.id)
    assert sync_repo.try_lock(db_session, job.id) is False


def test_stale_lock_can_be_reacquired(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    # Set lock to 15 minutes ago (stale) — naive for SQLite compatibility
    from datetime import datetime as dt
    job.locked_at = dt.utcnow() - timedelta(minutes=15)
    db_session.commit()

    assert sync_repo.try_lock(db_session, job.id) is True


def test_unlock(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    sync_repo.try_lock(db_session, job.id)
    sync_repo.unlock(db_session, job)
    db_session.refresh(job)
    assert job.locked_at is None


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def test_mark_succeeded(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    sync_repo.mark_succeeded(
        db_session, job,
        payload_hash="hash_abc",
        external_id="chroma:test",
        embedding_version="embed.v1",
    )

    db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.payload_hash == "hash_abc"
    assert job.external_id == "chroma:test"
    assert job.locked_at is None

    events = db_session.query(ArticleEvent).filter_by(
        article_id=article.id, event_type="sync.vector.succeeded"
    ).all()
    assert len(events) == 1


def test_mark_failed_with_retry(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["notion"], "embed.v1")
    job = db_session.query(SyncJob).first()

    sync_repo.mark_failed(
        db_session, job,
        error_code="429",
        error_message="rate limited",
        error_type="rate_limit",
        retryable=True,
        retry_after_seconds=120,
    )

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.next_retry_at is not None
    assert job.locked_at is None

    # Check event payload
    event = db_session.query(ArticleEvent).filter_by(
        event_type="sync.notion.failed",
    ).first()
    assert event is not None
    payload = json.loads(event.payload_json)
    assert payload["error_type"] == "rate_limit"
    assert payload["retryable"] is True
    assert payload["attempt_count"] == 1


def test_mark_failed_exponential_backoff(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    sync_repo.mark_failed(db_session, job, error_code="conn", error_message="fail")
    db_session.refresh(job)
    retry1 = job.next_retry_at

    sync_repo.mark_failed(db_session, job, error_code="conn", error_message="fail")
    db_session.refresh(job)
    retry2 = job.next_retry_at

    # Second retry should be later (exponential)
    assert retry2 > retry1


def test_record_noop(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()
    job.status = "succeeded"
    job.locked_at = now_utc()
    db_session.commit()

    sync_repo.record_noop(db_session, job)

    db_session.refresh(job)
    assert job.status == "succeeded"  # unchanged
    assert job.locked_at is None

    events = db_session.query(ArticleEvent).filter_by(
        event_type="sync.vector.skipped_noop"
    ).all()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Exhausted transition
# ---------------------------------------------------------------------------


def test_mark_failed_transitions_to_exhausted(db_session: Session):
    """Job transitions to exhausted when attempt_count reaches max."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="vector", status="failed",
        attempt_count=sync_repo.MAX_VECTOR_ATTEMPTS - 1,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.mark_failed(
        db_session, job,
        error_code="io_error",
        error_message="disk full",
        error_type="io_error",
        retryable=True,
    )

    db_session.refresh(job)
    assert job.status == "exhausted"
    assert job.attempt_count == sync_repo.MAX_VECTOR_ATTEMPTS
    assert job.next_retry_at is None

    event = db_session.query(ArticleEvent).filter_by(
        event_type="sync.vector.exhausted",
    ).first()
    assert event is not None
    payload = json.loads(event.payload_json)
    assert payload["exhausted"] is True
    assert payload["last_error_retryable"] is True
    assert payload["error_code"] == "io_error"
    assert payload["attempt_count"] == sync_repo.MAX_VECTOR_ATTEMPTS


def test_exhausted_event_truncates_long_message(db_session: Session):
    """Long error_message is truncated in event payload with indicator."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="notion", status="failed",
        attempt_count=sync_repo.MAX_NOTION_ATTEMPTS - 1,
    )
    db_session.add(job)
    db_session.commit()

    long_msg = "x" * 500
    sync_repo.mark_failed(
        db_session, job,
        error_code="server_error",
        error_message=long_msg,
        error_type="server_error",
        retryable=True,
    )

    event = db_session.query(ArticleEvent).filter_by(
        event_type="sync.notion.exhausted",
    ).first()
    payload = json.loads(event.payload_json)
    assert payload["error_message"].endswith("...(truncated)")
    assert payload["error_message_truncated"] is True
    assert len(payload["error_message"]) < 500


def test_failed_event_truncates_long_message(db_session: Session):
    """Long error_message is truncated in normal failed event too."""
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    long_msg = "y" * 500
    sync_repo.mark_failed(
        db_session, job,
        error_code="io_error",
        error_message=long_msg,
        error_type="io_error",
        retryable=True,
    )

    event = db_session.query(ArticleEvent).filter_by(
        event_type="sync.vector.failed",
    ).first()
    payload = json.loads(event.payload_json)
    assert payload["error_message"].endswith("...(truncated)")
    assert payload["error_message_truncated"] is True


def test_short_message_not_truncated(db_session: Session):
    """Short error_message is stored as-is without truncation flag."""
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector"], "embed.v1")
    job = db_session.query(SyncJob).first()

    sync_repo.mark_failed(
        db_session, job,
        error_code="io_error",
        error_message="short msg",
        error_type="io_error",
        retryable=True,
    )

    event = db_session.query(ArticleEvent).filter_by(
        event_type="sync.vector.failed",
    ).first()
    payload = json.loads(event.payload_json)
    assert payload["error_message"] == "short msg"
    assert "error_message_truncated" not in payload


# ---------------------------------------------------------------------------
# defer_for_rate_limit
# ---------------------------------------------------------------------------


def test_defer_for_rate_limit(db_session: Session):
    """Deferred job gets next_retry_at but attempt_count stays unchanged."""
    article = _create_article(db_session)
    job = SyncJob(
        article_id=article.id, target="notion", status="pending",
        attempt_count=0,
    )
    db_session.add(job)
    db_session.commit()

    sync_repo.defer_for_rate_limit(db_session, job, retry_after_seconds=120)

    db_session.refresh(job)
    assert job.attempt_count == 0  # unchanged
    assert job.next_retry_at is not None
    assert job.status == "pending"  # unchanged
    assert job.locked_at is None  # cleared defensively
