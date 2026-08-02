"""Nearest-stage resume for the GPT PDF-to-ACSD source lane.

The 02 Aug audit counted eleven orchestration entries into the PDF-to-ACSD
lane in one 40-minute run whose source contract hash never changed. Two
defects allowed that: the Phase 3 page-evidence artifact cache validated
against a schema version the extractor never produced (a dead cache), and the
extractor had no sealed complete-bundle reuse per source hash. These tests pin
the corrected contract: an unchanged source re-enters the lane zero times.
"""
from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase221_fallback as fallback
from app.services import (
    canonical_source_phase34_structured_output_contract as phase34,
)


def _fake_pages(start: int, end: int) -> list[fallback.PdfPage]:
    return [
        fallback.PdfPage(
            page_id=f"PDF-PAGE-{number:04d}",
            page_number=number,
            text=f"page {number} text",
            image_data_url="data:image/jpeg;base64,ZmFrZQ==",
            width=1000.0,
            height=1400.0,
        )
        for number in range(start, end + 1)
    ]


@pytest.fixture()
def lane(monkeypatch):
    """Two-page fake PDF with a unique source hash per test run."""

    sha = secrets.token_hex(32)
    monkeypatch.setattr(fallback, "_pdf_page_count", lambda _path: 2)
    monkeypatch.setattr(fallback, "_pdf_sha256", lambda _path: sha)
    monkeypatch.setattr(fallback, "_batch_size", lambda: 2)
    monkeypatch.setattr(
        fallback,
        "collect_pdf_pages",
        lambda _path, *, start_page=1, end_page=None: _fake_pages(
            start_page, end_page or 2),
    )
    logs: list[str] = []
    original_log = fallback.progress.log
    monkeypatch.setattr(
        fallback.progress,
        "log",
        lambda message, **kwargs: logs.append(str(message)),
    )
    del original_log
    return {"sha": sha, "logs": logs}


def _verified_batch_result() -> dict:
    return {
        "status": "verified",
        "pages": [
            {"page_number": 1, "blocks": []},
            {"page_number": 2, "blocks": []},
        ],
    }


def test_unchanged_source_replays_lane_zero_times(lane, tmp_path):
    provider_calls = 0

    def provider(_batch):
        nonlocal provider_calls
        provider_calls += 1
        return copy.deepcopy(_verified_batch_result())

    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-fake")

    first = fallback.extract_pdf_to_page_acsd(path, provider=provider)
    assert provider_calls == 1
    assert first["schema_version"] == fallback.PAGE_ACSD_SCHEMA_VERSION
    assert first["pdf_sha256"] == lane["sha"]
    assert any("will inspect" in message for message in lane["logs"])

    lane["logs"].clear()
    second = fallback.extract_pdf_to_page_acsd(path, provider=provider)
    # Zero model batches, zero lane banner: the sealed bundle is returned.
    assert provider_calls == 1
    assert second == first
    assert not any("will inspect" in message for message in lane["logs"])
    assert any("Reusing the sealed verified" in m for m in lane["logs"])


def test_changed_source_hash_re_materializes(lane, tmp_path, monkeypatch):
    provider_calls = 0

    def provider(_batch):
        nonlocal provider_calls
        provider_calls += 1
        return copy.deepcopy(_verified_batch_result())

    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-fake")
    fallback.extract_pdf_to_page_acsd(path, provider=provider)
    assert provider_calls == 1

    # A different source hash must not reuse the sealed bundle (and its
    # batch cache keys also change), so the lane runs again.
    monkeypatch.setattr(
        fallback, "_pdf_sha256", lambda _path: secrets.token_hex(32))
    fallback.extract_pdf_to_page_acsd(path, provider=provider)
    assert provider_calls == 2


def test_extractor_schema_version_matches_phase3_cache_gate(
    lane,
    tmp_path,
    monkeypatch,
):
    """The producer stamp and the Phase 3 cache gate share one constant.

    A hardcoded ``1.1.0`` in the gate against a produced ``1.0.0`` is exactly
    the dead-cache drift that caused the audited lane replay.
    """

    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    provider_calls = 0

    def provider(_batch):
        nonlocal provider_calls
        provider_calls += 1
        return copy.deepcopy(_verified_batch_result())

    bundle = fallback.extract_pdf_to_page_acsd(source_path, provider=provider)
    (artifact_dir / phase3.VISION_ACSD_FILENAME).write_text(
        json.dumps(bundle), encoding="utf-8")

    monkeypatch.setattr(phase3, "vision_enabled", lambda: True)
    monkeypatch.setattr(
        phase3.page_acsd,
        "extract_pdf_to_page_acsd",
        lambda *_args, **_kwargs: pytest.fail(
            "an unchanged source must reuse the cached page evidence"),
    )
    loaded = phase3.load_page_evidence(source_path, artifact_dir)
    assert loaded is not None
    assert loaded["pdf_sha256"] == lane["sha"]
    assert provider_calls == 1


def test_stale_schema_artifact_is_re_extracted_once(
    lane,
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    stale = {
        "schema_name": "Aegis GPT Page ACSD",
        "schema_version": "1.0.0",
        "compiler_version": fallback.FALLBACK_COMPILER,
        "pdf_sha256": lane["sha"],
        "pages": [],
    }
    (artifact_dir / phase3.VISION_ACSD_FILENAME).write_text(
        json.dumps(stale), encoding="utf-8")

    sentinel = {
        "schema_name": "Aegis GPT Page ACSD",
        "schema_version": fallback.PAGE_ACSD_SCHEMA_VERSION,
        "compiler_version": fallback.FALLBACK_COMPILER,
        "pdf_sha256": lane["sha"],
        "pages": [],
    }
    monkeypatch.setattr(phase3, "vision_enabled", lambda: True)
    monkeypatch.setattr(
        phase3.page_acsd,
        "extract_pdf_to_page_acsd",
        lambda *_args, **_kwargs: copy.deepcopy(sentinel),
    )
    loaded = phase3.load_page_evidence(source_path, artifact_dir)
    assert loaded == sentinel
    # The refreshed artifact now validates, so the next load reuses it.
    monkeypatch.setattr(
        phase3.page_acsd,
        "extract_pdf_to_page_acsd",
        lambda *_args, **_kwargs: pytest.fail(
            "the refreshed artifact must be reused"),
    )
    assert phase3.load_page_evidence(source_path, artifact_dir) == sentinel


def test_reasoning_effort_negotiation_is_logged_once(monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(
        phase34.progress,
        "log",
        lambda message, **kwargs: logs.append(str(message)),
    )
    monkeypatch.setattr(phase34, "_LOGGED_EFFORT_NEGOTIATIONS", set())

    # Unchanged effort: nothing to report.
    phase34._log_effort_negotiation_once(
        label="semantic hierarchy",
        model="model-x",
        requested="xhigh",
        used="xhigh",
    )
    assert logs == []

    phase34._log_effort_negotiation_once(
        label="semantic hierarchy",
        model="model-x",
        requested="xhigh",
        used="high",
    )
    phase34._log_effort_negotiation_once(
        label="semantic hierarchy",
        model="model-x",
        requested="xhigh",
        used="high",
    )
    negotiated = [m for m in logs if "reasoning effort negotiated" in m]
    assert len(negotiated) == 1
    assert "requested 'xhigh'" in negotiated[0]
    assert "used 'high'" in negotiated[0]
