"""Recap source questions leave the Post Master by a recorded claim (Q18).

Owner ruling, 21 Aug 2026 (option b): some textbooks open with
prerequisite-recap material — revision of earlier-class learning the
chapter re-activates before its own teaching. Questions that belong to
that material are PRE-LEARNING's territory, so they are removed from the
Post Master as a recorded, reviewable disposition: each rides the release
payload under ``pre_learning_claimed`` with its reason, the release reads
*Ready with flags*, and nothing is ever silently lost (R4 — recorded
exclusion, not loss).

The claim is ONE model verdict per chapter over the complete source-atom
set — critic-advised, Fixer-backed, decide-once — and position is NEVER
evidence (the owner's Sorrieu rule: the RNE chapter opens with the
Frederic Sorrieu print analysis, which is genuine chapter teaching and
must stay Post). The checker is mechanics only: every cited qid exists,
no qid is claimed twice, every claim carries a reason. An empty claim is
a legitimate verdict — most chapters have no recap material.

Two standing rules are explicitly UNCHANGED. The 17-Aug steer stands: a
claimed question is never lifted into any Pre artefact — Output 04 stays
generated-only and its leak barrier still refuses every source qid,
claimed or not. And this pass runs BEFORE the cell verdicts, so a claimed
question costs nothing downstream. The concept-side half of the ruling
(recap sections author no Post concepts; the verdict moves to inventory
time) is Q18 stage 2, recorded in the register with the parallel-tracks
build.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import assessment_lane_policy as lane_policy
from .phase3 import kernel

PRE_CLAIM_POLICY_VERSION = "assessment-pre-claim-1"

PRE_CLAIM_SYSTEM = (
    "You are the Aegis pre-learning claim judge. You are given every "
    "SOURCE question of one textbook chapter (id, source label, kind, and "
    "full text) plus the chapter metadata. Identify the questions that "
    "belong to PREREQUISITE-RECAP material: revision of earlier-class "
    "learning that the chapter re-activates before its own teaching "
    "begins (a 'let us recall' / 'what you already know' exercise, a "
    "revision drill of prior-grade skills). Those are pre-learning's "
    "territory and are claimed OUT of the Post assessment.\n"
    "POSITION IS NEVER EVIDENCE: a question is recap because of what it "
    "asks, not because it appears early. A chapter opener that teaches "
    "THIS chapter's own content — a source analysis, an introduction "
    "that develops the chapter's ideas — is chapter teaching and is NOT "
    "claimed. When you cannot tell from the text, do not claim. An empty "
    "claimed array is a legitimate verdict and most chapters have no "
    "recap material; there is no quota and claiming is never a goal.\n"
    "Respond with a single JSON object and nothing else:\n"
    '{"claimed":[{"source_qid":"","reason":"why this question is '
    'prerequisite recap, grounded in its own text"}],'
    '"confidence":0.0,"rationale":"evidence-bound reason"}'
)

PRE_CLAIM_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis pre-learning "
    "claim decision. Audit the claimed set against the actual question "
    "texts: is any claimed question genuinely THIS chapter's own teaching "
    "mislabelled as recap (the chapter-opener trap), and is any obvious "
    "earlier-class revision drill missed? Position in the chapter is "
    "never evidence either way. Dissent must name the source_qid(s). "
    "Respond with a single JSON object: "
    '{"verdict":"concur|dissent","confidence":0.0,"issues":["..."]}'
)


class PreClaimDecisionError(ValueError):
    """A pre-learning claim verdict violated its mechanical contract."""


def _live_claim(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        PRE_CLAIM_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_claim_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        PRE_CLAIM_CRITIC_SYSTEM, prompts.render(payload),
        purpose="advisory_critic",
    )


def _claim_checker(source_qids: set[str]) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        claimed = response.get("claimed")
        if not isinstance(claimed, list):
            return ["claimed must be an array"]
        defects: list[str] = []
        seen: set[str] = set()
        for position, row in enumerate(claimed, start=1):
            if not isinstance(row, Mapping):
                defects.append(f"claimed entry {position} is not an object")
                continue
            qid = str(row.get("source_qid") or "")
            if qid not in source_qids:
                defects.append(
                    f"claimed entry {position}: {qid!r} is not one of "
                    "this chapter's source questions"
                )
            if qid in seen:
                defects.append(
                    f"claimed entry {position}: {qid!r} is claimed twice"
                )
            seen.add(qid)
            if not str(row.get("reason") or "").strip():
                defects.append(
                    f"claimed entry {position}: {qid!r} carries no reason"
                )
        return defects

    return check


def decide_pre_learning_claims(
    atoms: list[Mapping],
    *,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> tuple[list[Mapping], list[dict[str, Any]]]:
    """One recorded per-chapter claim verdict over the source atoms.

    Returns ``(kept_atoms, claimed_records)`` — kept atoms in input
    order, each claimed record carrying the atom, the reason, and the
    decision flags. An empty atom set costs nothing and claims nothing.
    """

    rows = [atom for atom in atoms if isinstance(atom, Mapping)]
    by_qid = {
        str(atom.get("source_qid") or ""): atom
        for atom in rows
        if str(atom.get("source_qid") or "")
    }
    if not by_qid:
        return list(atoms), []

    if provider is None:
        provider = _live_claim
        if critic is None:
            critic = lane_policy.critic_for("pre_claim", _live_claim_critic)
    store = store or kernel.DecisionStore()

    payload = {
        "stage": "assessment.pre_learning_claim",
        "rules": PRE_CLAIM_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "source_questions": [
            {
                "source_qid": str(atom.get("source_qid") or ""),
                "source_label": str(atom.get("source_paper_number") or ""),
                "source_kind": str(atom.get("source_kind") or ""),
                "text": str(atom.get("normalized_public_text") or ""),
            }
            for atom in rows
            if str(atom.get("source_qid") or "")
        ],
    }
    decision = kernel.decide(
        kind="assessment.pre_learning_claim",
        unit_id="chapter",
        envelope_sha256=envelope_sha256,
        payload=payload,
        provider=provider,
        checker=_claim_checker(set(by_qid)),
        critic=critic,
        store=store,
        policy_version=PRE_CLAIM_POLICY_VERSION,
        fixer=fixer,
    )
    response = decision["response"]
    decision_flags = list(decision.get("review_flags") or [])

    claimed_records: list[dict[str, Any]] = []
    claimed_qids: set[str] = set()
    for row in response.get("claimed") or []:
        qid = str(row.get("source_qid") or "")
        claimed_qids.add(qid)
        claimed_records.append({
            "source_qid": qid,
            "reason": str(row.get("reason") or ""),
            "source_atom": copy.deepcopy(dict(by_qid.get(qid) or {})),
            "flags": [
                "source question claimed by pre-learning (Q18): "
                + str(row.get("reason") or "")
            ] + decision_flags,
        })
    kept = [
        atom for atom in atoms
        if (
            str(atom.get("source_qid") or "")
            if isinstance(atom, Mapping) else ""
        ) not in claimed_qids
    ]
    return kept, claimed_records
