"""Wipe all Aegis data for a fresh start."""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from .. import config, models
from ..db import Base, engine, init_db


def _clear_dir(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def reset_all(*, keep_seed: bool = False, db: Session | None = None) -> dict:
    """Drop the normalized DB and remove generated files.

    keep_seed=False (default) also removes bulk_import_database.xlsx so bootstrap
    does not reload demo content on restart.
    """
    if db is not None:
        db.close()

    Base.metadata.drop_all(bind=engine)
    init_db()

    removed: list[str] = []
    if config.BULK_IMPORT_OUTPUT.exists():
        config.BULK_IMPORT_OUTPUT.unlink()
        removed.append(str(config.BULK_IMPORT_OUTPUT.name))

    if not keep_seed and config.BULK_IMPORT_DB.exists():
        config.BULK_IMPORT_DB.unlink()
        removed.append(str(config.BULK_IMPORT_DB.name))

    from ..services.workbooks import WORKBOOK_ROOT

    _clear_dir(config.UPLOAD_DIR)
    _clear_dir(WORKBOOK_ROOT)
    WORKBOOK_ROOT.mkdir(parents=True, exist_ok=True)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    return {
        "status": "reset",
        "keep_seed": keep_seed,
        "removed_files": removed,
        "chapters": 0,
        "questions": 0,
    }


def stats(db: Session) -> dict:
    return {
        "chapters": db.query(models.Chapter).count(),
        "questions": db.query(models.Question).count(),
        "upload_jobs": db.query(models.UploadJob).count(),
        "output_workbook": config.BULK_IMPORT_OUTPUT.exists(),
        "seed_workbook": config.BULK_IMPORT_DB.exists(),
    }
