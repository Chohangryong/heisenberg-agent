"""Unit tests for NotionAdapter — uses fake client."""

import pytest

from heisenberg_agent.adapters.notion_adapter import (
    NotionAdapter,
    NotionSyncError,
    RetryAfterError,
)


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


def test_create_page_returns_id():
    client = FakeNotionClient()
    adapter = NotionAdapter(client, parent_page_id="parent-1")

    page_id = adapter.create_page(
        properties={"title": "Test"},
        children=[{"type": "summary", "content": "text"}],
    )

    assert page_id == "page-id-123"
    assert len(client.create_calls) == 1


def test_create_page_passes_parent():
    client = FakeNotionClient()
    adapter = NotionAdapter(client, parent_page_id="parent-abc")

    adapter.create_page(properties={"title": "Test"}, children=[])

    call = client.create_calls[0]
    assert call["parent"]["page_id"] == "parent-abc"


def test_update_page_returns_id():
    client = FakeNotionClient()
    adapter = NotionAdapter(client, parent_page_id="parent-1")

    page_id = adapter.update_page(
        page_id="existing-page",
        properties={"title": "Updated"},
        children=[],
    )

    assert page_id == "existing-page"
    assert len(client.update_calls) == 1


def test_429_raises_retry_after_error():
    client = FakeNotionClient(error=RuntimeError("429 rate limited"))
    adapter = NotionAdapter(client, parent_page_id="parent-1")

    with pytest.raises(RetryAfterError) as exc_info:
        adapter.create_page(properties={}, children=[])

    assert exc_info.value.retry_after == 60


def test_other_error_raises_notion_sync_error():
    client = FakeNotionClient(error=RuntimeError("500 Internal Server Error"))
    adapter = NotionAdapter(client, parent_page_id="parent-1")

    with pytest.raises(NotionSyncError):
        adapter.create_page(properties={}, children=[])
