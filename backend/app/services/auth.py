"""Authentication principals, Google Identity verification, and sessions."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import Cookie, HTTPException
from sqlalchemy.orm import Session

from .. import config, models


LOCAL_OWNER_SUB = "local:default"
_SESSION_VERSION = 1


@dataclass(frozen=True)
class Principal:
    sub: str
    email: str = ""
    name: str = ""
    picture: str = ""
    hd: str = ""

    def public_dict(self) -> dict[str, str]:
        return asdict(self)


LOCAL_PRINCIPAL = Principal(
    sub=LOCAL_OWNER_SUB,
    email="local@localhost",
    name="Local User",
    hd="local",
)


class GoogleCredentialError(ValueError):
    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def auth_mode() -> str:
    mode = str(config.AUTH_MODE or "").strip().lower()
    if mode not in {"local", "google"}:
        raise RuntimeError(
            "AEGIS_AUTH_MODE must be either 'local' or 'google'")
    return mode


def validate_configuration() -> None:
    if auth_mode() != "google":
        return
    if not str(config.GOOGLE_CLIENT_ID or "").strip():
        raise RuntimeError(
            "AEGIS_GOOGLE_CLIENT_ID is required when AEGIS_AUTH_MODE=google")
    secret = str(config.SESSION_SECRET or "").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "AEGIS_SESSION_SECRET must contain at least 32 characters when "
            "AEGIS_AUTH_MODE=google")
    if not str(config.ALLOWED_GOOGLE_DOMAIN or "").strip():
        raise RuntimeError(
            "AEGIS_ALLOWED_GOOGLE_DOMAIN is required in Google auth mode")
    legacy_email = str(config.LEGACY_OWNER_EMAIL or "").strip().lower()
    allowed_domain = str(config.ALLOWED_GOOGLE_DOMAIN).strip().lower()
    if legacy_email and (
        legacy_email.count("@") != 1
        or legacy_email.startswith("@")
        or not legacy_email.endswith(f"@{allowed_domain}")
    ):
        raise RuntimeError(
            "AEGIS_LEGACY_OWNER_EMAIL must be one exact email address in "
            f"the verified @{allowed_domain} domain"
        )
    admin_password = str(config.ADMIN_PASSWORD or "")
    if admin_password == "admin" or len(admin_password) < 16:
        raise RuntimeError(
            "AEGIS_ADMIN_PASSWORD must be non-default and at least 16 "
            "characters when AEGIS_AUTH_MODE=google"
        )


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _session_secret() -> bytes:
    validate_configuration()
    return str(config.SESSION_SECRET).strip().encode("utf-8")


def encode_session(
    principal: Principal,
    *,
    now: int | None = None,
    expires_at: int | None = None,
) -> str:
    """Create a compact HMAC-SHA256 authenticated session value."""
    issued_at = int(time.time() if now is None else now)
    expiry = (
        issued_at + int(config.SESSION_TTL_SECONDS)
        if expires_at is None else int(expires_at)
    )
    payload = {
        "v": _SESSION_VERSION,
        **principal.public_dict(),
        "iat": issued_at,
        "exp": expiry,
    }
    encoded = _b64encode(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))
    signature = _b64encode(hmac.new(
        _session_secret(),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest())
    return f"{encoded}.{signature}"


def decode_session(value: str, *, now: int | None = None) -> Principal:
    """Verify signature, structure, and expiry before returning a principal."""
    try:
        encoded, supplied_signature = value.split(".", 1)
        expected_signature = _b64encode(hmac.new(
            _session_secret(),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest())
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid signature")
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        if payload.get("v") != _SESSION_VERSION:
            raise ValueError("unsupported session version")
        current = int(time.time() if now is None else now)
        issued_at = payload.get("iat")
        expiry = payload.get("exp")
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or issued_at > current + 60
            or
            isinstance(expiry, bool)
            or not isinstance(expiry, int)
            or expiry <= current
            or expiry <= issued_at
        ):
            raise ValueError("session expired")
        sub = str(payload.get("sub") or "").strip()
        if not sub:
            raise ValueError("session subject is missing")
        principal = Principal(
            sub=sub,
            email=str(payload.get("email") or ""),
            name=str(payload.get("name") or ""),
            picture=str(payload.get("picture") or ""),
            hd=str(payload.get("hd") or ""),
        )
        if auth_mode() == "google":
            allowed = str(config.ALLOWED_GOOGLE_DOMAIN).strip().lower()
            if (
                principal.hd.strip().lower() != allowed
                or not principal.email.strip().lower().endswith(f"@{allowed}")
            ):
                raise ValueError("session domain is no longer allowed")
        return principal
    except (
        ValueError, TypeError, KeyError, json.JSONDecodeError,
        UnicodeDecodeError, binascii.Error,
    ) as exc:
        raise ValueError("invalid or expired session") from exc


def optional_principal(
    aegis_session: str | None = Cookie(
        default=None,
        alias=config.SESSION_COOKIE_NAME,
    ),
) -> Principal | None:
    if auth_mode() == "local":
        return LOCAL_PRINCIPAL
    if not aegis_session:
        return None
    try:
        return decode_session(aegis_session)
    except ValueError:
        return None


def require_user(
    aegis_session: str | None = Cookie(
        default=None,
        alias=config.SESSION_COOKIE_NAME,
    ),
) -> Principal:
    principal = optional_principal(aegis_session)
    if principal is None:
        raise HTTPException(401, "authentication required")
    return principal


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(supplied: str, cookie_value: str | None) -> None:
    if (
        not supplied
        or not cookie_value
        or not hmac.compare_digest(str(supplied), str(cookie_value))
    ):
        raise HTTPException(403, "authentication CSRF token is invalid")


def _decode_google_token(credential: str) -> dict[str, Any]:
    """Verify a Google Identity Services ID token using google-auth."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError(
            "google-auth is required when AEGIS_AUTH_MODE=google") from exc
    try:
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            str(config.GOOGLE_CLIENT_ID),
            clock_skew_in_seconds=10,
        )
    except ValueError as exc:
        raise GoogleCredentialError(
            "Google credential is invalid or expired") from exc
    if not isinstance(claims, dict):
        raise GoogleCredentialError("Google credential payload is invalid")
    return claims


def verify_google_credential(credential: str) -> Principal:
    validate_configuration()
    if not credential or len(credential) > 16_000:
        raise GoogleCredentialError("Google credential is missing or invalid")
    claims = _decode_google_token(credential)
    allowed_domain = str(config.ALLOWED_GOOGLE_DOMAIN).strip().lower()
    hosted_domain = str(claims.get("hd") or "").strip().lower()
    if not hosted_domain or hosted_domain != allowed_domain:
        raise GoogleCredentialError(
            f"sign in with a verified {allowed_domain} Google account",
            status_code=403,
        )
    if claims.get("email_verified") is not True:
        raise GoogleCredentialError(
            "Google account email is not verified",
            status_code=403,
        )
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise GoogleCredentialError("Google credential has no stable subject")
    email = str(claims.get("email") or "").strip()
    if not email.lower().endswith(f"@{allowed_domain}"):
        raise GoogleCredentialError(
            f"sign in with a verified {allowed_domain} Google account",
            status_code=403,
        )
    return Principal(
        sub=sub,
        email=email,
        name=str(claims.get("name") or ""),
        picture=str(claims.get("picture") or ""),
        hd=hosted_domain,
    )


def adopt_legacy_upload_jobs(db: Session, principal: Principal) -> int:
    """Atomically assign all pre-auth private work to one configured user.

    The deliberately narrow email allow-list avoids guessing which Google
    account owns rows created before authentication existed. Upload jobs and
    concept-mapping assessment sessions move in the same transaction so a
    failed adoption cannot leave the two private-work stores split.
    """
    configured_email = str(
        config.LEGACY_OWNER_EMAIL or "",
    ).strip().lower()
    if (
        auth_mode() != "google"
        or not configured_email
        or principal.email.strip().lower() != configured_email
    ):
        return 0
    try:
        uploads_updated = db.query(models.UploadJob).filter(
            models.UploadJob.owner_sub == LOCAL_OWNER_SUB,
        ).update(
            {models.UploadJob.owner_sub: principal.sub},
            synchronize_session=False,
        )
        sessions_updated = db.query(models.AssessmentSession).filter(
            models.AssessmentSession.owner_sub == LOCAL_OWNER_SUB,
        ).update(
            {models.AssessmentSession.owner_sub: principal.sub},
            synchronize_session=False,
        )
        db.commit()
        return int(uploads_updated) + int(sessions_updated)
    except Exception:
        db.rollback()
        raise


def auth_state(principal: Principal | None) -> dict:
    return {
        "authenticated": principal is not None,
        "user": principal.public_dict() if principal is not None else None,
    }
