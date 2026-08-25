"""Bind task-bearing threaded support to the language-plan destination.

``postlearning_support_contract`` exposes a task-bearing support occurrence to
Container 02 without removing its Container-03 QID. This companion contract
closes the destination seam: Place may still judge every ordinary pooled item,
but a language-plan support verdict already chose the concept for this exact
occurrence, so its Hub marker is projected to that planned concept after Place.

The existing activity renderer owns task presentation and emits its compact,
source-owned note and visual assets. To avoid rendering the same instruction
both as a whole copied support block and as that activity note, the exact text
of task-bearing support blocks is removed from the pre-Assemble Hub; non-task
Word Baskets, device explanations, grammar tables and similar source blocks
remain copied whole.

No label or content meaning is inspected. Task IDs, QIDs, plan concept IDs and
exact source strings are the only mechanics used here.
"""
from __future__ import annotations

import copy
import importlib
from functools import wraps
from typing import Any, Mapping, Sequence

from . import postlearning_formation_contract as post
from . import postlearning_support_contract as support


CONTRACT_VERSION = 1


def _normal(value: object) -> str:
    return " ".join(str(value or "").split())


def _qids_by_task_id(env: Mapping[str, Any]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for raw in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(raw, Mapping):
            continue
        task_id = str(
            raw.get("_acsd_task_id") or raw.get("task_id") or ""
        ).strip()
        qid = str(raw.get("qid") or "").strip()
        if task_id and qid:
            output.setdefault(task_id, [])
            if qid not in output[task_id]:
                output[task_id].append(qid)
    return output


def planned_hub_qids(env: Mapping[str, Any]) -> dict[str, str]:
    """Return QID -> destination plan concept for task-bearing support."""
    qids_by_task = _qids_by_task_id(env)
    destinations: dict[str, str] = {}
    for plan_id, records in support.support_records(env).items():
        for record in records:
            for task_id in record.get("task_ids") or []:
                for qid in qids_by_task.get(str(task_id), []):
                    prior = destinations.get(qid)
                    if prior is not None and prior != plan_id:
                        raise ValueError(
                            f"threaded support routes Hub QID {qid} to both "
                            f"{prior} and {plan_id}; one activity identity "
                            "needs one destination"
                        )
                    destinations[qid] = plan_id
    return destinations


def _place_ids_by_plan(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    from .phase3 import place

    output: dict[str, str] = {}
    for concept_id, row in zip(place.mint_concept_ids(list(rows)), rows):
        identity = row.get(post.PLAN_IDENTITY_FIELD)
        if not isinstance(identity, Mapping):
            continue
        plan_id = str(identity.get("plan_concept_id") or "").strip()
        if not plan_id:
            continue
        if plan_id in output:
            raise ValueError(
                f"two rows claim language plan concept {plan_id} before Place"
            )
        output[plan_id] = str(concept_id)
    return output


def enforce_place_result(
    env: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the model-authored support destination onto Hub placements."""
    destinations = planned_hub_qids(env)
    if not destinations:
        return copy.deepcopy(dict(result))
    place_ids = _place_ids_by_plan(rows)
    out = copy.deepcopy(dict(result))
    placements = copy.deepcopy(dict(out.get("hub_placements") or {}))
    rationales = copy.deepcopy(dict(out.get("rationales") or {}))
    for qid, plan_id in destinations.items():
        concept_id = place_ids.get(plan_id)
        if concept_id is None:
            raise ValueError(
                f"threaded Hub destination {plan_id} has no row at Place"
            )
        placements[qid] = concept_id
        rationales[qid] = (
            "The language topology plan threaded this exact task-bearing "
            f"support occurrence to {plan_id}; the recorded plan verdict "
            "is the Hub-placement authority."
        )
    out["hub_placements"] = placements
    out["rationales"] = rationales
    return out


def _task_bodies_by_plan(env: Mapping[str, Any]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for plan_id, records in support.support_records(env).items():
        bodies = [
            str(record.get("text") or "").strip()
            for record in records
            if record.get("task_ids") and str(record.get("text") or "").strip()
        ]
        if bodies:
            output[plan_id] = list(dict.fromkeys(bodies))
    return output


def strip_task_support_bodies(
    env: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Leave task rendering to the activity renderer; keep other support whole."""
    from . import concept_refiner as cr

    bodies_by_plan = _task_bodies_by_plan(env)
    out = [copy.deepcopy(dict(row)) for row in rows]
    if not bodies_by_plan:
        return out
    for row in out:
        identity = row.get(post.PLAN_IDENTITY_FIELD)
        if not isinstance(identity, Mapping):
            continue
        plan_id = str(identity.get("plan_concept_id") or "").strip()
        bodies = bodies_by_plan.get(plan_id)
        if not bodies:
            continue
        sections = cr.split_sections(str(row.get("concept_details") or ""))
        rebuilt: list[tuple[str, str]] = []
        for label, content in sections:
            if not cr.is_activity_hub_label(label):
                rebuilt.append((label, content))
                continue
            remaining = str(content or "")
            for body in bodies:
                remaining = remaining.replace(body, "")
            remaining = remaining.strip(" \n")
            if remaining:
                rebuilt.append((label, remaining))
        row["concept_details"] = cr.join_sections(rebuilt)
    return out


def install() -> None:
    """Install after support transport, before the Pre-Learning wrapper."""
    place = importlib.import_module("app.services.phase3.place")
    polish = importlib.import_module("app.services.phase3.polish")

    current_place = place.place
    if not getattr(current_place, "_aegis_postlearning_support_route", False):
        @wraps(current_place)
        def place_with_planned_hubs(env, rows, *args, **kwargs):
            result = current_place(env, rows, *args, **kwargs)
            return enforce_place_result(env, rows, result)

        place_with_planned_hubs._aegis_postlearning_support_route = True
        place_with_planned_hubs._aegis_postlearning_support_original = current_place
        place.place = place_with_planned_hubs

    current_polish = polish.polish
    if not getattr(current_polish, "_aegis_postlearning_support_dedupe", False):
        @wraps(current_polish)
        def polish_without_duplicate_task_bodies(env, rows, *args, **kwargs):
            polished = current_polish(env, rows, *args, **kwargs)
            return strip_task_support_bodies(env, polished)

        polish_without_duplicate_task_bodies._aegis_postlearning_support_dedupe = True
        polish_without_duplicate_task_bodies._aegis_postlearning_support_original = current_polish
        polish.polish = polish_without_duplicate_task_bodies
