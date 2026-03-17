"""AnalysisRun repository — CRUD for analysis runs.

Key rules:
- analysis_runs are immutable history. Only is_current flag changes.
- On success: demote old current → insert new current → update article pointer.
- On failure: old current stays. Failed run saved with is_current=false.
- article.analyze_status reflects "last attempt status", NOT "current valid analysis".
  The current valid analysis is determined by article.current_analysis_id.

All save operations run in a single transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from heisenberg_agent.storage.models import (
    AnalysisRun,
    Article,
    ArticleEvent,
    ArticleSection,
)
from heisenberg_agent.utils.dt import now_utc
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Reanalysis decision
# ---------------------------------------------------------------------------


@dataclass
class AnalysisDecision:
    """Result of needs_analysis() — should we analyze, and why."""

    should_analyze: bool
    reason: str


def needs_analysis(
    article: Article,
    current_run: AnalysisRun | None,
    analysis_version: str,
    prompt_bundle_version: str,
) -> AnalysisDecision:
    """Decide whether an article needs (re)analysis.

    Compares current_run.source_content_hash vs article.content_hash,
    plus analysis_version and prompt_bundle_version from settings.

    Args:
        article: The article to check.
        current_run: The current analysis run (from current_analysis_id), or None.
        analysis_version: Current analysis version from settings.
        prompt_bundle_version: Current prompt bundle version from settings.
    """
    if article.analyze_status == "PENDING":
        return AnalysisDecision(True, "status_pending")

    if current_run is None:
        return AnalysisDecision(True, "no_current_run")

    if current_run.source_content_hash != article.content_hash:
        return AnalysisDecision(True, "content_hash_changed")

    if current_run.analysis_version != analysis_version:
        return AnalysisDecision(True, "analysis_version_changed")

    if current_run.prompt_bundle_version != prompt_bundle_version:
        return AnalysisDecision(True, "prompt_bundle_version_changed")

    return AnalysisDecision(False, "up_to_date")


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def find_analysis_targets(session: Session) -> list[Article]:
    """Find articles that need analysis.

    Returns articles where collect_status=SUCCEEDED and
    analyze_status in (PENDING, FAILED).
    """
    stmt = select(Article).where(
        Article.collect_status == "SUCCEEDED",
        Article.analyze_status.in_(["PENDING", "FAILED"]),
    )
    return list(session.execute(stmt).scalars().all())


def get_current_run(session: Session, article: Article) -> AnalysisRun | None:
    """Get the current analysis run for an article."""
    if article.current_analysis_id is None:
        return None
    return session.get(AnalysisRun, article.current_analysis_id)


def get_article_sections(
    session: Session, article_id: int
) -> list[ArticleSection]:
    """Get article sections sorted by ordinal."""
    stmt = (
        select(ArticleSection)
        .where(ArticleSection.article_id == article_id)
        .order_by(ArticleSection.ordinal)
    )
    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Save — successful analysis (single transaction)
# ---------------------------------------------------------------------------


def save_successful_run(
    session: Session,
    article: Article,
    *,
    run_data: dict[str, Any],
) -> AnalysisRun:
    """Save a successful analysis run and promote it to current.

    In a single transaction:
    1. Demote existing current run (is_current=false)
    2. Insert new run (is_current=true, status=succeeded)
    3. Update article.current_analysis_id
    4. Set article.analyze_status = SUCCEEDED
    5. Record event

    Raises:
        Exception: On commit failure. Session is rolled back.
    """
    # 1. Demote old current
    session.execute(
        update(AnalysisRun)
        .where(
            AnalysisRun.article_id == article.id,
            AnalysisRun.is_current == True,  # noqa: E712
        )
        .values(is_current=False)
    )

    # 2. Insert new run
    new_run = AnalysisRun(
        article_id=article.id,
        is_current=True,
        status="succeeded",
        **run_data,
    )
    session.add(new_run)
    session.flush()  # new_run.id available

    # 3. Update article pointer
    article.current_analysis_id = new_run.id

    # 4. Update article status
    # analyze_status reflects "last attempt status"
    article.analyze_status = "SUCCEEDED"

    # 5. Event
    session.add(ArticleEvent(
        article_id=article.id,
        stage="analyzer",
        event_type="analysis.succeeded",
        payload_json=json.dumps({
            "run_id": new_run.id,
            "analysis_version": run_data.get("analysis_version"),
        }, ensure_ascii=False),
    ))

    session.commit()
    logger.info(
        "repo.analysis_saved",
        article_id=article.id,
        run_id=new_run.id,
        slug=article.slug,
    )
    return new_run


# ---------------------------------------------------------------------------
# Save — failed analysis (single transaction)
# ---------------------------------------------------------------------------


def save_failed_run(
    session: Session,
    article: Article,
    *,
    run_data: dict[str, Any],
    error_code: str,
    error_message: str,
) -> AnalysisRun:
    """Save a failed analysis run WITHOUT changing current.

    - Old current run stays is_current=true (if any).
    - article.current_analysis_id is NOT changed.
    - Failed run is saved with is_current=false, status=failed.
    - article.analyze_status = FAILED (reflects last attempt, not current valid state).

    Raises:
        Exception: On commit failure. Session is rolled back.
    """
    failed_run = AnalysisRun(
        article_id=article.id,
        is_current=False,
        status="failed",
        error_code=error_code,
        error_message=error_message[:500],
        **run_data,
    )
    session.add(failed_run)
    session.flush()

    # analyze_status reflects "last attempt status", NOT "current valid analysis".
    # The current valid analysis is article.current_analysis_id (unchanged here).
    article.analyze_status = "FAILED"
    article.analyze_attempt_count = (article.analyze_attempt_count or 0) + 1

    session.add(ArticleEvent(
        article_id=article.id,
        stage="analyzer",
        event_type="analysis.failed",
        payload_json=json.dumps({
            "run_id": failed_run.id,
            "error_code": error_code,
            "error": error_message[:500],
        }, ensure_ascii=False),
    ))

    session.commit()
    logger.warning(
        "repo.analysis_failed",
        article_id=article.id,
        run_id=failed_run.id,
        error_code=error_code,
    )
    return failed_run


# ---------------------------------------------------------------------------
# Skip — up to date
# ---------------------------------------------------------------------------


def record_skip(session: Session, article: Article, reason: str) -> None:
    """Record that analysis was skipped (already up to date)."""
    session.add(ArticleEvent(
        article_id=article.id,
        stage="analyzer",
        event_type="analysis.skipped",
        payload_json=json.dumps({"reason": reason}, ensure_ascii=False),
    ))
    session.commit()
    logger.info("repo.analysis_skipped", article_id=article.id, reason=reason)
