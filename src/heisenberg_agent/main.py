"""Heisenberg Agent — CLI entry point."""

import argparse

from heisenberg_agent.settings import load_settings
from heisenberg_agent.storage.db import create_db_engine, get_session_factory, init_db
from heisenberg_agent.utils.logger import get_logger, setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Heisenberg Agent")
    parser.add_argument(
        "--mode",
        choices=["collect", "analyze", "sync", "pipeline", "scheduler"],
        default="collect",
        help="Execution mode (default: collect)",
    )
    args = parser.parse_args()

    settings = load_settings()

    setup_logging(
        level=settings.logging.level,
        log_file=settings.logging.file,
    )
    logger = get_logger()

    engine = create_db_engine(settings.database.url)
    init_db(engine)
    logger.info("db_initialized", db_url=settings.database.url)

    if args.mode == "collect":
        _run_collect(settings, engine, logger)
    elif args.mode == "analyze":
        _run_analyze(settings, engine, logger)
    elif args.mode == "sync":
        _run_sync(settings, engine, logger)
    elif args.mode == "pipeline":
        _run_pipeline(settings, engine, logger)
    elif args.mode == "scheduler":
        _run_scheduler(settings, engine, logger)


def _run_collect(settings, engine, logger) -> None:
    """Run one collection cycle."""
    from heisenberg_agent.adapters.playwright_adapter import PlaywrightAdapter
    from heisenberg_agent.agents.collector import CollectorAgent
    from heisenberg_agent.scrapers.heisenberg import load_selectors

    selectors = load_selectors()
    session_factory = get_session_factory(engine)
    session = session_factory()

    adapter = PlaywrightAdapter(
        auth_state_path=str(settings.data_dir) + "/runtime/auth_state.json",
    )

    try:
        adapter.start()
        agent = CollectorAgent(
            adapter=adapter,
            session=session,
            selectors=selectors,
            settings=settings,
        )
        run = agent.run()
        logger.info(
            "collect_finished",
            run_id=run.id,
            status=run.status,
            collected=run.articles_collected,
            errors=run.errors,
        )
    finally:
        adapter.close()
        session.close()


def _run_analyze(settings, engine, logger) -> None:
    """Run one analysis cycle."""
    from heisenberg_agent.agents.analyzer import AnalyzerAgent
    from heisenberg_agent.llm.client import LLMClient

    llm_config = {}
    try:
        from pathlib import Path
        import yaml
        config_path = Path("config/llm_config.yaml")
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                llm_config = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("llm_config_load_failed", error=str(e))

    session_factory = get_session_factory(engine)
    session = session_factory()
    llm_client = LLMClient(llm_config)

    try:
        agent = AnalyzerAgent(
            session=session,
            llm_client=llm_client,
            settings=settings,
        )
        stats = agent.run()
        logger.info("analyze_finished", **stats)
    finally:
        session.close()


def _run_sync(settings, engine, logger) -> None:
    """Run one sync cycle."""
    from heisenberg_agent.adapters.chroma_adapter import ChromaAdapter
    from heisenberg_agent.adapters.notion_adapter import NotionAdapter
    from heisenberg_agent.agents.sync_agent import SyncAgent

    session_factory = get_session_factory(engine)
    session = session_factory()

    chroma = None
    if getattr(settings.vectordb, "enabled", True):
        chroma = ChromaAdapter.from_settings(settings)

    notion = None
    if getattr(settings.notion, "enabled", True):
        notion = NotionAdapter.from_settings(settings)

    try:
        agent = SyncAgent(
            session=session,
            chroma_adapter=chroma,
            notion_adapter=notion,
            settings=settings,
        )
        stats = agent.run()
        logger.info("sync_finished", **stats)
    finally:
        session.close()


def _run_pipeline(settings, engine, logger) -> None:
    """Run full pipeline: collect → analyze → sync."""
    import sys

    from heisenberg_agent.adapters.chroma_adapter import ChromaAdapter
    from heisenberg_agent.adapters.notion_adapter import NotionAdapter
    from heisenberg_agent.adapters.playwright_adapter import PlaywrightAdapter
    from heisenberg_agent.agents.collector import CollectorAgent
    from heisenberg_agent.agents.analyzer import AnalyzerAgent
    from heisenberg_agent.agents.sync_agent import SyncAgent
    from heisenberg_agent.llm.client import LLMClient
    from heisenberg_agent.orchestrator.pipeline import Pipeline
    from heisenberg_agent.runtime.locks import LockError
    from heisenberg_agent.scrapers.heisenberg import load_selectors

    session_factory = get_session_factory(engine)
    session = session_factory()
    lock_path = str(settings.data_dir) + "/runtime/pipeline.lock"

    # Assemble agents
    selectors = load_selectors()
    pw_adapter = PlaywrightAdapter(
        auth_state_path=str(settings.data_dir) + "/runtime/auth_state.json",
    )

    llm_config = {}
    try:
        from pathlib import Path
        import yaml
        config_path = Path("config/llm_config.yaml")
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                llm_config = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("llm_config_load_failed", error=str(e))

    chroma = None
    if getattr(settings.vectordb, "enabled", True):
        chroma = ChromaAdapter.from_settings(settings)
    notion = None
    if getattr(settings.notion, "enabled", True):
        notion = NotionAdapter.from_settings(settings)

    collector = CollectorAgent(
        adapter=pw_adapter, session=session, selectors=selectors, settings=settings,
    )
    analyzer = AnalyzerAgent(
        session=session, llm_client=LLMClient(llm_config), settings=settings,
    )
    syncer = SyncAgent(
        session=session, chroma_adapter=chroma, notion_adapter=notion, settings=settings,
    )

    try:
        pw_adapter.start()
        pipeline = Pipeline(
            session=session,
            collector=collector,
            analyzer=analyzer,
            syncer=syncer,
            lock_path=lock_path,
        )
        run_id = pipeline.run()
        logger.info("pipeline_finished", run_id=run_id)
    except LockError as e:
        logger.error("pipeline_locked", error=str(e))
        sys.exit(1)
    finally:
        pw_adapter.close()
        session.close()


def _run_scheduler(settings, engine, logger) -> None:
    """Start the scheduler for periodic pipeline execution.

    Uses a factory pattern: each job execution creates a fresh session,
    adapters, and agents. No long-lived session across runs.
    """
    from heisenberg_agent.scheduler import start_scheduler

    session_factory = get_session_factory(engine)

    def pipeline_factory():
        """Create a fresh Pipeline with its own session. Returns (pipeline, cleanup)."""
        from heisenberg_agent.adapters.chroma_adapter import ChromaAdapter
        from heisenberg_agent.adapters.notion_adapter import NotionAdapter
        from heisenberg_agent.adapters.playwright_adapter import PlaywrightAdapter
        from heisenberg_agent.agents.collector import CollectorAgent
        from heisenberg_agent.agents.analyzer import AnalyzerAgent
        from heisenberg_agent.agents.sync_agent import SyncAgent
        from heisenberg_agent.llm.client import LLMClient
        from heisenberg_agent.orchestrator.pipeline import Pipeline
        from heisenberg_agent.scrapers.heisenberg import load_selectors

        session = session_factory()
        lock_path = str(settings.data_dir) + "/runtime/pipeline.lock"

        selectors = load_selectors()
        pw_adapter = PlaywrightAdapter(
            auth_state_path=str(settings.data_dir) + "/runtime/auth_state.json",
        )
        pw_adapter.start()

        llm_config = {}
        try:
            from pathlib import Path
            import yaml
            config_path = Path("config/llm_config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    llm_config = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("llm_config_load_failed", error=str(e))

        chroma = None
        if getattr(settings.vectordb, "enabled", True):
            chroma = ChromaAdapter.from_settings(settings)
        notion = None
        if getattr(settings.notion, "enabled", True):
            notion = NotionAdapter.from_settings(settings)

        collector = CollectorAgent(
            adapter=pw_adapter, session=session, selectors=selectors, settings=settings,
        )
        analyzer = AnalyzerAgent(
            session=session, llm_client=LLMClient(llm_config), settings=settings,
        )
        syncer = SyncAgent(
            session=session, chroma_adapter=chroma, notion_adapter=notion, settings=settings,
        )

        pipeline = Pipeline(
            session=session, collector=collector, analyzer=analyzer,
            syncer=syncer, lock_path=lock_path,
        )

        def cleanup():
            pw_adapter.close()
            session.close()

        return pipeline, cleanup

    start_scheduler(pipeline_factory, settings)


if __name__ == "__main__":
    main()
