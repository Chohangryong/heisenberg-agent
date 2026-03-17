"""Timezone-aware datetime helpers."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_utc() -> datetime:
    """Current time in UTC."""
    return datetime.now(timezone.utc)


def now_kst() -> datetime:
    """Current time in Asia/Seoul."""
    return datetime.now(KST)


def to_utc(dt: datetime) -> datetime:
    """Convert any aware datetime to UTC."""
    if dt.tzinfo is None:
        raise ValueError("Cannot convert naive datetime to UTC")
    return dt.astimezone(timezone.utc)
