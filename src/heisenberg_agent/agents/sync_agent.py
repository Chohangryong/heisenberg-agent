"""SyncAgent — orchestrates SQLite → ChromaDB / Notion synchronization.

sync_jobs is the sole authority for sync status.
Articles have no notion/vector status columns.
sync_jobs.external_id stores the Notion page_id or Chroma doc_id.

Each target (vector, notion) is processed independently.
One target's failure does not affect the other.

Datetime note:
- Application-level datetimes are aware UTC.
- SQLite lock comparisons in sync_jobs repo use naive UTC at the repository boundary.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from heisenberg_agent.adapters.chroma_adapter import ChromaAdapter, ChromaSyncError
from heisenberg_agent.adapters.notion_adapter import (
    NotionAdapter,
    NotionSyncError,
    RetryAfterError,
)
from heisenberg_agent.services.sync_payload import (
    build_notion_payload,
    build_vector_payload,
)
from heisenberg_agent.storage.models import (
    AnalysisRun,
    Article,
    ArticleAnnotation,
    ArticleTag,
    SyncJob,
    Tag,
)
from heisenberg_agent.storage.repositories import sync_jobs as sync_repo
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


class SyncAgent:
    """Synchronizes analyzed articles to ChromaDB and Notion."""

    def __init__(
        self,
        session: Session,
        chroma_adapter: ChromaAdapter | None,
        notion_adapter: NotionAdapter | None,
        settings: Any,
    ) -> None:
        self._session = session
        self._chroma = chroma_adapter
        self._notion = notion_adapter
        self._settings = settings

    def run(self) -> dict[str, int]:
        """Run one sync cycle.

        Returns:
            Stats: {ensured, synced, skipped, failed, deferred}.
        """
        stats = {
            "ensured": 0, "synced": 0, "skipped": 0,
            "failed": 0, "deferred": 0,
        }

        # 1. Ensure sync jobs exist for analyzed articles
        stats["ensured"] = self._ensure_all_jobs()

        enabled = self._enabled_targets()
        logger.info("sync.targets_enabled", targets=enabled)

        # 2. Process each target independently
        for target in enabled:
            self._process_target(target, stats)

        logger.info("sync.run_finished", **stats)
        return stats

    # ------------------------------------------------------------------
    # Step 1: Ensure sync jobs
    # ------------------------------------------------------------------

    def _ensure_all_jobs(self) -> int:
        """Create/re-arm sync jobs for all analyzed articles.

        Pre-computes payload hashes so ensure_sync_jobs can detect
        payload changes for re-arm decisions without rebuilding payloads.
        """
        enabled = self._enabled_targets()
        if not enabled:
            return 0

        embedding_version = self._settings.vectordb.embedding_version

        stmt = select(Article).where(
            Article.analyze_status == "SUCCEEDED",
            Article.current_analysis_id != None,  # noqa: E711
        )
        articles = list(self._session.execute(stmt).scalars().all())

        ensured_count = 0
        for article in articles:
            vector_hash = None
            notion_hash = None

            analysis_run = self._session.get(
                AnalysisRun, article.current_analysis_id,
            )
            if analysis_run is None:
                continue

            if "vector" in enabled:
                _, vector_hash = build_vector_payload(
                    article, analysis_run, embedding_version,
                )

            if "notion" in enabled:
                annotations = self._session.get(
                    ArticleAnnotation, article.id,
                )
                tag_names = self._load_tag_names(article.id)
                _, notion_hash = build_notion_payload(
                    article, analysis_run, annotations, tag_names,
                )

            sync_repo.ensure_sync_jobs(
                self._session, article, enabled, embedding_version,
                current_vector_hash=vector_hash,
                current_notion_hash=notion_hash,
            )
            ensured_count += 1

        logger.info("sync.jobs_ensured", articles_ensured=ensured_count)
        return ensured_count

    # ------------------------------------------------------------------
    # Step 2: Process jobs per target
    # ------------------------------------------------------------------

    def _process_target(self, target: str, stats: dict[str, int]) -> None:
        """Process all pending jobs for a target.

        Notion circuit breaker: on RetryAfterError, remaining jobs are
        deferred (next_retry_at set) without incrementing attempt_count,
        and the loop breaks immediately.
        """
        jobs = sync_repo.find_pending_jobs(self._session, target)
        logger.info("sync.processing_target", target=target, job_count=len(jobs))

        for i, job in enumerate(jobs):
            if not sync_repo.try_lock(self._session, job.id):
                continue

            try:
                self._process_one_job(job, target, stats)
            except RetryAfterError as e:
                # Circuit breaker: defer remaining notion jobs
                self._handle_rate_limit(e, job, jobs[i + 1:], stats)
                break
            except Exception as e:
                logger.error(
                    "sync.unexpected_error",
                    target=target, job_id=job.id, error=str(e),
                )
                self._session.rollback()
                stats["failed"] += 1
            finally:
                # Ensure unlock even on unexpected errors.
                # After rollback, session.refresh() may fail on a detached
                # instance — fall back to a direct UPDATE to clear the lock.
                try:
                    self._session.refresh(job)
                    if job.locked_at is not None:
                        sync_repo.unlock(self._session, job)
                except Exception:
                    logger.warning(
                        "sync.unlock_fallback",
                        job_id=job.id,
                        target=target,
                    )
                    sync_repo.force_unlock(self._session, job.id)

    def _handle_rate_limit(
        self,
        error: RetryAfterError,
        failed_job: SyncJob,
        remaining_jobs: list[SyncJob],
        stats: dict[str, int],
    ) -> None:
        """Handle 429 circuit breaker for a target.

        1. The job that hit 429 is marked failed (attempt_count +1).
        2. Remaining un-called jobs get next_retry_at set without
           incrementing attempt_count (no API call was made).
        """
        # Mark the triggering job as failed
        sync_repo.mark_failed(
            self._session, failed_job,
            error_code="429",
            error_message=str(error),
            error_type="rate_limit",
            retryable=True,
            retry_after_seconds=error.retry_after,
        )
        stats["failed"] += 1

        # Defer remaining jobs
        deferred_count = 0
        for remaining_job in remaining_jobs:
            sync_repo.defer_for_rate_limit(
                self._session, remaining_job, error.retry_after,
            )
            deferred_count += 1

        stats["deferred"] += deferred_count

        logger.warning(
            "sync.notion_rate_limited",
            deferred_count=deferred_count,
            retry_after=error.retry_after,
            article_id=failed_job.article_id,
            job_id=failed_job.id,
        )

    def _process_one_job(
        self, job: SyncJob, target: str, stats: dict[str, int],
    ) -> None:
        """Process a single sync job.

        RetryAfterError is NOT caught here — it propagates to
        _process_target for circuit breaker handling.
        """
        article = self._session.get(Article, job.article_id)
        if article is None or article.current_analysis_id is None:
            sync_repo.unlock(self._session, job)
            return

        analysis_run = self._session.get(AnalysisRun, article.current_analysis_id)
        if analysis_run is None:
            sync_repo.unlock(self._session, job)
            return

        if target == "vector":
            self._sync_vector(job, article, analysis_run, stats)
        elif target == "notion":
            self._sync_notion(job, article, analysis_run, stats)

    # ------------------------------------------------------------------
    # Vector sync
    # ------------------------------------------------------------------

    def _sync_vector(
        self,
        job: SyncJob,
        article: Article,
        analysis_run: AnalysisRun,
        stats: dict[str, int],
    ) -> None:
        embedding_version = self._settings.vectordb.embedding_version
        payload, new_hash = build_vector_payload(
            article, analysis_run, embedding_version,
        )

        # Noop check
        if (
            job.status == "succeeded"
            and job.payload_hash == new_hash
            and job.embedding_version == embedding_version
        ):
            sync_repo.record_noop(self._session, job)
            stats["skipped"] += 1
            return

        doc_id = f"article:{article.source_site}:{article.slug}"

        try:
            assert self._chroma is not None
            self._chroma.upsert(
                doc_id=doc_id,
                document=payload["document"],
                metadata=payload["metadata"],
            )
            # payload_hash is updated ONLY on full success
            sync_repo.mark_succeeded(
                self._session, job,
                payload_hash=new_hash,
                external_id=doc_id,
                embedding_version=embedding_version,
                synced_analysis_id=article.current_analysis_id,
            )
            stats["synced"] += 1
        except ChromaSyncError as e:
            logger.warning(
                "sync.job_failed",
                target="vector",
                error_type=e.error_type,
                retryable=e.retryable,
                attempt_count=(job.attempt_count or 0) + 1,
                article_id=job.article_id,
                job_id=job.id,
                error_code="chroma_error",
                error_message=str(e),
            )
            sync_repo.mark_failed(
                self._session, job,
                error_code="chroma_error",
                error_message=str(e),
                error_type=e.error_type,
                retryable=e.retryable,
            )
            stats["failed"] += 1

    # ------------------------------------------------------------------
    # Notion sync
    # ------------------------------------------------------------------

    def _sync_notion(
        self,
        job: SyncJob,
        article: Article,
        analysis_run: AnalysisRun,
        stats: dict[str, int],
    ) -> None:
        """Sync article to Notion.

        For updates, properties and body are updated separately.
        Non-atomic: properties are updated first, then body is replaced.
        payload_hash is updated ONLY when BOTH succeed — if either fails,
        the hash remains stale so the next run retries a full replace.
        """
        # Load annotations and tags
        annotations = self._session.get(ArticleAnnotation, article.id)
        tag_names = self._load_tag_names(article.id)

        payload, new_hash = build_notion_payload(
            article, analysis_run, annotations, tag_names,
        )

        # Noop check
        if job.status == "succeeded" and job.payload_hash == new_hash:
            sync_repo.record_noop(self._session, job)
            stats["skipped"] += 1
            return

        try:
            assert self._notion is not None
            if job.external_id:
                # Update path: properties first, then body replace.
                # Non-atomic — if body replace fails after property update,
                # payload_hash is NOT updated so next run retries full replace.
                page_id = self._notion.update_page(
                    page_id=job.external_id,
                    properties=payload["properties"],
                )
                self._notion.replace_body(
                    page_id=job.external_id,
                    children=payload["body"],
                )
            else:
                # Create path: no adapter-level retry (non-idempotent).
                # If create succeeds but response/save fails, next run
                # will create a duplicate page (orphan risk).
                logger.warning(
                    "sync.notion.create_orphan_risk",
                    article_id=job.article_id,
                    job_id=job.id,
                    url=payload["properties"].get("url"),
                )
                page_id = self._notion.create_page(
                    properties=payload["properties"],
                    children=payload["body"],
                )
            # payload_hash is updated ONLY on full success
            # (both properties and body for update path)
            sync_repo.mark_succeeded(
                self._session, job,
                payload_hash=new_hash,
                external_id=page_id,
                synced_analysis_id=article.current_analysis_id,
            )
            stats["synced"] += 1
        except RetryAfterError:
            # Propagate to _process_target for circuit breaker handling
            raise
        except NotionSyncError as e:
            logger.warning(
                "sync.job_failed",
                target="notion",
                error_type=e.error_type,
                retryable=e.retryable,
                attempt_count=(job.attempt_count or 0) + 1,
                article_id=job.article_id,
                job_id=job.id,
                error_code="notion_error",
                error_message=str(e),
            )
            sync_repo.mark_failed(
                self._session, job,
                error_code="notion_error",
                error_message=str(e),
                error_type=e.error_type,
                retryable=e.retryable,
            )
            stats["failed"] += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enabled_targets(self) -> list[str]:
        targets: list[str] = []
        if getattr(self._settings.vectordb, "enabled", True):
            targets.append("vector")
        if getattr(self._settings.notion, "enabled", True):
            targets.append("notion")
        return targets

    def _load_tag_names(self, article_id: int) -> list[str]:
        stmt = (
            select(Tag.name)
            .join(ArticleTag, ArticleTag.tag_id == Tag.id)
            .where(ArticleTag.article_id == article_id)
        )
        return list(self._session.execute(stmt).scalars().all())
