"""Mathpix PDF → MMD conversion with on-disk caching.

The Mathpix Convert API turns a PDF into MMD (Mathpix Markdown), which is a
much richer source for the GPT pipeline than plain PyMuPDF text — equations,
tables, sub-headings, and math are preserved.

Flow:
  1. POST the PDF to /v3/pdf with conversion_formats={"mmd": true}
  2. Poll /v3/pdf/{pdf_id} until status == "completed"
  3. GET /v3/pdf/{pdf_id}.mmd to download the MMD text
  4. Cache the result keyed by source PDF sha256 so we don't re-bill on reruns.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import urllib.request
import urllib.error


MATHPIX_BASE = "https://api.mathpix.com/v3"


class MathpixError(RuntimeError):
    pass


class MathpixClient:
    def __init__(self, app_id: str | None = None, app_key: str | None = None,
                 cache_dir: str | Path = "mmd_cache", timeout: int = 600) -> None:
        self.app_id = app_id or os.getenv("MATHPIX_APP_ID")
        self.app_key = app_key or os.getenv("MATHPIX_APP_KEY")
        if not self.app_id or not self.app_key:
            raise MathpixError(
                "Mathpix credentials missing. Set MATHPIX_APP_ID and MATHPIX_APP_KEY."
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    # ---- public API -----------------------------------------------------

    def convert_to_mmd(self, pdf_path: str | Path, force: bool = False) -> str:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        cache_key = self._cache_key(pdf_path)
        cache_file = self.cache_dir / f"{cache_key}.mmd"
        meta_file = self.cache_dir / f"{cache_key}.meta.json"

        if cache_file.exists() and not force:
            return cache_file.read_text(encoding="utf-8")

        pdf_id = self._upload(pdf_path)
        self._wait_until_done(pdf_id)
        mmd_text = self._download_mmd(pdf_id)

        cache_file.write_text(mmd_text, encoding="utf-8")
        meta_file.write_text(
            json.dumps({"pdf_id": pdf_id, "source": str(pdf_path), "ts": time.time()}, indent=2),
            encoding="utf-8",
        )
        return mmd_text

    # ---- internals ------------------------------------------------------

    def _cache_key(self, pdf_path: Path) -> str:
        h = hashlib.sha256()
        h.update(pdf_path.name.encode("utf-8"))
        h.update(pdf_path.read_bytes())
        return h.hexdigest()[:24]

    def _headers(self) -> dict:
        return {"app_id": self.app_id, "app_key": self.app_key}

    def _upload(self, pdf_path: Path) -> str:
        url = f"{MATHPIX_BASE}/pdf"
        boundary = "----workbookboundary" + os.urandom(8).hex()
        # Mathpix already returns MMD by default; conversion_formats is for
        # *additional* output formats only.
        options = json.dumps(
            {
                "math_inline_delimiters": ["$", "$"],
                "math_display_delimiters": ["$$", "$$"],
                "rm_spaces": True,
            }
        )
        # Manual multipart so we don't add a dependency.
        body_parts = []
        body_parts.append(f"--{boundary}\r\n".encode())
        body_parts.append(b'Content-Disposition: form-data; name="options_json"\r\n\r\n')
        body_parts.append(options.encode("utf-8") + b"\r\n")
        body_parts.append(f"--{boundary}\r\n".encode())
        body_parts.append(
            f'Content-Disposition: form-data; name="file"; filename="{pdf_path.name}"\r\n'.encode()
        )
        body_parts.append(b"Content-Type: application/pdf\r\n\r\n")
        body_parts.append(pdf_path.read_bytes())
        body_parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(body_parts)

        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        for k, v in self._headers().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            raise MathpixError(f"Upload failed ({exc.code}): {detail}") from exc
        pdf_id = payload.get("pdf_id") or payload.get("request_id")
        if not pdf_id:
            raise MathpixError(f"Unexpected upload response: {payload}")
        return pdf_id

    def _wait_until_done(self, pdf_id: str) -> None:
        url = f"{MATHPIX_BASE}/pdf/{pdf_id}"
        deadline = time.time() + self.timeout
        delay = 1.0
        while time.time() < deadline:
            req = urllib.request.Request(url)
            for k, v in self._headers().items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise MathpixError(f"Poll failed ({exc.code})") from exc
            status = payload.get("status")
            if status == "completed":
                return
            if status in ("error", "failed"):
                raise MathpixError(f"Conversion failed: {payload}")
            time.sleep(delay)
            delay = min(delay * 1.4, 8)
        raise MathpixError(f"Timed out waiting for pdf_id={pdf_id}")

    def _download_mmd(self, pdf_id: str) -> str:
        url = f"{MATHPIX_BASE}/pdf/{pdf_id}.mmd"
        req = urllib.request.Request(url)
        for k, v in self._headers().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise MathpixError(f"MMD download failed ({exc.code})") from exc
