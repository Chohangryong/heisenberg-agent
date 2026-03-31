"""Notion API adapter — thin wrapper with version pin.

Uses config/notion_schema.yaml as the single source of truth for
property name ↔ type mapping. No property names are hard-coded.

Test boundary: inject a fake client to eliminate live Notion API dependency.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Protocol

import yaml
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from heisenberg_agent.utils.logger import get_logger

logger = get_logger()

# Notion API limits
_BLOCKS_PER_APPEND = 100  # Max children per blocks.children.append call
_BLOCKS_LIST_PAGE_SIZE = 100  # blocks.children.list pagination size
_TEXT_CHUNK_SIZE = 2000  # Max chars per rich_text block


class NotionClient(Protocol):
    """Minimal interface for Notion API client."""

    def pages_create(self, **kwargs: Any) -> dict[str, Any]: ...
    def pages_update(self, page_id: str, **kwargs: Any) -> dict[str, Any]: ...


class BlocksChildrenAPI(Protocol):
    """Interface for blocks.children operations (body replace)."""

    def list(self, block_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def append(self, block_id: str, **kwargs: Any) -> dict[str, Any]: ...


class BlocksAPI(Protocol):
    """Interface for blocks operations (delete)."""

    children: BlocksChildrenAPI
    def delete(self, block_id: str) -> dict[str, Any]: ...


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
# Adapter-level retry (update_page, replace_body only — NOT create_page)
# ---------------------------------------------------------------------------

_TRANSIENT_ERROR_TYPES = frozenset({
    "server_error",      # 500, 502, 503, 504
    "conflict",          # 409
    "io_error",          # OSError family
    "timeout",           # TimeoutError
    "connection_error",  # ConnectionError
})


def _is_notion_transient(error: BaseException) -> bool:
    """Return True for errors worth retrying at the adapter level.

    Uses an explicit allowlist of error_type values.
    Rate limit (429) is NOT retried here — it propagates to the
    circuit breaker in SyncAgent._process_target.
    """
    return (
        isinstance(error, NotionSyncError)
        and error.error_type in _TRANSIENT_ERROR_TYPES
    )


_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)

_RETRY_DECORATOR = retry(
    retry=retry_if_exception(_is_notion_transient),
    stop=stop_after_attempt(3),
    wait=_RETRY_WAIT,
    reraise=True,
)


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


def _default_schema_path() -> Path:
    """Resolve config/notion_schema.yaml relative to project root."""
    return Path(__file__).resolve().parent.parent.parent.parent / "config" / "notion_schema.yaml"


def load_notion_schema(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load notion_schema.yaml and return the properties mapping.

    Returns:
        {payload_key: {"name": str, "type": str, "nullable": bool, ...}}
    """
    schema_path = path or _default_schema_path()
    with open(schema_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("properties", {})


# ---------------------------------------------------------------------------
# SDK wrapper — bridges notion-client's client.pages.create() to Protocol's
# pages_create() / pages_update() interface used by NotionAdapter.
# ---------------------------------------------------------------------------


class _SDKClientWrapper:
    """Adapt notion-client SDK (client.pages.create) to flat Protocol interface."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def pages_create(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.pages.create(**kwargs)

    def pages_update(self, page_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._client.pages.update(page_id=page_id, **kwargs)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class NotionAdapter:
    """Wraps Notion API operations with version pin.

    Property name mapping is loaded from config/notion_schema.yaml (SSOT).
    No property names are hard-coded in this adapter.
    """

    def __init__(
        self,
        client: NotionClient,
        data_source_id: str,
        api_version: str = "2025-09-03",
        schema: dict[str, dict[str, Any]] | None = None,
        blocks_api: BlocksAPI | None = None,
        max_blocks: int = 200,
        max_payload_bytes: int = 200_000,
    ) -> None:
        self._client = client
        self._data_source_id = data_source_id
        self._api_version = api_version
        self._schema = schema or load_notion_schema()
        self._blocks = blocks_api
        self._max_blocks = max_blocks
        self._max_payload_bytes = max_payload_bytes

    @classmethod
    def from_settings(cls, settings: Any) -> "NotionAdapter":
        """Create adapter from settings, initializing real Notion client.

        Requires settings.notion_data_source_id to be non-empty
        when notion.enabled is True.
        """
        from notion_client import Client

        if not settings.notion_data_source_id:
            raise ValueError(
                "NOTION_DATA_SOURCE_ID is required when notion.enabled=True. "
                "Set it in .env or disable notion sync (NOTION__ENABLED=false)."
            )

        client = Client(
            auth=settings.notion_api_key,
            notion_version=settings.notion.api_version,
        )
        return cls(
            client=_SDKClientWrapper(client),
            data_source_id=settings.notion_data_source_id,
            api_version=settings.notion.api_version,
            blocks_api=client.blocks,
            max_blocks=settings.notion.max_blocks_per_payload,
            max_payload_bytes=settings.notion.max_payload_bytes,
        )

    def create_page(
        self,
        properties: dict[str, Any],
        children: list[dict[str, Any]],
    ) -> str:
        """Create a Notion page under the configured data source.

        Returns the page ID.

        Raises:
            NotionSyncError(too_many_blocks): Block count exceeds app limit.
            NotionSyncError(payload_too_large): Request body exceeds byte limit.
            RetryAfterError: On 429 rate limit.
            NotionSyncError: On other API failures.
        """
        notion_props = self._build_notion_properties(properties)
        blocks = self._build_notion_blocks(children)

        # App-level upper bound on total blocks per page (not a Notion API
        # hard limit). Prevents excessively large pages from being created.
        self._validate_block_count(blocks)

        parent = {
            "type": "data_source_id",
            "data_source_id": self._data_source_id,
        }
        request_body = {
            "parent": parent,
            "properties": notion_props,
            "children": blocks,
        }
        self._validate_payload_size(request_body)

        try:
            response = self._client.pages_create(**request_body)
            page_id = response.get("id", "")
            logger.info("notion.page_created", page_id=page_id)
            return page_id
        except Exception as e:
            self._raise_classified(e)

    @_RETRY_DECORATOR
    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
    ) -> str:
        """Update properties of an existing Notion page.

        Body (children) is updated separately via replace_body().
        Returns the page ID.

        Raises:
            NotionSyncError(payload_too_large): Properties body exceeds byte limit.
            RetryAfterError: On 429 rate limit.
            NotionSyncError: On other API failures.
        """
        notion_props = self._build_notion_properties(properties)
        self._validate_payload_size({"properties": notion_props})

        try:
            self._client.pages_update(
                page_id=page_id,
                properties=notion_props,
            )
            logger.info("notion.page_updated", page_id=page_id)
            return page_id
        except Exception as e:
            self._raise_classified(e)

    @_RETRY_DECORATOR
    def replace_body(
        self,
        page_id: str,
        children: list[dict[str, Any]],
    ) -> None:
        """Full-replace page body (children blocks).

        NON-ATOMIC: deletes all existing blocks then appends new ones.
        If this method fails mid-way, the page may have partial or no body.
        The caller must NOT update payload_hash on failure — the next run
        will detect the hash mismatch and retry a full replace.

        Steps:
          1. List existing child blocks (paginated, page_size=100)
          2. Delete each block
          3. Append new blocks in chunks of 100

        Raises:
            RetryAfterError: On 429 rate limit.
            NotionSyncError: On other API failures.
        """
        if self._blocks is None:
            raise NotionSyncError(
                "blocks API not available",
                error_type="client_error",
                retryable=False,
            )

        try:
            # 1. List all existing child blocks (pagination)
            existing_block_ids: list[str] = []
            cursor: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "page_size": _BLOCKS_LIST_PAGE_SIZE,
                }
                if cursor:
                    kwargs["start_cursor"] = cursor
                result = self._blocks.children.list(
                    block_id=page_id, **kwargs,
                )
                for block in result.get("results", []):
                    block_id = block.get("id")
                    if block_id:
                        existing_block_ids.append(block_id)
                if not result.get("has_more"):
                    break
                cursor = result.get("next_cursor")

            # 2. Delete each existing block
            for block_id in existing_block_ids:
                self._blocks.delete(block_id=block_id)

            # 3. Append new blocks in chunks of 100.
            # Each chunk is validated against max_payload_bytes before sending.
            new_blocks = self._build_notion_blocks(children)
            for i in range(0, len(new_blocks), _BLOCKS_PER_APPEND):
                chunk = new_blocks[i:i + _BLOCKS_PER_APPEND]
                self._validate_payload_size({"children": chunk})
                self._blocks.children.append(
                    block_id=page_id,
                    children=chunk,
                )

            logger.info(
                "notion.body_replaced",
                page_id=page_id,
                deleted=len(existing_block_ids),
                appended=len(new_blocks),
            )
        except Exception as e:
            self._raise_classified(e)

    def _validate_block_count(self, blocks: list[dict[str, Any]]) -> None:
        """Raise if block count exceeds the app-level upper bound.

        This is a conservative app limit, not a Notion API hard limit.
        Prevents excessively large pages from being created.
        """
        count = len(blocks)
        if count > self._max_blocks:
            raise NotionSyncError(
                f"Payload exceeds max blocks: {count} > {self._max_blocks}",
                error_type="too_many_blocks",
                retryable=False,
            )

    def _validate_payload_size(self, request_body: dict[str, Any]) -> None:
        """Raise if serialized request body exceeds max_payload_bytes."""
        size = len(_json.dumps(request_body, ensure_ascii=False).encode())
        if size > self._max_payload_bytes:
            raise NotionSyncError(
                f"Payload exceeds max bytes: {size} > {self._max_payload_bytes}",
                error_type="payload_too_large",
                retryable=False,
            )

    def _raise_classified(self, error: Exception) -> None:
        """Classify error and raise the appropriate NotionSyncError subtype."""
        # Don't re-wrap our own errors
        if isinstance(error, NotionSyncError):
            raise error

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

    # ------------------------------------------------------------------
    # Property mapping (driven by notion_schema.yaml)
    # ------------------------------------------------------------------

    def _build_notion_properties(self, props: dict[str, Any]) -> dict[str, Any]:
        """Convert flat properties dict to Notion API property format.

        Mapping is driven entirely by self._schema (loaded from
        config/notion_schema.yaml). No property names are hard-coded.

        Keys in schema but absent from props are silently skipped
        (no KeyError). This covers optional/nullable fields that
        build_notion_payload may omit.
        """
        notion_props: dict[str, Any] = {}

        for payload_key, schema_entry in self._schema.items():
            if payload_key not in props:
                continue

            value = props[payload_key]
            notion_name = schema_entry["name"]
            prop_type = schema_entry["type"]
            nullable = schema_entry.get("nullable", False)

            # Skip nullable properties with None value
            if value is None and nullable:
                continue

            converted = self._convert_property(prop_type, value)
            if converted is not None:
                notion_props[notion_name] = converted

        return notion_props

    def _convert_property(
        self, prop_type: str, value: Any,
    ) -> dict[str, Any] | None:
        """Convert a single property value to Notion API format."""
        converter = _PROPERTY_CONVERTERS.get(prop_type)
        if converter is None:
            logger.warning("notion.unknown_property_type", type=prop_type)
            return None
        return converter(value)

    # ------------------------------------------------------------------
    # Block building
    # ------------------------------------------------------------------

    def _build_notion_blocks(self, body: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert body blocks to Notion block format.

        Each section becomes a heading_2 + paragraph blocks.
        Text is chunked to 2000 chars per block (Notion limit).
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
            for chunk in _chunk_text(content, _TEXT_CHUNK_SIZE):
                blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": chunk}}]
                    },
                })
        return blocks


# ---------------------------------------------------------------------------
# Property type converters — pure functions
# ---------------------------------------------------------------------------


def _to_title(value: Any) -> dict[str, Any]:
    return {"title": [{"text": {"content": str(value) if value else ""}}]}


def _to_url(value: Any) -> dict[str, Any]:
    return {"url": str(value) if value else None}


def _to_date(value: Any) -> dict[str, Any]:
    if value is None:
        return {"date": None}
    return {"date": {"start": str(value)}}


def _to_select(value: Any) -> dict[str, Any]:
    if value is None:
        return {"select": None}
    return {"select": {"name": str(value)}}


def _to_multi_select(value: Any) -> dict[str, Any]:
    items = value if isinstance(value, list) else []
    return {"multi_select": [{"name": str(v)} for v in items]}


def _to_rich_text(value: Any) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": str(value) if value else ""}}]}


def _to_checkbox(value: Any) -> dict[str, Any]:
    return {"checkbox": bool(value)}


_PROPERTY_CONVERTERS: dict[str, Any] = {
    "title": _to_title,
    "url": _to_url,
    "date": _to_date,
    "select": _to_select,
    "multi_select": _to_multi_select,
    "rich_text": _to_rich_text,
    "checkbox": _to_checkbox,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks of max_len."""
    if not text:
        return [""]
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]
