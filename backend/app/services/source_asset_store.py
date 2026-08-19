"""Content-addressed durable store for source-asset crops (Q8, step 10).

Published rich text embeds absolute ``/source-assets/{job_id}/{sha256}.jpg``
URLs, but the bytes behind them live in the job's artifact directory, which
dies on data reset, source replacement, and fallback re-conversion. This store
keeps one copy of every minted crop under its content hash, outside
``UPLOAD_DIR``, so an already-published URL keeps resolving after the job copy
is gone. Each crop carries a manifest sidecar — the Q8 "content hash and
manifest entry" a later publication-time URL rewrite walks to migrate the
corpus.

The asset's identity is the content hash in its filename; the manifest entry
records first-mint provenance (job id, minted URL) as context, not identity. A
publication-time rewrite therefore keys on the hash and may ignore the URL's
job segment — the same bytes referenced by several jobs share one entry. Two
concurrent first mints of the same bytes may race on the sidecar; both write
atomically and either job's provenance is an acceptable winner.

Everything here is mechanics: hashing, atomic writes, path containment. The
only judgments are refusals of broken names/paths.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import config

_LOGGER = logging.getLogger(__name__)

STORE_DIRNAME = "source-asset-store"

# Owner of the content-addressed filename shape; phase221_fallback's _BBOX_RE
# aliases this so the mint and both serve paths cannot drift apart.
CONTENT_FILENAME_RE = re.compile(r"^[0-9a-f]{64}\.jpg$")

MEDIA_TYPE = "image/jpeg"


def store_root() -> Path:
    # Read DATA_DIR at call time so tests can point the store elsewhere.
    return Path(config.DATA_DIR) / STORE_DIRNAME


def stored_asset_path(filename: str) -> Path:
    if not CONTENT_FILENAME_RE.fullmatch(str(filename or "")):
        raise ValueError("invalid source asset filename")
    root = store_root().resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError("invalid source asset path")
    return path


def manifest_path(filename: str) -> Path:
    asset = stored_asset_path(filename)
    return asset.with_suffix(".json")


def is_store_member(name: str) -> bool:
    """Whether a directory entry is a store asset or its manifest sidecar."""
    name = str(name or "")
    if CONTENT_FILENAME_RE.fullmatch(name):
        return True
    return name.endswith(".json") and bool(
        CONTENT_FILENAME_RE.fullmatch(name[: -len(".json")] + ".jpg")
    )


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def pin_asset(
    data: bytes,
    *,
    job_id: int,
    asset_url: str,
    public_base_url: str = "",
) -> str:
    """Idempotently copy one crop into the store with its manifest entry.

    Returns the content-addressed filename. First mint wins for the manifest:
    the same bytes referenced by a later job keep their original provenance
    entry, which is all the corpus rewrite needs (hash -> bytes + first URL).
    """
    filename = f"{hashlib.sha256(data).hexdigest()}.jpg"
    asset = stored_asset_path(filename)
    if not asset.exists():
        _atomic_write_bytes(asset, data)
    elif hashlib.sha256(asset.read_bytes()).hexdigest() != asset.stem:
        # The stored bytes no longer match the name they live under (volume
        # corruption, partial restore). We hold the correct bytes — their
        # hash IS this name — so heal in place and record it.
        _LOGGER.warning(
            "stored source asset %s no longer matched its content hash; "
            "re-pinned from fresh bytes", filename,
        )
        _atomic_write_bytes(asset, data)
    sidecar = manifest_path(filename)
    if not sidecar.exists():
        entry: dict[str, Any] = {
            "sha256": asset.stem,
            "bytes": len(data),
            "media_type": MEDIA_TYPE,
            "job_id": int(job_id),
            "asset_url": str(asset_url or ""),
            "public_base_url": str(public_base_url or ""),
            "created_at": time.time(),
        }
        _atomic_write_bytes(
            sidecar,
            json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
    return filename
