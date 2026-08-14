"""Shared fixtures. See docs/12-testing-strategy.md §12.2.

Points DATABASE_URL at a throwaway `deploymint_test` database on the same
Postgres instance the dev container already runs — never a second auto-
provisioned server per test run."""

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolate the sandbox root AND the database so tests never touch real data."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("DEPLOYMINT_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv(
        "DATABASE_URL",
        os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+psycopg://deploymint:deploymint@localhost:5433/deploymint_test",
        ),
    )

    from deploymint.config import get_settings

    get_settings.cache_clear()

    import deploymint.db.database as dbmod
    from deploymint.db.models import Base

    dbmod.reset_engine_cache()
    engine = dbmod.get_engine()
    # Full isolation per test, not just per test-run: drop and recreate every
    # table so rows from an earlier test in the same session can't leak in.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield ws

    get_settings.cache_clear()
    dbmod.reset_engine_cache()


@pytest.fixture
def client(workspace):
    from deploymint.server import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sample_repo(workspace):
    """A copy of the FastAPI fixture, placed UNDER the isolated workspace root —
    the sandbox rejects anything else."""
    dst = workspace / "sample_fastapi"
    shutil.copytree(FIXTURES / "sample_fastapi", dst)
    return dst


@pytest.fixture
def registered_project(client, sample_repo):
    r = client.post("/api/projects", json={"name": "test-app", "repo_path": str(sample_repo)})
    assert r.status_code == 201
    return r.json()
