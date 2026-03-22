"""ChromaDB adapter — thin wrapper for vector storage.

Test boundary: inject a fake collection to eliminate live ChromaDB dependency.

Retry strategy (two layers):

Layer 1 — Adapter (here): absorbs transient I/O glitches.
  - 3 attempts, 1-10s exponential backoff, transient errors only.
  - Total wall time: ≤15s. Keeps the sync loop moving.

Layer 2 — Job (sync_jobs.mark_failed → next_retry_at):
  - 5 attempts, 5min-1hr exponential backoff.
  - Covers sustained outages. Adapter failure → job retry later.

Adapter retry is intentionally short and narrow so that the job-level
retry remains the primary recovery mechanism for persistent failures.
"""

from __future__ import annotations

from typing import Any, Protocol

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

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


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _is_transient(error: BaseException) -> bool:
    """Return True for errors worth retrying at the adapter level.

    OSError covers ConnectionError and TimeoutError (subclasses),
    so a single isinstance check suffices for the transient family.
    RuntimeError is transient only when the message hints at I/O issues.
    """
    if isinstance(error, OSError):  # includes ConnectionError, TimeoutError
        return True
    if isinstance(error, RuntimeError):
        msg = str(error).lower()
        return "timeout" in msg or "connection" in msg
    return False


def classify_chroma_error(error: Exception) -> tuple[str, bool]:
    """Classify a ChromaDB error into (error_type, retryable).

    Check order: specific subclasses before parent OSError,
    then non-transient types, then fallback.
    """
    # --- transient (specific subclasses first) ---
    if isinstance(error, ConnectionError):       # before OSError
        return "connection_error", True
    if isinstance(error, TimeoutError):           # before OSError
        return "timeout", True
    if isinstance(error, OSError):                # remaining OSError family
        return "io_error", True
    if isinstance(error, RuntimeError):
        msg = str(error).lower()
        if "timeout" in msg:
            return "timeout", True
        if "connection" in msg:
            return "connection_error", True

    # --- non-transient (retrying won't help) ---
    if isinstance(error, (ValueError, TypeError)):
        return "schema_mismatch", False

    return "unknown", False


class ChromaSyncError(Exception):
    """Raised when ChromaDB operation fails."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "unknown",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

_RETRY_DECORATOR = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


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

        Transient errors (I/O, connection, timeout) are retried up to 3 times
        at the adapter level. Non-transient errors (schema mismatch, bad input)
        fail immediately. All failures surface as ChromaSyncError.
        """
        try:
            self._upsert_with_retry(doc_id, document, metadata)
            logger.info("chroma.upserted", doc_id=doc_id)
            return doc_id
        except Exception as e:
            error_type, retryable = classify_chroma_error(e)
            raise ChromaSyncError(
                f"Chroma upsert failed: {e}",
                error_type=error_type,
                retryable=retryable,
            ) from e

    def delete(self, doc_id: str) -> None:
        """Delete a document by ID.

        Same retry semantics as upsert.
        """
        try:
            self._delete_with_retry(doc_id)
            logger.info("chroma.deleted", doc_id=doc_id)
        except Exception as e:
            error_type, retryable = classify_chroma_error(e)
            raise ChromaSyncError(
                f"Chroma delete failed: {e}",
                error_type=error_type,
                retryable=retryable,
            ) from e

    @_RETRY_DECORATOR
    def _upsert_with_retry(
        self, doc_id: str, document: str, metadata: dict[str, Any],
    ) -> None:
        self._collection.upsert(
            ids=[doc_id],
            documents=[document],
            metadatas=[metadata],
        )

    @_RETRY_DECORATOR
    def _delete_with_retry(self, doc_id: str) -> None:
        self._collection.delete(ids=[doc_id])
