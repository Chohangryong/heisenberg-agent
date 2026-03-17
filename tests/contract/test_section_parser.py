"""Contract tests for section parser against HTML fixture."""

from pathlib import Path

from heisenberg_agent.parsers.sections import (
    SectionData,
    build_body_text,
    extract_sections,
)
from heisenberg_agent.scrapers.heisenberg import load_selectors
from heisenberg_agent.utils.hashing import content_hash

FIXTURE = Path(__file__).parent / "fixtures" / "detail_page_sample.html"


def _html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _selectors() -> dict:
    return load_selectors()


def _sections() -> list[SectionData]:
    return extract_sections(_html(), _selectors())


def test_extracts_expected_section_count():
    """Fixture has 10 sections: profile, summary, chapter, opinion, like,
    comments, sources, contact_vip, contact_vvip, tag."""
    sections = _sections()
    assert len(sections) == 10


def test_section_kinds():
    sections = _sections()
    kinds = [s.section_kind for s in sections]
    assert "researcher_profile" in kinds
    assert "one_minute_summary" in kinds
    assert "main_body" in kinds
    assert "researcher_opinion" in kinds
    assert "like" in kinds
    assert "comments" in kinds  # content-reference (x2)
    assert "contact" in kinds   # content-contact (x2)
    assert "tag" in kinds


def test_ordinals_are_sequential():
    sections = _sections()
    ordinals = [s.ordinal for s in sections]
    assert ordinals == list(range(1, len(sections) + 1))


def test_gated_notice_detected():
    sections = _sections()
    opinion = [s for s in sections if s.section_kind == "researcher_opinion"][0]
    assert opinion.is_gated_notice is True
    assert opinion.access_tier == "business"


def test_non_gated_sections():
    sections = _sections()
    body = [s for s in sections if s.section_kind == "main_body"][0]
    assert body.is_gated_notice is False


def test_section_titles_extracted():
    sections = _sections()
    summary = [s for s in sections if s.section_kind == "one_minute_summary"][0]
    assert summary.section_title == "1분 요약"


def test_body_text_not_empty():
    sections = _sections()
    for s in sections:
        assert s.body_text, f"{s.section_kind} has empty body_text"


def test_body_html_contains_tags():
    sections = _sections()
    body = [s for s in sections if s.section_kind == "main_body"][0]
    assert "<li>" in body.body_html


def test_content_hash_stable():
    sections = _sections()
    body = [s for s in sections if s.section_kind == "main_body"][0]
    expected = content_hash(body.body_text)
    assert body.content_hash == expected


def test_content_hash_differs_across_sections():
    sections = _sections()
    hashes = [s.content_hash for s in sections]
    assert len(set(hashes)) == len(hashes), "All sections should have unique hashes"


def test_selector_used_recorded():
    sections = _sections()
    for s in sections:
        assert s.selector_used, f"{s.section_kind} missing selector_used"


def test_access_tier_from_classes():
    """Access tier is derived from CSS class names in v2."""
    sections = _sections()
    profile = [s for s in sections if s.section_kind == "researcher_profile"][0]
    assert profile.access_tier == "free"
    body = [s for s in sections if s.section_kind == "main_body"][0]
    assert body.access_tier == "standard"


def test_build_body_text():
    sections = _sections()
    body_text = build_body_text(sections)
    assert "Blackwell Ultra" in body_text
    assert "추론 효율 3배" in body_text
    assert "양산 일정" in body_text
    # Excludes researcher_profile, like, comments, contact, tag
    assert "김연구 — AI/반도체" not in body_text
    assert "커피챗" not in body_text
