"""One model-owned consolidation and teaching-order decision for new Post runs.

Runs after Host, before positional placement IDs and release certificates exist.
The model groups equivalent capabilities and supplies sequence permutations;
Python transports its verdict, evidence identities and existing question routes.
No wording similarity, keyword deduplication or inferred ordering lives here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

from . import envelope as envelope_mod
from . import kernel
from .evidence import decide_with_visual_evidence, image_inputs
from .. import generation_quality_policy as quality
from .. import source_topic_policy
from ..response_schemas import ResponseSchema, advisory_critic_schema

AUDIT_FIELD = "_aegis_concept_coherence"
ORDER_FIELD = "coherence_order"


class _ConceptGroup(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    source_row_ids: list[str]
    representative_row_id: str
    concept_title: str
    description: str
    achieving_mastery: str
    rationale: str


class _CoherenceResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    concepts: list[_ConceptGroup]
    type_order: list[str]
    case_order: list[str]
    question_order: list[str]


SCHEMA = ResponseSchema("aegis_post_concept_coherence_v1", _CoherenceResponse)

SYSTEM = """You are the Post-Learning concept coherence author. Compare the entire
chapter's settled and Host-created concept candidates, their descriptions,
mastery, source blocks and routed Types/Cases/questions. Return JSON only:
{"concepts":[{"source_row_ids":["ROW-0001"],"representative_row_id":"ROW-0001",
"concept_title":"existing title", "description":"", "achieving_mastery":"",
"rationale":"source-backed grouping and teaching-order reason"}],
"type_order":["TYPE-0001"], "case_order":["TYPE-0001::CASE-0001"],
"question_order":["QINV-0001"]}.

Every candidate row ID must occur exactly once across source_row_ids. Every
order array must be an exact permutation of its supplied identity list.
Consolidate semantically equivalent capabilities into one group even when
worded differently or repeated in separate source locations; preserve distinct
capabilities despite similar labels. Repeated examples, practice, facts and
representations do not themselves create new concepts. Do not impose a count.
Use one original representative row ID as the surviving topic/home. A singleton
keeps its exact title and teaching prose: return empty description and mastery.
For a merged group, author one focused concept_title, description and
achieving_mastery preserving all supported distinct teaching within that ONE
capability, without concatenating duplicate prose or large chapter extracts.
Do not merge distinct recorded language-plan identities or any culmination with
ordinary teaching. A planned concept's representative and title stay unchanged.

Return concepts in the source's teaching progression, grouped in the supplied
Topics' order. Within a topic, develop foundations before the source's later
applications; preserve its final culmination. Judge meaningful teaching order
from full source evidence, not lexicographic IDs or chapter-end exercise
position. Source placement is evidence for sequence, never academic ownership.
Order Types and Cases coherently within their already assigned capabilities;
keep each question within its original Type/Case and preserve the source's
local task progression. Do not regroup, omit, deduplicate or rewrite questions.
Tables, images, givens, options and question identities stay untouched.
The independent critic must be able to verify every merge and every sequence
against source evidence; explain the reasoning on each concept group.
"""
CRITIC_SYSTEM = """Independently audit the Post-Learning coherence decision.
Check equivalent capabilities were consolidated, distinct ones retained, all
source evidence and question occurrences remain represented, and teaching
progression follows the supplied source. Check the Type/Case/question order is
coherent with each assigned concept and the source's local sequence, without
moving ownership by printed location. Check merged prose teaches one capability
without repeated chapter extracts. Do not approve two differently worded copies
of one concept merely because titles differ. Preserve recorded literary plans
and culminations. Return {"verdict":"verified|dissent", "confidence":0.0,
"issues":[]} with specific ROW-/Type/Case/QID identities for any dissent.
"""


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    # This matches the already-established Host/Assemble identity transport;
    # it never decides whether two separately identified concepts are equivalent.
    return (
        str(row.get("topic_id") or row.get("_semantic_topic_id") or ""),
        " ".join(str(row.get("concept_title") or "").split()).casefold(),
    )


def _ids(env: Mapping[str, Any], hosts: Mapping[str, Any]) -> dict[str, list[str]]:
    types = (env.get("mined_types") or {}).get("types") or []
    return {
        "type_order": [str(row["type_id"]) for row in types],
        "case_order": [
            str(row["type_id"]) + "::" + str(case["case_id"])
            for row in types for case in row.get("case_prompts") or []
        ],
        "question_order": list((hosts.get("qid_map") or {}).keys()),
    }


def _is_culmination(row: Mapping[str, Any]) -> bool:
    from .. import concept_refiner

    return concept_refiner.is_culmination(str(row.get("concept_title") or ""))


def _checker(rows: Mapping[str, Mapping[str, Any]], topics: list[str],
             orders: Mapping[str, list[str]]) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        try:
            SCHEMA.validate_response(response)
        except ValidationError as exc:
            return ["coherence response schema: " + str(exc)]
        defects: list[str] = []
        groups = response.get("concepts")
        if not isinstance(groups, list):
            return ["concepts must be an array"]
        seen: list[str] = []
        keys: set[tuple[str, str]] = set()
        last_topic = -1
        closed_topics: set[str] = set()
        for group in groups:
            if not isinstance(group, Mapping):
                defects.append("concept group must be an object")
                continue
            refs = group.get("source_row_ids")
            if not isinstance(refs, list) or not refs or any(
                not isinstance(ref, str) or ref not in rows for ref in refs
            ):
                defects.append("source_row_ids must name existing candidate rows")
                continue
            seen.extend(refs)
            rep = group.get("representative_row_id")
            if rep not in refs:
                defects.append("representative_row_id must belong to source_row_ids")
                continue
            original = rows[rep]
            title = str(group.get("concept_title") or "").strip()
            if not title or not str(group.get("rationale") or "").strip():
                defects.append(f"{rep} requires a title and rationale")
            if len(refs) == 1 and (
                title != str(original.get("concept_title") or "")
                or group.get("description") or group.get("achieving_mastery")
            ):
                defects.append(f"{rep} singleton must preserve its title and prose")
            if len(refs) > 1:
                if any(_is_culmination(rows[ref]) for ref in refs):
                    defects.append(f"{rep} culmination identity must remain distinct")
                identities = {
                    json.dumps(rows[ref].get("_aegis_language_plan_identity"), sort_keys=True)
                    for ref in refs if rows[ref].get("_aegis_language_plan_identity")
                }
                if len(identities) > 1:
                    defects.append(f"{rep} distinct language-plan identities cannot merge")
                elif identities and (
                    not original.get("_aegis_language_plan_identity")
                    or title != str(original.get("concept_title") or "")
                ):
                    # An unplanned Host-created duplicate can be absorbed by
                    # its planned capability, but it cannot replace or rename
                    # that authoritative plan identity.
                    defects.append(f"{rep} merged language-plan group requires its planned representative and exact title")
                for field in ("description", "achieving_mastery"):
                    if not isinstance(group.get(field), str) or not group[field].strip():
                        defects.append(f"{rep} merged group requires {field}")
            topic = str(original.get("_semantic_topic_id") or "")
            if topic not in topics:
                defects.append(f"{rep} has an unknown source topic")
                continue
            ordinal = topics.index(topic)
            if ordinal < last_topic or topic in closed_topics:
                defects.append(f"{rep} violates recorded topic/final culmination order")
            last_topic = ordinal
            if _is_culmination(original):
                closed_topics.add(topic)
            key = (topic, " ".join(title.split()).casefold())
            if key in keys:
                defects.append(f"{rep} duplicates an exact output host identity")
            keys.add(key)
        if len(seen) != len(set(seen)) or set(seen) != set(rows):
            defects.append("every candidate row must be represented exactly once")
        for name, expected in orders.items():
            actual = response.get(name)
            if (not isinstance(actual, list) or any(not isinstance(v, str) for v in actual)
                    or len(actual) != len(expected) or set(actual) != set(expected)):
                defects.append(f"{name} must be an exact identity permutation")
        return defects

    return check


def _live(payload: dict[str, Any], *, critic: bool = False) -> dict[str, Any]:
    from .. import generation
    from . import prompts

    return generation._openai_json(
        CRITIC_SYSTEM if critic else SYSTEM, prompts.render(payload),
        purpose="advisory_critic" if critic else "concept_mapping",
        image_urls=image_inputs(payload),
        response_schema=advisory_critic_schema() if critic else SCHEMA,
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    return _live(payload, critic=True)


def consolidate(
    env: Mapping[str, Any], settled: list[Mapping[str, Any]],
    hosts: Mapping[str, Any], *, provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None, store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply one recorded semantic verdict before downstream IDs are minted."""
    if not quality.is_current(env):
        return list(settled), dict(hosts)
    all_rows = [*settled, *(hosts.get("new_concepts") or [])]
    if not all_rows:
        return [], dict(hosts)
    store = store or kernel.DecisionStore()
    row_by_id = {f"ROW-{i:04d}": row for i, row in enumerate(all_rows, 1)}
    topics = list((env.get("graph") or {}).get("topics") or [])
    orders = _ids(env, hosts)
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live
        critic = critic or _live_critic
        from . import fixer as fixer_mod

        fixer = fixer or fixer_mod.default_provider()
    from . import prompts

    payload = {
        "stage": "post.concept_coherence",
        "rules": source_topic_policy.POST_COHERENCE_INSTRUCTION
                 + prompts.instruction_rules_suffix(env),
        **quality.fields(env),
        "topics_in_teaching_order": topics,
        "candidate_rows": [{"row_id": ref, **copy.deepcopy(dict(row))}
                           for ref, row in row_by_id.items()],
        "canonical_source": copy.deepcopy(env.get("canonical") or {}),
        "source_graph": copy.deepcopy(env.get("graph") or {}),
        "question_inventory": copy.deepcopy(env.get("inventory") or {}),
        "mined_types": copy.deepcopy(env.get("mined_types") or {}),
        "host_map": copy.deepcopy(hosts.get("host_map") or {}),
        "qid_map": copy.deepcopy(hosts.get("qid_map") or {}),
        "required_orders": orders,
        "response_schema": SCHEMA.identity(),
    }
    policy = quality.VERSION + ";coherence:" + hashlib.sha256(
        (SYSTEM + CRITIC_SYSTEM).encode("utf-8")
    ).hexdigest()
    decision = decide_with_visual_evidence(
        kind="post.concept_coherence", unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""), payload=payload,
        provider=provider, checker=_checker(row_by_id, [str(t["topic_id"]) for t in topics], orders),
        critic=critic, store=store, fixer=fixer, policy_version=policy,
    )
    response = decision["response"]
    output: list[dict[str, Any]] = []
    destinations: dict[tuple[str, str], dict[str, Any]] = {}
    all_refs = list(row_by_id)
    for ordinal, group in enumerate(response["concepts"], 1):
        refs = group["source_row_ids"]
        sources = [row_by_id[ref] for ref in refs]
        row = copy.deepcopy(dict(row_by_id[group["representative_row_id"]]))
        if len(refs) > 1:
            row["concept_title"] = group["concept_title"]
            row["concept_details"] = (
                "Description: " + group["description"].strip()
                + "\nAchieving Mastery: " + group["achieving_mastery"].strip()
            )
            for field in ("_source_block_ids", "_reference_block_ids", "_semantic_subtopic_ids"):
                row[field] = list(dict.fromkeys(
                    value for source in sources for value in source.get(field) or []
                ))
            origins = list(dict.fromkeys(
                value for source in sources
                for value in [source.get("_phase32_origin_concept_id"),
                              *(source.get("_phase32_origin_concept_ids") or [])]
                if value
            ))
            if origins:
                row["_phase32_origin_concept_ids"] = origins
            row["_source_grounding_contract"] = "api-verified-source-block-ids"
        flags = list(dict.fromkeys(
            str(flag) for source in sources for flag in source.get("review_flags") or []
        ))
        for ref in refs:
            for flag in kernel.pin_flags(list(decision.get("review_flags") or []), all_refs, ref):
                if flag not in flags:
                    flags.append(flag)
        if flags:
            row["review_flags"] = flags
        row[AUDIT_FIELD] = {
            "policy_version": policy, "teaching_order": ordinal,
            "source_row_ids": list(refs), "rationale": group["rationale"],
            "original_rows": copy.deepcopy(sources),
        }
        output.append(row)
        for source in sources:
            key = _key(source)
            if key in destinations and destinations[key] is not row:
                raise kernel.ContractError("coherence left an ambiguous original host identity")
            destinations[key] = row
    updated = copy.deepcopy(dict(hosts))
    updated["coherence_original_routes"] = {
        name: copy.deepcopy(hosts.get(name) or {})
        for name in ("host_map", "qid_map")
    }
    for map_name in ("host_map", "qid_map"):
        for entry in (updated.get(map_name) or {}).values():
            key = _key(entry)
            if key not in destinations:
                raise kernel.ContractError("coherence route names no original concept identity")
            row = destinations[key]
            for field in ("concept_title", "parent_concept", "topic"):
                entry[field] = row.get(field) or ""
            entry["topic_id"] = row.get("_semantic_topic_id") or ""
    # All Host-created rows participated in the same exact-once decision.
    updated["new_concepts"] = []
    updated[ORDER_FIELD] = {name: list(response[name]) for name in orders}
    return output, updated
