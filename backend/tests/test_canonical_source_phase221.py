"""Regression coverage for the Phase 2.2.1 evidence-page protocol."""
from __future__ import annotations

import json
from pathlib import Path

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase221_contract as phase221

DATA = Path(__file__).parents[1] / "data" / "Testing"


def _page(number: int, text: str) -> phase22.EvidencePage:
    return phase22.EvidencePage(
        page_number=number,
        text=text,
        image_data_url="data:image/jpeg;base64,AA==",
        score=1.0,
    )


def _heading_packet() -> dict:
    return {
        "issue_id": "ADJ-SECTION-2-test",
        "issue_type": "missing_parent_section",
        "section_number": 2,
        "source_start": 200,
        "allowed_insert_before_block_ids": ["BLK-PREV", "BLK-2-1"],
        "nearby_blocks": [
            {
                "block_id": "BLK-PREV",
                "kind": "paragraph",
                "source_start": 150,
                "text": "Previous section tail",
            },
            {
                "block_id": "BLK-2-1",
                "kind": "heading",
                "source_start": 200,
                "text": "2.1 The Aristocracy and the New Middle Class",
            },
        ],
        "search_terms": ["The Aristocracy and the New Middle Class"],
        "fingerprint": "a" * 64,
    }


def _figure_packet() -> dict:
    return {
        "issue_id": "ADJ-FIGURE-FIG-00006-test",
        "issue_type": "orphan_figure_task",
        "figure_id": "FIG-00006",
        "figure_caption": "Fig. 6 - The Club of Thinkers",
        "source_start": 500,
        "allowed_insert_before_block_ids": ["BLK-TASK", "BLK-FIG"],
        "nearby_blocks": [
            {
                "block_id": "BLK-TASK",
                "kind": "paragraph",
                "source_start": 440,
                "text": "Plot on a map of Europe...",
            },
            {
                "block_id": "BLK-FIG",
                "kind": "figure",
                "source_start": 500,
                "text": "Fig. 6 - The Club of Thinkers",
            },
        ],
        "search_terms": ["Club of Thinkers"],
        "fingerprint": "b" * 64,
    }


def _corrupted_rne() -> str:
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    return source.replace(
        "\\section*{2 The Making of Nationalism in Europe}\n\n",
        "",
        1,
    ).replace(
        "\\section*{Discuss}\n\n"
        "What is the caricaturist trying to depict?\n\n",
        "",
        1,
    )


def test_prompt_uses_opaque_evidence_ids_and_server_owned_anchor():
    pages = [_page(7, "Section 2"), _page(9, "Club of Thinkers")]
    payload = json.loads(phase221.packet_prompt(_heading_packet(), pages=pages))

    assert payload["evidence_page_protocol"] == "opaque-evidence-page-id-v1"
    assert [
        item["evidence_page_id"]
        for item in payload["available_evidence_pages"]
    ] == ["EVIDENCE-PDF-00007", "EVIDENCE-PDF-00009"]
    assert payload["deterministic_insert_before_block_id"] == "BLK-2-1"
    assert "evidence_page_id" in payload["required_response_schema"]
    assert "page_number" not in payload["required_response_schema"]
    assert "insert_before_block_id" not in payload["required_response_schema"]


def test_printed_page_number_cannot_override_valid_evidence_page_id():
    page = _page(9, "2 The Making of Nationalism in Europe")
    candidate = {
        "verdict": "visible_exact",
        "evidence_page_id": "EVIDENCE-PDF-00009",
        # Simulate the model also seeing a printed textbook page number. The
        # opaque evidence ID is authoritative, so this stray number is ignored.
        "page_number": 5,
        "recovered_text": "2 The Making of Nationalism in Europe",
        "source_label": "",
        "insert_before_block_id": "BLK-PREV",
        "confidence": 0.999,
    }

    accepted, reason = phase221.validated_candidate(
        _heading_packet(), candidate, [page]
    )

    assert reason == ""
    assert accepted is not None
    assert accepted["evidence_page_id"] == "EVIDENCE-PDF-00009"
    assert accepted["page_number"] == 9
    assert accepted["insert_before_block_id"] == "BLK-2-1"


def test_unknown_evidence_page_id_reports_the_available_packet_ids():
    page = _page(9, "2 The Making of Nationalism in Europe")
    candidate = {
        "verdict": "visible_exact",
        "evidence_page_id": "EVIDENCE-PDF-00005",
        "recovered_text": "2 The Making of Nationalism in Europe",
        "confidence": 0.999,
    }

    accepted, reason = phase221.validated_candidate(
        _heading_packet(), candidate, [page]
    )

    assert accepted is None
    assert "unknown evidence_page_id" in reason
    assert "EVIDENCE-PDF-00009" in reason


def test_legacy_internal_pdf_page_number_is_accepted_only_when_in_packet():
    page = _page(9, "2 The Making of Nationalism in Europe")
    candidate = {
        "verdict": "visible_exact",
        "page_number": 9,
        "recovered_text": "2 The Making of Nationalism in Europe",
        "confidence": 0.999,
    }

    accepted, reason = phase221.validated_candidate(
        _heading_packet(), candidate, [page]
    )

    assert reason == ""
    assert accepted is not None
    assert accepted["evidence_page_id"] == "EVIDENCE-PDF-00009"


def test_extraction_and_verification_use_the_same_opaque_page_id(monkeypatch):
    packet = _figure_packet()
    page = _page(
        9,
        "Discuss\nWhat is the caricaturist trying to depict?\n"
        "Fig. 6 - The Club of Thinkers",
    )
    responses = iter([
        {
            "verdict": "visible_exact",
            "evidence_page_id": "EVIDENCE-PDF-00009",
            "recovered_text": "What is the caricaturist trying to depict?",
            "source_label": "Discuss",
            "confidence": 0.999,
            "evidence": ["Question is visibly printed above Fig. 6."],
        },
        {
            "verdict": "visible_exact",
            "evidence_page_id": "EVIDENCE-PDF-00009",
            "recovered_text": "What is the caricaturist trying to depict?",
            "source_label": "Discuss",
            "confidence": 0.999,
            "evidence": ["Independent visible-text confirmation."],
        },
    ])
    prompts: list[str] = []

    def fake_openai(*, system, prompt, pages, max_tokens=0):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_openai)

    result = phase22.adjudicate_packet_via_openai(packet, [page])

    assert result["status"] == "verified"
    decision = result["decision"]
    assert decision["page_number"] == 9
    assert decision["evidence_page_id"] == "EVIDENCE-PDF-00009"
    assert decision["insert_before_block_id"] == "BLK-FIG"
    assert decision["verification"]["evidence_page_id"] == (
        "EVIDENCE-PDF-00009"
    )
    assert all("EVIDENCE-PDF-00009" in prompt for prompt in prompts)


def test_review_required_results_are_not_cached(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path)
    phase22._write_cache(
        "bad-result",
        {
            "version": phase221.PATCH_VERSION,
            "result": {
                "status": "review_required",
                "reason": "unknown evidence_page_id",
            },
        },
    )

    assert not (tmp_path / "bad-result.json").exists()
    assert phase22._read_cache("bad-result") is None


def test_only_verified_protocol_results_are_reused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path)
    phase22._write_cache(
        "verified-result",
        {
            "version": "stale-value-is-replaced",
            "result": {
                "status": "verified",
                "decision": {
                    "evidence_page_id": "EVIDENCE-PDF-00009",
                    "recovered_text": "Visible source text",
                },
            },
        },
    )

    cached = phase22._read_cache("verified-result")

    assert cached is not None
    assert cached["version"] == phase221.PATCH_VERSION
    assert cached["evidence_page_protocol"] == "opaque-evidence-page-id-v1"


def test_phase22_marker_exposes_patch_protocol_without_changing_family_version():
    compiled = phase2.compile_phase2_source(
        _corrupted_rne(),
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    )
    marker = compiled.canonical["source_adjudication"]

    assert marker["version"] == "2.2.0"
    assert marker["patch_version"] == "2.2.1"
    assert marker["evidence_page_protocol"] == "opaque-evidence-page-id-v1"
    assert compiled.canonical["source_contract"][
        "adjudication_patch_version"
    ] == "2.2.1"
