"""Integration test — full collect flow with fake adapter + test SQLite.

No live site or browser dependency. Uses fixture HTML for parsing.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from heisenberg_agent.agents.collector import CollectorAgent, Disposition
from heisenberg_agent.scrapers.heisenberg import load_selectors
from heisenberg_agent.storage.models import (
    Article,
    ArticleEvent,
    ArticleImage,
    ArticleSection,
    ArticleTag,
    CollectionRun,
    Tag,
)

FIXTURES = Path(__file__).parent.parent / "contract" / "fixtures"


# ---------------------------------------------------------------------------
# Fake adapter — returns fixture HTML, no browser
# ---------------------------------------------------------------------------

@dataclass
class FakeAuthResult:
    success: bool = True
    error_code: str | None = None
    attempts: int = 0


class FakeAdapter:
    """Returns fixture HTML for any URL. No browser."""

    def __init__(self) -> None:
        self._list_html = (FIXTURES / "list_page_sample.html").read_text("utf-8")
        self._detail_html = (FIXTURES / "detail_page_sample.html").read_text("utf-8")

    def ensure_authenticated(self, **kw: Any) -> FakeAuthResult:
        return FakeAuthResult()

    def load_page(self, url: str, **kw: Any) -> str:
        if "latest" in url:
            return self._list_html
        return self._detail_html

    def take_snapshot(self, output_path: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Fake settings
# ---------------------------------------------------------------------------

class _DelaySettings:
    min = 0
    max = 0


class _CollectorSettings:
    base_url = "https://heisenberg.kr"
    login_url = "https://heisenberg.kr/login/"
    latest_url = "https://heisenberg.kr/latest/"
    max_pages_to_scan = 1
    max_articles_per_cycle = 5
    duplicate_safety_window_days = 7
    request_delay_seconds = _DelaySettings()


class FakeSettings:
    collector = _CollectorSettings()
    heisenberg_username_or_email = "test"
    heisenberg_password = "test"
    data_dir = "/tmp/heisenberg-test"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_full_collect_one_article(db_session: Session):
    """Collect from fixture HTML → verify Article + Sections + Tags + Images in DB."""
    selectors = load_selectors()
    adapter = FakeAdapter()

    agent = CollectorAgent(
        adapter=adapter,
        session=db_session,
        selectors=selectors,
        settings=FakeSettings(),
    )
    run = agent.run()

    # Run should succeed with zero errors
    assert run.status == "success", f"expected success, got {run.status}"
    assert run.errors == 0, f"expected 0 errors, got {run.errors}"
    assert run.articles_found == 3  # fixture has 3 cards

    # Articles created
    articles = db_session.query(Article).all()
    assert len(articles) >= 1

    # Check first article
    gtc = db_session.query(Article).filter_by(slug="gtc2026").first()
    assert gtc is not None
    assert gtc.title == "GTC 2026 핵심 정리"
    assert gtc.collect_status == "SUCCEEDED"
    assert gtc.content_hash is not None
    assert gtc.last_seen_at is not None

    # Sections created
    sections = db_session.query(ArticleSection).filter_by(article_id=gtc.id).all()
    assert len(sections) == 10
    kinds = {s.section_kind for s in sections}
    assert "main_body" in kinds
    assert "one_minute_summary" in kinds

    # Images
    images = db_session.query(ArticleImage).filter_by(article_id=gtc.id).all()
    assert len(images) == 2

    # Tags
    tag_joins = db_session.query(ArticleTag).filter_by(article_id=gtc.id).all()
    assert len(tag_joins) == 2  # GPU, 엔비디아

    # Events
    events = db_session.query(ArticleEvent).filter_by(article_id=gtc.id).all()
    assert any(e.event_type == "detail.saved" for e in events)


def test_rerun_is_noop(db_session: Session):
    """Second run with same content → NOOP (no duplicate rows)."""
    selectors = load_selectors()
    adapter = FakeAdapter()

    agent = CollectorAgent(
        adapter=adapter,
        session=db_session,
        selectors=selectors,
        settings=FakeSettings(),
    )

    # First run
    agent.run()
    count_after_first = db_session.query(Article).count()

    # Second run — same content, should be RECHECK → NOOP
    agent2 = CollectorAgent(
        adapter=adapter,
        session=db_session,
        selectors=selectors,
        settings=FakeSettings(),
    )
    run2 = agent2.run()

    count_after_second = db_session.query(Article).count()
    assert count_after_second == count_after_first

    # Check noop events exist
    events = db_session.query(ArticleEvent).filter_by(event_type="detail.skipped_noop").all()
    assert len(events) >= 1


def test_collection_run_created(db_session: Session):
    """CollectionRun record is created with stats."""
    selectors = load_selectors()
    adapter = FakeAdapter()

    agent = CollectorAgent(
        adapter=adapter,
        session=db_session,
        selectors=selectors,
        settings=FakeSettings(),
    )
    run = agent.run()

    db_run = db_session.get(CollectionRun, run.id)
    assert db_run is not None
    assert db_run.started_at is not None
    assert db_run.finished_at is not None
    assert db_run.articles_found == 3


def test_auth_failure_records_error(db_session: Session):
    """Auth failure → run status=failed, error recorded."""

    class FailAuthAdapter(FakeAdapter):
        def ensure_authenticated(self, **kw):
            return FakeAuthResult(success=False, error_code="login_failed", attempts=3)

    selectors = load_selectors()
    agent = CollectorAgent(
        adapter=FailAuthAdapter(),
        session=db_session,
        selectors=selectors,
        settings=FakeSettings(),
    )
    run = agent.run()

    assert run.status == "failed"
    db_run = db_session.get(CollectionRun, run.id)
    assert db_run.errors >= 1


def test_duplicate_slugs_across_pages_deduped(db_session: Session):
    """Multi-page discover with overlapping articles → dedupe before filter.

    Simulates page 1 and page 2 both returning the same 3 articles.
    After dedupe only 3 unique articles should be processed, not 6.
    """

    class MultiPageAdapter(FakeAdapter):
        """Returns the same list HTML for both page 1 and page 2."""

        def load_page(self, url: str, **kw: Any) -> str:
            if "latest" in url:
                return self._list_html
            return self._detail_html

    class _MultiPageCollectorSettings(_CollectorSettings):
        max_pages_to_scan = 2  # two pages → duplicates

    class _MultiPageSettings(FakeSettings):
        collector = _MultiPageCollectorSettings()

    selectors = load_selectors()
    agent = CollectorAgent(
        adapter=MultiPageAdapter(),
        session=db_session,
        selectors=selectors,
        settings=_MultiPageSettings(),
    )
    run = agent.run()

    assert run.status == "success", f"expected success, got {run.status}"
    assert run.errors == 0, f"expected 0 errors, got {run.errors}"
    # 3 unique articles (not 6)
    assert run.articles_found == 3
    articles = db_session.query(Article).all()
    assert len(articles) == 3


def test_integrity_error_absorbed_as_noop(db_session: Session):
    """If dedupe misses and IntegrityError fires, absorb as duplicate noop.

    Approach: first run inserts normally. Second run uses a fake adapter
    that makes the discover step return 1 page, but filter sees all items
    as NEW because we clear the session identity map (simulate stale lookup).
    Instead we force the scenario: directly insert an article, then run
    the collector which discovers the same slug → IntegrityError → absorbed.
    """
    from heisenberg_agent.storage.repositories import articles as article_repo
    from heisenberg_agent.utils.dt import now_utc

    selectors = load_selectors()

    # Pre-insert an article that matches the first fixture card
    article_repo.save_new_article(
        db_session,
        article_data={
            "source_site": "heisenberg.kr",
            "slug": "gtc2026",
            "url": "https://heisenberg.kr/gtc2026/",
            "title": "GTC 2026 핵심 정리",
            "collected_at": now_utc(),
        },
        sections=[],
        image_urls=[],
        tag_names=[],
    )
    count_before = db_session.query(Article).count()
    assert count_before == 1

    # Run collector — gtc2026 will be seen as NEW (fresh filter lookup returns
    # it, but it already exists in DB). The save_new_article will hit
    # IntegrityError which should be absorbed.
    # The other 2 articles are genuinely new.
    agent = CollectorAgent(
        adapter=FakeAdapter(),
        session=db_session,
        selectors=selectors,
        settings=FakeSettings(),
    )
    run = agent.run()

    assert run.errors == 0, f"expected 0 errors, got {run.errors}"
    assert run.status == "success"
    # 3 unique articles total (1 pre-existing + 2 new)
    articles = db_session.query(Article).all()
    assert len(articles) == 3
