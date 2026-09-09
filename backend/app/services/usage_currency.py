"""Dated INR reference quotes for displaying the provider's USD estimates.

Nothing in this module changes the USD billing ledger. Consumers freeze one
quote when a run starts and keep each request's converted amount on its receipt.
The published reference is an estimate, not a card settlement or tax invoice.
"""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
import tempfile
import threading
import time
from xml.etree import ElementTree

import httpx

from .. import config

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
# Verified against the ECB's USD and INR reference tables on 2026-09-09.
# Retain the real observation date on a network failure; never call it live.
_SEED = {
    "rate": str((Decimal("110.1315") / Decimal("1.1614")).quantize(Decimal("0.000000000001"))),
    "as_of": "2026-09-08",
    "source": ECB_URL,
    "kind": "ecb-reference",
}
_lock = threading.Lock()
_cached: dict | None = None
_next_refresh = 0.0


def _positive_decimal(value) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("USD to INR rate must be a positive finite number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("USD to INR rate must be a positive finite number")
    return parsed


def _validate_quote(value: dict) -> dict:
    quote = dict(value)
    quote["rate"] = str(_positive_decimal(quote["rate"]))
    observed = date.fromisoformat(str(quote["as_of"]))
    if observed > date.today():
        raise ValueError("FX quote has a future observation date")
    if quote.get("kind") not in {"configured", "ecb-reference"}:
        raise ValueError("unknown FX quote source")
    quote["as_of"] = observed.isoformat()
    quote["source"] = str(quote["source"])
    return {key: quote[key] for key in ("rate", "as_of", "source", "kind")}


def _parse_ecb(body: bytes) -> dict:
    if len(body) > 65536:
        raise ValueError("ECB rate response exceeds the expected size")
    root = ElementTree.fromstring(body)
    for observation in root.iter():
        observed = observation.attrib.get("time")
        if not observed:
            continue
        rates = {
            child.attrib.get("currency"): child.attrib.get("rate")
            for child in observation
        }
        if "USD" in rates and "INR" in rates:
            rate = _positive_decimal(rates["INR"]) / _positive_decimal(rates["USD"])
            return _validate_quote({
                "rate": str(rate.quantize(Decimal("0.000000000001"))),
                "as_of": observed, "source": ECB_URL, "kind": "ecb-reference",
            })
    raise ValueError("ECB response has no dated USD and INR pair")


def _fetch_quote() -> dict:
    response = httpx.get(ECB_URL, timeout=3.0, follow_redirects=False)
    response.raise_for_status()
    return _parse_ecb(response.content)


def _cache_path():
    return config.DATA_DIR / "currency" / "usd-inr-reference.json"


def _persist(quote: dict) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(quote, handle, sort_keys=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def snapshot() -> dict[str, str]:
    """One reusable dated quote; explicit configuration wins over reference FX.

    A network/storage failure uses the latest verified cached observation. The
    date stays visible, and refresh is attempted at most once per six hours per
    process. Offline test/dry mode makes no reference-data request.
    """
    override = os.environ.get("AEGIS_USD_TO_INR_RATE", "").strip()
    if override:
        return _validate_quote({
            "rate": override,
            "as_of": os.environ.get("AEGIS_USD_TO_INR_AS_OF", "").strip() or date.today().isoformat(),
            "source": "AEGIS_USD_TO_INR_RATE",
            "kind": "configured",
        })
    global _cached, _next_refresh
    with _lock:
        if _cached is None:
            candidates = [_validate_quote(_SEED)]
            try:
                candidates.append(_validate_quote(json.loads(_cache_path().read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError, KeyError):
                pass
            _cached = max(candidates, key=lambda quote: quote["as_of"])
        if not config.allow_dry() and time.monotonic() >= _next_refresh:
            _next_refresh = time.monotonic() + 6 * 60 * 60
            try:
                fresh = _fetch_quote()
                if fresh["as_of"] >= _cached["as_of"]:
                    _cached = fresh
                    _persist(fresh)
            except (httpx.HTTPError, OSError, ValueError, ElementTree.ParseError):
                pass
        return copy.deepcopy(_cached)


def to_inr(cost_usd, quote: dict | None) -> float | None:
    if cost_usd is None or quote is None:
        return None
    cost = Decimal(str(cost_usd))
    if not cost.is_finite() or cost < 0:
        raise ValueError("USD estimate must be a finite nonnegative amount")
    return float((cost * _positive_decimal(quote["rate"])).quantize(Decimal("0.000000000001")))
