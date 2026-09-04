"""Reconcile Host-created literary concepts back into the language plan.

Host may create a source-grounded concept when a Type/Case cannot fit the
settled map. That escape is correct for the universal pipeline, but a sealed
language plan has already decided the complete Post-Learning topology. A
method, response format, Warm-up or exercise must not re-enter as an extra
literary concept merely because its Type needs a home.

For a literary envelope this contract lets ordinary Host run unchanged. Only
when it actually creates one or more rows does one content-addressed model
verdict map every created identity to the best existing planned concept. The
created prose is evidence for that decision, never published topology. Every
host unit and QID that pointed to the created row moves with the verdict; Q14
may subsequently consolidate a split Type exactly as before.

Deterministic work is limited to identity clustering, closed-set response
validation, exact application and review-flag transport. Which planned concept
best hosts the Type remains an API judgment with critic and Fixer seams.
"""
from __future__ import annotations

import copy
import importlib
from functools import wraps
from typing import Any, Callable, Mapping, Sequence

from . import postlearning_formation_contract as post


CONTRACT_VERSION = 1
POLICY_VERSION = "post-language-host-reconciliation-1"

HOST_RECONCILIATION_SYSTEM = """\
You are reconciling Type-host concepts against an authoritative literary
Post-Learning plan. Host created the supplied temporary concepts because a
Type/Case did not appear to fit an existing row. Those temporary concepts may
NOT extend the plan: assessment methods, response formats, activities and
exercise structures live under the literary concepts they assess; they are not
new literary teaching concepts.

For every created_concept_id choose exactly one destination_plan_concept_id
from the supplied planned concepts. Judge the Type/Case units, their questions,
the temporary concept's source grounding and the planned concepts' actual
teaching. Choose the concept whose teaching the unit most genuinely exercises.
A local or chapter culmination is legitimate when the task truly combines the
member concepts. Never choose by title similarity, first position, count,
coverage balance or a desire to spread Types. Return a concise rationale for
every choice. Do not create, rename, split or omit any plan identity."""

HOST_RECONCILIATION_CRITIC_SYSTEM = """\
Independently audit the proposed reconciliation of temporary Host concepts
into the authoritative literary plan. Check that each Type/Case is moved to a
planned concept that actually teaches what its questions exercise; that a
Warm-up, response format, recitation direction or exercise pattern has not
been treated as new literary content; and that culminations are used only for
truly multi-concept tasks. Dissent is advisory and must name the relevant
created_concept_id."""


def _normal(value: object) -> str:
    return " ".join(str(value or "").split())


def _identity(value: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("topic_id") or value.get("_semantic_topic_id") or ""),
        _normal(value.get("concept_title")).casefold(),
    )


def _created_clusters(
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Cluster duplicate temporary rows by the identity Host exposes."""
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in result.get("new_concepts") or []:
        if not isinstance(row, Mapping):
            continue
        key = _identity(row)
        if not key[1]:
            continue
        if key not in clusters:
            clusters[key] = {
                "rows": [],
                "unit_ids": [],
                "qids": [],
            }
            order.append(key)
        clusters[key]["rows"].append(copy.deepcopy(dict(row)))

    for unit_id, entry in (result.get("host_map") or {}).items():
        if not isinstance(entry, Mapping):
            continue
        key = _identity(entry)
        if key in clusters and str(unit_id) not in clusters[key]["unit_ids"]:
            clusters[key]["unit_ids"].append(str(unit_id))
    for qid, entry in (result.get("qid_map") or {}).items():
        if not isinstance(entry, Mapping):
            continue
        key = _identity(entry)
        if key in clusters and str(qid) not in clusters[key]["qids"]:
            clusters[key]["qids"].append(str(qid))

    output: list[dict[str, Any]] = []
    for index, key in enumerate(order, start=1):
        cluster = clusters[key]
        output.append({
            "created_concept_id": f"HOST-CREATED-{index:04d}",
            "identity": key,
            "rows": cluster["rows"],
            "unit_ids": cluster["unit_ids"],
            "qids": cluster["qids"],
        })
    return output


def _planned_rows(
    env: Mapping[str, Any],
    settled_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries = {
        str(entry["plan_concept_id"]): entry
        for entry in post.plan_entries(env)
    }
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in settled_rows:
        identity = row.get(post.PLAN_IDENTITY_FIELD)
        if not isinstance(identity, Mapping):
            continue
        plan_id = str(identity.get("plan_concept_id") or "")
        entry = entries.get(plan_id)
        if entry is None or plan_id in seen:
            continue
        seen.add(plan_id)
        output.append({
            "plan_concept_id": plan_id,
            "concept_title": _normal(row.get("concept_title")),
            "parent_concept": _normal(row.get("parent_concept")),
            "topic": _normal(row.get("topic")),
            "topic_id": str(row.get("_semantic_topic_id") or ""),
            "semantic_role": str(entry.get("semantic_role") or ""),
            "concept_details": str(row.get("concept_details") or ""),
            "source_block_ids": list(row.get("_source_block_ids") or []),
            "planned_task_qids": list(entry.get("task_qids") or []),
        })
    expected = set(entries)
    if seen != expected:
        missing = sorted(expected - seen)
        raise ValueError(
            "the authoritative language plan has no settled row for: "
            + ", ".join(missing[:8])
        )
    return output


def _checker(
    created_ids: Sequence[str], planned_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = list(created_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        rows = response.get("reassignments")
        if not isinstance(rows, list):
            return ["response has no reassignments array"]
        defects: list[str] = []
        if len(rows) != len(expected):
            defects.append(
                f"response has {len(rows)} reassignments; expected {len(expected)}"
            )
        seen: set[str] = set()
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                defects.append("a reassignment is not an object")
                continue
            created_id = str(row.get("created_concept_id") or "")
            wanted = expected[position] if position < len(expected) else ""
            if created_id != wanted or created_id in seen:
                defects.append(
                    f"reassignment at position {position + 1} carries "
                    f"{created_id or '<empty>'!r}; expected {wanted!r}"
                )
            seen.add(created_id)
            destination = str(row.get("destination_plan_concept_id") or "")
            if destination not in planned_ids:
                defects.append(
                    f"{created_id or wanted} names unknown planned destination "
                    f"{destination or '<empty>'!r}"
                )
            if not _normal(row.get("rationale")):
                defects.append(f"{created_id or wanted} has no rationale")
        if seen != set(expected):
            defects.append("not every created_concept_id was returned exactly once")
        return defects

    return check


def _live_provider(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        HOST_RECONCILIATION_SYSTEM,
        prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        HOST_RECONCILIATION_CRITIC_SYSTEM,
        prompts.render(payload),
        purpose="advisory_critic",
    )


def _question_payload(env: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(row, Mapping):
            continue
        qid = str(row.get("qid") or "").strip()
        if not qid:
            continue
        output[qid] = {
            "qid": qid,
            "text": str(
                row.get("polished_task")
                or row.get("normalized_task")
                or row.get("raw_task")
                or ""
            )[:800],
            "source_kind": str(row.get("source_kind") or ""),
        }
    return output


def _apply(
    result: Mapping[str, Any],
    clusters: Sequence[Mapping[str, Any]],
    planned_rows: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any],
    flags: Sequence[str],
) -> dict[str, Any]:
    from .phase3 import kernel

    plan_by_id = {
        str(row["plan_concept_id"]): row for row in planned_rows
    }
    cluster_by_id = {
        str(row["created_concept_id"]): row for row in clusters
    }
    assignments = {
        str(row.get("created_concept_id") or ""): row
        for row in response.get("reassignments") or []
        if isinstance(row, Mapping)
    }
    destination_by_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    notes_by_identity: dict[tuple[str, str], list[str]] = {}
    flag_ids = [
        str(row["created_concept_id"]) for row in clusters
    ] + list(plan_by_id)

    for created_id, cluster in cluster_by_id.items():
        assignment = assignments[created_id]
        destination_id = str(assignment["destination_plan_concept_id"])
        destination = plan_by_id[destination_id]
        key = tuple(cluster["identity"])
        destination_by_identity[key] = destination
        notes = [
            f"{created_id}: Host proposed an extra literary concept; the "
            f"authoritative language plan reassigned its Type/Case work to "
            f"{destination_id} ('{destination['concept_title'][:80]}'): "
            + _normal(assignment.get("rationale"))
        ]
        notes.extend(kernel.pin_flags(list(flags), flag_ids, created_id))
        notes_by_identity[key] = list(dict.fromkeys(notes))

    host_map: dict[str, Any] = {}
    for unit_id, raw in (result.get("host_map") or {}).items():
        entry = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
        key = _identity(entry)
        destination = destination_by_identity.get(key)
        if destination is not None:
            entry.update({
                "decision": "existing_after_language_plan_reconciliation",
                "concept_title": destination["concept_title"],
                "parent_concept": destination["parent_concept"],
                "topic": destination["topic"],
                "topic_id": destination["topic_id"],
            })
            existing = entry.setdefault("review_flags", [])
            for note in notes_by_identity.get(key, []):
                if note not in existing:
                    existing.append(note)
        host_map[str(unit_id)] = entry

    qid_map: dict[str, Any] = {}
    for qid, raw in (result.get("qid_map") or {}).items():
        entry = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
        key = _identity(entry)
        destination = destination_by_identity.get(key)
        if destination is not None:
            entry.update({
                "decision": "api_placement_after_language_plan_reconciliation",
                "concept_title": destination["concept_title"],
                "parent_concept": destination["parent_concept"],
                "topic": destination["topic"],
                "topic_id": destination["topic_id"],
            })
            existing = entry.setdefault("review_flags", [])
            for note in notes_by_identity.get(key, []):
                if note not in existing:
                    existing.append(note)
        qid_map[str(qid)] = entry

    return {
        "host_map": host_map,
        "qid_map": qid_map,
        "new_concepts": [],
    }


def reconcile(
    env: Mapping[str, Any],
    settled_rows: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    *,
    provider: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    critic: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    store: Any = None,
    fixer: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    allow_live: bool = True,
) -> dict[str, Any]:
    """Return Host output with no extra literary Post concept."""
    entries = post.plan_entries(env)
    clusters = _created_clusters(result)
    if not entries or not clusters:
        return copy.deepcopy(dict(result))
    if provider is None and not allow_live:
        return copy.deepcopy(dict(result))

    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod
    from .phase3 import host as host_mod
    from .phase3 import kernel
    from . import progress

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_provider
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    planned = _planned_rows(env, settled_rows)
    units = {
        str(row.get("unit_id") or ""): row
        for row in host_mod.derive_units(env)
    }
    questions = _question_payload(env)
    payload = {
        "stage": "postlearning.language_host_reconciliation",
        "rules": HOST_RECONCILIATION_SYSTEM,
        "planned_concepts": copy.deepcopy(planned),
        "created_concepts": [
            {
                "created_concept_id": cluster["created_concept_id"],
                "temporary_rows": cluster["rows"],
                "units": [
                    {
                        **copy.deepcopy(dict(units[unit_id])),
                        "questions": [
                            questions[qid]
                            for qid in units[unit_id].get("qids") or []
                            if qid in questions
                        ],
                    }
                    for unit_id in cluster["unit_ids"]
                    if unit_id in units
                ],
                "qids_routed_to_temporary_concept": list(cluster["qids"]),
            }
            for cluster in clusters
        ],
    }
    decision = kernel.decide(
        kind="postlearning.language_host_reconciliation",
        unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""),
        payload=payload,
        provider=provider,
        checker=_checker(
            [str(row["created_concept_id"]) for row in clusters],
            {str(row["plan_concept_id"]) for row in planned},
        ),
        critic=critic,
        store=store,
        policy_version=POLICY_VERSION,
        fixer=fixer,
    )
    reconciled = _apply(
        result,
        clusters,
        planned,
        decision.get("response") or {},
        decision.get("review_flags") or [],
    )
    progress.log(
        "Post-Learning Host: reassigned "
        f"{len(clusters)} temporary concept identity/identities to the "
        "authoritative literary plan; no Host-created literary row ships.",
        level="success",
    )
    return reconciled


def install() -> None:
    """Install after Post formation, before support and Pre reconciliation."""
    host = importlib.import_module("app.services.phase3.host")
    current = host.host
    if getattr(current, "_aegis_postlearning_host", False):
        return

    @wraps(current)
    def host_with_plan(env, settled_rows, *args, **kwargs):
        post_provider = kwargs.pop("post_host_provider", None)
        post_critic = kwargs.pop("post_host_critic", None)
        explicit_host = kwargs.get("provider") is not None
        result = current(env, settled_rows, *args, **kwargs)
        return reconcile(
            env,
            settled_rows,
            result,
            provider=post_provider,
            critic=post_critic,
            store=kwargs.get("store"),
            fixer=kwargs.get("fixer"),
            allow_live=(not explicit_host or post_provider is not None),
        )

    host_with_plan._aegis_postlearning_host = True
    host_with_plan._aegis_postlearning_original = current
    host.host = host_with_plan
