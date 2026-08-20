"""Sign-in for the wrapped mobile apps (App Store / Play Store builds).

Google blocks its Identity Services sign-in inside embedded WebViews, so
the store apps cannot use the web page's one-tap flow. This router gives
them the sanctioned alternative — the OAuth authorization-code flow in
the SYSTEM browser:

    app opens  GET /auth/native/start          (system browser)
      → 302 accounts.google.com               (user signs in to Google)
      → 302 GET /auth/native/callback?code=…  (this server)
      → verifies the code, mints a 90-second one-time login ticket
      → 302 aegis://auth?ticket=…             (the OS hands it to the app)
    app posts  POST /auth/native/exchange {ticket}
      → the ticket becomes the normal session cookie inside the app's
        WebView; from here the app is indistinguishable from the web app.

The ticket IS an ``auth.encode_session`` value with a 90-second expiry —
one token authority, no second format — and the exchange re-issues the
full-TTL cookie. A best-effort in-memory single-use guard closes replay
within those 90 seconds. Requires ``AEGIS_GOOGLE_CLIENT_SECRET`` (the
web flow needs only the client id); with it unset every route answers
404 so nothing half-configured is reachable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..services import auth

router = APIRouter(prefix="/auth/native", tags=["auth"])

# Served at the root, outside the /auth/native prefix — Android requires
# this exact path for Play Store app-link verification.
wellknown_router = APIRouter(tags=["auth"])

# The one redirect target the apps register. A custom scheme cannot be
# claimed by a web page, and the allowlist keeps the callback from ever
# bouncing a ticket to an arbitrary URL.
APP_REDIRECT = "aegis://auth"

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

_STATE_TTL_SECONDS = 600
_TICKET_TTL_SECONDS = 90

# Best-effort single-use guard for tickets (single-process deployment;
# the 90-second expiry bounds replay regardless).
_used_tickets: set[str] = set()
_used_lock = threading.Lock()


def _client_secret() -> str:
    return str(
        getattr(config, "GOOGLE_CLIENT_SECRET", "")
        or ""
    ).strip()


def _require_configured() -> None:
    if auth.auth_mode() != "google" or not _client_secret():
        raise HTTPException(
            404,
            "native sign-in is not configured on this server "
            "(AEGIS_GOOGLE_CLIENT_SECRET)",
        )


def _public_base(request: Request) -> str:
    configured = str(
        getattr(config, "PUBLIC_BASE_URL", "") or ""
    ).strip().rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{proto}://{host}"


def _sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    secret = str(config.SESSION_SECRET).strip().encode("utf-8")
    mac = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return body.decode("ascii") + "." + mac


def _verify_signed(value: str, *, max_age: int) -> dict:
    try:
        body, mac = value.split(".", 1)
        secret = str(config.SESSION_SECRET).strip().encode("utf-8")
        expected = hmac.new(
            secret, body.encode("ascii"), hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(mac, expected):
            raise ValueError("bad signature")
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if int(payload.get("iat") or 0) + max_age < time.time():
            raise ValueError("expired")
        return payload
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "invalid or expired sign-in state") from exc


@router.get("/start")
def start(request: Request):
    """Send the system browser to Google's consent screen."""

    _require_configured()
    state = _sign({"iat": int(time.time()), "nonce": secrets.token_urlsafe(12)})
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": _public_base(request) + "/auth/native/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return RedirectResponse(
        _GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params),
        status_code=302,
    )


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    """Exchange Google's code and bounce a one-time ticket to the app."""

    _require_configured()
    _verify_signed(state, max_age=_STATE_TTL_SECONDS)
    if not code.strip():
        raise HTTPException(400, "Google returned no authorization code")
    token_response = http_requests.post(
        _GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": _client_secret(),
            "redirect_uri": _public_base(request) + "/auth/native/callback",
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if token_response.status_code != 200:
        raise HTTPException(
            401, "Google rejected the sign-in code; try again",
        )
    id_token = str(token_response.json().get("id_token") or "")
    try:
        principal = auth.verify_google_credential(id_token)
    except auth.GoogleCredentialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    auth.adopt_legacy_upload_jobs(db, principal)
    ticket = auth.encode_session(
        principal, expires_at=int(time.time()) + _TICKET_TTL_SECONDS,
    )
    return RedirectResponse(
        APP_REDIRECT + "?ticket=" + urllib.parse.quote(ticket),
        status_code=302,
    )


class ExchangeRequest(BaseModel):
    ticket: str


@router.post("/exchange")
def exchange(
    body: ExchangeRequest,
    response: Response,
):
    """Turn a fresh one-time ticket into the normal session cookie."""

    _require_configured()
    digest = hashlib.sha256(body.ticket.encode("utf-8")).hexdigest()
    with _used_lock:
        if digest in _used_tickets:
            raise HTTPException(401, "this sign-in ticket was already used")
        _used_tickets.add(digest)
        if len(_used_tickets) > 10_000:
            _used_tickets.clear()  # tickets expire in 90s anyway
    try:
        principal = auth.decode_session(body.ticket)
    except Exception as exc:  # noqa: BLE001 — any bad ticket is a 401
        raise HTTPException(
            401, "invalid or expired sign-in ticket; sign in again",
        ) from exc
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=auth.encode_session(principal),
        max_age=int(config.SESSION_TTL_SECONDS),
        httponly=True,
        secure=bool(config.SECURE_COOKIES),
        samesite="lax",
        path="/",
    )
    return auth.auth_state(principal)


@wellknown_router.get("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links for the Play Store app (TWA verification).

    Android checks this exact path to confirm the app and the site are the
    same publisher; only then does the app open full-screen without a
    browser bar. Fingerprints come from the signing key the owner sets in
    ``AEGIS_ANDROID_CERT_SHA256`` (comma-separated for upload + Play App
    Signing keys). Unset → an empty list, which verifies nothing but keeps
    the path well-formed.
    """

    fingerprints = [
        part.strip().upper()
        for part in str(
            getattr(config, "ANDROID_CERT_SHA256", "") or ""
        ).split(",")
        if part.strip()
    ]
    if not fingerprints:
        return []
    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": config.ANDROID_PACKAGE_NAME,
                "sha256_cert_fingerprints": fingerprints,
            },
        }
    ]
