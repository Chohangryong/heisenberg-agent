"""Unit tests for NotionAdapter — uses fake client."""

import pytest

from heisenberg_agent.adapters.notion_adapter import (
    NotionAdapter,
    NotionSyncError,
    RetryAfterError,
    classify_notion_error,
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
    assert exc_info.value.error_type == "rate_limit"
    assert exc_info.value.retryable is True


def test_other_error_raises_notion_sync_error():
    client = FakeNotionClient(error=RuntimeError("500 Internal Server Error"))
    adapter = NotionAdapter(client, parent_page_id="parent-1")

    with pytest.raises(NotionSyncError):
        adapter.create_page(properties={}, children=[])


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
    adapter = NotionAdapter(client, parent_page_id="parent-1")

    with pytest.raises(NotionSyncError) as exc_info:
        adapter.create_page(properties={}, children=[])

    assert exc_info.value.error_type == "server_error"
    assert exc_info.value.retryable is True


def test_retry_after_error_carries_attributes():
    err = RetryAfterError("rate limited", retry_after=120)
    assert err.error_type == "rate_limit"
    assert err.retryable is True
    assert err.retry_after == 120
