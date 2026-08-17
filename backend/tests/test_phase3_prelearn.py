"""The Phase 03 running pre-requisite capture (phase3/prelearn.py).

Step-7 slice B regressions: a per-stage capture decision over THAT
stage's own evidence, one merge that consolidates them, no count quota
anywhere, mechanics-only checkers, R4 exact-once consolidation, advisory
critic (Q10), Fixer on exhaustion (Q13), decide-once replay, the
legality of an empty capture (a thin chapter is never padded), and the
widened exit seam that lets the capture leave the sealed boundary. The
golden replay drives the pass with the recorded rne_prelearn.json
fixture.
"""
from __future__ import annotations

import copy
import json
import re

import pytest

from app.services.phase3 import kernel, prelearn
from tests import test_phase3_settle_golden as settle_golden

GOLDEN = settle_golden.GOLDEN


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return settle_golden.envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def golden_prelearn() -> dict:
    return json.loads(
        (GOLDEN / "rne_prelearn.json").read_text(encoding="utf-8")
    )


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


def prelearn_replay_provider(golden_prelearn: dict):
    """Answer each stage capture with that stage's recorded elements and
    the merge with the recorded consolidation. The fixture cites stable
    source ids (BLK-/TOPIC-/QINV-/LA-/Type-Case unit ids), never the
    positional concept mint, so the replay needs no resolution step."""

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prelearn.merge":
            return {
                "prerequisites": [
                    {
                        key: row[key]
                        for key in (
                            "prerequisite_id", "text", "captures",
                            "rationale",
                        )
                    }
                    for row in golden_prelearn["merge"]
                ]
            }
        name = stage.split(":", 1)[-1]
        return {
            "prerequisites": [
                dict(item)
                for item in golden_prelearn["captures"][name]
            ]
        }

    return provider


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


def _settled(golden_envelope, golden_rows):
    mapping = settle_golden._replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(mapping)
    from app.services.phase3 import settle

    return settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )


def _capture_all(
    golden_envelope, golden_prelearn, *, settled, analysis,
    provider=None, critic=_verified_critic, store=None, fixer=None,
):
    provider = provider or prelearn_replay_provider(golden_prelearn)
    store = store if store is not None else kernel.DecisionStore()
    evidence = {"settle": {"settled": settled}, "analyse": {"analysis": analysis}}
    return [
        prelearn.capture_stage(
            golden_envelope, stage,
            provider=provider, critic=critic, store=store, fixer=fixer,
            **evidence.get(stage, {}),
        )
        for stage in prelearn.STAGES
    ], provider, store


# ---------------------------------------------------------------------------
# golden replay


def test_capture_and_merge_reproduce_the_recorded_fixture(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    captures, provider, store = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
    )
    assert [capture["stage"] for capture in captures] == list(
        prelearn.STAGES
    )
    for capture in captures:
        recorded = golden_prelearn["captures"][capture["stage"]]
        assert [item["prerequisite_id"] for item in capture["items"]] == (
            prelearn.mint_prerequisite_ids(len(recorded))
        )
        assert [item["text"] for item in capture["items"]] == [
            row["text"] for row in recorded
        ]
        # A verified critic leaves no flags.
        assert capture["review_flags"] == {}

    merged = prelearn.merge(
        golden_envelope, captures,
        provider=provider, critic=_verified_critic, store=store,
    )
    assert len(merged["prerequisites"]) == len(golden_prelearn["merge"])
    # R4: every stage capture consolidated exactly once.
    consolidated = [
        ref
        for row in merged["prerequisites"]
        for ref in row["captures"]
    ]
    assert sorted(consolidated) == sorted({
        f"{stage}:{item['prerequisite_id']}"
        for stage in prelearn.STAGES
        for item in merged["captures"][stage]
    })
    assert len(consolidated) == len(set(consolidated)) == 20
    # The merged element inherits its captures' stages and cited
    # evidence, so a prerequisite the run saw three times stays traceable
    # to all three stages' evidence.
    map_reading = next(
        row for row in merged["prerequisites"] if len(row["stages"]) == 3
    )
    assert map_reading["stages"] == ["settle", "host", "place"]
    assert "BLK-00050" in map_reading["evidence"]
    assert "QINV-0004" in map_reading["evidence"]
    assert merged["review_flags"] == {}


def test_decide_once_replays_capture_and_merge_for_free(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    calls = {"n": 0}
    base = prelearn_replay_provider(golden_prelearn)

    def counted(request: dict) -> dict:
        calls["n"] += 1
        return base(request)

    critic_calls = {"n": 0}

    def counted_critic(request: dict) -> dict:
        critic_calls["n"] += 1
        return _verified_critic(request)

    fixer_calls = {"n": 0}

    def counted_fixer(request: dict) -> dict:
        fixer_calls["n"] += 1
        raise AssertionError("the fixer must not be engaged")

    store = kernel.DecisionStore()
    first, _, _ = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        provider=counted, critic=counted_critic, store=store,
        fixer=counted_fixer,
    )
    merged_first = prelearn.merge(
        golden_envelope, first, provider=counted, critic=counted_critic,
        store=store, fixer=counted_fixer,
    )
    assert calls["n"] == 5  # four stage captures + one merge
    after = calls["n"], critic_calls["n"]

    second, _, _ = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        provider=counted, critic=counted_critic, store=store,
        fixer=counted_fixer,
    )
    merged_second = prelearn.merge(
        golden_envelope, second, provider=counted, critic=counted_critic,
        store=store, fixer=counted_fixer,
    )
    assert (calls["n"], critic_calls["n"]) == after
    assert fixer_calls["n"] == 0
    assert second == first
    assert merged_second == merged_first


# ---------------------------------------------------------------------------
# per-stage evidence — each stage sees its own and no other's


def test_each_stage_capture_receives_only_that_stages_evidence(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    """The whole point of the per-stage shape (spec T1): the runner hands
    each capture the evidence THAT stage had. Pin the payload keys."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    seen: dict[str, dict] = {}
    base = prelearn_replay_provider(golden_prelearn)

    def capturing(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage.startswith("prelearn.capture:"):
            seen[stage.split(":", 1)[1]] = copy.deepcopy(request)
        return base(request)

    _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        provider=capturing,
    )
    assert set(seen) == set(prelearn.STAGES)
    keys = {stage: set(seen[stage]["evidence"]) for stage in seen}
    assert keys == {
        "settle": {"source_blocks", "topics", "settled_concepts"},
        "host": {"type_case_units", "questions"},
        # Both halves of the pool Place placed from, not just the hubs.
        "place": {"pooled_hub_items", "pooled_figures"},
        "analyse": {"analysis_inventory"},
    }
    # No stage's payload carries any other stage's material.
    for stage, payload in seen.items():
        others = set().union(
            *(value for name, value in keys.items() if name != stage)
        )
        assert not (keys[stage] & others)
        assert set(payload) == {
            "stage", "rules", "evidence", "attempt", "max_attempts",
        }

    # And the citable ids are disjoint families, so a capture at one
    # stage cannot cite an id only another stage was shown.
    ids = {
        stage: prelearn.citable_ids(stage, seen[stage]["evidence"])
        for stage in seen
    }
    assert any(name.startswith("BLK-") for name in ids["settle"])
    assert "TOPIC-0001" in ids["settle"]
    assert not any(name.startswith("LA-") for name in ids["settle"])
    assert "TYPE-0001::CASE-0002::0002" in ids["host"]
    assert not any(name.startswith("BLK-") for name in ids["host"])
    # Place sees the pool it placed from: the hub items — a strict subset
    # of the QIDs Host saw — plus any unclaimed figure block. This
    # chapter has no unclaimed figure with an extractable image, so the
    # figure half is empty here; the pin below covers the other case.
    figure_refs = {
        str(row["item_ref"])
        for row in seen["place"]["evidence"]["pooled_figures"]
    }
    assert figure_refs == set()
    assert (ids["place"] - figure_refs) < ids["host"]
    assert ids["analyse"] and all(
        name.startswith("LA-") for name in ids["analyse"]
    )
    assert not (ids["analyse"] & (ids["settle"] | ids["host"]))


def test_analyse_stage_sees_the_inventory_and_settle_sees_the_source(
    golden_envelope, golden_analysis, golden_rows,
):
    """The union no single existing pass has (map §2.5): the Types/Cases
    live only in the host capture, the chapter-wide source only in the
    settle capture, the inventory only in the analyse capture."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    settle_ev = prelearn.stage_evidence(
        golden_envelope, "settle", settled=settled
    )
    host_ev = prelearn.stage_evidence(golden_envelope, "host")
    analyse_ev = prelearn.stage_evidence(
        golden_envelope, "analyse", analysis=analysis
    )
    assert len(settle_ev["source_blocks"]) == 313
    assert any(row["text"] for row in settle_ev["source_blocks"])
    assert len(host_ev["type_case_units"]) == 31
    assert len(analyse_ev["analysis_inventory"]) == 94
    assert "type_case_units" not in settle_ev
    assert "source_blocks" not in host_ev
    assert "analysis_inventory" not in host_ev


def test_the_place_capture_sees_the_unclaimed_figures_place_placed(
    golden_envelope, monkeypatch,
):
    """Place pools hub items AND unclaimed figures before it decides
    (place.py: ``pool = [*hub_pool(env), *figure_pool(env)]``). The
    capture at that boundary is shown the same pool, so a caption that
    assumes prior knowledge is readable — the settle capture sees figure
    blocks only as ids with empty display text, so no other stage covers
    it. The golden chapter has no unclaimed figure carrying an image, so
    the pooled figure is injected here rather than left untested."""
    from app.services.phase3 import place as place_mod

    assert place_mod.figure_pool(golden_envelope) == []
    monkeypatch.setattr(place_mod, "figure_pool", lambda env: [{
        "item_ref": "BLK-00042",
        "pool_kind": "figure",
        "caption": "Frédéric Sorrieu, 1848: a print of the utopian vision.",
        "images": [{
            "url": "https://example.invalid/sorrieu.png",
            "caption": "Frédéric Sorrieu, 1848: a print of the utopian "
                       "vision.",
        }],
    }])
    evidence = prelearn.stage_evidence(golden_envelope, "place")
    assert evidence["pooled_figures"] == [{
        "item_ref": "BLK-00042",
        "caption": "Frédéric Sorrieu, 1848: a print of the utopian vision.",
        "image_captions": [
            "Frédéric Sorrieu, 1848: a print of the utopian vision."
        ],
    }]
    # And the figure's own id is citable at this stage, so an element
    # read out of that caption can name where it came from.
    known = prelearn.citable_ids("place", evidence)
    assert "BLK-00042" in known
    assert prelearn._capture_checker(known)({"prerequisites": [
        {
            "prerequisite_id": "PR-0001",
            "text": "Reading an attributed print as a dated source.",
            "evidence": ["BLK-00042"],
        },
    ]}) == []


def test_an_unknown_stage_is_refused(golden_envelope):
    with pytest.raises(ValueError, match="unknown prelearn capture stage"):
        prelearn.stage_evidence(golden_envelope, "polish")
    with pytest.raises(ValueError, match="unknown prelearn capture stage"):
        prelearn.capture_stage(
            golden_envelope, "polish", provider=lambda request: {},
            store=kernel.DecisionStore(),
        )


# ---------------------------------------------------------------------------
# no count quota (Rule 1: no volume-derived structure)


def test_no_count_quota_rides_the_prompt_or_the_payload(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    """Neither the system prompts nor any payload may bound how many
    prerequisites the model returns — no minimum, maximum, target, or
    per-concept/per-topic arithmetic of any kind."""
    from app.services.phase3 import prompts

    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    seen: list[dict] = []
    base = prelearn_replay_provider(golden_prelearn)

    def capturing(request: dict) -> dict:
        seen.append(copy.deepcopy(request))
        return base(request)

    captures, _, store = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        provider=capturing,
    )
    prelearn.merge(
        golden_envelope, captures, provider=capturing,
        critic=_verified_critic, store=store,
    )
    assert len(seen) == 5
    quota_language = re.compile(
        r"at least \d|at most \d|minimum of|maximum of|exactly \d+ |"
        r"per concept|per topic|one per|quota|target count|"
        r"\d+\s*(?:-|to)\s*\d+ (?:items|elements|prerequisites)",
        re.IGNORECASE,
    )
    for request in seen:
        assert not quota_language.search(request["rules"]), request["stage"]
        # No numeric knob rides any payload either: the evidence carries
        # content, never a count for the model to satisfy. The kernel's
        # own attempt bookkeeping is the only numeric field.
        numeric = {
            key for key, value in request.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
        }
        assert numeric == {"attempt", "max_attempts"}
    for system in (
        prompts.PRELEARN_CAPTURE_SYSTEM,
        prompts.PRELEARN_MERGE_SYSTEM,
        prompts.PRELEARN_CRITIC_SYSTEM,
    ):
        assert not quota_language.search(system)
    # And no numeric literal decides anything in the module itself.
    from pathlib import Path

    source = Path(prelearn.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\blen\([^)]*\)\s*[<>]=?\s*\d", source)


def test_empty_capture_is_legal_and_never_padded(
    golden_envelope, golden_analysis, golden_rows,
):
    """A chapter whose evidence assumes no prerequisite yields an empty
    set: every stage captures nothing, the merge decision is not spent,
    and the run returns cleanly rather than raising."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    merge_calls = {"n": 0}

    def provider(request: dict) -> dict:
        if request.get("stage") == "prelearn.merge":
            merge_calls["n"] += 1
        return {"prerequisites": []}

    store = kernel.DecisionStore()
    captures = [
        prelearn.capture_stage(
            golden_envelope, stage, provider=provider,
            critic=_verified_critic, store=store,
            **({"settled": settled} if stage == "settle" else {}),
            **({"analysis": analysis} if stage == "analyse" else {}),
        )
        for stage in prelearn.STAGES
    ]
    assert all(capture["items"] == [] for capture in captures)
    merged = prelearn.merge(
        golden_envelope, captures, provider=provider,
        critic=_verified_critic, store=store,
    )
    assert merged == {
        "captures": {stage: [] for stage in prelearn.STAGES},
        "prerequisites": [],
        "review_flags": {},
        "stage_flags": {},
    }
    assert merge_calls["n"] == 0


def test_a_stage_with_no_citable_evidence_spends_no_decision(
    golden_envelope,
):
    """place.py's empty-pool precedent: a stage whose evidence carries
    nothing to cite skips its decision entirely."""
    calls = {"n": 0}

    def provider(request: dict) -> dict:
        calls["n"] += 1
        return {"prerequisites": []}

    # The analyse capture with an empty inventory has no citable id.
    capture = prelearn.capture_stage(
        golden_envelope, "analyse", analysis={"inventory": []},
        provider=provider, critic=_verified_critic,
        store=kernel.DecisionStore(),
    )
    assert capture == {
        "stage": "analyse",
        "items": [],
        "review_flags": {},
        "stage_flags": [],
    }
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# checker mechanics


def test_capture_checker_rejects_bad_ids_empty_text_and_foreign_evidence():
    check = prelearn._capture_checker({"BLK-00001", "TOPIC-0001"})
    defects = check({"prerequisites": [
        {
            "prerequisite_id": "PR-0001",
            "text": "Reading a map of Europe.",
            "evidence": ["BLK-00001"],
        },
        {
            "prerequisite_id": "PR-0001",
            "text": "",
            "evidence": ["LA-0004"],
        },
    ]})
    # duplicate / non-positional id
    assert any("PR-0002" in row and "positional mint" in row
               for row in defects)
    assert any("has empty text" in row for row in defects)
    # evidence citing an id this stage's payload does not contain
    assert any(
        "cites LA-0004" in row and "does not contain" in row
        for row in defects
    )
    # an element citing nothing at all is a defect too
    assert any(
        "cites no evidence" in row
        for row in check({"prerequisites": [
            {"prerequisite_id": "PR-0001", "text": "t", "evidence": []},
        ]})
    )
    # a well-formed capture passes, and an empty capture is legal
    assert check({"prerequisites": [
        {"prerequisite_id": "PR-0001", "text": "a", "evidence": ["BLK-00001"]},
        {"prerequisite_id": "PR-0002", "text": "b", "evidence": ["TOPIC-0001"]},
    ]}) == []
    assert check({"prerequisites": []}) == []
    assert check({}) == ["response has no prerequisites array"]


def test_merge_checker_demands_every_capture_exactly_once():
    check = prelearn._merge_checker(
        ["settle:PR-0001", "host:PR-0001", "analyse:PR-0001"]
    )
    defects = check({"prerequisites": [
        {
            "prerequisite_id": "PR-0001",
            "text": "Reading a map.",
            "captures": ["settle:PR-0001", "settle:PR-0001"],
        },
        {
            "prerequisite_id": "PR-0002",
            "text": "",
            "captures": ["place:PR-0009"],
        },
    ]})
    assert any("unknown or repeated capture_ref settle:PR-0001" in row
               for row in defects)
    assert any("unknown or repeated capture_ref place:PR-0009" in row
               for row in defects)
    assert any("has empty text" in row for row in defects)
    # R4: a capture named by nothing is a silent loss, so it is a defect.
    assert any(
        "unconsolidated stage capture(s): analyse:PR-0001, host:PR-0001"
        in row
        for row in defects
    )
    assert any(
        "names no capture_ref" in row
        for row in check({"prerequisites": [
            {"prerequisite_id": "PR-0001", "text": "t", "captures": []},
        ]})
    )
    assert check({"prerequisites": [
        {
            "prerequisite_id": "PR-0001",
            "text": "Reading a map.",
            "captures": ["settle:PR-0001", "host:PR-0001"],
        },
        {
            "prerequisite_id": "PR-0002",
            "text": "Cause and consequence.",
            "captures": ["analyse:PR-0001"],
        },
    ]}) == []


def test_the_merge_never_dedupes_by_text(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    """Two captures with IDENTICAL text stay two prerequisites when the
    model keeps them apart — grouping is the model's meaning judgment,
    never a normalized-text collapse."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}

    same = "Reading a map of Europe."

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prelearn.capture:settle":
            return {"prerequisites": [
                {
                    "prerequisite_id": "PR-0001",
                    "text": same,
                    "evidence": ["BLK-00050"],
                },
            ]}
        if stage == "prelearn.capture:host":
            return {"prerequisites": [
                {
                    "prerequisite_id": "PR-0001",
                    "text": same,
                    "evidence": ["QINV-0004"],
                },
            ]}
        if stage == "prelearn.merge":
            return {"prerequisites": [
                {
                    "prerequisite_id": "PR-0001",
                    "text": same,
                    "captures": ["settle:PR-0001"],
                    "rationale": "the prose assumes locating places",
                },
                {
                    "prerequisite_id": "PR-0002",
                    "text": same,
                    "captures": ["host:PR-0001"],
                    "rationale": "the task assumes plotting onto a map",
                },
            ]}
        return {"prerequisites": []}

    store = kernel.DecisionStore()
    captures = [
        prelearn.capture_stage(
            golden_envelope, stage, provider=provider,
            critic=_verified_critic, store=store,
            **({"settled": settled} if stage == "settle" else {}),
            **({"analysis": analysis} if stage == "analyse" else {}),
        )
        for stage in prelearn.STAGES
    ]
    merged = prelearn.merge(
        golden_envelope, captures, provider=provider,
        critic=_verified_critic, store=store,
    )
    assert [row["text"] for row in merged["prerequisites"]] == [same, same]
    assert [row["stages"] for row in merged["prerequisites"]] == [
        ["settle"], ["host"],
    ]


# ---------------------------------------------------------------------------
# Q10 critic advisory / Q13 fixer


def test_critic_dissent_flags_but_never_gates(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}

    def dissenting(request: dict) -> dict:
        if request.get("stage") == "prelearn.capture:settle":
            return {
                "verdict": "rejected",
                "confidence": 0.93,
                "issues": ["PR-0004 restates the chapter's own teaching"],
            }
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    captures, provider, store = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        critic=dissenting,
    )
    settle_capture = captures[0]
    # The decision stands: every element still ships.
    assert len(settle_capture["items"]) == 7
    assert any(
        "restates the chapter's own teaching" in flag
        for flags in settle_capture["review_flags"].values()
        for flag in flags
    )
    merged = prelearn.merge(
        golden_envelope, captures, provider=provider,
        critic=dissenting, store=store,
    )
    # Nothing was dropped, and the dissent rides onto the merged rows the
    # flagged captures landed in.
    assert len(merged["prerequisites"]) == len(golden_prelearn["merge"])
    assert any(
        "restates the chapter's own teaching" in flag
        for flags in merged["review_flags"].values()
        for flag in flags
    )


def test_dissent_against_an_empty_capture_still_ships(
    golden_envelope, golden_analysis, golden_rows,
):
    """The critic's most valuable verdict is 'the evidence plainly
    assumes a prerequisite you missed', and the capture that missed
    everything has NO row for that flag to land on. Broadcasting dissent
    over rows alone would report a rejected run as clean (Q10), so the
    decision-level record ships beside the row-addressable one."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}

    def empty(_request: dict) -> dict:
        return {"prerequisites": []}

    def dissenting(request: dict) -> dict:
        if request.get("stage") == "prelearn.capture:host":
            return {
                "verdict": "rejected",
                "confidence": 0.42,
                "issues": [
                    "the tasks plainly assume reading a map of Europe and "
                    "this capture returned nothing"
                ],
            }
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    store = kernel.DecisionStore()
    captures = [
        prelearn.capture_stage(
            golden_envelope, stage, provider=empty, critic=dissenting,
            store=store,
            **({"settled": settled} if stage == "settle" else {}),
            **({"analysis": analysis} if stage == "analyse" else {}),
        )
        for stage in prelearn.STAGES
    ]
    host_capture = captures[1]
    assert host_capture["items"] == []
    # The row channel has nothing to carry, which is exactly the hole.
    assert host_capture["review_flags"] == {}
    assert any(
        "assume reading a map of Europe" in flag
        for flag in host_capture["stage_flags"]
    )
    # And it survives the merge — including the merge that is skipped for
    # want of any capture at all.
    merged = prelearn.merge(
        golden_envelope, captures, provider=empty,
        critic=dissenting, store=store,
    )
    assert merged["prerequisites"] == []
    assert list(merged["stage_flags"]) == ["host"]
    assert any(
        "assume reading a map of Europe" in flag
        for flag in merged["stage_flags"]["host"]
    )


def test_merge_dissent_is_recorded_at_the_decision_too(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    """The merge's own dissent lands on every merged row AND whole under
    the ``merge`` key, so a reviewer reading the chapter-level record
    sees which decision was doubted rather than a flag on 16 rows."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}

    def dissenting(request: dict) -> dict:
        if request.get("stage") == "prelearn.merge":
            return {
                "verdict": "rejected",
                "confidence": 0.51,
                "issues": ["chronology and causal order were kept apart"],
            }
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    captures, provider, store = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
    )
    merged = prelearn.merge(
        golden_envelope, captures, provider=provider,
        critic=dissenting, store=store,
    )
    assert len(merged["prerequisites"]) == len(golden_prelearn["merge"])
    assert list(merged["stage_flags"]) == ["merge"]
    assert any(
        "chronology and causal order" in flag
        for flag in merged["stage_flags"]["merge"]
    )


def test_fixer_engages_on_merge_exhaustion(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    """A merge provider that drops a capture exhausts the bounded
    corrections; The Fixer's contract-satisfying decision ships flagged
    and the run completes (Q13)."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    base = prelearn_replay_provider(golden_prelearn)

    def broken(request: dict) -> dict:
        response = base(request)
        if request.get("stage") == "prelearn.merge":
            return {"prerequisites": response["prerequisites"][:-1]}
        return response

    def fixer(request: dict) -> dict:
        response = base(request["original_payload"])
        response["rationale"] = (
            "restored the dropped capture to its own prerequisite"
        )
        return response

    store = kernel.DecisionStore()
    captures, _, _ = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        provider=broken, store=store, fixer=fixer,
    )
    merged = prelearn.merge(
        golden_envelope, captures, provider=broken,
        critic=_verified_critic, store=store, fixer=fixer,
    )
    consolidated = [
        ref for row in merged["prerequisites"] for ref in row["captures"]
    ]
    assert len(consolidated) == 20
    assert any(
        flag.startswith("fixer: blocked=")
        for flags in merged["review_flags"].values()
        for flag in flags
    )


def test_merge_exhaustion_without_a_fixer_fails_closed(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    base = prelearn_replay_provider(golden_prelearn)

    def broken(request: dict) -> dict:
        response = base(request)
        if request.get("stage") == "prelearn.merge":
            return {"prerequisites": response["prerequisites"][:-1]}
        return response

    store = kernel.DecisionStore()
    captures, _, _ = _capture_all(
        golden_envelope, golden_prelearn, settled=settled, analysis=analysis,
        provider=broken, store=store,
    )
    with pytest.raises(
        kernel.ContractError, match="unconsolidated stage capture"
    ):
        prelearn.merge(
            golden_envelope, captures, provider=broken,
            critic=_verified_critic, store=store,
        )


# ---------------------------------------------------------------------------
# the existing passes are untouched (spec T1)


def test_no_existing_pass_was_re_keyed_by_this_slice():
    """Adding a ``prerequisites`` field to Settle/Host/Place/Analyse would
    re-key every stored decision for those passes (kernel.decision_key
    hashes the whole payload and store.put never overwrites). The capture
    is a pass of its own precisely so that cannot happen — pin it."""
    from app.services import semantic_confidence_policy as confidence_policy
    from app.services.phase3 import analyse, place, polish

    assert confidence_policy.POLICY_VERSION == (
        "semantic-confidence-policy-3"
    )  # settle + host
    assert place.POLICY_VERSION == "place-1"
    assert analyse.POLICY_VERSION == "analysis-1"
    # Polish keys off its content-code list; the constant is pinned so a
    # change there is deliberate rather than a side effect of this slice.
    assert sorted(polish.CONTENT_CODES) == [
        "analysis_section_format",
        "description_truncated_clause",
        "error_analysis_framing",
        "generic_error_analysis",
        "generic_misconception",
        "mastery_statement_not_substantive",
        "misconception_framing",
        "missing_learner_analysis",
        "missing_mastery_statement",
        "verbatim_source_description",
    ]
    assert prelearn.POLICY_VERSION == "prelearn-1"

    # And no existing pass mentions the capture at all.
    from pathlib import Path

    root = Path(place.__file__).parent
    for name in ("settle.py", "host.py", "place.py", "analyse.py",
                 "polish.py", "assemble.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "prerequisite" not in text.lower(), name
        assert "prelearn" not in text.lower(), name


# ---------------------------------------------------------------------------
# the widened exit seam


def test_the_run_result_carries_the_capture_out_of_the_sealed_boundary(
    monkeypatch, tmp_path,
):
    """``_run_rewritten_phase3`` used to end at ``return
    result["records"]`` and discard every other key, so material built
    inside the run had no exit. The carry dict is that exit — and the
    records it returns are byte-identical with and without it."""
    from app.services import canonical_source_phase3 as phase3_core
    from app.services import concept_topology_contract as topology_contract
    from app.services import generation
    from app.services.phase3 import runner as p3_runner
    from tests import test_instruction_architect as ia_tests

    stored = ia_tests._sample_envelope("a" * 64)
    session = {
        "artifact_dir": str(tmp_path),
        "canonical": {"blocks": [{"block_id": "BLK-1", "display_text": "t"}]},
    }
    graph = copy.deepcopy(stored["graph"])
    monkeypatch.setattr(phase3_core, "active_session", lambda: session)
    monkeypatch.setattr(phase3_core, "active_graph", lambda: graph)
    out = [{"concept_title": "Place Value", "topic": "Numbers"}]
    (tmp_path / "source.phase3-envelope.json").write_text(
        json.dumps(
            {
                "boundary_skeleton_sha256": phase3_core._sha256_json(
                    list(out)
                ),
                "envelope": stored,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records = [{"concept_title": "Place Value", "topic": "Numbers"}]
    prerequisites = {
        "captures": {"settle": [{"prerequisite_id": "PR-0001"}]},
        "prerequisites": [{"prerequisite_id": "PR-0001", "text": "counting"}],
        "review_flags": {},
    }

    def fake_run(env, store_dir=None):
        return {
            "records": copy.deepcopy(records),
            "prerequisites": copy.deepcopy(prerequisites),
            "analysis": {"inventory": []},
            "summary": {
                "row_count": 1, "routed_qids": 0,
                "unrouted_items": 0, "flagged_row_count": 0,
            },
        }

    monkeypatch.setattr(p3_runner, "run", fake_run)
    kwargs = {
        "meta": {"board": "CBSE", "instruction_set_sha256": "a" * 64},
        "question_task_inventory": {},
        "mined_types": {},
    }
    without = topology_contract._run_rewritten_phase3(
        generation, out, dict(kwargs)
    )
    carry: dict = {}
    with_carry = topology_contract._run_rewritten_phase3(
        generation, out, dict(kwargs), carry=carry
    )
    # The Post lane's rows are untouched by the widening.
    assert with_carry == without == records
    # Every non-record key now has an exit; the capture among them.
    assert carry["prerequisites"] == prerequisites
    assert set(carry) == {"prerequisites", "analysis", "summary"}
    assert "records" not in carry
    # The carry is a copy: mutating it can never reach back into the run.
    carry["prerequisites"]["prerequisites"].clear()
    assert prerequisites["prerequisites"]


def test_the_generation_seam_forwards_a_carry_dict(monkeypatch):
    """The production entry point threads the dict through; a caller that
    passes none behaves exactly as before."""
    from app.services import concept_topology_contract as topology_contract
    from app.services import generation

    seen: list = []

    def fake(generation_module, out, kwargs, *, carry=None):
        seen.append(carry)
        if carry is not None:
            carry["prerequisites"] = {"prerequisites": []}
        return list(out)

    monkeypatch.setattr(
        topology_contract, "_run_rewritten_phase3", fake
    )
    rows = [{"concept_title": "A"}]
    assert generation._prepare_final_concept_content(rows, meta={}) == rows
    carry: dict = {}
    assert generation._prepare_final_concept_content(
        rows, meta={}, phase3_carry=carry
    ) == rows
    assert seen == [None, carry]
    assert carry == {"prerequisites": {"prerequisites": []}}
    # A non-dict phase3_carry is ignored rather than crashing the run.
    generation._prepare_final_concept_content(
        rows, meta={}, phase3_carry="nope"
    )
    assert seen[-1] is None


def _final_content_checkpoint(g):
    """The smallest ``final_content_ready`` checkpoint that resumes
    API-free, so the two tests below drive the REAL production entry
    point rather than a stand-in for it."""
    from tests import test_generation_validation_diagnostics as vd

    analysis = (
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "unlike denominators can be added directly.; Error Analysis: "
        "Students may add denominators without first finding a common "
        "denominator."
    )
    task = "Calculate one half plus one third and explain each step."
    rows = [
        vd._row(
            "Add unlike fractions",
            "Description: Compare two fractional quantities before "
            "combining them.\nAchieving Mastery: Explaining why a common "
            "denominator is needed before adding fractions. // Types: "
            "Type 01: Add unlike fractions Case 01: Build equivalent "
            f"fractional quantities Example 01: {task} // " + analysis,
            topic="T",
            parent="P",
        ),
        vd._row(
            "Culmination - Add unlike fractions",
            "Description: Recap of adding unlike fractions.",
            topic="T",
            parent="Culmination",
        ),
    ]
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "T",
        "raw_task": task,
    }], "stats": {}}
    mined_types = {"types": []}
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types, "QINV-0001", rows[0], basis="type_host_review",
    )
    return g._make_concept_checkpoint(
        "final_content_ready",
        records=rows,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )


CAPTURE = {
    "captures": {"settle": [{"prerequisite_id": "PR-0001"}]},
    "prerequisites": [
        {"prerequisite_id": "PR-0001", "text": "Adding like fractions."},
    ],
    "review_flags": {},
    "stage_flags": {},
}


def test_the_production_entry_point_stores_the_capture(monkeypatch, tmp_path):
    """The hop nothing else covers: ``concepts_from_mmd`` is what puts the
    capture somewhere durable. With the seam faked below it, this pins the
    write itself — the guard, the key name, and the fact that an empty
    capture is stored AS an empty capture rather than skipped."""
    from app.services import canonical_source_phase3 as phase3_core
    from app.services import generation as g

    checkpoint = _final_content_checkpoint(g)
    source = "# T\nA short source-grounded discussion of fractions."
    monkeypatch.setattr(
        phase3_core, "active_session", lambda: {"artifact_dir": str(tmp_path)}
    )
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("this drive must not call the API")
        ),
    )

    def fake_pre_final(_mmd, **_kwargs):
        return (
            copy.deepcopy(checkpoint["records"]),
            copy.deepcopy(checkpoint["question_task_inventory"]),
            copy.deepcopy(checkpoint["mined_types"]),
            {},
        )

    monkeypatch.setattr(
        g, "_run_live_concept_pre_final_stages", fake_pre_final
    )

    carried: dict = {}

    def fake_prepare(out, **kwargs):
        carry = kwargs.get("phase3_carry")
        assert isinstance(carry, dict), "the carry must reach the seam"
        carry["prerequisites"] = copy.deepcopy(carried)
        return out

    monkeypatch.setattr(g, "_prepare_final_concept_content", fake_prepare)

    # A run that captured prerequisites stores them.
    carried = CAPTURE
    artifacts: dict = {}
    g.concepts_from_mmd(
        source, subject="Mathematics", live=True,
        resume_checkpoint={"stages": []}, artifacts=artifacts,
    )
    assert artifacts["phase3_prerequisites"] == CAPTURE
    assert "phase3_prerequisites_absent" not in artifacts

    # A run whose chapter genuinely assumes nothing stores the EMPTY
    # capture — an empty set is an answer, not a missing key.
    carried = {"captures": {}, "prerequisites": [], "review_flags": {},
               "stage_flags": {}}
    empty_artifacts: dict = {}
    g.concepts_from_mmd(
        source, subject="Mathematics", live=True,
        resume_checkpoint={"stages": []}, artifacts=empty_artifacts,
    )
    assert empty_artifacts["phase3_prerequisites"] == carried
    assert checkpoint["stage"] == "final_content_ready"


def test_a_restored_checkpoint_reads_the_capture_back_or_records_its_absence(
    monkeypatch, tmp_path,
):
    """A restored final-content checkpoint skips phase 3 entirely, so the
    capture is never made in that process. It is read back from the
    snapshot the original run wrote; with no snapshot the ABSENCE is
    recorded, because a missing key would be indistinguishable from a
    chapter that assumes no prerequisite (R4)."""
    from app.services import canonical_source_phase3 as phase3_core
    from app.services import generation as g

    checkpoint = _final_content_checkpoint(g)
    source = "# T\nA short source-grounded discussion of fractions."
    monkeypatch.setattr(
        phase3_core, "active_session", lambda: {"artifact_dir": str(tmp_path)}
    )
    monkeypatch.setattr(
        g,
        "_prepare_final_concept_content",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("a restored checkpoint must not re-run phase 3")
        ),
    )
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("a restored checkpoint must not call the API")
        ),
    )

    # No snapshot: the absence is recorded and flagged, never silent.
    artifacts: dict = {}
    g.concepts_from_mmd(
        source, subject="Mathematics", live=True,
        resume_checkpoint=checkpoint, artifacts=artifacts,
    )
    assert "phase3_prerequisites" not in artifacts
    assert "source.phase3-prelearn-capture.json" in artifacts[
        "phase3_prerequisites_absent"
    ]

    # The snapshot the first run left beside its decision store.
    (tmp_path / "source.phase3-prelearn-capture.json").write_text(
        json.dumps(CAPTURE), encoding="utf-8",
    )
    restored: dict = {}
    g.concepts_from_mmd(
        source, subject="Mathematics", live=True,
        resume_checkpoint=checkpoint, artifacts=restored,
    )
    assert restored["phase3_prerequisites"] == CAPTURE
    assert "phase3_prerequisites_absent" not in restored


# ---------------------------------------------------------------------------
# the fixture is authored, not invented


def test_the_golden_fixture_cites_only_real_stage_evidence(
    golden_envelope, golden_prelearn, golden_analysis, golden_rows,
):
    """Every recorded citation resolves against the citable-id set of the
    stage that made it — the fixture was recorded from the real payloads,
    not hand-authored around plausible-looking ids."""
    settled = _settled(golden_envelope, golden_rows)
    analysis = {"inventory": golden_analysis["items"]}
    for stage in prelearn.STAGES:
        evidence = prelearn.stage_evidence(
            golden_envelope, stage,
            settled=settled if stage == "settle" else None,
            analysis=analysis if stage == "analyse" else None,
        )
        known = prelearn.citable_ids(stage, evidence)
        recorded = golden_prelearn["captures"][stage]
        assert recorded
        assert prelearn._capture_checker(known)(
            {"prerequisites": recorded}
        ) == []
    refs = [
        f"{stage}:{item['prerequisite_id']}"
        for stage in prelearn.STAGES
        for item in golden_prelearn["captures"][stage]
    ]
    assert prelearn._merge_checker(refs)(
        {"prerequisites": golden_prelearn["merge"]}
    ) == []
    assert golden_prelearn["_comment"]
