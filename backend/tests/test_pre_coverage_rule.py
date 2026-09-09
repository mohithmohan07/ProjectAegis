"""Register Q30 — the owner's Pre-Learning coverage rule.

Owner ruling, 7 Sep 2026: five Basic and five Intermediate generated
questions for every Pre-Learning concept. The rule is a frozen run
variable on the Phase 3 envelope; an envelope that records none keeps the
Q26 posture in full (the golden fixtures replay unchanged). These pin the
one place the numbers live, the plan and authoring contracts under the
rule, the authored tier's transport into the Master's level stage, and the
release audit that holds the staged questions to the rule.
"""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_release_run as run
from app.services import build_concepts_release as release
from app.services import release_qc
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel, pre_coverage, prequestions, prompts

from tests.test_assessment_pre_release_lane import (
    _cells,
    _chapter_with_concepts,
    _generated_authorities,
    _pre_job,
    _questions,
    _run_generated,
    _staged_concept_key,
)
from tests.test_phase3_prequestions import (
    _plan_response,
    _questions_for,
    _verified_critic,
    golden_envelope,  # noqa: F401 - fixture re-export
    pre_map,  # noqa: F401 - fixture re-export
)


RULE = pre_coverage.owner_rule()
TIERS = list(RULE["per_tier"])
TOTAL = pre_coverage.total(RULE)


def _ruled(env: dict) -> dict:
    """The golden envelope with the owner's rule recorded and resealed."""

    out = copy.deepcopy(env)
    out["metadata"] = pre_coverage.stamp(out.get("metadata") or {})
    out["envelope_sha256"] = envelope_mod.seal_sha256(out)
    return envelope_mod.validate(out)


def _rule_plan(rationale: str = "the Basic five fix the vocabulary; the Intermediate five apply it"):
    return {
        "total": TOTAL,
        "split": [
            {"tier": tier, "count": count}
            for tier, count in RULE["per_tier"].items()
        ],
        "rationale": rationale,
    }


def _tiered_questions(concept_id: str, split: dict[str, int]) -> list[dict]:
    """Authored rows in the shape ``prequestions.build`` records them."""

    rows = _questions_for(concept_id, sum(split.values()))
    position = 0
    for tier, count in split.items():
        for _ in range(count):
            rows[position]["tier"] = tier
            rows[position]["pre_concept_id"] = concept_id
            rows[position]["pre_question_id"] = prequestions.pre_question_id(
                concept_id, rows[position]["question_id"],
            )
            position += 1
    return rows


def _ruled_provider(plans=None, split_by_concept=None):
    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prequestions.plan":
            wanted = [
                str(row.get("pre_concept_id") or "")
                for row in request.get("pre_concepts") or []
            ]
            return {"plans": [
                {"pre_concept_id": cid, **copy.deepcopy(
                    (plans or {}).get(cid) or _rule_plan()
                )}
                for cid in wanted
            ]}
        concept_id = str(
            (request.get("pre_concept") or {}).get("pre_concept_id") or ""
        )
        split = (split_by_concept or {}).get(concept_id) or dict(
            RULE["per_tier"]
        )
        return {"questions": _tiered_questions(concept_id, split)}

    return provider


# --------------------------------------------------------------------------- #
# 1. One place for the numbers; the envelope records the posture
# --------------------------------------------------------------------------- #

def test_the_owner_rule_is_five_basic_and_five_intermediate():
    assert RULE == {
        "version": "pre-coverage-owner-2026-09-07",
        "per_tier": {"Basic": 5, "Intermediate": 5},
    }
    assert pre_coverage.total(RULE) == 10
    assert pre_coverage.describe(RULE) == "5 Basic + 5 Intermediate (10 in all)"
    assert pre_coverage.tiers(RULE) == ("Basic", "Intermediate")


def test_the_golden_envelope_records_no_rule_and_a_stamped_one_does(
    golden_envelope,
):
    assert pre_coverage.rule_for(golden_envelope) is None
    ruled = _ruled(golden_envelope)
    assert pre_coverage.rule_for(ruled) == RULE
    # The rule is inside the seal: the sealed envelope changed identity.
    assert ruled["envelope_sha256"] != golden_envelope["envelope_sha256"]
    # A rule the caller already supplied is kept, not overwritten.
    supplied = {"version": "profile-x", "per_tier": {"Advanced": 2}}
    stamped = pre_coverage.stamp({pre_coverage.RULE_FIELD: supplied})
    assert stamped[pre_coverage.RULE_FIELD] == supplied


@pytest.mark.parametrize("broken", [
    {"version": "", "per_tier": {"Basic": 5}},
    {"version": "v", "per_tier": {}},
    {"version": "v", "per_tier": {"Basic": 0}},
    {"version": "v", "per_tier": {"Basic": True}},
    {"version": "v", "per_tier": {"Easy": 5}},
    "not an object",
])
def test_a_malformed_recorded_rule_fails_closed(broken):
    with pytest.raises(pre_coverage.CoverageRuleError):
        pre_coverage.validate(broken)


def test_the_production_envelope_seam_stamps_the_rule():
    """The stamp is applied where a production envelope is built."""
    import inspect

    from app.services import concept_topology_contract as topology

    source = inspect.getsource(topology._run_rewritten_phase3)
    assert "p3_coverage.stamp(kwargs.get(\"meta\") or {})" in source


# --------------------------------------------------------------------------- #
# 2. The plan under the rule
# --------------------------------------------------------------------------- #

def test_the_plan_checker_holds_every_plan_to_the_rule():
    checker = prequestions._plan_checker(["PRC-0001"], RULE)
    assert checker({"plans": [{"pre_concept_id": "PRC-0001", **_rule_plan()}]}) == []
    defects = checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 8,
        "split": [
            {"tier": "Basic", "count": 5}, {"tier": "Intermediate", "count": 3},
        ],
        "rationale": "the model's own judgment of depth",
    }]})
    assert len(defects) == 1
    assert "plans 8 (5 Basic, 3 Intermediate)" in defects[0]
    assert RULE["version"] in defects[0]
    assert "5 Basic + 5 Intermediate (10 in all)" in defects[0]
    # Ten questions in the wrong tiers is not the rule either.
    defects = checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 10,
        "split": [
            {"tier": "Basic", "count": 5}, {"tier": "Advanced", "count": 5},
        ],
        "rationale": "stated",
    }]})
    assert any("fixes every pre-learning concept" in d for d in defects)


def test_a_zero_plan_is_still_the_recorded_drop_request_under_the_rule():
    checker = prequestions._plan_checker(["PRC-0001"], RULE)
    assert checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 0,
        "split": [],
        "rationale": "nothing here is worth verifying; drop the concept",
    }]}) == []


def test_without_a_rule_the_checker_is_the_q26_checker():
    checker = prequestions._plan_checker(["PRC-0001"])
    assert checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 8,
        "split": [
            {"tier": "Basic", "count": 5}, {"tier": "Intermediate", "count": 3},
        ],
        "rationale": "the model's own judgment of depth",
    }]}) == []


def test_the_rule_prose_states_the_owners_numbers_and_the_exception():
    prose = prequestions._plan_rules("", RULE)
    assert "COVERAGE RULE (owner ruling, register Q30" in prose
    assert RULE["version"] in prose
    assert "exactly 5 Basic + 5 Intermediate (10 in all)" in prose
    assert "total 10" in prose
    assert "request to DROP the concept" in prose
    assert "questions per concept" not in prose
    # And the Q26 prose is untouched for an envelope with no rule.
    assert "there are no quotas" in prequestions._plan_rules("")
    assert "COVERAGE RULE" not in prequestions._plan_rules("")


# --------------------------------------------------------------------------- #
# 3. Authoring under the rule: the tier is authored and counted
# --------------------------------------------------------------------------- #

def test_the_author_checker_counts_each_tier_of_the_plans_split():
    split = dict(RULE["per_tier"])
    checker = prequestions._author_checker(
        "PRC-0001", TOTAL, rule=RULE, split=split,
    )
    assert checker({"questions": _tiered_questions("PRC-0001", split)}) == []

    lopsided = checker({
        "questions": _tiered_questions(
            "PRC-0001", {"Basic": 6, "Intermediate": 4},
        ),
    })
    assert any("authored 6 Basic" in d and "asks for 5" in d for d in lopsided)
    assert any("authored 4 Intermediate" in d for d in lopsided)

    untiered = _questions_for("PRC-0001", TOTAL)
    defects = checker({"questions": untiered})
    assert any("carries tier <empty>" in d for d in defects)

    wrong_tier = _tiered_questions("PRC-0001", {"Basic": 5, "Advanced": 5})
    defects = checker({"questions": wrong_tier})
    assert any("carries tier Advanced" in d for d in defects)


def test_build_under_the_rule_authors_tiered_questions(golden_envelope, pre_map):
    ruled = _ruled(golden_envelope)
    store = kernel.DecisionStore()
    result = prequestions.build(
        ruled, pre_map, provider=_ruled_provider(),
        critic=_verified_critic, store=store,
    )
    assert result["coverage_rule"] == RULE
    assert result["blocked"] == {}
    for concept_id, plan in result["plans"].items():
        assert plan["total"] == TOTAL
        assert {e["tier"]: e["count"] for e in plan["split"]} == RULE["per_tier"]
        rows = result["questions"][concept_id]
        assert len(rows) == TOTAL
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["tier"]] = counts.get(row["tier"], 0) + 1
            assert set(row) == {
                "pre_question_id", "question_id", "pre_concept_id",
                "question_text", "answer", "rationale", "tier",
            }
        assert counts == RULE["per_tier"]
    # The rule rides both payloads, so the decision keys move with it.
    plan_payloads = [
        store.get(key) for key in store.keys()
    ]
    assert plan_payloads


def test_build_without_a_rule_authors_no_tier(golden_envelope, pre_map):
    from tests.test_phase3_prequestions import _build

    result = _build(golden_envelope, pre_map)
    assert "coverage_rule" not in result
    for rows in result["questions"].values():
        for row in rows:
            assert "tier" not in row


def test_a_plan_off_the_rule_blocks_and_names_the_rule(golden_envelope, pre_map):
    ruled = _ruled(golden_envelope)
    off = {
        "PRC-0001": {
            "total": 8,
            "split": [
                {"tier": "Basic", "count": 5},
                {"tier": "Intermediate", "count": 3},
            ],
            "rationale": "eight is what the evidence supports",
        },
    }
    result = prequestions.build(
        ruled, pre_map, provider=_ruled_provider(plans=off),
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    # The chapter-wide plan could not be made to contract: every concept
    # is blocked by name, the run completes, and the block names the rule.
    assert set(result["blocked"]) == {
        str(row["_pre_concept_id"]) for row in pre_map["rows"]
    }
    assert all(RULE["version"] in block for block in result["blocked"].values())
    assert result["questions"] == {}


def test_the_prompts_say_when_the_tier_is_authored():
    assert "coverage_rule" in prompts.PREQUESTIONS_PLAN_SYSTEM
    assert "coverage_rule" in prompts.PREQUESTIONS_AUTHOR_SYSTEM
    assert "coverage_rule" in prompts.PREQUESTIONS_CRITIC_SYSTEM
    # The no-rule sentence keeps its exact words.
    assert "tier or a difficulty" in prompts.PREQUESTIONS_AUTHOR_SYSTEM


# --------------------------------------------------------------------------- #
# 4. The Master's level stage transports the authored tier
# --------------------------------------------------------------------------- #

def test_the_master_groups_by_the_authored_tier_without_a_level_verdict(db):
    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    questions = _questions(TOTAL, concept="PRC-0001")
    position = 0
    for tier, count in RULE["per_tier"].items():
        for _ in range(count):
            questions[position]["tier"] = tier
            position += 1
    authorities, calls = _generated_authorities()
    released = _run_generated(
        db, job, questions=questions,
        cells=_cells(TOTAL, _staged_concept_key(db, job)),
        authorities=authorities,
    )

    # No level verdict was asked for: the tier is the authoring decision.
    assert "level" not in calls
    candidates = released.payload["candidates"]
    assert len(candidates) == TOTAL
    assert {candidate["question_source"] for candidate in candidates} == {"UpSchool DB"}
    by_tier: dict[str, int] = {}
    for candidate in candidates:
        audit = candidate[run._LEVEL_AUDIT_FIELD]
        assert audit["authority"].get("policy_version")
        assert "decision_key" not in audit["authority"]
        assert "register Q30" in audit["rationale"]
        by_tier[audit["tier"]] = by_tier.get(audit["tier"], 0) + 1
    assert by_tier == RULE["per_tier"]
    occupied = [
        group for group in released.payload["groups"]
        if group.get("question_labels") or group.get("member_candidate_ids")
        or group.get("members")
    ]
    assert {group["group_type"] for group in occupied} == set(TIERS)


def test_untiered_generated_questions_still_get_the_independent_verdict(db):
    chapter = _chapter_with_concepts(db)
    job = _pre_job(db, chapter)
    authorities, calls = _generated_authorities()
    _run_generated(db, job, count=2, authorities=authorities)
    assert len(calls.get("level") or []) == 2


# --------------------------------------------------------------------------- #
# 5. Release QC holds the staged questions to the rule
# --------------------------------------------------------------------------- #

def _qc_payload(questions, *, rule=RULE, records=None):
    return {
        "records": records if records is not None else [{
            "topic": "Counting",
            "concept_title": "Counting to ten",
            "_pre_concept_id": "PRC-0001",
            release.PRE_ROW_GENERATED_QUESTIONS_FIELD: [
                q["pre_question_id"] for q in questions
            ],
        }],
        "issues": [],
        "type_case_rows": [],
        release.RELEASE_LANE_FIELD: release.LANE_PRE,
        "directory_metadata": {"chapter_duration": "40 minutes"},
        "source_book": "NCERT",
        "pre_question_plans": {"PRC-0001": _rule_plan()},
        "pre_question_blocks": {},
        "pre_coverage_rule": rule,
        "generated_questions": questions,
    }


def test_release_qc_blocks_a_concept_tiered_off_the_rule():
    lopsided = _tiered_questions("PRC-0001", {"Basic": 4, "Intermediate": 6})
    issues, blocking = release_qc.audit(_qc_payload(lopsided))
    named = [
        i for i in issues if i["code"] == release_qc.PRE_CONCEPT_COVERAGE_OFF_RULE
    ]
    assert len(named) == 1
    assert "4 Basic, 6 Intermediate" in named[0]["message"]
    assert RULE["version"] in named[0]["message"]
    assert len(blocking) == 1

    exact = _tiered_questions("PRC-0001", dict(RULE["per_tier"]))
    issues, blocking = release_qc.audit(_qc_payload(exact))
    assert issues == []
    assert blocking == []


def test_release_qc_is_dormant_without_a_rule_or_without_tiers():
    untiered = _questions_for("PRC-0001", 3)
    for row in untiered:
        row["pre_question_id"] = f"PRC-0001-{row['question_id']}"
        row["pre_concept_id"] = "PRC-0001"
    issues, blocking = release_qc.audit(_qc_payload(untiered))
    assert issues == []
    assert blocking == []
    lopsided = _tiered_questions("PRC-0001", {"Basic": 4, "Intermediate": 6})
    issues, blocking = release_qc.audit(_qc_payload(lopsided, rule=None))
    assert issues == []
    assert blocking == []


def test_staging_carries_the_rule_and_the_questions_into_the_audit(db):
    from tests.test_pre_release_lane_wiring import _pre_map, _snapshot_job

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    lopsided = _tiered_questions("PRC-0001", {"Basic": 4, "Intermediate": 6})
    for row in lopsided:
        row["pre_question_id"] = f"PRC-0001-{row['question_id']}"
        row["pre_concept_id"] = "PRC-0001"
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id,
        pre_map=_pre_map(),
        pre_questions={
            "plans": {"PRC-0001": _rule_plan()},
            "questions": {"PRC-0001": lopsided},
            "blocked": {},
            "review_flags": {},
            "decision_flags": {},
            "coverage_rule": RULE,
        },
        reason="off the rule",
    )
    db.refresh(job)
    payload = release.release_payload(job, lane=release.LANE_PRE)
    defects = release.structural_defects(payload)
    assert any(release_qc.PRE_CONCEPT_COVERAGE_OFF_RULE in d for d in defects)
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
