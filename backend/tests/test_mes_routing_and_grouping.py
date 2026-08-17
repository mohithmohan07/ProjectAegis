"""MES PR 4 — routing, grouping, descriptions, QA, Tag New (spec §6–§8)."""
from __future__ import annotations

import json

import pytest

from app.services import assessment_grouping as ag
from app.services import assessment_quality as aq
from app.services import assessment_routing as ar
from app.services.phase3 import kernel

META = {"subject": "Mathematics", "grade": "06"}
ENVELOPE_SHA256 = "e" * 64


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


def _grouping_concept() -> dict:
    return {
        "concept_key": "db:1",
        "concept_id": "db:1",
        "concept_display_name": "Solid Shapes",
        "concept_title": "Solid Shapes",
        "description": "Cones, cubes, spheres and their faces.",
    }


def _verified_critic(payload):
    assert "proposed_decision" in payload
    return {"verdict": "verified", "confidence": 1.0, "issues": []}


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
# Model-authored levels and identity (Stage 8, §8)
# --------------------------------------------------------------------------- #

def test_level_is_a_model_verdict_even_when_it_contradicts_difficulty():
    calls = []

    def provider(payload):
        calls.append(payload)
        return {
            "candidate_id": "CAND-1",
            "tier": "Advanced",
            "rationale": "The learner must synthesize the supplied evidence.",
        }

    result = ag.decide_levels(
        [{"candidate": _candidate(difficulty="Less"),
          "concept": _grouping_concept()}],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert len(calls) == 1
    assert result[0]["tier"] == "Advanced"
    assert calls[0]["candidate"].get("difficulty") is None
    assert calls[0]["candidate"].get("cognitive_skill") is None
    assert result[0]["authority"]["policy_version"] == (
        ag.LEVEL_POLICY_VERSION)
    assert "There is no quota" in ag.LEVEL_SYSTEM


def test_level_payload_carries_complete_untruncated_semantic_evidence():
    long_source = "source-evidence-" * 300
    rubric = [{"criterion": "Names the property", "weightage": 1}]
    sub_questions = [{
        "text": "Explain how the face tapers.",
        "marks": 1,
        "rubric": "Connect the face to the apex.",
    }]
    route = {"basis": "recorded", "evidence": "The concept teaches cones."}
    assets = [{"url": "https://example.test/cone.png", "alt": "A cone"}]
    seen = {}

    def provider(payload):
        seen.update(payload["candidate"])
        return {
            "candidate_id": "CAND-1",
            "tier": "Basic",
            "rationale": "The question asks for direct identification.",
        }

    ag.decide_levels(
        [{"candidate": _candidate(
            rubric=rubric,
            sub_questions=sub_questions,
            source_atom_ids=["QINV-1"],
            source_evidence=long_source,
            route_evidence=route,
            assets=assets,
            image_manifest=[{"asset_id": "IMG-1"}],
        ), "concept": _grouping_concept()}],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert seen["question"] == _candidate()["question"]
    assert seen["question_text"] == _candidate()["question_text"]
    assert seen["answers"] == _candidate()["answers"]
    assert seen["rubric"] == rubric
    assert seen["sub_questions"] == sub_questions
    assert seen["source_evidence"] == long_source
    assert seen["route_evidence"] == route
    assert seen["assets"] == assets
    assert seen["image_manifest"] == [{"asset_id": "IMG-1"}]
    assert "sub_question_count" not in seen


def test_level_decide_once_replays_without_provider_or_critic_calls():
    calls = {"provider": 0, "critic": 0}
    store = kernel.DecisionStore()
    units = [
        {"candidate": _candidate("CAND-2"),
         "concept": _grouping_concept()},
        {"candidate": _candidate("CAND-1"),
         "concept": _grouping_concept()},
    ]

    def provider(payload):
        calls["provider"] += 1
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Intermediate",
            "rationale": "Recorded once.",
        }

    def critic(payload):
        calls["critic"] += 1
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    first = ag.decide_levels(
        units, meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=provider, critic=critic, store=store, fixer=None)
    second = ag.decide_levels(
        units, meta=META, envelope_sha256=ENVELOPE_SHA256,
        provider=provider, critic=critic, store=store, fixer=None)

    assert first == second
    assert [row["candidate_id"] for row in first] == ["CAND-2", "CAND-1"]
    assert calls == {"provider": 2, "critic": 2}
    assert "created_at" not in first[0]["authority"]
    assert "provider" not in first[0]["authority"]


def test_level_changed_evidence_gets_a_new_recorded_verdict():
    calls = []
    store = kernel.DecisionStore()

    def provider(payload):
        calls.append(payload["candidate"]["question"])
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Intermediate",
            "rationale": "The current complete evidence supports this tier.",
        }

    def decide(question):
        return ag.decide_levels(
            [{
                "candidate": _candidate("CAND-1", question=question),
                "concept": _grouping_concept(),
            }],
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=provider,
            critic=_verified_critic,
            store=store,
            fixer=None,
        )

    first = decide("Identify the solid.")
    second = decide("Explain how the solid occupies space.")

    assert len(calls) == 2
    assert first[0]["authority"]["decision_key"] != (
        second[0]["authority"]["decision_key"]
    )


def test_level_critic_dissent_is_advisory_and_never_reauthors():
    calls = {"provider": 0, "critic": 0}

    def provider(payload):
        calls["provider"] += 1
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Advanced",
            "rationale": "The response requires synthesis.",
        }

    def critic(_payload):
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 0.5,
            "issues": ["The expected response may require less synthesis."],
        }

    result = ag.decide_levels(
        [{"candidate": _candidate(), "concept": _grouping_concept()}],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert calls == {"provider": 1, "critic": 1}
    assert result[0]["tier"] == "Advanced"
    assert any("dissent" in flag for flag in result[0]["flags"])


def test_level_contract_defect_reaches_the_fixer_once():
    calls = {"provider": 0, "fixer": 0}

    def provider(payload):
        calls["provider"] += 1
        return {
            "candidate_id": payload["candidate"]["candidate_id"],
            "tier": "Unbound tier",
            "rationale": "Malformed on purpose.",
        }

    def fixer(payload):
        calls["fixer"] += 1
        return {
            "candidate_id": payload["original_payload"]["candidate"][
                "candidate_id"
            ],
            "tier": "Intermediate",
            "rationale": "The complete evidence supports this tier.",
        }

    result = ag.decide_levels(
        [{"candidate": _candidate(), "concept": _grouping_concept()}],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )

    assert calls == {"provider": 3, "fixer": 1}
    assert result[0]["tier"] == "Intermediate"
    assert result[0]["authority"]["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in result[0]["flags"])


def test_group_identity_follows_the_mes_naming_contract():
    key = ag.group_key_for("06MSBMA_3DS_PL_T01_SolidShapes", "Basic", 2)
    assert key == "(06MSBMA_3DS_PL_T01_SolidShapes) BG02"
    record = ag.group_record(
        concept_id=1, concept_machine_id="C1", concept_name="Solid Shapes",
        tier="Advanced", sequence=1, member_candidate_ids=["CAND-1"])
    assert record["group_key"] == "(C1) AG01"
    assert record["group_name"] == record["group_display_name"] == (
        "Solid Shapes — Advanced")
    assert record["group_status"] == "Active"
    assert record["group_sequence"] == 1


def test_visible_group_name_requires_an_explicit_concept_name():
    with pytest.raises(ag.GroupingError, match="explicit concept"):
        ag.group_record(
            concept_id=1, concept_machine_id="C1", concept_name="",
            tier="Basic", sequence=1, member_candidate_ids=[])


def test_every_concept_carries_the_three_required_shells():
    shells = ag.required_shells(1, "C1", "Solid Shapes")
    assert [s["group_key"] for s in shells] == [
        "(C1) BG01", "(C1) IG01", "(C1) AG01"]
    assert [s["group_name"] for s in shells] == [
        "Solid Shapes — Basic",
        "Solid Shapes — Intermediate",
        "Solid Shapes — Advanced",
    ]
    assert all(s["group_name"] == s["group_display_name"] for s in shells)
    assert all(s["semantic_description"] == "NA" for s in shells)
    assert all(s["member_candidate_ids"] == [] for s in shells)


# --------------------------------------------------------------------------- #
# Variant clustering (Stage 9)
# --------------------------------------------------------------------------- #

def test_singleton_clustering_is_still_a_recorded_model_verdict():
    calls = []

    def provider(payload):
        calls.append(payload)
        return {
            "concept_key": "db:1",
            "tier": "Basic",
            "families": [{
                "existing_group_key": "",
                "family": "identify a cone",
                "member_candidate_ids": ["CAND-1"],
            }],
            "rationale": "This is the only supplied family member.",
        }

    result = ag.cluster_tier(
        [_candidate("CAND-1")],
        concept=_grouping_concept(),
        tier="Basic",
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert len(calls) == 1
    assert result["families"][0]["member_candidate_ids"] == ["CAND-1"]
    assert result["authority"]["policy_version"] == (
        ag.VARIANT_CLUSTER_POLICY_VERSION)
    assert "There is no quota" in ag.CLUSTER_SYSTEM


def test_clustering_partition_defects_reach_fixer_and_zero_loss_holds():
    calls = {"provider": 0, "fixer": 0, "critic": 0}
    blocked = []

    def provider(payload):
        calls["provider"] += 1
        return {
            "concept_key": "db:1",
            "tier": "Basic",
            "families": [{
                "existing_group_key": "",
                "family": "broken family",
                "member_candidate_ids": ["CAND-1", "CAND-1"],
            }],
            "rationale": "Malformed on purpose.",
        }

    def fixer(payload):
        calls["fixer"] += 1
        blocked.extend(payload["blocked_check"])
        return {
            "concept_key": "db:1",
            "tier": "Basic",
            "families": [
                {"existing_group_key": "", "family": "identify",
                 "member_candidate_ids": ["CAND-1"]},
                {"existing_group_key": "", "family": "explain",
                 "member_candidate_ids": ["CAND-2"]},
            ],
            "rationale": "Every candidate now appears exactly once.",
        }

    def critic(payload):
        calls["critic"] += 1
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    result = ag.cluster_tier(
        [_candidate("CAND-1"), _candidate("CAND-2")],
        concept=_grouping_concept(),
        tier="Basic",
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )

    assert calls == {"provider": 3, "fixer": 1, "critic": 1}
    assert any("unclustered candidates" in defect for defect in blocked)
    assert any("more than one family" in defect for defect in blocked)
    assert [family["member_candidate_ids"] for family in result["families"]] == [
        ["CAND-1"], ["CAND-2"]]
    assert result["authority"]["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in result["flags"])


def test_clustering_critic_dissent_is_advisory_and_never_retries():
    calls = {"provider": 0, "critic": 0}

    def provider(payload):
        calls["provider"] += 1
        return {
            "concept_key": "db:1",
            "tier": "Basic",
            "families": [{
                "existing_group_key": "",
                "family": "identify solids",
                "member_candidate_ids": ["CAND-1", "CAND-2"],
            }],
            "rationale": "Both questions require the same response shape.",
        }

    def critic(payload):
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 0.7,
            "issues": ["The visual dependence may differ."],
        }

    result = ag.cluster_tier(
        [_candidate("CAND-1"), _candidate("CAND-2")],
        concept=_grouping_concept(),
        tier="Basic",
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert calls == {"provider": 1, "critic": 1}
    assert result["families"][0]["member_candidate_ids"] == [
        "CAND-1", "CAND-2"]
    assert any("dissent" in flag for flag in result["flags"])
    assert any("visual dependence" in flag for flag in result["flags"])


def test_clustering_can_join_an_existing_group_for_tag_new():
    result = ag.cluster_tier(
        [_candidate("CAND-9")],
        concept=_grouping_concept(),
        tier="Basic",
        existing_groups=[{
            "group_key": "(C1) BG01",
            "concept_key": "db:1",
            "group_type": "Basic",
            "semantic_description": "identify solids",
        }],
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=lambda payload: {
            "concept_key": "db:1",
            "tier": "Basic",
            "families": [{
                "existing_group_key": "(C1) BG01",
                "family": "identify solids",
                "member_candidate_ids": ["CAND-9"],
            }],
            "rationale": "The new question is a variant of the group.",
        },
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert result["families"][0]["existing_group_key"] == "(C1) BG01"


def test_partition_remains_fail_closed_when_fixer_cannot_cover_every_id():
    malformed = {
        "concept_key": "db:1",
        "tier": "Basic",
        "families": [{
            "existing_group_key": "",
            "family": "incomplete",
            "member_candidate_ids": ["CAND-1"],
        }],
        "rationale": "CAND-2 is absent.",
    }

    with pytest.raises(kernel.ContractError, match="Fixer could not"):
        ag.cluster_tier(
            [_candidate("CAND-1"), _candidate("CAND-2")],
            concept=_grouping_concept(),
            tier="Basic",
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=lambda payload: malformed,
            critic=_verified_critic,
            store=kernel.DecisionStore(),
            fixer=lambda payload: malformed,
        )


# --------------------------------------------------------------------------- #
# Descriptions (Stage 10, §8.3)
# --------------------------------------------------------------------------- #

def test_empty_shell_description_is_na_without_any_call():
    calls = []
    result = ag.describe_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=lambda payload: calls.append(payload),
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )
    assert result == {"description": "NA", "flags": [], "authority": {}}
    assert calls == []


def test_occupied_group_description_is_a_recorded_kernel_verdict():
    seen = {}

    def provider(payload):
        seen.update(payload)
        return {
            "group_key": "(C1) BG01",
            "description": (
                "Visual classification of everyday objects as two- or "
                "three-dimensional shapes."
            ),
            "rationale": "The member asks for classification from a figure.",
        }

    result = ag.describe_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [_candidate("CAND-1")],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert result["flags"] == []
    assert result["description"].startswith("Visual classification")
    assert seen["members"][0]["answers"] == _candidate()["answers"]
    assert "sub_question_count" not in seen["members"][0]
    assert result["authority"]["policy_version"] == (
        ag.GROUP_DESCRIPTION_POLICY_VERSION)
    assert "HOW" in ag.DESCRIBE_SYSTEM and "WHAT" in ag.DESCRIBE_SYSTEM
    assert "3 questions" not in ag.DESCRIBE_SYSTEM


def test_description_critic_dissent_flags_without_reauthoring():
    calls = {"provider": 0, "critic": 0}
    authored = "Explaining how a cone's surface narrows to its apex."

    def provider(payload):
        calls["provider"] += 1
        return {
            "group_key": payload["group"]["group_key"],
            "description": authored,
            "rationale": "The member asks for a geometric explanation.",
        }

    def critic(_payload):
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 0.5,
            "issues": ["The description may need to mention visual evidence."],
        }

    result = ag.describe_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [_candidate("CAND-1")],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert calls == {"provider": 1, "critic": 1}
    assert result["description"] == authored
    assert any("dissent" in flag for flag in result["flags"])


def test_description_contract_defect_reaches_the_fixer_once():
    calls = {"provider": 0, "fixer": 0}
    blocked = []

    def provider(_payload):
        calls["provider"] += 1
        return {
            "group_key": "(C1) BG01",
            "description": "Assessment family (C1) BG01",
            "rationale": "Leaks the supplied machine identity on purpose.",
        }

    def fixer(payload):
        calls["fixer"] += 1
        blocked.extend(payload["blocked_check"])
        return {
            "group_key": payload["original_payload"]["group"]["group_key"],
            "description": "Explaining how a cone narrows to its apex.",
            "rationale": "The complete member evidence supports this wording.",
        }

    result = ag.describe_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [_candidate("CAND-1")],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=_verified_critic,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )

    assert calls == {"provider": 3, "fixer": 1}
    assert any("exposes internal identity" in defect for defect in blocked)
    assert result["description"] == (
        "Explaining how a cone narrows to its apex."
    )
    assert result["authority"]["fixer"] is True
    assert any(flag.startswith("fixer:") for flag in result["flags"])


def test_membership_change_makes_the_description_stale():
    record = ag.group_record(
        concept_id=1, concept_machine_id="C1", concept_name="Solid Shapes",
        tier="Basic", sequence=1,
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


@pytest.mark.parametrize("changed_field", [
    {"source_evidence": "different source"},
    {"route_evidence": {"basis": "different recorded route"}},
    {"assets": [{"url": "https://example.test/new.png"}]},
    {"rubric": [{"criterion": "new rubric evidence"}]},
    {"sub_questions": [{"text": "new subquestion", "marks": 1}]},
])
def test_tag_new_hash_covers_all_semantic_evidence(changed_field):
    original = _candidate("CAND-1")
    changed = _candidate("CAND-1", **changed_field)
    assert ag.candidate_content_sha256(changed) != (
        ag.candidate_content_sha256(original))


def test_rewrite_full_processes_everything_without_clearing():
    plan = ag.plan_tagging("rewrite_full", [_candidate("CAND-1")], {})
    assert plan["process"] == ["CAND-1"]
    assert plan["skip"] == []


# --------------------------------------------------------------------------- #
# Touched-group QA (Stage 11)
# --------------------------------------------------------------------------- #

def test_holistic_qa_flags_with_full_sibling_context_and_advisory_dissent():
    calls = {"provider": 0, "critic": 0}
    seen = {}
    tail_sentinel = "complete-evidence-tail"
    member = _candidate(
        "CAND-1", source_evidence=("source passage " * 220) + tail_sentinel
    )

    def provider(payload):
        calls["provider"] += 1
        seen.update(payload)
        return {
            "group_key": "(C1) BG01",
            "flags": [{
                "code": "cohesion",
                "member_candidate_id": "CAND-1",
                "detail": "mixes identification with explanation",
            }],
        }

    def critic(payload):
        calls["critic"] += 1
        return {
            "verdict": "dissent",
            "confidence": 0.6,
            "issues": ["Review the sibling separation as well."],
        }

    result = aq.review_group(
        {"group_key": "(C1) BG01", "group_type": "Basic",
         "semantic_description": "identify solids"},
        [member],
        siblings=[{
            "group": {
                "group_key": "(C1) BG02",
                "semantic_description": "explain solid properties",
            },
            "members": [_candidate("CAND-2")],
        }],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=None,
    )

    assert calls == {"provider": 1, "critic": 1}
    assert result["flags"][0]["code"] == "cohesion"
    assert result["quality_review"] == "flagged"
    assert any(
        "sibling separation" in flag
        for flag in result["authority"]["review_flags"]
    )
    assert seen["members"][0]["answers"] == _candidate()["answers"]
    assert seen["members"][0]["source_evidence"].endswith(tail_sentinel)
    assert "sub_question_count" not in seen["members"][0]
    assert seen["sibling_groups"][0]["members"][0]["question"] == (
        _candidate("CAND-2")["question"])
    assert result["authority"]["policy_version"] == (
        aq.QUALITY_POLICY_VERSION)


def test_empty_group_quality_is_not_applicable_without_any_call():
    def forbidden(_payload):
        raise AssertionError("empty group spent a decision call")

    result = aq.review_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [],
        siblings=[],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=forbidden,
        critic=forbidden,
        store=kernel.DecisionStore(),
        fixer=forbidden,
    )

    assert result == {
        "flags": [],
        "quality_review": "not_applicable",
        "authority": {},
    }


def test_quality_contract_defects_reach_the_fixer_and_stay_flagged():
    calls = {"provider": 0, "fixer": 0, "critic": 0}
    blocked = []

    def provider(_payload):
        calls["provider"] += 1
        return {
            "group_key": "(OTHER) BG01",
            "flags": [{
                "code": "cohesion",
                "member_candidate_id": "UNKNOWN",
                "detail": "The malformed response names another member.",
            }],
        }

    def fixer(payload):
        calls["fixer"] += 1
        blocked.extend(payload["blocked_check"])
        return {
            "group_key": "(C1) BG01",
            "flags": [{
                "code": "cohesion",
                "member_candidate_id": "CAND-1",
                "detail": "The recorded concern is bound to this member.",
            }],
        }

    def critic(_payload):
        calls["critic"] += 1
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    result = aq.review_group(
        {"group_key": "(C1) BG01", "group_type": "Basic"},
        [_candidate("CAND-1")],
        siblings=[],
        concept=_grouping_concept(),
        meta=META,
        envelope_sha256=ENVELOPE_SHA256,
        provider=provider,
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )

    assert calls == {"provider": 3, "fixer": 1, "critic": 1}
    assert any("group_key must echo" in defect for defect in blocked)
    assert any("unknown member_candidate_id" in defect for defect in blocked)
    assert result["flags"][0]["member_candidate_id"] == "CAND-1"
    assert result["quality_review"] == "flagged"
    assert result["authority"]["fixer"] is True
    assert any(
        flag.startswith("fixer:")
        for flag in result["authority"]["review_flags"]
    )


def test_quality_rejects_malformed_sibling_evidence_without_silent_loss():
    with pytest.raises(aq.QualityError, match="sibling 1 member 1"):
        aq.review_group(
            {"group_key": "(C1) BG01", "group_type": "Basic"},
            [_candidate("CAND-1")],
            siblings=[{
                "group": {"group_key": "(C1) BG02"},
                "members": ["not-a-member-record"],
            }],
            concept=_grouping_concept(),
            meta=META,
            envelope_sha256=ENVELOPE_SHA256,
            provider=lambda payload: {
                "group_key": payload["group"]["group_key"],
                "flags": [],
            },
            critic=_verified_critic,
            store=kernel.DecisionStore(),
            fixer=None,
        )


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
