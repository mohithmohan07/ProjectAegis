"""Semantic groups: tiers, variant clustering, identity, and descriptions
(MES spec §6 Stages 8–10, §7 Tag New / Rewrite Full, §8 group contract).

Mechanical here: the difficulty→tier map, machine Group IDs, tier
sequences, required empty shells, exact-partition validation, fingerprints,
ordered label aggregates, and change detection for Tag New. Semantic and
therefore model work with an independent critic: which questions form one
variant family, and every occupied group's HOW + WHAT description.

Fail-closed (spec §14): an unresolved clustering ships every candidate as
its own single-member family, flagged — singleton clusters assert no
variant relationship, so no semantic merge is ever made deterministically;
an unresolved description ships flagged, never a generic count summary.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping

from . import assessment_release as rel

MAX_ATTEMPTS = 3

# Stage 8 — the accepted difficulty maps mechanically onto a tier.
DIFFICULTY_TO_TIER = {"Less": "Basic", "Moderate": "Intermediate",
                      "High": "Advanced"}
TIER_CODES = {"Basic": "BG", "Intermediate": "IG", "Advanced": "AG"}

CLUSTER_SYSTEM = (
    "You are the Aegis variant-clustering author. Partition the supplied "
    "assessment questions (all sharing one concept and one difficulty "
    "tier) into semantic variant families. One family means: the same "
    "atomic assessed idea, the same questioning intent, materially "
    "equivalent givens, the same expected response and answer space, the "
    "same solution or rubric shape, and compatible visual dependence and "
    "scaffolding. Paraphrases and value variants share a family. Keep "
    "separate: definition vs function, identification vs explanation, "
    "comparison vs application, recall vs multistep reasoning, visual vs "
    "text-only, single-part vs broad multipart, and different expected-"
    "answer signatures.\n"
    "When existing groups are supplied, a question that genuinely belongs "
    "to one of them joins it (return its group_key); otherwise open a new "
    "family. Never force unrelated assessments into one family and never "
    "split a true variant pair to balance sizes.\n"
    "Every candidate_id must appear in exactly one family.\n"
    "Return ONLY strict JSON:\n"
    '{"families":[{"existing_group_key":"","family":"short name",'
    '"member_candidate_ids":[""]}]}'
)

CLUSTER_CRITIC_SYSTEM = (
    "You are the independent Aegis clustering critic. You are READ-ONLY. "
    "Verify the proposed variant families against the questions: reject "
    "families that mix different assessed ideas, intents, response "
    "shapes, or visual dependence, and reject splits that separate true "
    "paraphrase/value variants.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"accept","proposal_sha256":"","feedback":[]} or '
    '{"verdict":"reject","proposal_sha256":"","feedback":["evidence-bound '
    'reason ..."]}\n'
    "proposal_sha256 MUST echo the exact hash you were given."
)

DESCRIBE_SYSTEM = (
    "You are the Aegis group-description author. Write ONE concise "
    "description of this assessment group from ALL of its member "
    "questions. It must state HOW the learner is assessed and WHAT "
    "capability is assessed — for example 'Visual classification of "
    "everyday objects as two- or three-dimensional shapes' or 'Solving a "
    "pair of linear equations using substitution and showing intermediate "
    "working'.\n"
    "Never write a label list, a concept-description copy, a membership "
    "history, a count summary ('3 questions covering Remember'), or "
    "generic difficulty prose ('Basic assessments for Shapes').\n"
    'Return ONLY strict JSON: {"description":""}'
)

DESCRIBE_CRITIC_SYSTEM = (
    "You are the independent Aegis group-description critic. You are "
    "READ-ONLY. Verify the description against the member questions: it "
    "must state HOW and WHAT is assessed, be true of every member, and "
    "contain no label lists, counts, membership history, or generic "
    "difficulty prose.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"accept","proposal_sha256":"","feedback":[]} or '
    '{"verdict":"reject","proposal_sha256":"","feedback":["evidence-bound '
    'reason ..."]}\n'
    "proposal_sha256 MUST echo the exact hash you were given."
)


class GroupingError(ValueError):
    """A grouping invariant that must never be violated was violated."""


# --------------------------------------------------------------------------- #
# Mechanical identity (spec §8.1, §8.2)
# --------------------------------------------------------------------------- #

def tier_for_difficulty(difficulty: str) -> str:
    tier = DIFFICULTY_TO_TIER.get(str(difficulty or "").strip())
    if tier is None:
        raise GroupingError(
            f"unknown difficulty {difficulty!r}; the accepted value maps "
            "mechanically and is never guessed")
    return tier


def group_key_for(concept_machine_id: str, tier: str, sequence: int) -> str:
    code = TIER_CODES[tier]
    return f"({concept_machine_id}) {code}{int(sequence):02d}"


def group_record(
    *,
    concept_id,
    concept_machine_id: str,
    tier: str,
    sequence: int,
    member_candidate_ids: list[str],
    family: str = "",
    description: str = "",
    flags: list[str] | None = None,
    authority: Mapping | None = None,
) -> dict:
    """AssessmentGroup record (spec §5.5) under the §8.2 naming contract:
    group_name = group_display_name = the exact machine Group ID."""
    key = group_key_for(concept_machine_id, tier, sequence)
    return {
        "group_key": key,
        "concept_id": concept_id,
        "group_type": tier,
        "group_sequence": int(sequence),
        "group_name": key,
        "group_display_name": key,
        "family": family,
        "semantic_fingerprint": rel.sha256_json(
            sorted(member_candidate_ids)),
        "semantic_description": description,
        "member_candidate_ids": list(member_candidate_ids),
        "group_status": "Active",
        "flags": list(flags or []),
        "authority": dict(authority or {}),
    }


def required_shells(concept_id, concept_machine_id: str) -> list[dict]:
    """The BG01/IG01/AG01 shells every concept carries (spec §8.1, §8.3)."""
    return [
        group_record(
            concept_id=concept_id,
            concept_machine_id=concept_machine_id,
            tier=tier, sequence=1,
            member_candidate_ids=[],
            description="NA",
        )
        for tier in ("Basic", "Intermediate", "Advanced")
    ]


# --------------------------------------------------------------------------- #
# Stage 9 — semantic variant clustering
# --------------------------------------------------------------------------- #

def _partition_defects(
    families: list[Mapping], candidate_ids: list[str],
    existing_keys: set[str],
) -> list[str]:
    """Exact disjoint partition over all candidate ids (mechanical)."""
    defects: list[str] = []
    seen: list[str] = []
    for family in families:
        if not isinstance(family, Mapping):
            defects.append("family is not an object")
            continue
        existing = str(family.get("existing_group_key") or "")
        if existing and existing not in existing_keys:
            defects.append(f"unknown existing_group_key {existing!r}")
        members = [
            str(m) for m in family.get("member_candidate_ids") or []
            if str(m).strip()
        ]
        if not members:
            defects.append("family with no members")
        seen.extend(members)
    report = rel.zero_loss_report(candidate_ids, seen, [])
    if report["missing"]:
        defects.append(f"unclustered candidates: {report['missing']}")
    if report["unexpected"]:
        defects.append(f"unknown candidates: {report['unexpected']}")
    if len(seen) != len(set(seen)):
        defects.append("a candidate appears in more than one family")
    return defects


def _member_payload(candidates: list[Mapping]) -> list[dict]:
    from . import assessment_routing

    return [
        {
            "candidate_id": str(c.get("candidate_id") or ""),
            "question": str(c.get("question") or ""),
            "answer_evidence": assessment_routing.routing_answer_evidence(c),
            "requires_visual": bool(c.get("requires_visual")),
            "sub_question_count": len(c.get("sub_questions") or []),
        }
        for c in candidates
    ]


def cluster_tier(
    candidates: list[Mapping],
    *,
    concept_title: str,
    tier: str,
    existing_groups: list[Mapping] | None = None,
    meta: Mapping,
    author_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Partition one concept+tier's candidates into variant families.

    Returns {"families": [...], "flags": [...], "authority": {...}}. A
    family is {"existing_group_key": "", "family": name, "member_candidate_
    ids": [...]}. Unresolved clustering ships singleton families, flagged:
    singletons assert no variant relationship, so nothing semantic was
    decided deterministically.
    """
    candidate_ids = [str(c.get("candidate_id") or "") for c in candidates]
    if not candidates:
        return {"families": [], "flags": [], "authority": {}}
    if len(candidates) == 1:
        # One question is one family; there is nothing to decide.
        return {
            "families": [{
                "existing_group_key": "",
                "family": "",
                "member_candidate_ids": candidate_ids,
            }],
            "flags": [],
            "authority": {"basis": "singleton"},
        }

    if author_call is None or critic_call is None:
        from . import generation

        def _default_author(system, user):
            return generation._openai_json(
                system, user, purpose="concept_mapping")

        def _default_critic(system, user):
            return generation._openai_json(
                system, user, purpose="concept_validation")

        author_call = author_call or _default_author
        critic_call = critic_call or _default_critic

    existing = list(existing_groups or [])
    existing_keys = {str(g.get("group_key") or "") for g in existing}
    payload = {
        "metadata": dict(meta),
        "concept_title": concept_title,
        "tier": tier,
        "candidates": _member_payload(candidates),
        "existing_groups": [
            {
                "group_key": str(g.get("group_key") or ""),
                "semantic_description": str(
                    g.get("semantic_description") or ""),
            }
            for g in existing
        ],
    }
    attempts: list[dict] = []
    feedback: list[str] = []
    for attempt in range(1, max(1, max_attempts) + 1):
        user = json.dumps(payload, ensure_ascii=False)
        if feedback:
            user += (
                "\n\nYOUR PREVIOUS PARTITION WAS REJECTED. Address every "
                "item, then return a fresh complete partition:\n- "
                + "\n- ".join(feedback)
            )
        try:
            proposal = author_call(CLUSTER_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "author_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        families = (
            list(proposal.get("families") or [])
            if isinstance(proposal, Mapping) else [])
        defects = _partition_defects(families, candidate_ids, existing_keys)
        if defects:
            attempts.append({"attempt": attempt, "outcome": "mechanical",
                             "defects": defects})
            feedback = defects
            continue
        sha = rel.sha256_json(families)
        try:
            review = critic_call(
                CLUSTER_CRITIC_SYSTEM,
                json.dumps({
                    "proposal": families,
                    "proposal_sha256": sha,
                    "candidates": _member_payload(candidates),
                }, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "critic_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        review = review if isinstance(review, Mapping) else {}
        if str(review.get("proposal_sha256") or "") != sha:
            attempts.append({"attempt": attempt, "outcome": "critic_unbound"})
            feedback = ["the critic review did not bind to the partition"]
            continue
        if str(review.get("verdict") or "").strip().lower() == "accept":
            return {
                "families": [
                    {
                        "existing_group_key": str(
                            f.get("existing_group_key") or ""),
                        "family": str(f.get("family") or ""),
                        "member_candidate_ids": [
                            str(m) for m in f["member_candidate_ids"]],
                    }
                    for f in families
                ],
                "flags": [],
                "authority": {
                    "proposal_sha256": sha,
                    "critic_response_sha256": rel.sha256_json(dict(review)),
                    "attempts": attempts + [
                        {"attempt": attempt, "outcome": "accepted"}],
                },
            }
        route_feedback = [
            str(f) for f in review.get("feedback") or [] if str(f).strip()]
        attempts.append({"attempt": attempt, "outcome": "critic_rejected",
                         "feedback": route_feedback})
        feedback = route_feedback or ["rejected without usable feedback"]

    # Unresolved: singleton families assert no variant relationship — no
    # semantic merge was made deterministically — and every family is
    # flagged for review.
    return {
        "families": [
            {"existing_group_key": "", "family": "",
             "member_candidate_ids": [cid]}
            for cid in candidate_ids
        ],
        "flags": ["unresolved_clustering"],
        "authority": {"attempts": attempts},
    }


# --------------------------------------------------------------------------- #
# Stage 10 — group descriptions
# --------------------------------------------------------------------------- #

def describe_group(
    group: Mapping,
    members: list[Mapping],
    *,
    meta: Mapping,
    author_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """One occupied group -> {"description": ..., "flags": [...]}.

    Empty required shells stay 'NA' (spec §8.3) — mechanical, no call.
    """
    if not members:
        return {"description": "NA", "flags": [], "authority": {}}

    if author_call is None or critic_call is None:
        from . import generation

        def _default_author(system, user):
            return generation._openai_json(
                system, user, purpose="concept_detailing")

        def _default_critic(system, user):
            return generation._openai_json(
                system, user, purpose="concept_validation")

        author_call = author_call or _default_author
        critic_call = critic_call or _default_critic

    payload = {
        "metadata": dict(meta),
        "group_key": str(group.get("group_key") or ""),
        "tier": str(group.get("group_type") or ""),
        "members": _member_payload(members),
    }
    attempts: list[dict] = []
    feedback: list[str] = []
    best = ""
    for attempt in range(1, max(1, max_attempts) + 1):
        user = json.dumps(payload, ensure_ascii=False)
        if feedback:
            user += (
                "\n\nYOUR PREVIOUS DESCRIPTION WAS REJECTED. Address every "
                "item, then return a fresh description:\n- "
                + "\n- ".join(feedback)
            )
        try:
            proposal = author_call(DESCRIBE_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "author_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        description = (
            str(proposal.get("description") or "").strip()
            if isinstance(proposal, Mapping) else "")
        if not description:
            attempts.append(
                {"attempt": attempt, "outcome": "empty_description"})
            feedback = ["the description was empty"]
            continue
        best = description
        sha = rel.sha256_json({"description": description})
        try:
            review = critic_call(
                DESCRIBE_CRITIC_SYSTEM,
                json.dumps({
                    "proposal": {"description": description},
                    "proposal_sha256": sha,
                    "members": _member_payload(members),
                }, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "critic_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        review = review if isinstance(review, Mapping) else {}
        if str(review.get("proposal_sha256") or "") != sha:
            attempts.append({"attempt": attempt, "outcome": "critic_unbound"})
            feedback = ["the critic review did not bind to the description"]
            continue
        if str(review.get("verdict") or "").strip().lower() == "accept":
            return {
                "description": description,
                "flags": [],
                "authority": {
                    "proposal_sha256": sha,
                    "critic_response_sha256": rel.sha256_json(dict(review)),
                    "attempts": attempts + [
                        {"attempt": attempt, "outcome": "accepted"}],
                },
            }
        route_feedback = [
            str(f) for f in review.get("feedback") or [] if str(f).strip()]
        attempts.append({"attempt": attempt, "outcome": "critic_rejected",
                         "feedback": route_feedback})
        feedback = route_feedback or ["rejected without usable feedback"]

    return {
        "description": best,
        "flags": ["unresolved_description"],
        "authority": {"attempts": attempts},
    }


def description_is_stale(group: Mapping, member_candidate_ids: list[str]) -> bool:
    """Membership changed -> the description must be REPLACED (spec §8.3)."""
    return group.get("semantic_fingerprint") != rel.sha256_json(
        sorted(str(m) for m in member_candidate_ids))


# --------------------------------------------------------------------------- #
# Aggregated labels (spec §8.4)
# --------------------------------------------------------------------------- #

def label_aggregates(
    placements: list[Mapping],
    labels_by_candidate: Mapping[str, str],
) -> dict:
    """Complete ordered label aggregates per concept and per group."""
    by_concept: dict = {}
    by_group: dict = {}
    for placement in placements:
        label = labels_by_candidate.get(
            str(placement.get("candidate_id") or ""))
        if not label:
            continue
        concept_id = placement.get("concept_id")
        group_key = str(placement.get("group_key") or "")
        if concept_id is not None:
            by_concept.setdefault(concept_id, []).append(label)
        if group_key:
            by_group.setdefault(group_key, []).append(label)
    return {"concept_question_labels": by_concept,
            "group_question_labels": by_group}


# --------------------------------------------------------------------------- #
# §7 — Tag New / Rewrite Full planning
# --------------------------------------------------------------------------- #

def candidate_content_sha256(candidate: Mapping) -> str:
    """Accepted-content identity for change detection (spec §7.1)."""
    return rel.sha256_json({
        "question": candidate.get("question"),
        "answers": candidate.get("answers"),
        "sub_questions": candidate.get("sub_questions"),
        "display_answer": candidate.get("display_answer"),
        "marks": candidate.get("marks"),
    })


def plan_tagging(
    mode: str,
    candidates: list[Mapping],
    existing: Mapping[str, Mapping] | None = None,
) -> dict:
    """Which candidates need semantic work, by mode. Idempotent.

    ``existing`` maps question label -> {"content_sha256", "concept_id",
    "group_key"} from the previously accepted release.

    - rewrite_full: every candidate is rebuilt; nothing published is
      cleared — the output is a new versioned draft.
    - tag_new: unchanged accepted questions keep their placement and are
      skipped; new or changed ones are routed and clustered; every group
      they touch is re-evaluated and its description replaced.
    """
    if mode not in {"rewrite_full", "tag_new"}:
        raise GroupingError("mode must be rewrite_full | tag_new")
    existing = existing or {}
    if mode == "rewrite_full":
        return {
            "mode": mode,
            "process": [
                str(c.get("candidate_id") or "") for c in candidates],
            "skip": [],
            "kept_placements": {},
            "touched_group_keys": [],
        }
    process: list[str] = []
    skip: list[str] = []
    kept: dict[str, dict] = {}
    touched: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        label = str(candidate.get("question_label") or "")
        prior = existing.get(label) if label else None
        if (
            prior is not None
            and str(prior.get("content_sha256") or "")
            == candidate_content_sha256(candidate)
        ):
            skip.append(candidate_id)
            kept[candidate_id] = {
                "concept_id": prior.get("concept_id"),
                "group_key": str(prior.get("group_key") or ""),
            }
            continue
        process.append(candidate_id)
        if prior is not None and prior.get("group_key"):
            # A changed question touches the group it used to live in.
            touched.add(str(prior.get("group_key")))
    return {
        "mode": mode,
        "process": process,
        "skip": skip,
        "kept_placements": kept,
        "touched_group_keys": sorted(touched),
    }
