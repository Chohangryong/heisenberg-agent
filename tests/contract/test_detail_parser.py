"""Contract tests for detail page parser against HTML fixture."""

from pathlib import Path

from heisenberg_agent.scrapers.heisenberg import load_selectors, parse_detail_page

FIXTURE = Path(__file__).parent / "fixtures" / "detail_page_sample.html"


def _html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _selectors() -> dict:
    return load_selectors()


def test_extracts_title():
    result = parse_detail_page(_html(), _selectors())
    assert result.title == "GTC 2026 핵심 정리"


def test_extracts_author():
    result = parse_detail_page(_html(), _selectors())
    assert result.author == "김연구"


def test_extracts_category():
    result = parse_detail_page(_html(), _selectors())
    assert result.category == "AI"


def test_extracts_published_at():
    result = parse_detail_page(_html(), _selectors())
    assert result.published_at == "2026-03-15T09:00:00+09:00"


def test_extracts_image_urls():
    result = parse_detail_page(_html(), _selectors())
    assert len(result.image_urls) == 2
    assert "gtc2026-keynote.jpg" in result.image_urls[0]
    assert "blackwell-ultra-arch.png" in result.image_urls[1]


def test_preserves_rendered_html():
    result = parse_detail_page(_html(), _selectors())
    assert "article-content" in result.rendered_html
