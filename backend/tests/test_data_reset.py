"""Data reset for a fresh start."""
import subprocess
import sys

from app import config, models
from app.bulk_import import reader
from tests.conftest import _load_test_fixtures


def _ensure_seed():
    if not config.BULK_IMPORT_DB.exists():
        subprocess.run(
            [sys.executable, "scripts/generate_dummy_data.py"],
            check=True,
            cwd=str(config.ROOT),
        )


def test_reset_clears_database(client, db):
    _ensure_seed()
    if db.query(models.Chapter).count() == 0:
        reader.import_workbook(db, config.BULK_IMPORT_DB)
    assert db.query(models.Chapter).count() > 0

    r = client.post("/data/reset")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reset"
    assert body["chapters"] == 0

    db.expire_all()
    assert db.query(models.Chapter).count() == 0
    assert db.query(models.Question).count() == 0

    tree = client.get("/directory/tree").json()
    assert tree == [] or all(not g.get("grades") for g in tree)

    # Restore shared session state for downstream tests in this run.
    _load_test_fixtures()
