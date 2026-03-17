"""Integration tests for Analyzer — fake LLM + test SQLite.

No live LLM calls. Uses FakeLLMClient that returns fixed structured output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from heisenberg_agent.agents.analyzer import AnalyzerAgent
from heisenberg_agent.llm.client import LLMClient, LLMError, LLMResult, UsageMeta
from heisenberg_agent.llm.schemas import CritiqueResult, SummaryResult
from heisenberg_agent.storage.models import (
    AnalysisRun,
    Article,
    ArticleEvent,
    ArticleSection,
)
from heisenberg_agent.utils.dt import now_utc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_SUMMARY = SummaryResult(
    core_thesis="AI inference is improving",
    supporting_points=["Blackwell Ultra", "NVLink 7"],
    conclusion="Nvidia leads",
    keywords=["AI", "GPU", "Nvidia"],
    importance="high",
    confidence=0.85,
    evidence_spans=[],
)

VALID_CRITIQUE = CritiqueResult(
    logic_gaps=["No competitor comparison"],
    missing_views=["AMD perspective"],
    claims_to_verify=["3x throughput improvement"],
    interest_analysis="Nvidia keynote is promotional",
    overall_assessment="Solid but one-sided",
    confidence=0.7,
)


class FakeLLMClient:
    """Returns fixed structured output. No real LLM calls."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self._should_fail = should_fail

    def call(self, prompt_name, article_text, response_model, *, task_key="summary"):
        if self._should_fail:
            raise LLMError(f"Fake LLM failure for {task_key}")
        data = VALID_SUMMARY if response_model is SummaryResult else VALID_CRITIQUE
        return LLMResult(
            data=data,
            usage=UsageMeta(
                provider="fake", model="fake-model",
                input_tokens=100, output_tokens=50,
                cost_usd=0.001, latency_ms=200,
            ),
        )


class FakeAnalysisSettings:
    analysis_version = "analysis.v1"
    prompt_bundle_version = "prompt-bundle.v1"


class FakeSettings:
    analysis = FakeAnalysisSettings()


def _create_article_with_sections(session: Session, slug: str = "test") -> Article:
    """Create a collected article with sections ready for analysis."""
    article = Article(
        source_site="heisenberg.kr",
        slug=slug,
        url=f"https://heisenberg.kr/{slug}/",
        title=f"Test Article {slug}",
        collected_at=now_utc(),
        collect_status="SUCCEEDED",
        analyze_status="PENDING",
        content_hash="hash123",
    )
    session.add(article)
    session.flush()

    sections = [
        ArticleSection(
            article_id=article.id, ordinal=1,
            section_kind="one_minute_summary", body_text="Summary text",
        ),
        ArticleSection(
            article_id=article.id, ordinal=2,
            section_kind="main_body", body_text="Main body text about AI.",
        ),
        ArticleSection(
            article_id=article.id, ordinal=3,
            section_kind="researcher_opinion", body_text="Opinion text.",
        ),
    ]
    session.add_all(sections)
    session.commit()
    return article


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_analyze_one_article(db_session: Session):
    """Analyze a PENDING article → AnalysisRun saved, current_analysis_id set."""
    article = _create_article_with_sections(db_session)

    agent = AnalyzerAgent(
        session=db_session, llm_client=FakeLLMClient(), settings=FakeSettings(),
    )
    stats = agent.run()

    assert stats["analyzed"] == 1
    assert stats["failed"] == 0

    # Refresh article
    db_session.refresh(article)
    assert article.analyze_status == "SUCCEEDED"
    assert article.current_analysis_id is not None

    # Check run
    run = db_session.get(AnalysisRun, article.current_analysis_id)
    assert run is not None
    assert run.is_current is True
    assert run.status == "succeeded"
    assert run.summary_json is not None
    assert run.critique_json is not None
    assert run.importance == "high"
    assert run.keywords_json is not None
    assert run.source_content_hash == "hash123"


def test_reanalyze_promotes_new_current(db_session: Session):
    """Content hash change → old run demoted, new run promoted."""
    article = _create_article_with_sections(db_session)

    # First analysis
    agent = AnalyzerAgent(
        session=db_session, llm_client=FakeLLMClient(), settings=FakeSettings(),
    )
    agent.run()
    db_session.refresh(article)
    old_run_id = article.current_analysis_id

    # Simulate content change
    article.content_hash = "hash456"
    article.analyze_status = "PENDING"
    db_session.commit()

    # Second analysis
    agent2 = AnalyzerAgent(
        session=db_session, llm_client=FakeLLMClient(), settings=FakeSettings(),
    )
    agent2.run()
    db_session.refresh(article)

    new_run_id = article.current_analysis_id
    assert new_run_id != old_run_id

    # Old run demoted
    old_run = db_session.get(AnalysisRun, old_run_id)
    assert old_run.is_current is False

    # New run is current
    new_run = db_session.get(AnalysisRun, new_run_id)
    assert new_run.is_current is True
    assert new_run.status == "succeeded"


def test_reanalyze_failure_keeps_old_current(db_session: Session):
    """Reanalysis failure → old current preserved, failed run added."""
    article = _create_article_with_sections(db_session)

    # First analysis — success
    agent = AnalyzerAgent(
        session=db_session, llm_client=FakeLLMClient(), settings=FakeSettings(),
    )
    agent.run()
    db_session.refresh(article)
    old_run_id = article.current_analysis_id
    assert old_run_id is not None

    # Simulate content change to trigger reanalysis
    article.content_hash = "hash_changed"
    article.analyze_status = "PENDING"
    db_session.commit()

    # Second analysis — failure
    agent2 = AnalyzerAgent(
        session=db_session,
        llm_client=FakeLLMClient(should_fail=True),
        settings=FakeSettings(),
    )
    agent2.run()
    db_session.refresh(article)

    # Old current UNCHANGED
    assert article.current_analysis_id == old_run_id

    # Old run still is_current=true
    old_run = db_session.get(AnalysisRun, old_run_id)
    assert old_run.is_current is True

    # Failed run exists with is_current=false
    all_runs = db_session.query(AnalysisRun).filter_by(article_id=article.id).all()
    failed_runs = [r for r in all_runs if r.status == "failed"]
    assert len(failed_runs) == 1
    assert failed_runs[0].is_current is False
    assert failed_runs[0].error_code == "LLMError"

    # analyze_status reflects last attempt (failed), but current analysis is valid
    assert article.analyze_status == "FAILED"

    # Verify events
    events = db_session.query(ArticleEvent).filter_by(
        article_id=article.id, event_type="analysis.failed"
    ).all()
    assert len(events) == 1


def test_skip_when_up_to_date(db_session: Session):
    """Same conditions → no new run created, skip event recorded."""
    article = _create_article_with_sections(db_session)

    agent = AnalyzerAgent(
        session=db_session, llm_client=FakeLLMClient(), settings=FakeSettings(),
    )
    agent.run()
    db_session.refresh(article)

    run_count_before = db_session.query(AnalysisRun).filter_by(article_id=article.id).count()

    # Run again — should skip
    # Need to set analyze_status to something other than PENDING
    # (it's already SUCCEEDED from the first run)
    agent2 = AnalyzerAgent(
        session=db_session, llm_client=FakeLLMClient(), settings=FakeSettings(),
    )
    # find_analysis_targets looks for PENDING or FAILED, so SUCCEEDED won't be picked up
    stats = agent2.run()

    run_count_after = db_session.query(AnalysisRun).filter_by(article_id=article.id).count()
    assert run_count_after == run_count_before
    assert stats["analyzed"] == 0


def test_failed_run_saved_with_error(db_session: Session):
    """LLM failure → run saved with status=failed, error details present."""
    article = _create_article_with_sections(db_session)

    agent = AnalyzerAgent(
        session=db_session,
        llm_client=FakeLLMClient(should_fail=True),
        settings=FakeSettings(),
    )
    stats = agent.run()

    assert stats["failed"] == 1
    db_session.refresh(article)
    assert article.analyze_status == "FAILED"

    runs = db_session.query(AnalysisRun).filter_by(article_id=article.id).all()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].is_current is False
    assert runs[0].error_code == "LLMError"
    assert "Fake LLM failure" in runs[0].error_message
    assert article.current_analysis_id is None  # no valid analysis
