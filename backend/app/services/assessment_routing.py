"""Exact home-concept routing (MES spec §6 Stage 7, §5.4).

Every MES question belongs to exactly one canonical home concept. The
router is a model judgment made from the full question, its answer
evidence, the source provenance, and every candidate concept's teaching
description — verified by an independent read-only critic bound to the
exact proposal by SHA-256.

What the router must never do is deterministic placement arithmetic:
round-robin, first/last concept, title overlap, even spreading, automatic
duplication, or Culmination as a dumping ground. The only mechanical
routes are a blueprint cell's explicit concept constraint and a
sole-candidate scope — configurations with nothing to decide.

For Objective questions the router sees the correct answer as topical
evidence and never the distractors (spec Stage 7): that filter is
mechanical evidence selection, applied before any model call.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from . import assessment_release as rel

MAX_ATTEMPTS = 3

ROUTER_SYSTEM = (
    "You are the Aegis assessment router. Choose the ONE concept whose "
    "teaching content this question assesses — its canonical home. Judge "
    "by the complete question, its answer evidence, and the source "
    "provenance against each candidate concept's teaching description.\n"
    "Never route by position, list order, title overlap, or spreading "
    "questions evenly. Never pick a Culmination concept unless the "
    "question is genuine cross-concept synthesis entailed by that "
    "Culmination's own description — Culmination is not a fallback for "
    "uncertainty.\n"
    "Return ONLY strict JSON:\n"
    '{"concept_id":0,"evidence":"the decisive teaching content","reason":'
    '"one sentence","confidence":"high|medium|low"}'
)

ROUTE_CRITIC_SYSTEM = (
    "You are the independent Aegis routing critic. You are READ-ONLY: you "
    "verify one proposed home-concept placement against the question, its "
    "answer evidence, and every candidate concept description. Reject a "
    "placement whose concept does not actually teach what the question "
    "assesses, a Culmination placement that is not genuine synthesis, or "
    "evidence that relies on title overlap rather than teaching content.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"accept","proposal_sha256":"","feedback":[]} or '
    '{"verdict":"reject","proposal_sha256":"","feedback":["evidence-bound '
    'reason ..."]}\n'
    "proposal_sha256 MUST echo the exact hash you were given."
)


def routing_answer_evidence(candidate: Mapping) -> dict:
    """Mechanical evidence filter: Objective routing ignores distractors."""
    if str(candidate.get("sheet_kind") or "") == "objective":
        correct = [
            str(a.get("answer_content") or "")
            for a in candidate.get("answers") or []
            if isinstance(a, Mapping)
            and str(a.get("correct_answer") or "").strip()
            in {"1", "true", "True"}
        ]
        return {"correct_answer": [c for c in correct if c]}
    return {
        "display_answer": str(candidate.get("display_answer") or ""),
        "answers": [
            str(a.get("answer_content") or a.get("answer") or "")
            for a in candidate.get("answers") or []
            if isinstance(a, Mapping)
        ],
        "sub_questions": [
            {
                "text": str(s.get("text") or ""),
                "marks": s.get("marks"),
            }
            for s in candidate.get("sub_questions") or []
            if isinstance(s, Mapping)
        ],
    }


def _concept_payload(concepts: list[Mapping]) -> list[dict]:
    return [
        {
            "concept_id": c.get("concept_id"),
            "concept_title": str(c.get("concept_title") or ""),
            "teaching_description": str(
                c.get("teaching_description") or "")[:2_000],
            "is_culmination": bool(c.get("is_culmination")),
        }
        for c in concepts
    ]


def _router_user(
    candidate: Mapping, concepts: list[Mapping], meta: Mapping,
    feedback: list[str],
) -> str:
    payload = {
        "metadata": dict(meta),
        "question": str(candidate.get("question") or ""),
        "question_text": str(candidate.get("question_text") or ""),
        "answer_evidence": routing_answer_evidence(candidate),
        "source_evidence": str(candidate.get("source_evidence") or ""),
        "route_evidence": candidate.get("route_evidence") or {},
        "blueprint": {
            "sheet_kind": candidate.get("sheet_kind"),
            "question_category": candidate.get("question_category"),
            "cognitive_skill": candidate.get("cognitive_skill"),
            "difficulty": candidate.get("difficulty"),
            "marks": candidate.get("marks"),
        },
        "candidate_concepts": _concept_payload(concepts),
    }
    body = json.dumps(payload, ensure_ascii=False)
    if feedback:
        body += (
            "\n\nYOUR PREVIOUS PLACEMENT WAS REJECTED BY THE INDEPENDENT "
            "CRITIC. Address every item, then return a fresh placement:\n- "
            + "\n- ".join(feedback)
        )
    return body


def _placement(
    candidate: Mapping,
    *,
    concept_id: Any,
    basis: str,
    evidence: str = "",
    reason: str = "",
    confidence: str = "",
    flags: list[str] | None = None,
    candidate_routes: list | None = None,
    authority: Mapping | None = None,
) -> dict:
    """AssessmentPlacement record (spec §5.4); group_key lands with grouping."""
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "concept_id": concept_id,
        "group_key": "",
        "secondary_placements": [],
        "candidate_routes": list(candidate_routes or []),
        "selected_route": concept_id,
        "basis": basis,
        "evidence": evidence,
        "reason": reason,
        "confidence": confidence,
        "flags": list(flags or []),
        "authority": dict(authority or {}),
    }


def route_candidate(
    candidate: Mapping,
    concepts: list[Mapping],
    *,
    meta: Mapping,
    router_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """One candidate -> one placement, routed or flagged. Never raises.

    Mechanical routes first: a blueprint concept constraint or a
    sole-candidate scope leaves nothing to decide. Everything else is a
    model judgment with an independent critic; exhaustion or provider
    failure ships a flagged placement preserving the best evidence.
    """
    route_ids = [c.get("concept_id") for c in concepts]
    constrained = candidate.get("blueprint_concept_id")
    if constrained is not None and constrained != "":
        if constrained in route_ids:
            return _placement(
                candidate, concept_id=constrained,
                basis="blueprint_constraint",
                candidate_routes=route_ids)
        return _placement(
            candidate, concept_id=None, basis="blueprint_constraint",
            flags=["blueprint_concept_not_in_scope"],
            candidate_routes=route_ids)
    if len(concepts) == 1:
        return _placement(
            candidate, concept_id=route_ids[0], basis="sole_candidate",
            candidate_routes=route_ids)
    if not concepts:
        return _placement(
            candidate, concept_id=None, basis="no_candidates",
            flags=["no_candidate_concepts"], candidate_routes=[])

    if router_call is None or critic_call is None:
        from . import generation

        def _default_router(system, user):
            return generation._openai_json(
                system, user, purpose="concept_mapping")

        def _default_critic(system, user):
            return generation._openai_json(
                system, user, purpose="concept_validation")

        router_call = router_call or _default_router
        critic_call = critic_call or _default_critic

    attempts: list[dict] = []
    feedback: list[str] = []
    best: Mapping | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            proposal = router_call(
                ROUTER_SYSTEM, _router_user(candidate, concepts, meta, feedback))
        except Exception as exc:  # noqa: BLE001 — never exception->placement
            attempts.append({"attempt": attempt, "outcome": "router_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        if not isinstance(proposal, Mapping) or (
            proposal.get("concept_id") not in route_ids
        ):
            attempts.append({
                "attempt": attempt, "outcome": "invalid_route",
                "proposed": (
                    proposal.get("concept_id")
                    if isinstance(proposal, Mapping) else None),
            })
            feedback = [
                "the placement must name one candidate concept_id exactly"]
            continue
        best = proposal
        sha = rel.sha256_json(dict(proposal))
        try:
            review = critic_call(
                ROUTE_CRITIC_SYSTEM,
                json.dumps({
                    "proposal": dict(proposal),
                    "proposal_sha256": sha,
                    "question": str(candidate.get("question") or ""),
                    "answer_evidence": routing_answer_evidence(candidate),
                    "candidate_concepts": _concept_payload(concepts),
                }, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "critic_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        review = review if isinstance(review, Mapping) else {}
        if str(review.get("proposal_sha256") or "") != sha:
            attempts.append(
                {"attempt": attempt, "outcome": "critic_unbound"})
            feedback = ["the critic review did not bind to the placement"]
            continue
        if str(review.get("verdict") or "").strip().lower() == "accept":
            return _placement(
                candidate,
                concept_id=proposal.get("concept_id"),
                basis="api_router",
                evidence=str(proposal.get("evidence") or ""),
                reason=str(proposal.get("reason") or ""),
                confidence=str(proposal.get("confidence") or ""),
                candidate_routes=route_ids,
                authority={
                    "proposal_sha256": sha,
                    "critic_reviewed_sha256": sha,
                    "critic_response_sha256": rel.sha256_json(dict(review)),
                    "attempts": attempts + [
                        {"attempt": attempt, "outcome": "accepted"}],
                })
        route_feedback = [
            str(f) for f in review.get("feedback") or [] if str(f).strip()
        ]
        attempts.append({"attempt": attempt, "outcome": "critic_rejected",
                         "feedback": route_feedback})
        feedback = route_feedback or ["rejected without usable feedback"]

    return _placement(
        candidate,
        concept_id=(
            best.get("concept_id") if isinstance(best, Mapping) else None),
        basis="unresolved",
        evidence=str((best or {}).get("evidence") or ""),
        reason=str((best or {}).get("reason") or ""),
        confidence="low",
        flags=["unresolved_routing"] + [
            f"attempt_{entry['attempt']}:{entry['outcome']}"
            for entry in attempts
        ],
        candidate_routes=route_ids,
        authority={"attempts": attempts},
    )


def route_candidates(
    candidates: list[Mapping],
    concepts: list[Mapping],
    *,
    meta: Mapping,
    router_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Route every candidate; exactly one placement each (zero loss)."""
    placements = [
        route_candidate(
            candidate, concepts, meta=meta,
            router_call=router_call, critic_call=critic_call,
            max_attempts=max_attempts)
        for candidate in candidates
    ]
    errors = rel.primary_placement_errors(placements)
    if errors:
        raise RuntimeError(
            "routing produced duplicate primary placements: "
            + "; ".join(errors))
    return {
        "placements": placements,
        "routed": sum(
            1 for p in placements
            if p["concept_id"] is not None
            and "unresolved_routing" not in p["flags"]),
        "flagged": sum(1 for p in placements if p["flags"]),
    }
