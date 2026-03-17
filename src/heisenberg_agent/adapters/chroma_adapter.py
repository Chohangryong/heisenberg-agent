"""ChromaDB adapter — thin wrapper for vector storage.

Test boundary: inject a fake collection to eliminate live ChromaDB dependency.
"""

from __future__ import annotations

from typing import Any, Protocol

from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


class ChromaCollection(Protocol):
    """Minimal interface for a ChromaDB collection."""

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    def delete(self, ids: list[str]) -> None: ...


class ChromaSyncError(Exception):
    """Raised when ChromaDB operation fails."""
    pass


class ChromaAdapter:
    """Wraps ChromaDB collection operations."""

    def __init__(self, collection: ChromaCollection) -> None:
        self._collection = collection

    @classmethod
    def from_settings(cls, settings: Any) -> "ChromaAdapter":
        """Create adapter from settings, initializing real ChromaDB client."""
        import chromadb

        client = chromadb.PersistentClient(path=settings.vectordb.persist_dir)
        collection = client.get_or_create_collection(
            name=settings.vectordb.collection_name,
        )
        return cls(collection)

    def upsert(
        self,
        doc_id: str,
        document: str,
        metadata: dict[str, Any],
    ) -> str:
        """Upsert a single document. Returns the doc_id.

        Raises:
            ChromaSyncError: On any ChromaDB failure.
        """
        try:
            self._collection.upsert(
                ids=[doc_id],
                documents=[document],
                metadatas=[metadata],
            )
            logger.info("chroma.upserted", doc_id=doc_id)
            return doc_id
        except Exception as e:
            raise ChromaSyncError(f"Chroma upsert failed: {e}") from e

    def delete(self, doc_id: str) -> None:
        """Delete a document by ID.

        Raises:
            ChromaSyncError: On any ChromaDB failure.
        """
        try:
            self._collection.delete(ids=[doc_id])
            logger.info("chroma.deleted", doc_id=doc_id)
        except Exception as e:
            raise ChromaSyncError(f"Chroma delete failed: {e}") from e
