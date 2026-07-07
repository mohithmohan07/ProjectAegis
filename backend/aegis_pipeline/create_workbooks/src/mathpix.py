"""Mathpix PDF → MMD conversion with on-disk caching.

The Mathpix Convert API turns a PDF into MMD (Mathpix Markdown), which is a
much richer source for the GPT pipeline than plain PyMuPDF text — equations,
tables, sub-headings, and math are preserved.

Flow:
  1. POST the PDF to /v3/pdf (optionally request mmd.zip for embedded images)
  2. Poll /v3/pdf/{pdf_id} until status == "completed"
  3. GET /v3/pdf/{pdf_id}.mmd and /v3/pdf/{pdf_id}.lines.json
  4. Inline bare Fig./Figure references with Mathpix crop URLs (via mmd_figures)
  5. Cache the enriched result keyed by source PDF sha256.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import urllib.request
import urllib.error


MATHPIX_BASE = "https://api.mathpix.com/v3"
_CDN_URL_RE = re.compile(r"https://cdn\.mathpix\.com/cropped/[^\s\)\}\"']+")


class MathpixError(RuntimeError):
    pass


class MathpixClient:
    def __init__(
        self,
        app_id: str | None = None,
        app_key: str | None = None,
        cache_dir: str | Path = "mmd_cache",
        timeout: int = 600,
        *,
        embed_images: bool = True,
        inline_figures: bool = True,
    ) -> None:
        self.app_id = app_id or os.getenv("MATHPIX_APP_ID")
        self.app_key = app_key or os.getenv("MATHPIX_APP_KEY")
        if not self.app_id or not self.app_key:
            raise MathpixError(
                "Mathpix credentials missing. Set MATHPIX_APP_ID and MATHPIX_APP_KEY."
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.embed_images = embed_images
        self.inline_figures = inline_figures

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
        if self.embed_images:
            self._wait_for_zip(pdf_id, "mmd.zip")
        mmd_text = self._download_mmd(pdf_id)
        lines_data = self._download_lines_json(pdf_id)
        if lines_data is not None:
            lines_file = self.cache_dir / f"{cache_key}.lines.json"
            lines_file.write_text(json.dumps(lines_data), encoding="utf-8")

        if self.inline_figures and lines_data is not None:
            mmd_text = self._enrich_with_figures(mmd_text, lines_data)

        if self.embed_images:
            mmd_text = self._localize_images_from_zip(
                pdf_id, cache_key, mmd_text,
            )

        cache_file.write_text(mmd_text, encoding="utf-8")
        meta_file.write_text(
            json.dumps(
                {
                    "pdf_id": pdf_id,
                    "source": str(pdf_path),
                    "ts": time.time(),
                    "inline_figures": self.inline_figures,
                    "embed_images": self.embed_images,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return mmd_text

    def load_cached_lines_json(self, pdf_path: str | Path) -> dict | None:
        """Return cached lines.json for a PDF if present."""
        pdf_path = Path(pdf_path)
        cache_key = self._cache_key(pdf_path)
        lines_file = self.cache_dir / f"{cache_key}.lines.json"
        if not lines_file.exists():
            return None
        return json.loads(lines_file.read_text(encoding="utf-8"))

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
        options: dict = {
            "math_inline_delimiters": ["$", "$"],
            "math_display_delimiters": ["$$", "$$"],
            "rm_spaces": True,
        }
        if self.embed_images:
            options["conversion_formats"] = {"mmd.zip": True}
        options_json = json.dumps(options)
        body_parts = []
        body_parts.append(f"--{boundary}\r\n".encode())
        body_parts.append(b'Content-Disposition: form-data; name="options_json"\r\n\r\n')
        body_parts.append(options_json.encode("utf-8") + b"\r\n")
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

    def _wait_for_zip(self, pdf_id: str, fmt: str) -> None:
        """Wait for an auxiliary conversion format (e.g. mmd.zip) to finish."""
        url = f"{MATHPIX_BASE}/converter/{pdf_id}"
        deadline = time.time() + self.timeout
        delay = 1.0
        while time.time() < deadline:
            req = urllib.request.Request(url)
            for k, v in self._headers().items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError:
                # Converter endpoint may not be ready yet; keep polling main status.
                time.sleep(delay)
                delay = min(delay * 1.4, 8)
                continue
            conv = (payload.get("conversion_status") or {}).get(fmt) or {}
            if conv.get("status") == "completed":
                return
            if conv.get("status") in ("error", "failed"):
                return  # Non-fatal: fall back to CDN URLs in MMD.
            time.sleep(delay)
            delay = min(delay * 1.4, 8)

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

    def _download_lines_json(self, pdf_id: str) -> dict | None:
        url = f"{MATHPIX_BASE}/pdf/{pdf_id}.lines.json"
        req = urllib.request.Request(url)
        for k, v in self._headers().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            return None

    def _download_bytes(self, pdf_id: str, ext: str) -> bytes | None:
        url = f"{MATHPIX_BASE}/pdf/{pdf_id}.{ext}"
        req = urllib.request.Request(url)
        for k, v in self._headers().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            return None

    def _localize_images_from_zip(
        self, pdf_id: str, cache_key: str, mmd_text: str,
    ) -> str:
        """Extract mmd.zip crops locally and rewrite CDN URLs to cache paths."""
        raw = self._download_bytes(pdf_id, "mmd.zip")
        if not raw:
            return mmd_text
        images_dir = self.cache_dir / f"{cache_key}_images"
        images_dir.mkdir(parents=True, exist_ok=True)
        url_to_local: dict[str, str] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith(
                        (".jpg", ".jpeg", ".png", ".gif", ".webp"),
                    ):
                        continue
                    data = zf.read(name)
                    dest = images_dir / Path(name).name
                    dest.write_bytes(data)
                    # Map by filename stem for CDN URL matching.
                    url_to_local[Path(name).name] = str(dest)
        except zipfile.BadZipFile:
            return mmd_text

        def _rewrite_url(match: re.Match[str]) -> str:
            cdn_url = match.group(0)
            basename = cdn_url.split("/")[-1].split("?")[0]
            local = url_to_local.get(basename)
            if local is None:
                return cdn_url
            return local

        return _CDN_URL_RE.sub(_rewrite_url, mmd_text)

    @staticmethod
    def _enrich_with_figures(mmd_text: str, lines_data: dict) -> str:
        from figure_inline import enrich_mmd_with_figures
        return enrich_mmd_with_figures(mmd_text, lines_data)
