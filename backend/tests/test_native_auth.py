"""Native app sign-in: system-browser OAuth flow + Play asset links.

The store apps cannot use Google Identity Services inside a WebView, so
they sign in through /auth/native/* (see app/api/native_auth.py). These
pins hold the security shape of that flow: nothing reachable until the
server is explicitly configured, HMAC-verified state, a 90-second
single-use ticket that IS a normal session value, and the exchange
re-issuing the real cookie.
"""
from __future__ import annotations

import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api import native_auth
from app.main import app
from app.services import auth


def _google_mode(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "google")
    monkeypatch.setattr(
        config, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-oauth-secret")
    monkeypatch.setattr(config, "ALLOWED_GOOGLE_DOMAIN", "up.school")
    monkeypatch.setattr(config, "LEGACY_OWNER_EMAIL", "")
    monkeypatch.setattr(
        config, "ADMIN_PASSWORD", "strong-google-test-admin-password")
    monkeypatch.setattr(
        config, "SESSION_SECRET", "test-session-secret-" + ("x" * 48))
    monkeypatch.setattr(config, "SECURE_COOKIES", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://aegis.example")


def _principal() -> auth.Principal:
    return auth.Principal(
        sub="google:native-tester",
        email="native-tester@up.school",
        name="Native Tester",
        hd="up.school",
    )


@pytest.fixture(autouse=True)
def _fresh_ticket_guard():
    # The single-use guard is module state; a leaked digest from one test
    # must never fail another.
    with native_auth._used_lock:
        native_auth._used_tickets.clear()
    yield
    with native_auth._used_lock:
        native_auth._used_tickets.clear()


def test_native_routes_answer_404_until_configured(client, monkeypatch):
    # Local mode (the default test posture) has no Google identity at all.
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    assert client.get(
        "/auth/native/start", follow_redirects=False).status_code == 404
    assert client.post(
        "/auth/native/exchange", json={"ticket": "x"}).status_code == 404

    # Google mode WITHOUT the OAuth client secret: web sign-in works but
    # the native flow stays dark rather than half-configured.
    _google_mode(monkeypatch)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    assert client.get(
        "/auth/native/start", follow_redirects=False).status_code == 404


def test_start_redirects_to_google_with_signed_state(client, monkeypatch):
    _google_mode(monkeypatch)
    resp = client.get("/auth/native/start", follow_redirects=False)
    assert resp.status_code == 302
    target = urllib.parse.urlparse(resp.headers["location"])
    assert target.scheme == "https"
    assert target.netloc == "accounts.google.com"
    params = dict(urllib.parse.parse_qsl(target.query))
    assert params["client_id"] == config.GOOGLE_CLIENT_ID
    assert params["redirect_uri"] == (
        "https://aegis.example/auth/native/callback")
    assert params["response_type"] == "code"
    # The state round-trips through Google untouched; only our own HMAC
    # signature makes it acceptable back at the callback.
    payload = native_auth._verify_signed(params["state"], max_age=600)
    assert payload["nonce"]


def test_callback_rejects_forged_or_stale_state(client, monkeypatch):
    _google_mode(monkeypatch)
    resp = client.get(
        "/auth/native/callback",
        params={"code": "anything", "state": "forged.deadbeef"},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    stale = native_auth._sign(
        {"iat": int(time.time()) - 3600, "nonce": "old"})
    resp = client.get(
        "/auth/native/callback",
        params={"code": "anything", "state": stale},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_callback_exchanges_code_and_bounces_one_time_ticket(
    client, monkeypatch,
):
    _google_mode(monkeypatch)

    posted: dict = {}

    class _TokenResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id_token": "google-id-token"}

    def _fake_post(url, data=None, timeout=None):
        posted["url"] = url
        posted["data"] = data
        return _TokenResponse()

    monkeypatch.setattr(native_auth.http_requests, "post", _fake_post)
    monkeypatch.setattr(
        auth, "verify_google_credential",
        lambda credential: _principal(),
    )

    state = native_auth._sign(
        {"iat": int(time.time()), "nonce": "fresh"})
    resp = client.get(
        "/auth/native/callback",
        params={"code": "the-google-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert posted["url"] == "https://oauth2.googleapis.com/token"
    assert posted["data"]["code"] == "the-google-code"
    assert posted["data"]["grant_type"] == "authorization_code"

    location = resp.headers["location"]
    assert location.startswith("aegis://auth?ticket=")
    ticket = urllib.parse.unquote(location.split("ticket=", 1)[1])
    # The ticket IS a session value — one token authority — with a short
    # expiry instead of a second format.
    principal = auth.decode_session(ticket)
    assert principal.email == "native-tester@up.school"


def test_exchange_sets_cookie_once_then_refuses_replay(client, monkeypatch):
    _google_mode(monkeypatch)
    ticket = auth.encode_session(
        _principal(), expires_at=int(time.time()) + 90)

    first = client.post("/auth/native/exchange", json={"ticket": ticket})
    assert first.status_code == 200
    assert first.json()["user"]["email"] == "native-tester@up.school"
    set_cookie = first.headers["set-cookie"]
    assert config.SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie

    replay = client.post("/auth/native/exchange", json={"ticket": ticket})
    assert replay.status_code == 401


def test_exchange_rejects_expired_and_garbage_tickets(client, monkeypatch):
    _google_mode(monkeypatch)
    expired = auth.encode_session(
        _principal(), expires_at=int(time.time()) - 5)
    assert client.post(
        "/auth/native/exchange", json={"ticket": expired}).status_code == 401
    assert client.post(
        "/auth/native/exchange",
        json={"ticket": "not-a-session-value"},
    ).status_code == 401


def test_assetlinks_serves_the_android_signing_identity(client, monkeypatch):
    monkeypatch.setattr(config, "ANDROID_CERT_SHA256", "")
    assert client.get("/.well-known/assetlinks.json").json() == []

    monkeypatch.setattr(
        config, "ANDROID_CERT_SHA256",
        "aa:bb:cc, dd:ee:ff",
    )
    monkeypatch.setattr(config, "ANDROID_PACKAGE_NAME", "school.up.aegis")
    body = client.get("/.well-known/assetlinks.json").json()
    assert body == [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "school.up.aegis",
                "sha256_cert_fingerprints": ["AA:BB:CC", "DD:EE:FF"],
            },
        }
    ]
