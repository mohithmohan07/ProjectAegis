"""
import re
import time
import json
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://www.cleariitmedical.com/p/icse-class-9-maths-selina-solutions.html"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

# Captures common Drive patterns seen in embeds/links
DRIVE_ID_PATTERNS = [
    re.compile(r"drive\.google\.com/uc\?[^#]*id=([a-zA-Z0-9_-]{10,})"),
    re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})"),
    re.compile(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]{10,})"),
    re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]{10,})"),
    re.compile(r"docs\.google\.com/presentation/d/([a-zA-Z0-9_-]{10,})"),
]

def fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def extract_chapter_links(index_html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(index_html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ", strip=True) or "").lower()
        href = a["href"].strip()

        # Heuristic: pick the chapter list items
        if "chapter" in text and ("ml" in index_html.lower() or "selina" in index_html.lower()):
            full = urljoin(base_url, href)
            links.append({"title": a.get_text(" ", strip=True), "url": full})

    # De-dupe by URL
    seen = set()
    uniq = []
    for x in links:
        if x["url"] not in seen:
            seen.add(x["url"])
            uniq.append(x)
    return uniq

def extract_drive_ids(html: str) -> list[str]:
    ids = set()
    for pat in DRIVE_ID_PATTERNS:
        for m in pat.findall(html):
            ids.add(m)
    return sorted(ids)

def main():
    index_html = fetch(INDEX_URL)
    chapters = extract_chapter_links(index_html, INDEX_URL)

    out = []
    for ch in chapters:
        try:
            html = fetch(ch["url"])
            drive_ids = extract_drive_ids(html)

            out.append({
                "chapter_title": ch["title"],
                "chapter_url": ch["url"],
                "drive_ids": drive_ids,
            })

            # Use plain ASCII so it prints correctly on Windows consoles
            print(f"OK {ch['title']} | drive_ids={len(drive_ids)}")
            time.sleep(1.0)  # be polite

        except Exception as e:
            # Use plain ASCII so it prints correctly on Windows consoles
            print(f"FAILED {ch['url']}: {e}")
            out.append({
                "chapter_title": ch["title"],
                "chapter_url": ch["url"],
                "error": repr(e),
                "drive_ids": [],
            })

    with open("cleariitmedical_selina9_sources.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\nSaved: cleariitmedical_selina9_sources.json")

if __name__ == "__main__":
    main() 
"""

import json
import time
import requests
from pathlib import Path

# ---------------- CONFIG ----------------
INPUT_JSON = "CBSE_RD_G09_Math_SourceFiles.json"  # change as needed
OUTPUT_JSON = INPUT_JSON  # overwrite safely
BASE_DIR = Path("data")
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_DOWNLOADS = 1.0
# ----------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
})

def drive_download_url(drive_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={drive_id}"

def download_pdf(drive_id: str, out_path: Path) -> None:
    url = drive_download_url(drive_id)
    with SESSION.get(url, stream=True, timeout=REQUEST_TIMEOUT) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        chapters = json.load(f)

    for ch in chapters:
        if ch.get("status") != "PENDING":
            print(f"SKIP {ch['chapter_code']} (status={ch['status']})")
            continue

        chapter_code = ch["chapter_code"]
        chapter_dir = BASE_DIR / chapter_code
        chapter_dir.mkdir(parents=True, exist_ok=True)

        local_paths = []

        try:
            for idx, drive_id in enumerate(ch.get("drive_ids", []), start=1):
                pdf_name = f"{chapter_code}_{idx:02d}.pdf"
                pdf_path = chapter_dir / pdf_name

                if pdf_path.exists():
                    print(f"EXISTS {pdf_name}")
                else:
                    print(f"DOWNLOADING {pdf_name}")
                    download_pdf(drive_id, pdf_path)
                    time.sleep(SLEEP_BETWEEN_DOWNLOADS)

                local_paths.append(str(pdf_path))

            ch["local_pdf_paths"] = local_paths
            ch["status"] = "PDF_DOWNLOADED"

        except Exception as e:
            ch["status"] = "ERROR"
            ch["notes"] = f"PDF download failed: {repr(e)}"
            print(f"ERROR {chapter_code}: {e}")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(chapters, f, ensure_ascii=False, indent=2)

    print("\nDONE: PDF extraction complete")

if __name__ == "__main__":
    main()