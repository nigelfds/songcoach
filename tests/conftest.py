"""Test harness: redirect DB + storage to a throwaway location, before songcoach imports read them."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="songcoach-tests-")
# os.environ wins over the repo .env in pydantic-settings' precedence order.
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["LOCAL_STORAGE_DIR"] = f"{_TMP}/data"

import pytest

from songcoach.config import settings
from songcoach.db import Base, SessionLocal, engine, init_db


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    """A per-test data dir; functions read settings.local_storage_dir dynamically."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(settings, "local_storage_dir", d)
    return d


@pytest.fixture
def db():
    """A session over freshly recreated tables."""
    Base.metadata.drop_all(bind=engine)
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
