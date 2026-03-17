"""CollectorAgent — orchestrates article collection.

Responsibilities:
- authenticate → discover → filter → collect_detail → save
- One article at a time through collect_detail + save
- Delegates HTML parsing to scrapers/parsers, DB ops to repositories
- Delegates browser control to PlaywrightAdapter

Does NOT:
- Run LLM analysis (AnalyzerAgent)
- Sync to Notion/ChromaDB (SyncAgent)
- Manage pipeline orchestration (Pipeline)
"""

from __future__ import annotations

import json
import time
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from heisenberg_agent.parsers.sections import SectionData, build_body_text, extract_sections
from heisenberg_agent.scrapers.heisenberg import (
    DetailResult,
    ListItem,
    parse_detail_page,
    parse_list_page,
)
from heisenberg_agent.storage.models import Article, CollectionRun
from heisenberg_agent.storage.repositories import articles as article_repo
from heisenberg_agent.utils.dt import now_utc
from heisenberg_agent.utils.hashing import content_hash, file_sha256
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO datetime string to datetime object."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Adapter protocol — for testing with fakes
# ---------------------------------------------------------------------------


class BrowserAdapter(Protocol):
    """Minimal interface that CollectorAgent needs from a browser adapter."""

    def ensure_authenticated(
        self,
        login_url: str,
        username: str,
        password: str,
        verification_url: str,
        verification_selector: str,
        max_attempts: int = 3,
    ) -> Any:
        """Returns an object with .success (bool), .error_code, .attempts."""
        ...

    def load_page(
        self,
        url: str,
        ready_selector: str | None = None,
        timeout_ms: int = 10000,
    ) -> str:
        """Returns rendered HTML."""
        ...

    def take_snapshot(self, output_path: str | Path) -> Path | None:
        """Returns Path to PDF or None on failure."""
        ...


# ---------------------------------------------------------------------------
# Filter disposition
# ---------------------------------------------------------------------------


class Disposition:
    NEW = "NEW"
    RECHECK = "RECHECK"
    SKIP = "SKIP"


@dataclass
class FilteredItem:
    item: ListItem
    disposition: str  # Disposition.NEW | RECHECK | SKIP
    existing_article: Article | None = None


# ---------------------------------------------------------------------------
# CollectorAgent
# ---------------------------------------------------------------------------


class CollectorAgent:
    """Collects articles from heisenberg.kr into SQLite."""

    def __init__(
        self,
        adapter: BrowserAdapter,
        session: Session,
        selectors: dict[str, Any],
        settings: Any,  # AppSettings or compatible
    ) -> None:
        self._adapter = adapter
        self._session = session
        self._selectors = selectors
        self._settings = settings

    def run(self) -> CollectionRun:
        """Execute one collection cycle.

        Returns:
            CollectionRun with final stats.
        """
        run = self._create_run()

        # 1. Authenticate
        auth = self._authenticate()
        if not auth.success:
            run.status = "failed"
            run.finished_at = now_utc()
            article_repo.record_run_error(
                self._session, run.id, slug="", url="",
                error=f"auth_failed: {auth.error_code}",
            )
            return run

        # 2. Discover
        list_items = self._discover()
        run.articles_found = len(list_items)
        self._session.commit()

        # 3. Filter
        filtered = self._filter(list_items)
        targets = [f for f in filtered if f.disposition != Disposition.SKIP]

        # 4+5. Collect detail + save (per article)
        for fi in targets:
            self._process_one(fi, run)
            self._polite_delay()

        # Finalize run
        run.status = "success" if run.errors == 0 else "partial"
        run.finished_at = now_utc()
        self._session.commit()

        logger.info(
            "collector.run_finished",
            run_id=run.id,
            found=run.articles_found,
            collected=run.articles_collected,
            errors=run.errors,
            status=run.status,
        )
        return run

    # ------------------------------------------------------------------
    # Step 1: Authenticate
    # ------------------------------------------------------------------

    def _authenticate(self) -> Any:
        """Authenticate via adapter. Returns AuthResult."""
        cfg = self._settings.collector
        return self._adapter.ensure_authenticated(
            login_url=cfg.login_url,
            username=self._settings.heisenberg_username_or_email,
            password=self._settings.heisenberg_password,
            verification_url=cfg.latest_url,
            verification_selector=self._selectors["list_page"]["article_card"],
        )

    # ------------------------------------------------------------------
    # Step 2: Discover
    # ------------------------------------------------------------------

    def _discover(self) -> list[ListItem]:
        """Load latest page(s) and extract article list."""
        cfg = self._settings.collector
        all_items: list[ListItem] = []

        for page_num in range(1, cfg.max_pages_to_scan + 1):
            url = cfg.latest_url if page_num == 1 else f"{cfg.latest_url}page/{page_num}/"
            try:
                html = self._adapter.load_page(url)
                items = parse_list_page(html, self._selectors)
                all_items.extend(items)
                logger.info("collector.discover_page", page=page_num, count=len(items))
                if not items:
                    break
            except Exception as e:
                logger.error("collector.discover_failed", page=page_num, error=str(e))
                break

        return all_items[:cfg.max_articles_per_cycle]

    # ------------------------------------------------------------------
    # Step 3: Filter
    # ------------------------------------------------------------------

    def _filter(self, items: list[ListItem]) -> list[FilteredItem]:
        """Classify each item as NEW, RECHECK, or SKIP.

        - NEW: slug not in DB
        - RECHECK: slug exists, collected within safety window
        - SKIP: slug exists, outside safety window
        """
        cfg = self._settings.collector
        source_site = cfg.base_url.replace("https://", "").replace("http://", "")

        slugs = [item.slug for item in items]
        existing = article_repo.find_existing_slugs(self._session, source_site, slugs)

        window = timedelta(days=cfg.duplicate_safety_window_days)
        cutoff = now_utc() - window

        result: list[FilteredItem] = []
        for item in items:
            article = existing.get(item.slug)
            if article is None:
                result.append(FilteredItem(item=item, disposition=Disposition.NEW))
            elif article.collected_at and article.collected_at.replace(tzinfo=timezone.utc) > cutoff:
                result.append(FilteredItem(
                    item=item, disposition=Disposition.RECHECK, existing_article=article,
                ))
            else:
                result.append(FilteredItem(
                    item=item, disposition=Disposition.SKIP, existing_article=article,
                ))

        counts = {d: sum(1 for f in result if f.disposition == d)
                  for d in (Disposition.NEW, Disposition.RECHECK, Disposition.SKIP)}
        logger.info("collector.filter_result", **counts)
        return result

    # ------------------------------------------------------------------
    # Step 4+5: Collect detail + save
    # ------------------------------------------------------------------

    def _process_one(self, fi: FilteredItem, run: CollectionRun) -> None:
        """Collect detail for one article and save to DB."""
        item = fi.item
        cfg = self._settings.collector
        source_site = cfg.base_url.replace("https://", "").replace("http://", "")
        full_url = f"{cfg.base_url}{item.url}" if item.url.startswith("/") else item.url

        try:
            # Load detail page
            ready_sel = self._selectors["detail_page"].get("content_area")
            html = self._adapter.load_page(full_url, ready_selector=ready_sel)

            # Parse metadata
            detail = parse_detail_page(html, self._selectors)

            # Extract sections
            sections = extract_sections(html, self._selectors)

            # Build derived cache
            body_text = build_body_text(sections)
            c_hash = content_hash(body_text) if body_text else None

            # Snapshot
            snapshot_path, snapshot_sha, snapshot_size = self._take_snapshot(item.slug)

            # Prepare data
            article_data = {
                "source_site": source_site,
                "slug": item.slug,
                "url": full_url,
                "title": detail.title,
                "author": detail.author,
                "category": detail.category,
                "published_at": _parse_datetime(detail.published_at),
                "collected_at": now_utc(),
                "rendered_html": detail.rendered_html,
                "rendered_html_hash": content_hash(detail.rendered_html) if detail.rendered_html else None,
                "body_text": body_text,
                "body_text_hash": content_hash(body_text) if body_text else None,
                "content_hash": c_hash,
                "selector_profile_version": self._selectors.get("version"),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "snapshot_sha256": snapshot_sha,
                "snapshot_byte_size": snapshot_size,
            }

            section_dicts = [
                {
                    "ordinal": s.ordinal,
                    "section_kind": s.section_kind,
                    "section_title": s.section_title,
                    "access_tier": s.access_tier,
                    "is_gated_notice": s.is_gated_notice,
                    "body_text": s.body_text,
                    "body_html": s.body_html,
                    "content_hash": s.content_hash,
                    "selector_used": s.selector_used,
                }
                for s in sections
            ]

            # Decide: INSERT / UPDATE / NOOP
            if fi.disposition == Disposition.NEW:
                article_repo.save_new_article(
                    self._session,
                    article_data=article_data,
                    sections=section_dicts,
                    image_urls=detail.image_urls,
                    tag_names=item.tags,
                )
                run.articles_collected += 1

            elif fi.disposition == Disposition.RECHECK:
                existing = fi.existing_article
                assert existing is not None
                if existing.content_hash != c_hash:
                    article_repo.update_article(
                        self._session,
                        existing,
                        article_data=article_data,
                        sections=section_dicts,
                        image_urls=detail.image_urls,
                        tag_names=item.tags,
                    )
                    run.articles_collected += 1
                else:
                    article_repo.mark_noop(self._session, existing)

            logger.info("collector.article_done", slug=item.slug, disposition=fi.disposition)

        except Exception as e:
            logger.error("collector.article_failed", slug=item.slug, error=str(e))
            self._session.rollback()

            # Record failure
            if fi.disposition == Disposition.NEW:
                article_repo.record_run_error(
                    self._session, run.id, slug=item.slug, url=full_url, error=str(e),
                )
            elif fi.existing_article is not None:
                article_repo.mark_failed(
                    self._session, fi.existing_article,
                    error_code=type(e).__name__, error_message=str(e),
                )
            run.errors += 1

    def _take_snapshot(self, slug: str) -> tuple[Path | None, str | None, int | None]:
        """Take PDF snapshot, return (path, sha256, byte_size) or Nones."""
        snapshot_dir = Path(self._settings.data_dir) / "snapshots"
        out = snapshot_dir / f"{slug}.pdf"
        path = self._adapter.take_snapshot(out)
        if path and path.exists():
            return path, file_sha256(str(path)), path.stat().st_size
        return None, None, None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_run(self) -> CollectionRun:
        """Create a new CollectionRun record."""
        run = CollectionRun(
            trigger_type="manual",
            started_at=now_utc(),
            status="running",
        )
        self._session.add(run)
        self._session.commit()
        return run

    def _polite_delay(self) -> None:
        """Random delay between requests to be polite."""
        delay = self._settings.collector.request_delay_seconds
        time.sleep(random.uniform(delay.min, delay.max))
