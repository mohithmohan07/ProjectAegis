"""Atomic, source-supported prerequisite authority inside the existing call.

Parents may contain several fundamentals. Atoms carry their parent provenance;
each atom is retained or explicitly disposed exactly once. Models alone decide
the split, eligibility and demand coverage. Historical authority remains intact.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

from . import prelearning_capture_policy as policy
from . import generation_quality_policy as quality
from .response_schemas import ResponseSchema


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Atom(_Record):
    atom_id: str
    text: str
    capture_refs: list[str]
    evidence: list[str]
    rationale: str


class Prerequisite(_Record):
    prerequisite_id: str
    text: str
    atoms: list[str]
    rationale: str


class Disposition(_Record):
    disposition_id: str
    classification: Literal[
        "chapter_taught", "instruction_supplied", "general_procedure_not_preconcept",
        "not_required_for_this_chapter", "insufficient_evidence",
    ]
    atoms: list[str]
    rationale: str


class Coverage(_Record):
    demand_ref: str
    prerequisite_ids: list[str]
    rationale: str


class ReviewResolution(_Record):
    concern_id: str
    atom_ids: list[str]
    rationale: str


class AuthorityResponse(_Record):
    atoms: list[Atom]
    prerequisites: list[Prerequisite]
    dispositions: list[Disposition]
    demand_coverage: list[Coverage]
    review_resolutions: list[ReviewResolution]


SCHEMA = ResponseSchema("aegis_prelearning_atomic_authority_v2", AuthorityResponse)

SYSTEM = """You are the final prerequisite adjudicator for a school content pipeline.
The supplied stage captures are evidence, not authority. Read their COMPLETE
source evidence, source demands, attached figures, merge suggestions and earlier
critic concerns. Find the prior-grade/year fundamentals needed to follow this
chapter. An earlier chapter in the same grade alone is insufficient provenance;
record uncertainty instead of inventing curriculum history. Revision of earlier
grade learning is eligible even when this chapter recaps it. Current-chapter new
teaching and supplied response instructions are not prerequisites. A procedure
the learner must already know is different from a printed response instruction.
Do not suppress a necessary skill because it is familiar. No concept quota.

Produce atomic fundamentals BEFORE grouping. Each atom is one independently
teachable and diagnosable capability, with PA-0001... positional IDs, text,
capture_refs, evidence IDs and rationale. Split a broad parent capture into as
many distinct atoms as its actual meaning requires. Its capture_ref may appear
on MULTIPLE distinct atoms: this records shared provenance, not duplicate content.
Every supplied capture_ref must be represented; account for its entire meaning,
including an ineligible part as an atom subsequently disposed. Merge duplicate
captures of the same fundamental into one atom carrying all their references.
Recover a missing fundamental from a source demand or earlier critic concern:
such an atom may have empty capture_refs but MUST cite supplied source evidence
and explain the assumption missed. Never add unrelated curriculum material.

Assign EVERY atom exactly once, either to one final prerequisite or one explicit
disposition. Final prerequisites contain prerequisite_id (PR-0001...), text,
atoms and rationale. Group only atoms that are one teachable capability; sharing
a lesson, vocabulary or downstream use alone does not make them one concept.
Dispositions contain disposition_id (PD-0001...), classification, atoms and
rationale, using the supplied classification list. Preserve all atom meaning.

For EVERY source demand, return demand_coverage: demand_ref, prerequisite_ids,
rationale. Explain which prerequisite knowledge is necessary to follow that
source teaching/task pattern. If it genuinely needs no eligible prerequisite,
use an empty list and explain why. Do not force links to cover a chapter. This
is a coverage audit, not permission to promote chapter teaching to Pre content.
For EVERY supplied critic concern, return review_resolutions: concern_id,
atom_ids and rationale. Resolve it against source evidence; atom_ids may be empty
when it is unsupported or needs no additional atom. Do not silently ignore it.
Return only the supplied JSON schema. Never copy or answer a current source task.
"""

CRITIC_SYSTEM = """Independently audit the final prerequisite authority against
the complete supplied source evidence and actual attached figures. Check that
every source demand's necessary earlier-grade/year fundamentals are represented,
including omissions raised by earlier critics. An empty demand link requires a
genuine source-supported reason. Check that each parent capture's full meaning
survives atomization or explicit disposition, shared provenance does not conceal
duplicate atoms, and genuinely different capabilities were not merged merely
because they share a lesson. Check eligibility and appropriate teaching level;
revision of earlier learning is not new chapter teaching. Do not impose a count.
Check each claimed recovery's evidence and each disposition's reason. Cite exact
atom/demand/capture IDs in any issue. Return {verdict: verified|rejected,
confidence, issues}; dissent is advisory and never a silent content deletion.
"""

VERSION = policy.VERSION + ";authority:" + hashlib.sha256(
    (SYSTEM + CRITIC_SYSTEM + str(SCHEMA.identity())).encode("utf-8")
).hexdigest()


def _evidence(
    merged: Mapping[str, Any], *, complete_demands: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from .phase3 import prelearn

    index: dict[str, Any] = {}
    demands: list[dict[str, Any]] = []
    for stage, packet in (merged.get("evidence_packets") or {}).items():
        if not isinstance(packet, Mapping):
            continue
        for ref, entries in prelearn.indexed_evidence(stage, packet).items():
            index.setdefault(ref, []).extend(entries)
        # These units already express the chapter's authored teaching and
        # source task patterns. No text/size heuristic creates a demand.
        sections = {
            "settle": (("settled_concepts", "concept_id"),),
            "host": (("type_case_units", "unit_id"),),
        }.get(stage, ())
        if complete_demands:
            # Enumerate supplied evidence addresses, not locally inferred
            # pedagogical demands. The model may explicitly return no prior
            # learning for any address; no block/task creates a concept quota.
            sections = {
                "settle": (
                    ("source_blocks", "block_id"),
                    ("settled_concepts", "concept_id"),
                ),
                "host": (
                    ("type_case_units", "unit_id"), ("questions", "qid"),
                ),
                "place": (
                    ("pooled_hub_items", "item_ref"),
                    ("pooled_figures", "item_ref"),
                ),
                "analyse": (("analysis_inventory", "item_id"),),
            }.get(stage, ())
        for section, field in sections:
            for row in packet.get(section) or []:
                if isinstance(row, Mapping) and row.get(field):
                    demands.append({
                        "demand_ref": (
                            f"{stage}:{section}:{row[field]}" if complete_demands
                            else f"{stage}:{row[field]}"
                        ),
                        "evidence_id": str(row[field]),
                    })
    return index, demands


def checker(payload: Mapping[str, Any]):
    expected_captures = {row["capture_ref"] for row in payload["captures"]}
    known_evidence = set(payload["evidence_index"])
    expected_demands = {row["demand_ref"] for row in payload["source_demands"]}
    expected_concerns = {row["concern_id"] for row in payload["prior_review_concerns"]}

    def check(response: Mapping[str, Any]) -> list[str]:
        try:
            SCHEMA.validate_response(response)
        except ValidationError as exc:
            return ["invalid atomic authority schema: " + str(exc)]
        defects: list[str] = []

        def nonempty(value: str, label: str) -> None:
            if not value.strip():
                defects.append(label + " is empty")

        def refs(values: list[str], known: set[str], label: str, *, required=False):
            if required and not values:
                defects.append(label + " is empty")
            if len(values) != len(set(values)):
                defects.append(label + " repeats an ID")
            if set(values) - known:
                defects.append(label + " names unknown IDs: " + ", ".join(sorted(set(values) - known)))

        atoms = response["atoms"]
        atom_ids = {row["atom_id"] for row in atoms}
        captured: set[str] = set()
        for position, row in enumerate(atoms, 1):
            if row["atom_id"] != f"PA-{position:04d}":
                defects.append("atom IDs must be positional PA-0001...")
            nonempty(row["text"], row["atom_id"] + " text")
            nonempty(row["rationale"], row["atom_id"] + " rationale")
            refs(row["capture_refs"], expected_captures, row["atom_id"] + " capture_refs")
            refs(row["evidence"], known_evidence, row["atom_id"] + " evidence", required=True)
            captured.update(row["capture_refs"])
        if expected_captures - captured:
            defects.append("unaccounted parent captures: " + ", ".join(sorted(expected_captures - captured)))
        owned: list[str] = []
        for field, id_field, prefix in (
            ("prerequisites", "prerequisite_id", "PR"),
            ("dispositions", "disposition_id", "PD"),
        ):
            for position, row in enumerate(response[field], 1):
                if row[id_field] != f"{prefix}-{position:04d}":
                    defects.append(field + " IDs must be positional")
                refs(row["atoms"], atom_ids, row[id_field] + " atoms", required=True)
                nonempty(row["rationale"], row[id_field] + " rationale")
                if field == "prerequisites":
                    nonempty(row["text"], row[id_field] + " text")
                owned.extend(row["atoms"])
        if set(owned) != atom_ids or len(owned) != len(set(owned)):
            defects.append("every atom must be retained or disposed exactly once")
        prereq_ids = {row["prerequisite_id"] for row in response["prerequisites"]}
        for field, id_field, expected, ref_field, known in (
            ("demand_coverage", "demand_ref", expected_demands, "prerequisite_ids", prereq_ids),
            ("review_resolutions", "concern_id", expected_concerns, "atom_ids", atom_ids),
        ):
            listed = [row[id_field] for row in response[field]]
            if set(listed) != expected or len(listed) != len(set(listed)):
                defects.append(field + " must account for every supplied ID exactly once")
            for row in response[field]:
                refs(row[ref_field], known, row[id_field] + " " + ref_field)
                nonempty(row["rationale"], row[id_field] + " rationale")
        return defects

    return check


def _live_author(payload):
    from . import generation
    from .phase3 import evidence, prompts

    return generation._openai_json(
        SYSTEM + policy.boundary_instruction(payload), prompts.render(payload), purpose="concept_mapping",
        image_urls=evidence.image_inputs(payload), response_schema=SCHEMA,
    )


def _live_critic(payload):
    from . import generation
    from .phase3 import evidence, prompts

    return generation._openai_json(
        CRITIC_SYSTEM + policy.boundary_instruction(payload), prompts.render(payload), purpose="advisory_critic",
        image_urls=evidence.image_inputs(payload),
    )


def adjudicate(env, merged, *, provider=None, critic=None, store=None, fixer=None):
    from . import prelearning_formation_contract as legacy
    from . import progress
    from .phase3 import envelope, evidence, fixer as fixer_mod, kernel, prelearn

    if provider is None:
        envelope.require_live_api()
        provider = _live_author
        critic = critic or _live_critic
        fixer = fixer or fixer_mod.live_fixer
    lookup = legacy._capture_lookup(merged)
    refined = quality.active(env)
    evidence_index, demands = _evidence(merged, complete_demands=refined)
    concerns = []
    for origin, flags in (merged.get("stage_flags") or {}).items():
        for flag in flags or []:
            concerns.append({"concern_id": f"PC-{len(concerns) + 1:04d}", "origin": str(origin), "issue": str(flag)})
    payload = {
        "stage": "prelearn.adjudicate", "capture_policy": policy.VERSION,
        **policy.boundary_fields(env),
        "rules": SYSTEM + policy.boundary_instruction(policy.boundary_fields(env)),
        "response_schema": SCHEMA.identity(),
        "chapter": legacy._chapter(env),
        "captures": [lookup[ref] for ref in sorted(lookup)],
        "evidence_index": evidence_index,
        "source_demands": demands, "prior_review_concerns": concerns,
        "merge_suggestions": copy.deepcopy(merged.get("prerequisites") or []),
        "disposition_classes": list(legacy.PREREQUISITE_DISPOSITIONS),
    }
    decision = evidence.decide_with_visual_evidence(
        kind="prelearn.adjudicate", unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""), payload=payload,
        provider=provider, critic=critic, checker=checker(payload),
        store=store or kernel.DecisionStore(), fixer=fixer,
        policy_version=VERSION + (";" + quality.VERSION if refined else ""),
    )
    response = decision["response"]
    atoms = {row["atom_id"]: row for row in response["atoms"]}
    flags = list(decision.get("review_flags") or [])
    result = copy.deepcopy(dict(merged))
    source_blocks = {
        str(row.get("block_id") or "")
        for row in (env.get("canonical") or {}).get("blocks") or []
        if isinstance(row, Mapping)
    }
    for field in ("prerequisites", "dispositions"):
        result[field] = []
        for row in response[field]:
            owned = [atoms[ref] for ref in row["atoms"]]
            refs = list(dict.fromkeys(ref for atom in owned for ref in atom["capture_refs"]))
            cited = list(dict.fromkeys(ref for atom in owned for ref in atom["evidence"]))
            resolved = prelearn.resolve_evidence(evidence_index, set(cited))
            result[field].append({
                **copy.deepcopy(row), "captures": refs,
                "evidence": cited,
                # Keep model citations intact, alongside the explicit owned
                # BLK closure needed by the task-free Pre teaching map.
                "source_block_ids": [ref for ref in resolved if ref in source_blocks],
                "stages": list(dict.fromkeys(lookup[ref]["stage"] for ref in refs)),
                **({"retained_atoms": [
                    {"atom_id": atom["atom_id"], "text": atom["text"]}
                    for atom in owned
                ]} if refined and field == "prerequisites" else {}),
            })
    result["atoms"] = copy.deepcopy(response["atoms"])
    result["demand_coverage"] = copy.deepcopy(response["demand_coverage"])
    result["review_resolutions"] = copy.deepcopy(response["review_resolutions"])
    result["stage_flags"] = {**copy.deepcopy(merged.get("stage_flags") or {}), "adjudication": flags}
    result["review_flags"] = {
        row["prerequisite_id"]: kernel.pin_flags(flags, [r["prerequisite_id"] for r in result["prerequisites"]], row["prerequisite_id"])
        for row in result["prerequisites"]
    }
    result["adjudication"] = {
        "policy_version": VERSION + (";" + quality.VERSION if refined else ""),
        "decision_key": decision["key"],
        "capture_count": len(lookup), "atom_count": len(atoms),
        "prerequisite_count": len(result["prerequisites"]),
        "disposed_atom_count": sum(len(row["atoms"]) for row in result["dispositions"]),
        "recovered_atom_count": sum(not row["capture_refs"] for row in atoms.values()),
    }
    progress.log(
        f"Pre-Learning authority: {len(lookup)} capture(s) resolved into {len(atoms)} atomic "
        f"fundamental(s); retained {len(result['prerequisites'])} prerequisite(s), explicitly "
        f"disposed {result['adjudication']['disposed_atom_count']} atom(s), recovered "
        f"{result['adjudication']['recovered_atom_count']} missed atom(s); "
        f"{len(demands)} source demand(s) reviewed.", level="success",
    )
    return result
