"""Heisenberg Agent — CLI entry point."""

import argparse

from heisenberg_agent.settings import load_settings
from heisenberg_agent.storage.db import create_db_engine, get_session_factory, init_db
from heisenberg_agent.utils.logger import get_logger, setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Heisenberg Agent")
    parser.add_argument(
        "--mode",
        choices=["collect"],
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


if __name__ == "__main__":
    main()
