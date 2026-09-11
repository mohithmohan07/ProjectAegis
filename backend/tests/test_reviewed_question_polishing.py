"""Q51 Step 2: reviewed Post questions are polished once, with the file as authority."""
from __future__ import annotations

import copy
import json

import pytest

from app import config
from app.services import assessment_release_snapshot as snapshot
from app.services import assessment_source_inventory as source_inventory
from app.services import assessment_teaching_order as order
from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as contract
from app.services import generation_quality_policy as quality
from app.services import question_polishing, uploads
from app.services import reviewed_file_input as reviewed
from app.services import reviewed_file_workflow_policy as workflow
from app.services import reviewed_question_polishing as polishing
from app.services import source_task_polishing_policy as source_format
from app.services.phase3 import kernel
from tests.test_independent_reviewed_files import critic, document, result, setup_job

REVIEWED = "What is DNA, and what are its uses?"
POLISHED = "DNA is the molecule described in the reviewed concept. Define DNA and state its uses."


@pytest.fixture(autouse=True)
def polish_state(monkeypatch, tmp_path):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()
    yield
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()


def forbidden(*_args, **_kwargs):
    pytest.fail("no provider call was expected")


def scripted(calls):
    def api(system, user, **kwargs):
        request = json.loads(user)
        calls.append({"system": system, "request": request, "purpose": kwargs.get("purpose")})
        if kwargs.get("purpose") == "advisory_critic":
            return {"items": [{"qid": q["qid"], "verdict": "verified", "issues": []}
                              for q in request["questions"]]}
        return {"items": [{"qid": q["qid"], "polished_task": POLISHED, "learner_context": "",
                           "note": "Named the referent from the cited reviewed block."}
                          for q in request["questions"]]}
    return api


def prepared_post(db, tmp_path, version):
    with workflow.bind_run(version):
        job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    current = reviewed.prepare(db, job, lane="post", owner_sub="local:default", provider=lambda _: result(),
                               critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"))
    assert workflow.version_of(current) == version
    return job, current


def test_v2_reviewed_post_questions_are_polished_once_with_reviewed_blocks_as_evidence(db, tmp_path):
    job, current = prepared_post(db, tmp_path, workflow.V2)
    calls = []
    # Production runs this inside model_routing_run.bind_job with the V2
    # workflow bound; the Step 1 deferral gate must not suppress Step 2.
    with workflow.bind_run(workflow.V2):
        updated = polishing.polish_reviewed_post_questions(db, job, payload=current, owner_sub="local:default",
                                                           api_call=scripted(calls))
    assert [call["purpose"] for call in calls] == ["source_extraction", "advisory_critic"]
    author, reviewer = calls
    assert polishing.POLISH_RULES in author["system"]
    assert polishing.REVIEW_RULES in reviewer["system"]
    assert source_format.CONTEXT_POLISH_RULES in author["system"]
    assert author["request"][polishing.KEY] == polishing.VERSION
    question = author["request"]["questions"][0]
    assert question["task"] == REVIEWED
    blocks = question["source_evidence"]["source_context"]["reviewed_file_blocks"]
    assert blocks == current["reviewed_file_receipt"]["document"]["blocks"]
    assert reviewer["request"]["questions"][0]["proposed_task"] == POLISHED

    item = updated["question_task_inventory"]["items"][0]
    assert item["raw_task"] == item["normalized_task"] == REVIEWED
    assert item["frozen_task_text"] == item["polished_task"] == POLISHED
    assert item["polish_flag"] == question_polishing.FLAG_POLISHED
    assert item["learner_context"] == ""
    assert item["polish_audit"]["critic"] == {"verdict": "verified", "issues": []}
    assert item["polish_audit"]["source_evidence"]["source_evidence"]["source_context"]["reviewed_file_blocks"] == blocks
    assert item["polish_review_required"] is False
    assert item[source_format.FIELD] == source_format.VERSION
    assert updated[polishing.RECEIPT_KEY] == {
        "version": polishing.VERSION, "sha256": current[reviewed.KEY]["sha256"],
        "polished": 1, "kept": 0, "review_flags": 0, "questions": 1,
    }
    assert updated["question_task_inventory"]["reviewed_source_questions"] == current["question_task_inventory"]["reviewed_source_questions"]
    assert updated["records"] == current["records"]
    assert release.release_payload(job) == updated
    assert release.staged_version(updated) == release.staged_version(current) + 1

    # The same reviewed file is never polished twice, and an identical reupload
    # keeps the polished revision as the Master input.
    assert polishing.polish_reviewed_post_questions(db, job, payload=release.release_payload(job), api_call=forbidden) is None
    assert reviewed.prepare(db, job, lane="post", provider=forbidden) == updated
    assert len(calls) == 2


def test_step_two_rules_are_additive_and_step_one_prompts_stay_byte_identical():
    plain = {"subject": "Biology", quality.KEY: quality.VERSION}
    stamped = {**plain, polishing.KEY: polishing.VERSION}
    assert question_polishing._author_system(plain) + "\n" + polishing.POLISH_RULES == question_polishing._author_system(stamped)
    assert question_polishing._critic_system(plain) + "\n" + polishing.REVIEW_RULES == question_polishing._critic_system(stamped)
    assert polishing.KEY not in question_polishing._batch_payload(plain, [])
    assert json.loads(question_polishing._batch_payload(stamped, []))[polishing.KEY] == polishing.VERSION
    assert question_polishing._cache_key([], plain) != question_polishing._cache_key([], stamped)


def test_v1_reviewed_post_payload_is_left_untouched(db, tmp_path):
    job, current = prepared_post(db, tmp_path, workflow.V1)
    assert polishing.polish_reviewed_post_questions(db, job, payload=current, api_call=forbidden) is None
    assert release.release_payload(job) == current
    assert polishing.RECEIPT_KEY not in current


def test_reviewed_pre_questions_are_never_polished(db, tmp_path):
    with workflow.bind_run(workflow.V2):
        job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="pre", path=path, filename=path.name, owner_sub="local:default")
    current = reviewed.prepare(db, job, lane="pre", owner_sub="local:default", provider=lambda _: result(),
                               critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"))
    assert current["generated_questions"][0]["question_text"] == REVIEWED
    assert polishing.polish_reviewed_post_questions(db, job, payload=current, api_call=forbidden) is None
    assert release.release_payload(job, lane="pre") == current


def test_polished_reviewed_payload_reaches_master_atoms_with_frozen_wording_and_audit(db, tmp_path):
    job, current = prepared_post(db, tmp_path, workflow.V2)
    updated = polishing.polish_reviewed_post_questions(db, job, payload=current, owner_sub="local:default",
                                                       api_call=scripted([]))
    with db.no_autoflush:
        bridge = snapshot.build(db, job, updated)
    built = source_inventory.build_source_atoms(
        bridge["question_task_inventory"], source_document_hash=bridge["source_document_hash"])
    atom = built["atoms"][0]
    assert atom["raw_text"] == REVIEWED
    assert atom["frozen_task_text"] == atom["normalized_public_text"] == POLISHED
    assert atom["learner_context"] == ""
    assert atom["polish_audit"]["critic"]["verdict"] == "verified"
    assert atom["polish_review_required"] is False
    assert atom[quality.KEY] == quality.VERSION
    assert atom["source_context"]["reviewed_file_blocks"] == current["reviewed_file_receipt"]["document"]["blocks"]
    assert source_inventory.source_atom_from_item(
        updated["question_task_inventory"]["items"][0], source_document_hash="doc")["frozen_task_text"] == POLISHED


def test_reviewed_records_carry_question_order_receipts_so_teaching_order_maps_every_atom(db, tmp_path):
    job, current = prepared_post(db, tmp_path, workflow.V2)
    qid = current["question_task_inventory"]["items"][0]["qid"]
    assert current["records"][0][release.RELEASE_ROW_QIDS_FIELD] == [qid]
    updated = polishing.polish_reviewed_post_questions(db, job, payload=current, owner_sub="local:default",
                                                       api_call=scripted([]))
    with db.no_autoflush:
        bridge = snapshot.build(db, job, updated)
    atoms = source_inventory.build_source_atoms(
        bridge["question_task_inventory"], source_document_hash=bridge["source_document_hash"])["atoms"]
    ordered, receipt = order.project_atoms(atoms, updated)
    assert receipt["unmapped_qids"] == []
    assert receipt["accepted_concept_qids"] == receipt["ordered_atom_qids"] == [qid]
    assert "flags" not in ordered[0]
    assert ordered[0][order.AUDIT_FIELD]["concept_record_index"] == 0
    assert ordered[0][order.AUDIT_FIELD]["ordinal"] == 0


def test_reviewed_records_own_their_questions_in_reviewed_order_across_concepts(db, tmp_path):
    with workflow.bind_run(workflow.V2):
        job = setup_job(db)
    path = document(tmp_path, "Definition and uses\nWhat is DNA, and what are its uses?\nName one use of DNA.")
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    parsed = result()
    parsed["concepts"].append({**parsed["concepts"][0], "concept_title": "Second reviewed concept"})
    second = copy.deepcopy(parsed["questions"][0])
    second.update({"concept_index": 1, "question_spans": ["Name one use of DNA."]})
    third = copy.deepcopy(parsed["questions"][0])
    third.update({"concept_index": 0, "question_spans": ["Name one use of DNA."]})
    parsed["questions"] = [parsed["questions"][0], second, third]
    current = reviewed.prepare(db, job, lane="post", provider=lambda _: parsed, critic=critic,
                               store=kernel.DecisionStore(tmp_path / "decisions"))
    qids = [item["qid"] for item in current["question_task_inventory"]["items"]]
    assert [row[release.RELEASE_ROW_QIDS_FIELD] for row in current["records"]] == [[qids[0], qids[2]], [qids[1]]]


@pytest.mark.parametrize("version", [workflow.V1, workflow.V2])
def test_build_review_masters_polishes_after_prepare_and_before_masters_only_for_v2(db, tmp_path, monkeypatch, version):
    with workflow.bind_run(version):
        job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    sequence, calls, seen = [], [], {}

    def provider(payload):
        if payload["lane"] == "pre":
            parsed = result(False)
            parsed["dispositions"] = [{"source_ref": block["ref"], "disposition": "concept",
                                       "rationale": "The unchanged Pre workbook is the reviewed input."}
                                      for block in payload["document"]["blocks"]]
            return parsed
        return result()

    real_prepare = reviewed.prepare

    def prepare(db_, job_, *, lane, owner_sub="", **_kwargs):
        sequence.append(("prepare", lane))
        return real_prepare(db_, job_, lane=lane, owner_sub=owner_sub, provider=provider, critic=critic,
                            store=kernel.DecisionStore(tmp_path / "decisions"))

    real_polish = polishing.polish_reviewed_post_questions

    def polish(db_, job_, *, payload, owner_sub="", api_call=None):
        sequence.append(("polish", payload.get(release.RELEASE_LANE_FIELD)))
        return real_polish(db_, job_, payload=payload, owner_sub=owner_sub, api_call=scripted(calls))

    def siblings(db_, job_id, _chapter_id, **_kwargs):
        sequence.append(("masters", None))
        seen["post"] = release.release_payload(uploads.get_job(db_, job_id), lane="post")
        return {release.LANE_PRE: {"release_id": 1}, release.LANE_POST: {"release_id": 2}}

    monkeypatch.setattr(reviewed, "prepare", prepare)
    monkeypatch.setattr(polishing, "polish_reviewed_post_questions", polish)
    monkeypatch.setattr(contract, "_regenerate_pre_questions_after_review", lambda *a, **k: None)
    monkeypatch.setattr(contract, "_build_master_siblings", siblings)

    outcome = contract.build_review_masters(db, job.id, owner_sub="local:default")
    assert outcome["all_four_outputs_ready"] is True
    item = seen["post"]["question_task_inventory"]["items"][0]
    assert item["raw_task"] == REVIEWED
    if version == workflow.V2:
        assert sequence == [("prepare", "post"), ("prepare", "pre"), ("polish", "post"), ("masters", None)]
        assert [call["purpose"] for call in calls] == ["source_extraction", "advisory_critic"]
        assert item["frozen_task_text"] == POLISHED
        assert seen["post"][polishing.RECEIPT_KEY]["polished"] == 1
    else:
        assert sequence == [("prepare", "post"), ("prepare", "pre"), ("masters", None)]
        assert calls == []
        assert item["frozen_task_text"] == REVIEWED
        assert polishing.RECEIPT_KEY not in seen["post"]


def test_explicit_post_rebuild_polishes_the_reviewed_file_before_the_runner(db, tmp_path, monkeypatch):
    from app.services import assessment_release_run as master_run
    job, current = prepared_post(db, tmp_path, workflow.V2)
    calls, seen = [], {}
    real_polish = polishing.polish_reviewed_post_questions
    monkeypatch.setattr(polishing, "polish_reviewed_post_questions",
                        lambda db_, job_, *, payload, owner_sub="", api_call=None:
                        real_polish(db_, job_, payload=payload, owner_sub=owner_sub, api_call=scripted(calls)))

    def runner(db_, job_id, *, owner_sub=None, **_kwargs):
        seen["post"] = release.release_payload(uploads.get_job(db_, job_id), lane="post")
        return {"release_id": 7}

    monkeypatch.setattr(master_run, "run_release_for_job", runner)
    assert contract.rebuild_lane_master(db, job.id, "post", owner_sub="local:default") == {"release_id": 7}
    assert len(calls) == 2
    assert seen["post"]["question_task_inventory"]["items"][0]["frozen_task_text"] == POLISHED
    assert seen["post"][polishing.RECEIPT_KEY]["sha256"] == current[reviewed.KEY]["sha256"]


def test_polishing_failure_is_recorded_as_a_master_failure(db, tmp_path, monkeypatch):
    from app.services import assessment_release_run as master_run
    job, _current = prepared_post(db, tmp_path, workflow.V2)

    def failing(*_args, **_kwargs):
        raise RuntimeError("insufficient_quota: polishing author denied")

    monkeypatch.setattr(polishing, "polish_reviewed_post_questions", failing)
    monkeypatch.setattr(master_run, "run_release_for_job", lambda *a, **k: pytest.fail("Master ran without polishing"))
    with pytest.raises(RuntimeError, match="insufficient_quota"):
        contract.rebuild_lane_master(db, job.id, "post", owner_sub="local:default")
    db.expire_all()
    issue = release.assessment_lane_issue(release.release_payload(uploads.get_job(db, job.id), lane="post"))
    assert issue and "insufficient_quota" in str(issue.get("message") or issue)


MCQ_FILE = (
    "Definition and uses\n"
    "Which molecule carries genetic information?\n"
    "(a) DNA\n"
    "(b) RNA\n"
)
MCQ_REVIEWED = "Which molecule carries genetic information?"
MCQ_POLISHED = "Which molecule carries the genetic information of a cell?"


def mcq_result():
    parsed = result()
    question = parsed["questions"][0]
    question.update({
        "question_spans": [MCQ_REVIEWED],
        "options": ["(a) DNA", "(b) RNA"],
        "tables": [{"headers": ["Molecule", "Role"], "rows": [["DNA", "Stores information"]]}],
    })
    return parsed


def mcq_api(calls):
    def api(system, user, **kwargs):
        request = json.loads(user)
        calls.append({"system": system, "request": request, "purpose": kwargs.get("purpose")})
        if kwargs.get("purpose") == "advisory_critic":
            return {"items": [{"qid": q["qid"], "verdict": "verified", "issues": []}
                              for q in request["questions"]]}
        # The polished wording keeps the reviewed ask; the reviewed file
        # prints its options on their own lines, outside the question span.
        return {"items": [{"qid": q["qid"], "polished_task": MCQ_POLISHED,
                           "learner_context": "", "note": ""}
                          for q in request["questions"]]}
    return api


def test_reviewed_mcq_polish_survives_options_the_question_spans_never_carried(db, tmp_path):
    """The Step 2 pass must actually apply to reviewed multiple-choice questions.

    ``reviewed_file_input.prepare`` sets ``raw_task`` to the joined question
    spans and keeps ``options`` as an independent extracted field, so Step 1's
    "the options are inside the source text" invariant does not hold here.
    Measuring option retention against the item's own source text keeps the
    polish instead of reverting it as a dropped option.
    """
    with workflow.bind_run(workflow.V2):
        job = setup_job(db)
    path = document(tmp_path, MCQ_FILE)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    current = reviewed.prepare(db, job, lane="post", owner_sub="local:default",
                               provider=lambda _: mcq_result(), critic=critic,
                               store=kernel.DecisionStore(tmp_path / "decisions"))
    item = current["question_task_inventory"]["items"][0]
    assert item["raw_task"] == MCQ_REVIEWED
    assert item["options"] == ["(a) DNA", "(b) RNA"]
    assert all(option not in item["raw_task"] for option in item["options"])

    calls = []
    with workflow.bind_run(workflow.V2):
        updated = polishing.polish_reviewed_post_questions(
            db, job, payload=current, owner_sub="local:default", api_call=mcq_api(calls))

    polished_item = updated["question_task_inventory"]["items"][0]
    assert polished_item["polish_flag"] == question_polishing.FLAG_POLISHED
    assert polished_item["polished_task"] == polished_item["frozen_task_text"] == MCQ_POLISHED
    assert "polish_note" not in polished_item or "dropped MCQ option" not in polished_item["polish_note"]
    assert polished_item["options"] == ["(a) DNA", "(b) RNA"]
    assert polished_item["raw_task"] == MCQ_REVIEWED
    assert updated[polishing.RECEIPT_KEY]["polished"] == 1
    assert updated[polishing.RECEIPT_KEY]["kept"] == 0


def test_reviewed_options_tables_and_cited_blocks_reach_the_author_and_the_critic(db, tmp_path):
    """Both Step 2 models see the evidence the reviewed item carries."""
    with workflow.bind_run(workflow.V2):
        job = setup_job(db)
    path = document(tmp_path, MCQ_FILE)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    current = reviewed.prepare(db, job, lane="post", owner_sub="local:default",
                               provider=lambda _: mcq_result(), critic=critic,
                               store=kernel.DecisionStore(tmp_path / "decisions"))
    item = current["question_task_inventory"]["items"][0]
    calls = []
    with workflow.bind_run(workflow.V2):
        polishing.polish_reviewed_post_questions(
            db, job, payload=current, owner_sub="local:default", api_call=mcq_api(calls))

    assert [call["purpose"] for call in calls] == ["source_extraction", "advisory_critic"]
    for call in calls:
        question = call["request"]["questions"][0]
        evidence = question["source_evidence"]
        assert question["options"] == item["options"]
        assert question["image_urls"] == item["image_urls"]
        assert question["has_images"] is bool(item["image_urls"])
        assert evidence["tables"] == item["tables"]
        assert evidence["source_context"]["options"] == item["options"]
        assert evidence["source_context"]["tables"] == item["tables"]
        assert evidence["source_context"]["image_urls"] == item["image_urls"]
        assert evidence["source_context"]["reviewed_file_blocks"] == \
            current["reviewed_file_receipt"]["document"]["blocks"]
        assert evidence["raw_task"] == MCQ_REVIEWED
        assert call["request"]["visual_evidence"]["version"]
