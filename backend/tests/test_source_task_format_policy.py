"""Source layout adaptation is frozen upstream and remains source-only.

Offline examples exercise actual primary-textbook task forms: ranking plants,
S/G number-pair boxes, and W/D/P picture classification. Scripted authors test
transport/identity/replay, not the model's semantic quality on the full books.
"""
from __future__ import annotations

import copy
import json
from types import ModuleType

import pytest

from app import config
from app.services import assessment_item_review as item_review
from app.services import assessment_materialization as materialization
from app.services import assessment_release_run as release_run
from app.services import assessment_source_inventory as source_inventory
from app.services import generation, question_polishing
from app.services import question_polishing_contract
from app.services import source_task_polishing_policy as policy
from app.services.phase3 import kernel
from tests.test_assessment_release_run import OWNER, _chapter_with_concepts, _make_job
from tests.test_assessment_visual_evidence import _pinned
from tests.test_mes_materialization import _cell, _descriptive_response


@pytest.fixture
def polish_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()
    yield
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()


def _polish(source, wording, *, calls=None, issues=None):
    def api(system, user, **kwargs):
        request = json.loads(user)
        if calls is not None:
            calls.append((system, request, kwargs))
        if kwargs["purpose"] == "advisory_critic":
            return {"items": [{"qid": source["qid"],
                              "verdict": "dissent" if issues else "verified",
                              "issues": issues or []}]}
        return {"items": [{"qid": source["qid"], "polished_task": wording,
                          "note": "Source response layout made explicit."}]}

    return question_polishing.polish_inventory(
        {"items": [source]}, meta={"subject": "Mathematics", "grade": "1"},
        api_call=api,
    )["items"][0]


def test_number_pair_boxes_keep_operands_order_and_frozen_authority(polish_state):
    pairs = [[31, 33], [42, 40], [25, 45], [47, 36], [39, 40],
             [25, 23], [25, 27], [30, 40], [31, 35]]
    raw = "Write S for the smaller number and G for the greater number in the boxes."
    polished = raw + "<br>" + "<br>".join(f"{a} [ ]    {b} [ ]" for a, b in pairs)
    source = {
        "qid": "QINV-0002", "raw_task": raw, "normalized_task": raw,
        "source_kind": "exercise", "source_label": "2", "page_hint": "27",
        "block_ids": ["BLK-0027"],
        "tables": [{"columns": ["Number", "Box", "Number", "Box"],
                    "rows": [[a, "", b, ""] for a, b in pairs]}],
    }
    original = copy.deepcopy(source)
    calls = []
    item = _polish(source, polished, calls=calls)
    atom = source_inventory.source_atom_from_item(item, source_document_hash="doc-sha")
    assert source == original
    assert item["raw_task"] == item["normalized_task"] == raw
    assert atom["raw_text"] == atom["normalized_source_text"] == raw
    assert atom["frozen_task_text"] == atom["normalized_public_text"] == polished
    assert atom["source_qid"] == source["qid"]
    assert atom["block_ids"] == source["block_ids"]
    assert atom["tables"] == source["tables"]
    assert [c[2]["purpose"] for c in calls] == ["source_extraction", "advisory_critic"]
    assert calls[0][1]["questions"][0]["source_evidence"]["tables"] == source["tables"]
    assert calls[1][1]["questions"][0]["proposed_task"] == polished
    request = materialization._decision_payload(
        atom, _cell(), candidate_id="C1", meta={}, context={},
        descriptive_answer_capacity=30,
    )
    assert request["source_wording_authority"]["authoritative_field"] == "source_atom.frozen_task_text"
    assert request["source_wording_authority"]["raw_text"] == raw
    assert request["rules"] == materialization.SOURCE_FORMAT_MATERIALIZE_SYSTEM
    assert "new response demands" in request["rules"]
    # Mining must read the same frozen task even if an incidental derived
    # display field is later stale; source audit fields remain unchanged.
    item["polished_task"] = "An obsolete display value"
    assert "An obsolete display value" not in generation._inventory_task_text(item)
    assert "31" in generation._inventory_task_text(item)


def test_picture_tasks_and_structured_choices_reach_both_polish_calls(polish_state, monkeypatch, tmp_path):
    url, _ = _pinned(monkeypatch, tmp_path)
    source = {
        "qid": "QINV-0004", "source_kind": "exercise",
        "raw_task": "Write W for wild, D for domestic and P for pet animals in the boxes.",
        "options": {"first_picture": {"image_url": url, "response_box": ""}},
        "image_urls": [url], "image_manifest": [{"url": url, "owner": "QINV-0004"}],
        "content_objects": {"pictures": [{"url": url, "box": ""}]},
        "sub_questions": [{"part": "a", "image_urls": [url]}],
    }
    calls = []
    item = _polish(source, source["raw_task"], calls=calls)
    for _, request, kwargs in calls:
        assert kwargs["image_urls"] and kwargs["image_urls"][0].startswith("data:image/jpeg;base64,")
        row = request["questions"][0]
        assert row["options"] == source["options"]
        assert row["source_evidence"]["sub_questions"] == source["sub_questions"]
        assert request["visual_evidence"]["images"][0]["state"] == "attached"
    assert item["frozen_task_text"] == source["raw_task"]
    assert item["polish_review_required"] is False


@pytest.mark.parametrize("version", [3, 4])
def test_recorded_polishing_is_never_reauthored_or_upgraded(polish_state, version):
    item = {"qid": "QINV-1", "raw_task": "Tick the box.",
            "polished_task": "Recorded shipping wording.", "source_kind": "exercise",
            "polish_audit": {"version": version, "critic": {"verdict": "verified"}}}
    if version == 4:
        item.update({policy.FIELD: policy.VERSION, "frozen_task_text": item["polished_task"]})
    source = {"items": [item]}
    def forbidden(*args, **kwargs):
        raise AssertionError("A recorded source polish cannot spend or change wording")
    assert question_polishing.polish_inventory(source, api_call=forbidden) == source
    atom = source_inventory.source_atom_from_item(item, source_document_hash="doc")
    authority = materialization._source_wording_authority(atom)
    expected = "source_atom.frozen_task_text" if version == 4 else "source_atom.raw_text"
    assert authority["authoritative_field"] == expected


def test_source_format_requires_explicit_frozen_wording():
    with pytest.raises(source_inventory.SourceInventoryError, match="without frozen wording"):
        source_inventory.source_atom_from_item({
            "qid": "QINV-1", "raw_task": "Tick the box.", policy.FIELD: policy.VERSION,
        }, source_document_hash="doc")


def test_early_inventory_join_polishes_before_checkpoint_and_inline_does_not_repeat(polish_state, monkeypatch):
    source = {"qid": "QINV-1", "raw_task": "Tick the box.", "source_kind": "exercise"}
    recorded = []
    def api(system, user, **kwargs):
        recorded.append(kwargs["purpose"])
        if kwargs["purpose"] == "advisory_critic":
            return {"items": [{"qid": "QINV-1", "verdict": "verified", "issues": []}]}
        return {"items": [{"qid": "QINV-1", "polished_task": "Select the box.", "note": ""}]}
    monkeypatch.setattr(generation, "_openai_json", api)
    stub = ModuleType("early_inventory_generation")
    extraction_calls = []
    stub._finish_inventory_with_topics = lambda inventory, anchors, **kwargs: copy.deepcopy(inventory)
    def inline(**kwargs):
        extraction_calls.append(True)
        return stub._finish_inventory_with_topics({"items": [source]}, [], **kwargs)
    stub._extract_question_task_inventory_via_api = inline
    stub._inventory_task_text = lambda item: item["raw_task"]
    stub._refresh_inventory_from_source_anchors = lambda inventory, sections: inventory
    stub._inventory_stats = lambda items: {"count": len(items)}
    question_polishing_contract.install(stub)
    finish = stub._finish_inventory_with_topics
    question_polishing_contract.install(stub)
    assert stub._finish_inventory_with_topics is finish
    # This is the default early-track join, which intentionally never calls
    # the extraction wrapper before the question_inventory checkpoint.
    early = stub._finish_inventory_with_topics(
        {"items": [source]}, [], meta={}, sections=[], records=[],
    )
    assert extraction_calls == []
    assert early["items"][0]["frozen_task_text"] == "Select the box."
    assert early["stats"] == {"count": 1}
    assert recorded == ["source_extraction", "advisory_critic"]
    inline_result = stub._extract_question_task_inventory_via_api(meta={}, sections=[], records=[])
    assert inline_result == early
    assert recorded == ["source_extraction", "advisory_critic"]
    assert generation._finish_inventory_with_topics._question_polishing_installed


def test_frozen_task_reaches_materialization_and_joint_review_with_source_audit(polish_state):
    raw = "Write 1 for the shortest plant and 3 for the tallest plant in each set."
    source = {"qid": "QINV-3", "raw_task": raw, "normalized_task": raw,
              "source_kind": "exercise"}
    item = _polish(source, raw, issues=["The supplied plant pictures are missing."])
    atom = source_inventory.source_atom_from_item(item, source_document_hash="doc")
    requests = []
    def author(request):
        requests.append(copy.deepcopy(request))
        return _descriptive_response(
            request, question=request["source_wording_authority"]["frozen_task_text"],
            requires_visual=False,
        )
    def critic(request):
        assert request["source_wording_authority"] == requests[0]["source_wording_authority"]
        return {"verdict": "verified", "confidence": 1.0, "issues": []}
    cell = _cell(sheet_kind="descriptive", question_category="Short Answer Type (2 Marks)", marks=2)
    store = kernel.DecisionStore()
    kwargs = dict(meta={"subject": "Mathematics", "grade": "1"},
                  envelope_sha256="e" * 64, provider=author, critic=critic, store=store)
    candidate = materialization.materialize_candidate(atom, cell, **kwargs)
    replay = materialization.materialize_candidate(atom, cell, **kwargs)
    assert len(requests) == 1 and replay == candidate
    assert candidate["question"] == candidate["question_text"] == raw
    assert candidate["source_evidence"] == raw
    assert candidate["normalized_source_text"] == raw
    assert candidate["polish_audit"] == atom["polish_audit"]
    assert candidate["assessment_eligibility"] == "flagged"
    assert any("source_task_polishing_review" in flag for flag in candidate["flags"])
    review = item_review._payload(candidate, cell, atom, meta={}, format_policy={})
    assert review["rules"] == item_review.SOURCE_FORMAT_ITEM_REVIEW_SYSTEM
    assert review["source_atom"]["frozen_task_text"] == raw
    assert "changed answer space" in review["rules"]
    # A historical atom retains its exact old prompt and authority version.
    legacy = {"source_qid": "QINV-3", "raw_text": raw, "normalized_public_text": "Derived text"}
    assert materialization._source_wording_authority(legacy)["version"] == "source-master-raw-1"
    assert item_review._payload(candidate, cell, legacy, meta={}, format_policy={})["rules"] == item_review.ITEM_REVIEW_SYSTEM


@pytest.mark.parametrize("generated", [[], [{"pre_question_id": "PRQ-1", "question_text": "Invented"}]])
def test_post_slot_rejects_generated_questions_before_snapshot_or_provider(db, monkeypatch, generated):
    job = _make_job(db, _chapter_with_concepts(db))
    def forbidden(*args, **kwargs):
        raise AssertionError("Post generated-lane refusal must precede source preparation or spending")
    monkeypatch.setattr(release_run.release_snapshot, "build", forbidden)
    with pytest.raises(release_run.ReleaseRunError, match="only from the uploaded source inventory"):
        release_run.run_release_for_job(db, job.id, owner_sub=OWNER,
                                       lane="post", generated_questions=generated)
