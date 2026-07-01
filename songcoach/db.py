"""SQLAlchemy engine, session, and a tiny CLI for schema management."""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_is_sqlite = settings.normalized_database_url.startswith("sqlite")
_connect_args = {}
if _is_sqlite:
    # Allow use across the web + worker processes/threads; wait on locks.
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(
    settings.normalized_database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL lets a reader (web) and writer (worker) coexist without the
        # "disk I/O error" you get with the default rollback journal.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_session():
    """FastAPI dependency that yields a session and always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create tables. Good enough before Alembic is introduced."""
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    # `python -m songcoach.db upgrade` — used by the Heroku release phase.
    cmd = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if cmd in {"upgrade", "init"}:
        init_db()
        print("Database schema created/updated.")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
