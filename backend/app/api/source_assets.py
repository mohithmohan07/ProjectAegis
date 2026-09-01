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
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services import canonical_source_phase221_fallback as fallback
from ..services import source_asset_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/source-assets", tags=["source-assets"])


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
