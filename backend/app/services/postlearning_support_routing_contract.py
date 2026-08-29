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


def _planned_hub_destination_claims(
    env: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """All plan-concept claims per Hub QID, conflicts kept, evidence carried.

    Returns ``(destinations, conflicts, evidence)``: unique claims land in
    ``destinations``; a QID claimed by MORE than one plan concept lands in
    ``conflicts`` (ordered plan ids) with each claimant's support text in
    ``evidence`` — the full context a recorded conflict decision needs.
    """
    qids_by_task = _qids_by_task_id(env)
    claims: dict[str, list[str]] = {}
    evidence: dict[str, dict[str, list[str]]] = {}
    for plan_id, records in support.support_records(env).items():
        for record in records:
            for task_id in record.get("task_ids") or []:
                for qid in qids_by_task.get(str(task_id), []):
                    plans = claims.setdefault(qid, [])
                    if plan_id not in plans:
                        plans.append(plan_id)
                    texts = evidence.setdefault(qid, {}).setdefault(
                        plan_id, []
                    )
                    text = _normal(record.get("text"))
                    if text and text not in texts:
                        texts.append(text)
    destinations = {
        qid: plans[0] for qid, plans in claims.items() if len(plans) == 1
    }
    conflicts = {
        qid: list(plans) for qid, plans in claims.items() if len(plans) > 1
    }
    return destinations, conflicts, evidence


def _conflict_error(qid: str, plans: list[str]) -> ValueError:
    return ValueError(
        f"threaded support routes Hub QID {qid} to both "
        f"{plans[0]} and {plans[1]}; one activity identity "
        "needs one destination"
    )


def planned_hub_qids(env: Mapping[str, Any]) -> dict[str, str]:
    """Return QID -> destination plan concept for task-bearing support."""
    destinations, conflicts, _ = _planned_hub_destination_claims(env)
    for qid, plans in sorted(conflicts.items()):
        raise _conflict_error(qid, plans)
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
    *,
    critic: Any = None,
    store: Any = None,
    fixer: Any = None,
) -> dict[str, Any]:
    """Project the model-authored support destination onto Hub placements.

    A Hub QID claimed by TWO plan concepts is a decidable mid-run block,
    not an impossibility: per docs/aegis-restructure.md §8.2/Q13 it goes
    to The Fixer for ONE recorded, flagged, content-addressed decision
    with the claimants' own support evidence, and the run completes.
    [measured] job "The School Bell Rings Again..." (owner report,
    2026-08-29): the former unconditional raise here ended the run
    incomplete at Place — Outputs 02/04 unbuildable — over a conflict
    one recorded decision resolves. Without a Fixer/store in scope
    (offline callers, tests) the block still raises exactly as before.
    """
    destinations, conflicts, evidence = _planned_hub_destination_claims(env)
    conflict_flags: dict[str, str] = {}
    if conflicts:
        from . import progress
        from .phase3 import fixer as fixer_mod
        from .phase3 import kernel

        if fixer is None:
            # Production reaches this wrapper with fixer=None — the
            # runner's providers dict is test-only injection, and each
            # stage resolves its own live Fixer internally (place.py's
            # ``fixer = fixer or _live_fixer``). Resolve the deployment's
            # Fixer the same way; dry/test runs still get None and keep
            # the fail-closed raise. [measured] 2026-08-29: without this,
            # the live poem run raised through the fallback even though
            # a live Fixer existed one frame below.
            fixer = fixer_mod.default_provider()
        if fixer is not None and store is None:
            # A recorded decision needs a store; without a durable one
            # (no artifact dir), an ephemeral store still records the
            # decision onto the release flags — re-asked on a later
            # resume rather than lost, never a run-stopping raise.
            store = kernel.DecisionStore()
        if fixer is None or store is None:
            for qid, plans in sorted(conflicts.items()):
                raise _conflict_error(qid, plans)

        for qid, plans in sorted(conflicts.items()):
            payload = {
                "blocked_check": (
                    f"threaded support routes Hub QID {qid} to "
                    f"{len(plans)} planned destinations {plans}; one "
                    "activity identity needs one destination"
                ),
                "contract": (
                    "postlearning support routing: each task-bearing "
                    "support occurrence has exactly ONE planned "
                    "destination concept. Choose, from the candidates "
                    "only, the plan concept this exact occurrence "
                    "belongs to, judged from each claimant's own "
                    "support text."
                ),
                "response_schema": {
                    "destination_plan_id": "", "rationale": "",
                },
                "qid": qid,
                "candidates": [
                    {
                        "plan_concept_id": plan_id,
                        "support_text": list(
                            (evidence.get(qid) or {}).get(plan_id) or []
                        ),
                    }
                    for plan_id in plans
                ],
            }

            def _checker(response, _plans=tuple(plans)):
                choice = str(
                    (response or {}).get("destination_plan_id") or ""
                ).strip()
                if choice not in _plans:
                    return [
                        "destination_plan_id must be exactly one of "
                        f"{list(_plans)}"
                    ]
                return []

            decision = kernel.decide(
                kind="hub_route_conflict",
                unit_id=str(qid),
                envelope_sha256=str(env.get("envelope_sha256") or ""),
                payload=payload,
                provider=fixer,
                checker=_checker,
                critic=critic,
                store=store,
                policy_version="hub-route-conflict-1",
            )
            choice = str(
                (decision.get("response") or {}).get("destination_plan_id")
                or ""
            ).strip()
            destinations[qid] = choice
            conflict_flags[qid] = (
                f"hub-route-conflict: {len(plans)} planned destinations "
                f"({', '.join(plans)}) claimed this task-bearing support "
                f"occurrence; The Fixer chose {choice} in one recorded "
                "decision — review."
            )
            progress.log(
                f"Hub QID {qid} was claimed by {len(plans)} planned "
                f"destinations; The Fixer routed it to {choice} in one "
                "recorded decision (flagged for review).",
                level="warning",
            )
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
    if conflict_flags:
        review_flags = copy.deepcopy(dict(out.get("review_flags") or {}))
        for qid, flag in conflict_flags.items():
            entries = list(review_flags.get(qid) or [])
            entries.append(flag)
            review_flags[qid] = entries
        out["review_flags"] = review_flags
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
            return enforce_place_result(
                env,
                rows,
                result,
                critic=kwargs.get("critic"),
                store=kwargs.get("store"),
                fixer=kwargs.get("fixer"),
            )

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
