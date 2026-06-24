"""Admin: password-gated editing of every GPT prompt in the tool.

Auth is intentionally lightweight (single shared password, no user accounts):
set ``AEGIS_ADMIN_PASSWORD`` (defaults to ``admin``). Login returns an opaque
token derived from the password; all other admin endpoints require it via the
``X-Admin-Token`` header. Editing a prompt writes an override that every
subsequent generation reads — no restart needed.
"""
from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..services import prompts
from ..services import workbook_prompts

router = APIRouter(prefix="/admin", tags=["admin"])

_SALT = "aegis-admin-v1"


def _password() -> str:
    return os.environ.get("AEGIS_ADMIN_PASSWORD", "admin")


def _token_for(password: str) -> str:
    return hashlib.sha256(f"{_SALT}:{password}".encode("utf-8")).hexdigest()


def _expected_token() -> str:
    return _token_for(_password())


def _require(token: str | None) -> None:
    if not token or token != _expected_token():
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
    _require(x_admin_token)
    workbook_prompts.ensure_registered()
    return {
        "categories": prompts.categories(),
        "prompts": prompts.export_all(),
    }


@router.put("/prompts/{key:path}")
def update_prompt(key: str, req: PromptUpdate, x_admin_token: str | None = Header(default=None)):
    _require(x_admin_token)
    workbook_prompts.ensure_registered()
    try:
        prompts.set_override(key, req.text)
    except KeyError:
        raise HTTPException(404, f"unknown prompt: {key}")
    return prompts.describe(key)


@router.post("/prompts/{key:path}/reset")
def reset_prompt(key: str, x_admin_token: str | None = Header(default=None)):
    _require(x_admin_token)
    workbook_prompts.ensure_registered()
    try:
        prompts.reset(key)
        return prompts.describe(key)
    except KeyError:
        raise HTTPException(404, f"unknown prompt: {key}")
