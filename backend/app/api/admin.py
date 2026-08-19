"""Admin: password-gated editing of every GPT prompt in the tool.

Auth is intentionally lightweight (single shared password, no user accounts):
set ``AEGIS_ADMIN_PASSWORD`` (defaults to ``admin``). Login returns an opaque
token derived from the password; all other admin endpoints require it via the
``X-Admin-Token`` header. Editing a prompt writes an override that every
subsequent generation reads — no restart needed.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import tarfile
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import config
from ..services import prompts
from ..services import source_asset_store
from ..services import workbook_prompts

router = APIRouter(prefix="/admin", tags=["admin"])

_SALT = "aegis-admin-v1"
_EXPORT_PREFIX = "aegis-asset-export-"


def _password() -> str:
    return str(config.ADMIN_PASSWORD)


def _token_for(password: str) -> str:
    return hashlib.sha256(f"{_SALT}:{password}".encode("utf-8")).hexdigest()


def _expected_token() -> str:
    return _token_for(_password())


def require_admin(token: str | None) -> None:
    if not token or not hmac.compare_digest(token, _expected_token()):
        raise HTTPException(401, "admin authentication required")


class LoginRequest(BaseModel):
    password: str


class PromptUpdate(BaseModel):
    text: str


@router.post("/login")
def login(req: LoginRequest):
    if req.password != _password():
        raise HTTPException(401, "incorrect password")
    return {"token": _token_for(req.password)}


@router.get("/prompts")
def list_prompts(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    workbook_prompts.ensure_registered()
    return {
        "categories": prompts.categories(),
        "prompts": prompts.export_all(),
    }


@router.put("/prompts/{key:path}")
def update_prompt(key: str, req: PromptUpdate, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    workbook_prompts.ensure_registered()
    try:
        prompts.set_override(key, req.text)
    except KeyError:
        raise HTTPException(404, f"unknown prompt: {key}")
    return prompts.describe(key)


@router.post("/prompts/{key:path}/reset")
def reset_prompt(key: str, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    workbook_prompts.ensure_registered()
    try:
        prompts.reset(key)
        return prompts.describe(key)
    except KeyError:
        raise HTTPException(404, f"unknown prompt: {key}")


@router.get("/source-asset-store/export")
def export_source_asset_store(x_admin_token: str | None = Header(default=None)):
    """Download the durable asset store (crops + manifest sidecars) as tar.gz.

    The off-box recovery and migration package for every learner-facing image
    URL: restoring this archive to a fresh volume makes every published link
    resolve again, and its manifest entries are what the designed UpSchool
    publication-time URL rewrite will walk.
    """
    require_admin(x_admin_token)
    root = source_asset_store.store_root()
    # A client that disconnects mid-download can skip the BackgroundTask
    # cleanup, so stale staged archives are swept on the next export.
    for stale in Path(tempfile.gettempdir()).glob(_EXPORT_PREFIX + "*.tar.gz"):
        try:
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink()
        except OSError:
            pass
    fd, tmp_name = tempfile.mkstemp(prefix=_EXPORT_PREFIX, suffix=".tar.gz")
    try:
        with os.fdopen(fd, "wb") as handle:
            with tarfile.open(fileobj=handle, mode="w:gz") as archive:
                if root.is_dir():
                    for child in sorted(root.iterdir()):
                        if not child.is_file():
                            continue
                        if not source_asset_store.is_store_member(child.name):
                            # In-flight atomic-write temp files are not part
                            # of the recovery package.
                            continue
                        try:
                            archive.add(
                                child,
                                arcname=(
                                    f"{source_asset_store.STORE_DIRNAME}"
                                    f"/{child.name}"
                                ),
                            )
                        except FileNotFoundError:
                            # Replaced between listing and archiving by a
                            # concurrent pin; the pinned copy is identical.
                            continue
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return FileResponse(
        tmp_name,
        media_type="application/gzip",
        filename="source-asset-store.tar.gz",
        background=BackgroundTask(os.unlink, tmp_name),
    )
