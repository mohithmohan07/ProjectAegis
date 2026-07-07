import os
import re
import json
import sys
import time
from pathlib import Path
from typing import Optional, List
import requests

# ---------------- CONFIG ----------------
SCRIPT_DIR = Path(__file__).resolve().parent
# Option A: Local PDF directories — all .pdf in each dir (and subdirs) are processed; .mmd written to OUTPUT_DIR
# When PRESERVE_STRUCTURE=True, outputs go to OUTPUT_BASE / <grade_folder_name> / <relative_path>.mmd
PDF_DIRS = [
    Path(r"C:\Users\FCI\OneDrive\Desktop\CBSE\Class 09"),
    Path(r"C:\Users\FCI\OneDrive\Desktop\CBSE\Class 10"),
]

# When using PDF_DIRS with PRESERVE_STRUCTURE: output base for grade folders (e.g. .../MMDs/CBSE)
OUTPUT_BASE = Path(r"C:\Users\FCI\OneDrive\Documents\CM\MMDs\CBSE")
PRESERVE_STRUCTURE = True  # Mirror input folder structure under OUTPUT_BASE/<grade_folder>/

# Option B: Explicit PDF paths (used if PDF_DIRS empty)
PDF_PATHS = [
    Path(r"C:\Users\FCI\Downloads\ICSE_SE_G09_CH18_Effects_of_Pollution.pdf"),
]
OUTPUT_DIR = SCRIPT_DIR / "mmds_single"

# Option C: Single Google Drive file (used if PDF_PATHS empty and GDRIVE_FOLDER_ID empty)
GDRIVE_FILE_ID = ""
GDRIVE_PDF_NAME = ""

# Option D: Google Drive folder — download all files, then convert every PDF to .mmd
# https://drive.google.com/drive/folders/1TrUTgQSCmC5fw8K6Q-dbHugc9GTZWnW7
GDRIVE_FOLDER_ID = ""

POLL_INTERVAL_SEC = 3.0
POLL_TIMEOUT_SEC = 300.0
SLEEP_BETWEEN_UPLOADS = 0.5
# ----------------------------------------

# --------- MATHPIX CREDENTIALS ----------
MATHPIX_APP_ID = os.getenv("MATHPIX_APP_ID")
MATHPIX_APP_KEY = os.getenv("MATHPIX_APP_KEY")

if not MATHPIX_APP_ID or not MATHPIX_APP_KEY:
    raise RuntimeError(
        "Missing Mathpix credentials. "
        "Set MATHPIX_APP_ID and MATHPIX_APP_KEY as environment variables."
    )

SESSION = requests.Session()
SESSION.headers.update({
    "app_id": MATHPIX_APP_ID,
    "app_key": MATHPIX_APP_KEY
})

MATHPIX_PDF_URL = "https://api.mathpix.com/v3/pdf"
# ----------------------------------------


def get_gdrive_file_id(url_or_id: str) -> str:
    """Extract Google Drive file ID from URL or return as-is if already an ID."""
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def get_gdrive_folder_id(url_or_id: str) -> str:
    """Extract Google Drive folder ID from URL or return as-is."""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def download_folder_from_gdrive(folder_id: str, output_dir: Path) -> None:
    """Download entire Google Drive folder using gdown. Requires: pip install gdown"""
    try:
        import gdown
    except ImportError:
        raise ImportError("Folder download requires gdown. Run: pip install gdown")
    folder_id = get_gdrive_folder_id(folder_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    gdown.download_folder(
        id=folder_id,
        output=str(output_dir),
        quiet=False,
        use_cookies=False,
    )


def download_pdf_from_gdrive(file_id: str, save_path: Path, session: requests.Session) -> None:
    """
    Download a PDF from Google Drive by file ID.
    Uses export link to avoid virus-scan page for large files when possible.
    """
    file_id = get_gdrive_file_id(file_id)
    # Direct download URL (may hit virus scan warning for large files)
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    r = session.get(url, timeout=60, stream=True)
    r.raise_for_status()

    # If response is HTML, we may have hit the virus scan warning; try to get confirm token
    content_type = r.headers.get("Content-Type", "")
    if "text/html" in content_type:
        # Token in form action or link (e.g. confirm=xxx or download_warning_xxx)
        match = re.search(r'confirm=([^"&\s]+)', r.text) or re.search(
            r'/download\?id=[^&]+&confirm=([^"&\s]+)', r.text
        )
        if match:
            token = match.group(1)
            url_confirm = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={token}"
            r = session.get(url_confirm, timeout=60, stream=True)
            r.raise_for_status()

    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def upload_pdf_to_mathpix(pdf_path: Path) -> str:
    """
    Upload a PDF to Mathpix and return pdf_id.
    NOTE: Do NOT request 'mmd' in conversion_formats.
    .mmd is Mathpix's native output and is always available.
    """
    with open(pdf_path, "rb") as f:
        files = {"file": f}
        options = {
            "math_inline_delimiters": ["$", "$"],
            "math_display_delimiters": ["$$", "$$"]
        }
        data = {"options_json": json.dumps(options)}

        r = SESSION.post(MATHPIX_PDF_URL, files=files, data=data, timeout=60)
        r.raise_for_status()
        resp = r.json()

    pdf_id = resp.get("pdf_id")
    if not pdf_id:
        raise RuntimeError(f"Mathpix upload failed: {resp}")

    return pdf_id


def poll_mathpix(pdf_id: str) -> None:
    """
    Poll Mathpix until processing is completed.
    """
    status_url = f"{MATHPIX_PDF_URL}/{pdf_id}"
    start = time.time()

    while True:
        r = SESSION.get(status_url, timeout=60)
        r.raise_for_status()
        resp = r.json()

        status = resp.get("status")
        if status == "completed":
            return
        if status in ("error", "failed"):
            raise RuntimeError(f"Mathpix processing failed: {resp}")

        if time.time() - start > POLL_TIMEOUT_SEC:
            raise TimeoutError(f"Mathpix timeout for pdf_id={pdf_id}")

        time.sleep(POLL_INTERVAL_SEC)


def download_mmd(pdf_id: str, out_path: Path) -> None:
    """
    Download the native Mathpix Markdown (.mmd).
    """
    mmd_url = f"{MATHPIX_PDF_URL}/{pdf_id}.mmd"
    r = SESSION.get(mmd_url, timeout=60)
    r.raise_for_status()
    out_path.write_bytes(r.content)


def main():
    # CLI: folders and/or individual .pdf files (files are processed in addition to any dirs)
    pdf_dirs: List[Path] = []
    pdf_files_cli: List[Path] = []
    output_base = OUTPUT_BASE
    if len(sys.argv) > 1:
        pdf_files_cli = [
            Path(p).resolve()
            for p in sys.argv[1:]
            if Path(p).is_file() and Path(p).suffix.lower() == ".pdf"
        ]
        pdf_dirs = [Path(p).resolve() for p in sys.argv[1:] if Path(p).is_dir()]
        if pdf_dirs:
            # Output to MMDs/ICSE when using ICSE paths, else MMDs/CBSE
            first_path = str(pdf_dirs[0])
            output_base = Path(r"C:\Users\FCI\OneDrive\Documents\CM\MMDs\ICSE") if "ICSE" in first_path else OUTPUT_BASE
    # Do not fall back to PDF_DIRS when CLI args were given (e.g. only .pdf files).
    if not pdf_dirs and len(sys.argv) <= 1:
        pdf_dirs = [d for d in PDF_DIRS] if PDF_DIRS else []

    # Create output directory if it doesn't exist
    if pdf_dirs and PRESERVE_STRUCTURE and output_base:
        output_base.mkdir(parents=True, exist_ok=True)
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build list of local PDF paths to process
    to_process = []
    if pdf_files_cli:
        to_process.extend(pdf_files_cli)
    if pdf_dirs:
        for d in pdf_dirs:
            if not d.is_dir():
                print(f"WARNING: PDF_DIR not found: {d}")
                continue
            # rglob: include PDFs in subfolders (e.g. CBSE_RD_G09_Mathematics/CH01_folder/file.pdf)
            to_process.extend(sorted(d.rglob("*.pdf")))
    if to_process:
        to_process = sorted(set(to_process))
    elif PDF_PATHS:
        for p in PDF_PATHS:
            to_process.append(Path(p))
    elif GDRIVE_FOLDER_ID:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Check if folder already downloaded (has PDFs)
        existing_pdfs = list(OUTPUT_DIR.glob("*.pdf"))
        if not existing_pdfs:
            print(f"DOWNLOAD: Google Drive folder -> {OUTPUT_DIR}")
            download_folder_from_gdrive(GDRIVE_FOLDER_ID, OUTPUT_DIR)
        else:
            print(f"EXISTS: {len(existing_pdfs)} PDF(s) in {OUTPUT_DIR.name} (using existing)")
        to_process = sorted(OUTPUT_DIR.glob("*.pdf"))
    elif GDRIVE_FILE_ID and GDRIVE_PDF_NAME:
        pdf_path = OUTPUT_DIR / GDRIVE_PDF_NAME
        if not pdf_path.exists():
            print(f"DOWNLOAD: Google Drive -> {pdf_path.name}")
            download_pdf_from_gdrive(GDRIVE_FILE_ID, pdf_path, SESSION)
            print(f"  Saved -> {pdf_path}")
        else:
            print(f"EXISTS: {pdf_path.name} (using existing)")
        to_process.append(pdf_path)
    else:
        print("Nothing to process: set PDF_DIRS, PDF_PATHS, GDRIVE_FOLDER_ID, or GDRIVE_FILE_ID + GDRIVE_PDF_NAME")
        return

    # When pdf_dirs + PRESERVE_STRUCTURE: output to output_base/<grade_folder>/<relative_path>.mmd
    # Otherwise: flat output to OUTPUT_DIR (legacy behavior)
    pdf_dirs_set = set(pdf_dirs) if pdf_dirs else set()
    use_preserve = bool(pdf_dirs and PRESERVE_STRUCTURE and output_base)

    def _pdf_dir_for(p: Path) -> Optional[Path]:
        """Return the PDF_DIR that contains p, or None."""
        for d in pdf_dirs:
            try:
                p.relative_to(d)
                return d
            except ValueError:
                continue
        return None

    # Rename migration only when NOT using preserve structure
    if not use_preserve:
        for pdf_path in to_process:
            if not pdf_path.exists():
                continue
            if pdf_dirs_set and pdf_path.parent not in pdf_dirs_set:
                old_mmd = OUTPUT_DIR / f"{pdf_path.stem}.mmd"
                new_mmd = OUTPUT_DIR / f"{pdf_path.parent.name}.mmd"
                if old_mmd.exists() and old_mmd != new_mmd and not new_mmd.exists():
                    old_mmd.rename(new_mmd)
                    print(f"RENAMED: {old_mmd.name} -> {new_mmd.name}")

    for pdf_path in to_process:
        if not pdf_path.exists():
            print(f"ERROR: PDF not found: {pdf_path}")
            continue

        if use_preserve:
            pdf_dir = _pdf_dir_for(pdf_path)
            if pdf_dir is not None:
                rel = pdf_path.relative_to(pdf_dir)
                mmd_path = output_base / pdf_dir.name / rel.with_suffix(".mmd")
            else:
                mmd_path = OUTPUT_DIR / f"{pdf_path.stem}.mmd"
        elif pdf_dirs_set and pdf_path.parent not in pdf_dirs_set:
            mmd_path = OUTPUT_DIR / f"{pdf_path.parent.name}.mmd"
        else:
            mmd_path = OUTPUT_DIR / f"{pdf_path.stem}.mmd"

        if mmd_path.exists():
            print(f"EXISTS: {mmd_path.name}")
            continue

        mmd_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            print(f"UPLOAD: {pdf_path.name}")
            pdf_id = upload_pdf_to_mathpix(pdf_path)
            print(f"  pdf_id={pdf_id}")

            print(f"  Polling for completion...")
            poll_mathpix(pdf_id)

            print(f"  Downloading mmd...")
            download_mmd(pdf_id, mmd_path)

            print(f"  DONE -> {mmd_path.name}")

            time.sleep(SLEEP_BETWEEN_UPLOADS)

        except Exception as e:
            print(f"ERROR processing {pdf_path.name}: {e}")
            continue

    print("\nDONE: Mathpix extraction complete.")
    print(f"Output directory: {output_base if (pdf_dirs and PRESERVE_STRUCTURE and output_base) else OUTPUT_DIR}")


if __name__ == "__main__":
    main()
