import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DB = ROOT / ".test-storage" / "pytest.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    leftover = Path(str(TEST_DB) + suffix)
    if leftover.exists():
        leftover.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["STORAGE_DIR"] = str(ROOT / ".test-storage" / "uploads")
os.environ["WORKER_POLL_SECONDS"] = "0.05"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["WORKER_ENABLED"] = "true"

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.database import engine


@pytest.fixture(scope="session", autouse=True)
def migrated():
    from backend_samples import rebuild

    rebuild()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "head")
    yield


@pytest.fixture(scope="session")
def client(migrated):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_tables(migrated):
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM uds_lines"))
        connection.execute(text("DELETE FROM uds_issues"))
        connection.execute(text("DELETE FROM uds_links"))
        connection.execute(text("DELETE FROM uds_resolutions"))
        connection.execute(text("DELETE FROM uds_observations"))
        connection.execute(text("DELETE FROM mapping_entries"))
        connection.execute(text("DELETE FROM mapping_versions"))
        connection.execute(text("DELETE FROM import_rows"))
        connection.execute(text("DELETE FROM imports"))
    yield
