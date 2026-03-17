"""Extract article_sections from rendered DOM.

Splits the content area into typed sections based on selector profile.
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

    # Determine content area
    detail_sel = selectors.get("detail_page", {})
    content_area_sel = detail_sel.get("content_area")
    root = soup.select_one(content_area_sel) if content_area_sel else soup

    if root is None:
        return []

    sections: list[SectionData] = []
    ordinal = 0

    # Walk through known section kinds in selector order
    for kind, css_selector in section_selectors.items():
        el = root.select_one(css_selector)
        if el is None:
            continue

        text = el.get_text(separator="\n", strip=True)
        html_str = str(el)

        if not text:
            continue

        gated = _is_gated_notice(text, gated_patterns)
        tier = _detect_access_tier(text, tier_map) if gated else _infer_tier(kind)

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
    """Infer default access tier from section kind."""
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
