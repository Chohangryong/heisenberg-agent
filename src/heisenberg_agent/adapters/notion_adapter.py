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


class NotionSyncError(Exception):
    """Raised when Notion API call fails."""
    pass


class RetryAfterError(NotionSyncError):
    """Raised on 429 rate limit. Includes retry_after seconds."""

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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
            self._handle_error(e)

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
            self._handle_error(e)

    def _handle_error(self, error: Exception) -> None:
        """Classify Notion errors into retry-able vs terminal."""
        error_str = str(error)

        # Check for rate limit (429)
        if "429" in error_str or "rate" in error_str.lower():
            retry_after = self._extract_retry_after(error)
            raise RetryAfterError(
                f"Notion rate limited: {error}", retry_after=retry_after,
            ) from error

        # All other errors
        raise NotionSyncError(f"Notion API error: {error}") from error

    def _extract_retry_after(self, error: Exception) -> int:
        """Try to extract Retry-After from error. Default 60s."""
        # notion-sdk-py includes retry_after in some error types
        retry = getattr(error, "retry_after", None)
        if retry and isinstance(retry, (int, float)):
            return int(retry)
        return 60

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
