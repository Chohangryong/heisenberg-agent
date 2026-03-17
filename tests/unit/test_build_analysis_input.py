"""Unit tests for build_analysis_input() — analysis input assembly contract."""

from heisenberg_agent.parsers.sections import SectionData, build_analysis_input
from heisenberg_agent.utils.hashing import content_hash


def _section(ordinal: int, kind: str, text: str) -> SectionData:
    """Helper to create a minimal SectionData."""
    return SectionData(
        ordinal=ordinal,
        section_kind=kind,
        section_title=None,
        access_tier="logged_in",
        is_gated_notice=False,
        body_text=text,
        body_html=f"<p>{text}</p>",
        content_hash=content_hash(text),
        selector_used="div.test",
    )


# ---------------------------------------------------------------------------
# Inclusion / exclusion
# ---------------------------------------------------------------------------


def test_includes_analysis_kinds():
    """one_minute_summary, main_body, researcher_opinion are included."""
    sections = [
        _section(1, "one_minute_summary", "Summary text"),
        _section(2, "main_body", "Body text"),
        _section(3, "researcher_opinion", "Opinion text"),
    ]
    result = build_analysis_input(sections)
    assert "Summary text" in result
    assert "Body text" in result
    assert "Opinion text" in result


def test_excludes_non_analysis_kinds():
    """researcher_profile, membership_gate_notice, qa, coffeechat, misc are excluded."""
    sections = [
        _section(1, "researcher_profile", "Profile text"),
        _section(2, "main_body", "Body text"),
        _section(3, "membership_gate_notice", "Gate text"),
        _section(4, "qa", "QA text"),
        _section(5, "coffeechat", "Coffee text"),
        _section(6, "misc", "Misc text"),
    ]
    result = build_analysis_input(sections)
    assert "Body text" in result
    assert "Profile text" not in result
    assert "Gate text" not in result
    assert "QA text" not in result
    assert "Coffee text" not in result
    assert "Misc text" not in result


# ---------------------------------------------------------------------------
# Ordinal sorting
# ---------------------------------------------------------------------------


def test_ordinal_sorting():
    """Sections are sorted by ordinal regardless of input order."""
    sections = [
        _section(3, "researcher_opinion", "Opinion"),
        _section(1, "one_minute_summary", "Summary"),
        _section(2, "main_body", "Body"),
    ]
    result = build_analysis_input(sections)
    summary_pos = result.index("Summary")
    body_pos = result.index("Body")
    opinion_pos = result.index("Opinion")
    assert summary_pos < body_pos < opinion_pos


# ---------------------------------------------------------------------------
# Section headers
# ---------------------------------------------------------------------------


def test_section_headers():
    """Each section gets a ## header with section_kind."""
    sections = [
        _section(1, "main_body", "Body text"),
    ]
    result = build_analysis_input(sections)
    assert "## main_body" in result


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_no_truncation_under_limit():
    """Short text is not truncated."""
    sections = [
        _section(1, "main_body", "Short body"),
    ]
    result = build_analysis_input(sections, max_chars=10000)
    assert "[본문이 잘렸습니다]" not in result


def test_truncation_adds_marker():
    """Long main_body gets truncated with marker."""
    long_body = "가" * 15000
    sections = [
        _section(1, "one_minute_summary", "Summary"),
        _section(2, "main_body", long_body),
        _section(3, "researcher_opinion", "Opinion"),
    ]
    result = build_analysis_input(sections, max_chars=1000)
    assert "[본문이 잘렸습니다]" in result


def test_truncation_preserves_summary_and_opinion():
    """one_minute_summary and researcher_opinion are preserved during truncation."""
    long_body = "나" * 15000
    summary_text = "요약 텍스트 전체 보존"
    opinion_text = "의견 텍스트 전체 보존"
    sections = [
        _section(1, "one_minute_summary", summary_text),
        _section(2, "main_body", long_body),
        _section(3, "researcher_opinion", opinion_text),
    ]
    result = build_analysis_input(sections, max_chars=1000)
    assert summary_text in result
    assert opinion_text in result


def test_truncation_main_body_shortened():
    """main_body content is shorter than original after truncation."""
    long_body = "다" * 15000
    sections = [
        _section(1, "main_body", long_body),
    ]
    result = build_analysis_input(sections, max_chars=500)
    # Result should be shorter than original
    assert len(result) <= 600  # 500 + marker + header


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_sections():
    """Empty input returns empty string."""
    assert build_analysis_input([]) == ""


def test_only_excluded_kinds():
    """Only non-analysis sections returns empty string."""
    sections = [
        _section(1, "researcher_profile", "Profile"),
        _section(2, "qa", "QA"),
    ]
    assert build_analysis_input(sections) == ""


def test_duplicate_section_kind_stable_order():
    """Multiple sections with same kind maintain ordinal order."""
    sections = [
        _section(1, "main_body", "Part A"),
        _section(2, "main_body", "Part B"),
        _section(3, "one_minute_summary", "Summary"),
        _section(4, "main_body", "Part C"),
    ]
    result = build_analysis_input(sections)
    a_pos = result.index("Part A")
    b_pos = result.index("Part B")
    summary_pos = result.index("Summary")
    c_pos = result.index("Part C")
    assert a_pos < b_pos < summary_pos < c_pos
