"""Sync payload builders — pure functions for payload assembly + hashing.

Canonicalization rules:
- json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
- list[str] fields (tags, keywords): sorted(set(values))
- datetime: timezone-aware ISO8601. Naive datetimes treated as UTC.
- None/missing optional: explicit null (key preserved)
- float: round(value, 4)
- Notion body blocks: fixed order [summary, critique, meta]
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from heisenberg_agent.utils.hashing import payload_hash


def canonicalize(data: dict[str, Any]) -> str:
    """Produce a canonical JSON string for deterministic hashing."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def _canonical_dt(dt: datetime | None) -> str | None:
    """Serialize datetime as timezone-aware ISO8601.

    Naive datetimes are assumed UTC. None becomes null.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _sorted_strings(values: list[str] | None) -> list[str]:
    """Deduplicate and sort a list of strings."""
    if not values:
        return []
    return sorted(set(values))


# ---------------------------------------------------------------------------
# Vector payload
# ---------------------------------------------------------------------------


def build_vector_payload(
    article: Any,
    analysis_run: Any,
    embedding_version: str,
) -> tuple[dict[str, Any], str]:
    """Build ChromaDB upsert payload and its hash.

    Args:
        article: Article model instance.
        analysis_run: Current AnalysisRun instance.
        embedding_version: From settings.vectordb.embedding_version.

    Returns:
        (payload_dict, payload_hash_hex)
    """
    summary = _safe_json_load(analysis_run.summary_json)

    keywords = _sorted_strings(summary.get("keywords", []))
    supporting = summary.get("supporting_points", [])

    document = "\n\n".join(filter(None, [
        article.title,
        summary.get("core_thesis", ""),
        "\n".join(supporting) if supporting else "",
        ", ".join(keywords) if keywords else "",
    ]))

    metadata = {
        "source_site": article.source_site,
        "slug": article.slug,
        "url": article.url,
        "category": article.category,
        "published_at": _canonical_dt(article.published_at),
        "analysis_version": analysis_run.analysis_version,
        "embedding_version": embedding_version,
        "content_hash": article.content_hash,
    }

    payload = {"document": document, "metadata": metadata}
    return payload, payload_hash(canonicalize(payload))


# ---------------------------------------------------------------------------
# Notion payload
# ---------------------------------------------------------------------------


def build_notion_payload(
    article: Any,
    analysis_run: Any,
    annotations: Any | None,
    tags: list[str],
) -> tuple[dict[str, Any], str]:
    """Build Notion page payload and its hash.

    Args:
        article: Article model instance.
        analysis_run: Current AnalysisRun instance.
        annotations: ArticleAnnotation instance or None.
        tags: List of tag name strings.

    Returns:
        (payload_dict, payload_hash_hex)
    """
    summary = _safe_json_load(analysis_run.summary_json)
    critique = _safe_json_load(analysis_run.critique_json)

    keywords = _sorted_strings(summary.get("keywords", []))
    sorted_tags = _sorted_strings(tags)

    properties = {
        "title": article.title,
        "published_at": _canonical_dt(article.published_at),
        "url": article.url,
        "category": article.category,
        "author": article.author,
        "tags": sorted_tags,
        "importance": summary.get("importance"),
        "keywords": keywords,
        "is_read": annotations.is_read if annotations else False,
        "starred": annotations.starred if annotations else False,
        "llm_model": analysis_run.llm_model,
        "analysis_version": analysis_run.analysis_version,
    }

    # Body blocks in fixed order: summary, critique, meta
    # Uses Notion block format directly for rich formatting
    body = [
        *_build_summary_blocks(summary),
        _divider(),
        *_build_critique_blocks(critique),
        _divider(),
        *_build_meta_blocks(article, analysis_run),
    ]

    payload = {"properties": properties, "body": body}
    return payload, payload_hash(canonicalize(payload))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _heading2(text: str) -> dict[str, Any]:
    return {"type": "heading_2", "heading_2": {
        "rich_text": [{"text": {"content": text}}],
    }}


def _heading3(text: str) -> dict[str, Any]:
    return {"type": "heading_3", "heading_3": {
        "rich_text": [{"text": {"content": text}}],
    }}


def _paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "paragraph": {
        "rich_text": [{"text": {"content": text}}],
    }}


def _bullet(text: str) -> dict[str, Any]:
    return {"type": "bulleted_list_item", "bulleted_list_item": {
        "rich_text": [{"text": {"content": text}}],
    }}


def _divider() -> dict[str, Any]:
    return {"type": "divider", "divider": {}}


def _build_summary_blocks(summary: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    blocks.append(_heading2("📋 요약"))

    thesis = summary.get("core_thesis", "")
    if thesis:
        blocks.append(_heading3("핵심 논지"))
        blocks.append(_paragraph(thesis))

    points = summary.get("supporting_points", [])
    if points:
        blocks.append(_heading3("주요 근거"))
        for p in points:
            blocks.append(_bullet(p))

    conclusion = summary.get("conclusion", "")
    if conclusion:
        blocks.append(_heading3("결론"))
        blocks.append(_paragraph(conclusion))

    keywords = summary.get("keywords", [])
    importance = summary.get("importance", "")
    if keywords or importance:
        meta_parts = []
        if keywords:
            meta_parts.append(f"키워드: {', '.join(keywords)}")
        if importance:
            meta_parts.append(f"중요도: {importance}")
        blocks.append(_paragraph(" | ".join(meta_parts)))

    return blocks


def _build_critique_blocks(critique: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    blocks.append(_heading2("🔍 비판적 분석"))

    for label, key in [
        ("논리적 허점", "logic_gaps"),
        ("누락된 관점", "missing_views"),
        ("검증 필요 주장", "claims_to_verify"),
    ]:
        items = critique.get(key, [])
        if items:
            blocks.append(_heading3(label))
            for item in items:
                blocks.append(_bullet(item))

    interest = critique.get("interest_analysis", "")
    if interest:
        blocks.append(_heading3("이해관계 분석"))
        blocks.append(_paragraph(interest))

    assessment = critique.get("overall_assessment", "")
    if assessment:
        blocks.append(_heading3("종합 평가"))
        blocks.append(_paragraph(assessment))

    return blocks


def _build_meta_blocks(article: Any, run: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    blocks.append(_heading2("ℹ️ 메타 정보"))
    blocks.append(_paragraph(
        f"카테고리: {article.category or '-'} | "
        f"저자: {article.author or '-'} | "
        f"발행일: {_canonical_dt(article.published_at) or '-'}"
    ))
    blocks.append(_paragraph(
        f"분석 버전: {run.analysis_version} | "
        f"콘텐츠 해시: {article.content_hash or '-'}"
    ))
    return blocks


def _safe_json_load(json_str: str | None) -> dict[str, Any]:
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return {}
