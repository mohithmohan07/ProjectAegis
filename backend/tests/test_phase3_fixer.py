"""The Fixer (docs/aegis-restructure.md §8.2, Q13): mid-run blocks become
one recorded, flagged, content-addressed best-judgment decision and the
run completes. These regressions cover the kernel seam (F1), the settle
topic-resolution seam (F2), the exact-once coverage family (F18-F21/F24),
the validation-gate acceptance seams (F22/F39/F40), the assemble fixpoint
(F10), and the orchestration ceilings (F46-F48)."""
from __future__ import annotations

import pytest

from app.services import build_concepts
from app.services import generation as g
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import fixer as fixer_mod
from app.services.phase3 import kernel, settle


# ---------------------------------------------------------------------------
# F1 — the kernel seam


def _payload() -> dict:
    return {
        "stage": "test",
        "rules": "the active prompt text",
        "concepts": [{"concept_id": "C-1"}],
        "source_blocks": [{"block_id": "B-1", "text": "evidence"}],
    }


def test_structural_exhaustion_invokes_fixer_with_the_full_context():
    provider_calls: list[dict] = []
    fixer_calls: list[dict] = []

    def provider(request: dict) -> dict:
        provider_calls.append(request)
        return {"broken": True}

    def checker(response: dict) -> list[str]:
        return [] if response.get("fixed") else ["C-1 has no segments"]

    def fixer(request: dict) -> dict:
        fixer_calls.append(request)
        return {"fixed": True, "rationale": "restored the missing segment"}

    decision = kernel.decide(
        kind="settle.topology",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=provider,
        checker=checker,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )

    # Bounded corrections were spent first, then ONE fixer engagement.
    assert len(provider_calls) == 3
    assert len(fixer_calls) == 1
    request = fixer_calls[0]
    # The full Q13 context reaches the Fixer: the failing check, the
    # contract, the original payload (rules + source evidence), and the
    # last produced output.
    assert request["fixer"] is True
    assert request["blocked_check"] == ["C-1 has no segments"]
    assert request["contract"]["kind"] == "settle.topology"
    assert request["contract"]["unit_id"] == "U-1"
    assert request["original_payload"] == _payload()
    assert request["last_response"] == {"broken": True}
    assert request["response_contract_feedback"] == ["C-1 has no segments"]
    # The decision ships the Fixer's artifact, flagged per original defect,
    # and the stored record says the Fixer decided it.
    assert decision["response"]["fixed"] is True
    assert decision["fixer"] is True
    assert decision["review_flags"] == [
        "fixer: blocked=C-1 has no segments; "
        "decided=restored the missing segment"
    ]


def test_fixer_protocol_failure_raises_contract_error_exactly_as_today():
    fixer_calls = 0

    def fixer(_request: dict) -> dict:
        nonlocal fixer_calls
        fixer_calls += 1
        return {"still": "broken"}

    with pytest.raises(kernel.ContractError) as failed:
        kernel.decide(
            kind="test",
            unit_id="U-1",
            envelope_sha256="e" * 64,
            payload=_payload(),
            provider=lambda _request: {},
            checker=lambda _response: ["C-1 has no segments"],
            store=kernel.DecisionStore(),
            fixer=fixer,
        )

    assert fixer_calls == 3  # the Fixer gets bounded attempts too
    assert "C-1 has no segments" in str(failed.value)
    assert failed.value.defects


def test_a_fixer_decision_is_cached_and_replays_without_any_provider(tmp_path):
    provider_calls = 0
    fixer_calls = 0

    def provider(_request: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {}

    def fixer(_request: dict) -> dict:
        nonlocal fixer_calls
        fixer_calls += 1
        return {"fixed": True, "rationale": "decided once"}

    kwargs = dict(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=provider,
        checker=lambda response: (
            [] if response.get("fixed") else ["defect"]
        ),
        store=kernel.DecisionStore(tmp_path),
        fixer=fixer,
    )
    first = kernel.decide(**kwargs)
    second = kernel.decide(**kwargs)

    assert provider_calls == 3
    assert fixer_calls == 1
    assert first["key"] == second["key"]
    assert second["fixer"] is True
    # A fresh store over the same directory replays too: resume is free.
    resumed = kernel.decide(
        **{**kwargs, "store": kernel.DecisionStore(tmp_path)}
    )
    assert provider_calls == 3 and fixer_calls == 1
    assert resumed["response"] == {"fixed": True, "rationale": "decided once"}


def test_confidence_only_shortfalls_never_engage_the_fixer():
    def fixer(_request: dict) -> dict:
        pytest.fail("a confidence-only shortfall is not a fixer block")

    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=lambda _request: {"confidence": 0.88},
        checker=lambda _response: [
            "[confidence] C-1 confidence 0.880 is below 0.920"
        ],
        store=kernel.DecisionStore(),
        fixer=fixer,
    )
    assert "fixer" not in decision
    assert any(
        "shipped for review" in flag for flag in decision["review_flags"]
    )


def test_the_critic_stays_advisory_on_a_fixer_decision():
    def critic(_request: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.91,
            "issues": ["the fixer's placement looks doubtful"],
        }

    decision = kernel.decide(
        kind="test",
        unit_id="U-1",
        envelope_sha256="e" * 64,
        payload=_payload(),
        provider=lambda _request: {},
        checker=lambda response: (
            [] if response.get("fixed") else ["defect"]
        ),
        critic=critic,
        store=kernel.DecisionStore(),
        fixer=lambda _request: {"fixed": True, "rationale": "best judgment"},
    )

    # The decision stands; the dissent is a flag beside the fixer flag.
    assert decision["response"]["fixed"] is True
    assert any(flag.startswith("fixer: blocked=")
               for flag in decision["review_flags"])
    assert any("rejected" in flag for flag in decision["review_flags"])


# ---------------------------------------------------------------------------
# F2 — settle: a skeleton row that resolves to no graph topic


def _f2_envelope() -> dict:
    return envelope_mod.build(
        graph={
            "source_contract_hash": "S-1",
            "topics": [{"topic_id": "T-1", "title": "Real Topic"}],
            "subtopics": [],
            "blocks": [{"block_id": "B-1", "topic_id": "T-1"}],
        },
        canonical={"blocks": [{
            "block_id": "B-1",
            "display_text": "The topic teaches the mystery idea in depth.",
        }]},
        skeleton_rows=[{
            "concept_title": "Mystery Concept",
            "topic": "Nowhere",  # resolves to no graph topic
            "parent_concept": "Parent",
            "concept_details": "Description: A mystery claim about the topic.",
            "keywords": "mystery",
        }],
        inventory={"items": []},
        mined_types={"types": []},
    )


def _f2_providers():
    def topology(request: dict) -> dict:
        return {"decisions": [
            {
                "concept_id": concept["concept_id"],
                "decision": "keep",
                "confidence": 0.95,
                "reason": "keep",
                "segments": [{
                    "concept_title": concept["concept_title"],
                    "parent_concept": concept["parent_concept"],
                    "concept_details": concept["concept_details"],
                    "keywords": concept["keywords"],
                }],
            }
            for concept in request["concepts"]
        ]}

    def grounding(request: dict) -> dict:
        return {"concepts": [
            {
                "concept_id": concept["concept_id"],
                "source_block_ids": ["B-1"],
                "confidence": 0.95,
                "reason": "the block teaches it",
            }
            for concept in request["concepts"]
        ]}

    def analysis(request: dict) -> dict:
        return {"rows": [
            {
                "concept_id": concept["concept_id"],
                "concept_description": (
                    "The mystery idea describes how the topic's central "
                    "quantity behaves under its stated conditions, why the "
                    "rule applies, what each term means, and how a learner "
                    "verifies the outcome with the source's own worked "
                    "figures step by step in practice."
                ),
                "achieving_mastery": (
                    "Explain the mystery idea and verify it on a new case."
                ),
                "misconception_error_analysis": (
                    "Misconceptions: Students may believe the mystery idea "
                    "applies everywhere without checking its stated "
                    "conditions first."
                ),
            }
            for concept in request["concepts"]
        ]}

    return topology, grounding, analysis


def test_an_unresolvable_skeleton_row_is_hosted_by_the_fixer_and_flagged():
    topology, grounding, analysis = _f2_providers()
    fixer_calls: list[dict] = []

    def fixer(request: dict) -> dict:
        fixer_calls.append(request)
        return {"topic_id": "T-1", "rationale": "B-1 teaches this claim"}

    settled = settle.settle(
        _f2_envelope(),
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        store=kernel.DecisionStore(),
        fixer=fixer,
    )

    assert len(fixer_calls) == 1
    assert fixer_calls[0]["blocked_check"] == [
        "skeleton row resolves to no graph topic: Mystery Concept"
    ]
    assert [row["topic_id"] for row in fixer_calls[0]["topics"]] == ["T-1"]
    # R4: the row was never dropped — it settled under the decided topic,
    # flagged for review.
    assert len(settled) == 1
    assert settled[0]["_semantic_topic_id"] == "T-1"
    assert any(
        flag.startswith(
            "fixer: blocked=skeleton row resolves to no graph topic"
        )
        and "T-1" in flag
        for flag in settled[0]["review_flags"]
    )


def test_without_a_fixer_the_unresolvable_row_still_fails_closed():
    topology, grounding, analysis = _f2_providers()
    with pytest.raises(envelope_mod.EnvelopeError, match="no graph topic"):
        settle.settle(
            _f2_envelope(),
            topology_provider=topology,
            grounding_provider=grounding,
            analysis_provider=analysis,
            store=kernel.DecisionStore(),
        )


def test_a_fixer_that_names_no_real_topic_fails_closed_as_today():
    topology, grounding, analysis = _f2_providers()
    with pytest.raises(envelope_mod.EnvelopeError, match="no graph topic"):
        settle.settle(
            _f2_envelope(),
            topology_provider=topology,
            grounding_provider=grounding,
            analysis_provider=analysis,
            store=kernel.DecisionStore(),
            fixer=lambda _request: {
                "topic_id": "T-404", "rationale": "guess",
            },
        )


# ---------------------------------------------------------------------------
# Exact-once coverage family (F18-F21, F24): place, never drop


def _coverage_row(title: str, details: str = "") -> dict:
    return {
        "topic": "Power Play",
        "parent_concept": "Exponent Methods",
        "concept_title": title,
        "concept_details": details or (
            "Description: Interpret and verify exponent procedures."
        ),
        "keywords": "exponents, powers",
    }


def _coverage_item(qid: str, prompt: str) -> dict:
    return {
        "qid": qid,
        "source_kind": "exercise",
        "source_label": qid,
        "topic_hint": "Power Play",
        "raw_task": prompt,
        "normalized_task": prompt,
    }


_PROMPT = (
    "Use the laws of exponents to simplify 2^3 multiplied by 2^4 and "
    "explain the exponent in the result."
)


def test_an_unplaceable_qid_is_placed_by_the_fixer_and_flagged():
    inventory = {"items": [_coverage_item("QINV-0001", _PROMPT)]}
    records = [
        _coverage_row("Interpreting Exponent Procedures"),
        _coverage_row("Explaining Powers"),
    ]
    fixer_calls: list[dict] = []

    def fixer(request: dict) -> dict:
        fixer_calls.append(request)
        return {"row_index": 1, "rationale": "the question exercises powers"}

    out = g._fix_inventory_coverage_via_fixer(
        records, inventory, {"types": []},
        fixer=fixer, store=kernel.DecisionStore(),
    )

    assert len(fixer_calls) == 1
    assert fixer_calls[0]["question"]["qid"] == "QINV-0001"
    assert fixer_calls[0]["question"]["text"].startswith(
        "Use the laws of exponents"
    )
    # Placed exactly once, on the decided row, flagged — never dropped.
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [], "duplicate": [],
    }
    assert "Use the laws of exponents" in out[1]["concept_details"]
    assert "Use the laws of exponents" not in out[0]["concept_details"]
    assert any(
        flag.startswith("fixer: placed unhoused question QINV-0001 under")
        for flag in out[1]["review_flags"]
    )


def test_a_duplicate_render_is_resolved_to_exactly_one_placement():
    inventory = {"items": [_coverage_item("QINV-0001", _PROMPT)]}
    types_section = (
        "Description: Interpret and verify exponent procedures. // Types: "
        "Type 01: Applying exponent laws Case 01: Equal bases are "
        f"multiplied Example 01: {_PROMPT}"
    )
    records = [
        _coverage_row("Interpreting Exponent Procedures", types_section),
        _coverage_row("Explaining Powers", types_section),
    ]
    item = inventory["items"][0]
    assert g._rendered_inventory_coverage_defects(
        records, inventory
    )["duplicate"] == ["QINV-0001"]

    out = g._fix_inventory_coverage_via_fixer(
        records, inventory, {"types": []},
        fixer=lambda _request: {
            "row_index": 1, "rationale": "this concept teaches the law",
        },
        store=kernel.DecisionStore(),
    )

    # R4: the QID stays placed exactly once — only the extra render went.
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [], "duplicate": [],
    }
    assert g._rendered_inventory_example_locations(out, item) == [1]
    assert any(
        flag.startswith("fixer: blocked=duplicate render of QINV-0001")
        for flag in out[1]["review_flags"]
    )


def test_enforce_coverage_reaches_the_fixer_before_raising(monkeypatch):
    """When the deterministic ladder cannot place a QID, the Fixer gets it
    (seams F18-F21) before the gate may raise."""
    inventory = {"items": [_coverage_item("QINV-0001", _PROMPT)]}
    records = [_coverage_row("Interpreting Exponent Procedures")]
    # Force the deterministic repair to fail so the enforce gate reaches
    # its raise ladder with the defect still standing.
    monkeypatch.setattr(
        g, "_repair_rendered_inventory_coverage",
        lambda rows, *_args, **_kwargs: rows,
    )
    with pytest.raises(RuntimeError, match="exact inventory coverage"):
        g._enforce_rendered_inventory_coverage(
            records, inventory, {"types": []})

    out = g._enforce_rendered_inventory_coverage(
        records, inventory, {"types": []},
        fixer=lambda _request: {"row_index": 0, "rationale": "hosts it"},
        fixer_store=kernel.DecisionStore(),
    )
    assert g._rendered_inventory_coverage_defects(out, inventory) == {
        "missing": [], "duplicate": [],
    }
    assert any(
        flag.startswith("fixer: placed unhoused question QINV-0001")
        for flag in out[0]["review_flags"]
    )


# ---------------------------------------------------------------------------
# Validation gates (F22/F39/F40): correct or accept-with-flag, never silent


def _strict_row() -> dict:
    from tests.test_generation_validation_diagnostics import (
        _strict_normal_row,
    )

    return _strict_normal_row()


def _strict_culmination() -> dict:
    from tests.test_generation_validation_diagnostics import (
        _strict_culmination as culm,
    )

    return culm()


def _drop_mastery(row: dict) -> dict:
    row["concept_details"] = row["concept_details"].replace(
        "\nAchieving Mastery: Selecting and applying the current "
        "relationship with the supplied circuit quantities.",
        "",
    )
    return row


def test_an_advisory_code_ships_flagged_through_both_gates_without_a_fixer():
    """RE-AUTHORED under S11 (T10-2): this test used to pin the Fixer
    accept-with-flag round for ``missing_mastery_statement``. That code is
    not in ``_BLOCKING_CODES`` any more, so it never reaches the gate Fixer
    — it ships flagged directly, with no model decision needed, and the
    deposit twin passes on the same record. The Fixer machinery itself
    stays pinned by the correction test below and by the inventory seams.
    """
    records = [_drop_mastery(_strict_row()), _strict_culmination()]
    fixer_calls: list[dict] = []

    def fixer(request: dict) -> dict:
        fixer_calls.append(request)
        return {"accept_with_flag": True, "rationale": "never asked"}

    report = g._validate_final_or_raise(
        records, fixer=fixer, fixer_store=kernel.DecisionStore())

    assert report["summary"] is not None
    assert fixer_calls == [], (
        "an advisory code must not spend a Fixer decision"
    )
    assert "_fixer_accepted_codes" not in records[0]
    assert any(
        "validation: missing_mastery_statement at the final gate" in flag
        for flag in records[0]["review_flags"]
    )
    # The deposit twin ships the same record without a Fixer either.
    g._validate_final_or_raise(records, stage="deposit")
    assert fixer_calls == []


def test_a_fixer_corrected_row_clears_the_final_gate():
    """RE-AUTHORED under S11 (T10-2): the correction flow is pinned on a
    BLOCKING code now — ``required`` (an empty mandatory field), the class
    that still halts a run — since advisory codes no longer reach the gate
    Fixer at all.
    """
    healthy = _strict_row()
    broken = _strict_row()
    broken["concept_details"] = ""
    records = [broken, _strict_culmination()]
    blocked_codes: list[str] = []

    def fixer(request: dict) -> dict:
        blocked_codes.extend(
            entry["code"] for entry in request["blocked_check"])
        return {
            "row": {"concept_details": healthy["concept_details"]},
            "rationale": "restored the authored description",
        }

    g._validate_final_or_raise(
        records, fixer=fixer, fixer_store=kernel.DecisionStore())

    assert "required" in blocked_codes
    assert records[0]["concept_details"] == healthy["concept_details"]
    assert any(
        flag.startswith(
            "fixer: blocked=required at final validation; "
            "decided=corrected row content"
        )
        for flag in records[0]["review_flags"]
    )
    assert "_fixer_accepted_codes" not in records[0]


def test_mechanics_codes_are_never_acceptable_with_a_flag():
    # RE-AUTHORED under S11 (T10-3): the duplicate-title pair left the
    # mechanics set with the blocking set — identity no longer depends on
    # title text. ``required`` is the mechanics class that remains: a row
    # with no mandatory field is corruption, acceptance must be refused,
    # and with no correction on offer the gate keeps failing closed.
    broken = _strict_row()
    broken["concept_details"] = ""
    records = [broken, _strict_culmination()]
    fixer_calls = 0

    def fixer(_request: dict) -> dict:
        nonlocal fixer_calls
        fixer_calls += 1
        return {"accept_with_flag": True, "rationale": "please accept"}

    with pytest.raises(RuntimeError, match="required"):
        g._validate_final_or_raise(
            records, fixer=fixer, fixer_store=kernel.DecisionStore())

    assert fixer_calls > 0  # it was asked, and its acceptance was refused
    assert all("_fixer_accepted_codes" not in row for row in records)


def test_empty_inventory_rows_can_be_accepted_but_identity_cannot():
    inventory = {"items": [
        {"qid": "QINV-0001", "source_kind": "exercise", "raw_task": "   "},
        {"source_kind": "exercise", "raw_task": "A real question here?"},
        # Brevity is no longer a defect at all: the two-word row that the
        # retired word-count cascade called a ``stub_task`` never reaches
        # the Fixer, because nothing rejects it.
        {"qid": "QINV-0003", "source_kind": "exercise", "raw_task": "Do."},
    ]}
    records = [_coverage_row("Interpreting Exponent Procedures")]
    invalid = g._invalid_inventory_items(inventory)
    reasons = {entry["reason"] for entry in invalid}
    assert reasons == {"empty_task", "missing_qid"}
    assert not any(entry.get("qid") == "QINV-0003" for entry in invalid)

    remaining = g._fix_invalid_inventory_via_fixer(
        records, inventory, invalid,
        stage="final",
        fixer=lambda _request: {
            "accept_with_flag": True,
            "rationale": "an empty banner row, not a learner question",
        },
        store=kernel.DecisionStore(),
    )

    # The text-less row was accepted (flagged on its host row); the identity
    # defect — a row with no qid — stays mechanics and keeps blocking.
    assert [entry["reason"] for entry in remaining] == ["missing_qid"]
    assert any(
        flag.startswith("fixer: blocked=invalid inventory row QINV-0001")
        for flag in records[0]["review_flags"]
    )


# ---------------------------------------------------------------------------
# F10 — the assemble fixpoint: converging drift is adopted, oscillation raises


@pytest.fixture(scope="module")
def _assemble_chain():
    import json

    from tests import test_phase3_host_golden as host_golden
    from tests import test_phase3_settle_golden as settle_golden
    from app.services.phase3 import host as host_mod

    golden_envelope = settle_golden.envelope_mod.load(
        settle_golden.GOLDEN / "rne_envelope.json"
    )
    golden_rows = json.loads(
        (settle_golden.GOLDEN / "rne_settled_rows.json").read_text(
            encoding="utf-8"
        )
    )["records"]
    golden_hosts = json.loads(
        (settle_golden.GOLDEN / "rne_host_maps.json").read_text(
            encoding="utf-8"
        )
    )
    mapping = settle_golden._replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(mapping)
    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )
    hosts = host_mod.host(
        golden_envelope,
        settled,
        provider=host_golden._replay_provider(golden_hosts, settled),
        critic=host_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    return golden_envelope, settled, hosts


def test_converging_pipeline_drift_is_adopted_and_flagged(
    _assemble_chain, monkeypatch,
):
    from app.services import concept_refiner
    from app.services.phase3 import assemble as assemble_mod

    golden_envelope, settled, hosts = _assemble_chain
    real_refine = concept_refiner.refine_chapter

    def drifting_refine(rows, *args, **kwargs):
        out = real_refine(rows, *args, **kwargs)
        for row in out:
            keywords = str(row.get("keywords") or "")
            # Converges after two applications: k -> k! -> k!! -> k!!.
            if not keywords.endswith("!!"):
                row["keywords"] = keywords + "!"
        return out

    monkeypatch.setattr(concept_refiner, "refine_chapter", drifting_refine)

    result = assemble_mod.assemble(golden_envelope, settled, hosts)

    assert all(
        str(row.get("keywords") or "").endswith("!!")
        for row in result["rows"]
    )
    flagged = [
        row for row in result["rows"]
        if any(
            flag.startswith(
                "assembly output normalized by the deterministic deposit "
                "pipeline; keywords adjusted"
            )
            for flag in row.get("review_flags") or []
        )
    ]
    assert flagged, "changed rows must carry the normalization flag"


def test_oscillating_pipeline_output_still_raises(
    _assemble_chain, monkeypatch,
):
    from app.services import concept_refiner
    from app.services.phase3 import assemble as assemble_mod

    golden_envelope, settled, hosts = _assemble_chain
    real_refine = concept_refiner.refine_chapter

    def oscillating_refine(rows, *args, **kwargs):
        out = real_refine(rows, *args, **kwargs)
        for row in out:
            keywords = str(row.get("keywords") or "")
            row["keywords"] = (
                keywords[:-1] if keywords.endswith("~") else keywords + "~"
            )
        return out

    monkeypatch.setattr(concept_refiner, "refine_chapter", oscillating_refine)

    with pytest.raises(assemble_mod.AssemblyError, match="not stable"):
        assemble_mod.assemble(golden_envelope, settled, hosts)


# ---------------------------------------------------------------------------
# Orchestration ceilings (F46-F48): the run continues, recorded and flagged


def test_an_exhausted_ceiling_settles_with_best_judgement_and_a_flag(
    monkeypatch,
):
    logs: list[str] = []
    recorded: list[dict] = []
    monkeypatch.setattr(
        build_concepts.progress, "log",
        lambda message, **_kwargs: logs.append(str(message)),
    )

    def record(_db, _job, decision_id, **kwargs):
        recorded.append({"decision_id": decision_id, **kwargs})
        return {"resolved_decision": {"decision_id": "settled-0001"}}

    monkeypatch.setattr(
        build_concepts, "_record_human_semantic_decision_locked", record)

    settled_id = build_concepts._settle_exhausted_ceiling(
        None, None,
        {"decision_id": "d-0001", "kind": "phase33_host_conflict"},
        owner_sub=None,
        reason="24 repair attempt(s) for the same semantic scope were "
        "spent and it kept returning.",
        ceiling="pathway_turns",
    )

    assert settled_id == "settled-0001"
    assert recorded[0]["choice"] == "carry_forward"
    assert recorded[0]["resolved_by"] == "agent"
    assert any(
        "Placed by best judgement:" in row
        and "fixer: ceiling=pathway_turns" in row
        for row in logs
    )


def _user_only_pending() -> dict:
    return {
        "decision_id": "d-0002",
        "context_hash": "c" * 64,
        "kind": "phase3_source_graph_review",
        "conflict": "the offered routes all require a person",
        "options": [
            {"choice": "replace_source", "label": "Replace the source"},
            {"choice": "custom_instruction", "label": "Give an instruction"},
        ],
    }


def test_the_fixer_resolution_choice_can_take_a_user_only_route(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(build_concepts.progress, "log", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_FIXER_FALLBACK_STORE", None)
    monkeypatch.setattr(
        fixer_mod, "default_provider",
        lambda: lambda _request: {
            "choice": "custom_instruction",
            "instruction": "Merge the two fragment topics into Real Topic.",
            "rationale": "the fragments plainly belong together",
        },
    )

    def record(_db, _job, decision_id, **kwargs):
        recorded.append({"decision_id": decision_id, **kwargs})
        return {"resolved_decision": {"decision_id": "fixed-0002"}}

    monkeypatch.setattr(
        build_concepts, "_record_human_semantic_decision_locked", record)

    fixed_id = build_concepts._apply_fixer_resolution_choice(
        None, None, _user_only_pending(), owner_sub=None)

    assert fixed_id == "fixed-0002"
    assert recorded[0]["choice"] == "custom_instruction"
    assert recorded[0]["instruction"] == (
        "Merge the two fragment topics into Real Topic."
    )
    assert recorded[0]["fixer_decision"] is True
    assert recorded[0]["resolution_status"] == "consumed"


def test_the_fixer_may_never_pick_replace_source(monkeypatch):
    calls = 0
    monkeypatch.setattr(build_concepts.progress, "log", lambda *_a, **_k: None)
    monkeypatch.setattr(g, "_FIXER_FALLBACK_STORE", None)

    def insists(_request: dict) -> dict:
        nonlocal calls
        calls += 1
        return {
            "choice": "replace_source",
            "rationale": "the source is broken",
        }

    monkeypatch.setattr(fixer_mod, "default_provider", lambda: insists)
    monkeypatch.setattr(
        build_concepts, "_record_human_semantic_decision_locked",
        lambda *_a, **_k: pytest.fail("replace_source must never record"),
    )

    fixed_id = build_concepts._apply_fixer_resolution_choice(
        None, None, _user_only_pending(), owner_sub=None)

    # Protocol impossibility: the caller's raise then ends the run.
    assert fixed_id is None
    assert calls == 3


# ---------------------------------------------------------------------------
# The Fixer shares the run's active model


def test_live_fixer_requests_the_fixer_model(monkeypatch):
    """Every live Fixer decision uses the same Luna identity as the run."""

    captured: list[dict] = []

    def fake_openai_json(system, user, *args, **kwargs):
        captured.append({"system": system, "user": user, **kwargs})
        return {"decision": "recorded"}

    monkeypatch.setattr(g, "_openai_json", fake_openai_json)
    monkeypatch.setattr(g.config, "OPENAI_MODEL", "gpt-5.6-luna")

    assert fixer_mod.fixer_model() == "gpt-5.6-luna"
    fixer_mod.live_fixer(_payload())
    assert captured[-1]["model"] == "gpt-5.6-luna"
    assert captured[-1]["purpose"] == "concept_validation"
