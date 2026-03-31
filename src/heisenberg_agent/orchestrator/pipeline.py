"""Pipeline orchestrator — collect → analyze → sync.

Owns the CollectionRun lifecycle. Each stage agent returns a StageSummary;
Pipeline is the sole authority for CollectionRun counter/status updates.

Agents receive run_id for event/log correlation only.
They do NOT modify CollectionRun directly.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from heisenberg_agent.runtime.locks import LockError, LockHandle, acquire, release
from heisenberg_agent.storage.models import CollectionRun
from heisenberg_agent.utils.dt import now_utc
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# StageSummary — uniform across all stages
# ---------------------------------------------------------------------------


@dataclass
class StageSummary:
    """Result summary from a pipeline stage.

    Fields have the same names across all stages.
    Semantic meaning per stage:
    - collect:  processed=discovered articles, succeeded=collected, skipped=existing/noop
    - analyze:  processed=analysis targets, succeeded=analyzed, skipped=up_to_date
    - sync.*:   processed=sync jobs attempted, succeeded=synced, skipped=noop
    """

    stage: str
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    fatal_error: str | None = None


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def derive_status(summaries: list[StageSummary]) -> str:
    """Derive pipeline run status from stage summaries.

    - "success": no failures, no fatal errors
    - "partial": some failures/fatals, but at least one success anywhere
    - "failed": fatal errors and zero successes across all stages
    """
    has_fatal = any(s.fatal_error for s in summaries)
    has_failure = any(s.failed > 0 for s in summaries)
    has_success = any(s.succeeded > 0 for s in summaries)

    if has_fatal and not has_success:
        return "failed"
    if has_failure or has_fatal:
        return "partial"
    return "success"


def compute_errors(summaries: list[StageSummary]) -> int:
    """Compute total error count from stage summaries.

    Includes both individual failures and fatal stage errors.
    Guarantees: status="failed" → errors >= 1.
    """
    total = sum(s.failed for s in summaries)
    total += sum(1 for s in summaries if s.fatal_error)
    return total


# ---------------------------------------------------------------------------
# Stage protocol
# ---------------------------------------------------------------------------


class StageAgent(Protocol):
    """Minimal interface for a pipeline stage agent."""

    def run(self, **kwargs: Any) -> Any:
        """Run the stage and return results."""
        ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """Orchestrates collect → analyze → sync with lock and reporting."""

    def __init__(
        self,
        session: Session,
        collector: StageAgent,
        analyzer: StageAgent,
        syncer: StageAgent,
        lock_path: str = "data/runtime/pipeline.lock",
    ) -> None:
        self._session = session
        self._collector = collector
        self._analyzer = analyzer
        self._syncer = syncer
        self._lock_path = lock_path

    def run(self) -> int:
        """Execute one pipeline cycle.

        Returns:
            run_id of the CollectionRun record.

        Raises:
            LockError: If another pipeline instance is running.
        """
        lock = acquire(self._lock_path)

        try:
            run_id = self._create_run()
            summaries = self._execute_stages(run_id)
            self._finalize_run(run_id, summaries)
            return run_id
        finally:
            release(lock)

    def _create_run(self) -> int:
        """Create a CollectionRun and return its ID."""
        run = CollectionRun(
            trigger_type="pipeline",
            started_at=now_utc(),
            status="running",
        )
        self._session.add(run)
        self._session.commit()
        return run.id

    def _execute_stages(self, run_id: int) -> list[StageSummary]:
        """Execute all stages, collecting summaries.

        Flow: collect ALL → per-article analyze+sync (incremental).
        """
        summaries: list[StageSummary] = []

        # Stage 1: Collect (batch)
        summaries.append(self._run_stage(
            "collect", lambda: self._run_collector(run_id),
        ))

        # Stage 2+3: Incremental analyze → sync per article
        try:
            analyze_summary, sync_summary = self._run_incremental_analyze_sync()
            summaries.append(analyze_summary)
            summaries.append(sync_summary)
        except Exception as e:
            logger.error("pipeline.incremental_fatal", error=str(e))
            summaries.append(StageSummary(stage="analyze", fatal_error=str(e)[:500]))
            summaries.append(StageSummary(stage="sync", fatal_error=str(e)[:500]))

        return summaries

    _LLM_MAX_WORKERS = 10  # Anthropic Tier1 50RPM min; 10 concurrent is safe

    def _run_incremental_analyze_sync(
        self,
    ) -> tuple[StageSummary, StageSummary]:
        """Parallel LLM analysis, then sequential DB save + sync.

        Phase 1: Prepare inputs (main thread, session) + parallel LLM calls
        Phase 2: Save results + sync per article (main thread, session)
        """
        a_stats = {"analyzed": 0, "skipped": 0, "failed": 0}
        s_stats = {"ensured": 0, "synced": 0, "skipped": 0, "failed": 0, "deferred": 0}

        targets = self._analyzer.find_targets()
        logger.info("pipeline.incremental_targets", count=len(targets))

        # Phase 1: Prepare inputs (main thread) and collect LLM tasks
        llm_tasks: list[tuple[Any, str, dict[str, Any]]] = []  # (article, input_text, base_data)
        for article in targets:
            prepared = self._analyzer.prepare_input(article)
            if prepared is None:
                a_stats["skipped"] += 1
                continue
            input_text, base_run_data = prepared
            llm_tasks.append((article, input_text, base_run_data))

        # Phase 1b: Parallel LLM calls (thread-safe, no session)
        llm_results: dict[int, tuple[Any, Exception | None]] = {}
        if llm_tasks:
            with ThreadPoolExecutor(max_workers=self._LLM_MAX_WORKERS) as pool:
                futures = {
                    pool.submit(self._analyzer.call_llm, input_text): (article, base_data)
                    for article, input_text, base_data in llm_tasks
                }
                for future in as_completed(futures):
                    article, base_data = futures[future]
                    try:
                        result = future.result()
                        llm_results[article.id] = (result, None)
                    except Exception as e:
                        llm_results[article.id] = (None, e)

        # Phase 2: Sequential DB save + sync (main thread)
        notion_blocked = False
        for article, _input_text, base_run_data in llm_tasks:
            llm_result, error = llm_results.get(article.id, (None, None))
            result = self._analyzer.save_result(article, base_run_data, llm_result, error)
            a_stats[result] += 1

            if result == "analyzed" and not notion_blocked:
                sync_result = self._syncer.sync_one(article)
                for k in s_stats:
                    s_stats[k] += sync_result.get(k, 0)

                if self._syncer.is_notion_rate_limited:
                    logger.warning("pipeline.notion_rate_limited_skipping_sync")
                    notion_blocked = True

        logger.info("analyzer.run_finished", **a_stats)
        logger.info("sync.run_finished", **s_stats)

        return (
            StageSummary(
                stage="analyze",
                processed=a_stats["analyzed"] + a_stats["skipped"] + a_stats["failed"],
                succeeded=a_stats["analyzed"],
                failed=a_stats["failed"],
                skipped=a_stats["skipped"],
            ),
            StageSummary(
                stage="sync",
                processed=s_stats["synced"] + s_stats["skipped"] + s_stats["failed"],
                succeeded=s_stats["synced"],
                failed=s_stats["failed"],
                skipped=s_stats["skipped"],
            ),
        )

    def _run_stage(
        self, stage_name: str, fn: Any,
    ) -> StageSummary:
        """Run a single stage with error boundary."""
        try:
            return fn()
        except Exception as e:
            logger.error(f"pipeline.{stage_name}_fatal", error=str(e))
            return StageSummary(
                stage=stage_name,
                fatal_error=str(e)[:500],
            )

    def _run_collector(self, run_id: int) -> StageSummary:
        """Run collector and convert result to StageSummary."""
        result = self._collector.run()

        # CollectorAgent.run() returns CollectionRun — extract stats
        if hasattr(result, "articles_found"):
            return StageSummary(
                stage="collect",
                processed=result.articles_found or 0,
                succeeded=result.articles_collected or 0,
                failed=result.errors or 0,
                skipped=(result.articles_found or 0)
                    - (result.articles_collected or 0)
                    - (result.errors or 0),
            )

        # Fallback: result is a dict
        return _dict_to_summary("collect", result)

    def _run_analyzer(self, run_id: int) -> StageSummary:
        """Run analyzer and convert result to StageSummary."""
        result = self._analyzer.run()
        return _dict_to_summary("analyze", result)

    def _run_sync_stage(self, run_id: int) -> list[StageSummary]:
        """Run syncer and convert result to per-target StageSummaries."""
        try:
            result = self._syncer.run()
        except Exception as e:
            logger.error("pipeline.sync_fatal", error=str(e))
            return [StageSummary(stage="sync", fatal_error=str(e)[:500])]

        # SyncAgent.run() returns dict with ensured/synced/skipped/failed
        if isinstance(result, dict) and "ensured" in result:
            return [StageSummary(
                stage="sync",
                processed=result.get("synced", 0) + result.get("skipped", 0) + result.get("failed", 0),
                succeeded=result.get("synced", 0),
                failed=result.get("failed", 0),
                skipped=result.get("skipped", 0),
            )]

        return [_dict_to_summary("sync", result)]

    def _finalize_run(
        self, run_id: int, summaries: list[StageSummary],
    ) -> None:
        """Update CollectionRun with final results. Pipeline is sole authority."""
        run = self._session.get(CollectionRun, run_id)
        if run is None:
            logger.error("pipeline.run_not_found", run_id=run_id)
            return

        # Map stage summaries to run columns
        collect = _find_stage(summaries, "collect")
        analyze = _find_stage(summaries, "analyze")
        sync = _find_stage(summaries, "sync")

        run.articles_found = collect.processed if collect else 0
        run.articles_collected = collect.succeeded if collect else 0
        run.articles_analyzed = analyze.succeeded if analyze else 0

        if sync:
            # Single sync summary — split not available at this level
            run.articles_synced_vector = 0
            run.articles_synced_notion = 0
            # Best effort: total synced count
            run.articles_synced_vector = sync.succeeded  # combined for now

        run.errors = compute_errors(summaries)
        run.status = derive_status(summaries)
        run.finished_at = now_utc()
        run.report_json = json.dumps(
            {"stages": [asdict(s) for s in summaries]},
            ensure_ascii=False,
        )

        self._session.commit()
        logger.info(
            "pipeline.finalized",
            run_id=run_id,
            status=run.status,
            errors=run.errors,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dict_to_summary(stage: str, result: Any) -> StageSummary:
    """Convert agent result dict to StageSummary."""
    if isinstance(result, dict):
        return StageSummary(
            stage=stage,
            processed=result.get("analyzed", 0) + result.get("skipped", 0) + result.get("failed", 0),
            succeeded=result.get("analyzed", 0),
            failed=result.get("failed", 0),
            skipped=result.get("skipped", 0),
        )
    return StageSummary(stage=stage)


def _find_stage(summaries: list[StageSummary], prefix: str) -> StageSummary | None:
    """Find first summary matching stage name prefix."""
    for s in summaries:
        if s.stage == prefix or s.stage.startswith(prefix + "."):
            return s
    return None
