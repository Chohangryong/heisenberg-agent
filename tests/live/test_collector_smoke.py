"""Live smoke tests for collector — auth, list, detail, collect-one.

Requires:
  LIVE_SMOKE=1 pytest -m live tests/live/test_collector_smoke.py -v
"""

from __future__ import annotations

import pytest

from heisenberg_agent.agents.collector import CollectorAgent
from heisenberg_agent.parsers.sections import extract_sections
from heisenberg_agent.scrapers.heisenberg import parse_detail_page, parse_list_page
from heisenberg_agent.storage.models import Article, ArticleSection

pytestmark = pytest.mark.live

# Analyzable section kinds (from parsers/sections.py)
_ANALYZABLE_KINDS = {"one_minute_summary", "main_body", "researcher_opinion"}


# ---------------------------------------------------------------------------
# 1. Auth + storage_state reuse
# ---------------------------------------------------------------------------


def test_auth_and_storage_state(pw_adapter, live_settings, selectors, auth_state_path):
    """Login succeeds and auth_state.json is created in tmp."""
    cfg = live_settings.collector
    result = pw_adapter.ensure_authenticated(
        login_url=cfg.login_url,
        username=live_settings.heisenberg_username_or_email,
        password=live_settings.heisenberg_password,
        verification_url=cfg.latest_url,
        verification_selector=selectors["list_page"]["article_card"],
    )
    assert result.success, f"auth failed: {result.error_code}"
    assert auth_state_path.exists(), "auth_state.json not created"


# ---------------------------------------------------------------------------
# 2. List page reachable + parseable
# ---------------------------------------------------------------------------


def test_list_page_reachable(pw_adapter, live_settings, selectors):
    """Latest page loads and yields at least 1 article card."""
    html = pw_adapter.load_page(live_settings.collector.latest_url)
    items = parse_list_page(html, selectors)

    assert len(items) >= 1, "no articles found on latest page"
    for item in items:
        assert item.url, f"item missing url: {item}"
        assert item.title, f"item missing title: {item}"


# ---------------------------------------------------------------------------
# 3. Detail page parse
# ---------------------------------------------------------------------------


def test_detail_page_parse(pw_adapter, live_settings, selectors):
    """First article's detail page parses with required fields."""
    # Discover first article
    html = pw_adapter.load_page(live_settings.collector.latest_url)
    items = parse_list_page(html, selectors)
    assert items, "need at least 1 article to test detail"

    first = items[0]
    cfg = live_settings.collector
    full_url = (
        f"{cfg.base_url}{first.url}" if first.url.startswith("/") else first.url
    )

    detail_html = pw_adapter.load_page(
        full_url,
        ready_selector=selectors["detail_page"].get("content_area"),
    )

    # Metadata
    detail = parse_detail_page(detail_html, selectors)
    assert detail.title, "detail title is empty"

    # Sections — must have at least 1 analyzable kind
    sections = extract_sections(detail_html, selectors)
    analyzable = [s for s in sections if s.section_kind in _ANALYZABLE_KINDS]
    assert len(analyzable) >= 1, (
        f"no analyzable sections found. kinds={[s.section_kind for s in sections]}"
    )


# ---------------------------------------------------------------------------
# 4. Collect one article (CollectorAgent → temp SQLite)
# ---------------------------------------------------------------------------


def test_collect_one_article(pw_adapter, live_settings, live_session, selectors):
    """CollectorAgent saves at least 1 Article + sections to temp DB."""
    agent = CollectorAgent(
        adapter=pw_adapter,
        session=live_session,
        selectors=selectors,
        settings=live_settings,
    )

    agent.run()

    # Verify: at least 1 article
    articles = live_session.query(Article).all()
    assert len(articles) >= 1, "no articles saved"

    # Verify: at least 1 section per article
    for article in articles:
        sections = (
            live_session.query(ArticleSection)
            .filter(ArticleSection.article_id == article.id)
            .all()
        )
        assert len(sections) >= 1, f"article {article.slug} has no sections"
