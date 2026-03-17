"""Unit tests for ChromaAdapter — uses fake collection."""

import pytest

from heisenberg_agent.adapters.chroma_adapter import ChromaAdapter, ChromaSyncError


class FakeCollection:
    """Records calls instead of hitting real ChromaDB."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.upsert_calls: list[dict] = []
        self.delete_calls: list[list[str]] = []
        self._should_fail = should_fail

    def upsert(self, ids, documents, metadatas):
        if self._should_fail:
            raise RuntimeError("Fake ChromaDB error")
        self.upsert_calls.append({
            "ids": ids, "documents": documents, "metadatas": metadatas,
        })

    def delete(self, ids):
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
