"""MES PR 4 — routing, grouping, descriptions, QA, Tag New (spec §6–§8)."""
from __future__ import annotations

import json

import pytest

from app.services import assessment_grouping as ag
from app.services import assessment_quality as aq
from app.services import assessment_routing as ar

META = {"subject": "Mathematics", "grade": "06"}


def _candidate(candidate_id="CAND-1", **kw) -> dict:
    base = {
        "candidate_id": candidate_id,
        "sheet_kind": "objective",
        "question": "Which solid has one curved face and an apex?",
        "question_text": "Which solid has one curved face and an apex?",
        "answers": [
            {"answer_content": "Cone", "correct_answer": "1",
             "answer_weightage": "1"},
            {"answer_content": "Cube", "correct_answer": "0",
             "answer_weightage": "0"},
        ],
        "sub_questions": [],
        "display_answer": "",
        "source_evidence": "Ex 1(2): name the solid in the figure.",
        "route_evidence": {},
        "question_category": "MCQ", "cognitive_skill": "Remember",
        "difficulty": "Less", "marks": 1.0,
        "requires_visual": False,
    }
    base.update(kw)
    return base


def _concepts() -> list[dict]:
    return [
        {"concept_id": 1, "concept_title": "Solid Shapes",
         "teaching_description": "Cones, cubes, spheres and their faces."},
        {"concept_id": 2, "concept_title": "Plane Figures",
         "teaching_description": "Two-dimensional figures and their sides."},
        {"concept_id": 9, "concept_title": "Culmination - Shapes",
         "teaching_description": "Cross-concept synthesis of every shape "
                                 "idea in the chapter.",
         "is_culmination": True},
    ]


def _accepting_critic(system, user):
    payload = json.loads(user)
    return {"verdict": "accept",
            "proposal_sha256": payload["proposal_sha256"], "feedback": []}


# --------------------------------------------------------------------------- #
# Routing (Stage 7)
# --------------------------------------------------------------------------- #

def test_objective_routing_sees_the_correct_answer_never_distractors():
    evidence = ar.routing_answer_evidence(_candidate())
    assert evidence == {"correct_answer": ["Cone"]}


def test_router_places_one_home_concept_with_bound_critic():
    seen = {}

    def router(system, user):
        seen["user"] = user
        return {"concept_id": 1, "evidence": "faces of solids",
                "reason": "asks about a cone", "confidence": "high"}

    placement = ar.route_candidate(
        _candidate(), _concepts(), meta=META,
        router_call=router, critic_call=_accepting_critic)
    assert placement["concept_id"] == 1
    assert placement["basis"] == "api_router"
    assert placement["secondary_placements"] == []
    # The router payload carried the correct answer, not the distractor.
    assert "Cone" in seen["user"] and "Cube" not in seen["user"]
    assert "Culmination is not a fallback" in ar.ROUTER_SYSTEM


def test_blueprint_concept_constraint_routes_mechanically():
    placement = ar.route_candidate(
        _candidate(blueprint_concept_id=2), _concepts(), meta=META,
        router_call=None, critic_call=None)
    assert placement["concept_id"] == 2
    assert placement["basis"] == "blueprint_constraint"


def test_unresolved_routing_ships_flagged_not_guessed():
    def router(system, user):
        return {"concept_id": 1, "evidence": "", "reason": "",
                "confidence": "low"}

    def rejecting_critic(system, user):
        payload = json.loads(user)
        return {"verdict": "reject",
                "proposal_sha256": payload["proposal_sha256"],
                "feedback": ["the concept does not teach this"]}

    placement = ar.route_candidate(
        _candidate(), _concepts(), meta=META,
        router_call=router, critic_call=rejecting_critic, max_attempts=2)
    assert "unresolved_routing" in placement["flags"]
    assert placement["basis"] == "unresolved"
    # Best evidence-bound route is preserved for review, marked low.
    assert placement["concept_id"] == 1
    assert placement["confidence"] == "low"


def test_route_candidates_enforces_exactly_one_placement_each():
    result = ar.route_candidates(
        [_candidate("CAND-1"), _candidate("CAND-2")],
        [_concepts()[0]], meta=META)
    assert [p["basis"] for p in result["placements"]] == [
        "sole_candidate", "sole_candidate"]
    assert result["routed"] == 2


# --------------------------------------------------------------------------- #
# Tiers and identity (Stage 8, §8)
# --------------------------------------------------------------------------- #

def test_difficulty_maps_mechanically_and_never_guesses():
    assert ag.tier_for_difficulty("Less") == "Basic"
    assert ag.tier_for_difficulty("Moderate") == "Intermediate"
    assert ag.tier_for_difficulty("High") == "Advanced"
    with pytest.raises(ag.GroupingError):
        ag.tier_for_difficulty("Medium")


def test_group_identity_follows_the_mes_naming_contract():
    key = ag.group_key_for("06MSBMA_3DS_PL_T01_SolidShapes", "Basic", 2)
    assert key == "(06MSBMA_3DS_PL_T01_SolidShapes) BG02"
    record = ag.group_record(
        concept_id=1, concept_machine_id="C1", tier="Advanced",
        sequence=1, member_candidate_ids=["CAND-1"])
    assert record["group_name"] == record["group_display_name"] == "(C1) AG01"
    assert record["group_status"] == "Active"
    assert record["group_sequence"] == 1


def test_every_concept_carries_the_three_required_shells():
    shells = ag.required_shells(1, "C1")
    assert [s["group_key"] for s in shells] == [
        "(C1) BG01", "(C1) IG01", "(C1) AG01"]
    assert all(s["semantic_description"] == "NA" for s in shells)
    assert all(s["member_candidate_ids"] == [] for s in shells)


# --------------------------------------------------------------------------- #
# Variant clustering (Stage 9)
# --------------------------------------------------------------------------- #

def test_clustering_accepts_a_verified_exact_partition():
    def author(system, user):
        return {"families": [
            {"existing_group_key": "", "family": "identify the solid",
             "member_candidate_ids": ["CAND-1", "CAND-2"]},
            {"existing_group_key": "", "family": "count faces",
             "member_candidate_ids": ["CAND-3"]},
        ]}

    result = ag.cluster_tier(
        [_candidate("CAND-1"), _candidate("CAND-2"), _candidate("CAND-3")],
        concept_title="Solid Shapes", tier="Basic", meta=META,
        author_call=author, critic_call=_accepting_critic)
    assert result["flags"] == []
    assert [f["member_candidate_ids"] for f in result["families"]] == [
        ["CAND-1", "CAND-2"], ["CAND-3"]]


def test_clustering_rejects_partition_defects_mechanically():
    proposals = iter([
        {"families": [  # CAND-2 missing, CAND-1 twice
            {"family": "a", "member_candidate_ids": ["CAND-1", "CAND-1"]},
        ]},
        {"families": [
            {"family": "a", "member_candidate_ids": ["CAND-1"]},
            {"family": "b", "member_candidate_ids": ["CAND-2"]},
        ]},
    ])
    prompts = []

    def author(system, user):
        prompts.append(user)
        return next(proposals)

    result = ag.cluster_tier(
        [_candidate("CAND-1"), _candidate("CAND-2")],
        concept_title="Solid Shapes", tier="Basic", meta=META,
        author_call=author, critic_call=_accepting_critic)
    assert result["flags"] == []
    assert "unclustered candidates" in prompts[1]


def test_unresolved_clustering_ships_flagged_singletons_never_merges():
    def broken_author(system, user):
        raise RuntimeError("provider down")

    result = ag.cluster_tier(
        [_candidate("CAND-1"), _candidate("CAND-2")],
        concept_title="Solid Shapes", tier="Basic", meta=META,
        author_call=broken_author, critic_call=_accepting_critic)
    assert result["flags"] == ["unresolved_clustering"]
    # Singletons assert no variant relationship: no semantic merge was
    # made deterministically, and nothing disappeared.
    assert [f["member_candidate_ids"] for f in result["families"]] == [
        ["CAND-1"], ["CAND-2"]]


def test_clustering_can_join_an_existing_group_for_tag_new():
    def author(system, user):
        return {"families": [
            {"existing_group_key": "(C1) BG01", "family": "",
             "member_candidate_ids": ["CAND-9"]},
        ]}

    result = ag.cluster_tier(
        [_candidate("CAND-9")],
        concept_title="Solid Shapes", tier="Basic", meta=META,
        existing_groups=[{"group_key": "(C1) BG01",
                          "semantic_description": "identify solids"}],
        author_call=author, critic_call=_accepting_critic)
    # A single candidate short-circuits mechanically; force two to hit API.
    result = ag.cluster_tier(
        [_candidate("CAND-9"), _candidate("CAND-10")],
        concept_title="Solid Shapes", tier="Basic", meta=META,
        existing_groups=[{"group_key": "(C1) BG01",
                          "semantic_description": "identify solids"}],
        author_call=lambda s, u: {"families": [
            {"existing_group_key": "(C1) BG01", "family": "",
             "member_candidate_ids": ["CAND-9", "CAND-10"]},
        ]},
        critic_call=_accepting_critic)
    assert result["families"][0]["existing_group_key"] == "(C1) BG01"


# --------------------------------------------------------------------------- #
# Descriptions (Stage 10, §8.3)
# --------------------------------------------------------------------------- #

def test_empty_shell_description_is_na_without_any_call():
    result = ag.describe_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"}, [], meta=META,
        author_call=None, critic_call=None)
    assert result == {"description": "NA", "flags": [], "authority": {}}


def test_occupied_group_description_is_authored_and_critic_bound():
    result = ag.describe_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [_candidate("CAND-1")], meta=META,
        author_call=lambda s, u: {"description": (
            "Visual classification of everyday objects as two- or "
            "three-dimensional shapes.")},
        critic_call=_accepting_critic)
    assert result["flags"] == []
    assert result["description"].startswith("Visual classification")
    assert "HOW" in ag.DESCRIBE_SYSTEM and "WHAT" in ag.DESCRIBE_SYSTEM


def test_membership_change_makes_the_description_stale():
    record = ag.group_record(
        concept_id=1, concept_machine_id="C1", tier="Basic", sequence=1,
        member_candidate_ids=["CAND-1", "CAND-2"])
    assert not ag.description_is_stale(record, ["CAND-2", "CAND-1"])
    assert ag.description_is_stale(record, ["CAND-1", "CAND-3"])


# --------------------------------------------------------------------------- #
# Aggregates (§8.4) and Tag New (§7)
# --------------------------------------------------------------------------- #

def test_label_aggregates_are_complete_and_ordered():
    placements = [
        {"candidate_id": "CAND-1", "concept_id": 1, "group_key": "(C1) BG01"},
        {"candidate_id": "CAND-2", "concept_id": 1, "group_key": "(C1) BG02"},
        {"candidate_id": "CAND-3", "concept_id": 2, "group_key": "(C2) IG01"},
    ]
    labels = {"CAND-1": "Q01", "CAND-2": "Q02", "CAND-3": "Q03"}
    aggregates = ag.label_aggregates(placements, labels)
    assert aggregates["concept_question_labels"] == {
        1: ["Q01", "Q02"], 2: ["Q03"]}
    assert aggregates["group_question_labels"] == {
        "(C1) BG01": ["Q01"], "(C1) BG02": ["Q02"], "(C2) IG01": ["Q03"]}


def test_tag_new_skips_unchanged_and_touches_prior_groups():
    unchanged = _candidate("CAND-1", question_label="Q01")
    changed = _candidate(
        "CAND-2", question_label="Q02",
        question="A reworded question about spheres.")
    brand_new = _candidate("CAND-3", question_label="Q03")
    existing = {
        "Q01": {"content_sha256": ag.candidate_content_sha256(unchanged),
                "concept_id": 1, "group_key": "(C1) BG01"},
        "Q02": {"content_sha256": "sha-of-the-old-wording",
                "concept_id": 1, "group_key": "(C1) BG02"},
    }
    plan = ag.plan_tagging("tag_new", [unchanged, changed, brand_new], existing)
    assert plan["skip"] == ["CAND-1"]
    assert plan["process"] == ["CAND-2", "CAND-3"]
    assert plan["kept_placements"]["CAND-1"]["group_key"] == "(C1) BG01"
    assert plan["touched_group_keys"] == ["(C1) BG02"]
    # Idempotent: the same input yields the same plan.
    assert ag.plan_tagging(
        "tag_new", [unchanged, changed, brand_new], existing) == plan


def test_rewrite_full_processes_everything_without_clearing():
    plan = ag.plan_tagging("rewrite_full", [_candidate("CAND-1")], {})
    assert plan["process"] == ["CAND-1"]
    assert plan["skip"] == []


# --------------------------------------------------------------------------- #
# Touched-group QA (Stage 11)
# --------------------------------------------------------------------------- #

def test_qa_flags_but_never_blocks():
    result = aq.review_group(
        {"group_key": "(C1) BG01", "group_type": "Basic",
         "semantic_description": "identify solids"},
        [_candidate("CAND-1")], meta=META,
        reviewer_call=lambda s, u: {"flags": [
            {"code": "cohesion", "member_candidate_id": "CAND-1",
             "detail": "mixes identification with explanation"}]})
    assert result["flags"][0]["code"] == "cohesion"

    broken = aq.review_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [_candidate("CAND-1")], meta=META,
        reviewer_call=lambda s, u: (_ for _ in ()).throw(
            RuntimeError("down")))
    assert broken["flags"][0]["code"] == "qa_unavailable"


def test_partition_and_blueprint_coverage_gates():
    groups = [
        {"group_key": "(C1) BG01", "member_candidate_ids": ["CAND-1"]},
        {"group_key": "(C1) BG02", "member_candidate_ids": ["CAND-1"]},
    ]
    errors = aq.partition_errors(groups)
    assert errors and "CAND-1" in errors[0]

    report = aq.blueprint_coverage_report(
        [{"candidate_id": "CAND-1", "blueprint_cell_id": "CELL-1"},
         {"candidate_id": "CAND-2", "blueprint_cell_id": "CELL-9"}],
        [{"cell_id": "CELL-1", "count": 2}])
    assert report["unfulfilled"] == {"CELL-1": 1}
    assert report["orphaned"] == ["CAND-2"]
    assert not report["complete"]
