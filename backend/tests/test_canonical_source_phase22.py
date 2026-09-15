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
    # The current frozen profile governs even if an old deployment setting
    # still names Luna. Source adjudication uses mini with complete evidence.
    assert call["model"] == "gpt-5.6-luna"
    # Default profile uniform-xhigh (register Q31).
    assert call["reasoning_effort"] == "xhigh"
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


# --------------------------------------------------------------------------- #
# The verified page ledger: a recorded page instead of a scored guess.
#
# Phase 2.2 pays for up to three PDF page images per packet, twice (author,
# then independent verifier). Sending the wrong three is not a defect the
# model can see through — it answers "not visible", honestly, and the packet
# comes back unrepaired after a full paid round. The page ledger written at
# conversion already records which page every transcribed block came from, so
# where it applies the page is a fact, not a guess. These tests hold the
# boundary between the two: the ledger must win where it speaks, must stay
# silent where it cannot be trusted, and must never quietly become the
# scorer's answer wearing a recorded fact's label.
# --------------------------------------------------------------------------- #


def _ledger_block(reading_order: int, *, kind: str = "paragraph", **extra) -> dict:
    block = {
        "reading_order": reading_order,
        "kind": kind,
        "bbox": [0, 0, 100, 100],
        "text": "",
        "heading_level": 0,
        "source_label": "",
        "latex": "",
        "table_rows": [],
        "linked_visual_orders": [],
        "linked_context_orders": [],
        "caption": "",
        "confidence": 0.999,
    }
    block.update(extra)
    return block


def _write_page_ledger(
    artifact_dir: Path,
    *,
    pdf_sha256: str,
    pages: list[dict],
    source_origin: str | None = None,
) -> Path:
    """Write a page bundle exactly where a converted job's own bundle sits."""
    from app.services import canonical_source_phase221_fallback as fallback

    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / fallback.GPT_PAGE_ACSD_FILENAME
    path.write_text(
        json.dumps({
            "schema_version": fallback.PAGE_ACSD_SCHEMA_VERSION,
            "source_origin": (
                fallback.FALLBACK_ORIGIN if source_origin is None else source_origin
            ),
            "pdf_sha256": pdf_sha256,
            "pages": pages,
        }),
        encoding="utf-8",
    )
    return path


def test_the_page_ledger_figure_reference_beats_the_scorers_decoy_page(
    tmp_path: Path,
):
    """An orphan figure's own pinned asset names its page; keywords guess it.

    The url on the packet was minted from the very ledger block that holds
    the picture, so this is an identity join. The scorer, given the same
    packet, lands on the page whose words look most like the caption — here
    a different page entirely. The whole point of the packet is the task
    printed BESIDE that figure, so a wrong page spends both calls on pages
    that cannot contain the answer.
    """
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    packet = {
        "source_start": 500,
        "search_terms": ["caricaturist trying to depict"],
        "figure_image_urls": ["https://aegis.example/source-assets/9/fig.jpg"],
    }
    _write_page_ledger(
        tmp_path / "artifacts",
        pdf_sha256=phase22._pdf_sha256(pdf),
        pages=[
            {
                "page_id": "PDF-PAGE-0001",
                "page_number": 1,
                "blocks": [_ledger_block(
                    1,
                    kind="figure",
                    asset_url="https://aegis.example/source-assets/9/fig.jpg",
                )],
            },
            {"page_id": "PDF-PAGE-0003", "page_number": 3, "blocks": []},
        ],
    )
    ledger = phase22.load_page_ledger(
        tmp_path / "artifacts",
        pdf_sha256=phase22._pdf_sha256(pdf),
        is_pdf=True,
    )

    assert ledger is not None
    resolved = phase22.resolve_packet_pdf_page(
        packet, bundle=ledger, offset_spans=None
    )
    assert resolved == 1

    # The decoy: what the scorer alone would have sent.
    scored = phase22.collect_evidence_pages(pdf, packet, source_chars=1000)
    assert scored[0].page_number == 3
    assert scored[0].selection == "scored"

    led = phase22.collect_evidence_pages(
        pdf, packet, source_chars=1000, pdf_page=resolved, selection="ledger"
    )
    assert led[0].page_number == 1
    assert [page.selection for page in led] == ["ledger"] * len(led)


def test_an_offset_span_places_a_packet_on_the_page_that_printed_it(
    tmp_path: Path, monkeypatch,
):
    """The second route, proven against a real converted bundle.

    A heading packet carries no figure, so its only ledger evidence is its
    ``source_start`` — an offset into the MMD the ledger itself renders to.
    This builds a genuine conversion (bundle, rendered MMD and compiled
    canonical all from one run) and asserts every body block resolves to the
    page it was actually transcribed from, not to a page that merely mentions
    similar words.
    """
    from tests.test_canonical_source_phase221_fallback import (
        _make_pdf as _make_two_page_pdf,
        _verified_provider,
    )

    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    from app.services import canonical_source_phase221_fallback as fallback

    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    pdf = tmp_path / "source.pdf"
    _make_two_page_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=91,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=_verified_provider,
    )
    canonical = result["canonical"]
    ledger = phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    )
    assert ledger is not None

    spans = phase22._ledger_offset_spans(ledger, canonical)
    assert spans, "the ledger renders exactly the compiled source"

    def page_of(needle: str) -> int | None:
        block = next(
            block for block in canonical["blocks"]
            if needle in str(block.get("raw_text") or "")
        )
        return phase22.resolve_packet_pdf_page(
            {"source_start": block["source_start"]},
            bundle=ledger,
            offset_spans=spans,
        )

    assert page_of("# 1 Number Patterns") == 1
    assert page_of("A sequence follows a visible rule.") == 1
    assert page_of("# Activity") == 2
    assert page_of("Describe the pattern shown in Fig. 1.") == 2


def test_a_position_inside_the_mmd_header_resolves_to_no_page_at_all(
    tmp_path: Path, monkeypatch,
):
    """M1: the header belongs to no page, and "page one" would be a guess.

    The rendered MMD opens with three provenance stamps and a blank line —
    a couple of hundred characters that no page block emitted, so their
    spans carry no page id and are filtered out. Without an explicit refusal
    the bisect would fall off the front of the list; with a naive clamp it
    would answer page one, which reads like a recorded fact and is not one.
    """
    from tests.test_canonical_source_phase221_fallback import (
        _make_pdf as _make_two_page_pdf,
        _verified_provider,
    )

    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    from app.services import canonical_source_phase221_fallback as fallback

    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    pdf = tmp_path / "source.pdf"
    _make_two_page_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=92,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=_verified_provider,
    )
    ledger = phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    )
    spans = phase22._ledger_offset_spans(ledger, result["canonical"])
    first_body_offset = spans[0][0]
    assert first_body_offset > 100, "the MMD header is real; this test needs it"

    for position in (0, 1, first_body_offset - 1):
        assert phase22.resolve_packet_pdf_page(
            {"source_start": position}, bundle=ledger, offset_spans=spans
        ) is None

    # And the fallback that refusal buys: the packet still gets evidence,
    # honestly labelled as a guess.
    pages = phase22.collect_evidence_pages(
        pdf, {"source_start": 0, "search_terms": []}, source_chars=1000,
        pdf_page=None, selection="ledger",
    )
    assert pages and [page.selection for page in pages] == ["scored"] * len(pages)


def test_a_ledger_that_no_longer_renders_the_compiled_source_is_refused(
    tmp_path: Path, monkeypatch,
):
    """The offsets are only meaningful while the render IS the source.

    One edited block shifts every offset after it, and a shifted offset
    points at the wrong page with no visible symptom at all. The digest gate
    is the only thing standing between "recorded page" and "confidently
    wrong page", so it must refuse rather than raise: a stale ledger is a
    reason to fall back to the scorer, never a reason to stop a paid run.
    """
    from tests.test_canonical_source_phase221_fallback import (
        _make_pdf as _make_two_page_pdf,
        _verified_provider,
    )

    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    from app.services import canonical_source_phase221_fallback as fallback

    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    pdf = tmp_path / "source.pdf"
    _make_two_page_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=93,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=_verified_provider,
    )
    ledger = phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    )
    canonical = json.loads(json.dumps(result["canonical"]))
    assert phase22._ledger_offset_spans(ledger, canonical) is not None

    canonical["document"]["source_sha256"] = "0" * 64
    assert phase22._ledger_offset_spans(ledger, canonical) is None

    canonical["document"].pop("source_sha256")
    assert phase22._ledger_offset_spans(ledger, canonical) is None

    # An unreadable bundle is a missing bundle, not an exception.
    (artifact_dir / fallback.GPT_PAGE_ACSD_FILENAME).write_text(
        "{not json", encoding="utf-8"
    )
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    ) is None


def test_a_page_ledger_from_a_different_pdf_is_ignored(tmp_path: Path):
    """An artifact directory outlives a re-upload; the digest is the guard.

    A ledger extracted from another file would place packets on confidently
    wrong pages — strictly worse than the scorer's honest guess, because
    nothing downstream would flag it.
    """
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    pages = [{
        "page_id": "PDF-PAGE-0001",
        "page_number": 1,
        "blocks": [_ledger_block(1, kind="figure", asset_url="https://x/fig.jpg")],
    }]
    _write_page_ledger(
        artifact_dir, pdf_sha256="a-different-upload", pages=pages
    )
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    ) is None

    # Same bytes, wrong converter: the shape is not a contract.
    _write_page_ledger(
        artifact_dir,
        pdf_sha256=phase22._pdf_sha256(pdf),
        pages=pages,
        source_origin="some_other_reader",
    )
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    ) is None

    # A non-PDF upload has no page ledger by construction.
    _write_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), pages=pages
    )
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=False
    ) is None
    assert phase22.load_page_ledger(
        tmp_path / "nowhere", pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    ) is None


def test_ledger_evidence_is_the_page_then_its_two_neighbours(tmp_path: Path):
    """[n, n+1, n-1], clamped — and the order is not arbitrary.

    A task can be printed across the page break from the figure that owns
    it, and the continuation runs onto the NEXT page, so that is the first
    neighbour offered. The clamp matters at both ends of a real book: page
    one has no predecessor and the last page no successor, and an
    out-of-range index would raise inside a paid lane.
    """
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)  # three pages
    packet = {"source_start": 0, "search_terms": []}

    def numbers(page: int) -> list[int]:
        return [
            evidence.page_number
            for evidence in phase22.collect_evidence_pages(
                pdf, packet, source_chars=1000, pdf_page=page, selection="ledger"
            )
        ]

    assert numbers(2) == [2, 3, 1]
    assert numbers(1) == [1, 2]
    assert numbers(3) == [3, 2]
    assert len(numbers(2)) <= phase22._MAX_PAGES

    # Out of range is not a page: it falls back rather than raising, and it
    # says "scored", because that is what it then did.
    out_of_range = phase22.collect_evidence_pages(
        pdf, packet, source_chars=1000, pdf_page=99, selection="ledger"
    )
    assert out_of_range
    assert {page.selection for page in out_of_range} == {"scored"}
    assert [page.page_number for page in out_of_range] == [
        page.page_number
        for page in phase22.collect_evidence_pages(pdf, packet, source_chars=1000)
    ]


def test_the_ledger_route_reads_no_more_page_text_than_it_sends(
    tmp_path: Path, monkeypatch,
):
    """The saving is real only if the scorer's whole-document read is skipped.

    Extracting the text layer of every page is most of what the scorer costs
    on a real book, and it is pure waste once the page is already known. The
    memoized reader must also never extract the same page twice: selection
    and the evidence payload both want that text.
    """
    import fitz

    calls: list[int] = []

    class CountingPage:
        def __init__(self, inner, index):
            self._inner = inner
            self._index = index

        def get_text(self, *args, **kwargs):
            calls.append(self._index)
            return self._inner.get_text(*args, **kwargs)

        def get_pixmap(self, *args, **kwargs):
            return self._inner.get_pixmap(*args, **kwargs)

    class CountingDocument:
        def __init__(self, inner):
            self._inner = inner

        @property
        def page_count(self):
            return self._inner.page_count

        def __getitem__(self, index):
            return CountingPage(self._inner[index], index)

        def close(self):
            self._inner.close()

    # A book, not a leaflet: with three pages a whole-document read and a
    # three-page read cost the same and the bound below would prove nothing.
    pdf = tmp_path / "source.pdf"
    document = fitz.open()
    for number in range(1, 13):
        page = document.new_page(width=595, height=842)
        page.insert_text((60, 90), f"Page {number} of the chapter")
    document.save(pdf)
    document.close()
    assert phase22._MAX_PAGES < 12
    packet = {"source_start": 0, "search_terms": []}
    real_open = fitz.open
    monkeypatch.setattr(
        fitz, "open", lambda *a, **k: CountingDocument(real_open(*a, **k))
    )

    pages = phase22.collect_evidence_pages(
        pdf, packet, source_chars=1000, pdf_page=7, selection="ledger"
    )
    assert [page.page_number for page in pages] == [7, 8, 6]
    assert len(calls) <= phase22._MAX_PAGES
    assert len(calls) == len(set(calls)), "each page's text layer is read once"

    # The scorer, by contrast, must still read the whole document — it has
    # nothing else to go on, and this is the cost the ledger route avoids.
    calls.clear()
    phase22.collect_evidence_pages(pdf, packet, source_chars=1000)
    assert len(set(calls)) == 12


def test_an_orphan_figure_packet_keeps_a_search_term_that_survives_the_filter(
    tmp_path: Path,
):
    """M4: the caption is the search term, whole.

    ``_candidate_page_numbers`` drops any term under eight normalized
    characters. Splitting the caption at its first period yields "Fig" (three)
    or "Figure 7" (eight, and a bare locator that matches every page of the
    chapter), so the caption-derived term was unusable on its own. On THIS
    fixture the packet was not left with nothing only because a hardcoded
    "Club of Thinkers" sat beside it — this chapter's own caption text, in
    production code — and that literal is what a Rule 1 keyword vocabulary
    looks like: it rescues the one chapter it was copied from and no other,
    where every other chapter's figure packet collapsed to a pure position
    guess. Both halves are asserted below. The whole caption is what the book
    prints beside the figure, so it is also what a page's text layer actually
    contains.
    """
    compiled = _compile(_corrupted_rne())
    packet = next(
        item for item in compiled.canonical["source_adjudication"]["packets"]
        if item["issue_type"] == "orphan_figure_task"
    )

    assert packet["figure_caption"].startswith("Fig. 6 - ")
    assert packet["search_terms"] == [packet["figure_caption"]]
    surviving = [
        term for term in packet["search_terms"]
        if len(phase22._normal(term)) >= 8
    ]
    assert surviving, "the packet must reach the scorer with something to score"
    assert phase22._normal(packet["figure_caption"].split(".", 1)[0]) == "fig"

    # The superseded pair, scored exactly as the filter scored it: the
    # caption half was dropped, and the ONLY survivor was the literal.
    superseded = [packet["figure_caption"].split(".", 1)[0], "Club of Thinkers"]
    assert [
        term for term in superseded if len(phase22._normal(term)) >= 8
    ] == ["Club of Thinkers"]

    # And no chapter-specific literal rides along any more: a keyword
    # vocabulary that classifies content is exactly what Rule 1 forbids.
    assert not any(
        "club of thinkers" == phase22._normal(term)
        for term in packet["search_terms"]
    )


def test_only_the_orphan_figure_packet_identity_moves(tmp_path: Path):
    """The cost of M4, stated exactly, and bounded to one packet type.

    A packet's fingerprint is its cache key material, so changing
    ``search_terms`` makes every cached orphan-figure decision unreachable —
    a fresh adjudication re-pays two model calls per such packet, at up to
    three page images each. That is a paid model re-read of an
    already-converted PDF's page IMAGES; it is not a reconversion, and no
    conversion cache key moves. The heading packet is pinned here because it
    must not pay anything at all.
    """
    compiled = _compile(_corrupted_rne())
    packets = compiled.canonical["source_adjudication"]["packets"]
    heading = next(p for p in packets if p["issue_type"] == "missing_parent_section")
    figure = next(p for p in packets if p["issue_type"] == "orphan_figure_task")

    assert heading["issue_id"] == "ADJ-SECTION-2-1fdddb133f"
    assert heading["fingerprint"] == (
        "1fdddb133f64f7dc753bc9808a4d58f9092b49bc9df1b5095272fe66d8168539"
    )
    assert figure["issue_id"] == "ADJ-FIGURE-FIG-00007-d1b8b25184"

    # The identity this replaced, recomputed from the old search terms so the
    # move is visible rather than asserted.
    superseded = {
        key: value for key, value in figure.items()
        if key not in {"fingerprint", "issue_id"}
    }
    superseded["search_terms"] = [
        figure["figure_caption"].split(".", 1)[0], "Club of Thinkers",
    ]
    assert phase22._packet_fingerprint(superseded) != figure["fingerprint"]
    assert phase22._packet_fingerprint(superseded).startswith("4bfd28caf6")

    # Page selection is not identity: it is deliberately excluded from the
    # fingerprint, so a ledger-selected page can never invalidate a cache
    # entry a scored page paid for.
    with_pages = dict(figure)
    with_pages["candidate_pages"] = [1, 2, 3]
    assert phase22._packet_fingerprint({
        key: value for key, value in with_pages.items()
        if key not in {"fingerprint", "issue_id"}
    }) == figure["fingerprint"]


def test_the_selected_pages_never_enter_the_adjudication_cache_key(
    tmp_path: Path, monkeypatch,
):
    """One cache directory, two different page selections, zero second calls.

    The ledger changes WHICH pages the adjudicator is shown; it must not
    change what a decision is keyed by, or every job that gains a ledger
    would silently re-pay for work it already has. The two runs share a
    cache directory on purpose — with a per-run directory the zero-call
    assertion proves nothing at all.
    """
    from app.services import uploads

    source = _corrupted_rne()
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(phase22, "_CACHE_DIR", cache_dir)

    figure_url = next(
        packet["figure_image_urls"][0]
        for packet in _compile(source).canonical["source_adjudication"]["packets"]
        if packet["issue_type"] == "orphan_figure_task"
    )
    with_ledger = tmp_path / "with-ledger"
    _write_page_ledger(
        with_ledger,
        pdf_sha256=phase22._pdf_sha256(pdf),
        pages=[
            {"page_id": "PDF-PAGE-0001", "page_number": 1, "blocks": []},
            {
                "page_id": "PDF-PAGE-0002",
                "page_number": 2,
                "blocks": [_ledger_block(
                    1, kind="figure", asset_url=figure_url,
                )],
            },
        ],
    )

    seen: list[tuple[str, str, int]] = []
    calls = 0

    def provider(packet, pages):
        nonlocal calls
        calls += 1
        seen.append((packet["issue_type"], pages[0].selection, pages[0].page_number))
        return _decision(packet)

    for run, artifacts in enumerate((with_ledger, tmp_path / "no-ledger")):
        monkeypatch.setattr(
            uploads, "source_artifact_directory", lambda _job_id, d=artifacts: d
        )
        compiled = _compile(source)
        job = SimpleNamespace(
            id=180 + run, filename="RNE.pdf", mmd_text=source,
            generation_checkpoint={}, question_inventory={}, detail="",
        )
        canonical, _report, ready = phase22.adjudicate_job_source(
            SimpleNamespace(commit=lambda: None), job,
            compiled.canonical, compiled.report, decision_provider=provider,
        )
        assert ready is True

    # The first run's figure packet was placed by the ledger; nothing in the
    # second run was. Same decisions, same keys, no second purchase.
    assert calls == 2
    assert ("orphan_figure_task", "ledger", 2) in seen
    assert not any(state == "ledger" for _type, state, _page in seen[2:])


def test_a_sealed_adjudicated_canonical_is_never_re_adjudicated(
    tmp_path: Path, monkeypatch,
):
    """A repaired run pays nothing for a moved packet identity.

    Only a canonical that still carries eligible unresolved issues builds
    packets at all, so the re-pay above is bounded to sources that were
    never repaired. A sealed one does not reach the provider, the ledger,
    or the PDF.
    """
    from app.services import uploads

    source = _corrupted_rne()
    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: tmp_path / "artifacts"
    )
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")
    compiled = _compile(source)
    job = SimpleNamespace(
        id=181, filename="RNE.pdf", mmd_text=source,
        generation_checkpoint={}, question_inventory={}, detail="",
    )
    canonical, report, ready = phase22.adjudicate_job_source(
        SimpleNamespace(commit=lambda: None), job,
        compiled.canonical, compiled.report,
        decision_provider=lambda packet, _pages: _decision(packet),
    )
    assert ready is True

    def refuse(*_args, **_kwargs):
        raise AssertionError("a sealed canonical must not be adjudicated again")

    monkeypatch.setattr(phase22, "load_page_ledger", refuse)
    monkeypatch.setattr(phase22, "collect_evidence_pages", refuse)
    again, _report, ready = phase22.adjudicate_job_source(
        SimpleNamespace(commit=lambda: None), job, canonical, report,
        decision_provider=refuse,
    )
    assert ready is True
    assert again["source_adjudication"]["status"] == "verified"


def test_one_asset_url_on_several_pages_does_not_become_a_recorded_page(
    tmp_path: Path,
):
    """A content-addressed asset names a PICTURE, not a page.

    ``canonical_source_phase221_fallback`` mints a crop's filename as
    ``<sha256 of the jpeg bytes>.jpg``, so a crop that renders byte-identical
    on several pages — a mascot or an icon reprinted beside every activity,
    which is exactly the lower-grade book the project's own rules say
    shortcuts hurt most — carries ONE url on all of them. Returning the first
    page in bundle order would ship a guess under the ledger's label, past
    the digest-verified offset span that answers for this packet's own
    position. The ambiguous join must decline instead.
    """
    shared = "https://aegis.example/source-assets/9/deadbeef.jpg"
    unique = "https://aegis.example/source-assets/9/cafef00d.jpg"
    bundle = {
        "source_origin": "gpt_pdf_acsd_fallback",
        "pdf_sha256": "irrelevant-here",
        "pages": [
            {
                "page_id": "PDF-PAGE-0001",
                "page_number": 1,
                "blocks": [_ledger_block(1, kind="figure", asset_url=shared)],
            },
            {
                "page_id": "PDF-PAGE-0007",
                "page_number": 7,
                "blocks": [
                    _ledger_block(1, kind="figure", asset_url=shared),
                    _ledger_block(2, kind="figure", asset_url=unique),
                ],
            },
        ],
    }
    # The offset spans say, with the digest behind them, that this packet's
    # own text was printed on page 7.
    spans = [(0, 1), (4000, 7)]
    repeated = {"source_start": 5000, "figure_image_urls": [shared]}

    assert phase22.resolve_packet_pdf_page(
        repeated, bundle=bundle, offset_spans=spans
    ) == 7

    # With nothing to fall through to, it refuses rather than picking one.
    assert phase22.resolve_packet_pdf_page(
        repeated, bundle=bundle, offset_spans=None
    ) is None

    # A picture that really is on one page still resolves by identity, and
    # still beats a position that would have said otherwise.
    assert phase22.resolve_packet_pdf_page(
        {"source_start": 0, "figure_image_urls": [unique]},
        bundle=bundle,
        offset_spans=spans,
    ) == 7

    # A figure whose asset never materialized joins nothing and falls
    # through, rather than matching the first page that has any asset at all.
    assert phase22.resolve_packet_pdf_page(
        {"source_start": 5000, "figure_image_urls": ["https://aegis.example/x/none.jpg"]},
        bundle=bundle,
        offset_spans=spans,
    ) == 7


def test_the_kept_neighbour_is_the_one_the_packet_is_looking_towards(
    tmp_path: Path, monkeypatch,
):
    """The two packet kinds look in opposite directions from their anchor.

    An orphan-figure packet is anchored on the figure and wants the task
    printed beside it, which runs on to the NEXT page. A
    ``missing_parent_section`` packet is anchored on its FIRST SUBSECTION —
    ``min(source_start)`` over the subsection blocks — and wants the parent
    heading, which the book printed BEFORE that, so its likelier neighbour is
    the PREVIOUS page. At the default ``_MAX_PAGES`` both neighbours ship and
    only the order differs; below it the wrong order drops the likelier page
    outright.
    """
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)  # three pages

    def numbers(packet: dict) -> list[int]:
        return [
            evidence.page_number
            for evidence in phase22.collect_evidence_pages(
                pdf, packet, source_chars=1000, pdf_page=2, selection="ledger"
            )
        ]

    figure = {"issue_type": "orphan_figure_task", "source_start": 0, "search_terms": []}
    heading = {"issue_type": "missing_parent_section", "source_start": 0, "search_terms": []}

    assert numbers(figure) == [2, 3, 1]
    assert numbers(heading) == [2, 1, 3]

    # The window is the page-break argument, so it is never wider than the
    # anchor and its two immediate neighbours, whatever the budget says.
    monkeypatch.setattr(phase22, "_MAX_PAGES", 5)
    assert numbers(figure) == [2, 3, 1]

    # A budget below three can only shrink it, and then the direction is
    # what decides which page the packet keeps.
    monkeypatch.setattr(phase22, "_MAX_PAGES", 2)
    assert numbers(figure) == [2, 3]
    assert numbers(heading) == [2, 1]


def test_a_page_ledger_present_and_refused_says_so(tmp_path: Path, monkeypatch):
    """A refused bundle must not read like a job that never had one.

    A missing ledger is the ordinary case and says nothing. A ledger that is
    sitting on disk and declined — unreadable, written by another reader, or
    extracted from a different upload of the same filename — silently becomes
    the positional scorer, which is indistinguishable from never having had a
    ledger at all. That is the shape of a degradation nobody looks for.
    """
    from app.services import canonical_source_phase221_fallback as fallback
    from app.services import uploads

    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    digest = phase22._pdf_sha256(pdf)
    artifact_dir = tmp_path / "artifacts"
    pages = [{
        "page_id": "PDF-PAGE-0001",
        "page_number": 1,
        "blocks": [_ledger_block(1, kind="figure", asset_url="https://x/fig.jpg")],
    }]

    refusals: list[str] = []
    collect = refusals.append

    # No file at all: the ordinary case, and silent.
    assert phase22.load_page_ledger(
        tmp_path / "nowhere", pdf_sha256=digest, is_pdf=True, on_refusal=collect
    ) is None
    assert refusals == []

    _write_page_ledger(artifact_dir, pdf_sha256="another-upload", pages=pages)
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=digest, is_pdf=True, on_refusal=collect
    ) is None
    assert "different upload" in refusals[-1]

    _write_page_ledger(
        artifact_dir, pdf_sha256=digest, pages=pages, source_origin="some_other_reader"
    )
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=digest, is_pdf=True, on_refusal=collect
    ) is None
    assert "some_other_reader" in refusals[-1]

    (artifact_dir / fallback.GPT_PAGE_ACSD_FILENAME).write_text(
        "{not json", encoding="utf-8"
    )
    assert phase22.load_page_ledger(
        artifact_dir, pdf_sha256=digest, is_pdf=True, on_refusal=collect
    ) is None
    assert "could not be read" in refusals[-1]
    assert len(refusals) == 3
    assert all(fallback.GPT_PAGE_ACSD_FILENAME in message for message in refusals)

    # And the run itself says it, rather than quietly scoring.
    _write_page_ledger(artifact_dir, pdf_sha256="another-upload", pages=pages)
    logs: list[str] = []
    monkeypatch.setattr(
        phase22.progress, "log", lambda message, **_kw: logs.append(str(message))
    )
    monkeypatch.setattr(phase22.progress, "step", lambda *_a, **_kw: None)
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: artifact_dir
    )
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")
    compiled = _compile(_corrupted_rne())
    job = SimpleNamespace(
        id=182, filename="RNE.pdf", mmd_text=_corrupted_rne(),
        generation_checkpoint={}, question_inventory={}, detail="",
    )
    phase22.adjudicate_job_source(
        SimpleNamespace(commit=lambda: None), job,
        compiled.canonical, compiled.report,
        decision_provider=lambda packet, _pages: _decision(packet),
    )
    assert any(
        "present but not usable" in message and "different upload" in message
        for message in logs
    ), logs


def test_a_ledger_that_cannot_place_by_offset_says_which_half_it_lost(
    tmp_path: Path, monkeypatch,
):
    """"Placed 0 of 2" is a count, not a reason.

    A bundle can pass every load check — right converter, right upload — and
    still fail the digest gate in ``_ledger_offset_spans``, which is the only
    thing standing between a recorded page and a confidently wrong one. When
    that happens the offset route is gone entirely and only a packet carrying
    its own pinned figure can still be placed, so the run must say that
    rather than report a bare zero that reads like a ledger with nothing to
    contribute.
    """
    from app.services import uploads

    pdf = tmp_path / "RNE.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "artifacts"
    # A well-formed bundle for exactly this upload whose render is not the
    # compiled RNE source — the shape the gate exists to catch.
    _write_page_ledger(
        artifact_dir,
        pdf_sha256=phase22._pdf_sha256(pdf),
        pages=[{"page_id": "PDF-PAGE-0001", "page_number": 1, "blocks": []}],
    )
    ledger = phase22.load_page_ledger(
        artifact_dir, pdf_sha256=phase22._pdf_sha256(pdf), is_pdf=True
    )
    assert ledger is not None, "this test needs a bundle that LOADS"

    logs: list[str] = []
    monkeypatch.setattr(
        phase22.progress, "log", lambda message, **_kw: logs.append(str(message))
    )
    monkeypatch.setattr(phase22.progress, "step", lambda *_a, **_kw: None)
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(
        uploads, "source_artifact_directory", lambda _job_id: artifact_dir
    )
    monkeypatch.setattr(phase22, "_CACHE_DIR", tmp_path / "cache")
    source = _corrupted_rne()
    compiled = _compile(source)
    job = SimpleNamespace(
        id=183, filename="RNE.pdf", mmd_text=source,
        generation_checkpoint={}, question_inventory={}, detail="",
    )
    phase22.adjudicate_job_source(
        SimpleNamespace(commit=lambda: None), job,
        compiled.canonical, compiled.report,
        decision_provider=lambda packet, _pages: _decision(packet),
    )

    assert any("no longer renders the compiled source" in m for m in logs), logs
    # The bare count still rides along; it is the reason that was missing.
    assert any("placed 0 of 2" in m for m in logs), logs
