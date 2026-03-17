"""Extract article_sections from rendered DOM.

Splits the content area into typed sections based on selector profile.

Section kind mapping (selector.v2):
- content-profile → researcher_profile
- content-summary → one_minute_summary
- content-chapter → main_body
- content-opinion → researcher_opinion
- content-like, content-reference, content-contact, content-tag → auxiliary

Access tier is derived from content-block class names:
- content-free, content-standard, content-business, content-vip, content-vvip
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup, Tag

from heisenberg_agent.utils.hashing import content_hash


@dataclass
class SectionData:
    ordinal: int
    section_kind: str
    section_title: str | None
    access_tier: str
    is_gated_notice: bool
    body_text: str
    body_html: str
    content_hash: str
    selector_used: str


def _detect_access_tier(text: str, tier_map: dict[str, str]) -> str:
    """Detect access tier from gated notice text."""
    for pattern, tier in tier_map.items():
        if pattern in text:
            return tier
    return "unknown"


def _is_gated_notice(text: str, patterns: list[str]) -> bool:
    """Check if text contains a gated notice pattern."""
    return any(p in text for p in patterns)


def _extract_title(el: Tag) -> str | None:
    """Extract section title from first heading if present."""
    heading = el.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        return heading.get_text(strip=True)
    return None


def _tier_from_classes(classes: list[str], class_map: dict[str, str]) -> str:
    """Extract access tier from element's CSS classes."""
    for cls in classes:
        if cls in class_map:
            return class_map[cls]
    return "unknown"


def extract_sections(
    html: str,
    selectors: dict[str, Any],
) -> list[SectionData]:
    """Parse rendered HTML into ordered sections.

    Args:
        html: Full page rendered HTML.
        selectors: Selector profile dict (from heisenberg.yaml).

    Returns:
        List of SectionData ordered by ordinal.
    """
    soup = BeautifulSoup(html, "html.parser")
    section_selectors = selectors["sections"]
    gated_patterns = selectors.get("gated_notice_patterns", [])
    tier_map = selectors.get("access_tier_map", {})
    tier_class_map = selectors.get("access_tier_class_map", {})

    # Determine content area
    detail_sel = selectors.get("detail_page", {})
    content_area_sel = detail_sel.get("content_area")
    root = soup.select_one(content_area_sel) if content_area_sel else soup

    if root is None:
        return []

    sections: list[SectionData] = []
    ordinal = 0

    # Walk through known section kinds in selector order.
    # A single selector may match multiple elements (e.g. content-reference × 2).
    for kind, css_selector in section_selectors.items():
        elements = root.select(css_selector)
        if not elements:
            continue

        for el in elements:
            text = el.get_text(separator="\n", strip=True)
            html_str = str(el)

            if not text:
                continue

            gated = _is_gated_notice(text, gated_patterns)
            classes = el.get("class", [])

            if gated:
                tier = _detect_access_tier(text, tier_map)
            elif tier_class_map:
                tier = _tier_from_classes(classes, tier_class_map)
            else:
                tier = _infer_tier(kind)

            ordinal += 1
            sections.append(SectionData(
                ordinal=ordinal,
                section_kind=kind,
                section_title=_extract_title(el),
                access_tier=tier,
                is_gated_notice=gated,
                body_text=text,
                body_html=html_str,
                content_hash=content_hash(text),
                selector_used=css_selector,
            ))

    return sections


def _infer_tier(kind: str) -> str:
    """Infer default access tier from section kind (fallback)."""
    public_kinds = {"researcher_profile", "one_minute_summary", "qa", "coffeechat"}
    if kind in public_kinds:
        return "public"
    if kind == "membership_gate_notice":
        return "unknown"
    return "logged_in"


def build_body_text(sections: list[SectionData]) -> str:
    """Build denormalized body_text from sections (derived cache)."""
    content_kinds = {"one_minute_summary", "main_body", "researcher_opinion"}
    parts = [s.body_text for s in sections if s.section_kind in content_kinds]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Analysis input assembly
# ---------------------------------------------------------------------------

# Section kinds included in analysis input (in priority order for truncation)
_ANALYSIS_INCLUDE_KINDS = {"one_minute_summary", "main_body", "researcher_opinion"}

# Section kinds excluded from analysis input
# researcher_profile, like, comments, contact, tag, misc

_TRUNCATION_MARKER = "\n\n[본문이 잘렸습니다]"

# Kinds that are preserved in full during truncation (shorter, high-value)
_PRESERVE_KINDS = {"one_minute_summary", "researcher_opinion"}


def build_analysis_input(
    sections: list[SectionData],
    max_chars: int = 12000,
) -> str:
    """Build analysis input text from article sections.

    Includes only analysis-relevant section kinds, sorted by ordinal.
    Each section is prefixed with "## {section_kind}" header.
    If total text exceeds max_chars, main_body is truncated from the end
    while one_minute_summary and researcher_opinion are preserved in full.

    Truncation contract:
    - Preserved kinds (summary, opinion) are emitted in full at their ordinal position.
    - Truncatable kinds (main_body) are joined into a single compressed block,
      truncated from the end, and emitted once at the position of the first
      truncatable section. Later truncatable sections are collapsed into this block,
      so their individual ordinal positions are NOT preserved after truncation.
    - This policy is accepted at the current stage.

    Args:
        sections: List of SectionData (from extract_sections or DB).
        max_chars: Maximum character count. Default 12000 (~3000-4000 tokens).

    Returns:
        Assembled text ready for LLM input.
    """
    # Filter and sort by ordinal
    relevant = sorted(
        [s for s in sections if s.section_kind in _ANALYSIS_INCLUDE_KINDS],
        key=lambda s: s.ordinal,
    )

    if not relevant:
        return ""

    # Build indexed parts: (original_position, kind, formatted_text)
    parts: list[tuple[int, str, str]] = []
    for idx, s in enumerate(relevant):
        formatted = f"## {s.section_kind}\n{s.body_text}"
        parts.append((idx, s.section_kind, formatted))

    # Check total length
    separator_len = (len(parts) - 1) * 2  # "\n\n" between parts
    total = sum(len(text) for _, _, text in parts) + separator_len
    if total <= max_chars:
        return "\n\n".join(text for _, _, text in parts)

    # Truncation needed — compute preserved (summary/opinion) vs truncatable (main_body)
    preserved_len = 0
    truncatable_texts: list[str] = []

    for _, kind, text in parts:
        if kind in _PRESERVE_KINDS:
            preserved_len += len(text) + 2  # +2 for "\n\n" separator
        else:
            truncatable_texts.append(text)

    # Join all truncatable parts into one block, then truncate as a whole
    truncatable_joined = "\n\n".join(truncatable_texts)
    marker_len = len(_TRUNCATION_MARKER)
    available = max(max_chars - preserved_len - marker_len, 100)

    if len(truncatable_joined) > available:
        truncatable_joined = truncatable_joined[:available] + _TRUNCATION_MARKER

    # Reassemble in original ordinal order
    # Walk parts in order, emit preserved text as-is, emit truncated block once
    result: list[str] = []
    truncatable_emitted = False

    for _, kind, text in parts:
        if kind in _PRESERVE_KINDS:
            result.append(text)
        elif not truncatable_emitted:
            result.append(truncatable_joined)
            truncatable_emitted = True
        # else: skip — truncatable content already emitted as one block

    return "\n\n".join(result)
