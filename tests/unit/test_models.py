"""Smoke tests for ORM models — table creation and basic CRUD."""

from datetime import datetime, timezone

from sqlalchemy import inspect, text

from heisenberg_agent.storage.models import (
    AnalysisRun,
    AppState,
    Article,
    ArticleAnnotation,
    ArticleEvent,
    ArticleImage,
    ArticleSection,
    CollectionRun,
    SyncJob,
    Tag,
)


def test_all_tables_created(db_session):
    """All expected tables exist after metadata.create_all."""
    inspector = inspect(db_session.bind)
    tables = set(inspector.get_table_names())
    expected = {
        "articles",
        "article_sections",
        "tags",
        "article_tags",
        "article_images",
        "analysis_runs",
        "sync_jobs",
        "article_annotations",
        "article_events",
        "collection_runs",
        "app_state",
    }
    assert expected.issubset(tables)


def test_foreign_keys_enabled(db_session):
    """PRAGMA foreign_keys is ON."""
    result = db_session.execute(text("PRAGMA foreign_keys")).scalar()
    assert result == 1


def test_article_insert_and_read(db_session):
    now = datetime.now(timezone.utc)
    article = Article(
        source_site="heisenberg.kr",
        slug="test-article",
        url="https://heisenberg.kr/test-article/",
        title="Test Article",
        collected_at=now,
    )
    db_session.add(article)
    db_session.commit()

    fetched = db_session.get(Article, article.id)
    assert fetched is not None
    assert fetched.slug == "test-article"
    assert fetched.collect_status == "PENDING"
    assert fetched.analyze_status == "PENDING"


def test_article_sections_cascade_delete(db_session):
    now = datetime.now(timezone.utc)
    article = Article(
        slug="cascade-test", url="https://heisenberg.kr/cascade/",
        title="Cascade", collected_at=now,
    )
    db_session.add(article)
    db_session.flush()

    section = ArticleSection(
        article_id=article.id, ordinal=1, section_kind="main_body",
    )
    db_session.add(section)
    db_session.commit()

    db_session.delete(article)
    db_session.commit()

    remaining = db_session.query(ArticleSection).filter_by(article_id=article.id).all()
    assert remaining == []


def test_sync_job_unique_article_target(db_session):
    now = datetime.now(timezone.utc)
    article = Article(
        slug="sync-test", url="https://heisenberg.kr/sync/",
        title="Sync", collected_at=now,
    )
    db_session.add(article)
    db_session.flush()

    job1 = SyncJob(article_id=article.id, target="notion")
    db_session.add(job1)
    db_session.commit()

    # Same article+target should violate unique constraint
    import pytest
    from sqlalchemy.exc import IntegrityError

    job2 = SyncJob(article_id=article.id, target="notion")
    db_session.add(job2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_analysis_run_is_current_partial_unique(db_session):
    """At most one is_current=True per article."""
    now = datetime.now(timezone.utc)
    article = Article(
        slug="analysis-test", url="https://heisenberg.kr/analysis/",
        title="Analysis", collected_at=now,
    )
    db_session.add(article)
    db_session.flush()

    run1 = AnalysisRun(
        article_id=article.id,
        source_content_hash="abc",
        analysis_version="v1",
        prompt_bundle_version="p1",
        is_current=True,
    )
    db_session.add(run1)
    db_session.commit()

    # Second is_current=True for same article should fail
    import pytest
    from sqlalchemy.exc import IntegrityError

    run2 = AnalysisRun(
        article_id=article.id,
        source_content_hash="def",
        analysis_version="v1",
        prompt_bundle_version="p1",
        is_current=True,
    )
    db_session.add(run2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_app_state_kv(db_session):
    state = AppState(key="last_run", value="2026-03-17T12:00:00")
    db_session.add(state)
    db_session.commit()

    fetched = db_session.get(AppState, "last_run")
    assert fetched is not None
    assert fetched.value == "2026-03-17T12:00:00"
