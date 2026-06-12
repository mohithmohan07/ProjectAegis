from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# OUTPUT_DIR is for SAMPLES only (e.g. `--sample`). Real workbook runs publish to
# PUBLISH_ROOT in a grade/subject tree (see default_output_path below).
OUTPUT_DIR = ROOT / "output"
MMD_CACHE_DIR = ROOT / "mmd_cache"
PLAN_CACHE_DIR = ROOT / "plan_cache"
for d in (OUTPUT_DIR, MMD_CACHE_DIR, PLAN_CACHE_DIR):
    d.mkdir(exist_ok=True, parents=True)

# Real outputs land here, organised as <PUBLISH_ROOT>/Class NN/<Subject>/<stem>.pdf.
# This is a separate tree from the NCERT sources (Class 08 / Class 09 under Books).
PUBLISH_ROOT = Path(r"C:\Users\FCI\OneDrive\Desktop\Books\Workbooks")


def _ensure_env(name: str) -> str | None:
    """Return the env var, falling back to the Windows User scope if missing.

    The PowerShell session that spawned us may have been created *before* the
    user added these vars to their User profile. We pull them via a one-shot
    `[Environment]::GetEnvironmentVariable` call so the Python process can see
    them.
    """
    value = os.getenv(name)
    if value:
        return value
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"[Environment]::GetEnvironmentVariable('{name}','User')",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = result.stdout.strip()
        if value:
            os.environ[name] = value
            return value
    except Exception:
        return None
    return None


# Eagerly pull credentials from the User scope on Windows so downstream code
# can just use os.getenv.
for _name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "MATHPIX_APP_ID", "MATHPIX_APP_KEY"):
    _ensure_env(_name)


DEFAULT_CONFIG: dict = {
    "openai_model": "gpt-5.4-mini-2026-03-17",
    "openai_api_key_env": "OPENAI_API_KEY",
    "openai_base_url": os.getenv("OPENAI_BASE_URL"),
    "mathpix_app_id_env": "MATHPIX_APP_ID",
    "mathpix_app_key_env": "MATHPIX_APP_KEY",
    "margin_mm": 18,
    "content_width_mm": 174,
    "min_topics": 6,
    "max_topics": 16,
    "min_glossary": 20,
    "max_glossary": 32,
    "mmd_cache_dir": str(MMD_CACHE_DIR),
    "plan_cache_dir": str(PLAN_CACHE_DIR),
}


import re as _re


def _grade_folder(grade: str) -> str:
    """'Grade 9' / '9' / 'Class 09' -> 'Class 09'."""
    m = _re.search(r"(\d{1,2})", str(grade or ""))
    return f"Class {int(m.group(1)):02d}" if m else "Class 00"


def default_output_path(cfg: dict, *, sample: bool = False) -> Path:
    """Where a generated workbook PDF should be written.

    - Real runs  -> PUBLISH_ROOT / 'Class NN' / Subject / <source-stem>.pdf
    - Samples    -> OUTPUT_DIR / <source-stem>.pdf  (workbook/output)

    The source-stem keeps the NCERT filename (e.g. CBSE_NCERT_G09_CH01_...),
    matching the published library naming.
    """
    source = cfg.get("source_pdf")
    if source:
        stem = Path(source).stem
    else:
        safe = "".join(c if c.isalnum() else "_" for c in cfg.get("chapter_title", "")).strip("_")
        stem = f"CBSE_workbook_{safe}" or "workbook"
    if sample:
        return OUTPUT_DIR / f"{stem}.pdf"
    subject = str(cfg.get("subject", "")).strip() or "Unsorted"
    return PUBLISH_ROOT / _grade_folder(cfg.get("grade", "")) / subject / f"{stem}.pdf"
