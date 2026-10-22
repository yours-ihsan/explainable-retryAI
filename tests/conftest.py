"""Shared pytest fixtures.

Each test gets its own temporary SQLite file, so tests never pollute the real
`retry_history.db` and never depend on each other.
"""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh temporary database."""
    monkeypatch.setenv("RETRY_DB_PATH", str(tmp_path / "test_history.db"))
    # `with TestClient(app)` runs the startup hook, which creates the table.
    with TestClient(app) as test_client:
        yield test_client
