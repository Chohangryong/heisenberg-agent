"""Live smoke test fixtures.

Opt-in gates (all must be satisfied):
1. pytest -m live
2. LIVE_SMOKE=1
3. HEISENBERG_USERNAME_OR_EMAIL + HEISENBERG_PASSWORD set

Auth state isolation:
- Tests use tmp_path auth_state, never the real one directly.
- If data/runtime/auth_state.json exists, it is seed-copied into tmp.
- LIVE_REFRESH_AUTH=1 copies tmp auth_state back after tests succeed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from heisenberg_agent.adapters.playwright_adapter import PlaywrightAdapter
from heisenberg_agent.scrapers.heisenberg import load_selectors
from heisenberg_agent.settings import load_settings
from heisenberg_agent.storage.db import init_db
from heisenberg_agent.storage.models import Base


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


def _skip_unless_live():
    """Check all opt-in gates. Returns settings if satisfied."""
    if os.environ.get("LIVE_SMOKE") != "1":
        pytest.skip("LIVE_SMOKE=1 required")

    settings = load_settings()
    if not settings.heisenberg_username_or_email or not settings.heisenberg_password:
        pytest.skip("heisenberg credentials not configured")

    return settings


def _skip_unless_llm(settings):
    """Additional gate for tests that need LLM API."""
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY required for pipeline smoke")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_settings():
    """Load settings with live opt-in gates."""
    return _skip_unless_live()


@pytest.fixture(scope="module")
def live_db(tmp_path_factory):
    """Temp SQLite engine + initialized schema."""
    db_path = tmp_path_factory.mktemp("db") / "smoke.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    init_db(engine)
    return engine


@pytest.fixture(scope="module")
def live_session(live_db):
    """Session from temp SQLite."""
    factory = sessionmaker(bind=live_db)
    session = factory()
    yield session
    session.close()


@pytest.fixture(scope="module")
def auth_state_path(tmp_path_factory):
    """Isolated auth_state path.

    - Seed-copies from real auth_state if it exists.
    - Optionally writes back on LIVE_REFRESH_AUTH=1.
    """
    tmp_runtime = tmp_path_factory.mktemp("runtime")
    tmp_auth = tmp_runtime / "auth_state.json"

    real_auth = Path("data/runtime/auth_state.json")
    if real_auth.exists():
        shutil.copy2(real_auth, tmp_auth)

    yield tmp_auth

    if os.environ.get("LIVE_REFRESH_AUTH") == "1" and tmp_auth.exists():
        real_auth.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_auth, real_auth)


@pytest.fixture(scope="module")
def pw_adapter(live_settings, auth_state_path):
    """PlaywrightAdapter with isolated auth_state. Browser started once per module."""
    adapter = PlaywrightAdapter(
        auth_state_path=str(auth_state_path),
        headless=True,
    )
    adapter.start()
    yield adapter
    adapter.close()


@pytest.fixture(scope="module")
def selectors():
    """Selector profile from config."""
    return load_selectors()
