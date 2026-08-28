"""Recorded RNE cell, materialization, and route verdicts replay once."""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

from app.services import assessment_cells as cells
from app.services import assessment_materialization as materialization
from app.services import assessment_routing as routing
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel


GOLDEN = Path(__file__).parent / "golden"


def _load_recorded() -> tuple[dict, dict]:
    recorded = json.loads(
        (GOLDEN / "rne_assessment_candidates.json").read_text(
            encoding="utf-8"
        )
    )
    assert recorded["envelope_fixture"] == "rne_envelope.json"
    envelope = envelope_mod.load(GOLDEN / recorded["envelope_fixture"])
    return recorded, envelope


def _q21_conforming_rows(recorded: dict) -> dict:
    """Filter only the mechanically superseded one-block four-mark row."""

    copied = copy.deepcopy(recorded)
    keep = [
        index
        for index, (cell, response) in enumerate(zip(
            copied["cell_responses"], copied["materialization_responses"]
        ))
        if not (
            cell.get("sheet_kind") == "descriptive"
            and float(cell.get("marks") or 0) == 4
            and len(response.get("answers") or []) < 2
        )
    ]
    for field in (
        "atoms", "cell_responses", "materialization_responses",
        "route_responses",
    ):
        copied[field] = [copied[field][index] for index in keep]
    kept_qids = {
        str(response.get("source_qid") or "")
        for response in copied["cell_responses"]
    }
    copied["expected_cell_ids"] = {
        qid: cell_id
        for qid, cell_id in copied["expected_cell_ids"].items()
        if qid in kept_qids
    }
    return copied


def _provider(recorded: dict, calls: list[str]) -> kernel.Provider:
    cell_rows = {
        row["source_qid"]: row for row in recorded["cell_responses"]
    }
    materialized_rows = {
        row["candidate_id"]: row
        for row in recorded["materialization_responses"]
    }
    route_rows = {
        row["candidate_id"]: row for row in recorded["route_responses"]
    }

    def provide(request: dict) -> dict:
        stage = request["stage"]
        calls.append(stage)
        if stage == "assessment.cell":
            row = cell_rows[request["source_atom"]["source_qid"]]
        elif stage == "assessment.materialize":
            row = materialized_rows[request["candidate_id"]]
        elif stage == "assessment.route":
            row = route_rows[request["candidate"]["candidate_id"]]
        else:  # pragma: no cover - an unknown pass is the regression
            raise AssertionError(f"unexpected assessment stage {stage!r}")
        return copy.deepcopy(row)

    return provide


def _critic(recorded: dict, calls: list[str]) -> kernel.Critic:
    def review(request: dict) -> dict:
        calls.append(request["stage"])
        return copy.deepcopy(recorded["critic_review"])

    return review


def _forbidden(label: str):
    def call(_request: dict) -> dict:
        raise AssertionError(f"cached replay called {label}")

    return call


def _run(
    recorded: dict,
    envelope: dict,
    *,
    store: kernel.DecisionStore,
    provider: kernel.Provider,
    critic: kernel.Critic,
    fixer: kernel.Provider,
) -> dict:
    atoms = copy.deepcopy(recorded["atoms"])
    decided_cells = cells.decide_cells(
        atoms,
        meta=recorded["metadata"],
        profile=recorded["profile"],
        envelope_sha256=envelope["envelope_sha256"],
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )
    materialized = materialization.materialize_candidates(
        list(zip(atoms, decided_cells)),
        meta=recorded["metadata"],
        context={
            "source_concept_release_sha256": recorded[
                "source_concept_release_sha256"
            ],
            "released_concepts": recorded["concepts"],
        },
        envelope_sha256=envelope["envelope_sha256"],
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )
    routed = routing.route_candidates(
        materialized["candidates"],
        recorded["concepts"],
        meta=recorded["metadata"],
        envelope_sha256=envelope["envelope_sha256"],
        source_concept_release_sha256=recorded[
            "source_concept_release_sha256"
        ],
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )
    return {
        "cells": decided_cells,
        "materialized": materialized,
        "routed": routed,
    }


def test_recorded_candidate_verdicts_replay_without_authority_calls() -> None:
    recorded, envelope = _load_recorded()
    recorded = _q21_conforming_rows(recorded)
    store = kernel.DecisionStore()
    provider_calls: list[str] = []
    critic_calls: list[str] = []

    first = _run(
        recorded,
        envelope,
        store=store,
        provider=_provider(recorded, provider_calls),
        critic=_critic(recorded, critic_calls),
        fixer=_forbidden("the Fixer"),
    )

    expected_ids = recorded["expected_cell_ids"]
    assert {
        cell["accepted_source_qids"][0]: cell["cell_id"]
        for cell in first["cells"]
    } == expected_ids
    for cell, response in zip(first["cells"], recorded["cell_responses"]):
        assert cell["accepted_source_qids"] == [response["source_qid"]]
        assert {
            key: cell[key]
            for key in response
            if key != "source_qid"
        } == {
            key: value
            for key, value in response.items()
            if key != "source_qid"
        }
        assert cell["authority"]["policy_version"] == "assessment-cell-3"

    candidates = first["materialized"]["candidates"]
    for candidate, response in zip(
        candidates, recorded["materialization_responses"]
    ):
        for key in (
            "candidate_id", "question", "display_answer", "answers",
            "sub_questions", "answer_explanation", "requires_visual",
        ):
            if key in {"answers", "sub_questions"}:
                continue
            assert candidate[key] == response[key]
        assert candidate["answer_restriction"] == ""
        assert candidate["restriction_reason"] == ""
        assert all(
            answer["answer_weightage"] == ""
            for answer in candidate["answers"]
        )
        assert all(
            str(subquestion.get("marks") or "") == ""
            and all(
                str(keyword.get("weightage") or "") == ""
                for keyword in subquestion.get("keywords") or []
            )
            for subquestion in candidate["sub_questions"]
        )
        assert candidate["question"] == candidate["question_text"]
        audit = candidate["_aegis_assessment_materialization"]
        assert audit["rationale"] == response["rationale"]
        assert audit["authority"]["policy_version"] == (
            "assessment-materialize-12"
        )

    for placement, response in zip(
        first["routed"]["placements"], recorded["route_responses"]
    ):
        assert {
            key: placement[key] for key in response
        } == response
        assert placement["authority"]["policy_version"] == (
            "assessment-route-2"
        )
    assert candidates[0]["shared_context"] == "Write a note on:"
    assert candidates[0]["source_context"]["parent_qid"] == "QINV-0016"

    expected_calls = Counter({
        "assessment.cell": 1,
        "assessment.materialize": 1,
        "assessment.route": 1,
    })
    assert Counter(provider_calls) == expected_calls
    assert Counter(critic_calls) == expected_calls
    assert len(store.keys()) == 3

    second = _run(
        recorded,
        envelope,
        store=store,
        provider=_forbidden("a provider"),
        critic=_forbidden("a critic"),
        fixer=_forbidden("the Fixer"),
    )

    assert second == first
    assert Counter(provider_calls) == expected_calls
    assert Counter(critic_calls) == expected_calls
    assert len(store.keys()) == 3


def test_q21_supersedes_the_recorded_one_block_four_mark_response() -> None:
    recorded, _envelope = _load_recorded()
    cell = recorded["cell_responses"][0]
    response = recorded["materialization_responses"][0]

    defects = materialization._proposal_defects(
        response, cell, str(response["candidate_id"]),
    )

    assert any("at least two answer/rubric blocks" in defect for defect in defects)
