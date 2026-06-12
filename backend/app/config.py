import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", ROOT / "data"))
DB_URL = os.environ.get("AEGIS_DB_URL", f"sqlite:///{ROOT / 'aegis.db'}")

# The Bulk Import workbook IS the database — single source of truth.
BULK_IMPORT_DB = DATA_DIR / "bulk_import_database.xlsx"
# Every generation appends here (append-only, never overwritten).
BULK_IMPORT_OUTPUT = DATA_DIR / "bulk_import_output.xlsx"
UPLOAD_DIR = DATA_DIR / "uploads"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def has_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def has_mathpix() -> bool:
    return bool(os.environ.get("MATHPIX_APP_ID") and os.environ.get("MATHPIX_APP_KEY"))


def _live_disabled() -> bool:
    """AEGIS_USE_LIVE=0/false/off explicitly forces dry mode."""
    return os.environ.get("AEGIS_USE_LIVE", "").strip().lower() in {"0", "false", "no", "off"}


def use_live_generation() -> bool:
    """Live OpenAI generation: ON by default whenever the key is present."""
    return has_openai() and not _live_disabled()


def use_live_mmd() -> bool:
    """Live Mathpix MMD conversion: ON by default whenever keys are present."""
    return has_mathpix() and not _live_disabled()


# OpenAI model for concept extraction / pre-learning derivation. The same
# model family the Create Workbooks pipeline is validated with.
OPENAI_MODEL = os.environ.get("AEGIS_OPENAI_MODEL", "gpt-5.4-mini-2026-03-17")
