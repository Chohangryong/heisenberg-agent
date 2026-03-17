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

        date_el = card.select_one(sel["date"])
        cat_el = card.select_one(sel["category"])
        author_el = card.select_one(sel["author"])
        tag_els = card.select(sel["tags"])

        items.append(ListItem(
            slug=slug,
            url=str(link),
            title=title_text,
            date=date_el.get("datetime") if date_el else None,
            category=cat_el.get_text(strip=True) if cat_el else None,
            author=author_el.get_text(strip=True) if author_el else None,
            tags=[t.get_text(strip=True) for t in tag_els],
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
    author_el = soup.select_one(sel["author"])
    cat_el = soup.select_one(sel["category"])
    date_el = soup.select_one(sel["published_at"])

    content_area = soup.select_one(sel["content_area"])
    image_urls: list[str] = []
    if content_area:
        for img in content_area.select("img"):
            src = img.get("src")
            if src:
                image_urls.append(str(src))

    return DetailResult(
        title=title_el.get_text(strip=True) if title_el else "",
        author=author_el.get_text(strip=True) if author_el else None,
        category=cat_el.get_text(strip=True) if cat_el else None,
        published_at=date_el.get("datetime") if date_el else None,
        rendered_html=html,
        image_urls=image_urls,
    )
