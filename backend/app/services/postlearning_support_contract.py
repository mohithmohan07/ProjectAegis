"""Apply the language plan's support-block verdicts to Post-Learning rows.

The language-plan author already decides which concept owns every Warm-up,
performance cue, Word Basket, device box, grammar/listening/phonics component
and other source-owned support occurrence. That verdict used to stop inside
the plan artifact. This contract carries it through the shared pipeline:

* a task-bearing threaded block becomes a Container-02 activity/info-hub item
  without losing its existing Container-03 question identity;
* after Polish has converged the teaching prose, the exact source block text is
  appended to the destination concept's Activity/Info Hub; and
* explicitly non-teaching blocks remain in a row-private audit record rather
  than becoming concepts or disappearing.

The semantic act is the model's recorded ``threaded_components`` decision.
This module performs only ID validation, exact-once transport, source-text
copying and envelope resealing. It never guesses from labels, subjects,
positions or vocabulary.
"""
from __future__ import annotations

import copy
import importlib
from functools import wraps
from typing import Any, Mapping, Sequence

from . import language_topology as topology
from . import postlearning_formation_contract as post


CONTRACT_VERSION = 3
SUPPORT_VERSION = "post-language-support-3"
SUPPORT_FIELD = "_aegis_language_threaded_support"
NON_TEACHING_FIELD = "_aegis_language_non_teaching_blocks"
SUPPORT_AUDIT_FIELDS = frozenset({SUPPORT_FIELD, NON_TEACHING_FIELD})


def _normal(value: object) -> str:
    return " ".join(str(value or "").split())


def _block_catalog(env: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("block_id") or ""): copy.deepcopy(dict(row))
        for row in (env.get("canonical") or {}).get("blocks") or []
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }


def _block_text(block: Mapping[str, Any]) -> str:
    return str(
        block.get("display_text") or block.get("raw_text") or ""
    ).strip()


def _task_ids(block: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("task_ids", "source_task_ids"):
        for value in block.get(field) or []:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _threaded_block_ids(plan: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("block_id") or "").strip()
        for row in plan.get("threaded_components") or []
        if isinstance(row, Mapping) and str(row.get("block_id") or "").strip()
    }


def support_records(env: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Validated threaded components keyed by destination plan concept."""
    plan = post.language_plan(env)
    entries = post.plan_entries(env)
    if plan is None or not entries:
        return {}
    blocks = _block_catalog(env)
    known_concepts = {
        str(entry["plan_concept_id"]) for entry in entries
    }
    first_plan_concept_id = str(entries[0]["plan_concept_id"])
    seen_blocks: dict[str, str] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for position, raw in enumerate(plan.get("threaded_components") or [], start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("language plan contains a non-object threaded component")
        block_id = str(raw.get("block_id") or "").strip()
        destination = str(
            raw.get("destination_plan_concept_id") or ""
        ).strip()
        placement_context = str(
            raw.get("placement_context") or ""
        ).strip()
        if block_id not in blocks:
            raise ValueError(
                f"threaded component {position} names unknown block {block_id!r}"
            )
        if destination not in known_concepts:
            raise ValueError(
                f"threaded component {block_id} names unknown destination "
                f"{destination!r}"
            )
        if not _normal(raw.get("skill")) or not _normal(raw.get("rationale")):
            raise ValueError(
                f"threaded component {block_id} has no skill or rationale"
            )
        if placement_context not in topology.THREADING_PLACEMENT_CONTEXTS:
            raise ValueError(
                f"threaded component {block_id} has invalid placement_context "
                f"{placement_context!r}"
            )
        if (
            placement_context == "opening_pre_reading"
            and destination != first_plan_concept_id
        ):
            raise ValueError(
                f"threaded component {block_id} is opening_pre_reading but "
                f"routes to {destination!r}; opening support must route to "
                f"the first plan concept {first_plan_concept_id!r}"
            )
        prior = seen_blocks.get(block_id)
        if prior is not None:
            raise ValueError(
                f"threaded component repeats source block {block_id}; one "
                "source occurrence requires exactly one threading verdict"
            )
        seen_blocks[block_id] = destination
        block = blocks[block_id]
        grouped.setdefault(destination, []).append({
            "block_id": block_id,
            "text": _block_text(block),
            "placement_context": placement_context,
            "skill": _normal(raw.get("skill")),
            "rationale": _normal(raw.get("rationale")),
            "task_ids": _task_ids(block),
        })
    return grouped


def non_teaching_records(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The plan's explicitly non-teaching source occurrences, in order."""
    plan = post.language_plan(env)
    if plan is None:
        return []
    blocks = _block_catalog(env)
    threaded = _threaded_block_ids(plan)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in plan.get("non_teaching_block_ids") or []:
        block_id = str(value or "").strip()
        if not block_id or block_id in seen:
            continue
        if block_id not in blocks:
            raise ValueError(
                f"language plan non-teaching block {block_id!r} is unknown"
            )
        if block_id in threaded:
            raise ValueError(
                f"language plan block {block_id} is both threaded support and "
                "non-teaching; one source occurrence needs one support verdict"
            )
        seen.add(block_id)
        records.append({
            "block_id": block_id,
            "text": _block_text(blocks[block_id]),
        })
    return records


def prepare_envelope(env: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize the plan and expose task-bearing support as dual-role hubs."""
    from .phase3 import envelope as envelope_mod

    materialized = post.materialize_envelope(env)
    grouped = support_records(materialized)
    if not grouped and post.language_plan(materialized) is None:
        return materialized

    threaded_task_ids = {
        task_id
        for records in grouped.values()
        for record in records
        for task_id in record.get("task_ids") or []
    }
    out = copy.deepcopy(materialized)
    promoted_qids: list[str] = []
    inventory = copy.deepcopy(dict(out.get("inventory") or {}))
    items: list[dict[str, Any]] = []
    for position, raw in enumerate(inventory.get("items") or [], start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"question inventory item {position} is not an object"
            )
        item = copy.deepcopy(dict(raw))
        task_id = str(
            item.get("_acsd_task_id") or item.get("task_id") or ""
        ).strip()
        if task_id and task_id in threaded_task_ids:
            # Non-destructive dual role: the QID remains in Container 03 and
            # joins Container 02 through the same recorded identity.
            item["_activity_origin"] = True
            qid = str(item.get("qid") or "").strip()
            if qid and qid not in promoted_qids:
                promoted_qids.append(qid)
        items.append(item)
    inventory["items"] = items
    out["inventory"] = inventory
    metadata = copy.deepcopy(dict(out.get("metadata") or {}))
    metadata["post_language_support_materialization"] = {
        "version": SUPPORT_VERSION,
        "threaded_block_count": sum(len(rows) for rows in grouped.values()),
        "destination_concept_count": len(grouped),
        "promoted_hub_qids": promoted_qids,
        "non_teaching_block_count": len(non_teaching_records(out)),
    }
    out["metadata"] = metadata
    out["envelope_sha256"] = envelope_mod.seal_sha256(out)
    return envelope_mod.validate(out)


def _append_hub(details: object, source_bodies: Sequence[str]) -> str:
    """Append exact source bodies to the canonical Activity/Info Hub section."""
    from . import concept_refiner as cr

    bodies = [str(value).strip() for value in source_bodies if str(value).strip()]
    if not bodies:
        return str(details or "")
    sections = cr.split_sections(str(details or ""))
    index = next(
        (
            position
            for position, (label, _content) in enumerate(sections)
            if cr.is_activity_hub_label(label)
        ),
        -1,
    )
    if index >= 0:
        label, existing = sections[index]
        existing_text = existing.strip()
        pieces = [existing_text] if existing_text else []
        for body in bodies:
            # A replay sees the combined Hub body as one section. Membership
            # in that exact source-owned text, not list-shape, is the idempotent
            # guard; no semantic comparison or normalization is involved.
            if body not in existing_text:
                pieces.append(body)
        sections[index] = (label, "\n\n".join(pieces))
    else:
        # Description (including its line-broken mastery) remains first; Hub
        # precedes Types and learner analysis when those are added in Assemble.
        sections.insert(1 if sections else 0, (
            "Activity/Info Hub",
            "\n\n".join(dict.fromkeys(bodies)),
        ))
    return cr.join_sections(sections)


def attach_support(
    env: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach every recorded support block after Polish, before Assemble."""
    grouped = support_records(env)
    non_teaching = non_teaching_records(env)
    out = [copy.deepcopy(dict(row)) for row in rows]
    if not grouped and not non_teaching:
        return out

    row_by_plan_id: dict[str, dict[str, Any]] = {}
    for row in out:
        identity = row.get(post.PLAN_IDENTITY_FIELD)
        if not isinstance(identity, Mapping):
            continue
        plan_id = str(identity.get("plan_concept_id") or "").strip()
        if plan_id:
            if plan_id in row_by_plan_id:
                raise ValueError(
                    f"two Post-Learning rows claim language plan concept {plan_id}"
                )
            row_by_plan_id[plan_id] = row

    for plan_id, records in grouped.items():
        target = row_by_plan_id.get(plan_id)
        if target is None:
            raise ValueError(
                f"threaded support destination {plan_id} has no Post row"
            )
        target["concept_details"] = _append_hub(
            target.get("concept_details"),
            [str(record.get("text") or "") for record in records],
        )
        target[SUPPORT_FIELD] = [
            {
                "block_id": record["block_id"],
                "placement_context": record["placement_context"],
                "skill": record["skill"],
                "rationale": record["rationale"],
            }
            for record in records
        ]

    if non_teaching:
        carrier = next(
            (
                row for row in reversed(out)
                if str(row.get(post.SEMANTIC_ROLE_FIELD) or "")
                == "chapter_culmination"
            ),
            out[-1] if out else None,
        )
        if carrier is None:
            raise ValueError(
                "language plan records non-teaching blocks but produced no Post row"
            )
        carrier[NON_TEACHING_FIELD] = copy.deepcopy(non_teaching)
    return out


def _register_release_audit_fields() -> None:
    try:
        release = importlib.import_module("app.services.build_concepts_release")
    except Exception:
        return
    current = set(getattr(release, "_RELEASE_AUDIT_FIELDS", frozenset()))
    release._RELEASE_AUDIT_FIELDS = frozenset(current | set(SUPPORT_AUDIT_FIELDS))


def install() -> None:
    """Install support transport after Post formation and before Pre repair."""
    envelope = importlib.import_module("app.services.phase3.envelope")
    runner = importlib.import_module("app.services.phase3.runner")
    polish = importlib.import_module("app.services.phase3.polish")

    # Fresh runs must persist the already-materialized envelope. The runner
    # wrapper below still upgrades legacy stored envelopes on read.
    current_build = envelope.build
    if not getattr(current_build, "_aegis_postlearning_support_build", False):
        @wraps(current_build)
        def build_with_support(*args, **kwargs):
            return prepare_envelope(current_build(*args, **kwargs))

        build_with_support._aegis_postlearning_support_build = True
        build_with_support._aegis_postlearning_support_original = current_build
        envelope.build = build_with_support

    current_run = runner.run
    if not getattr(current_run, "_aegis_postlearning_support_runner", False):
        @wraps(current_run)
        def run_with_support(env, *args, **kwargs):
            return current_run(prepare_envelope(env), *args, **kwargs)

        run_with_support._aegis_postlearning_support_runner = True
        run_with_support._aegis_postlearning_support_original = current_run
        runner.run = run_with_support

    current_polish = polish.polish
    if not getattr(current_polish, "_aegis_postlearning_support_polish", False):
        @wraps(current_polish)
        def polish_with_support(env, rows, *args, **kwargs):
            polished = current_polish(env, rows, *args, **kwargs)
            return attach_support(env, polished)

        polish_with_support._aegis_postlearning_support_polish = True
        polish_with_support._aegis_postlearning_support_original = current_polish
        polish.polish = polish_with_support

    _register_release_audit_fields()
