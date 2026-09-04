"""Semantic assessment groups (MES §6 Stages 8–10, §8 contract).

Level, variant-family, and occupied-group-description authorship all use the
Phase-3 decision kernel: one cached model verdict, a mechanics-only checker,
an independent advisory critic, and The Fixer for a structurally blocked
decision.  No difficulty label, question count, or local content rule is an
authority for those judgments.

Internal group IDs, friendly-name projection, tier sequences, required empty
shells, exact-partition validation, fingerprints, ordered label aggregates,
and Tag-New change detection remain mechanical.  Empty required shells keep
their explicit ``NA`` description without spending a model call.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from .. import config
from . import assessment_lane_policy as lane_policy
from . import assessment_release as rel
from . import identity
from .phase3 import kernel

TIER_CODES = identity.GROUP_TIER_CODES

LEVEL_POLICY_VERSION = "assessment-level-1"
VARIANT_CLUSTER_POLICY_VERSION = "assessment-variant-cluster-1"
GROUP_DESCRIPTION_POLICY_VERSION = "assessment-group-description-1"

LEVEL_SYSTEM = (
    "You are the Aegis assessment-level author. Decide whether this one "
    "question belongs in Basic, Intermediate, or Advanced by reading the "
    "complete question, expected answer and rubric, source and routing "
    "evidence, assets, and the home concept's teaching description. The "
    "blueprint difficulty label is deliberately absent and has no authority "
    "over this verdict. Judge the capability and construction actually "
    "required by the question. There is no quota for the three tiers; never "
    "balance, spread, or infer a tier from how many questions exist.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","tier":"Basic|Intermediate|Advanced",'
    '"rationale":"evidence-bound reason"}'
)

LEVEL_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one assessment-level "
    "verdict. Audit the proposed tier against the complete question, answer "
    "and rubric, source and route evidence, assets, and home-concept "
    "description. Do not substitute a blueprint difficulty label, balance "
    "tiers, or revise the verdict. There is no quota. Your dissent is "
    "advisory: the proposed verdict stands and your concerns ship for "
    "review. State your honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)

CLUSTER_SYSTEM = (
    "You are the Aegis variant-clustering author. Partition the supplied "
    "assessment questions (all sharing one concept and one authored tier) "
    "into semantic variant families. One family means: the same "
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
    "split a true variant pair to balance sizes. There is no quota for the "
    "number or size of families.\n"
    "Every candidate_id must appear in exactly one family.\n"
    "Return ONLY strict JSON:\n"
    '{"concept_key":"","tier":"Basic|Intermediate|Advanced",'
    '"families":[{"existing_group_key":"","family":"short name",'
    '"member_candidate_ids":[""]}],"rationale":"evidence-bound reason"}'
)

CLUSTER_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis variant-family "
    "verdict. Audit the proposed partition against the complete questions, "
    "answers and rubrics, source and route evidence, assets, and the home "
    "concept. Flag families that mix different assessed ideas, intents, "
    "response shapes, or visual dependence, and flag splits that separate "
    "true paraphrase or value variants. There is no quota. Do not revise or "
    "gate the partition; dissent ships as review evidence. State your honest "
    "confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
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
    "history, a question-count summary, or "
    "generic difficulty prose ('Basic assessments for Shapes').\n"
    "Return ONLY strict JSON:\n"
    '{"group_key":"","description":"",'
    '"rationale":"evidence-bound reason"}'
)

DESCRIBE_CRITIC_SYSTEM = (
    "You are the independent advisory Aegis group-description critic. "
    "Audit the description against the complete member questions: it "
    "must state HOW and WHAT is assessed, be true of every member, and "
    "contain no label lists, counts, membership history, or generic "
    "difficulty prose. Do not revise or gate the description; dissent ships "
    "as review evidence. State your honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)


class GroupingError(ValueError):
    """A grouping invariant that must never be violated was violated."""


# --------------------------------------------------------------------------- #
# Mechanical identity (spec §8.1, §8.2)
# --------------------------------------------------------------------------- #


def group_key_for(concept_machine_id: str, tier: str, sequence: int) -> str:
    """The SOP §3.2 group ID: ``(<ConceptID>) <BG|IG|AG>##``.

    Since the owner's 2026-08-21 nomenclature ruling (Q16, superseding
    Q12's friendly names) this single composition ALSO supplies the
    visible ``group_name`` / ``group_display_name`` — SOP §6.1 says both
    carry the group ID itself. One composer, three columns.
    """
    if tier not in TIER_CODES:
        raise GroupingError(f"unknown group tier {tier!r}")
    return identity.compose_group_key(concept_machine_id, tier, sequence)


def group_record(
    *,
    concept_id,
    concept_machine_id: str,
    concept_name: str,
    tier: str,
    sequence: int,
    member_candidate_ids: list[str],
    family: str = "",
    description: str = "",
    flags: list[str] | None = None,
    authority: Mapping | None = None,
) -> dict:
    """AssessmentGroup record (spec §5.5) under the SOP §6.1 naming
    contract (Q16): ``group_name`` and ``group_display_name`` both carry
    the group ID itself — the same value as ``group_key``. ``concept_name``
    stays in the signature as call-site evidence of the group's home; it
    no longer feeds the visible name."""
    key = group_key_for(concept_machine_id, tier, sequence)
    visible_name = key
    return {
        "group_key": key,
        "concept_id": concept_id,
        "group_type": tier,
        "group_sequence": int(sequence),
        "group_name": visible_name,
        "group_display_name": visible_name,
        "family": family,
        "semantic_fingerprint": rel.sha256_json(
            sorted(member_candidate_ids)),
        "semantic_description": description,
        "member_candidate_ids": list(member_candidate_ids),
        "group_status": "Active",
        "flags": list(flags or []),
        "authority": dict(authority or {}),
    }


def required_shells(
    concept_id, concept_machine_id: str, concept_name: str,
) -> list[dict]:
    """The BG01/IG01/AG01 shells every concept carries (spec §8.1, §8.3)."""
    return [
        group_record(
            concept_id=concept_id,
            concept_machine_id=concept_machine_id,
            concept_name=concept_name,
            tier=tier, sequence=1,
            member_candidate_ids=[],
            description="NA",
        )
        for tier in ("Basic", "Intermediate", "Advanced")
    ]


# --------------------------------------------------------------------------- #
# Shared semantic evidence and recorded-decision mechanics
# --------------------------------------------------------------------------- #

def _member_payload(candidates: list[Mapping]) -> list[dict[str, Any]]:
    """Project complete semantic evidence without prior level authority.

    Both rich and plain question forms, every answer/rubric/subquestion,
    source and route provenance, and every supplied asset ride intact.  The
    blueprint's difficulty and cognitive labels are deliberately absent:
    neither is evidence for a fresh level or family verdict.  No text is
    truncated and no volume-derived summary is manufactured.
    """

    return [
        {
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "question": copy.deepcopy(candidate.get("question")),
            "question_text": copy.deepcopy(candidate.get("question_text")),
            "question_display": copy.deepcopy(
                candidate.get("question_display")),
            "sheet_kind": copy.deepcopy(candidate.get("sheet_kind")),
            "question_category": copy.deepcopy(
                candidate.get("question_category")),
            "marks": copy.deepcopy(candidate.get("marks")),
            "answer_restriction": copy.deepcopy(
                candidate.get("answer_restriction")),
            "restriction_reason": copy.deepcopy(
                candidate.get("restriction_reason")),
            "display_answer": copy.deepcopy(
                candidate.get("display_answer")),
            "answers": copy.deepcopy(candidate.get("answers")),
            "rubric": copy.deepcopy(candidate.get("rubric")),
            "marking_scheme": copy.deepcopy(
                candidate.get("marking_scheme")),
            "sub_questions": copy.deepcopy(candidate.get("sub_questions")),
            "answer_explanation": copy.deepcopy(
                candidate.get("answer_explanation")),
            "requires_visual": bool(candidate.get("requires_visual")),
            "assets": copy.deepcopy(candidate.get("assets")),
            "image_manifest": copy.deepcopy(
                candidate.get("image_manifest")),
            "tables": copy.deepcopy(candidate.get("tables")),
            "source_atom_ids": copy.deepcopy(
                candidate.get("source_atom_ids")),
            "source_qid": copy.deepcopy(candidate.get("source_qid")),
            "source_document_hash": copy.deepcopy(
                candidate.get("source_document_hash")),
            "source_kind": copy.deepcopy(candidate.get("source_kind")),
            "source_evidence": copy.deepcopy(
                candidate.get("source_evidence")),
            "shared_context": copy.deepcopy(
                candidate.get("shared_context")),
            "source_context": copy.deepcopy(
                candidate.get("source_context")),
            "raw_text": copy.deepcopy(candidate.get("raw_text")),
            "normalized_public_text": copy.deepcopy(
                candidate.get("normalized_public_text")),
            "options": copy.deepcopy(candidate.get("options")),
            "answer_evidence": copy.deepcopy(
                candidate.get("answer_evidence")),
            "route_evidence": copy.deepcopy(
                candidate.get("route_evidence")),
            "image_urls": copy.deepcopy(candidate.get("image_urls")),
            "content_objects": copy.deepcopy(
                candidate.get("content_objects")),
            "blueprint_cell_id": copy.deepcopy(
                candidate.get("blueprint_cell_id")),
            "assessment_gist": copy.deepcopy(
                candidate.get("assessment_gist")),
        }
        for candidate in candidates
    ]


def _concept_payload(concept: Mapping) -> dict[str, Any]:
    """Carry explicit concept identity, display text, and full descriptions."""

    return {
        "concept_key": copy.deepcopy(concept.get("concept_key")),
        "concept_id": copy.deepcopy(concept.get("concept_id")),
        "concept_machine_id": copy.deepcopy(
            concept.get("concept_machine_id")),
        "concept_display_name": copy.deepcopy(
            concept.get("concept_display_name")),
        "concept_title": copy.deepcopy(concept.get("concept_title")),
        "description": copy.deepcopy(concept.get("description")),
        "teaching_description": copy.deepcopy(
            concept.get("teaching_description")),
        "concept_description": copy.deepcopy(
            concept.get("concept_description")),
        "concept_details": copy.deepcopy(concept.get("concept_details")),
    }


def _envelope_hash(value: str) -> str:
    envelope_sha = str(value or "").strip()
    if not envelope_sha:
        raise GroupingError("assessment grouping requires an envelope hash")
    return envelope_sha


def _candidate_ids(candidates: list[Mapping], *, stage: str) -> list[str]:
    ids: list[str] = []
    for position, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, Mapping):
            raise GroupingError(
                f"{stage} candidate {position} is not an object")
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise GroupingError(
                f"{stage} candidate {position} has no candidate_id")
        ids.append(candidate_id)
    if len(ids) != len(set(ids)):
        raise GroupingError(f"{stage} candidates repeat a candidate_id")
    return ids


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag) for flag in decision.get("review_flags") or []
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


def _live_level(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        LEVEL_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_level_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        LEVEL_CRITIC_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="advisory_critic",
    )


def _live_cluster(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        CLUSTER_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_cluster_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        CLUSTER_CRITIC_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="advisory_critic",
    )


def _live_description(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        DESCRIBE_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="concept_detailing",
    )


def _live_description_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        DESCRIBE_CRITIC_SYSTEM, json.dumps(payload, ensure_ascii=False),
        purpose="advisory_critic",
    )


def _live_authorities(
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    fixer: kernel.Provider | None,
    *,
    live_provider: kernel.Provider,
    live_critic: kernel.Critic,
    stage: str,
) -> tuple[kernel.Provider, kernel.Critic | None, kernel.Provider | None]:
    """Wire production authorities while preserving injected test seams."""

    if provider is not None:
        return provider, critic, fixer
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod

    envelope_mod.require_live_api()
    return (
        live_provider,
        critic or lane_policy.critic_for(stage, live_critic),
        fixer or fixer_mod.live_fixer,
    )


# --------------------------------------------------------------------------- #
# Stage 8 — model-authored level verdicts
# --------------------------------------------------------------------------- #

def _level_checker(candidate_id: str) -> kernel.Checker:
    """Mechanics only: bind identity, enum, and required response fields."""

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("candidate_id") or "") != candidate_id:
            defects.append(f"candidate_id must echo {candidate_id!r}")
        if str(response.get("tier") or "").strip() not in TIER_CODES:
            defects.append(
                "tier must be Basic, Intermediate, or Advanced")
        if not str(response.get("rationale") or "").strip():
            defects.append("response has no rationale")
        return defects

    return check


def decide_levels(
    units: list[Mapping],
    *,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> list[dict[str, Any]]:
    """Author one recorded tier verdict per candidate, in input order."""

    envelope_sha = _envelope_hash(envelope_sha256)
    prepared: list[tuple[Mapping, Mapping, str]] = []
    seen: set[str] = set()
    for position, unit in enumerate(units, start=1):
        if not isinstance(unit, Mapping):
            raise GroupingError(f"level unit {position} is not an object")
        candidate = unit.get("candidate")
        concept = unit.get("concept")
        if not isinstance(candidate, Mapping):
            raise GroupingError(
                f"level unit {position} has no candidate object")
        if not isinstance(concept, Mapping):
            raise GroupingError(
                f"level unit {position} has no concept object")
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise GroupingError(
                f"level unit {position} candidate has no candidate_id")
        if candidate_id in seen:
            raise GroupingError(
                f"level units repeat candidate_id {candidate_id!r}")
        seen.add(candidate_id)
        prepared.append((candidate, concept, candidate_id))
    if not prepared:
        return []

    provider, critic, fixer = _live_authorities(
        provider, critic, fixer,
        live_provider=_live_level,
        live_critic=_live_level_critic,
        stage="level",
    )
    store = store or kernel.DecisionStore()

    def decide_one(unit: tuple[Mapping, Mapping, str]) -> dict[str, Any]:
        candidate, concept, candidate_id = unit
        payload = {
            "stage": "assessment.level",
            "rules": LEVEL_SYSTEM,
            "metadata": copy.deepcopy(dict(meta)),
            "candidate": _member_payload([candidate])[0],
            "concept": _concept_payload(concept),
        }
        decision = kernel.decide(
            kind="assessment.level",
            unit_id=candidate_id,
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_level_checker(candidate_id),
            critic=critic,
            store=store,
            policy_version=LEVEL_POLICY_VERSION,
            fixer=fixer,
        )
        response = copy.deepcopy(dict(decision["response"]))
        flags = _review_flags(decision)
        return {
            "candidate_id": candidate_id,
            "tier": str(response.get("tier") or ""),
            "rationale": str(response.get("rationale") or ""),
            "decision": response,
            "flags": flags,
            "authority": _decision_authority(decision),
        }

    return kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
    )


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
    used_existing: list[str] = []
    for position, family in enumerate(families, start=1):
        if not isinstance(family, Mapping):
            defects.append(f"family {position} is not an object")
            continue
        existing = str(family.get("existing_group_key") or "")
        if existing and existing not in existing_keys:
            defects.append(f"unknown existing_group_key {existing!r}")
        if existing:
            used_existing.append(existing)
        if "family" not in family or not isinstance(family.get("family"), str):
            defects.append(f"family {position} has no string family name")
        raw_members = family.get("member_candidate_ids")
        if not isinstance(raw_members, list):
            defects.append(
                f"family {position} has no member_candidate_ids array")
            continue
        members = [str(member) for member in raw_members]
        if not members or any(not member.strip() for member in members):
            defects.append(f"family {position} has an empty member identity")
        seen.extend(members)
    report = rel.zero_loss_report(candidate_ids, seen, [])
    if report["missing"]:
        defects.append(f"unclustered candidates: {report['missing']}")
    if report["unexpected"]:
        defects.append(f"unknown candidates: {report['unexpected']}")
    if len(seen) != len(set(seen)):
        defects.append("a candidate appears in more than one family")
    if len(used_existing) != len(set(used_existing)):
        defects.append("an existing_group_key appears in more than one family")
    return defects


def _cluster_checker(
    candidate_ids: list[str], existing_keys: set[str],
    concept_key: str, tier: str,
) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("concept_key") or "") != concept_key:
            defects.append(f"concept_key must echo {concept_key!r}")
        if str(response.get("tier") or "") != tier:
            defects.append(f"tier must echo {tier!r}")
        families = response.get("families")
        if not isinstance(families, list):
            return [*defects, "response has no families array"]
        defects.extend(
            _partition_defects(families, candidate_ids, existing_keys))
        if not str(response.get("rationale") or "").strip():
            defects.append("response has no rationale")
        return defects

    return check


def cluster_tier(
    candidates: list[Mapping],
    *,
    concept: Mapping,
    tier: str,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
    existing_groups: list[Mapping] | None = None,
) -> dict[str, Any]:
    """Record one variant-family verdict for one occupied concept+tier.

    Every non-empty pool, including a singleton, is a semantic decision.
    Empty pools require no family and therefore spend no provider call.
    """

    if not candidates:
        return {"families": [], "flags": [], "authority": {}}
    if not isinstance(concept, Mapping):
        raise GroupingError("variant clustering requires a concept object")
    if tier not in TIER_CODES:
        raise GroupingError(f"unknown group tier {tier!r}")
    envelope_sha = _envelope_hash(envelope_sha256)
    candidate_ids = _candidate_ids(candidates, stage="variant clustering")
    concept_key = str(concept.get("concept_key") or "").strip()
    if not concept_key:
        raise GroupingError("variant clustering concept has no concept_key")

    existing = list(existing_groups or [])
    existing_keys: list[str] = []
    for position, group in enumerate(existing, start=1):
        if not isinstance(group, Mapping):
            raise GroupingError(
                f"existing group {position} is not an object")
        group_key = str(group.get("group_key") or "").strip()
        if not group_key:
            raise GroupingError(f"existing group {position} has no group_key")
        if str(group.get("concept_key") or "") != concept_key:
            raise GroupingError(
                f"existing group {group_key!r} belongs to a different concept")
        if str(group.get("group_type") or "") != tier:
            raise GroupingError(
                f"existing group {group_key!r} belongs to a different tier")
        existing_keys.append(group_key)
    if len(existing_keys) != len(set(existing_keys)):
        raise GroupingError("existing groups repeat a group_key")
    known_existing = set(existing_keys)

    provider, critic, fixer = _live_authorities(
        provider, critic, fixer,
        live_provider=_live_cluster,
        live_critic=_live_cluster_critic,
        stage="cluster",
    )
    store = store or kernel.DecisionStore()
    payload = {
        "stage": "assessment.variant_cluster",
        "rules": CLUSTER_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "concept": _concept_payload(concept),
        "tier": tier,
        "candidates": _member_payload(candidates),
        "existing_groups": [
            {
                "group_key": str(group.get("group_key") or ""),
                "group_type": copy.deepcopy(group.get("group_type")),
                "family": copy.deepcopy(group.get("family")),
                "semantic_description": copy.deepcopy(
                    group.get("semantic_description")),
                "member_candidate_ids": copy.deepcopy(
                    group.get("member_candidate_ids")),
            }
            for group in existing
        ],
    }
    decision = kernel.decide(
        kind="assessment.variant_cluster",
        unit_id=f"{concept_key}|{tier}",
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_cluster_checker(
            candidate_ids, known_existing, concept_key, tier),
        critic=critic,
        store=store,
        policy_version=VARIANT_CLUSTER_POLICY_VERSION,
        fixer=fixer,
    )
    response = copy.deepcopy(dict(decision["response"]))
    families = [
        {
            "existing_group_key": str(
                family.get("existing_group_key") or ""),
            "family": str(family.get("family") or ""),
            "member_candidate_ids": [
                str(member)
                for member in family.get("member_candidate_ids") or []
            ],
        }
        for family in response.get("families") or []
    ]
    return {
        "families": families,
        "decision": response,
        "flags": _review_flags(decision),
        "authority": _decision_authority(decision),
    }


# --------------------------------------------------------------------------- #
# Stage 10 — group descriptions
# --------------------------------------------------------------------------- #

def _description_checker(
    group_key: str, internal_ids: set[str],
) -> kernel.Checker:
    """Mechanics only: required fields and no supplied internal-ID leak."""

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("group_key") or "") != group_key:
            defects.append(f"group_key must echo {group_key!r}")
        description = str(response.get("description") or "").strip()
        if not description:
            defects.append("response has no description")
        folded_description = description.casefold()
        leaked = sorted(
            internal_id
            for internal_id in internal_ids
            if internal_id and internal_id.casefold() in folded_description
        )
        if leaked:
            defects.append(
                "description exposes internal identity: "
                + ", ".join(repr(value) for value in leaked)
            )
        if not str(response.get("rationale") or "").strip():
            defects.append("response has no rationale")
        return defects

    return check


def _description_internal_ids(
    group: Mapping, concept: Mapping, members: list[Mapping],
) -> set[str]:
    """Exact supplied machine identities that may never enter visible prose."""

    def identity(record: Mapping, field: str) -> str:
        value = record.get(field)
        return value.strip() if isinstance(value, str) else ""

    identities = {
        identity(group, field)
        for field in ("group_key", "concept_key", "concept_id")
    }
    identities.update(
        identity(concept, field)
        for field in ("concept_key", "concept_id", "concept_machine_id")
    )
    for member in members:
        identities.update(
            identity(member, field)
            for field in (
                "candidate_id",
                "group_key",
                "blueprint_cell_id",
                "source_qid",
            )
        )
        identities.update(
            str(value or "").strip()
            for value in member.get("source_atom_ids") or []
        )
    return {identity for identity in identities if identity}


def describe_group(
    group: Mapping,
    members: list[Mapping],
    *,
    concept: Mapping,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Author an occupied group's HOW + WHAT description once.

    Empty required shells stay ``NA`` mechanically and make no model call.
    """

    if not members:
        return {"description": "NA", "flags": [], "authority": {}}
    if not isinstance(group, Mapping):
        raise GroupingError("group description requires a group object")
    if not isinstance(concept, Mapping):
        raise GroupingError("group description requires a concept object")
    group_key = str(group.get("group_key") or "").strip()
    if not group_key:
        raise GroupingError("group description requires a group_key")
    _candidate_ids(members, stage="group description")
    envelope_sha = _envelope_hash(envelope_sha256)

    provider, critic, fixer = _live_authorities(
        provider, critic, fixer,
        live_provider=_live_description,
        live_critic=_live_description_critic,
        stage="describe",
    )
    store = store or kernel.DecisionStore()
    payload = {
        "stage": "assessment.group_description",
        "rules": DESCRIBE_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "group": copy.deepcopy(dict(group)),
        "concept": _concept_payload(concept),
        "members": _member_payload(members),
    }
    decision = kernel.decide(
        kind="assessment.group_description",
        unit_id=group_key,
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_description_checker(
            group_key, _description_internal_ids(group, concept, members)
        ),
        critic=critic,
        store=store,
        policy_version=GROUP_DESCRIPTION_POLICY_VERSION,
        fixer=fixer,
    )
    response = copy.deepcopy(dict(decision["response"]))
    return {
        "description": str(response.get("description") or ""),
        "decision": response,
        "flags": _review_flags(decision),
        "authority": _decision_authority(decision),
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
    """All semantic candidate evidence for Tag-New change detection.

    Placement/group identities and recorded audit stamps are downstream
    bookkeeping, not authored content.  Every other field participates so a
    change to source/route evidence, rich assets, a rubric, a subquestion, or
    any future semantic field automatically makes the candidate new again.
    """

    downstream = {
        "candidate_id", "question_label", "concept_id", "group_key",
        "group_name", "group_display_name", "group_description",
        "group_type", "group_sequence", "semantic_fingerprint",
        "content_sha256", "flags", "authority", "created_at", "updated_at",
    }
    content = {
        str(key): copy.deepcopy(value)
        for key, value in candidate.items()
        if str(key) not in downstream and not str(key).startswith("_aegis_")
    }
    return rel.sha256_json(content)


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
