"""Phase 2.2 placement pass (phase3/place.py) and its Assemble consumption.

Doctrine under test (docs/aegis-restructure.md §4 Phase 2.2, CLAUDE.md
Rule 1): the pool is chapter-wide; the placement verdict is the MODEL's,
from content evidence only — printer position is deliberately absent from
the payload; every pooled item ends placed or carries a recorded
disposition (R4), hub items having no disposition escape; the critic is
advisory (Q10); exhaustion goes to the Fixer (Q13); an unbound hub item
fails closed at the deposit boundary instead of vanishing.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services import containers, generation
from app.services.phase3 import assemble as assemble_mod
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel
from app.services.phase3 import place as place_mod
from app.services.phase3 import prompts as phase3_prompts

PINHOLE_URL = "https://cdn.example.com/figures/pinhole.jpg"
BANNER_URL = "https://cdn.example.com/figures/banner.jpg"
CLAIMED_URL = "https://cdn.example.com/figures/mirror.jpg"


def _mini_envelope() -> dict:
    graph = {
        "source_contract_hash": "contract-hash-step4",
        "metadata": {"subject": "Science", "board": "CBSE", "grade": "6"},
        "topics": [{"topic_id": "TOPIC-0001", "title": "Light"}],
        "subtopics": [],
        "blocks": [
            {
                "block_id": "BLK-0001",
                "topic_id": "TOPIC-0001",
                "kind": "paragraph",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-0001",
                "kind": "paragraph",
                "display_text": "Light travels in straight lines.",
            },
            {
                # Unclaimed figure: no inventory item carries its URL, so
                # the placement pass pools it.
                "block_id": "BLK-0002",
                "kind": "figure",
                "raw_text": (
                    "![Fig. 2 - A pinhole camera forming an inverted "
                    f"image.]({PINHOLE_URL})\n"
                ),
                "display_text": None,
                # Printer position rides the canonical but must never
                # reach the placement payload.
                "source_start": 4321,
                "source_end": 4400,
            },
            {
                # Unclaimed decorative figure: the model records the one
                # allowed disposition for it.
                "block_id": "BLK-0003",
                "kind": "figure",
                "raw_text": f"![Decorative chapter banner.]({BANNER_URL})\n",
                "source_start": 10,
                "source_end": 90,
            },
            {
                # Claimed figure: QINV-0002 already carries its URL, so it
                # is attached-to-item, never pooled.
                "block_id": "BLK-0004",
                "kind": "figure",
                "raw_text": (
                    f"![Fig. 3 - Reflection in a plane mirror.]({CLAIMED_URL})\n"
                ),
                "source_start": 6000,
                "source_end": 6100,
            },
        ],
    }
    skeleton_rows = [
        {"concept_title": "Rectilinear Propagation Of Light", "topic": "Light"},
        {"concept_title": "Reflection From Plane Mirrors", "topic": "Light"},
    ]
    inventory = {
        "items": [
            {
                "qid": "QINV-0001",
                "source_kind": "info_hub",
                "raw_task": (
                    "Do you know? The metre was once defined by the path "
                    "light travels in a fraction of a second."
                ),
                "source_label": "Do you know?",
                "image_urls": [],
                # Printer-position provenance — must never reach the
                # placement payload.
                "source_start": 812,
                "page_hint": 14,
                "topic_hint": "Reflection",
                "source_location_topic_title": "Reflection",
            },
            {
                "qid": "QINV-0002",
                "source_kind": "activity",
                "_activity_origin": True,
                "raw_task": (
                    "Hold a plane mirror upright and observe the image of "
                    "a lit candle. Note whether the image is erect."
                ),
                "source_label": "Activity 11.1",
                "image_urls": [CLAIMED_URL],
                "_image_captions": {
                    CLAIMED_URL: "Fig. 3 - Reflection in a plane mirror.",
                },
                "source_start": 5900,
                "page_hint": 15,
            },
        ],
    }
    mined_types = {"types": []}
    return envelope_mod.build(
        graph=graph,
        canonical=canonical,
        skeleton_rows=skeleton_rows,
        inventory=inventory,
        mined_types=mined_types,
        metadata={"subject": "Science", "board": "CBSE", "grade": "6"},
    )


def _settled_rows() -> list[dict]:
    return [
        {
            "topic": "Light",
            "parent_concept": "Light",
            "concept_title": "Rectilinear Propagation Of Light",
            "_semantic_topic_id": "TOPIC-0001",
            "_source_block_ids": ["BLK-0001"],
            "_source_grounding_contract": "api-verified-source-block-ids",
            "keywords": "light, rectilinear, shadow",
            "concept_details": (
                "Description: Light travels in straight lines through a "
                "uniform medium, which is why opaque objects cast sharp "
                "shadows and a pinhole camera forms a small inverted image "
                "on its screen. Because the path is straight, predicting "
                "where light reaches only requires drawing straight rays "
                "from the source past the edges of the object.\n"
                "Achieving Mastery: The learner can draw straight-line ray "
                "diagrams to predict shadows and pinhole images. // "
                "Misconception/ Error Analysis: Misconceptions: Learners "
                "believe light bends around corners on its own in air."
            ),
        },
        {
            "topic": "Light",
            "parent_concept": "Light",
            "concept_title": "Reflection From Plane Mirrors",
            "_semantic_topic_id": "TOPIC-0001",
            "_source_block_ids": ["BLK-0001"],
            "_source_grounding_contract": "api-verified-source-block-ids",
            "keywords": "reflection, mirror, image",
            "concept_details": (
                "Description: A plane mirror reflects light so that the "
                "image appears erect, virtual, and as far behind the "
                "mirror as the object stands in front of it. The mirror "
                "reverses left and right, which is why printed text seen "
                "in a mirror reads backwards while its size is unchanged.\n"
                "Achieving Mastery: The learner can state the properties "
                "of a plane-mirror image and locate it by distance. // "
                "Misconception/ Error Analysis: Error Analysis: The "
                "learner measures the image distance from the object "
                "instead of from the mirror surface."
            ),
        },
    ]


def _good_provider(request: dict) -> dict:
    placements = []
    for entry in request["pool"]:
        ref = entry["item_ref"]
        if ref == "QINV-0001":
            concept, why = "CONCEPT-0001", (
                "The light-path definition of the metre enriches "
                "rectilinear propagation."
            )
        elif ref == "QINV-0002":
            concept, why = "CONCEPT-0002", (
                "The candle-and-mirror activity exercises plane-mirror "
                "reflection."
            )
        elif ref == "BLK-0002":
            concept, why = "CONCEPT-0001", (
                "The pinhole-camera figure depicts straight-line light "
                "paths."
            )
        else:
            placements.append({
                "item_ref": ref,
                "disposition": place_mod.FIGURE_DISPOSITION,
                "rationale": (
                    "A decorative banner illustrating no concept's "
                    "teaching."
                ),
            })
            continue
        placements.append({
            "item_ref": ref, "concept_id": concept, "rationale": why,
        })
    return {"placements": placements}


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


# ---------------------------------------------------------------------------
# the pool and the payload


def test_pool_holds_hub_items_and_unclaimed_figures_only():
    env = envelope_mod.validate(_mini_envelope())
    hub = place_mod.hub_pool(env)
    figures = place_mod.figure_pool(env)
    assert [row["item_ref"] for row in hub] == ["QINV-0001", "QINV-0002"]
    # The claimed figure (BLK-0004) is attached to its item, never pooled.
    assert [row["item_ref"] for row in figures] == ["BLK-0002", "BLK-0003"]
    assert figures[0]["images"] == [{
        "url": PINHOLE_URL,
        "caption": "Fig. 2 - A pinhole camera forming an inverted image",
    }]


def test_payload_deliberately_excludes_printer_position():
    """'Never where the printer put it' is enforced structurally: position
    is not offered as evidence at all."""
    env = _mini_envelope()
    requests: list[dict] = []

    def capturing(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        return _good_provider(request)

    place_mod.place(
        env, _settled_rows(), provider=capturing,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    assert requests
    for request in requests:
        raw = json.dumps(request, ensure_ascii=False)
        for banned in (
            "source_start", "source_end", "page_hint", "page",
            "topic_hint", "source_location", "printed_under",
            "order_index", "source_position",
        ):
            assert f'"{banned}"' not in raw, banned


def test_every_pooled_item_is_decided_and_dispositions_are_recorded():
    env = _mini_envelope()
    result = place_mod.place(
        env, _settled_rows(), provider=_good_provider,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    assert result["hub_placements"] == {
        "QINV-0001": "CONCEPT-0001",
        "QINV-0002": "CONCEPT-0002",
    }
    assert result["figure_placements"] == {
        "BLK-0002": "CONCEPT-0001",
        "BLK-0003": place_mod.FIGURE_DISPOSITION,
    }
    # The disposition carries its recorded rationale — never a silent drop.
    assert "decorative banner" in result["rationales"]["BLK-0003"]


def test_isolated_operator_guidance_does_not_override_meaningful_vision():
    seen: dict[str, str] = {}

    def capturing(request: dict) -> dict:
        seen["rules"] = request["rules"]
        return _good_provider(request)

    place_mod.place(
        _mini_envelope(), _settled_rows(), provider=capturing,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )

    assert "lone + sign" in phase3_prompts.PLACE_SYSTEM
    assert "isolated typographic operator/glyph" in (
        phase3_prompts.PLACE_CRITIC_SYSTEM
    )
    assert "keep the operator as text" in seen["rules"]

    checker = place_mod._place_checker(
        [{
            "item_ref": "BLK-PLUS",
            "pool_kind": "figure",
            "caption": "+",
            "images": [{"url": "https://cdn.example.com/plus.png"}],
        }],
        {"CONCEPT-0001"},
    )
    assert checker({
        "placements": [{
            "item_ref": "BLK-PLUS",
            "concept_id": "CONCEPT-0001",
            "disposition": "",
            "rationale": (
                "Vision shows an addition diagram beyond the caption glyph."
            ),
        }]
    }) == []
    assert checker({
        "placements": [{
            "item_ref": "BLK-PLUS",
            "concept_id": "",
            "disposition": place_mod.FIGURE_DISPOSITION,
            "rationale": "The operator remains text.",
        }]
    }) == []


def test_live_place_attaches_pooled_images_as_vision_inputs(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kwargs):
        captured.update(kwargs)
        return {"placements": []}

    from app.services import generation
    monkeypatch.setattr(generation, "_openai_json", fake_openai)

    place_mod._live_place({
        "pool": [{
            "item_ref": "FIG-1",
            "images": [
                {"url": "https://cdn.example.com/diagram.png"},
                {"url": "https://cdn.example.com/diagram.png"},
            ],
        }],
    })

    assert captured["image_urls"] == [
        "https://cdn.example.com/diagram.png",
    ]


def test_live_place_fixer_keeps_original_pool_images_as_vision(monkeypatch):
    captured = {}

    def fake_openai(system, user, **kwargs):
        captured.update(kwargs)
        return {"placements": []}

    from app.services import generation
    monkeypatch.setattr(generation, "_openai_json", fake_openai)

    place_mod._live_fixer({
        "fixer": True,
        "original_payload": {
            "pool": [{
                "item_ref": "FIG-1",
                "images": [{"url": "https://cdn.example.com/diagram.png"}],
            }],
        },
    })

    assert place_mod.POLICY_VERSION == "place-2"
    assert captured["image_urls"] == [
        "https://cdn.example.com/diagram.png",
    ]


def test_checker_rejects_a_figure_with_both_placement_outcomes():
    checker = place_mod._place_checker(
        [{"item_ref": "FIG-1", "pool_kind": "figure"}],
        {"CONCEPT-0001"},
    )

    defects = checker({
        "placements": [{
            "item_ref": "FIG-1",
            "concept_id": "CONCEPT-0001",
            "disposition": place_mod.FIGURE_DISPOSITION,
            "rationale": "Contradictory by construction.",
        }],
    })

    assert any("both concept_id and disposition" in defect for defect in defects)


def test_empty_pool_skips_the_pass_entirely():
    env = _mini_envelope()
    stripped = json.loads(json.dumps(env))
    stripped["inventory"]["items"] = []
    stripped["canonical"]["blocks"] = [
        row for row in stripped["canonical"]["blocks"]
        if row.get("kind") != "figure"
    ]
    stripped["envelope_sha256"] = envelope_mod.seal_sha256(stripped)
    calls = {"n": 0}

    def provider(request: dict) -> dict:
        calls["n"] += 1
        return {"placements": []}

    result = place_mod.place(
        stripped, _settled_rows(), provider=provider,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    assert calls["n"] == 0
    assert result == {
        "hub_placements": {}, "figure_placements": {},
        "figure_pool": {}, "rationales": {}, "review_flags": {},
    }


# ---------------------------------------------------------------------------
# checker mechanics


def _batch() -> list[dict]:
    return [
        {"item_ref": "QINV-0001", "pool_kind": "hub_item"},
        {"item_ref": "BLK-0002", "pool_kind": "figure"},
    ]


def test_checker_requires_every_item_exactly_once():
    check = place_mod._place_checker(_batch(), {"CONCEPT-0001"})
    defects = check({"placements": [
        {"item_ref": "QINV-0001", "concept_id": "CONCEPT-0001",
         "rationale": "fits"},
        {"item_ref": "QINV-0001", "concept_id": "CONCEPT-0001",
         "rationale": "fits"},
    ]})
    assert any("repeated" in d for d in defects)
    assert any("undecided" in d and "BLK-0002" in d for d in defects)


def test_checker_denies_hub_items_a_disposition_escape():
    check = place_mod._place_checker(_batch(), {"CONCEPT-0001"})
    defects = check({"placements": [
        {"item_ref": "QINV-0001",
         "disposition": place_mod.FIGURE_DISPOSITION,
         "rationale": "it is a box"},
        {"item_ref": "BLK-0002", "concept_id": "CONCEPT-0001",
         "rationale": "fits"},
    ]})
    assert any("no disposition" in d for d in defects)


def test_checker_rejects_unknown_concepts_and_missing_rationale():
    check = place_mod._place_checker(_batch(), {"CONCEPT-0001"})
    defects = check({"placements": [
        {"item_ref": "QINV-0001", "concept_id": "CONCEPT-9999",
         "rationale": "fits"},
        {"item_ref": "BLK-0002", "concept_id": "CONCEPT-0001",
         "rationale": ""},
    ]})
    assert any("unknown concept_id" in d for d in defects)
    assert any("no rationale" in d for d in defects)


# ---------------------------------------------------------------------------
# decide-once, advisory critic, the Fixer


def test_decide_once_replays_from_the_store_for_free():
    env = _mini_envelope()
    store = kernel.DecisionStore()
    calls = {"n": 0}

    def counting(request: dict) -> dict:
        calls["n"] += 1
        return _good_provider(request)

    first = place_mod.place(
        env, _settled_rows(), provider=counting,
        critic=_verified_critic, store=store,
    )
    after_first = calls["n"]
    second = place_mod.place(
        env, _settled_rows(), provider=counting,
        critic=_verified_critic, store=store,
    )
    assert calls["n"] == after_first
    assert second == first


def test_critic_dissent_flags_and_never_gates():
    env = _mini_envelope()

    def dissenting(_request: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.95,
            "issues": ["the banner figure genuinely illustrates shadows"],
        }

    result = place_mod.place(
        env, _settled_rows(), provider=_good_provider,
        critic=dissenting, store=kernel.DecisionStore(),
    )
    # The decision stands; the dissent rides every batch item as flags.
    assert result["hub_placements"]["QINV-0001"] == "CONCEPT-0001"
    assert any(
        "banner figure" in flag
        for flags in result["review_flags"].values()
        for flag in flags
    )


def test_fixer_engages_on_exhaustion_and_its_decision_is_flagged():
    env = _mini_envelope()

    def omits_one(request: dict) -> dict:
        response = _good_provider(request)
        response["placements"] = [
            row for row in response["placements"]
            if row["item_ref"] != "QINV-0001"
        ]
        return response

    def fixer(request: dict) -> dict:
        assert request["fixer"] is True
        assert any(
            "QINV-0001" in defect for defect in request["blocked_check"]
        )
        response = _good_provider(request["original_payload"])
        response["rationale"] = (
            "placed the omitted info hub with the concept its text enriches"
        )
        return response

    result = place_mod.place(
        env, _settled_rows(), provider=omits_one,
        critic=_verified_critic, store=kernel.DecisionStore(), fixer=fixer,
    )
    assert result["hub_placements"]["QINV-0001"] == "CONCEPT-0001"
    assert any(
        flag.startswith("fixer:")
        for flags in result["review_flags"].values()
        for flag in flags
    )


def test_exhaustion_without_a_fixer_fails_closed():
    env = _mini_envelope()

    def never_decides(request: dict) -> dict:
        response = _good_provider(request)
        response["placements"] = [
            row for row in response["placements"]
            if row["item_ref"] != "QINV-0001"
        ]
        return response

    with pytest.raises(kernel.ContractError, match="QINV-0001"):
        place_mod.place(
            env, _settled_rows(), provider=never_decides,
            critic=_verified_critic, store=kernel.DecisionStore(),
        )


# ---------------------------------------------------------------------------
# end-to-end: info + [img] land in the placed concepts' hub sections


def _assembled(env: dict | None = None, placements: dict | None = None):
    env = env or _mini_envelope()
    if placements is None:
        placements = place_mod.place(
            env, _settled_rows(), provider=_good_provider,
            critic=_verified_critic, store=kernel.DecisionStore(),
        )
    hosts = {"host_map": {}, "qid_map": {}, "new_concepts": []}
    return assemble_mod.assemble(env, _settled_rows(), hosts, placements)


def test_info_hub_and_unclaimed_figure_render_as_info_plus_img():
    result = _assembled()
    rows = result["rows"]
    first = next(
        row for row in rows
        if row["concept_title"].startswith("Rectilinear")
    )
    second = next(
        row for row in rows
        if row["concept_title"].startswith("Reflection")
    )
    # The info hub's full info text renders in its placed concept's hub.
    assert "Activity/Info Hub:" in first["concept_details"]
    assert "metre" in first["concept_details"]
    # The placed unclaimed figure renders as a Figure note + [img] embed.
    assert f'[img src="{PINHOLE_URL}"' in first["concept_details"]
    assert "Figure — " in first["concept_details"]
    # The activity renders on ITS placed concept with the image its item
    # already carries.
    assert "Activity/Info Hub:" in second["concept_details"]
    assert "plane mirror" in second["concept_details"]
    assert f'[img src="{CLAIMED_URL}"' in second["concept_details"]
    # The banner's disposition is recorded in coverage, never silent.
    assert result["coverage"]["figure_dispositions"] == {
        "BLK-0003": {
            "disposition": place_mod.FIGURE_DISPOSITION,
            "rationale": (
                "A decorative banner illustrating no concept's teaching."
            ),
        }
    }
    # The stamped markers are the release-audit trail.
    assert first["_aegis_hub_placements"] == ["QINV-0001"]
    assert second["_aegis_hub_placements"] == ["QINV-0002"]
    assert first["_aegis_figure_placements"] == [{
        "block_id": "BLK-0002",
        "url": PINHOLE_URL,
        "caption": "Fig. 2 - A pinhole camera forming an inverted image",
    }]


def test_assemble_with_placements_is_deterministic():
    env = _mini_envelope()
    placements = place_mod.place(
        env, _settled_rows(), provider=_good_provider,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    assert _assembled(env, placements) == _assembled(env, placements)


def test_placement_naming_a_missing_concept_is_a_hard_error():
    env = _mini_envelope()
    placements = place_mod.place(
        env, _settled_rows(), provider=_good_provider,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    broken = copy.deepcopy(placements)
    broken["hub_placements"]["QINV-0001"] = "CONCEPT-9999"
    with pytest.raises(assemble_mod.AssemblyError, match="does not exist"):
        _assembled(env, broken)


def test_unbound_hub_item_fails_closed_at_the_deposit_boundary():
    """A hub item with no recorded placement has no disposition escape:
    the normalizer refuses to render rather than silently dropping it."""
    env = envelope_mod.validate(_mini_envelope())
    rows = _settled_rows()  # no markers, no release qids
    with pytest.raises(RuntimeError, match="QINV-0001"):
        generation._normalize_activity_hubs_from_inventory(
            rows, dict(env["inventory"]), dict(env["mined_types"])
        )


def test_normalizer_is_idempotent_over_placed_hub_and_figure_notes():
    env = envelope_mod.validate(_mini_envelope())
    rows = _assembled(_mini_envelope())["rows"]
    again = generation._normalize_activity_hubs_from_inventory(
        [dict(row) for row in rows],
        dict(env["inventory"]),
        dict(env["mined_types"]),
    )
    assert [row["concept_details"] for row in again] == [
        row["concept_details"] for row in rows
    ]


def test_settle_topology_rules_carry_the_banner_guidance():
    """§4 Phase 1.2: activity/'do you know?' banners are never concepts of
    their own — prompt guidance only, judged by the model and audited by
    the advisory critic; no checker vocabulary exists for it."""
    import json as _json
    from pathlib import Path

    from app.services.phase3 import settle
    from tests import test_phase3_settle_golden as settle_golden

    golden = Path(__file__).parent / "golden"
    env = envelope_mod.load(golden / "rne_envelope.json")
    golden_rows = _json.loads(
        (golden / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]
    mapping = settle_golden._replay_map(env, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(mapping)
    seen: dict[str, str] = {}

    def capturing(request: dict) -> dict:
        seen.setdefault("rules", request["rules"])
        return topology(request)

    settle.settle(
        env,
        topology_provider=capturing,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )
    assert "NEVER concepts of their own" in seen["rules"]
    assert "Activity/Info Hub" in seen["rules"]


def test_containers_vocabulary_is_the_single_source():
    from app.services import coverage_ledger, question_polishing

    assert generation._HUB_INVENTORY_KINDS is containers.HUB_INVENTORY_KINDS
    assert coverage_ledger._HUB_KINDS is containers.HUB_INVENTORY_KINDS
    assert question_polishing.SKIP_KINDS is containers.HUB_INVENTORY_KINDS


def test_normalizer_binds_via_the_explicit_placements_mapping_too():
    """The binding union: a direct caller may hand the pass's verdicts as
    the explicit placements mapping (qid -> row index) instead of stamped
    row markers; either authority binds the hub item."""
    env = envelope_mod.validate(_mini_envelope())
    rows = _settled_rows()  # no markers, no release qids
    out = generation._normalize_activity_hubs_from_inventory(
        rows, dict(env["inventory"]), dict(env["mined_types"]),
        placements={"QINV-0001": 0, "QINV-0002": 1},
    )
    assert "metre" in out[0]["concept_details"]
    assert "plane mirror" in out[1]["concept_details"]
