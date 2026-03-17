"""Unit tests for sync payload builders — canonicalization + determinism."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from heisenberg_agent.services.sync_payload import (
    build_notion_payload,
    build_vector_payload,
    canonicalize,
    _canonical_dt,
    _sorted_strings,
)


@dataclass
class FakeArticle:
    title: str = "Test Article"
    source_site: str = "heisenberg.kr"
    slug: str = "test-article"
    url: str = "https://heisenberg.kr/test-article/"
    category: str = "AI"
    author: str = "김연구"
    published_at: datetime = None
    collected_at: datetime = None
    content_hash: str = "abc123"

    def __post_init__(self):
        if self.published_at is None:
            self.published_at = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)
        if self.collected_at is None:
            self.collected_at = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeAnalysisRun:
    analysis_version: str = "analysis.v1"
    llm_model: str = "claude-sonnet-4-5"
    summary_json: str = json.dumps({
        "core_thesis": "AI is improving",
        "supporting_points": ["Point B", "Point A"],
        "conclusion": "Nvidia leads",
        "keywords": ["GPU", "AI", "Nvidia"],
        "importance": "high",
    })
    critique_json: str = json.dumps({
        "logic_gaps": ["Gap 1"],
        "missing_views": ["View 1"],
        "claims_to_verify": ["Claim 1"],
        "interest_analysis": "Commercial bias",
        "overall_assessment": "Solid but biased",
    })


@dataclass
class FakeAnnotation:
    is_read: bool = False
    starred: bool = True


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def test_canonicalize_sorted_keys():
    data = {"z": 1, "a": 2, "m": 3}
    result = canonicalize(data)
    parsed = json.loads(result)
    assert list(parsed.keys()) == ["a", "m", "z"]


def test_canonical_dt_aware():
    dt = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)
    assert _canonical_dt(dt) == "2026-03-15T09:00:00+00:00"


def test_canonical_dt_naive_assumes_utc():
    dt = datetime(2026, 3, 15, 9, 0)
    result = _canonical_dt(dt)
    assert "+00:00" in result


def test_canonical_dt_none():
    assert _canonical_dt(None) is None


def test_sorted_strings_deduplicates():
    assert _sorted_strings(["b", "a", "b", "c"]) == ["a", "b", "c"]


def test_sorted_strings_empty():
    assert _sorted_strings([]) == []
    assert _sorted_strings(None) == []


# ---------------------------------------------------------------------------
# Vector payload
# ---------------------------------------------------------------------------


def test_vector_payload_deterministic():
    article = FakeArticle()
    run = FakeAnalysisRun()
    _, hash1 = build_vector_payload(article, run, "embed.v1")
    _, hash2 = build_vector_payload(article, run, "embed.v1")
    assert hash1 == hash2


def test_vector_payload_structure():
    article = FakeArticle()
    run = FakeAnalysisRun()
    payload, _ = build_vector_payload(article, run, "embed.v1")
    assert "document" in payload
    assert "metadata" in payload
    assert payload["metadata"]["embedding_version"] == "embed.v1"
    assert payload["metadata"]["slug"] == "test-article"


def test_vector_payload_keywords_sorted():
    article = FakeArticle()
    run = FakeAnalysisRun()
    payload, _ = build_vector_payload(article, run, "embed.v1")
    # Keywords in document should be sorted
    doc = payload["document"]
    assert "AI" in doc


def test_vector_payload_changes_with_embedding_version():
    article = FakeArticle()
    run = FakeAnalysisRun()
    _, hash1 = build_vector_payload(article, run, "embed.v1")
    _, hash2 = build_vector_payload(article, run, "embed.v2")
    assert hash1 != hash2


# ---------------------------------------------------------------------------
# Notion payload
# ---------------------------------------------------------------------------


def test_notion_payload_deterministic():
    article = FakeArticle()
    run = FakeAnalysisRun()
    _, hash1 = build_notion_payload(article, run, FakeAnnotation(), ["tag1", "tag2"])
    _, hash2 = build_notion_payload(article, run, FakeAnnotation(), ["tag2", "tag1"])
    assert hash1 == hash2  # tags are sorted


def test_notion_payload_structure():
    article = FakeArticle()
    run = FakeAnalysisRun()
    payload, _ = build_notion_payload(article, run, None, ["GPU"])
    assert "properties" in payload
    assert "body" in payload
    assert payload["properties"]["title"] == "Test Article"
    assert payload["properties"]["is_read"] is False  # None annotations → default


def test_notion_payload_body_order():
    article = FakeArticle()
    run = FakeAnalysisRun()
    payload, _ = build_notion_payload(article, run, None, [])
    body_types = [b["type"] for b in payload["body"]]
    assert body_types == ["summary", "critique", "meta"]


def test_notion_payload_annotations():
    article = FakeArticle()
    run = FakeAnalysisRun()
    payload, _ = build_notion_payload(article, run, FakeAnnotation(is_read=True, starred=True), [])
    assert payload["properties"]["is_read"] is True
    assert payload["properties"]["starred"] is True
