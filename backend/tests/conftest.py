import os

# Tests are always deterministic dry-mode, even when live API keys are present
# in the environment (live is default-on when keys exist).
os.environ["AEGIS_ALLOW_DRY"] = "1"
os.environ["AEGIS_USE_LIVE"] = "0"

import pytest
from fastapi.testclient import TestClient

from app import config
from app.bulk_import import reader
from app.db import Base, SessionLocal, engine, init_db
from app.main import app, bootstrap


def _load_test_fixtures() -> None:
    if not config.BULK_IMPORT_DB.exists():
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "scripts/generate_dummy_data.py"],
            check=True,
            cwd=str(config.ROOT),
        )
    db = SessionLocal()
    try:
        reader.import_workbook(db, config.BULK_IMPORT_DB)
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _prepare():
    Base.metadata.drop_all(bind=engine)
    init_db()
    # Fresh output workbook per test session.
    config.BULK_IMPORT_OUTPUT.unlink(missing_ok=True)
    _load_test_fixtures()
    yield
    Base.metadata.drop_all(bind=engine)
    config.BULK_IMPORT_OUTPUT.unlink(missing_ok=True)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def first_chapter(client):
    tree = client.get("/directory/tree").json()
    return tree[0]["grades"][0]["subjects"][0]["units"][0]["chapters"][0]


@pytest.fixture()
def first_concept(client, first_chapter):
    detail = client.get(f"/directory/chapters/{first_chapter['id']}").json()
    return detail["topics"][0]["concepts"][0]
