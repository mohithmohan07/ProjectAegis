"""Regression coverage for Phase 2.2 evidence-backed source adjudication."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from app import config
from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase21_structure as structure
from app.services import canonical_source_phase22 as phase22
from app.services import generation

DATA = Path(__file__).parents[1] / "data" / "Testing"


def _rne_source() -> str:
    return (DATA / "RNE.mmd").read_text(encoding="utf-8")


def _corrupted_rne() -> str:
    source = _rne_source()
    section_two = "\\section*{2 The Making of Nationalism in Europe}\n\n"
    club = (
        "\\section*{Discuss}\n\n"
        "What is the caricaturist trying to depict?\n\n"
    )
    assert section_two in source and club in source
    return source.replace(section_two, "", 1).replace(club, "", 1)


def _compile(source: str):
    return phase2.compile_phase2_source(
        source,
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    )


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 90), "1 The French Revolution and the Idea of the Nation")
    page.insert_text((60, 140), "2 The Making of Nationalism in Europe")
    page.insert_text((60, 190), "2.1 The Aristocracy and the New Middle Class")
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 90), "Activity")
    page.insert_text((60, 140), "Plot on a map of Europe the changes drawn up by the Vienna Congress.")
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 90), "Discuss")
    page.insert_text((60, 140), "What is the caricaturist trying to depict?")
    page.insert_text((60, 190), "Fig. 6 - The Club of Thinkers")
    doc.save(path)
    doc.close()


def _decision(packet: dict) -> dict:
    if packet["issue_type"] == "missing_parent_section":
        anchor = next(
            block["block_id"]
            for block in packet["nearby_blocks"]
            if "If you look at the map" in block["text"]
        )
        return {
            "status": "verified",
            "decision": {
                "verdict": "visible_exact",
                "page_number": 1,
                "recovered_text": "2 The Making of Nationalism in Europe",
                "source_label": "",
                "insert_before_block_id": anchor,
                "confidence": 0.999,
                "verification": {
                    "confidence": 0.999,
                    "page_number": 1,
                    "text_layer_match": True,
                },
            },
        }
    anchor = next(
        block["block_id"]
        for block in packet["nearby_blocks"]
        if block["kind"] == "figure"
    )
    return {
        "status": "verified",
        "decision": {
            "verdict": "visible_exact",
            "page_number": 3,
            "recovered_text": "What is the caricaturist trying to depict?",
            "source_label": "Discuss",
            "task_kind": "checkpoint_question",
            "insert_before_block_id": anchor,
            "confidence": 0.999,
            "verification": {
                "confidence": 0.999,
                "page_number": 3,
                "text_layer_match": True,
            },
        },
    }


def test_corrupted_rne_exposes_two_bounded_adjudication_packets():
    compiled = _compile(_corrupted_rne())
    marker = compiled.canonical["source_adjudication"]

    assert marker["version"] == "2.2.1"
    assert marker["status"] == "pending"
    assert marker["eligible_issue_count"] == 3
    assert [packet["issue_type"] for packet in marker["packets"]] == [
        "missing_parent_section",
        "orphan_figure_task",
    ]
    assert len(compiled.canonical["tasks"]) == 25


def test_verified_pdf_decisions_restore_six_sections_and_26_tasks(
    tmp_path: Path,
    monkeypatch,
):
    source = _corrupted_rne()
    compiled = _compile(source)
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"

    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", cache_dir)

    job = SimpleNamespace(
        id=72,
        filename="RNE.pdf",
        mmd_text=source,
        generation_checkpoint={"stage": "question_inventory"},
        question_inventory={"items": [{"qid": "legacy"}]},
        detail="",
    )
    db = SimpleNamespace(commits=0)
    db.commit = lambda: setattr(db, "commits", db.commits + 1)

    canonical, report, ready = phase22.adjudicate_job_source(
        db,
        job,
        compiled.canonical,
        compiled.report,
        decision_provider=lambda packet, _pages: _decision(packet),
    )

    assert ready is True
    assert report["phase2_issues"] == []
    assert canonical["phase2_inventory_ready"] is True
    assert canonical["source_adjudication"]["status"] == "verified"
    assert canonical["source_adjudication"]["verified_repairs"] == 2
    assert len(canonical["tasks"]) == 26
    assert [task["qid"] for task in canonical["tasks"]] == [
        f"QINV-{index:04d}" for index in range(1, 27)
    ]
    inventory = phase2.inventory_from_canonical(canonical)
    # Never-split: 26 whole parent items; the sealed counts below still
    # tally the ledger's leaf routes (27: only QINV-0011's recovered
    # follow-up prompt still mints leaves — the retired deterministic
    # enumeration split no longer does).
    assert len(inventory["items"]) == 26
    assert not any("." in item["qid"] for item in inventory["items"])
    assert any(item["qid"] == "QINV-0011" for item in inventory["items"])
    assert canonical["phase21_hardening"]["parent_task_count"] == 26
    assert canonical["phase21_hardening"]["inventory_item_count"] == 27
    assert canonical["source_contract"]["inventory_item_count"] == 27
    assert report["summary"]["inventory_items"] == 27
    mains, _subsections = structure.numbered_heading_inventory(canonical)
    assert sorted(mains) == [1, 2, 3, 4, 5, 6]

    club = next(
        task for task in canonical["tasks"]
        if "caricaturist trying to depict" in task["raw_prompt"]
    )
    assert len(club["figure_refs"]) == 1
    map_task = next(
        task for task in canonical["tasks"]
        if "Plot on a map of Europe" in task["raw_prompt"]
    )
    assert map_task["figure_refs"] == []

    semantic = phase22.semantic_source(canonical, source)
    assert "\\section*{2 The Making of Nationalism in Europe}" in semantic
    assert "What is the caricaturist trying to depict?" in semantic
    assert (artifact_dir / "source.raw.mmd").read_text(encoding="utf-8") == source
    assert job.generation_checkpoint == {}
    assert job.question_inventory == {}
    assert db.commits == 1


def test_phase22_rebuilds_stale_missing_leaf_derivations_unconditionally(
    tmp_path: Path,
    monkeypatch,
):
    source = _corrupted_rne()
    compiled = _compile(source)
    # Simulate a persisted pre-fix artifact whose parent tasks are valid but
    # whose derived follow-up/leaf rows were lost before adjudication.
    for task in compiled.canonical["tasks"]:
        task.pop("source_followup_prompts", None)
        task.pop("leaf_cases", None)
        task.pop("inventory_leaf_count", None)
    compiled.canonical["statistics"]["parent_tasks"] = 25
    compiled.canonical["statistics"]["decomposed_parent_tasks"] = 0
    compiled.canonical["statistics"]["inventory_leaf_tasks"] = 25
    compiled.canonical["source_contract"]["parent_task_count"] = 25
    compiled.canonical["source_contract"]["decomposed_parent_task_count"] = 0
    compiled.canonical["source_contract"]["inventory_item_count"] = 25
    compiled.canonical["phase21_hardening"]["parent_task_count"] = 25
    compiled.canonical["phase21_hardening"]["decomposed_parent_task_count"] = 0
    compiled.canonical["phase21_hardening"]["inventory_item_count"] = 25

    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")
    job = SimpleNamespace(
        id=172,
        filename="RNE.pdf",
        mmd_text=source,
        generation_checkpoint={"stage": "question_inventory"},
        question_inventory={"items": [{"qid": "legacy"}]},
        detail="",
    )
    db = SimpleNamespace(commits=0)
    db.commit = lambda: setattr(db, "commits", db.commits + 1)

    canonical, report, ready = phase22.adjudicate_job_source(
        db,
        job,
        compiled.canonical,
        compiled.report,
        decision_provider=lambda packet, _pages: _decision(packet),
    )

    inventory = phase2.inventory_from_canonical(canonical)
    by_qid = {item["qid"]: item for item in inventory["items"]}
    assert ready is True
    assert len(canonical["tasks"]) == 26
    # Never-split: whole parents only in the inventory; the rebuilt leaf
    # derivations are visible in the ledger and its sealed counts.
    assert len(inventory["items"]) == 26
    assert "QINV-0011" in by_qid
    rebuilt = next(
        task for task in canonical["tasks"] if task["qid"] == "QINV-0011"
    )
    assert [leaf["qid"] for leaf in rebuilt["leaf_cases"]] == [
        "QINV-0011.1", "QINV-0011.2",
    ]
    assert "Examine Fig. 14(b)." in rebuilt["leaf_cases"][1]["raw_prompt"]
    assert canonical["phase21_hardening"]["inventory_item_count"] == 27
    assert canonical["statistics"]["inventory_leaf_tasks"] == 27
    assert canonical["source_contract"]["inventory_item_count"] == 27
    assert report["phase21_hardening"]["inventory_item_count"] == 27
    assert report["summary"]["inventory_items"] == 27


def test_unverified_decision_never_mutates_source_topology(tmp_path: Path, monkeypatch):
    source = _corrupted_rne()
    compiled = _compile(source)
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"

    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")
    job = SimpleNamespace(
        id=73,
        filename="RNE.pdf",
        mmd_text=source,
        generation_checkpoint={},
        question_inventory={},
        detail="",
    )
    db = SimpleNamespace(commit=lambda: None)

    canonical, report, ready = phase22.adjudicate_job_source(
        db,
        job,
        compiled.canonical,
        compiled.report,
        decision_provider=lambda _packet, _pages: {
            "status": "review_required",
            "reason": "text is not visibly legible",
        },
    )

    assert ready is False
    assert len(canonical["tasks"]) == 25
    assert canonical.get("source_overlays") in (None, [])
    assert canonical["source_adjudication"]["status"] == "review_required"
    assert len(report["phase2_issues"]) == 3


def test_candidate_page_selection_uses_original_pdf_evidence(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    packet = {
        "source_start": 500,
        "search_terms": ["Club of Thinkers", "caricaturist trying to depict"],
        "figure_image_urls": [],
    }

    pages = phase22.collect_evidence_pages(pdf, packet, source_chars=1000)

    assert pages
    assert pages[0].page_number == 3
    assert pages[0].image_data_url.startswith("data:image/jpeg;base64,")
    assert "caricaturist trying to depict" in pages[0].text


def test_verified_cache_avoids_a_second_adjudicator_call(tmp_path: Path, monkeypatch):
    source = _corrupted_rne()
    compiled = _compile(source)
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"

    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", cache_dir)
    calls = 0

    def provider(packet, _pages):
        nonlocal calls
        calls += 1
        return _decision(packet)

    for job_id in (74, 75):
        current = _compile(source)
        job = SimpleNamespace(
            id=job_id,
            filename="RNE.pdf",
            mmd_text=source,
            generation_checkpoint={},
            question_inventory={},
            detail="",
        )
        db = SimpleNamespace(commit=lambda: None)
        canonical, report, ready = phase22.adjudicate_job_source(
            db, job, current.canonical, current.report, decision_provider=provider
        )
        assert ready is True
        assert canonical["source_adjudication"]["status"] == "verified"

    assert calls == 2  # two packets on first run, zero on the cached second run


def test_multimodal_call_uses_source_adjudication_policy(monkeypatch):
    import openai

    calls: list[dict] = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({
                        "verdict": "not_visible",
                        "evidence_id": "EVIDENCE-PAGE-A",
                        "recovered_text": "",
                        "source_label": "",
                        "task_kind": "not_applicable",
                        "insert_before_block_id": "",
                        "confidence": 0.99,
                        "evidence": [],
                    })),
                    finish_reason="stop",
                )]
            )

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna")
    generation._openai_gate = None
    page = phase22.EvidencePage(
        evidence_id="EVIDENCE-PAGE-A",
        page_number=1,
        text="Visible source",
        image_data_url="data:image/jpeg;base64,AA==",
        score=1.0,
    )

    result = phase22._openai_multimodal_json(
        system="system",
        prompt="prompt",
        pages=[page],
        response_schema=phase22._extraction_schema(
            {"allowed_insert_before_block_ids": ["BLK-1"]}, [page]
        ),
        max_tokens=1234,
    )

    assert result["verdict"] == "not_visible"
    call = calls[-1]
    assert call["model"] == "gpt-5.6-luna"
    assert call["reasoning_effort"] == "high"
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["max_completion_tokens"] == 1234
    content = call["messages"][1]["content"]
    assert any(item.get("type") == "image_url" for item in content)
    generation._openai_gate = None


def test_protocol_violation_retries_with_opaque_evidence_id(monkeypatch):
    page = phase22.EvidencePage(
        evidence_id="EVIDENCE-PAGE-A",
        page_number=7,
        text="2 The Making of Nationalism in Europe",
        image_data_url="data:image/jpeg;base64,AA==",
        score=1.0,
    )
    packet = {
        "issue_type": "missing_parent_section",
        "section_number": 2,
        "allowed_insert_before_block_ids": ["BLK-0056"],
    }
    replies = [
        {
            "verdict": "visible_exact",
            "evidence_id": "PDF-PAGE-7",
            "recovered_text": "2 The Making of Nationalism in Europe",
            "source_label": "",
            "insert_before_block_id": "BLK-0056",
            "confidence": 0.999,
            "evidence": [],
        },
        {
            "verdict": "visible_exact",
            "evidence_id": "EVIDENCE-PAGE-A",
            "recovered_text": "2 The Making of Nationalism in Europe",
            "source_label": "",
            "insert_before_block_id": "BLK-0056",
            "confidence": 0.999,
            "evidence": [],
        },
        {
            "verdict": "matches_exactly",
            "recovered_text": "2 The Making of Nationalism in Europe",
            "source_label_matches": True,
            "confidence": 0.999,
            "evidence": [],
        },
    ]
    calls: list[dict] = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return replies.pop(0)

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)

    result = phase22.adjudicate_packet_via_openai(packet, [page])

    assert result["status"] == "verified"
    assert result["decision"]["evidence_id"] == "EVIDENCE-PAGE-A"
    assert len(calls) == 3
    assert "protocol_retry" in calls[1]["prompt"]
    assert calls[2]["pages"] == [page]


def test_review_required_phase22_results_are_not_cached(tmp_path: Path, monkeypatch):
    source = _corrupted_rne()
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"

    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", cache_dir)
    calls = 0

    def provider(_packet, _pages):
        nonlocal calls
        calls += 1
        return {"status": "review_required", "reason": "not visible"}

    for job_id in (90, 91):
        compiled = _compile(source)
        job = SimpleNamespace(
            id=job_id,
            filename="RNE.pdf",
            mmd_text=source,
            generation_checkpoint={},
            question_inventory={},
            detail="",
        )
        db = SimpleNamespace(commit=lambda: None)
        _canonical, _report, ready = phase22.adjudicate_job_source(
            db,
            job,
            compiled.canonical,
            compiled.report,
            decision_provider=provider,
        )
        assert ready is False

    assert calls == 4
    assert list(cache_dir.glob("*.json")) == []


def test_phase221_rejects_out_of_packet_evidence_id_as_protocol_error():
    packet = {
        "issue_type": "missing_parent_section",
        "section_number": 2,
        "allowed_insert_before_block_ids": ["BLK-00056"],
    }
    pages = [phase22.EvidencePage(
        evidence_id="EVIDENCE-PAGE-A",
        page_number=8,
        text="2 The Making of Nationalism in Europe",
        image_data_url="data:image/jpeg;base64,AA==",
        score=1.0,
    )]
    candidate = {
        "verdict": "visible_exact",
        "evidence_id": "8",
        "recovered_text": "2 The Making of Nationalism in Europe",
        "source_label": "",
        "insert_before_block_id": "BLK-00056",
        "confidence": 0.999,
        "evidence": [],
    }

    normalized, reason, protocol_error = phase22._validated_candidate(
        packet, candidate, pages
    )

    assert normalized is None
    assert protocol_error is True
    assert "EVIDENCE-PAGE-A" in reason


def test_review_required_adjudication_result_is_not_cached(tmp_path: Path, monkeypatch):
    source = _corrupted_rne()
    compiled = _compile(source)
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"

    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", cache_dir)
    job = SimpleNamespace(
        id=76,
        filename="RNE.pdf",
        mmd_text=source,
        generation_checkpoint={},
        question_inventory={},
        detail="",
    )
    db = SimpleNamespace(commit=lambda: None)

    _canonical, _report, ready = phase22.adjudicate_job_source(
        db,
        job,
        compiled.canonical,
        compiled.report,
        decision_provider=lambda _packet, _pages: {
            "status": "review_required",
            "reason": "protocol mismatch",
            "protocol_error": True,
        },
    )

    assert ready is False
    assert list(cache_dir.glob("*.json")) == []


def _orphan_candidate(**overrides) -> dict:
    candidate = {
        "verdict": "visible_exact",
        "evidence_id": "EVIDENCE-PAGE-A",
        "recovered_text": "Find out more about the members of this club.",
        "source_label": "Explore",
        "task_kind": "activity",
        "insert_before_block_id": "BLK-00102",
        "confidence": 0.999,
        "evidence": [],
    }
    candidate.update(overrides)
    return candidate


def _orphan_packet() -> dict:
    return {
        "issue_type": "orphan_figure_task",
        "figure_caption": "Fig. 6 - The Club of Thinkers",
        "allowed_insert_before_block_ids": ["BLK-00102"],
    }


def _evidence_page() -> phase22.EvidencePage:
    return phase22.EvidencePage(
        evidence_id="EVIDENCE-PAGE-A",
        page_number=9,
        text="Explore\nFind out more about the members of this club.",
        image_data_url="data:image/jpeg;base64,AA==",
        score=1.0,
    )


def test_is_task_like_no_longer_vetoes_a_page_verified_recovery():
    """§3 purge, item 4C: the keyword imperative list must not veto a
    recovery the page verifier is about to judge — even wording the list
    never anticipated ('Ponder over this map with a friend.') is validated."""
    candidate = _orphan_candidate(
        recovered_text="Ponder over this map with a friend.",
    )

    normalized, reason, protocol_error = phase22._validated_candidate(
        _orphan_packet(), candidate, [_evidence_page()]
    )

    assert reason == ""
    assert protocol_error is False
    assert normalized is not None
    assert normalized["recovered_text"] == "Ponder over this map with a friend."


def test_unknown_source_labels_are_preserved_verbatim():
    """§3 purge, item 4D: `_SOURCE_LABELS` used to blank any cue outside its
    six-word vocabulary; the visible label now survives verbatim."""
    assert phase22._normalize_source_label("Explore More!") == "Explore More!"
    assert phase22._normalize_source_label("  Let's   Try  ") == "Let's Try"
    assert phase22._normalize_source_label("") == ""

    normalized, reason, _protocol = phase22._validated_candidate(
        _orphan_packet(),
        _orphan_candidate(source_label="Keep the Curiosity Alive"),
        [_evidence_page()],
    )
    assert reason == ""
    assert normalized["source_label"] == "Keep the Curiosity Alive"


def test_sub_floor_extractor_confidence_flags_instead_of_rejecting():
    """6E: numeric confidence floors are review flags, never acceptance
    gates — the author's positive verdict ships flagged."""
    normalized, reason, protocol_error = phase22._validated_candidate(
        _orphan_packet(),
        _orphan_candidate(confidence=0.42),
        [_evidence_page()],
    )

    assert reason == ""
    assert protocol_error is False
    assert normalized is not None
    assert any("evidence floor" in flag for flag in normalized["review_flags"])


def test_unruled_task_kind_ships_neutral_and_flagged():
    normalized, reason, _protocol = phase22._validated_candidate(
        _orphan_packet(),
        _orphan_candidate(task_kind=""),
        [_evidence_page()],
    )

    assert reason == ""
    assert normalized["task_kind"] == "checkpoint_question"
    assert any("kind" in flag for flag in normalized["review_flags"])


def test_verifier_dissent_flags_the_decision_instead_of_rejecting(monkeypatch):
    """R2/6E: the independent verifier is an auditor, never a judge — its
    dissent becomes a review flag on the shipped decision."""
    page = _evidence_page()
    candidate = _orphan_candidate(
        page_number=page.page_number,
        text_layer_match=True,
    )

    def dissenting(**_kwargs):
        return {
            "verdict": "does_not_match",
            "recovered_text": "Entirely different wording.",
            "source_label_matches": False,
            "task_kind_matches": False,
            "confidence": 0.31,
            "evidence": [],
        }

    monkeypatch.setattr(phase22, "_openai_multimodal_json", dissenting)
    accepted, reason = phase22._verify_candidate(
        _orphan_packet(), candidate, [page]
    )

    assert reason == ""
    assert accepted is not None
    # The author's transcription ships, carrying every dissent as a flag.
    assert accepted["recovered_text"] == candidate["recovered_text"]
    flags = "\n".join(accepted["review_flags"])
    assert "does_not_match" in flags
    assert "source label" in flags
    assert "task" in flags and "kind" in flags
    assert "disagree" in flags
    assert "evidence floor" in flags
    assert accepted["verification"]["verdict"] == "does_not_match"


def test_verifier_missing_evidence_page_stays_fail_closed(monkeypatch):
    monkeypatch.setattr(
        phase22,
        "_openai_multimodal_json",
        lambda **_kwargs: pytest.fail("no call may happen without a page"),
    )
    accepted, reason = phase22._verify_candidate(
        _orphan_packet(),
        _orphan_candidate(evidence_id="EVIDENCE-PAGE-Z"),
        [_evidence_page()],
    )

    assert accepted is None
    assert "unavailable" in reason


def test_adjudicated_task_kind_drives_source_kind_and_verbatim_label(
    tmp_path: Path, monkeypatch,
):
    """The model's task_kind verdict — not the label wording — decides
    source_kind/activity_origin, and the visible label ships verbatim."""
    source = _corrupted_rne()
    compiled = _compile(source)
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"

    from app.services import uploads

    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(uploads, "source_artifact_directory", lambda _job_id: artifact_dir)
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")

    def decision(packet: dict) -> dict:
        value = _decision(packet)
        if packet["issue_type"] == "orphan_figure_task":
            value["decision"]["source_label"] = "Wonder Why"
            value["decision"]["task_kind"] = "activity"
        return value

    job = SimpleNamespace(
        id=77,
        filename="RNE.pdf",
        mmd_text=source,
        generation_checkpoint={},
        question_inventory={},
        detail="",
    )
    db = SimpleNamespace(commit=lambda: None)
    canonical, _report, ready = phase22.adjudicate_job_source(
        db,
        job,
        compiled.canonical,
        compiled.report,
        decision_provider=lambda packet, _pages: decision(packet),
    )

    assert ready is True
    club = next(
        task for task in canonical["tasks"]
        if "caricaturist trying to depict" in task["raw_prompt"]
    )
    assert club["source_label"] == "Wonder Why"
    assert club["source_kind"] == "activity"
    assert club["activity_origin"] is True
    semantic = phase22.semantic_source(canonical, source)
    assert "\\section*{Wonder Why}" in semantic


def test_verifier_is_fixed_to_one_page_and_requires_source_label(monkeypatch):
    packet = {
        "issue_type": "orphan_figure_task",
        "figure_caption": "Fig. 6 - The Club of Thinkers",
        "allowed_insert_before_block_ids": ["BLK-00102"],
    }
    page = phase22.EvidencePage(
        evidence_id="EVIDENCE-PAGE-A",
        page_number=9,
        text="Discuss\nWhat is the caricaturist trying to depict?",
        image_data_url="data:image/jpeg;base64,AA==",
        score=1.0,
    )
    candidate = {
        "verdict": "visible_exact",
        "evidence_id": page.evidence_id,
        "page_number": page.page_number,
        "recovered_text": "What is the caricaturist trying to depict?",
        "source_label": "Discuss",
        "insert_before_block_id": "BLK-00102",
        "confidence": 0.999,
        "text_layer_match": True,
    }
    seen: dict = {}

    def response(**kwargs):
        seen.update(kwargs)
        return {
            "verdict": "matches_exactly",
            "recovered_text": candidate["recovered_text"],
            "source_label_matches": True,
            "confidence": 0.999,
            "evidence": [],
        }

    monkeypatch.setattr(phase22, "_openai_multimodal_json", response)
    accepted, reason = phase22._verify_candidate(packet, candidate, [page])

    assert reason == ""
    assert accepted is not None
    assert seen["pages"] == [page]
    assert "insert_before_block_id" not in seen["response_schema"]["schema"]["properties"]
