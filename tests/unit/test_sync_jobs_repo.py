"""Unit tests for sync_jobs repository — CRUD, lock, state transitions."""

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
# find_pending_jobs
# ---------------------------------------------------------------------------


def test_find_pending_jobs(db_session: Session):
    article = _create_article(db_session)
    sync_repo.ensure_sync_jobs(db_session, article, ["vector", "notion"], "embed.v1")

    vector_jobs = sync_repo.find_pending_jobs(db_session, "vector")
    assert len(vector_jobs) == 1

    notion_jobs = sync_repo.find_pending_jobs(db_session, "notion")
    assert len(notion_jobs) == 1


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
        retry_after_seconds=120,
    )

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.next_retry_at is not None
    assert job.locked_at is None


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
