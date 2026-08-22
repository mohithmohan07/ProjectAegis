"""Exact home-concept routing (MES spec §6 Stage 7, §5.4).

Every assessment candidate belongs to exactly one released concept.  A real
choice is one recorded ``assessment.route`` decision made through the Phase-3
kernel: mechanical response defects receive bounded corrections, the critic
is advisory, and an immutable decision-store entry makes resume free.

The only no-spend routes are mechanical.  An explicit blueprint constraint
that names a released concept, or a scope containing exactly one concept,
leaves nothing for a model to decide.  Objective routing carries the correct
answer as topical evidence and never carries distractors.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .. import config
from . import assessment_release as rel
from .phase3 import kernel

# -2: released candidate concepts and rules are now the explicit GPT-5.6
# cache prefix; the routed candidate is the complete varying suffix.
ROUTE_POLICY_VERSION = "assessment-route-2"

_PROMPT_CACHE_STABLE_KEYS = (
    "stage",
    "rules",
    "critic_rules",
    "metadata",
    "source_concept_release_sha256",
    "candidate_concepts",
)

ROUTER_SYSTEM = (
    "You are the Aegis assessment router. Choose the ONE released concept "
    "whose teaching content this question assesses: its canonical home. "
    "Judge from the complete question, expected answer or rubric, source "
    "and routing evidence, assets, and every candidate concept's released "
    "teaching description.\n"
    "Never route by position, list order, title overlap, topic proximity, "
    "difficulty, quotas, or spreading questions evenly. A concept marked "
    "upstream as Culmination is not a fallback: choose it only when its own "
    "released description teaches the genuine cross-concept synthesis the "
    "question assesses.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","concept_key":"","evidence":"decisive released '
    'teaching content","rationale":"evidence-bound reason"}'
)

ROUTE_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis home-concept "
    "route. Audit the proposed route against the complete question, answer "
    "or rubric, source and routing evidence, assets, and every released "
    "candidate concept description. Flag a concept that does not teach what "
    "the question assesses, a Culmination route that is not genuine "
    "synthesis, or reasoning based on names, ordering, difficulty, quotas, "
    "or spreading. Do not revise or gate the route. Your dissent is advisory: "
    "the proposed route stands and your concerns ship for review. State your "
    "honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)


class RoutingError(ValueError):
    """A routing input or exact-coverage invariant was violated."""


_PRINT_POSITION_FIELDS = frozenset({
    "bbox",
    "example_number",
    "page",
    "page_hint",
    "position",
    "printer_page",
    "row",
    "row_index",
    "row_number",
    "source_end",
    "source_order",
    "source_page",
    "source_paper_number",
    "source_start",
    "_phase32_segment_order",
    "_phase32_source_order",
})


def _content_evidence(value: Any) -> Any:
    """Deep-copy semantic evidence while removing printer coordinates."""

    if isinstance(value, Mapping):
        return {
            str(key): _content_evidence(raw)
            for key, raw in value.items()
            if str(key) not in _PRINT_POSITION_FIELDS
        }
    if isinstance(value, list):
        return [_content_evidence(raw) for raw in value]
    if isinstance(value, tuple):
        return [_content_evidence(raw) for raw in value]
    return copy.deepcopy(value)


def routing_answer_evidence(candidate: Mapping) -> dict[str, Any]:
    """Project complete answer evidence, filtering Objective distractors.

    Objective options have one purpose in routing: the correct answer is
    topical evidence.  Descriptive questions instead carry every authored
    answer, rubric, marking instruction, and sub-question without summaries
    or truncation.
    """

    if str(candidate.get("sheet_kind") or "").strip().lower() == "objective":
        correct = [
            str(answer.get("answer_content") or answer.get("answer") or "")
            for answer in candidate.get("answers") or []
            if isinstance(answer, Mapping)
            and rel.is_correct_option(answer.get("correct_answer"))
        ]
        return {"correct_answer": [text for text in correct if text]}
    return {
        "display_answer": copy.deepcopy(candidate.get("display_answer")),
        "answers": copy.deepcopy(candidate.get("answers")),
        "rubric": copy.deepcopy(candidate.get("rubric")),
        "marking_scheme": copy.deepcopy(candidate.get("marking_scheme")),
        "sub_questions": copy.deepcopy(candidate.get("sub_questions")),
        "answer_explanation": copy.deepcopy(
            candidate.get("answer_explanation")
        ),
        "answer_evidence": copy.deepcopy(candidate.get("answer_evidence")),
    }


def _candidate_payload(candidate: Mapping) -> dict[str, Any]:
    """Carry the candidate's semantic routing evidence without position.

    Printer/page/order coordinates are deliberately absent.  For Objective
    rows, answer-bearing fields are represented only by
    :func:`routing_answer_evidence`, so an option distractor cannot become a
    concept-routing hint.
    """

    objective = (
        str(candidate.get("sheet_kind") or "").strip().lower() == "objective"
    )
    payload = {
        "candidate_id": str(candidate.get("candidate_id") or "").strip(),
        "question": copy.deepcopy(candidate.get("question")),
        "question_text": copy.deepcopy(candidate.get("question_text")),
        "sheet_kind": copy.deepcopy(candidate.get("sheet_kind")),
        "question_category": copy.deepcopy(
            candidate.get("question_category")
        ),
        "cognitive_skill": copy.deepcopy(candidate.get("cognitive_skill")),
        "difficulty": copy.deepcopy(candidate.get("difficulty")),
        "marks": copy.deepcopy(candidate.get("marks")),
        "answer_restriction": copy.deepcopy(
            candidate.get("answer_restriction")
        ),
        "restriction_reason": copy.deepcopy(
            candidate.get("restriction_reason")
        ),
        "answer_evidence": routing_answer_evidence(candidate),
        "requires_visual": bool(candidate.get("requires_visual")),
        "assets": copy.deepcopy(candidate.get("assets")),
        "image_manifest": copy.deepcopy(candidate.get("image_manifest")),
        "image_urls": copy.deepcopy(candidate.get("image_urls")),
        "images": copy.deepcopy(candidate.get("images")),
        "tables": copy.deepcopy(candidate.get("tables")),
        "content_objects": copy.deepcopy(candidate.get("content_objects")),
        "source_atom_ids": copy.deepcopy(candidate.get("source_atom_ids")),
        "source_qid": copy.deepcopy(candidate.get("source_qid")),
        "source_document_hash": copy.deepcopy(
            candidate.get("source_document_hash")
        ),
        "source_kind": copy.deepcopy(candidate.get("source_kind")),
        "source_evidence": copy.deepcopy(candidate.get("source_evidence")),
        "shared_context": copy.deepcopy(candidate.get("shared_context")),
        "source_context": copy.deepcopy(candidate.get("source_context")),
        "route_evidence": copy.deepcopy(candidate.get("route_evidence")),
        "blueprint_cell_id": copy.deepcopy(
            candidate.get("blueprint_cell_id")
        ),
        "assessment_gist": copy.deepcopy(candidate.get("assessment_gist")),
    }
    if not objective:
        # These rich public/source forms can contain a rendered option list
        # for Objective rows.  The stem already rides above, so include the
        # forms only where they cannot re-introduce distractors.
        payload.update({
            "question_display": copy.deepcopy(
                candidate.get("question_display")
            ),
            "raw_text": copy.deepcopy(candidate.get("raw_text")),
            "normalized_public_text": copy.deepcopy(
                candidate.get("normalized_public_text")
            ),
            "options": copy.deepcopy(candidate.get("options")),
        })
    return _content_evidence(payload)


def _concept_payload(concepts: list[Mapping]) -> list[dict[str, Any]]:
    """Carry complete released semantic evidence without position proxies.

    ``is_culmination`` is copied only when the staged release supplies it;
    this module never infers culmination status from a title or position.
    """

    rows: list[dict[str, Any]] = []
    for concept in concepts:
        row = _content_evidence(dict(concept))
        row["concept_key"] = str(concept.get("concept_key") or "").strip()
        # Preserve the established wire shape without manufacturing meaning.
        row.setdefault("concept_display_name", None)
        row.setdefault("concept_title", None)
        row.setdefault("description", None)
        row.setdefault("teaching_description", None)
        row.setdefault("concept_description", None)
        row.setdefault("concept_details", None)
        row.setdefault("is_culmination", None)
        rows.append(row)
    return rows


def _candidate_id(candidate: Mapping, *, position: int | None = None) -> str:
    if not isinstance(candidate, Mapping):
        where = f" {position}" if position is not None else ""
        raise RoutingError(f"routing candidate{where} is not an object")
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not candidate_id:
        where = f" {position}" if position is not None else ""
        raise RoutingError(f"routing candidate{where} has no candidate_id")
    return candidate_id


def _concept_keys(concepts: list[Mapping]) -> list[str]:
    keys: list[str] = []
    for position, concept in enumerate(concepts, start=1):
        if not isinstance(concept, Mapping):
            raise RoutingError(f"routing concept {position} is not an object")
        raw_key = concept.get("concept_key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise RoutingError(
                f"routing concept {position} has no string concept_key"
            )
        keys.append(raw_key.strip())
    if len(keys) != len(set(keys)):
        raise RoutingError("routing concepts repeat a concept_key")
    return keys


def _envelope_hash(value: str) -> str:
    envelope_sha = str(value or "").strip()
    if not envelope_sha:
        raise RoutingError("assessment routing requires an envelope hash")
    return envelope_sha


def _constraint(candidate: Mapping) -> str | None:
    """Return an explicit release-key constraint, including legacy alias."""

    raw_key = candidate.get("blueprint_concept_key")
    raw_alias = candidate.get("blueprint_concept_id")
    key = str(raw_key).strip() if raw_key is not None else ""
    alias = str(raw_alias).strip() if raw_alias is not None else ""
    if key and alias and key != alias:
        raise RoutingError(
            "blueprint_concept_key and blueprint_concept_id disagree"
        )
    return key or alias or None


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag)
        for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]


def _decision_authority(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Stable row-private audit; volatile provider/time fields never escape."""

    return {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": _review_flags(decision),
        "fixer": bool(decision.get("fixer")),
    }


def _mechanical_authority(basis: str) -> dict[str, Any]:
    return {
        "decision_key": "",
        "policy_version": ROUTE_POLICY_VERSION,
        "review_flags": [],
        "fixer": False,
        "mechanical_basis": basis,
    }


def _placement(
    candidate: Mapping,
    *,
    concept_key: str | None,
    basis: str,
    evidence: str = "",
    rationale: str = "",
    flags: list[str] | None = None,
    candidate_routes: list[str] | None = None,
    authority: Mapping | None = None,
) -> dict[str, Any]:
    """Build one stable AssessmentPlacement record."""

    # ``concept_id`` remains a compatibility alias, but it is never a second
    # identity: it is mechanically identical to the released concept_key.
    return {
        "candidate_id": str(candidate.get("candidate_id") or "").strip(),
        "concept_key": concept_key,
        "concept_id": concept_key,
        "group_key": "",
        "secondary_placements": [],
        "candidate_routes": list(candidate_routes or []),
        "selected_route": concept_key,
        "basis": basis,
        "evidence": str(evidence or ""),
        "rationale": str(rationale or ""),
        "reason": str(rationale or ""),
        "confidence": "",
        "flags": list(flags or []),
        "authority": dict(authority or {}),
    }


def _route_checker(candidate_id: str, route_keys: list[str]) -> kernel.Checker:
    """Mechanics only: exact identity, one known route, required strings."""

    known = set(route_keys)

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        response_candidate = response.get("candidate_id")
        if not isinstance(response_candidate, str) or (
            response_candidate != candidate_id
        ):
            defects.append(f"candidate_id must echo {candidate_id!r}")
        concept_key = response.get("concept_key")
        if not isinstance(concept_key, str) or concept_key not in known:
            defects.append("concept_key must name one candidate concept exactly")
        evidence = response.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            defects.append("response has no evidence")
        rationale = response.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            defects.append("response has no rationale")
        return defects

    return check


def _live_route(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload,
        stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    candidate = payload.get("candidate")
    candidate_id = (
        str(candidate.get("candidate_id") or "")
        if isinstance(candidate, Mapping) else ""
    )
    return generation._openai_json(
        ROUTER_SYSTEM,
        suffix,
        purpose="concept_mapping",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "route-author-v2",
            prefix,
            shard_seed=candidate_id,
        ),
    )


def _live_route_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload,
        stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    candidate = payload.get("candidate")
    candidate_id = (
        str(candidate.get("candidate_id") or "")
        if isinstance(candidate, Mapping) else ""
    )
    return generation._openai_json(
        ROUTE_CRITIC_SYSTEM,
        suffix,
        purpose="concept_validation",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "route-critic-v2",
            prefix,
            shard_seed=candidate_id,
        ),
    )


def _live_authorities(
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    fixer: kernel.Provider | None,
) -> tuple[kernel.Provider, kernel.Critic | None, kernel.Provider | None]:
    """Wire production authorities while preserving injected test seams."""

    if provider is not None:
        return provider, critic, fixer
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod

    envelope_mod.require_live_api()
    return _live_route, critic or _live_route_critic, fixer or fixer_mod.live_fixer


def route_candidate(
    candidate: Mapping,
    concepts: list[Mapping],
    *,
    meta: Mapping,
    envelope_sha256: str,
    source_concept_release_sha256: str = "",
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Route one candidate by mechanics or one cached kernel decision."""

    candidate_id = _candidate_id(candidate)
    route_keys = _concept_keys(concepts)
    envelope_sha = _envelope_hash(envelope_sha256)
    constrained = _constraint(candidate)
    if constrained is not None:
        if constrained in route_keys:
            basis = "blueprint_constraint"
            return _placement(
                candidate,
                concept_key=constrained,
                basis=basis,
                evidence="the blueprint cell names this released concept",
                rationale="the explicit valid constraint leaves no choice",
                candidate_routes=route_keys,
                authority=_mechanical_authority(basis),
            )
        basis = "blueprint_constraint"
        flag = "blueprint_concept_not_in_scope"
        return _placement(
            candidate,
            concept_key=None,
            basis=basis,
            flags=[flag],
            candidate_routes=route_keys,
            authority={
                **_mechanical_authority(basis),
                "review_flags": [flag],
            },
        )
    if len(route_keys) == 1:
        basis = "sole_candidate"
        return _placement(
            candidate,
            concept_key=route_keys[0],
            basis=basis,
            evidence="the released scope contains exactly one concept",
            rationale="the sole-candidate scope leaves no choice",
            candidate_routes=route_keys,
            authority=_mechanical_authority(basis),
        )
    if not route_keys:
        basis = "no_candidates"
        flag = "no_candidate_concepts"
        return _placement(
            candidate,
            concept_key=None,
            basis=basis,
            flags=[flag],
            candidate_routes=[],
            authority={
                **_mechanical_authority(basis),
                "review_flags": [flag],
            },
        )

    source_release_sha = str(source_concept_release_sha256 or "").strip()
    if not source_release_sha:
        raise RoutingError(
            "semantic multi-concept routing requires the sealed source "
            "concept release hash"
        )
    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    decision_store = store or kernel.DecisionStore()
    payload = {
        "stage": "assessment.route",
        "rules": ROUTER_SYSTEM,
        "critic_rules": ROUTE_CRITIC_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "source_concept_release_sha256": source_release_sha,
        "candidate": _candidate_payload(candidate),
        "candidate_concepts": _concept_payload(concepts),
    }
    decision = kernel.decide(
        kind="assessment.route",
        unit_id=candidate_id,
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_route_checker(candidate_id, route_keys),
        critic=critic,
        store=decision_store,
        policy_version=ROUTE_POLICY_VERSION,
        fixer=fixer,
    )
    response = copy.deepcopy(dict(decision["response"]))
    flags = _review_flags(decision)
    return _placement(
        candidate,
        concept_key=str(response.get("concept_key") or ""),
        basis="api_router",
        evidence=str(response.get("evidence") or ""),
        rationale=str(response.get("rationale") or ""),
        flags=flags,
        candidate_routes=route_keys,
        authority=_decision_authority(decision),
    )


def route_candidates(
    candidates: list[Mapping],
    concepts: list[Mapping],
    *,
    meta: Mapping,
    envelope_sha256: str,
    source_concept_release_sha256: str = "",
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Route every candidate with exact, ordered, duplicate-free coverage."""

    expected_ids = [
        _candidate_id(candidate, position=position)
        for position, candidate in enumerate(candidates, start=1)
    ]
    if len(expected_ids) != len(set(expected_ids)):
        raise RoutingError("routing candidates repeat a candidate_id")
    _concept_keys(concepts)
    envelope_sha = _envelope_hash(envelope_sha256)
    if not candidates:
        return {"placements": [], "routed": 0, "flagged": 0}

    decision_store = store or kernel.DecisionStore()

    def route_one(candidate: Mapping) -> dict[str, Any]:
        return route_candidate(
            candidate,
            concepts,
            meta=meta,
            envelope_sha256=envelope_sha,
            source_concept_release_sha256=source_concept_release_sha256,
            provider=provider,
            critic=critic,
            store=decision_store,
            fixer=fixer,
        )

    placements = kernel.parallel_map_in_order(
        candidates,
        route_one,
        max_workers=config.phase3_decision_workers(),
    )
    returned_ids = [
        str(placement.get("candidate_id") or "") for placement in placements
    ]
    if returned_ids != expected_ids or len(returned_ids) != len(set(returned_ids)):
        missing = [value for value in expected_ids if value not in returned_ids]
        unexpected = [value for value in returned_ids if value not in expected_ids]
        raise RoutingError(
            "routing did not preserve exact candidate coverage and order "
            f"(missing={missing}, unexpected={unexpected})"
        )
    return {
        "placements": placements,
        "routed": sum(
            1 for placement in placements if placement["concept_key"] is not None
        ),
        "flagged": sum(1 for placement in placements if placement["flags"]),
    }
