"""The Q1 chapter analysis inventory pass (phase3/analyse.py).

Phase 2.4 (Build) + Phase 4.3 (Allot) regressions from the step-5 spec:
no count quota anywhere, positional ids, mechanics-only checker, R4
exact-once allotment, advisory critic (Q10), Fixer on exhaustion (Q13),
decide-once replay, and the legality of an empty inventory (a thin
chapter is never padded). The golden replay drives the pass with the
recorded rne_analysis.json fixture.
"""
from __future__ import annotations

import json
import re

import pytest

from app.services.phase3 import analyse, kernel
from tests import test_phase3_settle_golden as settle_golden

GOLDEN = settle_golden.GOLDEN


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return settle_golden.envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def golden_analysis() -> dict:
    return json.loads(
        (GOLDEN / "rne_analysis.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def golden_rows() -> list[dict]:
    return json.loads(
        (GOLDEN / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]


def analyse_replay_provider(golden_analysis: dict):
    """Answer Build with the recorded items and Allot with the recorded
    (topic_id, concept_title) destinations, resolved against the
    request's own settled_concepts payload — exactly the rne_place.json
    replay pattern."""

    items = [dict(item) for item in golden_analysis["items"]]
    by_id = {item["item_id"]: item for item in items}

    def provider(request: dict) -> dict:
        if request.get("stage") == "analyse.inventory":
            return {
                "items": [
                    {
                        key: item[key]
                        for key in (
                            "item_id", "kind", "text", "evidence",
                            "rationale",
                        )
                    }
                    for item in items
                ]
            }
        by_identity = {
            (
                str(row.get("topic_id") or ""),
                _normal(row.get("concept_title")).casefold(),
            ): str(row.get("concept_id") or "")
            for row in request.get("settled_concepts") or []
        }
        allotments = []
        for requested in request.get("items") or []:
            recorded = by_id[str(requested.get("item_id"))]
            destination = recorded["allot_to"]
            allotments.append({
                "item_id": recorded["item_id"],
                "concept_id": by_identity[(
                    str(destination.get("topic_id") or ""),
                    _normal(destination.get("concept_title")).casefold(),
                )],
                "rationale": "replayed from the golden analysis fixture",
            })
        return {"allotments": allotments}

    return provider


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


def _settled(golden_envelope, golden_rows):
    mapping = settle_golden._replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(
        mapping
    )
    from app.services.phase3 import settle

    return settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )


# ---------------------------------------------------------------------------
# golden replay


def test_analyse_reproduces_the_recorded_inventory_and_allotments(
    golden_envelope, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    result = analyse.analyse(
        golden_envelope,
        settled,
        provider=analyse_replay_provider(golden_analysis),
        critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    assert len(result["inventory"]) == len(golden_analysis["items"]) == 94
    assert set(result["allotments"]) == {
        item["item_id"] for item in golden_analysis["items"]
    }
    # R4: every item allotted exactly once (the mapping is total).
    assert all(result["allotments"].values())
    # A verified critic leaves no flags.
    assert result["review_flags"] == {}
    # Item ids are the positional mint.
    assert [item["item_id"] for item in result["inventory"]] == (
        analyse.mint_item_ids(94)
    )


def test_analyse_decide_once_replays_from_the_store(
    golden_envelope, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    calls = {"n": 0}
    base = analyse_replay_provider(golden_analysis)

    def counted(request: dict) -> dict:
        calls["n"] += 1
        return base(request)

    store = kernel.DecisionStore()
    first = analyse.analyse(
        golden_envelope, settled,
        provider=counted, critic=_verified_critic, store=store,
    )
    calls_after_first = calls["n"]
    second = analyse.analyse(
        golden_envelope, settled,
        provider=counted, critic=_verified_critic, store=store,
    )
    assert calls["n"] == calls_after_first
    assert second == first


# ---------------------------------------------------------------------------
# no count quota (Rule 1: no volume-derived structure)


def test_inventory_build_carries_no_count_quota(
    golden_envelope, golden_analysis, golden_rows,
):
    """Neither the system prompt nor the payload may bound how many
    items the model returns — no minimum, maximum, target, or
    per-concept arithmetic of any kind."""
    from app.services.phase3 import prompts

    captured: dict = {}
    base = analyse_replay_provider(golden_analysis)

    def capturing(request: dict) -> dict:
        if request.get("stage") == "analyse.inventory":
            captured.update(request)
        return base(request)

    settled = _settled(golden_envelope, golden_rows)
    analyse.analyse(
        golden_envelope, settled,
        provider=capturing, critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    quota_language = re.compile(
        r"at least \d|at most \d|minimum of|maximum of|exactly \d+ items?|"
        r"per concept|per topic|one per|quota|target count|"
        r"\d+\s*(?:-|to)\s*\d+ items",
        re.IGNORECASE,
    )
    assert not quota_language.search(captured["rules"])
    assert not quota_language.search(prompts.ANALYSE_INVENTORY_SYSTEM)
    # No numeric knob rides the payload either: the evidence carries
    # content, never counts for the model to satisfy.
    assert not any(
        isinstance(value, (int, float))
        for value in captured.values()
        if not isinstance(value, bool)
    ) or all(
        key in {"attempt", "max_attempts"} for key, value in captured.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def test_empty_inventory_is_legal_for_a_thin_chapter(
    golden_envelope, golden_rows,
):
    """A model that judges the chapter supports no genuine item returns
    an empty inventory; the pass ships it — never padded, no allot
    decision spent."""
    settled = _settled(golden_envelope, golden_rows)
    allot_calls = {"n": 0}

    def provider(request: dict) -> dict:
        if request.get("stage") == "analyse.inventory":
            return {"items": []}
        allot_calls["n"] += 1
        return {"allotments": []}

    result = analyse.analyse(
        golden_envelope, settled,
        provider=provider, critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    assert result == {
        "inventory": [],
        "allotments": {},
        "rationales": {},
        "review_flags": {},
    }
    assert allot_calls["n"] == 0


# ---------------------------------------------------------------------------
# checker mechanics


def test_inventory_checker_rejects_dup_ids_bad_kind_and_empty_text():
    check = analyse._inventory_checker()
    defects = check({"items": [
        {"item_id": "LA-0001", "kind": "misconception", "text": "a belief"},
        {"item_id": "LA-0001", "kind": "hunch", "text": ""},
    ]})
    assert any("LA-0002" in d and "positional mint" in d for d in defects)
    assert any("not one of misconception/error_analysis" in d
               for d in defects)
    assert any("empty text" in d for d in defects)
    # Sequential, well-formed ids pass.
    assert check({"items": [
        {"item_id": "LA-0001", "kind": "misconception", "text": "a belief"},
        {"item_id": "LA-0002", "kind": "error_analysis", "text": "a slip"},
    ]}) == []
    # Empty inventory is mechanically valid.
    assert check({"items": []}) == []


def test_allot_checker_demands_exact_once_and_known_concepts():
    check = analyse._allot_checker(
        ["LA-0001", "LA-0002"], {"CONCEPT-0001"}
    )
    defects = check({"allotments": [
        {"item_id": "LA-0001", "concept_id": "CONCEPT-9999"},
    ]})
    assert any("unknown concept_id" in d for d in defects)
    assert any("unallotted item(s): LA-0002" in d for d in defects)
    defects = check({"allotments": [
        {"item_id": "LA-0001", "concept_id": "CONCEPT-0001"},
        {"item_id": "LA-0001", "concept_id": "CONCEPT-0001"},
        {"item_id": "LA-0002", "concept_id": "CONCEPT-0001"},
    ]})
    assert any("unknown or repeated item_id LA-0001" in d for d in defects)


def test_culminations_are_not_offered_as_allotment_targets(
    golden_envelope, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    offered: list[str] = []
    base = analyse_replay_provider(golden_analysis)

    def capturing(request: dict) -> dict:
        if request.get("stage") == "analyse.allot":
            offered.extend(
                str(row.get("concept_title") or "")
                for row in request.get("settled_concepts") or []
            )
        return base(request)

    analyse.analyse(
        golden_envelope, settled,
        provider=capturing, critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    assert offered
    assert not any(
        title.lower().startswith("culmination") for title in offered
    )


# ---------------------------------------------------------------------------
# Q10 critic advisory / Q13 fixer


def test_critic_dissent_flags_but_never_gates(
    golden_envelope, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)

    def dissenting(request: dict) -> dict:
        if request.get("stage") == "analyse.inventory":
            return {
                "verdict": "rejected",
                "confidence": 0.94,
                "issues": ["LA-0003 near-duplicates LA-0004"],
            }
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    result = analyse.analyse(
        golden_envelope, settled,
        provider=analyse_replay_provider(golden_analysis),
        critic=dissenting,
        store=kernel.DecisionStore(),
    )
    # The decision stands: every item still ships, allotted exactly once.
    assert len(result["inventory"]) == 94
    assert set(result["allotments"]) == {
        item["item_id"] for item in result["inventory"]
    }
    # The dissent rides as review flags on the items.
    assert any(
        "near-duplicates" in flag
        for flags in result["review_flags"].values()
        for flag in flags
    )


def test_fixer_engages_on_allot_exhaustion(
    golden_envelope, golden_analysis, golden_rows,
):
    """A provider that never allots an item exhausts the bounded
    corrections; The Fixer's contract-satisfying decision ships flagged
    and the run completes (Q13)."""
    settled = _settled(golden_envelope, golden_rows)
    base = analyse_replay_provider(golden_analysis)

    def broken(request: dict) -> dict:
        if request.get("stage") == "analyse.inventory":
            return base(request)
        response = base(request)
        return {"allotments": response["allotments"][:-1]}  # drops one

    def fixer(request: dict) -> dict:
        original = request["original_payload"]
        response = base(original)
        response["rationale"] = (
            "allotted the dropped item to its best-supported concept"
        )
        return response

    result = analyse.analyse(
        golden_envelope, settled,
        provider=broken, critic=_verified_critic,
        store=kernel.DecisionStore(), fixer=fixer,
    )
    assert set(result["allotments"]) == {
        item["item_id"] for item in result["inventory"]
    }
    assert any(
        flag.startswith("fixer: blocked=")
        for flags in result["review_flags"].values()
        for flag in flags
    )


def test_allot_exhaustion_without_fixer_fails_closed(
    golden_envelope, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    base = analyse_replay_provider(golden_analysis)

    def broken(request: dict) -> dict:
        if request.get("stage") == "analyse.inventory":
            return base(request)
        response = base(request)
        return {"allotments": response["allotments"][:-1]}

    with pytest.raises(kernel.ContractError, match="unallotted item"):
        analyse.analyse(
            golden_envelope, settled,
            provider=broken, critic=_verified_critic,
            store=kernel.DecisionStore(),
        )


# ---------------------------------------------------------------------------
# Q1 rendering: assemble's stamping step


def _stamp_rows() -> list[dict]:
    return [
        {
            "topic": "Light",
            "concept_title": "Rectilinear Propagation Of Light",
            "concept_details": (
                "Description: Light travels in straight lines.\n"
                "Achieving Mastery: Drawing straight-line ray diagrams."
            ),
        },
        {
            "topic": "Light",
            "concept_title": "Reflection From Plane Mirrors",
            "concept_details": (
                "Description: A plane mirror forms an erect virtual "
                "image.\nAchieving Mastery: Locating a mirror image."
            ),
        },
        {
            "topic": "Light",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Light",
            "concept_details": "Description: Recap of the topic.",
        },
    ]


def _stamp(rows, analysis):
    from app.services.phase3 import assemble as assemble_mod
    from app.services.phase3 import place as place_mod

    row_by_place_id = dict(zip(place_mod.mint_concept_ids(rows), rows))
    return assemble_mod.stamp_analysis_allotments(
        rows, analysis, row_by_place_id
    )


def test_stamping_renders_the_section_only_on_allotted_rows():
    rows = _stamp_rows()
    analysis = {
        "inventory": [
            {
                "item_id": "LA-0001",
                "kind": "misconception",
                "text": (
                    "Students may believe light bends around corners on "
                    "its own in air."
                ),
            },
            {
                "item_id": "LA-0002",
                "kind": "error_analysis",
                "text": (
                    "Students may measure the image distance from the "
                    "object instead of from the mirror surface."
                ),
            },
        ],
        "allotments": {"LA-0001": "CONCEPT-0001", "LA-0002": "CONCEPT-0001"},
        "review_flags": {},
    }
    allotments, inventory = _stamp(rows, analysis)
    assert allotments == analysis["allotments"]
    assert len(inventory) == 2
    # The allotted row carries the exact canonical §5 shape — one
    # combined section, Misconceptions before Error Analysis, item texts
    # merged in item-id order, NO visible LA-id in the rendered line.
    details = rows[0]["concept_details"]
    assert details.endswith(
        " // Misconception/ Error Analysis: Misconceptions: Students may "
        "believe light bends around corners on its own in air."
        "; Error Analysis: Students may measure the image distance from "
        "the object instead of from the mirror surface."
    )
    assert "LA-0001" not in details and "LA-0002" not in details
    # The ids ride the row-private audit field instead.
    assert rows[0]["_aegis_analysis_allotments"] == ["LA-0001", "LA-0002"]
    # Unallotted rows carry NO section and no marker.
    assert "Misconception" not in rows[1]["concept_details"]
    assert "_aegis_analysis_allotments" not in rows[1]
    assert "Misconception" not in rows[2]["concept_details"]
    # The stamped shape is exactly what the deposit pipeline's format
    # helper produces — the fixpoint holds on the first pass.
    from app.services import concept_validator as cv
    import copy

    normalized = cv.ensure_valid_learner_analysis(copy.deepcopy(rows))
    assert [row["concept_details"] for row in normalized] == [
        row["concept_details"] for row in rows
    ]


def test_stamping_removes_an_arriving_unallotted_section_verbatim():
    rows = _stamp_rows()
    legacy_text = (
        "Misconceptions: Learners believe mirrors add light of their own."
    )
    rows[1]["concept_details"] += (
        " // Misconception/ Error Analysis: " + legacy_text
    )
    _stamp(rows, {"inventory": [], "allotments": {}})
    # Removed — the inventory is the only mechanism — and flagged with
    # the exact removed text, never silently (R4).
    assert "Misconception" not in rows[1]["concept_details"]
    flags = rows[1]["review_flags"]
    assert any(
        flag.startswith(
            "Q1: removed a learner-analysis section not backed by the "
            "chapter inventory"
        )
        and legacy_text in flag
        for flag in flags
    )
    # The untouched rows saw no change and no flags.
    assert "review_flags" not in rows[0]
    assert "review_flags" not in rows[2]


def test_stamping_is_idempotent_over_allotted_and_unallotted_rows():
    """The deposit pipeline must be a fixpoint over both shapes: an
    unallotted row without a section stays sectionless (cv:908 emits no
    section when both components are empty), and a stamped row's section
    is stable under the format normalizer."""
    import copy

    from app.services import concept_validator as cv

    rows = _stamp_rows()
    analysis = {
        "inventory": [{
            "item_id": "LA-0001",
            "kind": "error_analysis",
            "text": (
                "Students may measure the image distance from the object "
                "instead of from the mirror surface."
            ),
        }],
        "allotments": {"LA-0001": "CONCEPT-0002"},
    }
    _stamp(rows, analysis)
    once = cv.ensure_valid_learner_analysis(copy.deepcopy(rows))
    twice = cv.ensure_valid_learner_analysis(copy.deepcopy(once))
    assert once == twice
    assert "Misconception" not in once[0]["concept_details"]
    assert once[1]["concept_details"].count(
        "Misconception/ Error Analysis"
    ) == 1


def test_stamping_fails_closed_on_broken_accounting():
    from app.services.phase3 import assemble as assemble_mod

    rows = _stamp_rows()
    with pytest.raises(assemble_mod.AssemblyError, match="R4 exact-once"):
        _stamp(rows, {
            "inventory": [{
                "item_id": "LA-0001", "kind": "misconception", "text": "t",
            }],
            "allotments": {},
        })
    rows = _stamp_rows()
    with pytest.raises(assemble_mod.AssemblyError, match="does not exist"):
        _stamp(rows, {
            "inventory": [{
                "item_id": "LA-0001", "kind": "misconception", "text": "t",
            }],
            "allotments": {"LA-0001": "CONCEPT-9999"},
        })
    rows = _stamp_rows()
    with pytest.raises(assemble_mod.AssemblyError, match="culmination"):
        _stamp(rows, {
            "inventory": [{
                "item_id": "LA-0001", "kind": "misconception", "text": "t",
            }],
            "allotments": {"LA-0001": "CONCEPT-0003"},
        })
