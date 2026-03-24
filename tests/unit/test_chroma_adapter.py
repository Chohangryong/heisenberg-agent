"""Unit tests for ChromaAdapter — uses fake collection."""

import pytest

from heisenberg_agent.adapters.chroma_adapter import (
    ChromaAdapter,
    ChromaSyncError,
    classify_chroma_error,
)


class FakeCollection:
    """Records calls instead of hitting real ChromaDB."""

    def __init__(self, *, should_fail: bool = False, error: Exception | None = None) -> None:
        self.upsert_calls: list[dict] = []
        self.delete_calls: list[list[str]] = []
        self._should_fail = should_fail
        self._error = error

    def upsert(self, ids, documents, metadatas):
        if self._error:
            raise self._error
        if self._should_fail:
            raise RuntimeError("Fake ChromaDB error")
        self.upsert_calls.append({
            "ids": ids, "documents": documents, "metadatas": metadatas,
        })

    def delete(self, ids):
        if self._error:
            raise self._error
        if self._should_fail:
            raise RuntimeError("Fake ChromaDB error")
        self.delete_calls.append(ids)


def test_upsert_passes_correct_args():
    coll = FakeCollection()
    adapter = ChromaAdapter(coll)

    result = adapter.upsert("doc:1", "hello world", {"key": "val"})

    assert result == "doc:1"
    assert len(coll.upsert_calls) == 1
    call = coll.upsert_calls[0]
    assert call["ids"] == ["doc:1"]
    assert call["documents"] == ["hello world"]
    assert call["metadatas"] == [{"key": "val"}]


def test_upsert_raises_chroma_sync_error():
    coll = FakeCollection(should_fail=True)
    adapter = ChromaAdapter(coll)

    with pytest.raises(ChromaSyncError):
        adapter.upsert("doc:1", "text", {})


def test_delete_passes_correct_args():
    coll = FakeCollection()
    adapter = ChromaAdapter(coll)

    adapter.delete("doc:1")

    assert len(coll.delete_calls) == 1
    assert coll.delete_calls[0] == ["doc:1"]


def test_delete_raises_chroma_sync_error():
    coll = FakeCollection(should_fail=True)
    adapter = ChromaAdapter(coll)

    with pytest.raises(ChromaSyncError):
        adapter.delete("doc:1")


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def test_classify_connection_error():
    error_type, retryable = classify_chroma_error(ConnectionError("refused"))
    assert error_type == "connection_error"
    assert retryable is True


def test_classify_timeout_error():
    error_type, retryable = classify_chroma_error(TimeoutError("timed out"))
    assert error_type == "timeout"
    assert retryable is True


def test_classify_os_error():
    error_type, retryable = classify_chroma_error(OSError("disk full"))
    assert error_type == "io_error"
    assert retryable is True


def test_classify_value_error_schema_mismatch():
    error_type, retryable = classify_chroma_error(ValueError("dimension mismatch"))
    assert error_type == "schema_mismatch"
    assert retryable is False


def test_classify_type_error_schema_mismatch():
    error_type, retryable = classify_chroma_error(TypeError("bad arg"))
    assert error_type == "schema_mismatch"
    assert retryable is False


def test_classify_runtime_timeout():
    error_type, retryable = classify_chroma_error(RuntimeError("request timeout"))
    assert error_type == "timeout"
    assert retryable is True


def test_classify_runtime_connection():
    error_type, retryable = classify_chroma_error(RuntimeError("connection reset"))
    assert error_type == "connection_error"
    assert retryable is True


def test_classify_unknown():
    error_type, retryable = classify_chroma_error(Exception("something else"))
    assert error_type == "unknown"
    assert retryable is False


# ---------------------------------------------------------------------------
# Transient retry (adapter layer 1)
# ---------------------------------------------------------------------------


def test_transient_retry_succeeds_after_failures():
    """Transient error twice, then success → adapter retries and succeeds."""
    call_count = 0

    class FlakeyCollection:
        def upsert(self, ids, documents, metadatas):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("temporary disk busy")

        def delete(self, ids):
            pass

    adapter = ChromaAdapter(FlakeyCollection())
    result = adapter.upsert("doc:1", "text", {})
    assert result == "doc:1"
    assert call_count == 3


def test_non_transient_fails_immediately():
    """Non-transient error (ValueError) → no retry, immediate ChromaSyncError."""
    call_count = 0

    class BadSchemaCollection:
        def upsert(self, ids, documents, metadatas):
            nonlocal call_count
            call_count += 1
            raise ValueError("dimension mismatch: expected 1536, got 768")

        def delete(self, ids):
            pass

    adapter = ChromaAdapter(BadSchemaCollection())
    with pytest.raises(ChromaSyncError) as exc_info:
        adapter.upsert("doc:1", "text", {})

    assert call_count == 1  # no retry
    assert exc_info.value.error_type == "schema_mismatch"
    assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# ChromaSyncError attributes
# ---------------------------------------------------------------------------


def test_chroma_sync_error_carries_attributes():
    coll = FakeCollection(error=ConnectionError("refused"))
    adapter = ChromaAdapter(coll)

    with pytest.raises(ChromaSyncError) as exc_info:
        adapter.upsert("doc:1", "text", {})

    assert exc_info.value.error_type == "connection_error"
    assert exc_info.value.retryable is True
