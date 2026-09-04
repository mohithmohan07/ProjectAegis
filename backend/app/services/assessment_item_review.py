"""The joint per-item review (Master Governing Contract v2.0 §27 step 6).

One independent model review per finished assessment item, after its cell,
materialization, answer-space verdict and marking have all been recorded:
it sees the source atom, the blueprint cell, the complete item, the
Open/Specific verdict and the mark decomposition together and jointly
verifies question fidelity, lane and category fit, answer correctness, the
answer space, model-answer completeness, criterion atomicity and
bidirectional coverage, accepted equivalents, rubric-tag containment and
the arithmetic.

It is an AUDITOR (register Q10): its dissent becomes review flags on the
candidate and rides the release for the reviewer; it never rewrites,
retries or gates anything.  It replaces the four separate per-decision
critics that used to audit the same item piecemeal (register Q26), so the
item is audited once, whole, rather than four times in fragments.

Mechanics only in code: the response shape is checked, the verdict is an
enum, and a review that cannot run leaves a named flag rather than a
blocked Master.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .. import config
from . import assessment_profile
from .phase3 import kernel

ITEM_REVIEW_POLICY_VERSION = "assessment-item-review-1"
AUDIT_FIELD = "_aegis_assessment_item_review"
WARNING = "assessment_item_review"
UNAVAILABLE_WARNING = "assessment_item_review_unavailable"

_PROMPT_CACHE_STABLE_KEYS = (
    "stage",
    "rules",
    "metadata",
    "assessment_format_policy",
    "rubric_tag_policy",
)

ITEM_REVIEW_SYSTEM = (
    "You are the independent joint reviewer of ONE finished Aegis "
    "assessment item (Master Governing Contract v2.0 §27 step 6). You see "
    "the source atom (when the item is source-owned), the recorded blueprint "
    "cell, the complete materialized item, its recorded Open/Specific "
    "answer-space verdict and its recorded mark decomposition. Verify, "
    "jointly and against the source evidence: (1) source fidelity — the "
    "item preserves the source task's demand, scope, response mode, answer "
    "space and media dependency; polishing added no requirement and lost no "
    "required context; (2) lane and category — the response mechanics match "
    "the sheet and the exact category (a closed one-key option set is "
    "Objective; deterministic blanks and every True or False item are "
    "Subjective with placeholder-bound answers; constructed responses are "
    "Descriptive); (3) answer correctness at the accepted grade scope; "
    "(4) the Open/Specific verdict follows from the whole item, model answer "
    "and unweighted rubric, never from a command word or category; (5) the "
    "model answer is complete and learner-facing, identical in "
    "display_answer and answer_explanation for Descriptive items, and free "
    "of rubric narration, criterion tags, marks or evaluator instructions; "
    "an Objective explanation opens with the exact correct answer text and "
    "no option letter or number; (6) every criterion is one observable, "
    "question-specific credit-bearing demand worth 0.5 or 1, nothing asked "
    "is unscored, nothing unasked is credited, nothing is double-counted, "
    "every criterion appears in the model answer and every required "
    "model-answer component is scored; (7) rubric-tag containment follows "
    "the supplied rubric_tag_policy exactly; (8) the arithmetic — option, "
    "slot, parent and child sums reconcile to the item marks; (9) the "
    "duration follows the supplied assessment_format_policy for the "
    "category and difficulty. Judge only this item on its own evidence; "
    "never infer from length, position, neighbours or quotas. There is no "
    "quota for issues: return every genuine, evidence-bound concern and an "
    "empty list when there is none. You do not rewrite, retry, or gate "
    "anything: the recorded decisions stand and your dissent ships for "
    "review. State your honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","verdict":"verified|dissent","confidence":0.0,'
    '"issues":[]}'
)


class ItemReviewError(ValueError):
    """The review input cannot be bound mechanically."""


def _checker(candidate_id: str) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("candidate_id") or "") != candidate_id:
            defects.append(f"candidate_id must echo {candidate_id!r}")
        if response.get("verdict") not in {"verified", "dissent"}:
            defects.append("verdict must be exactly verified or dissent")
        confidence = response.get("confidence")
        if isinstance(confidence, bool) or not isinstance(
            confidence, (int, float)
        ) or not 0 <= float(confidence) <= 1:
            defects.append("confidence must be a number between 0 and 1")
        issues = response.get("issues")
        if not isinstance(issues, list) or any(
            not isinstance(item, str) for item in issues
        ):
            defects.append("issues must be an array of strings")
        elif response.get("verdict") == "dissent" and not any(
            str(item).strip() for item in issues
        ):
            defects.append("a dissent names at least one issue")
        return defects

    return check


def _live_review(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload, stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    return generation._openai_json(
        ITEM_REVIEW_SYSTEM,
        suffix,
        purpose="advisory_critic",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "item-review-v1",
            prefix,
            shard_seed=str(payload.get("candidate_id") or ""),
        ),
    )


def _payload(
    candidate: Mapping[str, Any],
    cell: Mapping[str, Any],
    atom: Mapping[str, Any] | None,
    *,
    meta: Mapping[str, Any],
    format_policy: Mapping[str, Any],
) -> dict[str, Any]:
    item = copy.deepcopy(dict(candidate))
    # The item's own audit records are not evidence for the review.
    for key in list(item):
        if key.startswith("_aegis_"):
            item.pop(key, None)
    return {
        "stage": "assessment.item_review",
        "rules": ITEM_REVIEW_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "assessment_format_policy": copy.deepcopy(dict(format_policy)),
        "rubric_tag_policy": assessment_profile.rubric_tag_policy(meta),
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "source_atom": copy.deepcopy(dict(atom)) if atom is not None else None,
        "blueprint_cell": copy.deepcopy(dict(cell)),
        "item": item,
    }


def review_items(
    units: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None]],
    *,
    meta: Mapping[str, Any],
    profile: Mapping | str | None,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    store: kernel.DecisionStore | None = None,
    on_result=None,
) -> list[dict[str, Any]]:
    """Return one review record per ``(candidate, cell, atom)`` unit, in order.

    Each record is ``{candidate_id, verdict, confidence, issues,
    review_flags, authority}``. A review that cannot complete (provider
    fault, a response that never met the mechanical contract) returns
    ``verdict="unavailable"`` with the reason — the item ships flagged, the
    Master is never blocked by its auditor (Q10).
    """

    envelope_sha = str(envelope_sha256 or "").strip()
    if not envelope_sha:
        raise ItemReviewError("item review requires an envelope hash")
    metadata = dict(meta) if isinstance(meta, Mapping) else {}
    run_profile = assessment_profile.resolve_for_metadata(profile, metadata)
    format_policy = assessment_profile.assessment_format_policy(
        run_profile, metadata,
    )
    prepared: list[tuple[str, Mapping, Mapping, Mapping | None]] = []
    seen: set[str] = set()
    for position, unit in enumerate(units, start=1):
        if not isinstance(unit, (tuple, list)) or len(unit) != 3:
            raise ItemReviewError(
                f"item review unit {position} is not a candidate/cell/atom "
                "triple"
            )
        candidate, cell, atom = unit
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ItemReviewError(
                f"item review unit {position} candidate has no candidate_id"
            )
        if candidate_id in seen:
            raise ItemReviewError(
                f"item review repeats candidate_id {candidate_id!r}"
            )
        seen.add(candidate_id)
        prepared.append((candidate_id, candidate, cell, atom))
    if not prepared:
        return []

    def unavailable(candidate_id: str, exc: BaseException) -> dict[str, Any]:
        return {
            "candidate_id": candidate_id,
            "verdict": "unavailable",
            "confidence": 0.0,
            "issues": [],
            "review_flags": [
                f"{UNAVAILABLE_WARNING}: the joint item review could not "
                f"complete ({type(exc).__name__}: {exc})"
            ],
            "authority": {"policy_version": ITEM_REVIEW_POLICY_VERSION},
        }

    if provider is None:
        from .phase3 import envelope as envelope_mod

        try:
            envelope_mod.require_live_api()
        except envelope_mod.LiveApiUnavailable as exc:
            # The auditor never blocks (Q10): with no live API and no
            # injected reviewer every item ships carrying the named flag.
            return [
                unavailable(candidate_id, exc)
                for candidate_id, _candidate, _cell, _atom in prepared
            ]
        provider = _live_review
    decision_store = store or kernel.DecisionStore()

    def review_one(
        unit: tuple[str, Mapping, Mapping, Mapping | None],
    ) -> dict[str, Any]:
        candidate_id, candidate, cell, atom = unit
        payload = _payload(
            candidate, cell, atom, meta=metadata, format_policy=format_policy,
        )
        try:
            decision = kernel.decide(
                kind="assessment.item_review",
                unit_id=candidate_id,
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=provider,
                checker=_checker(candidate_id),
                critic=None,
                store=decision_store,
                policy_version=ITEM_REVIEW_POLICY_VERSION,
                fixer=None,
            )
        except Exception as exc:  # noqa: BLE001 — the auditor never blocks
            return unavailable(candidate_id, exc)
        response = dict(decision.get("response") or {})
        issues = [
            str(item).strip() for item in response.get("issues") or []
            if str(item).strip()
        ]
        verdict = str(response.get("verdict") or "")
        flags = [f"item review: {issue}" for issue in issues]
        if verdict == "dissent" and not flags:
            flags.append("item review: dissent recorded without detail")
        return {
            "candidate_id": candidate_id,
            "verdict": verdict,
            "confidence": float(response.get("confidence") or 0.0),
            "issues": issues,
            "review_flags": flags,
            "authority": {
                "decision_key": str(decision.get("key") or ""),
                "policy_version": str(decision.get("policy_version") or ""),
            },
        }

    return kernel.parallel_map_in_order(
        prepared,
        review_one,
        max_workers=config.phase3_decision_workers(),
        on_result=on_result,
    )
