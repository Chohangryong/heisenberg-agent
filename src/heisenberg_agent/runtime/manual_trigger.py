"""Manual trigger HTTP server — local-only, token-authenticated.

Runs on a daemon thread alongside the scheduler.
Accepts POST /trigger to enqueue an immediate pipeline job.

Security:
- Binds to 127.0.0.1 only (not 0.0.0.0).
- Requires MANUAL_TRIGGER_TOKEN for authentication.
- No token configured → server does not start.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from heisenberg_agent.utils.logger import get_logger

logger = get_logger()

MANUAL_JOB_ID = "pipeline_manual"


class TriggerHandler(BaseHTTPRequestHandler):
    """Handles POST /trigger requests."""

    # Suppress default stderr logging
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        if self.path != "/trigger":
            self._respond(404, {"status": "not_found"})
            return

        if not self._check_token():
            self._respond(401, {"status": "unauthorized"})
            return

        status_code, body = self._enqueue_job()
        self._respond(status_code, body)

    def _check_token(self) -> bool:
        expected = self.server.trigger_token  # type: ignore[attr-defined]
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {expected}"

    def _enqueue_job(self) -> tuple[int, dict[str, str]]:
        """Enqueue a manual pipeline job.

        Returns:
            (status_code, response_body)
            - 200: triggered
            - 409: already_queued (pending manual job exists)
            - 500: scheduler error
        """
        scheduler = self.server.scheduler  # type: ignore[attr-defined]
        run_pipeline_fn = self.server.run_pipeline_fn  # type: ignore[attr-defined]
        get_now = self.server.get_now  # type: ignore[attr-defined]

        # Check for existing pending manual job
        existing = scheduler.get_job(MANUAL_JOB_ID)
        if existing is not None:
            logger.info("trigger.already_queued")
            return 409, {"status": "already_queued"}

        try:
            scheduler.add_job(
                run_pipeline_fn,
                trigger="date",
                run_date=get_now(),
                id=MANUAL_JOB_ID,
                replace_existing=False,
            )
            logger.info("trigger.enqueued")
            return 200, {"status": "triggered"}
        except Exception as e:
            logger.error("trigger.enqueue_failed", error=str(e))
            return 500, {"status": "error", "message": str(e)[:200]}

    def _respond(self, code: int, body: dict[str, str]) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))


class TriggerServer:
    """Manages the manual trigger HTTP server lifecycle."""

    def __init__(
        self,
        scheduler: Any,
        run_pipeline_fn: Any,
        get_now: Any,
        token: str,
        bind: str = "127.0.0.1",
        port: int = 8321,
    ) -> None:
        self._server = HTTPServer((bind, port), TriggerHandler)
        self._server.scheduler = scheduler  # type: ignore[attr-defined]
        self._server.run_pipeline_fn = run_pipeline_fn  # type: ignore[attr-defined]
        self._server.get_now = get_now  # type: ignore[attr-defined]
        self._server.trigger_token = token  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start HTTP server on a daemon thread."""
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="manual-trigger",
        )
        self._thread.start()
        addr = self._server.server_address
        logger.info("trigger.server_started", bind=addr[0], port=addr[1])

    def shutdown(self) -> None:
        """Stop HTTP server and join thread."""
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server.server_close()
        logger.info("trigger.server_stopped")
