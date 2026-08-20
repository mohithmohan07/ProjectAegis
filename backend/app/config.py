import os
from pathlib import Path

from aegis_pipeline.openai_policy import (
    configured_context_window_tokens,
    configured_max_input_tokens,
    configured_max_output_tokens,
    configured_openai_model,
    provider_max_tokens_enabled,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", ROOT / "data"))
DB_URL = os.environ.get(
    # Keep the historical local-development location so checking out this
    # release never makes an existing database appear to disappear. Hosted
    # deployments set AEGIS_DB_URL explicitly to their persistent volume.
    "AEGIS_DB_URL", f"sqlite:///{ROOT / 'aegis.db'}")

# Authentication is deliberately local/offline by default so a developer can
# run Aegis without network access or identity-provider credentials. Hosted
# deployments opt into Google Identity explicitly.
AUTH_MODE = os.environ.get("AEGIS_AUTH_MODE", "local").strip().lower()
GOOGLE_CLIENT_ID = os.environ.get("AEGIS_GOOGLE_CLIENT_ID", "").strip()
ALLOWED_GOOGLE_DOMAIN = (
    os.environ.get("AEGIS_ALLOWED_GOOGLE_DOMAIN", "up.school")
    .strip()
    .lower()
)
# One-time, explicit upgrade bridge for rows created before owner identities
# existed. Only this verified Google email may claim ``local:default`` rows.
LEGACY_OWNER_EMAIL = (
    os.environ.get("AEGIS_LEGACY_OWNER_EMAIL", "").strip().lower()
)
ADMIN_PASSWORD = os.environ.get("AEGIS_ADMIN_PASSWORD", "admin")
SESSION_SECRET = os.environ.get("AEGIS_SESSION_SECRET", "")
SESSION_COOKIE_NAME = "aegis_session"
AUTH_CSRF_COOKIE_NAME = "aegis_auth_csrf"
SESSION_TTL_SECONDS = 12 * 60 * 60
_secure_cookie_setting = os.environ.get("AEGIS_SECURE_COOKIES", "").strip().lower()
SECURE_COOKIES = (
    _secure_cookie_setting not in {"0", "false", "no", "off"}
    if _secure_cookie_setting
    else AUTH_MODE == "google"
)
CORS_ORIGINS = [
    value.strip()
    for value in os.environ.get(
        "AEGIS_CORS_ORIGINS",
        os.environ.get(
            "AEGIS_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ),
    ).split(",")
    if value.strip()
]


# Canonical source assets created by the GPT PDF-to-ACSD fallback need stable
# public HTTPS URLs because Bulk Import rich text accepts only public images.
# Hosted deployments set this to their same-origin application URL.
PUBLIC_BASE_URL = os.environ.get("AEGIS_PUBLIC_BASE_URL", "").strip().rstrip("/")
SOURCE_ASSET_SECRET = (
    os.environ.get("AEGIS_SOURCE_ASSET_SECRET", "").strip()
    or SESSION_SECRET
    or ADMIN_PASSWORD
)

# The Bulk Import workbook IS the database — single source of truth.
BULK_IMPORT_DB = DATA_DIR / "bulk_import_database.xlsx"
# Every generation appends here (append-only, never overwritten).
BULK_IMPORT_OUTPUT = DATA_DIR / "bulk_import_output.xlsx"
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_UPLOAD_BYTES = max(
    1,
    int(os.environ.get("AEGIS_MAX_UPLOAD_BYTES", str(128 * 1024 * 1024))),
)
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
    """A usable model-provider credential is present (OpenAI or Gemini).

    Gemini rides the OpenAI-compatible endpoint through the same client, so
    for every "can we generate live?" question the two credentials are
    interchangeable. The name is kept for its long-standing call sites.
    """
    return bool(
        os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    )


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
MSG_WORKBOOKS = (
    "Live Create Workbooks is required (dry mode is disabled). "
    "Set OPENAI_API_KEY in your environment."
)


def use_live_generation() -> bool:
    """Live OpenAI generation when the key is present and live is not disabled."""
    return has_openai() and not _live_disabled()


def use_live_workbooks() -> bool:
    """Live revision-workbook pipeline.

    The pipeline used to need a second credential for PDF -> MMD conversion.
    That converter is gone, so a model-provider key is the whole requirement.
    """
    return use_live_generation()


def require_generation_live() -> None:
    if use_live_generation() or allow_dry():
        return
    raise LiveRequiredError(MSG_OPENAI)


def require_workbooks_live() -> None:
    if use_live_workbooks() or allow_dry():
        return
    raise LiveRequiredError(MSG_WORKBOOKS)


# OpenAI model for concept extraction and concept generation. The same model
# family the Create Workbooks pipeline is validated with. Provider-capacity mode
# is enabled by default: live calls receive the model's configured maximum output
# allowance, while oversized source is losslessly batched within its separate
# input limit.
OPENAI_MODEL = configured_openai_model()
OPENAI_PROVIDER_MAX_TOKENS = provider_max_tokens_enabled()
OPENAI_CONTEXT_WINDOW_TOKENS = configured_context_window_tokens(OPENAI_MODEL)
OPENAI_MAX_OUTPUT_TOKENS = configured_max_output_tokens(OPENAI_MODEL)
OPENAI_MAX_INPUT_TOKENS = configured_max_input_tokens(
    OPENAI_MODEL,
    output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
)
# Character-based capacity checks use this as a conservative upper boundary.
# Purpose-specific splitters may choose smaller packets to improve coverage;
# their concatenated source remains lossless.
OPENAI_MAX_INPUT_CHARS = OPENAI_MAX_INPUT_TOKENS

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
    1, int(os.environ.get("AEGIS_OPENAI_MAX_CONCURRENCY", "8")))
# Keep each provider request finite. The generation layer owns retries so it
# can report them to the live console and retain a resumable checkpoint.
OPENAI_REQUEST_TIMEOUT_SECONDS = max(
    1.0,
    float(os.environ.get("AEGIS_OPENAI_REQUEST_TIMEOUT_SECONDS", "600")),
)
# A queued request used to wait forever if an in-flight provider call never
# released its shared slot. A timeout turns that state into a clear,
# resumable failure instead of a permanently running job.
OPENAI_SLOT_WAIT_TIMEOUT_SECONDS = max(
    0.0,
    float(os.environ.get("AEGIS_OPENAI_SLOT_WAIT_TIMEOUT_SECONDS", "900")),
)
OPENAI_SLOT_WAIT_LOG_SECONDS = max(
    1.0,
    float(os.environ.get("AEGIS_OPENAI_SLOT_WAIT_LOG_SECONDS", "20")),
)


def phase3_decision_workers() -> int:
    """Per-run parallel decision workers for Settle topics / Host batches.

    Multi-tenant sizing rule: every concurrent generation run contributes up
    to this many contenders for the shared OPENAI_MAX_CONCURRENCY slots, so
    a deployment with N simultaneous creators should keep

        N x AEGIS_PHASE3_DECISION_WORKERS <= AEGIS_OPENAI_MAX_CONCURRENCY

    or slot queue waits grow toward AEGIS_OPENAI_SLOT_WAIT_TIMEOUT_SECONDS
    and can fail runs. The DEFAULT is 6 against the default gate of 8 —
    sized for the single-creator deployment this ships to, where a chapter
    run was latency-bound on sequential calls. Multi-creator deployments
    set both knobs together per the rule above (e.g. 3 creators: gate 9,
    workers 3). Set to 1 to restore strictly sequential decisions.
    """
    raw = os.environ.get("AEGIS_PHASE3_DECISION_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    return 6


def source_chunk_workers() -> int:
    """Per-run parallel workers for Phase 2 source chunks and packets.

    Governs the chunk fan-outs that read the source (question
    identification, the Question/Task Inventory, skeleton extraction) and
    the Phase 2.2 evidence-packet adjudication. Chunks are decided in
    parallel and APPLIED in input order, so output — including cross-chunk
    dedup and QID numbering — is byte-identical to a sequential run.

    Counts against the same shared OPENAI_MAX_CONCURRENCY gate as
    AEGIS_PHASE3_DECISION_WORKERS (the two never run at the same moment
    within one run, so they don't add up). Same multi-creator sizing rule:
    N simultaneous creators x workers <= the gate. Set to 1 for strictly
    sequential chunk reads.
    """
    raw = os.environ.get("AEGIS_SOURCE_CHUNK_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    return 4


OPENAI_TRANSIENT_RETRIES = max(
    0, int(os.environ.get("AEGIS_OPENAI_TRANSIENT_RETRIES", "10")))
OPENAI_BACKOFF_MAX_SECONDS = max(
    1.0, float(os.environ.get("AEGIS_OPENAI_BACKOFF_MAX_SECONDS", "90")))
