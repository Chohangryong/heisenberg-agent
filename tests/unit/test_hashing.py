"""Tests for utils.hashing."""

from heisenberg_agent.utils.hashing import content_hash, normalize_text, payload_hash


def test_normalize_collapses_whitespace():
    assert normalize_text("  hello   world  ") == "hello world"


def test_normalize_strips_newlines():
    assert normalize_text("a\n\n  b\t c") == "a b c"


def test_content_hash_stable():
    h1 = content_hash("hello  world")
    h2 = content_hash("hello world")
    assert h1 == h2


def test_content_hash_differs_for_different_content():
    assert content_hash("aaa") != content_hash("bbb")


def test_payload_hash_deterministic():
    data = '{"key": "value"}'
    assert payload_hash(data) == payload_hash(data)
