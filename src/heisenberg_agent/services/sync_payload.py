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
    body = [
        {"type": "summary", "content": _format_summary(summary)},
        {"type": "critique", "content": _format_critique(critique)},
        {"type": "meta", "content": _format_meta(article, analysis_run)},
    ]

    payload = {"properties": properties, "body": body}
    return payload, payload_hash(canonicalize(payload))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_summary(summary: dict[str, Any]) -> str:
    parts = [
        f"핵심 논지: {summary.get('core_thesis', '')}",
        "주요 근거:",
        *[f"- {p}" for p in summary.get("supporting_points", [])],
        f"결론: {summary.get('conclusion', '')}",
        f"키워드: {', '.join(summary.get('keywords', []))}",
        f"중요도: {summary.get('importance', '')}",
    ]
    return "\n".join(parts)


def _format_critique(critique: dict[str, Any]) -> str:
    parts = [
        "논리적 허점:",
        *[f"- {g}" for g in critique.get("logic_gaps", [])],
        "누락된 관점:",
        *[f"- {v}" for v in critique.get("missing_views", [])],
        "검증 필요 주장:",
        *[f"- {c}" for c in critique.get("claims_to_verify", [])],
        f"이해관계 분석: {critique.get('interest_analysis', '')}",
        f"종합 평가: {critique.get('overall_assessment', '')}",
    ]
    return "\n".join(parts)


def _format_meta(article: Any, run: Any) -> str:
    return "\n".join([
        f"category: {article.category}",
        f"author: {article.author}",
        f"published_at: {_canonical_dt(article.published_at)}",
        f"analysis_version: {run.analysis_version}",
        f"content_hash: {article.content_hash}",
    ])


def _safe_json_load(json_str: str | None) -> dict[str, Any]:
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return {}
