"""AnalyzerAgent — orchestrates article analysis.

Responsibilities:
- Find articles needing analysis
- Prepare input from article_sections (SSOT)
- Call LLM for summary + critique (structured output)
- Save analysis_runs (immutable history)
- Manage current_analysis_id pointer

Does NOT:
- Collect articles (CollectorAgent)
- Sync to Notion/ChromaDB (SyncAgent)

Status semantics:
- article.analyze_status = "last attempt status" (PENDING/SUCCEEDED/FAILED)
- article.current_analysis_id = pointer to the current VALID analysis run
- These are independent: analyze_status=FAILED + current_analysis_id=some_run
  means "last attempt failed but a previous valid analysis exists".
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from sqlalchemy.orm import Session

from heisenberg_agent.llm.client import LLMClient, LLMError, LLMResult
from heisenberg_agent.llm.schemas import CritiqueResult, SummaryResult
from heisenberg_agent.parsers.sections import SectionData, build_analysis_input
from heisenberg_agent.storage.models import Article, ArticleSection
from heisenberg_agent.storage.repositories import analyses as analysis_repo
from heisenberg_agent.utils.hashing import content_hash
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


class AnalyzerAgent:
    """Analyzes collected articles using structured LLM output."""

    def __init__(
        self,
        session: Session,
        llm_client: LLMClient,
        settings: Any,
    ) -> None:
        self._session = session
        self._llm = llm_client
        self._settings = settings

    def run(self) -> dict[str, int]:
        """Run one analysis cycle over all pending articles.

        Returns:
            Stats dict: {analyzed, skipped, failed}.
        """
        stats = {"analyzed": 0, "skipped": 0, "failed": 0}

        targets = analysis_repo.find_analysis_targets(self._session)
        logger.info("analyzer.targets_found", count=len(targets))

        for article in targets:
            self._process_one(article, stats)

        logger.info("analyzer.run_finished", **stats)
        return stats

    def _process_one(self, article: Article, stats: dict[str, int]) -> None:
        """Analyze a single article."""
        analysis_cfg = self._settings.analysis
        current_run = analysis_repo.get_current_run(self._session, article)

        # Check if analysis is needed
        decision = analysis_repo.needs_analysis(
            article,
            current_run,
            analysis_version=analysis_cfg.analysis_version,
            prompt_bundle_version=analysis_cfg.prompt_bundle_version,
        )

        if not decision.should_analyze:
            analysis_repo.record_skip(self._session, article, decision.reason)
            stats["skipped"] += 1
            return

        logger.info(
            "analyzer.analyzing",
            slug=article.slug,
            reason=decision.reason,
        )

        # Prepare input from sections (SSOT)
        db_sections = analysis_repo.get_article_sections(self._session, article.id)
        section_data = [
            SectionData(
                ordinal=s.ordinal,
                section_kind=s.section_kind,
                section_title=s.section_title,
                access_tier=s.access_tier or "unknown",
                is_gated_notice=s.is_gated_notice or False,
                body_text=s.body_text or "",
                body_html=s.body_html or "",
                content_hash=s.content_hash or "",
                selector_used=s.selector_used or "",
            )
            for s in db_sections
        ]

        max_chars = self._settings.analysis.__dict__.get("max_input_chars", 12000)
        input_text = build_analysis_input(section_data, max_chars=max_chars)

        if not input_text.strip():
            logger.warning("analyzer.empty_input", slug=article.slug)
            stats["failed"] += 1
            return

        # Base run data (shared between success and failure)
        base_run_data = {
            "source_content_hash": article.content_hash or "",
            "analysis_version": analysis_cfg.analysis_version,
            "prompt_bundle_version": analysis_cfg.prompt_bundle_version,
        }

        # Call LLM
        try:
            summary_result = self._llm.call(
                "summary.md", input_text, SummaryResult, task_key="summary",
            )
            critique_result = self._llm.call(
                "critique.md", input_text, CritiqueResult, task_key="critique",
            )
        except LLMError as e:
            logger.error("analyzer.llm_failed", slug=article.slug, error=str(e))
            self._save_failed(article, base_run_data, e, summary_result=None)
            stats["failed"] += 1
            return
        except Exception as e:
            logger.error("analyzer.unexpected_error", slug=article.slug, error=str(e))
            self._save_failed(article, base_run_data, e, summary_result=None)
            stats["failed"] += 1
            return

        # Save successful run
        self._save_success(article, base_run_data, summary_result, critique_result)
        stats["analyzed"] += 1

    def _save_success(
        self,
        article: Article,
        base_run_data: dict[str, Any],
        summary_result: LLMResult,
        critique_result: LLMResult,
    ) -> None:
        """Save a successful analysis run."""
        summary: SummaryResult = summary_result.data
        critique: CritiqueResult = critique_result.data

        # Merge usage from both calls
        s_usage = summary_result.usage
        c_usage = critique_result.usage

        run_data = {
            **base_run_data,
            "summary_json": summary.model_dump_json(),
            "critique_json": critique.model_dump_json(),
            "importance": summary.importance,
            "keywords_json": json.dumps(summary.keywords, ensure_ascii=False),
            "llm_provider": s_usage.provider,
            "llm_model": s_usage.model,
            "fallback_used": s_usage.fallback_used or c_usage.fallback_used,
            "input_tokens": (s_usage.input_tokens or 0) + (c_usage.input_tokens or 0),
            "output_tokens": (s_usage.output_tokens or 0) + (c_usage.output_tokens or 0),
            "cost_usd": (s_usage.cost_usd or 0.0) + (c_usage.cost_usd or 0.0),
            "latency_ms": (s_usage.latency_ms or 0) + (c_usage.latency_ms or 0),
        }

        try:
            analysis_repo.save_successful_run(
                self._session, article, run_data=run_data,
            )
        except Exception as e:
            self._session.rollback()
            logger.error("analyzer.save_failed", slug=article.slug, error=str(e))

    def _save_failed(
        self,
        article: Article,
        base_run_data: dict[str, Any],
        error: Exception,
        summary_result: LLMResult | None,
    ) -> None:
        """Save a failed analysis run. Old current is preserved."""
        run_data = {
            **base_run_data,
            "llm_provider": summary_result.usage.provider if summary_result else "",
            "llm_model": summary_result.usage.model if summary_result else "",
        }

        try:
            analysis_repo.save_failed_run(
                self._session,
                article,
                run_data=run_data,
                error_code=type(error).__name__,
                error_message=str(error),
            )
        except Exception as e2:
            self._session.rollback()
            logger.error(
                "analyzer.save_failed_error",
                slug=article.slug,
                original_error=str(error),
                secondary_error=str(e2),
            )
