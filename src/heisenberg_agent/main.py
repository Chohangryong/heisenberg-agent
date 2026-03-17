"""Heisenberg Agent — CLI entry point."""

import sys

from heisenberg_agent.settings import load_settings
from heisenberg_agent.storage.db import create_db_engine, init_db
from heisenberg_agent.utils.logger import get_logger, setup_logging


def main() -> None:
    settings = load_settings()

    setup_logging(
        level=settings.logging.level,
        log_file=settings.logging.file,
    )
    logger = get_logger()

    engine = create_db_engine(settings.database.url)
    init_db(engine)
    logger.info("db_initialized", db_url=settings.database.url)

    # Phase 1: CollectorAgent
    # Phase 2: AnalyzerAgent
    # Phase 3: SyncAgent
    # Phase 4: Pipeline orchestrator with lock + report

    logger.info("pipeline_placeholder", msg="No agents implemented yet")


if __name__ == "__main__":
    main()
