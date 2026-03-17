"""Unit tests for manual trigger HTTP handler."""

import json
import threading
import time
from http.client import HTTPConnection
from unittest.mock import MagicMock

import pytest

from heisenberg_agent.runtime.manual_trigger import MANUAL_JOB_ID, TriggerServer
from heisenberg_agent.utils.dt import now_kst


class FakeScheduler:
    """Mimics APScheduler for trigger tests."""

    def __init__(self) -> None:
        self._jobs: dict[str, object] = {}

    def get_job(self, job_id: str) -> object | None:
        return self._jobs.get(job_id)

    def add_job(self, func, *, trigger, run_date, id, replace_existing=False):
        self._jobs[id] = {"func": func, "run_date": run_date}

    def remove_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)


class FailingScheduler(FakeScheduler):
    """add_job always raises."""

    def add_job(self, *args, **kwargs):
        raise RuntimeError("scheduler broken")


@pytest.fixture()
def trigger_env():
    """Start a TriggerServer on a random port, yield connection info, then shutdown."""
    scheduler = FakeScheduler()
    token = "test-token-123"

    server = TriggerServer(
        scheduler=scheduler,
        run_pipeline_fn=lambda: None,
        get_now=now_kst,
        token=token,
        bind="127.0.0.1",
        port=0,  # OS assigns port
    )
    # Get actual port after bind
    port = server._server.server_address[1]
    server.start()

    yield {
        "port": port,
        "token": token,
        "scheduler": scheduler,
        "server": server,
    }

    server.shutdown()


def _post(port: int, path: str, token: str | None = None) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", path, headers=headers)
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    conn.close()
    return resp.status, body


def test_valid_trigger(trigger_env):
    code, body = _post(trigger_env["port"], "/trigger", trigger_env["token"])
    assert code == 200
    assert body["status"] == "triggered"


def test_invalid_token(trigger_env):
    code, body = _post(trigger_env["port"], "/trigger", "wrong-token")
    assert code == 401
    assert body["status"] == "unauthorized"


def test_no_token(trigger_env):
    code, body = _post(trigger_env["port"], "/trigger")
    assert code == 401


def test_wrong_path(trigger_env):
    code, body = _post(trigger_env["port"], "/other", trigger_env["token"])
    assert code == 404


def test_duplicate_trigger_returns_409(trigger_env):
    """Second trigger while first is pending → 409."""
    code1, body1 = _post(trigger_env["port"], "/trigger", trigger_env["token"])
    assert code1 == 200

    code2, body2 = _post(trigger_env["port"], "/trigger", trigger_env["token"])
    assert code2 == 409
    assert body2["status"] == "already_queued"


def test_trigger_after_job_removal(trigger_env):
    """After job completes (removed), trigger works again."""
    code1, _ = _post(trigger_env["port"], "/trigger", trigger_env["token"])
    assert code1 == 200

    # Simulate job completion: remove from scheduler
    trigger_env["scheduler"].remove_job(MANUAL_JOB_ID)

    code2, body2 = _post(trigger_env["port"], "/trigger", trigger_env["token"])
    assert code2 == 200
    assert body2["status"] == "triggered"


def test_add_job_failure_returns_500():
    """Scheduler.add_job raises → 500 response."""
    scheduler = FailingScheduler()

    server = TriggerServer(
        scheduler=scheduler,
        run_pipeline_fn=lambda: None,
        get_now=now_kst,
        token="test-token",
        bind="127.0.0.1",
        port=0,
    )
    port = server._server.server_address[1]
    server.start()

    try:
        code, body = _post(port, "/trigger", "test-token")
        assert code == 500
        assert body["status"] == "error"
    finally:
        server.shutdown()


def test_server_shutdown():
    """Server thread stops after shutdown."""
    scheduler = FakeScheduler()
    server = TriggerServer(
        scheduler=scheduler,
        run_pipeline_fn=lambda: None,
        get_now=now_kst,
        token="test-token",
        bind="127.0.0.1",
        port=0,
    )
    server.start()
    assert server._thread is not None
    assert server._thread.is_alive()

    server.shutdown()
    assert not server._thread.is_alive()
