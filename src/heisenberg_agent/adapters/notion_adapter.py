"""Notion API adapter — thin wrapper with version pin.

Test boundary: inject a fake client to eliminate live Notion API dependency.
"""

from __future__ import annotations

from typing import Any, Protocol

from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


class NotionClient(Protocol):
    """Minimal interface for Notion API client."""

    def pages_create(self, **kwargs: Any) -> dict[str, Any]: ...
    def pages_update(self, page_id: str, **kwargs: Any) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def classify_notion_error(error: Exception) -> tuple[str, bool, int | None]:
    """Classify a Notion error into (error_type, retryable, retry_after).

    Uses status attribute from notion-sdk-py APIResponseError when available,
    falls back to string matching.

    Returns:
        (error_type, retryable, retry_after_seconds_or_None)
    """
    status = getattr(error, "status", None)
    retry_after = _extract_retry_after(error)

    if status is not None:
        if status == 429:
            return "rate_limit", True, retry_after or 60
        if status == 409:
            return "conflict", True, None
        if status in (500, 502, 503, 504):
            return "server_error", True, None
        if status in (400, 401, 403, 404):
            return "client_error", False, None
        return "unknown", False, None

    # Fallback: string matching
    error_str = str(error)
    error_lower = error_str.lower()
    if "429" in error_str or "rate" in error_lower:
        return "rate_limit", True, retry_after or 60
    if "timeout" in error_lower:
        return "server_error", True, None
    if "500" in error_str or "502" in error_str or "503" in error_str:
        return "server_error", True, None

    return "unknown", False, None


def _extract_retry_after(error: Exception) -> int | None:
    """Try to extract Retry-After from error. None if unavailable."""
    retry = getattr(error, "retry_after", None)
    if retry and isinstance(retry, (int, float)):
        return int(retry)
    return None


class NotionSyncError(Exception):
    """Raised when Notion API call fails."""

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


class RetryAfterError(NotionSyncError):
    """Raised on 429 rate limit. Includes retry_after seconds."""

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(
            message, error_type="rate_limit", retryable=True,
        )
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class NotionAdapter:
    """Wraps Notion API operations with version pin."""

    def __init__(
        self,
        client: NotionClient,
        parent_page_id: str,
        api_version: str = "2022-06-28",
    ) -> None:
        self._client = client
        self._parent_page_id = parent_page_id
        self._api_version = api_version

    @classmethod
    def from_settings(cls, settings: Any) -> "NotionAdapter":
        """Create adapter from settings, initializing real Notion client."""
        from notion_client import Client

        client = Client(
            auth=settings.notion_api_key,
            notion_version=settings.notion.api_version,
        )
        return cls(
            client=client,
            parent_page_id=settings.notion_parent_page_id,
            api_version=settings.notion.api_version,
        )

    def create_page(
        self,
        properties: dict[str, Any],
        children: list[dict[str, Any]],
    ) -> str:
        """Create a Notion page. Returns the page ID.

        Raises:
            RetryAfterError: On 429 rate limit.
            NotionSyncError: On other API failures.
        """
        try:
            response = self._client.pages_create(
                parent={"page_id": self._parent_page_id},
                properties=self._build_notion_properties(properties),
                children=self._build_notion_blocks(children),
            )
            page_id = response.get("id", "")
            logger.info("notion.page_created", page_id=page_id)
            return page_id
        except Exception as e:
            self._raise_classified(e)

    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
        children: list[dict[str, Any]],
    ) -> str:
        """Update an existing Notion page. Returns the page ID.

        Raises:
            RetryAfterError: On 429 rate limit.
            NotionSyncError: On other API failures.
        """
        try:
            self._client.pages_update(
                page_id=page_id,
                properties=self._build_notion_properties(properties),
            )
            logger.info("notion.page_updated", page_id=page_id)
            return page_id
        except Exception as e:
            self._raise_classified(e)

    def _raise_classified(self, error: Exception) -> None:
        """Classify error and raise the appropriate NotionSyncError subtype."""
        error_type, retryable, retry_after = classify_notion_error(error)

        if error_type == "rate_limit":
            raise RetryAfterError(
                f"Notion rate limited: {error}",
                retry_after=retry_after or 60,
            ) from error

        raise NotionSyncError(
            f"Notion API error: {error}",
            error_type=error_type,
            retryable=retryable,
        ) from error

    def _build_notion_properties(self, props: dict[str, Any]) -> dict[str, Any]:
        """Convert flat properties dict to Notion API property format.

        Placeholder — real implementation maps to Notion property types.
        """
        notion_props: dict[str, Any] = {}

        if "title" in props:
            notion_props["제목"] = {
                "title": [{"text": {"content": props["title"] or ""}}]
            }

        # Additional properties would be mapped here per Notion schema
        return notion_props

    def _build_notion_blocks(self, body: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert body blocks to Notion block format.

        Placeholder — real implementation creates heading + paragraph blocks.
        """
        blocks: list[dict[str, Any]] = []
        for section in body:
            content = section.get("content", "")
            blocks.append({
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"text": {"content": section.get("type", "")}}]
                },
            })
            # Split content into paragraphs (Notion limit: 2000 chars per block)
            for chunk in _chunk_text(content, 2000):
                blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": chunk}}]
                    },
                })
        return blocks


def _chunk_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks of max_len."""
    if not text:
        return [""]
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]
