"""Content hashing utilities."""

import hashlib
import re


def normalize_text(text: str) -> str:
    """Normalize text for stable hashing: collapse whitespace, strip."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def content_hash(text: str) -> str:
    """Compute SHA-256 hash of normalized text."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def file_sha256(path: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def payload_hash(data: str) -> str:
    """Compute SHA-256 of a serialized payload string for no-op skip."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
