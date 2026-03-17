"""Database engine and session factory.

Applies SQLite PRAGMAs on connect:
- foreign_keys = ON
- journal_mode = WAL
"""

from sqlalchemy import Engine, event, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from heisenberg_agent.storage.models import Base


def _set_sqlite_pragmas(dbapi_conn: object, _connection_record: object) -> None:
    """Set SQLite PRAGMAs on every new connection."""
    cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


def create_db_engine(db_url: str, echo: bool = False) -> Engine:
    """Create SQLAlchemy engine with SQLite PRAGMAs."""
    engine = create_engine(db_url, echo=echo)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def init_db(engine: Engine) -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to the engine."""
    return sessionmaker(bind=engine)
