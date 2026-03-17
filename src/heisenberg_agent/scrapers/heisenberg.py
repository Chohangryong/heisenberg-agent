"""heisenberg.kr HTML parser for list and detail pages.

Parses rendered DOM (not raw HTTP response).
Selectors are loaded from config/selectors/heisenberg.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from bs4 import BeautifulSoup, Tag


def load_selectors(path: str = "config/selectors/heisenberg.yaml") -> dict[str, Any]:
    """Load selector profile from YAML."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# List page parsing
# ---------------------------------------------------------------------------

@dataclass
class ListItem:
    slug: str
    url: str
    title: str
    date: str | None = None
    category: str | None = None
    author: str | None = None
    tags: list[str] = field(default_factory=list)


def _parse_category_date(text: str) -> tuple[str | None, str | None]:
    """Parse 'Category|Date' text into (category, date).

    Examples:
        'AI|2026.03.17' → ('AI', '2026.03.17')
        'AI' → ('AI', None)
    """
    if "|" in text:
        parts = text.split("|", 1)
        return parts[0].strip() or None, parts[1].strip() or None
    return text.strip() or None, None


def parse_list_page(html: str, selectors: dict[str, Any]) -> list[ListItem]:
    """Extract article cards from a list page."""
    soup = BeautifulSoup(html, "html.parser")
    sel = selectors["list_page"]
    items: list[ListItem] = []

    for card in soup.select(sel["article_card"]):
        title_el = card.select_one(sel["title"])
        if not title_el:
            continue

        link = title_el.get("href", "")
        title_text = title_el.get_text(strip=True)
        slug = link.strip("/").split("/")[-1] if link else ""

        # author from p.author
        author_el = card.select_one(sel["author"])
        author = author_el.get_text(strip=True) if author_el else None

        # category and date from p.category (format: "Category|Date")
        cat_el = card.select_one(sel["category"])
        category, date = None, None
        if cat_el:
            category, date = _parse_category_date(cat_el.get_text(strip=True))

        # tags from p.tag a
        tag_els = card.select(sel["tags"])
        tags = [t.get_text(strip=True).lstrip("#") for t in tag_els]

        items.append(ListItem(
            slug=slug,
            url=str(link),
            title=title_text,
            date=date,
            category=category,
            author=author,
            tags=tags,
        ))

    return items


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

@dataclass
class DetailResult:
    title: str
    author: str | None
    category: str | None
    published_at: str | None
    rendered_html: str
    image_urls: list[str] = field(default_factory=list)


def parse_detail_page(html: str, selectors: dict[str, Any]) -> DetailResult:
    """Extract metadata from a detail page."""
    soup = BeautifulSoup(html, "html.parser")
    sel = selectors["detail_page"]

    title_el = soup.select_one(sel["title"])

    # Category and date from meta_info (format: "Category|Date")
    meta_info_el = soup.select_one(sel.get("meta_info", ""))
    category, published_at = None, None
    if meta_info_el:
        category, published_at = _parse_category_date(meta_info_el.get_text(strip=True))

    content_area = soup.select_one(sel["content_area"])
    image_urls: list[str] = []
    if content_area:
        for img in content_area.select("img"):
            src = img.get("src")
            if src:
                image_urls.append(str(src))

    return DetailResult(
        title=title_el.get_text(strip=True) if title_el else "",
        author=None,  # author is not in detail page header; available via researcher_profile section
        category=category,
        published_at=published_at,
        rendered_html=html,
        image_urls=image_urls,
    )
