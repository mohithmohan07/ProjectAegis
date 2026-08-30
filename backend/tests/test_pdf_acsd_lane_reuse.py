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


def _identified_verified_batch_result() -> dict:
    return {
        "status": "verified",
        "pages": [
            {
                "page_id": "PDF-PAGE-0001",
                "page_number": 1,
                "blocks": [],
            },
            {
                "page_id": "PDF-PAGE-0002",
                "page_number": 2,
                "blocks": [],
            },
        ],
    }


def _minimal_legacy_bundle(pdf_sha256: str) -> dict:
    return {
        "schema_name": "Aegis GPT Page ACSD",
        "schema_version": "1.1.0",
        "compiler_version": fallback.FALLBACK_COMPILER,
        "source_origin": fallback.FALLBACK_ORIGIN,
        "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": pdf_sha256,
        "pages": copy.deepcopy(_identified_verified_batch_result()["pages"]),
        "batches": [],
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


def test_legacy_sealed_bundle_is_upgraded_without_provider_spend(
    lane,
    tmp_path,
    monkeypatch,
):
    """A verified 2.4.0 seal migrates to the supported-text contract in place.

    Job 97 was a new upload but reused this exact stale-cache shape. The old
    seal remains evidence only after its original page digest verifies; its
    page render carriers and duplicated outline boundary text are then
    mechanically upgraded and resealed under the new identity.
    """
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    legacy_bundle = {
        "schema_name": "Aegis GPT Page ACSD",
        "schema_version": "1.1.0",
        "compiler_version": fallback.FALLBACK_COMPILER,
        "source_origin": fallback.FALLBACK_ORIGIN,
        "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": lane["sha"],
        "pages": [
            {
                "page_id": "PDF-PAGE-0001",
                "page_number": 1,
                "blocks": [{
                    "reading_order": 7,
                    "kind": "math",
                    "text": "",
                    "latex": (
                        r"1\ \mathrm{W}=1\ \text{volt}\times1\ \text{ampere}"
                        r"=1\ \mathrm{V\ A}\qquad(11.23)"
                    ),
                    "table_rows": [[
                        r"1\ \mathrm{kW\,h}",
                        r"1\ \mathrm{J/s}",
                        r"y=\mathrm{x/y}",
                    ]],
                }],
            },
            {
                "page_id": "PDF-PAGE-0002",
                "page_number": 2,
                "blocks": [{
                    "reading_order": 1,
                    "kind": "math",
                    "text": "",
                    "latex": r"R=5\ \mathrm{\Omega}",
                    "table_rows": [],
                }],
            },
        ],
        "batches": [],
        "chapter_outline": {
            "version": fallback.OUTLINE_VERSION,
            # Semantic titles are not render copies and must stay untouched.
            "topics": [{"title": r"\mathrm{Electricity}"}],
            "task_partitions": [{
                "page_id": "PDF-PAGE-0001",
                "reading_order": 7,
                "independent_parts": [{
                    "label": "(a)",
                    "stem": r"Use 1\ \mathrm{kW\ h} for the calculation.",
                    "text": r"Find energy in \mathrm{kW\,h}.",
                }],
            }],
        },
    }
    legacy_key = fallback._legacy_bundle_cache_keys(lane["sha"])[0]
    fallback._write_verified_batch_cache(legacy_key, {
        "version": "2.4.0",
        "status": "verified",
        "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": lane["sha"],
        "result_sha256": fallback._bundle_pages_sha256(legacy_bundle),
        "result": copy.deepcopy(legacy_bundle),
    })

    def provider(_batch):
        pytest.fail("a verified legacy seal must not replay the provider")

    upgraded = fallback.extract_pdf_to_page_acsd(
        source_path, provider=provider,
    )

    assert upgraded["schema_version"] == fallback.PAGE_ACSD_SCHEMA_VERSION
    assert upgraded["ingestion_contract_version"] == (
        fallback.INGESTION_CONTRACT_VERSION
    )
    first = upgraded["pages"][0]["blocks"][0]
    assert first["latex"] == (
        r"1\ \text{W}=1\ \text{volt}\times1\ \text{ampere}"
        r"=1\ \text{V A}\qquad(11.23)"
    )
    assert first["table_rows"] == [[
        r"1\ \text{kW h}",
        r"1\ \text{J}/\text{s}",
        r"y=\text{x}/\text{y}",
    ]]
    assert r"\text{x/y}" not in first["table_rows"][0][2]
    parts = upgraded["chapter_outline"]["task_partitions"][0][
        "independent_parts"
    ]
    assert parts == [{
        "label": "(a)",
        "stem": r"Use 1\ \text{kW h} for the calculation.",
        "text": r"Find energy in \text{kW h}.",
    }]

    # Recursively inspect every source-rendering carrier. Only the deliberately
    # unsupported nested semantic command remains for downstream review.
    carriers = [
        value
        for page in upgraded["pages"]
        for block in page["blocks"]
        for value in (
            block.get("text"),
            block.get("latex"),
            *(cell for row in block.get("table_rows") or [] for cell in row),
        )
        if isinstance(value, str)
    ] + [
        str(part.get(field) or "")
        for partition in upgraded["chapter_outline"]["task_partitions"]
        for part in partition["independent_parts"]
        for field in ("stem", "text")
    ]
    assert [value for value in carriers if r"\mathrm" in value] == [
        r"R=5\ \mathrm{\Omega}",
    ]
    assert upgraded["chapter_outline"]["topics"][0]["title"] == (
        r"\mathrm{Electricity}"
    )

    current_seal = fallback._read_verified_batch_cache(
        fallback._bundle_cache_key(lane["sha"])
    )
    assert current_seal is not None
    assert current_seal["version"] == fallback.FALLBACK_VERSION
    assert current_seal["ingestion_contract_version"] == (
        fallback.INGESTION_CONTRACT_VERSION
    )
    assert current_seal["result_sha256"] == (
        fallback._bundle_pages_sha256(upgraded)
    )
    assert any("Upgraded the sealed verified" in m for m in lane["logs"])


@pytest.mark.parametrize(
    ("target", "field", "wrong_value"),
    [
        ("envelope", "version", "2.3.0"),
        ("envelope", "model", "wrong-model"),
        ("result", "schema_version", "0.0.0"),
        ("result", "compiler_version", "wrong-compiler"),
        ("result", "source_origin", "wrong-origin"),
        ("result", "model", "wrong-model"),
    ],
)
def test_wrong_identity_legacy_seal_falls_through_as_a_miss(
    lane,
    tmp_path,
    monkeypatch,
    target,
    field,
    wrong_value,
):
    """A legacy filename cannot promote mismatched JSON as current evidence."""
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(fallback, "derive_chapter_outline", lambda _bundle: None)
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    legacy_bundle = _minimal_legacy_bundle(lane["sha"])
    legacy_envelope = {
        "version": "2.4.0",
        "status": "verified",
        "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": lane["sha"],
        "result_sha256": fallback._bundle_pages_sha256(legacy_bundle),
        "result": legacy_bundle,
    }
    if target == "envelope":
        legacy_envelope[field] = wrong_value
    else:
        legacy_bundle[field] = wrong_value
        legacy_envelope["result_sha256"] = fallback._bundle_pages_sha256(
            legacy_bundle
        )
    fallback._write_verified_batch_cache(
        fallback._legacy_bundle_cache_keys(lane["sha"])[0],
        legacy_envelope,
    )
    provider_calls = 0

    def provider(_batch):
        nonlocal provider_calls
        provider_calls += 1
        return copy.deepcopy(_identified_verified_batch_result())

    result = fallback.extract_pdf_to_page_acsd(source_path, provider=provider)

    assert provider_calls == 1
    assert result["batches"][0]["cache"] == "miss"
    assert not any("Upgraded the sealed verified" in m for m in lane["logs"])


@pytest.mark.parametrize(
    "tampered_identity",
    ["envelope_page_id", "result_page_id", "result_page_number"],
)
def test_wrong_page_identity_in_legacy_batch_falls_through_as_a_miss(
    lane,
    tmp_path,
    monkeypatch,
    tampered_identity,
):
    """Both the batch key receipt and its result must name the exact pages."""
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(fallback, "derive_chapter_outline", lambda _bundle: None)
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    batch = _fake_pages(1, 2)
    legacy_result = _identified_verified_batch_result()
    legacy_envelope = {
        "version": "2.4.0",
        "status": "verified",
        "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": lane["sha"],
        "page_ids": [page.page_id for page in batch],
        "result": legacy_result,
    }
    if tampered_identity == "envelope_page_id":
        legacy_envelope["page_ids"][0] = "PDF-PAGE-9999"
    elif tampered_identity == "result_page_id":
        legacy_result["pages"][0]["page_id"] = "PDF-PAGE-9999"
    else:
        legacy_result["pages"][0]["page_number"] = "oops"
    fallback._write_verified_batch_cache(
        fallback._legacy_batch_cache_keys(lane["sha"], batch)[0],
        legacy_envelope,
    )
    provider_calls = 0

    def provider(_batch):
        nonlocal provider_calls
        provider_calls += 1
        return copy.deepcopy(_identified_verified_batch_result())

    result = fallback.extract_pdf_to_page_acsd(source_path, provider=provider)

    assert provider_calls == 1
    assert result["batches"][0]["cache"] == "miss"


def test_wrong_model_legacy_outline_falls_through_as_a_miss(
    lane,
    tmp_path,
    monkeypatch,
):
    """A wrong-model outline is re-authored, not relabelled as current."""
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    page_acsd = {
        "pdf_sha256": lane["sha"],
        "pages": [{
            "page_id": "PDF-PAGE-0001",
            "page_number": 1,
            "blocks": [
                {
                    "kind": "heading",
                    "reading_order": 1,
                    "heading_level": 1,
                    "text": "Electricity",
                },
                {
                    "kind": "task",
                    "reading_order": 2,
                    "source_label": "Question",
                    "text": "State the unit of electric power.",
                },
            ],
        }],
    }
    legacy_outline = {
        "version": fallback.OUTLINE_VERSION,
        "chapter_title": "Stale title",
        "topics": [],
        "task_partitions": [],
        "ruled_task_kinds": [
            ["PDF-PAGE-0001", 2, "question"],
        ],
        "unruled_task_refs": [],
        "notes": [],
        "review_flags": [],
    }
    fallback._write_verified_batch_cache(
        fallback._legacy_outline_cache_keys(lane["sha"])[0],
        {
            "version": "2.4.0",
            "status": "verified",
            "model": "wrong-model",
            "pdf_sha256": lane["sha"],
            "result": legacy_outline,
        },
    )
    model_calls = 0

    def model_outline(*_args, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        return {
            "chapter_title": "Electricity",
            "topics": [{
                "title": "Electricity",
                "kind": "content",
                "start_page_id": "PDF-PAGE-0001",
                "start_reading_order": 1,
            }],
            "task_partitions": [],
            "whole_tasks": [{
                "page_id": "PDF-PAGE-0001",
                "reading_order": 2,
                "task_kind": "question",
            }],
            "notes": [],
        }

    monkeypatch.setattr(
        fallback.phase22,
        "_openai_multimodal_json",
        model_outline,
    )

    outline = fallback.derive_chapter_outline(page_acsd)

    assert model_calls == 1
    assert outline is not None
    assert outline["chapter_title"] == "Electricity"


def test_legacy_batch_and_outline_caches_upgrade_without_model_replay(
    lane,
    tmp_path,
    monkeypatch,
):
    """A missing seal may reuse both paid 2.4 caches under the new contract."""
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    batch = _fake_pages(1, 2)
    legacy_batch_result = {
        "status": "verified",
        "pages": [
            {
                "page_id": "PDF-PAGE-0001",
                "page_number": 1,
                "blocks": [{
                    "reading_order": 1,
                    "kind": "task",
                    "text": r"Find energy in 1\ \mathrm{kW\,h}.",
                    "latex": "",
                    "table_rows": [],
                }],
            },
            {
                "page_id": "PDF-PAGE-0002",
                "page_number": 2,
                "blocks": [],
            },
        ],
    }
    legacy_batch_key = fallback._legacy_batch_cache_keys(
        lane["sha"], batch,
    )[0]
    fallback._write_verified_batch_cache(legacy_batch_key, {
        "version": "2.4.0",
        "status": "verified",
        "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": lane["sha"],
        "page_ids": [page.page_id for page in batch],
        "result": legacy_batch_result,
    })
    legacy_outline = {
        "version": fallback.OUTLINE_VERSION,
        "chapter_title": "Electricity",
        "topics": [],
        "task_partitions": [{
            "page_id": "PDF-PAGE-0001",
            "reading_order": 1,
            "independent_parts": [
                {
                    "label": "(a)",
                    "stem": r"Use 1\ \mathrm{kW\ h}.",
                    "text": r"Find energy in 1\ \mathrm{kW\,h}.",
                },
                {
                    "label": "(b)",
                    "stem": "",
                    "text": r"State the answer in \mathrm{J/s}.",
                },
            ],
        }],
        "ruled_task_kinds": [["PDF-PAGE-0001", 1, "question"]],
        "unruled_task_refs": [],
        "notes": [],
        "review_flags": [],
    }
    fallback._write_verified_batch_cache(
        fallback._legacy_outline_cache_keys(lane["sha"])[0],
        {
            "version": "2.4.0",
            "status": "verified",
            "model": fallback.config.OPENAI_MODEL,
            "pdf_sha256": lane["sha"],
            "result": legacy_outline,
        },
    )
    monkeypatch.setattr(
        fallback.phase22,
        "_openai_multimodal_json",
        lambda *_args, **_kwargs: pytest.fail(
            "a verified legacy outline must not be re-billed"
        ),
    )

    result = fallback.extract_pdf_to_page_acsd(
        source_path,
        provider=lambda _batch: pytest.fail(
            "a verified legacy page batch must not be re-billed"
        ),
    )

    assert result["batches"][0]["cache"] == "upgraded"
    assert result["pages"][0]["blocks"][0]["text"] == (
        r"Find energy in 1\ \text{kW h}."
    )
    outline = result["chapter_outline"]
    assert outline["ingestion_contract_version"] == (
        fallback.INGESTION_CONTRACT_VERSION
    )
    assert outline["task_partitions"][0]["independent_parts"] == [
        {
            "label": "(a)",
            "stem": r"Use 1\ \text{kW h}.",
            "text": r"Find energy in 1\ \text{kW h}.",
        },
        {
            "label": "(b)",
            "stem": "",
            "text": r"State the answer in \text{J}/\text{s}.",
        },
    ]
    current_outline_cache = fallback._read_verified_batch_cache(
        fallback._outline_cache_key(lane["sha"])
    )
    assert current_outline_cache is not None
    assert current_outline_cache["ingestion_contract_version"] == (
        fallback.INGESTION_CONTRACT_VERSION
    )


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
        "ingestion_contract_version": fallback.INGESTION_CONTRACT_VERSION,
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


def test_stale_ingestion_identity_is_re_extracted_once(
    lane,
    tmp_path,
    monkeypatch,
):
    """Schema+compiler alone cannot authorize pre-canonicalization evidence."""
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-fake")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    stale = {
        "schema_name": "Aegis GPT Page ACSD",
        "schema_version": fallback.PAGE_ACSD_SCHEMA_VERSION,
        "compiler_version": fallback.FALLBACK_COMPILER,
        "pdf_sha256": lane["sha"],
        "pages": [],
    }
    artifact = artifact_dir / phase3.VISION_ACSD_FILENAME
    artifact.write_text(json.dumps(stale), encoding="utf-8")
    current = {
        **stale,
        "ingestion_contract_version": fallback.INGESTION_CONTRACT_VERSION,
    }
    calls: list[str] = []
    monkeypatch.setattr(phase3, "vision_enabled", lambda: True)
    monkeypatch.setattr(
        phase3.page_acsd,
        "extract_pdf_to_page_acsd",
        lambda *_args, **_kwargs: calls.append("extract")
        or copy.deepcopy(current),
    )

    assert phase3.load_page_evidence(source_path, artifact_dir) == current
    assert calls == ["extract"]
    monkeypatch.setattr(
        phase3.page_acsd,
        "extract_pdf_to_page_acsd",
        lambda *_args, **_kwargs: pytest.fail(
            "current ingestion evidence must be reused"
        ),
    )
    assert phase3.load_page_evidence(source_path, artifact_dir) == current


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
