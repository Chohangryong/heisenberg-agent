"""Contract tests for list page parser against HTML fixture."""

from pathlib import Path

from heisenberg_agent.scrapers.heisenberg import load_selectors, parse_list_page

FIXTURE = Path(__file__).parent / "fixtures" / "list_page_sample.html"


def _html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _selectors() -> dict:
    return load_selectors()


def test_extracts_all_cards():
    items = parse_list_page(_html(), _selectors())
    assert len(items) == 3


def test_first_card_fields():
    items = parse_list_page(_html(), _selectors())
    first = items[0]
    assert first.slug == "gtc2026"
    assert first.url == "/gtc2026/"
    assert first.title == "GTC 2026 핵심 정리"
    assert first.date == "2026.03.15"
    assert first.category == "AI"
    assert first.author == "김연구"
    assert first.tags == ["GPU", "엔비디아"]


def test_card_without_tags():
    items = parse_list_page(_html(), _selectors())
    third = items[2]
    assert third.slug == "fed-rate-march"
    assert third.tags == []


def test_all_cards_have_required_fields():
    items = parse_list_page(_html(), _selectors())
    for item in items:
        assert item.slug
        assert item.url
        assert item.title
