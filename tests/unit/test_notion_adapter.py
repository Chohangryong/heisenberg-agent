"""Unit tests for NotionAdapter — uses fake client.

Tests cover:
- Page create/update with data_source_id parent
- Full property mapping from notion_schema.yaml (SSOT)
- replace_body pagination + chunking
- Error classification
"""

import pytest

from heisenberg_agent.adapters.notion_adapter import (
    NotionAdapter,
    NotionSyncError,
    RetryAfterError,
    classify_notion_error,
    load_notion_schema,
)
from heisenberg_agent.services.sync_payload import build_notion_payload


# ---------------------------------------------------------------------------
# Fake clients
# ---------------------------------------------------------------------------


class FakeNotionClient:
    """Records calls instead of hitting real Notion API."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self._error = error

    def pages_create(self, **kwargs):
        if self._error:
            raise self._error
        self.create_calls.append(kwargs)
        return {"id": "page-id-123"}

    def pages_update(self, page_id, **kwargs):
        if self._error:
            raise self._error
        self.update_calls.append({"page_id": page_id, **kwargs})
        return {"id": page_id}


class FakeBlocksChildrenAPI:
    """Records blocks.children.list and blocks.children.append calls."""

    def __init__(
        self,
        existing_blocks: list[dict] | None = None,
        *,
        fail_on_append: bool = False,
    ) -> None:
        self._existing = existing_blocks or []
        self._fail_on_append = fail_on_append
        self.list_calls: list[dict] = []
        self.append_calls: list[dict] = []

    def list(self, block_id: str, **kwargs) -> dict:
        page_size = kwargs.get("page_size", 100)
        start_cursor = kwargs.get("start_cursor")

        # Simple pagination: start_cursor is an index
        start_idx = int(start_cursor) if start_cursor else 0
        end_idx = start_idx + page_size
        page = self._existing[start_idx:end_idx]
        has_more = end_idx < len(self._existing)

        self.list_calls.append({
            "block_id": block_id,
            "page_size": page_size,
            "start_cursor": start_cursor,
        })

        return {
            "results": page,
            "has_more": has_more,
            "next_cursor": str(end_idx) if has_more else None,
        }

    def append(self, block_id: str, **kwargs) -> dict:
        if self._fail_on_append:
            raise RuntimeError("500 Internal Server Error")
        self.append_calls.append({"block_id": block_id, **kwargs})
        return {"results": []}


class FakeBlocksAPI:
    """Fake blocks API with children sub-API and delete tracking."""

    def __init__(
        self,
        existing_blocks: list[dict] | None = None,
        *,
        fail_on_append: bool = False,
    ) -> None:
        self.children = FakeBlocksChildrenAPI(
            existing_blocks, fail_on_append=fail_on_append,
        )
        self.delete_calls: list[str] = []

    def delete(self, block_id: str) -> dict:
        self.delete_calls.append(block_id)
        return {}


def _make_adapter(
    client: FakeNotionClient | None = None,
    blocks_api: FakeBlocksAPI | None = None,
) -> NotionAdapter:
    """Create adapter with defaults for testing."""
    return NotionAdapter(
        client=client or FakeNotionClient(),
        data_source_id="ds-test-123",
        blocks_api=blocks_api,
    )


# ---------------------------------------------------------------------------
# Page create / update
# ---------------------------------------------------------------------------


def test_create_page_returns_id():
    client = FakeNotionClient()
    adapter = _make_adapter(client)

    page_id = adapter.create_page(
        properties={"title": "Test"},
        children=[{"type": "summary", "content": "text"}],
    )

    assert page_id == "page-id-123"
    assert len(client.create_calls) == 1


def test_create_page_uses_data_source_id_parent():
    client = FakeNotionClient()
    adapter = _make_adapter(client)

    adapter.create_page(properties={"title": "Test"}, children=[])

    call = client.create_calls[0]
    assert call["parent"] == {
        "type": "data_source_id",
        "data_source_id": "ds-test-123",
    }


def test_update_page_returns_id():
    client = FakeNotionClient()
    adapter = _make_adapter(client)

    page_id = adapter.update_page(
        page_id="existing-page",
        properties={"title": "Updated"},
    )

    assert page_id == "existing-page"
    assert len(client.update_calls) == 1


def test_429_raises_retry_after_error():
    client = FakeNotionClient(error=RuntimeError("429 rate limited"))
    adapter = _make_adapter(client)

    with pytest.raises(RetryAfterError) as exc_info:
        adapter.create_page(properties={}, children=[])

    assert exc_info.value.retry_after == 60
    assert exc_info.value.error_type == "rate_limit"
    assert exc_info.value.retryable is True


def test_other_error_raises_notion_sync_error():
    client = FakeNotionClient(error=RuntimeError("500 Internal Server Error"))
    adapter = _make_adapter(client)

    with pytest.raises(NotionSyncError):
        adapter.create_page(properties={}, children=[])


# ---------------------------------------------------------------------------
# Property mapping — driven by notion_schema.yaml
# ---------------------------------------------------------------------------


def test_schema_keys_match_payload_keys():
    """Drift detection: notion_schema.yaml keys must match build_notion_payload keys."""
    from dataclasses import dataclass
    from datetime import datetime, timezone
    import json

    @dataclass
    class _Art:
        title: str = "t"
        url: str = "u"
        category: str = "c"
        author: str = "a"
        published_at: datetime = None
        collected_at: datetime = None
        content_hash: str = "h"

        def __post_init__(self):
            if self.published_at is None:
                self.published_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            if self.collected_at is None:
                self.collected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    @dataclass
    class _Run:
        analysis_version: str = "v1"
        llm_model: str = "m"
        summary_json: str = json.dumps({
            "core_thesis": "", "supporting_points": [],
            "conclusion": "", "keywords": [], "importance": "low",
        })
        critique_json: str = json.dumps({
            "logic_gaps": [], "missing_views": [],
            "claims_to_verify": [], "interest_analysis": "",
            "overall_assessment": "",
        })

    @dataclass
    class _Ann:
        is_read: bool = False
        starred: bool = False

    payload, _ = build_notion_payload(_Art(), _Run(), _Ann(), [])
    payload_keys = set(payload["properties"].keys())

    schema = load_notion_schema()
    schema_keys = set(schema.keys())

    assert schema_keys == payload_keys, (
        f"Schema drift detected.\n"
        f"  In schema but not payload: {schema_keys - payload_keys}\n"
        f"  In payload but not schema: {payload_keys - schema_keys}"
    )


def test_build_notion_properties_all_fields():
    """All 12 property types are correctly converted to Notion API format."""
    adapter = _make_adapter()

    props = {
        "title": "테스트 기사",
        "url": "https://heisenberg.kr/test/",
        "published_at": "2026-03-15T09:00:00+00:00",
        "importance": "high",
        "category": "AI",
        "keywords": ["GPU", "AI"],
        "author": "김연구",
        "tags": ["tech", "review"],
        "is_read": True,
        "starred": False,
        "llm_model": "claude-sonnet-4-5",
        "analysis_version": "analysis.v1",
    }

    result = adapter._build_notion_properties(props)

    # title
    assert result["제목"]["title"][0]["text"]["content"] == "테스트 기사"
    # url
    assert result["URL"]["url"] == "https://heisenberg.kr/test/"
    # date
    assert result["발행일"]["date"]["start"] == "2026-03-15T09:00:00+00:00"
    # select
    assert result["중요도"]["select"]["name"] == "high"
    assert result["카테고리"]["select"]["name"] == "AI"
    # multi_select
    assert result["키워드"]["multi_select"] == [{"name": "GPU"}, {"name": "AI"}]
    assert result["태그"]["multi_select"] == [{"name": "tech"}, {"name": "review"}]
    # rich_text
    assert result["작성자"]["rich_text"][0]["text"]["content"] == "김연구"
    assert result["분석모델"]["rich_text"][0]["text"]["content"] == "claude-sonnet-4-5"
    assert result["분석버전"]["rich_text"][0]["text"]["content"] == "analysis.v1"
    # checkbox
    assert result["읽음"]["checkbox"] is True
    assert result["즐겨찾기"]["checkbox"] is False


def test_nullable_properties_omitted_when_none():
    """Nullable properties with None value are omitted from output."""
    adapter = _make_adapter()

    props = {
        "title": "Test",
        "url": "https://example.com",
        "published_at": None,   # nullable=true
        "importance": "high",
        "category": None,       # nullable=true
        "keywords": [],
        "author": None,         # nullable=true
        "tags": [],
        "is_read": False,
        "starred": False,
        "llm_model": None,      # nullable=true
        "analysis_version": None,  # nullable=true
    }

    result = adapter._build_notion_properties(props)

    assert "발행일" not in result
    assert "카테고리" not in result
    assert "작성자" not in result
    assert "분석모델" not in result
    assert "분석버전" not in result
    # Non-nullable with values should still be present
    assert "제목" in result
    assert "URL" in result
    assert "중요도" in result


def test_non_nullable_empty_fallback():
    """Non-nullable fields get fallback values when value is falsy."""
    adapter = _make_adapter()

    props = {
        "title": "",
        "url": "",
        "importance": "",
        "keywords": [],
        "tags": [],
        "is_read": False,
        "starred": False,
    }

    result = adapter._build_notion_properties(props)

    assert result["제목"]["title"][0]["text"]["content"] == ""
    assert result["키워드"]["multi_select"] == []
    assert result["읽음"]["checkbox"] is False


# ---------------------------------------------------------------------------
# replace_body — pagination + chunking
# ---------------------------------------------------------------------------


def test_replace_body_deletes_existing_and_appends_new():
    """Basic replace_body: list → delete all → append new."""
    existing = [{"id": f"block-{i}"} for i in range(3)]
    blocks_api = FakeBlocksAPI(existing)
    adapter = _make_adapter(blocks_api=blocks_api)

    adapter.replace_body(
        page_id="page-1",
        children=[{"type": "summary", "content": "New content"}],
    )

    assert len(blocks_api.delete_calls) == 3
    assert set(blocks_api.delete_calls) == {"block-0", "block-1", "block-2"}
    assert len(blocks_api.children.append_calls) == 1


def test_replace_body_pagination_lists_all_blocks():
    """blocks.children.list with pagination fetches all existing blocks."""
    # Create 250 blocks — needs 3 pages at page_size=100
    existing = [{"id": f"block-{i}"} for i in range(250)]
    blocks_api = FakeBlocksAPI(existing)
    adapter = _make_adapter(blocks_api=blocks_api)

    adapter.replace_body(page_id="page-1", children=[])

    # Should have made 3 list calls (100 + 100 + 50)
    assert len(blocks_api.children.list_calls) == 3
    # All 250 blocks deleted
    assert len(blocks_api.delete_calls) == 250


def test_replace_body_append_chunking():
    """New blocks are appended in chunks of 100."""
    blocks_api = FakeBlocksAPI(existing_blocks=[])
    adapter = _make_adapter(blocks_api=blocks_api)

    # Create body that produces >100 blocks: 55 sections × 2 blocks each = 110 blocks
    children = [
        {"type": f"section-{i}", "content": f"Content {i}"}
        for i in range(55)
    ]

    adapter.replace_body(page_id="page-1", children=children)

    # 55 sections × (1 heading + 1 paragraph) = 110 blocks → 2 append calls
    assert len(blocks_api.children.append_calls) == 2
    # First chunk: 100 blocks
    first_chunk = blocks_api.children.append_calls[0]["children"]
    assert len(first_chunk) == 100
    # Second chunk: 10 blocks
    second_chunk = blocks_api.children.append_calls[1]["children"]
    assert len(second_chunk) == 10


def test_replace_body_no_blocks_api_raises():
    """replace_body without blocks_api raises NotionSyncError."""
    adapter = NotionAdapter(
        client=FakeNotionClient(),
        data_source_id="ds-test",
        blocks_api=None,
    )

    with pytest.raises(NotionSyncError, match="blocks API not available"):
        adapter.replace_body(page_id="page-1", children=[])


def test_replace_body_empty_page():
    """replace_body on a page with no existing blocks just appends."""
    blocks_api = FakeBlocksAPI(existing_blocks=[])
    adapter = _make_adapter(blocks_api=blocks_api)

    adapter.replace_body(
        page_id="page-1",
        children=[{"type": "summary", "content": "text"}],
    )

    assert len(blocks_api.delete_calls) == 0
    assert len(blocks_api.children.append_calls) == 1


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class FakeAPIError(Exception):
    """Simulates notion-sdk-py APIResponseError with status attribute."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def test_classify_429_by_status():
    error_type, retryable, retry_after = classify_notion_error(
        FakeAPIError("rate limited", status=429),
    )
    assert error_type == "rate_limit"
    assert retryable is True
    assert retry_after == 60


def test_classify_409_conflict():
    error_type, retryable, _ = classify_notion_error(
        FakeAPIError("conflict", status=409),
    )
    assert error_type == "conflict"
    assert retryable is True


def test_classify_500_server_error():
    error_type, retryable, _ = classify_notion_error(
        FakeAPIError("internal error", status=500),
    )
    assert error_type == "server_error"
    assert retryable is True


def test_classify_502_server_error():
    error_type, retryable, _ = classify_notion_error(
        FakeAPIError("bad gateway", status=502),
    )
    assert error_type == "server_error"
    assert retryable is True


def test_classify_400_client_error():
    error_type, retryable, _ = classify_notion_error(
        FakeAPIError("bad request", status=400),
    )
    assert error_type == "client_error"
    assert retryable is False


def test_classify_401_client_error():
    error_type, retryable, _ = classify_notion_error(
        FakeAPIError("unauthorized", status=401),
    )
    assert error_type == "client_error"
    assert retryable is False


def test_classify_fallback_429_string():
    error_type, retryable, retry_after = classify_notion_error(
        RuntimeError("429 Too Many Requests"),
    )
    assert error_type == "rate_limit"
    assert retryable is True
    assert retry_after == 60


def test_classify_fallback_timeout_string():
    error_type, retryable, _ = classify_notion_error(
        RuntimeError("request timeout after 30s"),
    )
    assert error_type == "server_error"
    assert retryable is True


def test_classify_unknown():
    error_type, retryable, _ = classify_notion_error(
        Exception("something unexpected"),
    )
    assert error_type == "unknown"
    assert retryable is False


# ---------------------------------------------------------------------------
# NotionSyncError attributes
# ---------------------------------------------------------------------------


def test_notion_sync_error_carries_attributes():
    client = FakeNotionClient(error=FakeAPIError("bad gateway", status=502))
    adapter = _make_adapter(client)

    with pytest.raises(NotionSyncError) as exc_info:
        adapter.create_page(properties={}, children=[])

    assert exc_info.value.error_type == "server_error"
    assert exc_info.value.retryable is True


def test_retry_after_error_carries_attributes():
    err = RetryAfterError("rate limited", retry_after=120)
    assert err.error_type == "rate_limit"
    assert err.retryable is True
    assert err.retry_after == 120
