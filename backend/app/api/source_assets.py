"""Public delivery of immutable content-addressed source crops.

Durability contract (Q8, step 10): a URL already embedded in published content
keeps resolving for as long as the bytes exist anywhere on this volume. The
``sig`` query parameter is accepted for compatibility with every URL already
in the wild, but it is advisory — the sha256 content-hash filename is the
capability token, and coupling delivery to a rotatable secret is what used to
kill published links. The gates are mechanical: filename shape, path
containment, and content verification — every candidate file is hashed before
it is served, because the response's year-long immutable cache header would
otherwise pin wrong bytes under the good content identity.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from ..services import canonical_source_phase221_fallback as fallback
from ..services import source_asset_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/source-assets", tags=["source-assets"])

# Temporary, content-locked migration constants. The route below accepts one
# exact archive only; it cannot be used as a general upload surface. It is
# removed immediately after the fixed manual asset set is pinned and verified.
_MANUAL_IMPORT_PATH = "/manual-import-balbharati-grade6-20260901"
_MANUAL_IMPORT_ARCHIVE_SHA256 = (
    "6c4feb163e9219869ec12c01731a03075db92e1af1e824d8f800d1880a72d70a"
)
_MANUAL_IMPORT_MANIFEST_SHA256 = (
    "cbcfac958411058706263c50a9c4a02a4ac7d1bc0136e567814dcbd8cde73838"
)
_MANUAL_IMPORT_MANIFEST = (
    "aegis-manual-fly-import/bundle/migration-manifest.json"
)
_MANUAL_IMPORT_ASSET_PREFIX = "aegis-manual-fly-import/bundle/assets/"
_MANUAL_IMPORT_ASSET_COUNT = 28
_MANUAL_IMPORT_MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
_MANUAL_IMPORT_MAX_EXPANDED_BYTES = 16 * 1024 * 1024

_MANUAL_IMPORT_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Aegis fixed asset import</title>
<body style="font-family:system-ui;max-width:52rem;margin:3rem auto;padding:0 1rem">
<h1>Aegis fixed asset import</h1>
<p>This temporary page accepts only the pre-approved archive whose SHA-256 is
<code>6c4feb163e9219869ec12c01731a03075db92e1af1e824d8f800d1880a72d70a</code>.</p>
<input id="archive" type="file" accept=".gz,application/gzip">
<button id="upload" type="button">Import 28 assets</button>
<pre id="status" style="white-space:pre-wrap"></pre>
<script>
const input = document.getElementById('archive');
const button = document.getElementById('upload');
const status = document.getElementById('status');
button.addEventListener('click', async () => {
  const file = input.files[0];
  if (!file) { status.textContent = 'Choose the prepared archive first.'; return; }
  button.disabled = true;
  status.textContent = `Uploading ${file.name} (${file.size} bytes)...`;
  try {
    const response = await fetch(location.pathname, {
      method: 'POST', headers: {'Content-Type': 'application/gzip'}, body: file
    });
    status.textContent = `${response.status} ${await response.text()}`;
  } catch (error) {
    status.textContent = String(error);
  } finally { button.disabled = false; }
});
</script></body></html>"""


@router.get(_MANUAL_IMPORT_PATH, response_class=HTMLResponse, include_in_schema=False)
def manual_asset_import_page() -> str:
    """Render the short-lived uploader for the fixed, hash-locked archive."""
    return _MANUAL_IMPORT_HTML


@router.post(_MANUAL_IMPORT_PATH, include_in_schema=False)
async def manual_asset_import(request: Request) -> dict[str, object]:
    """Pin the one pre-approved manual asset archive into the durable store."""
    length = request.headers.get("content-length", "")
    if length:
        try:
            if int(length) > _MANUAL_IMPORT_MAX_COMPRESSED_BYTES:
                raise HTTPException(413, "archive is too large")
        except ValueError as exc:
            raise HTTPException(400, "invalid Content-Length") from exc

    archive_bytes = await request.body()
    if len(archive_bytes) > _MANUAL_IMPORT_MAX_COMPRESSED_BYTES:
        raise HTTPException(413, "archive is too large")
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != _MANUAL_IMPORT_ARCHIVE_SHA256:
        raise HTTPException(422, "archive hash is not approved")

    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            if any(not (member.isdir() or member.isfile()) for member in members):
                raise ValueError("archive contains a non-regular member")
            expanded_bytes = sum(member.size for member in members if member.isfile())
            if expanded_bytes > _MANUAL_IMPORT_MAX_EXPANDED_BYTES:
                raise ValueError("expanded archive is too large")

            by_name = {member.name: member for member in members if member.isfile()}
            if len(by_name) != sum(member.isfile() for member in members):
                raise ValueError("archive contains duplicate member names")
            manifest_member = by_name.get(_MANUAL_IMPORT_MANIFEST)
            if manifest_member is None:
                raise ValueError("migration manifest is missing")
            manifest_handle = archive.extractfile(manifest_member)
            if manifest_handle is None:
                raise ValueError("migration manifest is unreadable")
            manifest_bytes = manifest_handle.read()
            if hashlib.sha256(manifest_bytes).hexdigest() != _MANUAL_IMPORT_MANIFEST_SHA256:
                raise ValueError("migration manifest hash mismatch")
            manifest = json.loads(manifest_bytes)
            expected = {
                str(item["jpeg_filename"]): int(item["jpeg_size_bytes"])
                for item in manifest.get("assets", [])
            }
            if len(expected) != _MANUAL_IMPORT_ASSET_COUNT:
                raise ValueError("migration manifest asset count mismatch")

            asset_members = {
                name.removeprefix(_MANUAL_IMPORT_ASSET_PREFIX): member
                for name, member in by_name.items()
                if name.startswith(_MANUAL_IMPORT_ASSET_PREFIX)
            }
            if set(asset_members) != set(expected):
                raise ValueError("archive asset inventory mismatch")

            pinned: list[str] = []
            for filename in sorted(expected):
                if not source_asset_store.CONTENT_FILENAME_RE.fullmatch(filename):
                    raise ValueError(f"invalid asset filename: {filename}")
                member = asset_members[filename]
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"unreadable asset: {filename}")
                data = handle.read()
                if len(data) != expected[filename]:
                    raise ValueError(f"asset size mismatch: {filename}")
                if hashlib.sha256(data).hexdigest() != filename.removesuffix(".jpg"):
                    raise ValueError(f"asset hash mismatch: {filename}")
                public_url = f"/source-assets/0/{filename}"
                stored = source_asset_store.pin_asset(
                    data,
                    job_id=0,
                    asset_url=public_url,
                )
                if stored != filename:
                    raise ValueError(f"pinned filename mismatch: {filename}")
                pinned.append(filename)
    except (json.JSONDecodeError, KeyError, OSError, tarfile.TarError, ValueError) as exc:
        logger.warning("fixed manual asset import rejected: %s", exc)
        raise HTTPException(422, str(exc)) from exc

    return {
        "status": "ok",
        "archive_sha256": archive_sha256,
        "assets_pinned": len(pinned),
    }


def _asset_response(path: Path, filename: str) -> FileResponse:
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{job_id}/{filename}")
def source_asset(job_id: int, filename: str, sig: str = Query(default="")):
    """Serve an immutable source crop; the signature never gates delivery."""
    try:
        job_path = fallback.source_asset_path(job_id, filename)
        store_path = source_asset_store.stored_asset_path(filename)
    except ValueError as exc:
        raise HTTPException(404, "source asset not found") from exc
    expected = filename.removesuffix(".jpg")
    failed: list[str] = []

    if job_path.is_file():
        try:
            data = job_path.read_bytes()
        except OSError:
            data = None
        if data is not None and hashlib.sha256(data).hexdigest() == expected:
            # Self-heal: a crop minted before the durable store existed gets
            # pinned on first serve, in case the boot-time sweep was
            # interrupted. Only verified bytes are ever pinned — their hash
            # IS this name — so the store cannot be poisoned from here.
            try:
                if not store_path.exists():
                    try:
                        minted_url = fallback.asset_url(job_id, filename)
                    except ValueError:
                        minted_url = ""
                    source_asset_store.pin_asset(
                        data, job_id=int(job_id), asset_url=minted_url
                    )
            except Exception:
                logger.warning(
                    "opportunistic pin failed for source asset %s", filename
                )
            return _asset_response(job_path, filename)
        failed.append("job copy")
        logger.warning(
            "source asset %s in job %s does not match its content hash; "
            "falling back to the durable store",
            filename, int(job_id),
        )

    if store_path.is_file():
        # The mint-time pin keeps the published URL serveable after the
        # job's artifact copy is gone (reset, replacement, re-conversion) —
        # but only bytes that still match the name are the published asset.
        try:
            store_data = store_path.read_bytes()
        except OSError:
            store_data = None
        if (
            store_data is not None
            and hashlib.sha256(store_data).hexdigest() == expected
        ):
            return _asset_response(store_path, filename)
        failed.append("store copy")

    if failed:
        # Refusing is the only honest answer: serving would cache wrong
        # bytes under the good content identity for a year. The loss is
        # recorded by name, never silent (R4).
        logger.warning(
            "source asset integrity loss: no candidate matches content hash "
            "%s (job_id=%s, failed verification: %s); refusing to serve "
            "wrong bytes",
            expected, int(job_id), ", ".join(failed),
        )
    else:
        # A learner-facing image that cannot be served is a loss; the
        # silence would be the defect, so the miss is recorded (R4).
        logger.warning(
            "source asset missing everywhere: job_id=%s filename=%s "
            "sig_valid=%s",
            int(job_id),
            filename,
            fallback.validate_asset_signature(job_id, filename, sig),
        )
    raise HTTPException(404, "source asset not found")
