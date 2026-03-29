"""Unit tests for CollectorAgent._filter() logic."""

from datetime import datetime, timedelta, timezone

from heisenberg_agent.agents.collector import CollectorAgent, Disposition, FilteredItem
from heisenberg_agent.scrapers.heisenberg import ListItem
from heisenberg_agent.storage.models import Article


def _make_item(slug: str) -> ListItem:
    return ListItem(slug=slug, url=f"/{slug}/", title=f"Title {slug}")


def _make_article(slug: str, collected_at: datetime) -> Article:
    return Article(
        source_site="heisenberg.kr",
        slug=slug,
        url=f"https://heisenberg.kr/{slug}/",
        title=f"Title {slug}",
        collected_at=collected_at,
    )


class FakeSettings:
    class collector:
        base_url = "https://heisenberg.kr"
        duplicate_safety_window_days = 7


class FakeAdapter:
    def ensure_authenticated(self, **kw):
        pass

    def load_page(self, url, **kw):
        return ""

    def take_snapshot(self, path):
        return None


def _make_agent(session) -> CollectorAgent:
    return CollectorAgent(
        adapter=FakeAdapter(),
        session=session,
        selectors={"list_page": {}, "detail_page": {}, "sections": {}},
        settings=FakeSettings(),
    )


def test_new_article(db_session):
    """Unknown slug → NEW."""
    agent = _make_agent(db_session)
    result = agent._filter([_make_item("new-slug")])
    assert len(result) == 1
    assert result[0].disposition == Disposition.NEW
    assert result[0].existing_article is None


def test_recent_article_recheck(db_session):
    """Known slug within safety window → RECHECK."""
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    article = _make_article("recent", recent)
    db_session.add(article)
    db_session.commit()

    agent = _make_agent(db_session)
    result = agent._filter([_make_item("recent")])
    assert result[0].disposition == Disposition.RECHECK
    assert result[0].existing_article is not None


def test_old_article_skip(db_session):
    """Known slug outside safety window → SKIP."""
    old = datetime.now(timezone.utc) - timedelta(days=30)
    article = _make_article("old", old)
    db_session.add(article)
    db_session.commit()

    agent = _make_agent(db_session)
    result = agent._filter([_make_item("old")])
    assert result[0].disposition == Disposition.SKIP


def test_mixed_items(db_session):
    """Multiple items with different dispositions."""
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    old = datetime.now(timezone.utc) - timedelta(days=30)

    db_session.add(_make_article("recent", recent))
    db_session.add(_make_article("old", old))
    db_session.commit()

    agent = _make_agent(db_session)
    items = [_make_item("brand-new"), _make_item("recent"), _make_item("old")]
    result = agent._filter(items)

    dispositions = {r.item.slug: r.disposition for r in result}
    assert dispositions["brand-new"] == Disposition.NEW
    assert dispositions["recent"] == Disposition.RECHECK
    assert dispositions["old"] == Disposition.SKIP


def test_exact_cutoff_is_skip(db_session, monkeypatch):
    """collected_at exactly at cutoff (now - 7 days) → SKIP."""
    fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "heisenberg_agent.agents.collector.now_utc", lambda: fixed_now
    )

    cutoff = fixed_now - timedelta(days=7)
    article = _make_article("exact-cutoff", cutoff)
    db_session.add(article)
    db_session.commit()

    agent = _make_agent(db_session)
    result = agent._filter([_make_item("exact-cutoff")])
    assert result[0].disposition == Disposition.SKIP


def test_just_after_cutoff_is_recheck(db_session, monkeypatch):
    """collected_at one second after cutoff → RECHECK."""
    fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "heisenberg_agent.agents.collector.now_utc", lambda: fixed_now
    )

    cutoff = fixed_now - timedelta(days=7)
    collected_at = cutoff + timedelta(seconds=1)
    article = _make_article("just-after", collected_at)
    db_session.add(article)
    db_session.commit()

    agent = _make_agent(db_session)
    result = agent._filter([_make_item("just-after")])
    assert result[0].disposition == Disposition.RECHECK
