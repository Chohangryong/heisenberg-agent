"""Live smoke test for pipeline — collect → analyze → sync(disabled).

Requires:
  LIVE_SMOKE=1 pytest -m live tests/live/test_pipeline_smoke.py -v

Also requires ANTHROPIC_API_KEY for analyze stage.

Sync targets are disabled (notion.enabled=False, vectordb.enabled=False).
Therefore:
- ensure_sync_jobs() creates no jobs (no enabled targets)
- sync stage processes 0 jobs → noop
- Pipeline status reflects collect+analyze only
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from heisenberg_agent.agents.analyzer import AnalyzerAgent
from heisenberg_agent.agents.collector import CollectorAgent
from heisenberg_agent.agents.sync_agent import SyncAgent
from heisenberg_agent.llm.client import LLMClient
from heisenberg_agent.orchestrator.pipeline import Pipeline
from heisenberg_agent.scrapers.heisenberg import load_selectors
from heisenberg_agent.storage.models import Article, CollectionRun, SyncJob
from tests.live.conftest import _skip_unless_llm

pytestmark = pytest.mark.live


def test_pipeline_collect_analyze_only(
    pw_adapter, live_settings, live_db, auth_state_path,
):
    """Pipeline runs collect→analyze with sync disabled.

    Assertions:
    - CollectionRun.status == "success"
    - articles_collected >= 1
    - At least one article has current_analysis_id (analyze succeeded)
    - sync_jobs table has 0 rows (disabled targets → no jobs created)
    """
    _skip_unless_llm(live_settings)

    # Override settings: disable sync targets, minimize collect scope for smoke
    live_settings.vectordb.enabled = False
    live_settings.notion.enabled = False
    live_settings.collector.max_articles_per_cycle = 1

    # Fresh session for pipeline (isolated from collector smoke)
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=live_db)
    session = factory()

    try:
        selectors = load_selectors()

        # Load LLM config
        llm_config = {}
        try:
            from pathlib import Path
            import yaml

            config_path = Path("config/llm_config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    llm_config = yaml.safe_load(f) or {}
        except Exception:
            pass

        # Assemble agents
        collector = CollectorAgent(
            adapter=pw_adapter,
            session=session,
            selectors=selectors,
            settings=live_settings,
        )
        analyzer = AnalyzerAgent(
            session=session,
            llm_client=LLMClient(llm_config),
            settings=live_settings,
        )
        syncer = SyncAgent(
            session=session,
            chroma_adapter=None,
            notion_adapter=None,
            settings=live_settings,
        )

        # Lock path in tmp
        import tempfile

        lock_path = str(Path(tempfile.mkdtemp()) / "pipeline.lock")

        pipeline = Pipeline(
            session=session,
            collector=collector,
            analyzer=analyzer,
            syncer=syncer,
            lock_path=lock_path,
        )

        run_id = pipeline.run()

        # Verify CollectionRun
        run = session.get(CollectionRun, run_id)
        assert run is not None, "CollectionRun not found"
        assert run.status == "success", f"expected success, got {run.status}"
        assert run.articles_collected >= 1, f"collected={run.articles_collected}"

        # Verify at least one article was analyzed
        articles = session.query(Article).filter(
            Article.current_analysis_id != None,  # noqa: E711
        ).all()
        assert len(articles) >= 1, "no articles with current_analysis_id"

        # Verify no sync_jobs created (disabled targets)
        sync_jobs = session.query(SyncJob).all()
        assert len(sync_jobs) == 0, (
            f"expected 0 sync_jobs with disabled targets, got {len(sync_jobs)}"
        )

    finally:
        session.close()
