"""Playwright browser adapter — login, page load, PDF snapshot.

Encapsulates all Playwright interactions. External callers receive
only strings (HTML) and Paths (snapshots), never Playwright objects.

Test boundary: mock this adapter to eliminate live site dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


@dataclass
class AuthResult:
    """Result of ensure_authenticated()."""

    success: bool
    error_code: str | None = None  # "login_failed" | "verify_failed"
    attempts: int = 0


class PlaywrightAdapter:
    """Manages browser lifecycle, auth state, page loading, and snapshots."""

    def __init__(
        self,
        auth_state_path: str = "data/runtime/auth_state.json",
        headless: bool = True,
    ) -> None:
        self._auth_state_path = Path(auth_state_path)
        self._headless = headless
        self._pw_ctx: Any | None = None  # playwright context manager
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch browser. Call once before any page operations."""
        self._pw_ctx = sync_playwright().start()
        self._browser = self._pw_ctx.chromium.launch(headless=self._headless)
        self._context = self._create_context()
        self._page = self._context.new_page()

    def close(self) -> None:
        """Clean up browser resources."""
        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw_ctx:
            self._pw_ctx.stop()

    def _create_context(self) -> BrowserContext:
        """Create context, restoring storage_state if available."""
        assert self._browser is not None
        if self._auth_state_path.exists():
            logger.info("auth.loading_state", path=str(self._auth_state_path))
            return self._browser.new_context(
                storage_state=str(self._auth_state_path)
            )
        return self._browser.new_context()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def ensure_authenticated(
        self,
        login_url: str,
        username: str,
        password: str,
        verification_url: str,
        verification_selector: str,
        max_attempts: int = 3,
    ) -> AuthResult:
        """Ensure we have a valid authenticated session.

        Flow:
        1. If auth_state.json exists → load → verify
        2. If valid → success
        3. If invalid or missing → login → save → verify
        4. Up to max_attempts login retries

        Returns:
            AuthResult with success flag, error_code, and attempt count.
        """
        if self._auth_state_path.exists():
            if self._verify_auth(verification_url, verification_selector):
                logger.info("auth.state_valid")
                return AuthResult(success=True, attempts=0)
            logger.warning("auth.state_expired")

        last_error_code: str | None = None
        for attempt in range(1, max_attempts + 1):
            logger.info("auth.login_attempt", attempt=attempt)
            if self._login(login_url, username, password):
                self._save_auth_state()
                if self._verify_auth(verification_url, verification_selector):
                    logger.info("auth.login_succeeded", attempt=attempt)
                    return AuthResult(success=True, attempts=attempt)
                last_error_code = "verify_failed"
            else:
                last_error_code = "login_failed"
            logger.warning("auth.login_failed", attempt=attempt, error_code=last_error_code)

        logger.error("auth.all_attempts_failed", error_code=last_error_code)
        return AuthResult(success=False, error_code=last_error_code, attempts=max_attempts)

    def _login(self, login_url: str, username: str, password: str) -> bool:
        """Submit login form. Returns True if page navigated after submit."""
        assert self._page is not None
        try:
            self._page.goto(login_url, wait_until="domcontentloaded")
            # Fill login form — field names based on site observation (§2.1)
            self._page.fill('input[name="log"], input[type="email"]', username)
            self._page.fill('input[name="pwd"], input[type="password"]', password)
            self._page.click('button[type="submit"], input[type="submit"]')
            self._page.wait_for_load_state("domcontentloaded")
            return True
        except Exception as e:
            logger.error("auth.login_exception", error=str(e))
            return False

    def _verify_auth(self, url: str, selector: str) -> bool:
        """Load a page and check if auth-gated selector is visible."""
        assert self._page is not None
        try:
            self._page.goto(url, wait_until="domcontentloaded")
            el = self._page.query_selector(selector)
            return el is not None
        except Exception as e:
            logger.error("auth.verify_exception", error=str(e))
            return False

    def _save_auth_state(self) -> None:
        """Persist browser storage state for reuse."""
        assert self._context is not None
        self._auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(self._auth_state_path))
        logger.info("auth.state_saved", path=str(self._auth_state_path))

    # ------------------------------------------------------------------
    # Page loading
    # ------------------------------------------------------------------

    def load_page(
        self,
        url: str,
        ready_selector: str | None = None,
        timeout_ms: int = 10000,
    ) -> str:
        """Load URL and return rendered DOM HTML.

        Args:
            url: Page URL.
            ready_selector: CSS selector to wait for before capturing HTML.
            timeout_ms: Max wait time for ready_selector.

        Returns:
            Rendered HTML string from page.content().
        """
        assert self._page is not None
        self._page.goto(url, wait_until="domcontentloaded")

        if ready_selector:
            self._page.wait_for_selector(
                ready_selector, timeout=timeout_ms, state="attached"
            )

        return self._page.content()

    # ------------------------------------------------------------------
    # PDF Snapshot
    # ------------------------------------------------------------------

    def take_snapshot(self, output_path: str | Path) -> Path | None:
        """Generate PDF snapshot of the currently loaded page.

        Uses screen media emulation (not print) per design spec §10.5.
        Failure does not propagate — returns None with a warning log.

        Args:
            output_path: Where to save the PDF file.

        Returns:
            Path to saved PDF, or None if snapshot failed.
        """
        assert self._page is not None
        out = Path(output_path)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            self._page.emulate_media(media="screen")
            self._page.pdf(
                path=str(out),
                print_background=True,
                format="A4",
            )
            logger.info("snapshot.saved", path=str(out))
            return out
        except Exception as e:
            logger.warning("snapshot.failed", path=str(out), error=str(e))
            return None
