"""File-based single-run lock for pipeline.

Prevents concurrent pipeline execution on a local single-user machine.

Lock file contains: {"pid": ..., "started_at": ..., "owner_token": ...}
Acquire uses os.open(O_CREAT | O_EXCL) for atomic creation.
Release verifies owner_token before deletion.

Premises:
- Local single-user environment. PID reuse is safe for minute-scale runs.
- Not designed for NFS/network filesystems (O_EXCL may not be guaranteed).
- Corrupted lock files are treated as stale.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from heisenberg_agent.utils.dt import now_utc
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


class LockError(Exception):
    """Raised when lock cannot be acquired."""
    pass


@dataclass
class LockHandle:
    """Handle returned by acquire(). Pass to release()."""

    path: str
    token: str


def acquire(lock_path: str) -> LockHandle:
    """Acquire an exclusive pipeline lock.

    Uses atomic file creation (O_CREAT | O_EXCL) to prevent races.
    Detects stale locks via PID liveness check.

    Returns:
        LockHandle for use with release().

    Raises:
        LockError: If lock is held by a live process.
    """
    token = uuid4().hex
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: try atomic exclusive create
    handle = _try_create(lock_path, token)
    if handle:
        return handle

    # Step 2: lock exists — read and check stale
    existing = _safe_read_lock(lock_path)
    if existing is None:
        # Corrupted or unreadable — treat as stale
        logger.warning("lock.corrupted", path=lock_path)
        _safe_unlink(lock_path)
        return _retry_create(lock_path, token)

    # Step 3: PID alive check
    pid = existing.get("pid", -1)
    if _is_process_alive(pid):
        raise LockError(
            f"Pipeline already running (pid={pid}, "
            f"started={existing.get('started_at', 'unknown')})"
        )

    # Step 4: stale lock — remove and retry
    logger.warning("lock.stale_recovered", old_pid=pid)
    _safe_unlink(lock_path)
    return _retry_create(lock_path, token)


def release(handle: LockHandle) -> None:
    """Release the pipeline lock.

    Only deletes the lock file if current process owns it (token match).
    """
    existing = _safe_read_lock(handle.path)
    if existing is None:
        logger.warning("lock.already_gone", path=handle.path)
        return

    if existing.get("owner_token") == handle.token:
        _safe_unlink(handle.path)
        logger.info("lock.released", path=handle.path)
    else:
        logger.warning(
            "lock.not_owner",
            path=handle.path,
            expected=handle.token,
            actual=existing.get("owner_token"),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _try_create(lock_path: str, token: str) -> LockHandle | None:
    """Attempt atomic exclusive file creation. Returns None if file exists."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        _write_lock_fd(fd, token)
        os.close(fd)
        logger.info("lock.acquired", path=lock_path, pid=os.getpid())
        return LockHandle(path=lock_path, token=token)
    except FileExistsError:
        return None


def _retry_create(lock_path: str, token: str) -> LockHandle:
    """Retry exclusive create after stale removal.

    Raises LockError if another process acquired between unlink and create.
    """
    handle = _try_create(lock_path, token)
    if handle:
        return handle
    raise LockError("Lock acquired by another process during recovery")


def _write_lock_fd(fd: int, token: str) -> None:
    """Write lock content to file descriptor."""
    content = json.dumps({
        "pid": os.getpid(),
        "started_at": now_utc().isoformat(),
        "owner_token": token,
    })
    os.write(fd, content.encode("utf-8"))


def _safe_read_lock(lock_path: str) -> dict | None:
    """Read lock file content. Returns None on any error."""
    try:
        with open(lock_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _safe_unlink(path: str) -> None:
    """Remove file, ignore FileNotFoundError (race with another process)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _is_process_alive(pid: int) -> bool:
    """Check if PID is alive. Local single-user premise.

    PID reuse risk is negligible for minute-scale pipeline runs.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False  # process dead
    except PermissionError:
        return True  # process exists but different user
