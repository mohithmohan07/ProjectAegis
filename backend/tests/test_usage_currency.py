from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from app import config
from app.services import usage_currency as fx


@pytest.fixture(autouse=True)
def reset_fx(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fx, "_cached", None)
    monkeypatch.setattr(fx, "_next_refresh", 0.0)
    monkeypatch.delenv("AEGIS_USD_TO_INR_RATE", raising=False)
    monkeypatch.delenv("AEGIS_USD_TO_INR_AS_OF", raising=False)


def test_reference_cross_rate_and_unknown_cost():
    quote = fx.snapshot()
    assert quote["as_of"] == "2026-09-08"
    assert Decimal(quote["rate"]) == (Decimal("110.1315") / Decimal("1.1614")).quantize(Decimal("0.000000000001"))
    assert fx.to_inr(None, quote) is None
    assert fx.to_inr(0, quote) == 0
    assert fx.to_inr("0.01", quote) == pytest.approx(0.94826502496986)


def test_explicit_rate_snapshot_is_not_revalued(monkeypatch):
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "95")
    monkeypatch.setenv("AEGIS_USD_TO_INR_AS_OF", "2026-09-08")
    old = fx.snapshot()
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", "96")
    assert fx.to_inr("0.10", old) == 9.5
    assert fx.to_inr("0.10", fx.snapshot()) == 9.6


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "invalid"])
def test_invalid_configured_rate_is_explicit(value, monkeypatch):
    monkeypatch.setenv("AEGIS_USD_TO_INR_RATE", value)
    with pytest.raises(ValueError, match="positive finite"):
        fx.snapshot()


def test_ecb_parser_uses_one_dated_pair_and_rejects_missing_or_future():
    body = b'<Envelope><Cube><Cube time="2026-09-08"><Cube currency="USD" rate="1.1614"/><Cube currency="INR" rate="110.1315"/></Cube></Cube></Envelope>'
    assert fx._parse_ecb(body) == fx.snapshot()
    with pytest.raises(ValueError, match="no dated"):
        fx._parse_ecb(body.replace(b'currency="INR"', b'currency="JPY"'))
    future = (date.today() + timedelta(days=1)).isoformat().encode()
    with pytest.raises(ValueError, match="future"):
        fx._parse_ecb(body.replace(b"2026-09-08", future))


def test_refresh_failure_keeps_observation_date_and_is_bounded(monkeypatch):
    monkeypatch.setenv("AEGIS_ALLOW_DRY", "0")
    calls = []

    def fail():
        calls.append(True)
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(fx, "_fetch_quote", fail)
    first = fx.snapshot()
    first["rate"] = "1"
    again = fx.snapshot()
    assert again["as_of"] == "2026-09-08"
    assert again["rate"] != "1"
    assert len(calls) == 1
