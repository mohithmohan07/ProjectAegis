"""The generated (Pre-Learning) assessment lane, and its leak barrier.

docs/aegis-restructure.md §4 Phase 03: *"The Pre Master contains
generated questions only — current-chapter source questions never appear
in it."*  Owner steer, 17 Aug 2026, point 3: *"NO EXTRACTION OF ANY
QUESTIONS, anywhere in the Pre lane."*

The defect these pin was real and observed: a staged PRE release carries
the CURRENT chapter's question/task inventory, and
``assessment_release_run`` was driven entirely off that inventory, so
pointing the pipeline at a Pre job unchanged published the chapter's own
source questions — QIDs and all — as Output 04.
"""
from __future__ import annotations

import copy
import json

import pytest

from app import models
from app.services import assessment_materialization as materialization
from app.services import assessment_release as rel
from app.services import assessment_release_run as run
from app.services import assessment_release_service as svc
from app.services import build_concepts_release
from app.services.phase3 import kernel
from app.services.phase3 import premap

from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
)

SOURCE_INVENTORY = {
    "items": [
        {
            "qid": "QINV-0001",
            "source_kind": "exercise",
            "source_label": "Exercise 1(1)",
            "raw_task": "Which of these is a solid?",
            "options": ["Cube", "Circle"],
        },
        {
            "qid": "QINV-0002",
            "source_kind": "exercise",
            "source_label": "Exercise 1(2)",
            "raw_task": "Explain why a cube is a solid.",
        },
    ],
}


def _pre_job(db, chapter) -> models.UploadJob:
    """A Pre job whose staged release still carries the chapter inventory.

    That is exactly the shipped state the barrier exists for: the
    inventory is stored on every job, Pre included, so the fixture must
    not "fix" it by omission.
    """

    job = models.UploadJob(
        owner_sub=OWNER,
        module="build_concepts",
        upload_type="textbook",
        filename="ch.mmd",
        mmd_text="# Chapter\n\nExercise 1. Which of these is a solid?",
        status="generated",
        learning_kind="pre",
        deposit_scope_type="chapter",
        deposit_scope_ids=[chapter.id],
        question_inventory=copy.deepcopy(SOURCE_INVENTORY),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    build_concepts_release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[
            {
                "topic": "Counting",
                "concept_title": "Counting to ten",
                "concept_details": (
                    "Description: Counting to ten names each number in "
                    "order.\nAchieving Mastery: count aloud to ten."
                ),
                "keywords": "count, number",
            },
        ],
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="recorded Pre Output-03 fixture",
    )
    return job


def _staged_concept_key(db, job) -> str:
    from app.services import assessment_release_snapshot as release_snapshot

    bridge = release_snapshot.build(
        db, job, build_concepts_release.release_payload(job)
    )
    return next(iter(bridge["concept_records_by_key"]))


def _questions(count: int, *, concept: str = "PRC-0001") -> list[dict]:
    return [
        {
            "pre_question_id": f"{concept}-PRQ-{index:04d}",
            "pre_concept_id": concept,
            "question_id": f"PRQ-{index:04d}",
            "question_text": f"Count aloud from one to {index + 4}.",
            "answer": f"one … {index + 4}",
            "rationale": "checks the learner already counts in order",
        }
        for index in range(1, count + 1)
    ]


def _cells(count: int, concept_key: str) -> list[dict]:
    return [
        {
            "sheet_kind": "descriptive",
            "question_category": "Short Answer",
            "cognitive_skill": "Remember",
            "difficulty": "Less",
            "marks": 2,
            "count": 1,
            "concept_key": concept_key,
        }
        for _ in range(count)
    ]


def _generated_authorities(*, calls=None, critic=None, materialize=None):
    """Scripted authorities for the generated lane.

    Routing is deliberately absent: a generated cell names its concept
    key, so ``assessment_routing`` places it mechanically at zero spend.
    """

    calls = calls if calls is not None else {}

    def record(stage, payload):
        calls.setdefault(stage, []).append(copy.deepcopy(payload))

    def materialize_author(payload):
        record("materialize", payload)
        assert payload["source_atom"] is None
        generated = payload["blueprint_cell"]["generated_question"]
        question = (
            materialize(generated) if materialize
            else generated["question_text"]
        )
        return {
            "candidate_id": payload["candidate_id"],
            "question": question,
            "display_answer": generated["answer"],
            "answers": [
                {
                    "answer_type": "Phrases",
                    "answer_content": generated["answer"],
                    "answer_weightage": "2",
                },
            ],
            "sub_questions": [],
            "answer_explanation": "",
            "requires_visual": False,
            "rationale": "authored from the generated prerequisite question",
        }

    def answer_restriction_author(payload):
        record("answer_restriction", payload)
        candidate = payload["candidate"]
        return {
            "candidate_id": candidate["candidate_id"],
            "answer_restriction": "Open",
            "restriction_reason": "Several complete recitations earn credit.",
            "answer_space_contract": "Count in order without omission.",
            "required_elements": ["every number in order"],
            "accepted_variations": ["equivalent phrasing"],
            "evidence": "The generated prerequisite question decides it.",
            "rationale": "Scripted evidence-bound answer-space verdict.",
            "review_required": False,
            "review_reason": "",
        }

    def marking_author(payload):
        record("marking", payload)
        candidate = payload["candidate"]
        answers = copy.deepcopy(candidate["answers"])
        answers[0]["answer_weightage"] = 2
        return {
            "candidate_id": candidate["candidate_id"],
            "question": candidate["question"],
            "question_text": candidate["question_text"],
            "answers": answers,
            "sub_questions": [],
            "question_duration": 3,
            "math_keyboard": "No",
            "rationale": "The generated cell owns the decomposition.",
        }

    def level_author(payload):
        record("level", payload)
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Basic",
            "rationale": "a prerequisite recall task",
        }

    def cluster_author(payload):
        record("cluster", payload)
        return {
            "concept_key": payload["concept"]["concept_key"],
            "tier": payload["tier"],
            "families": [
                {
                    "existing_group_key": "",
                    "family": f"scripted variant {index}",
                    "member_candidate_ids": [candidate["candidate_id"]],
                }
                for index, candidate in enumerate(
                    payload["candidates"], start=1
                )
            ],
            "rationale": "the recorded families preserve distinct variants",
        }

    def describe_author(payload):
        record("describe", payload)
        return {
            "group_key": payload["group"]["group_key"],
            "description": "Reciting the counting sequence on demand.",
            "rationale": "the wording states how and what is assessed",
        }

    def qa_reviewer(payload):
        record("qa", payload)
        return {"group_key": payload["group"]["group_key"], "flags": []}

    def refiner_author(payload):
        record("refiner", payload)
        unit_kind = payload["unit_kind"]
        return {
            "record_kind": unit_kind,
            "row_ref": payload["row_ref"],
            "record": copy.deepcopy(payload[unit_kind]),
            "rationale": "Nothing to polish in this recorded fixture.",
        }

    def dedup_author(payload):
        record("dedup", payload)
        return {
            "duplicate_sets": [],
            "confidence": 1.0,
            "rationale": "each question asks a distinct prerequisite",
        }

    def verified_critic(payload):
        record("critic", payload)
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    stage_critic = critic or verified_critic
    return {
        "dedup": (dedup_author, stage_critic),
        "materialize": (materialize_author, stage_critic),
        "answer_restriction": (answer_restriction_author, stage_critic),
        "marking": (marking_author, stage_critic),
        "level": (level_author, stage_critic),
        "cluster": (cluster_author, stage_critic),
        "describe": (describe_author, stage_critic),
        "qa": (qa_reviewer, stage_critic),
        "refiner": (refiner_author, stage_critic),
    }, calls


def _run_generated(db, job, *, count=2, concept="PRC-0001", **kwargs):
    concept_key = _staged_concept_key(db, job)
    questions = kwargs.pop("questions", None)
    if questions is None:
        questions = _questions(count, concept=concept)
    cells = kwargs.pop("cells", None)
    if cells is None:
        cells = _cells(len(questions), concept_key)
    authorities = kwargs.pop("authorities", None)
    if authorities is None:
        authorities, _ = _generated_authorities()
    context = kwargs.pop("context", None) or _decision_context()
    return run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        generated_questions=questions,
        blueprint_cells=cells,
        authorities=authorities,
        **context,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# The leak itself
# --------------------------------------------------------------------------- #

def test_a_pre_job_can_never_take_the_source_question_lane(db):
    """The observed defect, refused at its root.

    Before the fix this exact call PUBLISHED a release whose source atoms
    were ``['QINV-0001', 'QINV-0002']``, whose candidates carried
    ``source_atom_ids`` ``[['QINV-0001'], ['QINV-0002']]``, and whose
    learner-facing questions were the chapter's own, verbatim.
    """

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    assert build_concepts_release.release_payload(job)[
        "question_task_inventory"
    ]["items"], "the fixture must keep the leak's raw material"
    authorities, _ = _authorities(db, chapter)
    before = db.query(models.AssessmentRelease).count()

    with pytest.raises(run.SourceQuestionLeak) as raised:
        run.run_release_for_job(
            db, job.id, owner_sub=OWNER, authorities=authorities,
            **_decision_context())
    assert "generated" in str(raised.value).lower()
    assert db.query(models.AssessmentRelease).count() == before


def test_the_lane_is_read_from_the_staged_release_not_the_mutable_job(db):
    """Flipping the job row after staging cannot open or close the lane.

    Output 02/04 route against the immutable staged payload; the job's
    ``learning_kind`` column is mutable and is never the authority.
    """

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    job.learning_kind = "post"
    db.commit()
    authorities, _ = _authorities(db, chapter)

    with pytest.raises(run.SourceQuestionLeak):
        run.run_release_for_job(
            db, job.id, owner_sub=OWNER, authorities=authorities,
            **_decision_context())


def test_generated_pre_release_carries_no_source_question_anywhere(db):
    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)

    release = _run_generated(db, job, count=2)

    payload = release.payload
    blob = json.dumps(payload)
    for qid in ("QINV-0001", "QINV-0002"):
        assert qid not in blob
    assert payload["source_atoms"] == []
    assert len(payload["candidates"]) == 2
    for candidate in payload["candidates"]:
        assert candidate["source_atom_ids"] == []
        assert candidate["source_qid"] == ""
        assert candidate["source_policy"] == "generate"
        assert candidate["origin"] == "pre_learning_generated"
        assert candidate["question"] == candidate["question_text"]
    assert {
        candidate["question"] for candidate in payload["candidates"]
    } == {
        "Count aloud from one to 5.",
        "Count aloud from one to 6.",
    }
    for cell in payload["blueprint_cells"]:
        assert cell["source_policy"] == "generate"
        assert cell["accepted_source_qids"] == []
    # Routing is mechanical off the blueprint concept key: every candidate
    # has a home and none of them cost a decision.
    assert all(
        placement["concept_key"].startswith("release:")
        for placement in payload["placements"]
    )
    assert (release.diagnostics or {}).get("readiness") in {
        svc.READY, svc.RELEASED_WITH_WARNINGS,
    }


def test_barrier_fails_closed_on_an_injected_source_qid(db):
    """A source QID reaching an AUTHORED field refuses the release."""

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    authorities, _ = _generated_authorities(
        materialize=lambda generated: (
            "Which of these is a solid? (QINV-0001)"
        ),
    )
    before = db.query(models.AssessmentRelease).count()

    with pytest.raises(premap.PreExtractionError) as raised:
        _run_generated(db, job, count=1, authorities=authorities)
    assert "QINV-0001" in str(raised.value)
    # Refused before anything was frozen: no release row, and the refusal
    # happened at materialization, before the eight later stages spent.
    assert db.query(models.AssessmentRelease).count() == before


def test_barrier_fails_closed_on_a_cell_that_accepts_a_source_question(db):
    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    cells = _cells(1, _staged_concept_key(db, job))
    cells[0]["accepted_source_qids"] = ["QINV-0002"]

    with pytest.raises(run.GeneratedLaneError) as raised:
        _run_generated(db, job, count=1, cells=cells)
    assert "QINV-0002" in str(raised.value)


def test_barrier_fails_closed_when_a_candidate_loses_its_generated_marker(db):
    """The marker is checked for PRESENCE, not for an absent source id."""

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    payload = {
        "candidates": [
            {
                "candidate_id": "CAND-1",
                "source_atom_ids": [],
                "source_qid": "",
                "origin": "pre_learning_generated",
            },
        ],
    }
    with pytest.raises(run.SourceQuestionLeak) as raised:
        run._refuse_source_questions(
            payload, source_qids=["QINV-0001"], where="a fixture",
        )
    assert "source_policy" in str(raised.value)
    assert job.learning_kind == "pre"


# --------------------------------------------------------------------------- #
# The two traps the map found
# --------------------------------------------------------------------------- #

def test_many_questions_from_one_concept_get_distinct_candidate_ids(db):
    """Spec T6 trap 1: ``_candidate_id`` seeds on (source_qid, cell_id).

    With ``atom=None`` the first half of that seed is constant, so N
    questions sharing ONE cell collapse onto one candidate id. The
    decided sidestep is one cell per generated question, minted from the
    question's own durable id.
    """

    shared = {"cell_id": "CELL-shared"}
    assert (
        materialization._candidate_id(None, shared)
        == materialization._candidate_id(None, dict(shared))
    ), "the collapse the mint exists to avoid must be real"

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    release = _run_generated(db, job, count=3, concept="PRC-0001")

    candidates = release.payload["candidates"]
    assert len(candidates) == 3
    assert len({c["candidate_id"] for c in candidates}) == 3
    assert len({c["blueprint_cell_id"] for c in candidates}) == 3
    assert len(
        {cell["cell_id"] for cell in release.payload["blueprint_cells"]}
    ) == 3


def test_a_generated_cell_never_carries_more_than_one_question(db):
    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    cells = _cells(1, _staged_concept_key(db, job))
    cells[0]["count"] = 4

    with pytest.raises(run.GeneratedLaneError) as raised:
        _run_generated(db, job, count=1, cells=cells)
    assert "exactly one question" in str(raised.value)


def test_empty_source_atom_ids_is_deliberate_not_string_coercion():
    """Spec T6 trap 2, pinned as a decision rather than an accident.

    ``_missing`` tests ``str(record.get(field)).strip() == ""`` and
    ``str([])`` is ``"[]"`` — non-empty — so a generated candidate used
    to pass this validator by coercion. The rule is now written down.
    """

    assert str([]).strip() != ""
    assert "source_atom_ids" not in rel._CANDIDATE_REQUIRED

    base = {
        "candidate_id": "C1", "source_atom_ids": [],
        "blueprint_cell_id": "CELL-01", "question": "Q?",
        "question_text": "Q?", "sheet_kind": "objective",
        "question_category": "MCQ", "cognitive_skill": "Remember",
        "difficulty": "Less", "marks": 1, "question_duration": 2,
        "math_keyboard": "", "restriction_reason": "Authored evidence.",
        "answer_restriction": "Specific",
        "answers": [
            {
                "answer_type": "Phrases", "answer_content": "A",
                "correct_answer": "Yes", "answer_weightage": 1,
            },
            {
                "answer_type": "Phrases", "answer_content": "B",
                "correct_answer": "No", "answer_weightage": 0,
            },
        ],
        "sub_questions": [],
    }
    reused = rel.validate_candidate(dict(base))
    assert any("source_atom_ids may be empty only" in e for e in reused)

    generated = rel.validate_candidate(dict(base, source_policy="generate"))
    assert generated == []

    assert any(
        "must not contain an empty id" in error
        for error in rel.validate_candidate(
            dict(base, source_atom_ids=[""], source_policy="generate")
        )
    )
    assert any(
        "must be a list" in error
        for error in rel.validate_candidate(
            dict(base, source_atom_ids="QINV-0001")
        )
    )


# --------------------------------------------------------------------------- #
# The ordering rule (Q10): a critic may never brick a run
# --------------------------------------------------------------------------- #

def test_a_critic_naming_a_source_qid_flags_and_never_kills_the_run(
    db, tmp_path,
):
    """The guard runs over the AUTHORED artefact, not the audit of it.

    A critic asked whether a generated question is a source question
    reworded may answer by naming the question it resembles. Scanning
    critic prose would turn that advisory dissent into the hardest gate
    in the pipeline — and because verdicts are content-addressed, a
    DIRECTORY-BACKED store would replay the refusal forever, at zero
    spend, with no way back. Hence the directory store here.
    """

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    store = kernel.DecisionStore(tmp_path / "decisions")

    def naming_critic(payload):
        return {
            "verdict": "dissent",
            "confidence": 0.4,
            "issues": ["this reads like QINV-0001 reworded"],
        }

    authorities, calls = _generated_authorities(critic=naming_critic)
    release = _run_generated(
        db, job, count=1, authorities=authorities,
        context={"envelope_sha256": "e" * 64, "decision_store": store},
    )
    assert len(calls["materialize"]) == 1
    flags = [
        flag
        for candidate in release.payload["candidates"]
        for flag in candidate.get("flags") or []
    ]
    assert "assessment_materialization_review" in flags
    assert "QINV-0001" not in json.dumps(
        run._authored_surface(release.payload)
    )
    # And the id does not survive in the audit channel either: it is
    # REDACTED there rather than refused, so the steer's "no QID anywhere
    # in the Pre release payload" holds without a critic ever being able
    # to end a run.
    blob = json.dumps(release.payload)
    assert "QINV-0001" not in blob
    assert premap._REDACTED_QID in blob
    assert any("reworded" in str(flag) for flag in json.loads(blob).get(
        "candidates")[0]["_aegis_assessment_materialization"]["flags"])

    # Replay from the same durable store: the recorded dissent is read
    # back rather than re-decided, and the run still completes.
    replayed = _run_generated(
        db, job, count=1, authorities=authorities,
        context={"envelope_sha256": "e" * 64, "decision_store": store},
    )
    assert len(calls["materialize"]) == 1, "the replay must cost nothing"
    assert replayed.payload["candidates"][0]["candidate_id"] == (
        release.payload["candidates"][0]["candidate_id"]
    )
    assert "assessment_materialization_review" in [
        flag
        for candidate in replayed.payload["candidates"]
        for flag in candidate.get("flags") or []
    ]


def test_the_authored_surface_excludes_every_audit_channel():
    payload = {
        "candidates": [
            {
                "question": "clean",
                "flags": ["QINV-0001"],
                "authority": {"review_flags": ["QINV-0001"]},
                "_aegis_assessment_materialization": {
                    "rationale": "QINV-0001",
                },
            },
        ],
        "refinements": {"review_flags": ["QINV-0001"]},
    }
    surface = run._authored_surface(payload)
    assert "QINV-0001" not in json.dumps(surface)
    assert surface["candidates"][0]["question"] == "clean"
    assert "refinements" not in surface


# --------------------------------------------------------------------------- #
# Explicit origin, and the POST lane's stillness
# --------------------------------------------------------------------------- #

def test_generated_questions_upload_with_the_explicit_origin(db):
    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    release = _run_generated(db, job, count=1)

    assert svc.QUESTION_ORIGIN_PRE_LEARNING == "pre_learning_generated"
    assert svc._question_origin(release.payload["candidates"][0]) == (
        svc.QUESTION_ORIGIN_PRE_LEARNING
    )
    # A reused source candidate keeps the long-standing value, and an
    # unknown declaration is refused rather than written through.
    assert svc._question_origin({"candidate_id": "C"}) == (
        svc.QUESTION_ORIGIN_ASSESSMENT_RELEASE
    )
    with pytest.raises(svc.UploadRefused):
        svc._question_origin({"candidate_id": "C", "origin": "smuggled"})


def test_the_post_lane_is_unmoved(db):
    """A source-question release behaves exactly as before."""

    from tests.test_assessment_release_run import _make_job

    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities(db, chapter)

    release = run.run_release_for_job(
        db, job.id, owner_sub=OWNER, authorities=authorities,
        **_decision_context())

    payload = release.payload
    assert [atom["source_qid"] for atom in payload["source_atoms"]] == [
        "QINV-0001", "QINV-0002",
    ]
    for candidate in payload["candidates"]:
        assert candidate["source_atom_ids"] == [candidate["source_qid"]]
        # The generated lane's markers are minted only by that lane.
        assert "source_policy" not in candidate
        assert "origin" not in candidate
        assert svc._question_origin(candidate) == (
            svc.QUESTION_ORIGIN_ASSESSMENT_RELEASE
        )
    for cell in payload["blueprint_cells"]:
        assert cell["source_policy"] == "reuse"
        assert "generated_question" not in cell


# --------------------------------------------------------------------------- #
# Mechanics
# --------------------------------------------------------------------------- #

def test_generated_question_records_flattens_in_authored_order():
    flattened = run.generated_question_records({
        "questions": {
            "PRC-0001": [
                {
                    "pre_question_id": "PRC-0001-PRQ-0001",
                    "pre_concept_id": "PRC-0001",
                    "question_id": "PRQ-0001",
                    "question_text": "one",
                    "answer": "a",
                    "rationale": "r",
                },
                {
                    "pre_question_id": "PRC-0001-PRQ-0002",
                    "pre_concept_id": "PRC-0001",
                    "question_id": "PRQ-0002",
                    "question_text": "two",
                    "answer": "b",
                    "rationale": "r",
                },
            ],
            "PRC-0002": [],
        },
        "blocked": {"PRC-0003": "recorded block"},
    })
    assert [row["pre_question_id"] for row in flattened] == [
        "PRC-0001-PRQ-0001", "PRC-0001-PRQ-0002",
    ]
    assert run.generated_question_records({}) == []


def test_source_inventory_qids_reads_the_pre_lane_owner():
    assert run.source_inventory_qids(SOURCE_INVENTORY) == [
        "QINV-0001", "QINV-0002",
    ]
    assert run.source_inventory_qids(
        {"items": [{"qid": "QINV-9", "parent_qid": "QINV-8"}]}
    ) == ["QINV-9", "QINV-8"]


# --------------------------------------------------------------------------- #
# Where the barrier stops the run, and where it must not
# --------------------------------------------------------------------------- #

def test_the_pre_lane_marker_is_read_the_way_the_rest_of_the_tree_reads_it(db):
    """``learning_kind`` is normalised here as it is everywhere else.

    ``build_concepts_release_publication.py:74`` and
    ``build_concepts_release_files.py:142`` both read this same key off
    this same staged payload through ``.strip().lower()``.  An
    exact-match gate would let a payload reading ``"Pre"`` render as a
    Pre workbook in those two lanes while taking the SOURCE lane here —
    the barrier's predicate must never be narrower than the lane's own
    definition elsewhere in the tree.
    """

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    inventory = copy.deepcopy(job.question_inventory)
    inventory[build_concepts_release.RELEASE_KEY]["learning_kind"] = "  Pre  "
    job.question_inventory = inventory
    db.commit()
    assert build_concepts_release.release_payload(job)["learning_kind"] == (
        "  Pre  "
    )
    authorities, _ = _authorities(db, chapter)

    with pytest.raises(run.SourceQuestionLeak):
        run.run_release_for_job(
            db, job.id, owner_sub=OWNER, authorities=authorities,
            **_decision_context())


def test_a_poisoned_staged_concept_snapshot_refuses_before_any_spend(db):
    """A QID already in the staged concept release is a PRE-SPEND pause.

    ``payload["concept_snapshot"]`` is the staged snapshot verbatim, so
    the final barrier is certain to refuse it — but only after every
    stage including the Master Refiner had spent, and again on every
    retry, since the staged payload is immutable and the model verdicts
    replay out of a content-addressed store.  CLAUDE.md reserves
    stopping a run for exactly this shape of defect ("the pre-spend
    source-integrity pauses"), so it is refused before the first call.
    """

    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    build_concepts_release.stage_release(
        db,
        job,
        target_chapter_id=chapter.id,
        records=[
            {
                "topic": "Counting",
                "concept_title": "Counting to ten",
                "concept_details": (
                    "Description: counting to ten (see QINV-0001).\n"
                    "Achieving Mastery: count aloud to ten."
                ),
                "keywords": "count, number",
            },
        ],
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="a staged Pre row that leaked an upstream QID",
    )
    authorities, calls = _generated_authorities()
    before = db.query(models.AssessmentRelease).count()

    with pytest.raises(premap.PreExtractionError) as raised:
        _run_generated(db, job, count=1, authorities=authorities)
    assert "QINV-0001" in str(raised.value)
    # Pre-spend: not one authority was called, and nothing was frozen.
    assert calls == {}
    assert db.query(models.AssessmentRelease).count() == before


def test_the_source_lane_still_refuses_an_atom_cell_cardinality_mismatch(db):
    """``zip`` must not absorb the mismatch the gate exists to detect.

    Both Post cell producers guarantee one cell per atom, so this is
    defence in depth against a bug in them — and what the undetected
    direction (more atoms than cells) loses is trailing SOURCE questions,
    dropped with no flag, which is precisely the R4 silent-loss shape.
    """

    from tests.test_assessment_release_run import _make_job

    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, calls = _authorities(db, chapter)
    original = run.cell_decisions.decide_cells

    def drop_the_last_cell(*args, **kwargs):
        return original(*args, **kwargs)[:-1]

    run_cells = run.cell_decisions
    before = db.query(models.AssessmentRelease).count()
    try:
        run_cells.decide_cells = drop_the_last_cell
        with pytest.raises(run.ReleaseRunError) as raised:
            run.run_release_for_job(
                db, job.id, owner_sub=OWNER, authorities=authorities,
                **_decision_context())
    finally:
        run_cells.decide_cells = original
    assert "source atom" in str(raised.value)
    assert db.query(models.AssessmentRelease).count() == before
    # Refused before materialization spent anything.
    assert "materialize" not in calls


def test_a_generated_cell_with_a_non_integer_count_is_refused_cleanly():
    """The generated lane's own error type, never a bare ``ValueError``.

    ``_bind_generated_cells`` runs BEFORE ``validate_cells`` (which owns
    the robust integer check), so an unguarded ``int()`` here would
    escape ``api/build_assessments.py:437``'s ``ReleaseRunError`` mapping
    and surface as a 500 rather than a 400 naming the broken cell.
    """

    from app.services import assessment_profile

    profile = assessment_profile.resolve(None)
    questions = _questions(1)
    cells = _cells(1, "release:ck")
    cells[0]["count"] = "many"

    with pytest.raises(run.GeneratedLaneError) as raised:
        run._bind_generated_cells(
            questions, cells, profile=profile,
            concept_keys={"release:ck"},
        )
    assert "not an integer" in str(raised.value)
    assert issubclass(run.GeneratedLaneError, run.ReleaseRunError)
