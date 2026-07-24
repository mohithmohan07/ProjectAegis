import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", ROOT / "data"))
DB_URL = os.environ.get(
    # Keep the historical local-development location so checking out this
    # release never makes an existing database appear to disappear. Hosted
    # deployments set AEGIS_DB_URL explicitly to their persistent volume.
    "AEGIS_DB_URL", f"sqlite:///{ROOT / 'aegis.db'}")

# The Bulk Import workbook IS the database — single source of truth.
BULK_IMPORT_DB = DATA_DIR / "bulk_import_database.xlsx"
# Every generation appends here (append-only, never overwritten).
BULK_IMPORT_OUTPUT = DATA_DIR / "bulk_import_output.xlsx"
UPLOAD_DIR = DATA_DIR / "uploads"
# Bundled syllabus workbooks committed in git (shipped in the Docker image).
BUNDLED_SYLLABUS_DIR = Path(
    os.environ.get("AEGIS_BUNDLED_SYLLABUS_DIR", ROOT / "data" / "syllabus"),
)
# Runtime syllabus dir (user uploads + Fly volume); overrides bundled on name clash.
SYLLABUS_DIR = DATA_DIR / "syllabus"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SYLLABUS_DIR.mkdir(parents=True, exist_ok=True)
BUNDLED_SYLLABUS_DIR.mkdir(parents=True, exist_ok=True)


def syllabus_workbook_dirs() -> list[Path]:
    """Directories to scan for syllabus .xlsx files (bundled first, then runtime)."""
    dirs: list[Path] = []
    if BUNDLED_SYLLABUS_DIR.is_dir():
        dirs.append(BUNDLED_SYLLABUS_DIR)
    if SYLLABUS_DIR.is_dir() and SYLLABUS_DIR.resolve() != BUNDLED_SYLLABUS_DIR.resolve():
        dirs.append(SYLLABUS_DIR)
    return dirs


def has_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def has_mathpix() -> bool:
    return bool(os.environ.get("MATHPIX_APP_ID") and os.environ.get("MATHPIX_APP_KEY"))


def allow_dry() -> bool:
    """Dry/stub generation is opt-in (tests/CI only). Production runs live-only."""
    return os.environ.get("AEGIS_ALLOW_DRY", "").strip().lower() in {"1", "true", "yes", "on"}


def _live_disabled() -> bool:
    """AEGIS_USE_LIVE=0/false/off explicitly forces dry mode (tests only)."""
    return os.environ.get("AEGIS_USE_LIVE", "").strip().lower() in {"0", "false", "no", "off"}


class LiveRequiredError(ValueError):
    """Raised when live APIs are required but credentials are missing."""


MSG_OPENAI = (
    "Live OpenAI generation is required (dry mode is disabled). "
    "Set OPENAI_API_KEY in your environment."
)
MSG_MATHPIX = (
    "Live Mathpix conversion is required (dry mode is disabled). "
    "Set MATHPIX_APP_ID and MATHPIX_APP_KEY in your environment."
)
MSG_WORKBOOKS = (
    "Live Create Workbooks is required (dry mode is disabled). "
    "Set OPENAI_API_KEY, MATHPIX_APP_ID, and MATHPIX_APP_KEY."
)


def use_live_generation() -> bool:
    """Live OpenAI generation when the key is present and live is not disabled."""
    return has_openai() and not _live_disabled()


def use_live_mmd() -> bool:
    """Live Mathpix MMD conversion when keys are present and live is not disabled."""
    return has_mathpix() and not _live_disabled()


def use_live_workbooks() -> bool:
    """Live revision-workbook pipeline (OpenAI + Mathpix)."""
    return use_live_generation() and use_live_mmd()


def require_generation_live() -> None:
    if use_live_generation() or allow_dry():
        return
    raise LiveRequiredError(MSG_OPENAI)


def require_mmd_live(*, pdf_or_image: bool = False) -> None:
    if not pdf_or_image or use_live_mmd() or allow_dry():
        return
    raise LiveRequiredError(MSG_MATHPIX)


def require_workbooks_live() -> None:
    if use_live_workbooks() or allow_dry():
        return
    raise LiveRequiredError(MSG_WORKBOOKS)


# OpenAI model for concept extraction / pre-learning derivation. The same
# model family the Create Workbooks pipeline is validated with.
OPENAI_MODEL = os.environ.get(
    "AEGIS_OPENAI_MODEL", "gpt-5.4-mini-2026-03-17"
)
# Large concept-map passes prefer complete JSON over speed or token economy;
# keep the default within current model completion limits and allow env override.
OPENAI_MAX_OUTPUT_TOKENS = int(
    os.environ.get("AEGIS_OPENAI_MAX_OUTPUT_TOKENS", "128000")
)

# ---- Multi-user safety ------------------------------------------------------
# All users share one OPENAI_API_KEY, so concurrent generation runs compete for
# the same rate/token budget. Two protections keep output quality unaffected:
#
#  1. A process-wide cap on in-flight OpenAI calls. Extra calls WAIT for a free
#     slot instead of stampeding the API into 429s. Under load jobs get slower,
#     never lower-quality.
#  2. Patient retries for transient API errors (rate limits, timeouts, 5xx)
#     with exponential backoff, honouring the server's Retry-After when given.
#     A job only fails after the API has been unavailable for several minutes.
OPENAI_MAX_CONCURRENCY = max(
    1, int(os.environ.get("AEGIS_OPENAI_MAX_CONCURRENCY", "3")))
OPENAI_TRANSIENT_RETRIES = max(
    0, int(os.environ.get("AEGIS_OPENAI_TRANSIENT_RETRIES", "10")))
OPENAI_BACKOFF_MAX_SECONDS = max(
    1.0, float(os.environ.get("AEGIS_OPENAI_BACKOFF_MAX_SECONDS", "90")))
