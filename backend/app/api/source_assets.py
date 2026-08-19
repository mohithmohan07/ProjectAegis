"""Public delivery of immutable content-addressed source crops.

Durability contract (Q8, step 10): a URL already embedded in published content
keeps resolving for as long as the bytes exist anywhere on this volume. The
``sig`` query parameter is accepted for compatibility with every URL already
in the wild, but it is advisory — the sha256 content-hash filename is the
capability token, and coupling delivery to a rotatable secret is what used to
kill published links. The only gates are mechanical: filename shape and path
containment.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services import canonical_source_phase221_fallback as fallback
from ..services import source_asset_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/source-assets", tags=["source-assets"])


@router.get("/{job_id}/{filename}")
def source_asset(job_id: int, filename: str, sig: str = Query(default="")):
    """Serve an immutable source crop; the signature never gates delivery."""
    try:
        path = fallback.source_asset_path(job_id, filename)
    except ValueError as exc:
        raise HTTPException(404, "source asset not found") from exc
    if path.exists() and path.is_file():
        # Self-heal: a crop minted before the durable store existed gets
        # pinned on first serve, in case the boot-time sweep was interrupted.
        try:
            if not source_asset_store.stored_asset_path(filename).exists():
                try:
                    minted_url = fallback.asset_url(job_id, filename)
                except ValueError:
                    minted_url = ""
                stored_name = source_asset_store.pin_asset(
                    path.read_bytes(), job_id=int(job_id), asset_url=minted_url
                )
                if stored_name != filename:
                    # Same record the boot sweep makes: bytes that no longer
                    # match their content-hash name cannot heal this URL.
                    logger.warning(
                        "source asset %s in job %s does not match its "
                        "content hash (stored as %s); its published URL "
                        "stays job-bound",
                        filename, int(job_id), stored_name,
                    )
        except Exception:
            logger.warning(
                "opportunistic pin failed for source asset %s", filename
            )
    if not path.exists() or not path.is_file():
        # The job's artifact copy is gone (reset, replacement, re-conversion);
        # the mint-time pin keeps the published URL serveable.
        store_path = source_asset_store.stored_asset_path(filename)
        if store_path.exists() and store_path.is_file():
            path = store_path
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
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
