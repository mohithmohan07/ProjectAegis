"""Public authentication bootstrap and session endpoints."""
from __future__ import annotations

import re

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..services import auth, drive_checkpoints


router = APIRouter(prefix="/auth", tags=["auth"])
_CSRF_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=16_000)
    csrf_token: str = Field(min_length=1, max_length=256)


def _secure_cookie() -> bool:
    return bool(config.SECURE_COOKIES)


def _set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.AUTH_CSRF_COOKIE_NAME,
        value=token,
        max_age=30 * 60,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )


def _set_private_auth_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"


@router.get("/config")
def auth_config(
    response: Response,
    aegis_auth_csrf: str | None = Cookie(
        default=None,
        alias=config.AUTH_CSRF_COOKIE_NAME,
    ),
):
    _set_private_auth_headers(response)
    mode = auth.auth_mode()
    if mode == "google":
        auth.validate_configuration()
    csrf_token = (
        aegis_auth_csrf
        if aegis_auth_csrf and _CSRF_RE.fullmatch(aegis_auth_csrf)
        else auth.new_csrf_token()
    )
    _set_csrf_cookie(response, csrf_token)
    backup = drive_checkpoints.checkpoint_backup_status()
    return {
        "mode": mode,
        "google_client_id": (
            str(config.GOOGLE_CLIENT_ID) if mode == "google" else ""
        ),
        "allowed_google_domain": str(config.ALLOWED_GOOGLE_DOMAIN),
        "csrf_token": csrf_token,
        "drive_checkpoint_backup": {
            "enabled": backup.enabled,
            "configured": backup.configured,
            "state": backup.state,
            "verified": bool(backup.last_success_at),
            "auth_mode": backup.auth_mode,
            "notice": backup.notice,
        },
    }


@router.get("/me")
def me(
    response: Response,
    aegis_session: str | None = Cookie(
        default=None,
        alias=config.SESSION_COOKIE_NAME,
    ),
):
    _set_private_auth_headers(response)
    return auth.auth_state(auth.optional_principal(aegis_session))


@router.post("/google")
def google_login(
    request: GoogleLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    aegis_auth_csrf: str | None = Cookie(
        default=None,
        alias=config.AUTH_CSRF_COOKIE_NAME,
    ),
):
    if auth.auth_mode() != "google":
        raise HTTPException(400, "Google sign-in is disabled in local auth mode")
    auth.verify_csrf(request.csrf_token, aegis_auth_csrf)
    try:
        principal = auth.verify_google_credential(request.credential)
    except auth.GoogleCredentialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    auth.adopt_legacy_upload_jobs(db, principal)
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=auth.encode_session(principal),
        max_age=int(config.SESSION_TTL_SECONDS),
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )
    # Rotate the pre-authentication nonce so it cannot be replayed.
    _set_csrf_cookie(response, auth.new_csrf_token())
    return auth.auth_state(principal)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        key=config.SESSION_COOKIE_NAME,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        key=config.AUTH_CSRF_COOKIE_NAME,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )
    return {"ok": True}
