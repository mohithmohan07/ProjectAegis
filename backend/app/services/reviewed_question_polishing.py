"""Q51 Step 2: polish the reviewed Post questions once, before Master authoring.

Under the V2 reviewed-file workflow Step 1 extracts every question AS IS into
the Concept files; the question-polishing pass no longer runs there. After the
team's reviewed file is read independently (Q49) its questions are the exact
Post set, and this pass runs the existing polishing author with its
independent advisory critic over them so each reviewed question becomes a
standalone Aegis task. The reviewed file is the complete authority: the
reviewer's edits, omissions, additions and Type/Case placement are final, and
the rule block appended for this policy says so to both models.

* ``raw_task`` / ``normalized_task`` keep the reviewed quote as source
  evidence; ``frozen_task_text`` carries the accepted wording (polished or
  reviewed) that the Post Master materializes, with ``learner_context`` and
  ``polish_audit`` beside it, exactly as Step 1 used to record them.
* The pass is recorded once per reviewed file as a new revision round plus an
  idempotency receipt on the payload; identical reruns spend nothing.
* Pre questions are never polished here: file-supplied Pre questions are the
  reviewer's own and generated Pre questions are authored complete.
* V1 (Q49) payloads were polished in Step 1 and are left untouched.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

from . import build_concepts_release as release
from . import model_provider, progress, question_polishing, release_review
from . import reviewed_file_input
from . import reviewed_file_workflow_policy as workflow

KEY = "reviewed_question_polishing_policy"
VERSION = "reviewed-question-polishing-2026-09-11-v1"
VERSIONS = (VERSION,)
RECEIPT_KEY = "reviewed_question_polishing"
REVIEWED_SOURCE_KIND = "reviewed_file"

# Additive, versioned rule blocks appended by the polishing author/critic
# system prompts only when the batch payload carries KEY. The v1 rule texts in
# question_polishing/source_task_polishing_policy stay byte-identical: they
# participate in historical decision keys.
POLISH_RULES = """\
REVIEWED STEP 2 POLISHING — THE REVIEWED FILE IS THE COMPLETE AUTHORITY
This batch carries the recorded reviewed-question polishing policy. Every
task here was extracted AS IS from the reviewer's uploaded Concept file for
this Master step; that file is the complete authority. The reviewer's edits,
omissions, additions and Type/Case placement are deliberate and final. Your
only permission is to make each reviewed question a standalone Aegis task:
resolve its referents from the cited reviewed blocks supplied under
source_evidence.source_context.reviewed_file_blocks, keep the necessary
context, every option, table, image and ordered part, and the same response
mode and assessed skill. Never add, remove, split, merge or reorder
questions. Never restore content the reviewer removed or reach for material
outside the reviewed file. Never change the demand, difficulty or answer
space, and never leak an answer. When unsure, return the reviewed wording
unchanged as polished_task and explain in note; that item ships flagged for
review, and no later Master stage may rewrite it.
"""

REVIEW_RULES = """\
REVIEWED STEP 2 REVIEW — THE REVIEWED FILE IS THE COMPLETE AUTHORITY
Each original task in this batch is the reviewer's exact wording from the
uploaded Concept file, and its cited reviewed blocks under
source_evidence.source_context.reviewed_file_blocks are its complete
evidence. The reviewer's edits, omissions, additions and Type/Case placement
are deliberate and final. Verify that the proposal only made the same
reviewed question standalone: referents resolved from the cited blocks,
necessary context kept, and every option, table, image, ordered part,
response mode and assessed skill intact. Flag any added, removed, split,
merged or reordered question, any restored or outside material, any changed
demand or answer space, and any answer leakage. Keeping the reviewed wording
unchanged is always acceptable.
"""


def active(value: Any) -> bool:
    """Whether a batch payload/metadata mapping carries this policy stamp."""
    return isinstance(value, dict) and value.get(KEY) in VERSIONS


def fields(value: Any) -> dict[str, str]:
    return {KEY: value[KEY]} if active(value) else {}


def author_rules(value: Any) -> str:
    return "\n" + POLISH_RULES if active(value) else ""


def review_rules(value: Any) -> str:
    return "\n" + REVIEW_RULES if active(value) else ""


def defers_polishing(payload: Any) -> bool:
    """The payload's recorded workflow version decides; the bound run is the fallback."""
    if workflow.version_of(payload) is not None:
        return workflow.defers_polishing(payload)
    return workflow.run_defers_polishing()


def applies(payload: Any) -> bool:
    """Only an independent reviewed Post payload under the V2 workflow."""
    return (
        reviewed_file_input.active(payload)
        and str(payload.get(release.RELEASE_LANE_FIELD) or "") == release.LANE_POST
        and defers_polishing(payload)
    )


def recorded(payload: Any) -> bool:
    """Whether this reviewed file's questions already carry the pass receipt."""
    if not isinstance(payload, dict):
        return False
    receipt = payload.get(RECEIPT_KEY)
    sha = str((payload.get(reviewed_file_input.KEY) or {}).get("sha256") or "")
    return (
        isinstance(receipt, dict)
        and receipt.get("version") in VERSIONS
        and str(receipt.get("sha256") or "") == sha
    )


def _reviewed_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = payload.get("question_task_inventory") or {}
    return [
        item for item in inventory.get("items") or []
        if isinstance(item, dict) and item.get("source_kind") == REVIEWED_SOURCE_KIND
    ]


def polish_reviewed_post_questions(
    db,
    job,
    *,
    payload: dict[str, Any] | None,
    owner_sub: str = "",
    api_call: Callable[..., dict] | None = None,
) -> dict[str, Any] | None:
    """Run the author + independent critic once over the reviewed Post questions.

    Returns the committed payload revision, or ``None`` when the pass does not
    apply (V1 or Pre payload), was already recorded for this reviewed file, or
    has no eligible reviewed question.
    """
    if not applies(payload) or recorded(payload):
        return None
    eligible = [item for item in _reviewed_items(payload) if question_polishing._eligible(item)]
    if not eligible:
        return None
    sha = str((payload.get(reviewed_file_input.KEY) or {}).get("sha256") or "")
    meta = {**reviewed_file_input.metadata(db, payload), **reviewed_file_input._policies(), KEY: VERSION}
    progress.step("Step 2 · Polishing reviewed Post questions")
    with model_provider.bind_profile(model_provider.new_profile()):
        polished = question_polishing.polish_inventory(
            {"items": copy.deepcopy(eligible)}, meta=meta, api_call=api_call,
        )
    decided = {
        str(item.get("qid") or ""): item
        for item in polished.get("items") or [] if isinstance(item, dict)
    }
    if not any(isinstance(item.get("polish_audit"), dict) for item in decided.values()):
        # The pass did not run (live generation unavailable): record nothing,
        # so a later live Step 2 can still polish this reviewed file.
        progress.log("Reviewed Post questions were not polished: live generation is unavailable.", level="warning")
        return None
    updated = copy.deepcopy(payload)
    items = updated["question_task_inventory"]["items"]
    polished_count = kept_count = review_count = 0
    for index, item in enumerate(items):
        replacement = decided.get(str(item.get("qid") or "")) if isinstance(item, dict) else None
        if replacement is None:
            continue
        items[index] = replacement
        flag = str(replacement.get("polish_flag") or "")
        polished_count += flag == question_polishing.FLAG_POLISHED
        kept_count += flag == question_polishing.FLAG_KEPT
        review_count += bool(replacement.get("polish_review_required"))
    receipt = {
        "version": VERSION, "sha256": sha, "polished": polished_count,
        "kept": kept_count, "review_flags": review_count, "questions": len(eligible),
    }
    updated[RECEIPT_KEY] = receipt
    release_review._commit_round(
        db, job, release.LANE_POST, updated, origin="manual_edit", owner_sub=owner_sub,
        instruction="Polish reviewed Post questions before Master authoring",
        operations=[{"kind": "reviewed_question_polishing", "sha256": sha}],
        diff={KEY: VERSION, RECEIPT_KEY: copy.deepcopy(receipt)},
        parent_uid=str(payload.get(release.STAGED_RELEASE_UID_FIELD) or ""),
    )
    progress.log(
        f"Reviewed Post file: {polished_count} question(s) polished for standalone use, "
        f"{kept_count} kept the reviewed wording and flagged, "
        f"{len(eligible) - polished_count - kept_count} already standalone; "
        f"{review_count} independent review flag(s). The reviewed question set is unchanged.",
        level="warning" if review_count else "success",
    )
    return updated
