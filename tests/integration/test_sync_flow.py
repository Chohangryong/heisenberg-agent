"""Integration tests for SyncAgent — fake adapters + test SQLite.

No live ChromaDB or Notion API dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from heisenberg_agent.agents.sync_agent import SyncAgent
from heisenberg_agent.storage.models import (
    AnalysisRun,
    Article,
    ArticleEvent,
    ArticleSection,
    SyncJob,
)
from heisenberg_agent.utils.dt import now_utc


# ---------------------------------------------------------------------------
# Fake adapters
# ---------------------------------------------------------------------------


class FakeChromaAdapter:
    """Records upsert calls. Optionally fails."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.upsert_calls: list[dict] = []
        self._should_fail = should_fail

    def upsert(self, doc_id: str, document: str, metadata: dict) -> str:
        if self._should_fail:
            from heisenberg_agent.adapters.chroma_adapter import ChromaSyncError
            raise ChromaSyncError(
                "Fake chroma failure",
                error_type="io_error", retryable=True,
            )
        self.upsert_calls.append({"doc_id": doc_id, "document": document, "metadata": metadata})
        return doc_id


class FakeNotionAdapter:
    """Records create/update/replace_body calls. Optionally fails.

    Supports independent failure modes:
    - should_fail: create_page and update_page raise NotionSyncError
    - fail_429: create_page raises RetryAfterError
    - fail_on_update: update_page raises NotionSyncError (property update failure)
    - fail_on_replace_body: replace_body raises NotionSyncError (body replace failure)
    """

    def __init__(
        self,
        *,
        should_fail: bool = False,
        fail_429: bool = False,
        fail_on_update: bool = False,
        fail_on_replace_body: bool = False,
    ) -> None:
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.replace_body_calls: list[dict] = []
        self._should_fail = should_fail
        self._fail_429 = fail_429
        self._fail_on_update = fail_on_update
        self._fail_on_replace_body = fail_on_replace_body

    def create_page(self, properties: dict, children: list) -> str:
        if self._fail_429:
            from heisenberg_agent.adapters.notion_adapter import RetryAfterError
            raise RetryAfterError("Rate limited", retry_after=120)
        if self._should_fail:
            from heisenberg_agent.adapters.notion_adapter import NotionSyncError
            raise NotionSyncError(
                "Fake notion failure",
                error_type="server_error", retryable=True,
            )
        self.create_calls.append({"properties": properties, "children": children})
        return "notion-page-id-123"

    def update_page(self, page_id: str, properties: dict) -> str:
        if self._should_fail or self._fail_on_update:
            from heisenberg_agent.adapters.notion_adapter import NotionSyncError
            raise NotionSyncError(
                "Fake notion update failure",
                error_type="server_error", retryable=True,
            )
        self.update_calls.append({"page_id": page_id, "properties": properties})
        return page_id

    def replace_body(self, page_id: str, children: list) -> None:
        if self._fail_on_replace_body:
            from heisenberg_agent.adapters.notion_adapter import NotionSyncError
            raise NotionSyncError(
                "Fake body replace failure",
                error_type="server_error", retryable=True,
            )
        self.replace_body_calls.append({"page_id": page_id, "children": children})


# ---------------------------------------------------------------------------
# Fake settings
# ---------------------------------------------------------------------------


class _VectorDBSettings:
    enabled = True
    embedding_version = "embed.v1"


class _NotionSettings:
    enabled = True


class FakeSettings:
    vectordb = _VectorDBSettings()
    notion = _NotionSettings()


class FakeSettingsVectorOnly:
    vectordb = _VectorDBSettings()

    class notion:
        enabled = False


class FakeSettingsNotionOnly:
    class vectordb:
        enabled = False
        embedding_version = "embed.v1"

    notion = _NotionSettings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_analyzed_article(session: Session, slug: str = "test") -> Article:
    """Create an article with analysis run ready for sync."""
    article = Article(
        source_site="heisenberg.kr",
        slug=slug,
        url=f"https://heisenberg.kr/{slug}/",
        title=f"Article {slug}",
        author="김연구",
        category="AI",
        collected_at=now_utc(),
        published_at=now_utc(),
        collect_status="SUCCEEDED",
        analyze_status="SUCCEEDED",
        content_hash="hash_abc",
    )
    session.add(article)
    session.flush()

    section = ArticleSection(
        article_id=article.id, ordinal=1,
        section_kind="main_body", body_text="Main body text.",
    )
    session.add(section)

    run = AnalysisRun(
        article_id=article.id,
        source_content_hash="hash_abc",
        analysis_version="analysis.v1",
        prompt_bundle_version="prompt-bundle.v1",
        summary_json=json.dumps({
            "core_thesis": "AI is improving",
            "supporting_points": ["Point 1"],
            "conclusion": "Nvidia leads",
            "keywords": ["AI", "GPU"],
            "importance": "high",
        }),
        critique_json=json.dumps({
            "logic_gaps": ["Gap"],
            "missing_views": ["View"],
            "claims_to_verify": ["Claim"],
            "interest_analysis": "Commercial",
            "overall_assessment": "Solid",
        }),
        llm_model="fake-model",
        is_current=True,
        status="succeeded",
    )
    session.add(run)
    session.flush()

    article.current_analysis_id = run.id
    session.commit()
    return article


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sync_vector_success(db_session: Session):
    """Analyzed article → vector job → upsert → succeeded."""
    article = _create_analyzed_article(db_session)
    chroma = FakeChromaAdapter()

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=chroma,
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )
    stats = agent.run()

    assert stats["synced"] >= 1
    assert len(chroma.upsert_calls) == 1

    job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="vector",
    ).first()
    assert job is not None
    assert job.status == "succeeded"
    assert job.external_id is not None
    assert job.payload_hash is not None


def test_sync_notion_success(db_session: Session):
    """Analyzed article → notion job → create page → succeeded."""
    article = _create_analyzed_article(db_session)
    notion = FakeNotionAdapter()

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion,
        settings=FakeSettings(),
    )
    stats = agent.run()

    assert len(notion.create_calls) == 1

    job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="notion",
    ).first()
    assert job is not None
    assert job.status == "succeeded"
    assert job.external_id == "notion-page-id-123"


def test_noop_skip(db_session: Session):
    """Same payload twice → second run does not re-call API.

    Succeeded jobs with unchanged payload stay succeeded.
    find_pending_jobs does not return them, so no API call happens.
    """
    article = _create_analyzed_article(db_session)
    chroma = FakeChromaAdapter()

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=chroma,
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )

    # First run — syncs
    agent.run()
    assert len(chroma.upsert_calls) == 1

    # Second run — same payload, jobs stay succeeded
    chroma2 = FakeChromaAdapter()
    agent2 = SyncAgent(
        session=db_session,
        chroma_adapter=chroma2,
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )
    stats = agent2.run()

    # No new API calls — succeeded jobs not re-processed
    assert len(chroma2.upsert_calls) == 0
    assert stats["synced"] == 0

    # Job still succeeded with original payload_hash
    job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="vector",
    ).first()
    assert job.status == "succeeded"
    assert job.payload_hash is not None


def test_vector_failure_does_not_block_notion(db_session: Session):
    """Vector fails, notion succeeds — independent."""
    article = _create_analyzed_article(db_session)
    chroma = FakeChromaAdapter(should_fail=True)
    notion = FakeNotionAdapter()

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=chroma,
        notion_adapter=notion,
        settings=FakeSettings(),
    )
    stats = agent.run()

    vector_job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="vector",
    ).first()
    notion_job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="notion",
    ).first()

    assert vector_job.status == "failed"
    assert notion_job.status == "succeeded"
    assert len(notion.create_calls) == 1


def test_retry_scheduling(db_session: Session):
    """Failed job gets attempt_count and next_retry_at."""
    article = _create_analyzed_article(db_session)

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(should_fail=True),
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )
    agent.run()

    job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="vector",
    ).first()
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.next_retry_at is not None


def test_disabled_target_no_job(db_session: Session):
    """Disabled target → no job created."""
    article = _create_analyzed_article(db_session)

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=None,
        settings=FakeSettingsVectorOnly(),
    )
    agent.run()

    notion_jobs = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="notion",
    ).all()
    assert len(notion_jobs) == 0

    vector_jobs = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="vector",
    ).all()
    assert len(vector_jobs) == 1


def test_resync_after_reanalysis(db_session: Session):
    """Succeeded job + new analysis → re-synced with new payload."""
    article = _create_analyzed_article(db_session)
    chroma = FakeChromaAdapter()

    # First sync
    agent = SyncAgent(
        session=db_session,
        chroma_adapter=chroma,
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )
    agent.run()
    assert len(chroma.upsert_calls) == 1

    job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="vector",
    ).first()
    old_hash = job.payload_hash
    old_synced_id = job.synced_analysis_id
    assert job.status == "succeeded"
    assert old_synced_id == article.current_analysis_id

    # Simulate re-analysis: new AnalysisRun with different content
    from heisenberg_agent.storage.models import AnalysisRun
    from sqlalchemy import update

    # Demote old current
    db_session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == article.current_analysis_id)
        .values(is_current=False)
    )

    new_run = AnalysisRun(
        article_id=article.id,
        source_content_hash="hash_new",
        analysis_version="analysis.v1",
        prompt_bundle_version="prompt-bundle.v1",
        summary_json=json.dumps({
            "core_thesis": "CHANGED THESIS",
            "supporting_points": ["New point"],
            "conclusion": "New conclusion",
            "keywords": ["New", "Keywords"],
            "importance": "medium",
        }),
        critique_json=json.dumps({
            "logic_gaps": [],
            "missing_views": [],
            "claims_to_verify": [],
            "interest_analysis": "New analysis",
            "overall_assessment": "New assessment",
        }),
        llm_model="fake-model",
        is_current=True,
        status="succeeded",
    )
    db_session.add(new_run)
    db_session.flush()
    article.current_analysis_id = new_run.id
    article.content_hash = "hash_new"
    db_session.commit()

    # Second sync — should detect analysis change and re-sync
    chroma2 = FakeChromaAdapter()
    agent2 = SyncAgent(
        session=db_session,
        chroma_adapter=chroma2,
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )
    stats = agent2.run()

    assert len(chroma2.upsert_calls) == 1  # re-synced
    assert stats["synced"] >= 1

    db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.payload_hash != old_hash
    assert job.synced_analysis_id == new_run.id


# ---------------------------------------------------------------------------
# Circuit breaker — Notion 429
# ---------------------------------------------------------------------------


def test_notion_429_circuit_breaker(db_session: Session):
    """First notion job hits 429 → remaining jobs deferred, not failed."""
    # Create 3 articles so we get 3 notion jobs
    articles = [
        _create_analyzed_article(db_session, slug=f"cb-{i}")
        for i in range(3)
    ]

    notion = FakeNotionAdapter(fail_429=True)
    agent = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion,
        settings=FakeSettings(),
    )
    stats = agent.run()

    notion_jobs = db_session.query(SyncJob).filter_by(target="notion").all()

    # Exactly 1 job should be failed (the one that hit 429)
    failed_jobs = [j for j in notion_jobs if j.status == "failed"]
    assert len(failed_jobs) == 1
    assert failed_jobs[0].attempt_count == 1

    # Remaining jobs should be pending with next_retry_at set
    deferred_jobs = [j for j in notion_jobs if j.status == "pending" and j.next_retry_at is not None]
    assert len(deferred_jobs) == 2
    for dj in deferred_jobs:
        assert dj.attempt_count == 0  # not incremented

    assert stats["deferred"] == 2
    assert stats["failed"] >= 1


# ---------------------------------------------------------------------------
# Failed event payload
# ---------------------------------------------------------------------------


def test_failed_event_contains_structured_payload(db_session: Session):
    """Failed sync job produces ArticleEvent with structured payload_json."""
    article = _create_analyzed_article(db_session)

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(should_fail=True),
        notion_adapter=FakeNotionAdapter(),
        settings=FakeSettings(),
    )
    agent.run()

    event = db_session.query(ArticleEvent).filter_by(
        article_id=article.id,
        event_type="sync.vector.failed",
    ).first()
    assert event is not None
    assert event.payload_json is not None

    payload = json.loads(event.payload_json)
    assert payload["target"] == "vector"
    assert "error_type" in payload
    assert "retryable" in payload
    assert "attempt_count" in payload
    assert "error_code" in payload
    assert "error_message" in payload


# ---------------------------------------------------------------------------
# Failure recovery — payload_hash not updated on partial failure
# ---------------------------------------------------------------------------


def _first_sync_then_setup_update(db_session: Session):
    """Helper: first sync creates page, then return article + job for update tests."""
    article = _create_analyzed_article(db_session)
    notion = FakeNotionAdapter()

    agent = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion,
        settings=FakeSettings(),
    )
    agent.run()

    job = db_session.query(SyncJob).filter_by(
        article_id=article.id, target="notion",
    ).first()
    assert job.status == "succeeded"
    assert job.external_id == "notion-page-id-123"
    first_hash = job.payload_hash

    # Change analysis to trigger a new payload hash
    from heisenberg_agent.storage.models import AnalysisRun
    from sqlalchemy import update

    db_session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == article.current_analysis_id)
        .values(is_current=False)
    )
    new_run = AnalysisRun(
        article_id=article.id,
        source_content_hash="hash_changed",
        analysis_version="analysis.v1",
        prompt_bundle_version="prompt-bundle.v1",
        summary_json=json.dumps({
            "core_thesis": "CHANGED",
            "supporting_points": ["New"],
            "conclusion": "New conclusion",
            "keywords": ["Changed"],
            "importance": "medium",
        }),
        critique_json=json.dumps({
            "logic_gaps": [], "missing_views": [],
            "claims_to_verify": [], "interest_analysis": "New",
            "overall_assessment": "New",
        }),
        llm_model="fake-model",
        is_current=True,
        status="succeeded",
    )
    db_session.add(new_run)
    db_session.flush()
    article.current_analysis_id = new_run.id
    article.content_hash = "hash_changed"
    db_session.commit()

    return article, job, first_hash


def test_property_update_failure_leaves_hash_unchanged(db_session: Session):
    """Property update failure → payload_hash NOT updated → next run retries full replace."""
    article, job, first_hash = _first_sync_then_setup_update(db_session)

    # Second sync with property update failure
    notion_fail = FakeNotionAdapter(fail_on_update=True)
    agent2 = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion_fail,
        settings=FakeSettings(),
    )
    stats = agent2.run()

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.payload_hash == first_hash  # hash NOT updated
    assert len(notion_fail.replace_body_calls) == 0  # body never attempted

    # Third sync with working adapter → full replace (not noop)
    notion_ok = FakeNotionAdapter()
    agent3 = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion_ok,
        settings=FakeSettings(),
    )
    stats3 = agent3.run()

    db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.payload_hash != first_hash  # hash updated after success
    assert len(notion_ok.update_calls) == 1
    assert len(notion_ok.replace_body_calls) == 1


def test_body_replace_failure_leaves_hash_unchanged(db_session: Session):
    """Body replace failure → payload_hash NOT updated → next run retries full replace."""
    article, job, first_hash = _first_sync_then_setup_update(db_session)

    # Second sync: property update succeeds, body replace fails
    notion_fail = FakeNotionAdapter(fail_on_replace_body=True)
    agent2 = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion_fail,
        settings=FakeSettings(),
    )
    stats = agent2.run()

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.payload_hash == first_hash  # hash NOT updated
    assert len(notion_fail.update_calls) == 1  # property update succeeded
    # replace_body was attempted but failed — calls list records only successes
    assert len(notion_fail.replace_body_calls) == 0

    # Third sync with working adapter → full replace (not noop)
    notion_ok = FakeNotionAdapter()
    agent3 = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion_ok,
        settings=FakeSettings(),
    )
    stats3 = agent3.run()

    db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.payload_hash != first_hash
    assert stats3["synced"] >= 1


def test_both_succeed_then_hash_updated(db_session: Session):
    """Properties + body both succeed → payload_hash IS updated → next run is noop."""
    article, job, first_hash = _first_sync_then_setup_update(db_session)

    # Second sync: both succeed
    notion_ok = FakeNotionAdapter()
    agent2 = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion_ok,
        settings=FakeSettings(),
    )
    stats = agent2.run()

    db_session.refresh(job)
    assert job.status == "succeeded"
    new_hash = job.payload_hash
    assert new_hash != first_hash  # hash updated

    # Third sync: noop (hash matches)
    notion_noop = FakeNotionAdapter()
    agent3 = SyncAgent(
        session=db_session,
        chroma_adapter=FakeChromaAdapter(),
        notion_adapter=notion_noop,
        settings=FakeSettings(),
    )
    stats3 = agent3.run()

    assert len(notion_noop.update_calls) == 0
    assert len(notion_noop.replace_body_calls) == 0
    assert stats3["synced"] == 0
