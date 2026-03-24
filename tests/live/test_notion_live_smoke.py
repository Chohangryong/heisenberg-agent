"""Notion-only live smoke test.

Opt-in gates:
1. pytest -m live
2. LIVE_SMOKE=1
3. NOTION_API_KEY + NOTION_DATA_SOURCE_ID set

Isolation:
- Temp SQLite DB (never touches prod)
- Temp data_dir
- Seed data only (no collector/analyzer pipeline dependency)

Cleanup:
- Created Notion pages are trashed (best-effort, in_trash=True)
- All pages use [heisenberg-agent smoke] title prefix for identification
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from notion_client import Client as NotionClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from heisenberg_agent.adapters.notion_adapter import NotionAdapter
from heisenberg_agent.agents.sync_agent import SyncAgent
from heisenberg_agent.settings import load_settings
from heisenberg_agent.storage.models import (
    AnalysisRun,
    Article,
    ArticleSection,
    Base,
    SyncJob,
)
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()

SMOKE_PREFIX = "[heisenberg-agent smoke]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _skip_unless_notion_live():
    """Gate: LIVE_SMOKE=1 + Notion credentials."""
    if os.environ.get("LIVE_SMOKE") != "1":
        pytest.skip("LIVE_SMOKE=1 required")

    settings = load_settings()
    if not settings.notion_api_key:
        pytest.skip("NOTION_API_KEY required")
    if not settings.notion_data_source_id:
        pytest.skip("NOTION_DATA_SOURCE_ID required")
    return settings


@dataclass
class _SmokeSettings:
    """Minimal settings for Notion-only SyncAgent."""

    notion_api_key: str = ""
    notion_data_source_id: str = ""

    @dataclass
    class _Vectordb:
        enabled: bool = False
        embedding_version: str = "embed.v1"

    @dataclass
    class _Notion:
        enabled: bool = True
        api_version: str = "2025-09-03"
        sync_mode: str = "one_way"
        dry_run: bool = False
        obey_retry_after: bool = True
        max_blocks_per_payload: int = 200
        max_payload_bytes: int = 200000

    vectordb: _Vectordb = field(default_factory=_Vectordb)
    notion: _Notion = field(default_factory=_Notion)


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


def _seed_article(session: Session, slug: str = "smoke-test") -> Article:
    """Create a fully analyzed article with seed data for sync."""
    article = Article(
        source_site="heisenberg.kr",
        slug=slug,
        url=f"https://heisenberg.kr/{slug}/",
        title=f"{SMOKE_PREFIX} AI 반도체 전망 분석",
        author="김연구",
        category="AI",
        collected_at=_now_utc(),
        published_at=_now_utc(),
        collect_status="SUCCEEDED",
        analyze_status="SUCCEEDED",
        content_hash="smoke_hash_001",
    )
    session.add(article)
    session.flush()

    section = ArticleSection(
        article_id=article.id,
        ordinal=1,
        section_kind="main_body",
        body_text="AI 반도체 시장이 빠르게 성장하고 있다.",
    )
    session.add(section)

    run = AnalysisRun(
        article_id=article.id,
        source_content_hash="smoke_hash_001",
        analysis_version="analysis.v1",
        prompt_bundle_version="prompt-bundle.v1",
        summary_json=json.dumps({
            "core_thesis": "AI 반도체 시장은 2026년까지 급성장할 것이다",
            "supporting_points": ["수요 증가", "기술 혁신"],
            "conclusion": "투자 매력도 높음",
            "keywords": ["AI", "반도체"],
            "importance": "high",
        }),
        critique_json=json.dumps({
            "logic_gaps": ["공급 측면 분석 부족"],
            "missing_views": ["중국 시장 리스크"],
            "claims_to_verify": ["2026년 시장 규모 추정치"],
            "interest_analysis": "반도체 업계 관점",
            "overall_assessment": "단기 전망은 긍정적이나 리스크 주시 필요",
        }),
        llm_model="claude-sonnet-4-20250514",
        is_current=True,
        status="succeeded",
    )
    session.add(run)
    session.flush()

    article.current_analysis_id = run.id
    session.commit()
    return article


def _get_notion_job(session: Session, article_id: int) -> SyncJob | None:
    stmt = select(SyncJob).where(
        SyncJob.article_id == article_id,
        SyncJob.target == "notion",
    )
    return session.execute(stmt).scalar_one_or_none()


def _read_notion_page(client: NotionClient, page_id: str) -> dict:
    """Retrieve a Notion page with its properties."""
    return client.pages.retrieve(page_id=page_id)


def _read_notion_blocks(client: NotionClient, page_id: str) -> list[dict]:
    """Read all child blocks from a Notion page."""
    blocks: list[dict] = []
    cursor = None
    while True:
        kwargs: dict = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.blocks.children.list(**kwargs)
        blocks.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return blocks


def _extract_block_text(blocks: list[dict]) -> str:
    """Concatenate all rich_text content from blocks into a single string."""
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        for rt in block_data.get("rich_text", []):
            text = rt.get("text", {}).get("content", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


def _get_remote_select(page: dict, prop_name: str) -> str | None:
    """Extract select property value from a Notion page response."""
    prop = page.get("properties", {}).get(prop_name, {})
    select_val = prop.get("select")
    if select_val is None:
        return None
    return select_val.get("name")


def _get_remote_title(page: dict, prop_name: str) -> str:
    """Extract title property value from a Notion page response."""
    prop = page.get("properties", {}).get(prop_name, {})
    title_parts = prop.get("title", [])
    return "".join(t.get("text", {}).get("content", "") for t in title_parts)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_notion_live_smoke(tmp_path):
    """End-to-end Notion sync: create -> noop -> update property -> update body.

    Uses temp SQLite, real Notion API. Created pages are trashed on cleanup.
    """
    # --- Gate ---
    real_settings = _skip_unless_notion_live()

    # --- Temp DB ---
    db_path = tmp_path / "smoke.db"
    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    # --- Notion adapter (real) + raw client for verification ---
    settings = _SmokeSettings(
        notion_api_key=real_settings.notion_api_key,
        notion_data_source_id=real_settings.notion_data_source_id,
    )
    notion = NotionAdapter.from_settings(settings)

    raw_client = NotionClient(
        auth=real_settings.notion_api_key,
        notion_version="2025-09-03",
    )

    created_page_ids: list[str] = []

    try:
        # === Step 1: Create ===
        article = _seed_article(session)
        agent = SyncAgent(
            session=session,
            chroma_adapter=None,
            notion_adapter=notion,
            settings=settings,
        )
        stats = agent.run()

        assert stats["synced"] == 1, f"Expected 1 synced, got {stats}"
        assert stats["failed"] == 0, f"Unexpected failures: {stats}"

        job = _get_notion_job(session, article.id)
        assert job is not None, "Notion sync job not found"
        assert job.status == "succeeded"
        assert job.external_id, "No page_id stored"
        assert job.payload_hash, "No payload_hash stored"

        page_id = job.external_id
        first_hash = job.payload_hash
        created_page_ids.append(page_id)

        # Verify remote: title and importance
        remote_page = _read_notion_page(raw_client, page_id)
        remote_title = _get_remote_title(remote_page, "제목")
        assert SMOKE_PREFIX in remote_title, (
            f"Remote title mismatch: {remote_title!r}"
        )
        remote_importance = _get_remote_select(remote_page, "중요도")
        assert remote_importance == "high", (
            f"Remote importance mismatch: expected 'high', got {remote_importance!r}"
        )

        logger.info(
            "smoke.step1_create_ok",
            page_id=page_id,
            payload_hash=first_hash,
            remote_title=remote_title,
            remote_importance=remote_importance,
        )

        # === Step 2: Noop (same payload) ===
        # Succeeded jobs with unchanged payload_hash are not re-armed,
        # so find_pending_jobs returns 0 jobs. The noop manifests as
        # synced=0, skipped=0, failed=0 — no API calls made.
        agent2 = SyncAgent(
            session=session,
            chroma_adapter=None,
            notion_adapter=notion,
            settings=settings,
        )
        stats2 = agent2.run()

        assert stats2["synced"] == 0, f"Unexpected sync on noop: {stats2}"
        assert stats2["failed"] == 0, f"Unexpected failures on noop: {stats2}"

        job2 = _get_notion_job(session, article.id)
        assert job2.status == "succeeded", f"Job status changed on noop: {job2.status}"
        assert job2.external_id == page_id, "page_id changed on noop"
        assert job2.payload_hash == first_hash, "payload_hash changed on noop"

        logger.info("smoke.step2_noop_ok")

        # === Step 3: Update (property change -- importance high -> low) ===
        analysis_run = session.get(AnalysisRun, article.current_analysis_id)
        summary = json.loads(analysis_run.summary_json)
        summary["importance"] = "low"
        analysis_run.summary_json = json.dumps(summary)
        session.commit()

        agent3 = SyncAgent(
            session=session,
            chroma_adapter=None,
            notion_adapter=notion,
            settings=settings,
        )
        stats3 = agent3.run()

        assert stats3["synced"] == 1, f"Expected update sync, got {stats3}"
        assert stats3["failed"] == 0, f"Update failed: {stats3}"

        job3 = _get_notion_job(session, article.id)
        assert job3.external_id == page_id, "page_id changed on update"
        assert job3.payload_hash != first_hash, "payload_hash should change"

        second_hash = job3.payload_hash

        # Verify remote: importance changed to "low", same page
        remote_page3 = _read_notion_page(raw_client, page_id)
        remote_importance3 = _get_remote_select(remote_page3, "중요도")
        assert remote_importance3 == "low", (
            f"Remote importance not updated: expected 'low', got {remote_importance3!r}"
        )
        assert remote_page3["id"].replace("-", "") == page_id.replace("-", ""), (
            "Remote page ID mismatch — a new page may have been created"
        )

        logger.info(
            "smoke.step3_update_property_ok",
            old_hash=first_hash,
            new_hash=second_hash,
            remote_importance=remote_importance3,
        )

        # === Step 4: Update (body change -- new critique) ===
        updated_assessment = "리스크 재평가 필요 -- 공급망 이슈 심화"
        critique = json.loads(analysis_run.critique_json)
        critique["overall_assessment"] = updated_assessment
        analysis_run.critique_json = json.dumps(critique)
        session.commit()

        agent4 = SyncAgent(
            session=session,
            chroma_adapter=None,
            notion_adapter=notion,
            settings=settings,
        )
        stats4 = agent4.run()

        assert stats4["synced"] == 1, f"Expected body update sync, got {stats4}"
        assert stats4["failed"] == 0, f"Body update failed: {stats4}"

        job4 = _get_notion_job(session, article.id)
        assert job4.external_id == page_id, "page_id changed on body update"
        assert job4.payload_hash != second_hash, "payload_hash should change"

        # Verify remote: updated body contains new assessment text
        remote_blocks = _read_notion_blocks(raw_client, page_id)
        body_text = _extract_block_text(remote_blocks)
        assert updated_assessment in body_text, (
            f"Body does not contain updated assessment.\n"
            f"Expected substring: {updated_assessment!r}\n"
            f"Got body: {body_text[:500]!r}"
        )

        logger.info(
            "smoke.step4_update_body_ok",
            old_hash=second_hash,
            new_hash=job4.payload_hash,
        )

        logger.info(
            "smoke.all_steps_passed",
            page_id=page_id,
            final_hash=job4.payload_hash,
        )

    finally:
        # --- Cleanup: trash created pages (best-effort) ---
        for pid in created_page_ids:
            try:
                raw_client.pages.update(page_id=pid, in_trash=True)
                logger.info("smoke.cleanup_trashed", page_id=pid)
            except Exception as e:
                logger.warning(
                    "smoke.cleanup_failed",
                    page_id=pid,
                    error=str(e),
                )

        session.close()
        engine.dispose()
