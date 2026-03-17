"""Unit tests for file-based pipeline lock."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from heisenberg_agent.runtime.locks import (
    LockError,
    LockHandle,
    acquire,
    release,
)


@pytest.fixture()
def lock_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.lock")


def test_acquire_creates_lock_file(lock_path: str):
    handle = acquire(lock_path)
    assert Path(lock_path).exists()
    assert handle.token

    # Lock file contains pid and token
    with open(lock_path) as f:
        data = json.load(f)
    assert data["pid"] == os.getpid()
    assert data["owner_token"] == handle.token
    assert "started_at" in data

    release(handle)


def test_acquire_fails_if_already_locked(lock_path: str):
    handle = acquire(lock_path)

    with pytest.raises(LockError, match="already running"):
        acquire(lock_path)

    release(handle)


def test_release_removes_file(lock_path: str):
    handle = acquire(lock_path)
    release(handle)
    assert not Path(lock_path).exists()


def test_release_with_wrong_token_does_not_delete(lock_path: str):
    handle = acquire(lock_path)

    # Try releasing with wrong token
    wrong_handle = LockHandle(path=lock_path, token="wrong-token")
    release(wrong_handle)

    # File should still exist
    assert Path(lock_path).exists()

    release(handle)


def test_reacquire_after_release(lock_path: str):
    handle1 = acquire(lock_path)
    release(handle1)

    handle2 = acquire(lock_path)
    assert handle2.token != handle1.token
    release(handle2)


def test_stale_lock_recovery(lock_path: str):
    """Dead PID lock is treated as stale and recovered."""
    # Write a lock with a PID that doesn't exist
    dead_pid = 99999999
    with open(lock_path, "w") as f:
        json.dump({
            "pid": dead_pid,
            "started_at": "2026-01-01T00:00:00+00:00",
            "owner_token": "old-token",
        }, f)

    # Should recover and acquire
    handle = acquire(lock_path)
    assert handle.token != "old-token"

    with open(lock_path) as f:
        data = json.load(f)
    assert data["pid"] == os.getpid()

    release(handle)


def test_corrupted_lock_recovery(lock_path: str):
    """Corrupted lock file is treated as stale."""
    with open(lock_path, "w") as f:
        f.write("not valid json{{{")

    handle = acquire(lock_path)
    assert handle.token
    release(handle)


def test_release_already_gone(lock_path: str):
    """Release on missing file does not raise."""
    handle = LockHandle(path=lock_path, token="any")
    release(handle)  # should not raise


def test_creates_parent_directories(tmp_path: Path):
    lock_path = str(tmp_path / "nested" / "dir" / "test.lock")
    handle = acquire(lock_path)
    assert Path(lock_path).exists()
    release(handle)
