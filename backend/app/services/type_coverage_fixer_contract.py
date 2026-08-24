"""Replace residual semantic Type fabrication with one recorded Fixer decision.

Type mining already spends its authored broad repairs and focused additive
repairs.  The final fallback used to cross the Rule-1 boundary: code inspected
question wording/source kind and *authored* a new Type title, Case title and
semantic task pattern from regex/verb tables.  That is meaning, not mechanics.

This production contract keeps the exact-cover hard gate but changes the last
recovery step:

* the remaining missed QIDs, their complete canonical wording/context and the
  immutable existing Type metadata go to The Fixer once;
* the existing focused-delta validator remains the mechanical checker — every
  missed QID must be represented exactly once, existing assignments cannot
  move, and public Example wording is restored from the canonical inventory;
* the decision is content-addressed in the same durable Phase-3 Fixer store;
* a contract-satisfying result ships with visible review flags on the affected
  mined Types, and that provenance follows the QIDs through the immediately
  following semantic Type-consolidation pass; and
* when no live Fixer exists or it cannot satisfy the checker, no semantic value
  is fabricated.  The existing exact-cover gate refuses the run as genuine
  protocol/provider impossibility.

No subject, grade, source-kind, keyword or text-shape rule chooses a Type.
"""
from __future__ import annotations

import copy
import hashlib
from functools import wraps
from typing import Any, Mapping

from . import generation
from . import progress
from .phase3 import fixer as fixer_mod
from .phase3 import kernel


CONTRACT_VERSION = 1
DECISION_KIND = "fixer.type_coverage"


def _normal(value: object) -> str:
    return " ".join(str(value or "").split())


def _qid(item: object) -> str:
    if isinstance(item, Mapping):
        return _normal(item.get("qid"))
    return _normal(item)


def _source_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Full semantic evidence needed for the decision, no positional guessing."""
    return {
        "qid": _qid(item),
        "source_kind": _normal(item.get("source_kind")),
        "source_label": _normal(item.get("source_label")),
        "topic_hint": _normal(item.get("topic_hint")),
        "task": generation._inventory_task_text(dict(item)),
        "shared_context": str(item.get("shared_context") or ""),
        "requires_context": bool(item.get("requires_context")),
        "requires_visual": bool(item.get("requires_visual")),
        "image_urls": [
            str(url) for url in item.get("image_urls") or []
            if str(url or "").strip()
        ],
        "content_objects": copy.deepcopy(item.get("content_objects") or {}),
    }


def _candidate_from_response(
    response: Mapping[str, Any],
    *,
    types: list[dict],
    missed_items: list[dict],
    inventory: dict,
) -> list[dict]:
    delta = generation._validate_focused_type_delta(
        dict(response),
        missed_items=missed_items,
        existing_types=types,
    )
    return generation._normalize_mined_type_candidate(
        generation._merge_focused_type_delta(types, delta),
        inventory,
    )


def _checker(
    *,
    types: list[dict],
    missed_items: list[dict],
    inventory: dict,
):
    expected = {_qid(item) for item in missed_items if _qid(item)}
    before = generation._inventory_assignment_counts(types)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        if not _normal(response.get("rationale")):
            defects.append("rationale is required")
        try:
            candidate = _candidate_from_response(
                response,
                types=types,
                missed_items=missed_items,
                inventory=inventory,
            )
        except Exception as exc:  # mechanical response contract
            defects.append(f"invalid additive Type delta: {exc}")
            return defects

        after = generation._inventory_assignment_counts(candidate)
        altered = sorted(
            qid for qid, count in before.items()
            if count and after.get(qid, 0) != count
        )
        if altered:
            defects.append(
                "the Fixer changed previously authored QID assignment(s): "
                + ", ".join(altered[:12])
            )
        unresolved = sorted(
            qid for qid in expected if after.get(qid, 0) != 1
        )
        if unresolved:
            defects.append(
                "every residual missed QID must be assigned exactly once; "
                "unresolved: " + ", ".join(unresolved[:12])
            )
        duplicates = generation._duplicate_inventory_assignments(
            inventory, candidate
        )
        if duplicates:
            defects.append(
                "the Fixer produced duplicate QID assignment(s): "
                + ", ".join(
                    qid for row in duplicates[:12]
                    if (qid := _qid(row))
                )
            )
        return defects

    return check


def _owned_qids(mtype: Mapping[str, Any]) -> set[str]:
    owned = {
        str(qid or "").strip()
        for qid in mtype.get("source_question_ids") or []
        if str(qid or "").strip()
    }
    for case in mtype.get("case_prompts") or []:
        if not isinstance(case, dict):
            continue
        for example in generation._case_examples(case):
            qid = str(example.get("source_question_id") or "").strip()
            if qid:
                owned.add(qid)
    return owned


def _flag_affected_types(
    types: list[dict],
    *,
    missed_qids: set[str],
    decision: Mapping[str, Any],
) -> list[dict]:
    out = copy.deepcopy(types)
    rationale = _normal((decision.get("response") or {}).get("rationale"))[:240]
    decision_key = str(decision.get("key") or "")
    for mtype in out:
        if not isinstance(mtype, dict):
            continue
        recovered = sorted(_owned_qids(mtype) & missed_qids)
        if not recovered:
            continue
        flags = list(mtype.get("review_flags") or [])
        flag = (
            "fixer: residual Type coverage was unresolved after authored "
            "repairs; assigned " + ", ".join(recovered)
            + (f" — {rationale}" if rationale else "")
        )
        if flag not in flags:
            flags.append(flag)
        mtype["review_flags"] = flags
        mtype["_fixer_type_coverage"] = {
            "decision_key": decision_key,
            "qids": recovered,
            "rationale": rationale,
        }
    return out


def _carry_fixer_provenance(
    before: list[dict], after: list[dict],
) -> list[dict]:
    """Keep the recorded Fixer decision attached after semantic Type merging."""
    audit_by_qid: dict[str, dict[str, Any]] = {}
    for mtype in before:
        if not isinstance(mtype, Mapping):
            continue
        audit = mtype.get("_fixer_type_coverage")
        if not isinstance(audit, Mapping):
            continue
        for qid in audit.get("qids") or []:
            value = str(qid or "").strip()
            if value:
                audit_by_qid[value] = copy.deepcopy(dict(audit))
    if not audit_by_qid:
        return after

    out = copy.deepcopy(after)
    for mtype in out:
        if not isinstance(mtype, dict):
            continue
        recovered = sorted(_owned_qids(mtype) & set(audit_by_qid))
        if not recovered:
            continue
        grouped: dict[str, dict[str, Any]] = {}
        for qid in recovered:
            audit = audit_by_qid[qid]
            key = str(audit.get("decision_key") or "")
            row = grouped.setdefault(key, {
                "decision_key": key,
                "qids": [],
                "rationale": str(audit.get("rationale") or ""),
            })
            row["qids"].append(qid)
        history = list(mtype.get("_fixer_type_coverage_history") or [])
        for audit in grouped.values():
            if audit not in history:
                history.append(audit)
            flag = (
                "fixer: residual Type coverage provenance retained after "
                "semantic Type consolidation for "
                + ", ".join(audit["qids"])
                + (
                    f" — {audit['rationale']}"
                    if audit.get("rationale") else ""
                )
            )
            flags = list(mtype.get("review_flags") or [])
            if flag not in flags:
                flags.append(flag)
            mtype["review_flags"] = flags
        mtype["_fixer_type_coverage_history"] = history
        if len(history) == 1:
            mtype["_fixer_type_coverage"] = copy.deepcopy(history[0])
    return out


def install() -> None:
    if getattr(generation, "_TYPE_COVERAGE_FIXER_CONTRACT_VERSION", 0) >= (
        CONTRACT_VERSION
    ):
        return

    original = generation._append_deterministic_type_fallbacks
    original_consolidate = generation._consolidate_semantic_types_via_api
    generation._LEGACY_DETERMINISTIC_TYPE_FALLBACKS = original
    generation._PRE_FIXER_TYPE_CONSOLIDATION = original_consolidate

    @wraps(original)
    def recover_with_fixer(
        types: list[dict], *, missed_items: list[dict], inventory: dict,
    ) -> tuple[list[dict], int]:
        missed = [
            copy.deepcopy(item)
            for item in missed_items
            if isinstance(item, dict) and _qid(item)
        ]
        if not missed:
            return types, 0

        provider = fixer_mod.default_provider()
        if provider is None:
            progress.log(
                "Type Mining residual coverage has no live Fixer; Aegis will "
                "not fabricate semantic Types deterministically. The exact "
                "coverage gate remains closed.",
                level="warning",
            )
            return types, 0

        missed_qids = {_qid(item) for item in missed}
        payload = {
            "fixer": True,
            "stage": "type_mining_residual_coverage",
            "blocked_check": [
                "authored Type mining and focused repairs still leave source "
                "QIDs without exactly one Type/Case Example placement: "
                + ", ".join(sorted(missed_qids))
            ],
            "contract": {
                "kind": DECISION_KIND,
                "rule": (
                    "Classify every residual source QID into a precise reusable "
                    "Type and Case using its supplied source evidence. Return "
                    "ONLY an additive delta: existing Type/Case semantics and "
                    "already assigned QIDs are immutable. Every listed QID "
                    "must appear exactly once in source_question_ids and as "
                    "exactly one Example under exactly one Case. You may add a "
                    "Case to an existing Type by its exact type_id or add a new "
                    "Type. Never omit a residual QID and never claim any other "
                    "QID. The checker restores public Example wording from the "
                    "canonical inventory. Response schema: {\"types\": [...], "
                    "\"rationale\": \"...\"}."
                ),
            },
            "residual_inventory_items": [_source_item(item) for item in missed],
            "existing_type_metadata": generation._compact_mined_type_metadata(
                types
            ),
        }
        # Payload hashing already binds the exact inventory evidence and current
        # Type metadata.  The stable source-contract digest is an additional
        # envelope identity when available; no content meaning is inferred.
        source_contract = inventory.get("source_contract")
        source_contract = (
            dict(source_contract) if isinstance(source_contract, Mapping) else {}
        )
        envelope_sha = str(
            source_contract.get("source_contract_hash")
            or source_contract.get("source_sha256")
            or ""
        )
        if not envelope_sha:
            envelope_sha = hashlib.sha256(
                repr(sorted(missed_qids)).encode("utf-8")
            ).hexdigest()

        try:
            decision = kernel.decide(
                kind=DECISION_KIND,
                unit_id="residual:" + hashlib.sha256(
                    "\n".join(sorted(missed_qids)).encode("utf-8")
                ).hexdigest()[:16],
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=provider,
                checker=_checker(
                    types=types,
                    missed_items=missed,
                    inventory=inventory,
                ),
                store=generation._phase3_fixer_store(),
                policy_version=fixer_mod.FIXER_POLICY_VERSION,
                provider_label="The Fixer",
            )
        except kernel.ContractError as exc:
            progress.log(
                "The Fixer could not satisfy residual Type exact coverage: "
                f"{exc}. No deterministic semantic fallback was applied; the "
                "existing exact-coverage gate will refuse the unresolved run.",
                level="warning",
            )
            return types, 0

        candidate = _candidate_from_response(
            decision.get("response") or {},
            types=types,
            missed_items=missed,
            inventory=inventory,
        )
        candidate = _flag_affected_types(
            candidate,
            missed_qids=missed_qids,
            decision=decision,
        )
        progress.log(
            "The Fixer resolved residual Type coverage for "
            f"{len(missed_qids)} source QID(s); no semantic Type was authored "
            "by deterministic fallback code.",
            level="success",
        )
        return candidate, len(missed_qids)

    @wraps(original_consolidate)
    def consolidate_with_fixer_provenance(
        mined_types: dict, *, inventory: dict, meta: dict,
    ) -> dict:
        before = copy.deepcopy((mined_types or {}).get("types") or [])
        result = original_consolidate(
            mined_types, inventory=inventory, meta=meta
        )
        if not isinstance(result, dict):
            return result
        result = copy.deepcopy(result)
        result["types"] = _carry_fixer_provenance(
            before,
            list(result.get("types") or []),
        )
        return result

    generation._append_deterministic_type_fallbacks = recover_with_fixer
    generation._consolidate_semantic_types_via_api = (
        consolidate_with_fixer_provenance
    )
    generation._TYPE_COVERAGE_FIXER_CONTRACT_VERSION = CONTRACT_VERSION
