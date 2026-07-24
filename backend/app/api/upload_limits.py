"""Bounded reads for multipart uploads."""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

from .. import config


async def read_limited_upload(
    file: UploadFile,
    *,
    max_bytes: int | None = None,
    description: str = "upload",
) -> bytes:
    """Read no more than the configured cap plus one detection byte."""
    limit = (
        int(config.MAX_UPLOAD_BYTES)
        if max_bytes is None
        else max(0, int(max_bytes))
    )
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            413,
            f"{description} exceeds the {limit:,}-byte upload limit",
        )
    return data
