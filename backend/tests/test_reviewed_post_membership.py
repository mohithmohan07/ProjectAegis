"""Q51/D4 — the reviewed Post question set is authoritative (owner approval,
11 September 2026, `docs/three-step-workflow-review-2026-09-11.md` §7).

Step 02 builds the Post Master from the Concept file the team edited and
uploaded. Q41 makes that set authoritative: Aegis "must not invent additional
Post questions or restore deliberately removed ones" — and the two chapter-wide
membership verdicts the source path runs (Q18's pre-learning claim and P3's
source-duplicate fold) can only ever REMOVE a question the reviewer kept. On a
reviewed bank neither is judged, and the skip is recorded per decision on the
payload and in the run log — never a silent "zero claims found".

A source-extracted bank is the historical path and still pays for both.
"""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_release_run as run
from app.services import assessment_release_service as svc
from app.services import build_concepts_release as release
from app.services import progress
from app.services import reviewed_file_input as reviewed
from app.services.phase3 import kernel
from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
    _make_job,
)
from tests.test_independent_reviewed_files import critic, setup_job

FIRST = "What is DNA, and what are its uses?"
SECOND = "Write the same question again: what is DNA, and what are its uses?"


def _reviewed_document(tmp_path):
    """Two reviewed questions, the second deliberately near-identical.

    The reviewer kept both. A duplicate verdict over this set would be free to
    fold one into the other — which is exactly the judgment D4 removes.
    """
    path = tmp_path / "reviewed-post.txt"
    path.write_text("Definition and uses\n" + FIRST + "\n" + SECOND)
    return path


def _extraction(payload):
    return {
        "pre_scope_verdict": "retained",
        "concepts": [{
            "topic": "Reorganized",
            "concept_title": "New reviewed concept",
            "parent_concept": "",
            "concept_details": (
                "Description: Definition and uses.<br>Achieving Mastery: "
                "Explain definition and uses."
            ),
            "keywords": "definition",
            "source_refs": ["B1"],
        }],
        "questions": [
            {
                "concept_index": 0, "source_refs": ["B1"],
                "question_spans": [task], "context_spans": [],
                "answer_spans": [], "pre_answer": "",
                "options": [], "tables": [], "image_refs": [],
                "type_title": "Definition and uses",
                "type_definition": "Define and explain uses.",
                "case_title": "", "case_definition": "",
                "placement_section": "types",
            }
            for task in (FIRST, SECOND)
        ],
        "dispositions": [{
            "source_ref": "B1", "disposition": "concept",
            "rationale": "Reviewed concept and both dependent questions.",
        }],
        "empty_reason": "",
    }


def _reviewed_post_job(db, tmp_path):
    job = setup_job(db)
    path = _reviewed_document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name,
                   owner_sub=OWNER)
    payload = reviewed.prepare(
        db, job, lane="post", owner_sub=OWNER, provider=_extraction,
        critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"))
    assert reviewed.active(payload)
    tasks = [item["raw_task"] for item in payload["question_task_inventory"]["items"]]
    assert tasks == [FIRST, SECOND]
    return job, payload


def _membership_authorities(db, chapter):
    """The scripted stack, with both membership verdicts wired to fail."""
    authorities, _ = _authorities(db, chapter)

    def refuse(stage):
        def call(_payload):
            pytest.fail(
                f"the {stage} verdict must not be asked for on a reviewed "
                "Post set (Q51/D4)"
            )
        return call

    authorities["pre_claim"] = (refuse("Q18 pre-learning claim"),
                                refuse("Q18 claim critic"))
    authorities["dedup"] = (refuse("P3 source-duplicate"),
                            refuse("P3 duplicate critic"))
    return authorities


def _run(db, job, authorities):
    events: list[dict] = []
    token = progress._sink.set(events.append)
    try:
        published = run.run_release_for_job(
            db, job.id, owner_sub=OWNER, authorities=authorities,
            **_decision_context())
    finally:
        progress._sink.reset(token)
    logs = [str(event.get("message") or "") for event in events
            if event.get("type") == "log"]
    return published, logs


def test_reviewed_post_master_keeps_every_reviewed_question_without_membership_verdicts(
        db, tmp_path):
    job, payload = _reviewed_post_job(db, tmp_path)
    chapter = _chapter_with_concepts(db)
    published, logs = _run(db, job, _membership_authorities(db, chapter))

    # A skip is not a flag: nothing was dropped, so readiness is untouched.
    assert (published.diagnostics or {}).get("readiness") == svc.READY
    frozen = published.payload
    # The reviewer's set, whole: neither the claim nor the fold removed one.
    assert [atom["normalized_public_text"] for atom in frozen["source_atoms"]] == [
        FIRST, SECOND]
    assert len(frozen["candidates"]) == 2
    assert frozen["pre_learning_claimed"] == []
    assert frozen["source_duplicates_represented"] == []

    # ... and the run says it never judged, per decision.
    recorded = frozen["reviewed_question_set_authoritative"]
    assert [entry["decision"] for entry in recorded] == [
        "pre_learning_claim", "source_duplicates"]
    for entry in recorded:
        assert entry["judged"] is False
        assert entry["policy"] == run.REVIEWED_SET_AUTHORITY_POLICY
        assert entry["authority"] == "Q51/D4"
        assert entry["reviewed_question_count"] == 2
        assert "authoritative Post question set (Q41)" in entry["reason"]
    assert {entry["register_entry"] for entry in recorded} == {"Q18", "P3"}

    # One visible log line per skipped decision, naming the reason.
    claim_lines = [line for line in logs
                   if "Q18 pre-learning claim is NOT judged" in line]
    dedup_lines = [line for line in logs
                   if "P3 source-duplicate verdict is NOT judged" in line]
    assert len(claim_lines) == 1 and len(dedup_lines) == 1
    for line in (*claim_lines, *dedup_lines):
        assert "authoritative Post question set (Q51/D4)" in line
        assert "2 reviewed question(s)" in line
    # Nothing may read as "the verdict ran and found nothing".
    assert not [line for line in logs if "claimed by pre-learning" in line]
    assert not [line for line in logs if "duplicate survivor" in line]


def test_source_extracted_post_master_still_pays_for_both_membership_verdicts(db):
    """The historical path is untouched: both verdicts run, unrecorded skip."""
    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    assert not reviewed.active(release.release_payload(job))
    calls: dict[str, list] = {}
    authorities, _ = _authorities(db, chapter, calls=calls)

    published, _logs = _run(db, job, authorities)

    assert len(calls.get("pre_claim") or []) == 1
    assert len(calls.get("dedup") or []) == 1
    assert "reviewed_question_set_authoritative" not in published.payload
    assert published.payload["pre_learning_claimed"] == []
    assert published.payload["source_duplicates_represented"] == []
