"""Module 2: Build Concepts.

  Post Learning — upload a document (any format), convert to MMD, parse it into
  concepts, and deposit them under a chapter.

  Restructure step 7 retired the two legacy Pre Learning lanes (upload, and
  "derive from existing Post Learning"): both ran the whole Post pipeline
  first and then derived prerequisites from the finished map. Phase 03
  captures prerequisites during the run instead. ``UploadJob.learning_kind``
  and the checkpoint routes' ``post|pre`` contract are deliberately kept —
  see the column's own note in ``models.py``.

  All created concepts are written to the Bulk Import output workbook
  (append-only) as concept-catalog rows, and chapters' pre_topics / post_topics
  summaries are kept in sync.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import config, models, schemas
from .. import bulk_import as bi
from ..bulk_import import workbook_sync, writer
from . import (
    autonomous_resolution,
    canonical_source_phase22,
    chapter_durations,
    concept_cleanup,
    concept_refiner,
    concept_validator,
    drive_checkpoints,
    generation,
    grounding_certificate,
    identity,
    instruction_architect,
    mmd,
    openai_usage,
    progress,
    semantic_recovery,
    source_topic_decision,
    type_granularity_decision,
    uploads,
)


class DepositValidationError(ValueError):
    """Deterministic content validation rejected deposit-ready concept rows."""


class HumanDecisionConflictError(ValueError):
    """A semantic decision is stale, already answered, or not currently open."""


class UnattendedDecisionUnavailable(RuntimeError):
    """Unattended generation reached a decision only a person could answer.

    Raised instead of parking the run in ``awaiting_decision``. An unattended
    batch cannot act on a pause, so a stalled job is strictly worse than a
    terminal failure: this ends the run with the reason on the record. The
    wording deliberately avoids the semantic-failure vocabulary so bounded
    semantic recovery classifies it as non-semantic and does not retry it.
    """


_HUMAN_DECISIONS_KEY = "human_decisions"
_HUMAN_DECISIONS_VERSION = 1
_HUMAN_DECISION_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HUMAN_DECISION_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_HUMAN_DECISIONS = 5_000
_MAX_DEFERRED_HUMAN_DECISIONS = 5_000
_MAX_AGENT_REVIEW_HISTORY = 100
_DEPOSITED_GROUNDING_CERTIFICATE_KEY = "deposited_grounding_certificate"
_ACTIVE_AGENT_RESOLUTION_IDS: ContextVar[frozenset[str]] = ContextVar(
    "aegis_active_agent_resolution_ids",
    default=frozenset(),
)


def _normalize_pending_human_decision(
    raw: dict,
    *,
    cumulative_usage: dict | None = None,
) -> dict:
    """Project Phase 3.3's rich pause packet into the stable public schema."""
    if not isinstance(raw, dict):
        raise ValueError("human semantic decision payload must be an object")
    def rows(key: str, maximum: int) -> list[dict]:
        value = raw.get(key) or []
        if (
            not isinstance(value, list)
            or len(value) > maximum
            or any(not isinstance(row, dict) for row in value)
        ):
            raise ValueError(f"pending decision {key} is not bounded")
        return value

    item = dict(raw.get("item") or {})
    topic = item.get("topic")
    if isinstance(topic, dict):
        topic = topic.get("title") or topic.get("topic_id") or ""
    item["topic"] = str(topic or "")
    item = {
        key: item.get(key, [] if key in {"qids", "questions"} else "")
        for key in (
            "unit_id", "type_id", "type_title",
            "qids", "questions", "topic",
        )
    }
    candidates = []
    for row in rows("candidates", 100):
        source_block_ids = row.get("source_block_ids") or []
        if not isinstance(source_block_ids, list):
            raise ValueError(
                "pending decision candidate source_block_ids is not a list"
            )
        candidates.append({
            **{
                key: str(row.get(key) or "")
                for key in (
                    "target_id",
                    "concept_id",
                    "title",
                    "topic",
                    "coverage",
                    "gap",
                    "action",
                    "source_topic_id",
                    "target_topic_id",
                    "boundary_relation",
                    "source_kind",
                    "source_page",
                    "text_sha256",
                    "binding_hash",
                )
            },
            "source_block_ids": [
                str(value) for value in source_block_ids if str(value)
            ],
        })
    evidence = [
        {
            key: str(row.get(key) or "")
            for key in ("evidence_id", "page", "label", "text")
        }
        for row in rows("evidence", 100)
    ]
    options = [{
        "choice": (
            "custom_instruction"
            if row.get("choice") == "custom"
            else row.get("choice")
        ),
        "label": str(row.get("label") or ""),
        "recommended": bool(row.get("recommended")),
        "target_id": str(row.get("target_id") or ""),
        "target_concept_id": str(row.get("target_concept_id") or ""),
    } for row in rows("options", 16)]
    deferred_unit_ids = raw.get("deferred_assignment_unit_ids") or []
    if (
        not isinstance(deferred_unit_ids, list)
        or len(deferred_unit_ids) > _MAX_DEFERRED_HUMAN_DECISIONS
    ):
        raise ValueError(
            "pending decision deferred_assignment_unit_ids is not bounded")
    stable = {
        "kind": str(raw.get("kind") or "semantic_host_conflict"),
        "phase": str(raw.get("phase") or "3.3"),
        "conflict": str(raw.get("conflict") or ""),
        "diagnosis": str(raw.get("diagnosis") or ""),
        "decision_question": str(raw.get("decision_question") or ""),
        "checkpoint_progress": raw.get("checkpoint_progress") or 0.0,
        "item": item,
        "candidates": candidates,
        "evidence": evidence,
        "deferred_assignment_unit_ids": [
            str(value)
            for value in deferred_unit_ids
            if str(value)
        ],
        "options": options,
        "source_patch": copy.deepcopy(raw.get("source_patch")),
    }
    computed_hash = hashlib.sha256(json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    context_hash = str(raw.get("context_hash") or computed_hash).strip().lower()
    if not _HUMAN_DECISION_HASH_RE.fullmatch(context_hash):
        raise ValueError(
            "pending decision context_hash must be a lowercase SHA-256")
    decision_id = str(
        raw.get("decision_id") or f"semantic-{context_hash[:24]}").strip()
    if not _HUMAN_DECISION_ID_RE.fullmatch(decision_id):
        raise ValueError("pending decision decision_id has an invalid format")
    usage = raw.get("cumulative_usage")
    if not isinstance(usage, dict):
        usage = cumulative_usage if isinstance(cumulative_usage, dict) else {}
    payload = schemas.PendingSemanticDecision.model_validate({
        "decision_id": decision_id,
        "context_hash": context_hash,
        **stable,
        "cumulative_usage": copy.deepcopy(usage),
        "agent_review": copy.deepcopy(raw.get("agent_review")),
    }).model_dump()
    return payload


def _human_decision_ledger(checkpoint: dict | None) -> dict:
    if not isinstance(checkpoint, dict):
        return {}
    value = checkpoint.get(_HUMAN_DECISIONS_KEY)
    return copy.deepcopy(value) if isinstance(value, dict) else {}








def _pending_human_decision(checkpoint: dict | None) -> dict | None:
    pending = _human_decision_ledger(checkpoint).get("pending")
    return copy.deepcopy(pending) if isinstance(pending, dict) else None


def _human_decisions_with_status(
    checkpoint: dict | None,
    *,
    statuses: set[str],
) -> list[dict]:
    ledger = _human_decision_ledger(checkpoint)
    result = []
    for entry in ledger.get("resolutions") or []:
        if (
            isinstance(entry, dict)
            and str(entry.get("status") or "") in statuses
            and isinstance(entry.get("pending_decision"), dict)
        ):
            result.append({
                **copy.deepcopy(entry["pending_decision"]),
                "status": str(entry.get("status") or ""),
                "choice": str(entry.get("choice") or ""),
                "instruction": str(entry.get("instruction") or ""),
                "target_concept_id": str(
                    entry.get("target_concept_id") or ""),
                "target_id": str(entry.get("target_id") or ""),
                "resolved_at": str(entry.get("resolved_at") or ""),
                "resolved_by": str(entry.get("resolved_by") or "human"),
            })
    deferred = [
        str(value)
        for value in ledger.get("deferred_assignment_unit_ids") or []
        if str(value)
    ]
    if result and deferred:
        result[-1]["deferred_assignment_unit_ids"] = deferred
    return result


def _ready_human_decisions(checkpoint: dict | None) -> list[dict]:
    return _human_decisions_with_status(checkpoint, statuses={"ready"})


def _compact_resolved_pending_decision(
    pending: dict,
    *,
    choice: str,
    target_id: str,
    target_concept_id: str,
) -> dict:
    """Keep the audit identity without copying a shrinking queue N² times."""

    snapshot = copy.deepcopy(pending)
    snapshot["evidence"] = []
    snapshot["deferred_assignment_unit_ids"] = []
    selected_target = target_id or target_concept_id
    if selected_target:
        snapshot["candidates"] = [
            candidate
            for candidate in snapshot.get("candidates") or []
            if isinstance(candidate, dict)
            and selected_target in {
                str(candidate.get("target_id") or ""),
                str(candidate.get("concept_id") or ""),
            }
        ]
    else:
        snapshot["candidates"] = []
    selected_options = [
        copy.deepcopy(option)
        for option in snapshot.get("options") or []
        if isinstance(option, dict)
        and str(option.get("choice") or "") == choice
    ]
    if selected_target:
        for option in selected_options:
            if target_id:
                option["target_id"] = target_id
            if target_concept_id:
                option["target_concept_id"] = target_concept_id
    snapshot["options"] = selected_options
    return schemas.PendingSemanticDecision.model_validate(snapshot).model_dump()


def _copy_human_decision_ledger(
    target: dict,
    source: dict | None,
) -> dict:
    result = copy.deepcopy(target)
    ledger = _human_decision_ledger(source)
    if ledger:
        result[_HUMAN_DECISIONS_KEY] = ledger
    return result


def _prune_human_decisions_after_checkpoint_fallback(
    envelope: dict,
    *,
    retained_stage: str,
) -> dict:
    """Retire decisions derived from checkpoint stages this build discarded.

    A pending semantic action is meaningful only with the inventory/topology
    stage that produced its candidates.  When a stage-contract bump rewinds an
    envelope, dispatching that old pause before compatibility normalization
    would spend once on stale evidence.  Earlier source decisions remain
    reusable; only decisions whose own saved progress is later than the
    retained stage are removed.
    """

    result = copy.deepcopy(envelope)
    ledger = _human_decision_ledger(result)
    if not ledger:
        return result

    retained_order = generation._checkpoint_order(retained_stage)

    def derived_after_fallback(raw_pending: object) -> bool:
        if not isinstance(raw_pending, dict):
            return False
        try:
            progress_value = float(raw_pending.get("checkpoint_progress") or 0.0)
        except (TypeError, ValueError):
            progress_value = 0.0
        if progress_value <= 0.0 or retained_order < 0:
            return False
        exact_orders = [
            int(spec.get("order", -1))
            for spec in generation._CONCEPT_CHECKPOINT_STAGES.values()
            if abs(
                float(spec.get("progress") or 0.0) - progress_value
            ) <= 1e-9
        ]
        earlier_orders = [
            int(spec.get("order", -1))
            for spec in generation._CONCEPT_CHECKPOINT_STAGES.values()
            if float(spec.get("progress") or 0.0) < progress_value
        ]
        derived_order = (
            min(exact_orders)
            if exact_orders else max(earlier_orders, default=-1)
        )
        return derived_order > retained_order

    invalidated = False
    invalidated_issue_keys: set[str] = set()

    def remember_review_issue(raw_pending: object) -> None:
        if not isinstance(raw_pending, dict):
            return
        review = raw_pending.get("agent_review")
        issue_key = (
            str(review.get("issue_key") or "")
            if isinstance(review, dict) else ""
        )
        if issue_key:
            invalidated_issue_keys.add(issue_key)

    pending = ledger.get("pending")
    if derived_after_fallback(pending):
        remember_review_issue(pending)
        ledger["pending"] = None
        invalidated = True

    retained_resolutions = []
    for raw in ledger.get("resolutions") or []:
        if (
            isinstance(raw, dict)
            and derived_after_fallback(raw.get("pending_decision"))
            and str(raw.get("choice") or "") != "replace_source"
        ):
            remember_review_issue(raw.get("pending_decision"))
            invalidated = True
            continue
        retained_resolutions.append(copy.deepcopy(raw))
    ledger["resolutions"] = retained_resolutions

    if invalidated:
        # These queues and review attempts are identities of the discarded
        # candidate workspace.  Keeping them could either replay a stale
        # direction or suppress the regenerated 31-Case issue as a duplicate.
        ledger["deferred_assignment_unit_ids"] = []
        ledger["agent_review_history"] = [
            copy.deepcopy(row)
            for row in ledger.get("agent_review_history") or []
            if (
                isinstance(row, dict)
                and str(row.get("issue_key") or "")
                not in invalidated_issue_keys
            )
        ]
        result[_HUMAN_DECISIONS_KEY] = ledger
    return result



def _applied_human_decision_ids(checkpoint: dict | None) -> set[str]:
    """Decision IDs whose bounded action is durably represented here."""
    if not isinstance(checkpoint, dict):
        return set()
    ids: set[str] = set()
    source_review_graph = checkpoint.get("source_review_graph")
    if isinstance(source_review_graph, dict):
        for key in (
            "source_review_resolution",
            "numbered_topic_patch_resolution",
        ):
            applied = source_review_graph.get(key)
            if not isinstance(applied, dict):
                continue
            decision_id = str(
                applied.get("decision_id")
                or applied.get("human_decision_id")
                or ""
            )
            if decision_id:
                ids.add(decision_id)
    source_state = checkpoint.get("source_topic_recovery")
    if isinstance(source_state, dict):
        attempt = source_state.get("last_attempt")
        if isinstance(attempt, dict) and attempt.get("decision_id"):
            ids.add(str(attempt["decision_id"]))
        follow_up = source_state.get("pending_followup")
        if isinstance(follow_up, dict) and follow_up.get("prior_decision_id"):
            ids.add(str(follow_up["prior_decision_id"]))
    mined_types = checkpoint.get("mined_types")
    review = (
        mined_types.get("_granularity_review")
        if isinstance(mined_types, dict) else None
    )
    if isinstance(review, dict):
        last_attempt = review.get("last_attempt")
        if isinstance(last_attempt, dict) and last_attempt.get("decision_id"):
            ids.add(str(last_attempt["decision_id"]))
        applied = review.get("human_resolution")
        if isinstance(applied, dict) and applied.get("decision_id"):
            ids.add(str(applied["decision_id"]))
        follow_up = review.get("pending_followup")
        if isinstance(follow_up, dict) and follow_up.get("prior_decision_id"):
            ids.add(str(follow_up["prior_decision_id"]))
    return {decision_id for decision_id in ids if decision_id}


def _consume_applied_human_decisions(
    envelope: dict,
    applied_checkpoint: dict | None,
) -> dict:
    """Mark an authorization consumed in the same durable checkpoint write."""
    result = copy.deepcopy(envelope)
    applied_ids = _applied_human_decision_ids(applied_checkpoint)
    if not applied_ids:
        return result
    ledger = _human_decision_ledger(result)
    changed = False
    resolutions: list[dict] = []
    for raw in ledger.get("resolutions") or []:
        entry = copy.deepcopy(raw)
        if (
            isinstance(entry, dict)
            and str(entry.get("decision_id") or "") in applied_ids
            and entry.get("status") == "ready"
        ):
            entry["status"] = "consumed"
            entry["consumed_at"] = str(
                (applied_checkpoint or {}).get("saved_at")
                or datetime.now(timezone.utc).isoformat()
            )
            changed = True
        resolutions.append(entry)
    if changed:
        ledger["resolutions"] = resolutions
        result[_HUMAN_DECISIONS_KEY] = ledger
    return result


@contextmanager
def _human_decision_resolution_context(checkpoint: dict | None):
    durable_resolutions = _human_decisions_with_status(
        checkpoint, statuses={"ready", "consumed"})
    active_agent_ids = _ACTIVE_AGENT_RESOLUTION_IDS.get()
    # A settled decision stays settled — across replays AND across resumes.
    # Application remains identity-guarded (context hash / unit ids), so a
    # stale row that no longer matches the workspace simply directs nothing.
    resolutions: list[dict] = []
    effective_durable: list[dict] = []
    for raw in durable_resolutions:
        row = copy.deepcopy(raw)
        reactivate_agent = (
            row.get("status") == "consumed"
            and row.get("resolved_by") == "agent"
            and str(row.get("decision_id") or "") in active_agent_ids
        )
        if reactivate_agent:
            row["status"] = "ready"
        effective_durable.append(row)
        if row.get("status") == "ready":
            resolutions.append(copy.deepcopy(row))
    durable_resolutions = effective_durable
    if not resolutions and not durable_resolutions:
        yield
        return
    from . import canonical_source_phase3 as phase3
    with phase3.human_source_resolution_context(
        copy.deepcopy(resolutions)
    ):
        with source_topic_decision.human_resolution_context(
            copy.deepcopy(durable_resolutions)
        ):
            with type_granularity_decision.human_resolution_context(
                copy.deepcopy(durable_resolutions)
            ):
                yield


def _find_concept_in_chapter(
    chapter: models.Chapter, title: str, *, pre_post: str = "",
) -> models.Concept | None:
    """Locate an existing concept anywhere under the chapter by normalized title.

    Schools use different books; the same concept arriving from another book
    must be reused (and its sources extended), never duplicated.
    """
    norm = bi.normalize_question_text(title)
    for t in chapter.topics:
        if pre_post and t.pre_post_learning.lower() != pre_post.lower():
            continue
        for c in t.concepts:
            if bi.normalize_question_text(c.concept_title) == norm:
                return c
    return None


def _find_or_create_topic(
    db: Session, chapter: models.Chapter, topic_title: str, pre_post: str,
) -> models.Topic:
    display_name = bi.strip_topic_title(topic_title) or topic_title
    # T4-6: the third of four live topic identities, converged on the one
    # shared normaliser in ``services.identity``.
    normalized_title = identity.topic_identity(topic_title)
    for t in chapter.topics:
        if (
            t.pre_post_learning == pre_post
            and identity.topic_identity(t.topic_title) == normalized_title
        ):
            # The stored ``topic_title`` is NEVER rewritten to the incoming
            # casing. [measured, real SQLite session] that rewrite is the
            # reproducible cause of the Master-upload refusal class MC6
            # declared unfindable: depositing "Growth and Reproduction" and
            # then re-running with "growth and reproduction" reused the row,
            # renamed it, and the next
            # ``_resolve_snapshot_concept_ids`` raised UploadRefused
            # ("does not have one exact published identity") against a
            # title nobody had edited. Match leniently, write nothing.
            if t.topic_display_name != display_name:
                t.topic_display_name = display_name
            return t
    # Create through the relationship so chapter.topics stays current within
    # this session — otherwise every repeat of the same topic title would
    # miss the lookup above and create a duplicate Topic row.
    topic = models.Topic(
        topic_title=topic_title,
        topic_display_name=display_name, pre_post_learning=pre_post,
    )
    chapter.topics.append(topic)
    db.flush()
    return topic


def _add_concept(db: Session, topic: models.Topic, rec: dict,
                 source_book: str = "") -> models.Concept:
    chapter = topic.chapter
    # Normalize name (& collapse) and description (strip dangling refs) before
    # persisting, so dry and live output are equally import-clean.
    rec = concept_cleanup.clean_concept_record(dict(rec))
    concept = models.Concept(
        topic_id=topic.id,
        concept_title=rec["concept_title"],
        # Display name stays CLEAN; the writer composes the tagged title column.
        concept_display_name=rec["concept_title"],
        parent_concept=rec.get("parent_concept", ""),
        concept_details=rec.get("concept_details", ""),
        keywords=rec.get("keywords", ""),
        sources=source_book.strip(),
    )
    db.add(concept)
    db.flush()
    # Every concept gets the three standard group shells.
    for g_type in ("Basic", "Intermediate", "Advanced"):
        db.add(models.Group(
            concept_id=concept.id, group_type=g_type,
            group_name=f"{concept.concept_title} — {g_type}",
            group_display_name=f"{concept.concept_title} — {g_type}",
            group_status="Active",
        ))
    return concept


def _missing_deposit_source_topics(
    records: list[dict], source_text: str,
) -> list[dict]:
    """Return structurally proven source topics absent at the DB boundary.

    This is a deterministic last line of defence for restored/legacy
    checkpoints. A source with fewer than two usable main headings does not
    provide enough topology evidence for this guard; ordinary validation still
    applies in that case.
    """
    if not str(source_text or "").strip():
        return []
    sections = [
        section
        for chunk in generation._section_aware_chunks(source_text)
        for section in chunk.get("sections") or []
        if isinstance(section, dict)
    ]
    if len(generation._topic_headings(sections)) < 2:
        return []
    return generation._missing_source_topic_excerpts(
        records,
        generation._group_source_topic_excerpts(sections),
    )


def _require_deposit_source_topic_coverage(
    records: list[dict], source_text: str,
) -> None:
    """Fail closed when a generated map loses a proven source topic."""
    missing_source_topics = _missing_deposit_source_topics(
        records, source_text)
    if not missing_source_topics:
        return
    names = [
        str(group.get("topic") or "").strip()
        for group in missing_source_topics
        if str(group.get("topic") or "").strip()
    ]
    progress.log(
        "Deposit blocked because the generated map lost "
        f"{len(names)} structurally proven source topic(s): "
        + ", ".join(names),
        level="error",
    )
    raise DepositValidationError(
        "source-topic topology failed before deposit: missing="
        + ",".join(names)
    )


def _restore_deposit_source_topic_snapshot(
    records: list[dict],
    baseline: list[dict],
    source_text: str,
    *,
    stage: str,
) -> list[dict]:
    """Reject a deposit-only mutation that removes a proven source topic."""

    if not _missing_deposit_source_topics(records, source_text):
        return records
    _require_deposit_source_topic_coverage(baseline, source_text)
    progress.log(
        f"Rejected {stage} because it dropped a structurally proven source "
        "topic; restoring the validated final-content snapshot without "
        "another model call.",
        level="warning",
    )
    return copy.deepcopy(baseline)


def _deposit_concepts(
    db: Session, chapter: models.Chapter, records: list[dict],
    pre_post: str, source_book: str,
    *,
    inventory: dict | None = None,
    mined_types: dict | None = None,
    source_text: str = "",
    final_grounding_certificate: dict | None = None,
    grounding_certificate_sink: dict | None = None,
) -> tuple[list[int], list[int]]:
    """Create concepts under the chapter, reusing existing ones across books.

    Returns (created_ids, merged_ids): a same-learning-kind normalized-title
    match is refreshed from the newly validated record and its sources grow.
    """
    type_case_qid_placement_ledger: dict | None = None
    if pre_post == "Post" and final_grounding_certificate is not None:
        from . import canonical_source_phase3 as phase3

        semantic_graph = phase3.active_graph()
        if not isinstance(semantic_graph, dict):
            session = phase3.active_session()
            semantic_graph = (
                session.get("graph")
                if isinstance(session, dict)
                else None
            )
        try:
            type_case_qid_placement_ledger = (
                generation._resolved_type_case_qid_placement_ledger(
                    inventory,
                    mined_types,
                )
            )
            grounding_certificate.verify_final_certificate(
                records,
                final_grounding_certificate,
                semantic_graph=(
                    semantic_graph
                    if isinstance(semantic_graph, dict)
                    else None
                ),
                require_semantic_graph=config.use_live_generation(),
                # The rewritten Phase 3 never mints per-row placement
                # contracts; its sealed row certificates are verified above.
                require_placement_contracts=False,
                type_case_qid_placement_ledger=(
                    type_case_qid_placement_ledger
                ),
            )
        except (
            grounding_certificate.GroundingCertificateError,
            RuntimeError,
        ) as exc:
            raise DepositValidationError(str(exc)) from exc
    if pre_post == "Post":
        _require_deposit_source_topic_coverage(records, source_text)
    source_topic_safe_records = copy.deepcopy(records)
    # Clean each record (name hygiene, Title Case, dangling-ref removal), then
    # run the deterministic chapter pass: continuous "Type NN" numbering across
    # the whole chapter, "Miscellaneous Type NN" for culmination rows, and a
    # "Recap" description for culminations. Chapter-wide *intelligence* (dedup,
    # Types enrichment, naming) is done by the API passes in concepts_from_mmd;
    # this pass only enforces the numbering/format the team requires.
    records = [concept_cleanup.clean_concept_record(dict(r)) for r in records]
    records = concept_cleanup.filter_review_violations(
        records, subject=chapter.subject, board=chapter.board,
        chapter_title=chapter.chapter_title)
    records = concept_refiner.refine_chapter(records)
    # The final deposit boundary must be resilient when the API repair pass
    # fails or returns generic/misclassified learner analysis. Preserve valid
    # Misconceptions and/or Error Analysis, and add the deterministic fallback
    # only when a normal concept has neither.
    records = concept_validator.ensure_valid_learner_analysis(records)
    if pre_post == "Post":
        records = generation._ensure_mastery_lines_via_api(
            records, meta={}, use_api=False)
        records = generation._ensure_terminal_culmination_contract(records)
        records = generation._canonicalize_concept_rich_text(records)
        records = _restore_deposit_source_topic_snapshot(
            records,
            source_topic_safe_records,
            source_text,
            stage="deposit-only cleanup",
        )
    # The Fixer (Q13, seams F38/F39/F40): the deposit twins of the
    # final-gate blocks reach the same content-addressed decisions — a QID
    # the Fixer placed at the final gate replays free here, so the deposit
    # payload stays idempotent with the certified one.
    from .phase3 import fixer as p3_fixer

    deposit_fixer = p3_fixer.default_provider() if pre_post == "Post" else None
    if pre_post == "Post" and inventory is not None:
        # A final-content checkpoint is intentionally restored without another
        # model call.  The deposit-only formatting pass above can still remove
        # or reshape an Example from that otherwise-valid checkpoint, though.
        # Reapply the source-owned exact-once coverage contract at this last
        # deterministic boundary, before deciding whether deposit may proceed.
        # This uses the persisted inventory and mined placement hints only; it
        # never spends an API request or invents a question.
        try:
            records = generation._normalize_activity_hubs_from_inventory(
                records, inventory, mined_types)
            records = generation._enforce_rendered_inventory_coverage(
                records, inventory, mined_types, fixer=deposit_fixer)
            # Inventory repair deliberately restores exact source wording. In
            # mathematics that wording can contain bare TeX, so canonicalize
            # after the repair as well as before it. Recheck exact coverage
            # once more because Katex wrappers are presentation-only and must
            # never change qid ownership or placement.
            records = generation._canonicalize_concept_rich_text(records)
            records = generation._enforce_rendered_inventory_coverage(
                records, inventory, mined_types, fixer=deposit_fixer)
            records = generation._canonicalize_concept_rich_text(records)
        except RuntimeError as exc:
            raise DepositValidationError(str(exc)) from exc
        records = concept_refiner.renumber_types_continuously(records)
        records = concept_validator.ensure_valid_learner_analysis(records)
        coverage = generation._rendered_inventory_coverage_defects(
            records, inventory)
        if coverage["missing"] or coverage["duplicate"]:
            progress.log(
                "Deposit inventory validation failed: "
                f"{len(coverage['missing'])} missing and "
                f"{len(coverage['duplicate'])} duplicated question/task(s).",
                level="error",
            )
            defect_parts = []
            if coverage["missing"]:
                defect_parts.append(
                    "missing=" + ",".join(coverage["missing"]))
            if coverage["duplicate"]:
                defect_parts.append(
                    "duplicate=" + ",".join(coverage["duplicate"]))
            raise DepositValidationError(
                "question/task inventory coverage failed before deposit: "
                + "; ".join(defect_parts)
            )
        # Topic locality was a legacy-placement expectation: under the
        # rewritten Phase 3 the house routing rules deliberately move a
        # multi-concept question to a later topic (or its culmination), so
        # the API placement is the authority and no locality check runs.
        try:
            generation._validate_final_or_raise(
                records,
                stage="deposit",
                inventory=inventory,
                mined_types=mined_types,
                source_text=source_text,
                fixer=deposit_fixer,
            )
        except RuntimeError as exc:
            raise DepositValidationError(str(exc)) from exc
    # Cleanup, chapter-wide dedupe, refinement, culmination repair and
    # inventory restoration above can all remove or reshape rows after the
    # early restored-checkpoint guard. Re-run the deterministic topology
    # contract at the terminal record boundary, immediately before the final
    # validator and the first possible DB write.
    if pre_post == "Post":
        records = _restore_deposit_source_topic_snapshot(
            records,
            source_topic_safe_records,
            source_text,
            stage="terminal deposit normalization",
        )
        _require_deposit_source_topic_coverage(records, source_text)
    report = concept_validator.validate_concept_rows(
        records,
        allow_types=True,
        require_culmination=pre_post == "Post",
        allow_culmination=True,
        allowed_source_examples=generation._inventory_source_examples(
            inventory),
        strict_type_hierarchy=pre_post == "Post",
        strict_analysis_section=pre_post == "Post",
        strict_mastery_statement=pre_post == "Post",
        source_text=source_text if pre_post == "Post" else "",
        # Q1 gate split (Post only): analysis existence is scoped to the
        # rows the chapter inventory allotted items to. Step 7 retired the
        # legacy Pre lanes, so nothing in production reaches the ``None``
        # (every-row) branch today; the parameter stays for the Phase 03
        # Pre map, whose own contract is decided when that lane lands.
        analysis_allotted_keys=(
            concept_validator.analysis_allotted_keys(records)
            if pre_post == "Post"
            else None
        ),
    )
    classified_fatal = generation._fatal_errors(report)
    # Post deposit is the same terminal boundary as generation's final gate, so
    # use its complete fatal-code policy even when no inventory was supplied.
    # The non-Post branch deliberately remains tolerant of warnings emitted by
    # Post-only/intermediate contracts. No production caller reaches it since
    # step 7 retired the legacy Pre lanes.
    fatal = (
        classified_fatal
        if pre_post == "Post"
        else [
            error for error in classified_fatal
            if error.get("severity") == "error"
        ]
    )
    # Seam F40 (Q13): a fixer-accepted (row, code) pair is a recorded
    # decision, not a silent skip — it ships flagged. Rows are sealed at
    # this boundary, so the Fixer may only accept-with-flag here; mechanics
    # codes are never acceptable and any remaining defect fails closed.
    fatal = generation._without_fixer_accepted(records, fatal)
    if fatal and deposit_fixer is not None:
        generation._fix_validation_failures_via_fixer(
            records, fatal, stage="deposit", fixer=deposit_fixer,
            allow_corrections=False,
        )
        fatal = generation._without_fixer_accepted(records, fatal)
    progress.log(
        f"Deposit validation: {len(fatal)} fatal error(s), "
        f"{report['summary'].get('warnings', 0)} warning(s).")
    if fatal:
        codes = ", ".join(sorted({e["code"] for e in fatal}))
        raise DepositValidationError(
            f"concept validation failed before deposit: {codes}")

    if pre_post == "Post" and final_grounding_certificate is not None:
        # Deposit-only cleanup must be fully idempotent with the payload that
        # cleared generation's final gate.  A lineage-only comparison cannot
        # see a changed public payload or a removed Type/Case QID-host
        # manifest, because those attestations intentionally live in the final
        # aggregate certificate rather than the per-row grounding seal.
        # Recompute and compare the complete certificate before the first DB
        # flush; any drift returns to the bounded recovery path instead of
        # minting a second, divergent "valid" certificate at deposit time.
        try:
            deposited_certificate = grounding_certificate.verify_final_certificate(
                records,
                final_grounding_certificate,
                semantic_graph=(
                    semantic_graph
                    if isinstance(semantic_graph, dict)
                    else None
                ),
                require_semantic_graph=config.use_live_generation(),
                # The rewritten Phase 3 never mints per-row placement
                # contracts; its sealed row certificates are verified above.
                require_placement_contracts=False,
                type_case_qid_placement_ledger=(
                    type_case_qid_placement_ledger
                ),
            )
        except grounding_certificate.GroundingCertificateError as exc:
            raise DepositValidationError(str(exc)) from exc
        if grounding_certificate_sink is not None:
            grounding_certificate_sink["certificate"] = copy.deepcopy(
                deposited_certificate
            )

    created_ids: list[int] = []
    merged_ids: list[int] = []
    topic_positions: dict[str, int] = {}
    concept_positions: dict[str, int] = {}
    for rec in records:
        topic_key = bi.normalize_question_text(rec["topic"])
        topic_positions.setdefault(topic_key, len(topic_positions) + 1)
        concept_positions[topic_key] = concept_positions.get(topic_key, 0) + 1
        source_order = concept_positions[topic_key]
        existing = _find_concept_in_chapter(
            chapter, rec["concept_title"], pre_post=pre_post)
        if existing is not None:
            # A re-run must not leave legacy split analysis, stale Types, or
            # misplaced source questions in the workbook merely because a
            # concept title already exists.  The incoming record has passed
            # the current final contract, so it is the authoritative version.
            topic = _find_or_create_topic(db, chapter, rec["topic"], pre_post)
            topic.source_order = topic_positions[topic_key]
            existing.topic = topic
            # The accepted generated topology assigns this concept to exactly
            # one topic. Historical same-chapter ConceptTag rows are secondary
            # placements from an older map; retaining them makes a repaired
            # concept reappear under the wrong topic during scoped export.
            # Cross-chapter and opposite-learning-kind tags remain legitimate
            # reusable relationships and are preserved.
            for tag in list(existing.tags):
                tagged_topic = tag.topic
                if (
                    tagged_topic is not None
                    and tagged_topic.chapter_id == chapter.id
                    and tagged_topic.pre_post_learning == pre_post
                    and tagged_topic.id != topic.id
                ):
                    db.delete(tag)
            existing.concept_title = rec["concept_title"]
            existing.concept_display_name = rec["concept_title"]
            existing.parent_concept = rec.get("parent_concept", "")
            existing.concept_details = rec.get("concept_details", "")
            existing.keywords = rec.get("keywords", "")
            existing.source_order = source_order
            if source_book.strip():
                existing.sources = bi.merge_sources(existing.sources, source_book)
            db.flush()
            merged_ids.append(existing.id)
            continue
        topic = _find_or_create_topic(db, chapter, rec["topic"], pre_post)
        topic.source_order = topic_positions[topic_key]
        concept = _add_concept(db, topic, rec, source_book)
        concept.source_order = source_order
        db.flush()
        created_ids.append(concept.id)
    return created_ids, merged_ids


_BLANK_VALUES = {"", "na", "n/a", "none", "-", "tbd"}


def _is_blank(value: str) -> bool:
    return (value or "").strip().lower() in _BLANK_VALUES


def _parse_duration_minutes(value: str) -> int | None:
    """Parse finalized duration strings like '270 minutes' or '270'."""
    text = (value or "").strip().lower()
    if not text or text in _BLANK_VALUES:
        return None
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _chapter_meta_summary(
    chapter: models.Chapter,
    active_concept_ids: set[int] | None = None,
    *,
    pre_post: str | None = None,
) -> dict:
    """API-written chapter/topic metadata (empty dict in dry mode / on failure).

    The deterministic summaries in ``_sync_chapter_topic_summary`` are the
    fallback; a metadata failure must never fail a generation job that has
    already produced a valid concept map.
    """
    finalized = _parse_duration_minutes(chapter.chapter_duration)
    expected_duration = finalized or chapter_durations.lookup_duration_minutes(
        board=chapter.board,
        grade=chapter.grade,
        subject=chapter.subject,
        chapter_title=chapter.chapter_title,
    )
    active_concept_ids = set(active_concept_ids or ())
    scoped_topics = [
        topic for topic in chapter.topics
        if (
            (pre_post is None or topic.pre_post_learning == pre_post)
            and (
                not active_concept_ids
                or any(c.id in active_concept_ids for c in topic.concepts)
            )
        )
    ]
    topics_payload = [
        {
            "topic": t.topic_title,
            "pre_post_learning": t.pre_post_learning,
            "concepts": [
                c.concept_title
                for c in sorted(
                    (
                        c for c in t.concepts
                        if not active_concept_ids or c.id in active_concept_ids
                    ),
                    key=lambda c: (
                        c.source_order if c.source_order > 0 else 10**9,
                        c.id,
                    ),
                )
            ],
        }
        for t in sorted(
            scoped_topics,
            key=lambda t: (
                t.source_order if t.source_order > 0 else 10**9,
                t.id,
            ),
        )
    ]
    meta = generation._metadata(
        subject=chapter.subject, board=chapter.board, grade=chapter.grade,
        unit=chapter.unit, chapter_title=chapter.chapter_title,
        chapter_id=chapter.id, chapter_code=chapter.chapter_code,
        finalized_duration_minutes=expected_duration or 0,
    )
    # GPT writes the chapter description/duration/topic descriptions; retry
    # before ever falling back to deterministic summaries, so formula-estimate
    # durations (reviewed as wrong) only ship as an absolute last resort.
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            return generation.chapter_meta_via_api(meta=meta, topics=topics_payload)
        except Exception as exc:  # noqa: BLE001 — metadata must never kill the job
            last_exc = exc
            progress.log(
                f"Chapter/topic metadata attempt {attempt}/3 failed ({exc}).",
                level="warning",
            )
    progress.log(
        f"Chapter/topic metadata pass failed after retries ({last_exc}) — "
        "using deterministic summaries instead.",
        level="warning",
    )
    return {}


def _sync_chapter_topic_summary(
    chapter: models.Chapter,
    meta_summary: dict | None = None,
    *,
    active_concept_ids: set[int] | None = None,
    pre_post: str | None = None,
) -> None:
    """Refresh topic lists and fill the summary/duration fields.

    pre_topics / post_topics are comma-separated topic titles. When
    ``meta_summary`` (the API-written chapter/topic metadata) is available it
    OVERWRITES the chapter description, chapter duration, and per-topic
    descriptions — these fields were previously synthesized and read weak.
    Deterministic summaries remain the fallback for anything missing, so the
    output never ships "NA" in a required column.
    """
    meta_summary = meta_summary or {}
    active_concept_ids = set(active_concept_ids or ())
    all_topics = sorted(
        chapter.topics,
        key=lambda t: (
            t.source_order if t.source_order > 0 else 10**9,
            t.id,
        ),
    )
    active_topics = [
        topic for topic in all_topics
        if (
            (pre_post is None or topic.pre_post_learning == pre_post)
            and (
                not active_concept_ids
                or any(c.id in active_concept_ids for c in topic.concepts)
            )
        )
    ]
    topics = active_topics if active_topics else all_topics
    # pre/post topic columns list each topic by its tagged Topic Title (with the
    # code), matching the topic_title column exactly, so the importer links them.
    pre = [
        writer.composed_topic_title(t)
        for t in topics if t.pre_post_learning == "Pre"
    ]
    post = [
        writer.composed_topic_title(t)
        for t in topics if t.pre_post_learning == "Post"
    ]
    if pre_post != "Post":
        chapter.pre_topics = ", ".join(pre)
    if pre_post != "Pre":
        chapter.post_topics = ", ".join(post)

    # Per-topic description: API-written when available, else the concept list.
    topic_descriptions = meta_summary.get("topic_descriptions") or {}
    for t in topics:
        written = topic_descriptions.get(bi.normalize_question_text(t.topic_title))
        if written:
            t.topic_description = written
        else:
            names = [
                c.concept_title
                for c in sorted(
                    (
                        c for c in t.concepts
                        if not active_concept_ids or c.id in active_concept_ids
                    ),
                    key=lambda c: (
                        c.source_order if c.source_order > 0 else 10**9,
                        c.id,
                    ),
                )
            ]
            if names:
                # Always replace the fallback for the accepted topology. A
                # non-blank old description can be semantically stale after
                # concepts are moved between topics.
                t.topic_description = "Covers " + ", ".join(names) + "."

    # PURGED (Rule 1, CLAUDE.md:17-18), atomically with spec-step8 S8's
    # ``forced_blank_fields`` lever. Two code-composed fallbacks used to
    # sit here and both were VOLUME-DERIVED STRUCTURE:
    #
    #   chapter_description = f"This chapter develops {n_concepts}
    #       concept(s) across {len(topics)} topic(s): {topic_names}."
    #   chapter_duration    = f"{max(40, n_concepts * 12)} minutes"
    #       # "Rough classroom estimate: ~12 minutes per concept"
    #
    # Neither reads the book; both scale a learner-visible cell off a
    # count. They were invisible while ``chapter_duration`` shipped
    # force-blanked — un-blanking it through the profile without purging
    # them first would have shipped a manufactured number to every school,
    # a Rule-1 regression introduced by a portability fix. A chapter whose
    # description or duration nothing authored now ships BLANK, which is
    # honest, rather than filled with arithmetic dressed as content.
    if meta_summary.get("chapter_description"):
        chapter.chapter_description = meta_summary["chapter_description"]
    finalized = _parse_duration_minutes(chapter.chapter_duration)
    if finalized:
        chapter.chapter_duration = f"{finalized} minutes"
    elif meta_summary.get("chapter_duration_minutes") and _is_blank(chapter.chapter_duration):
        chapter.chapter_duration = f"{meta_summary['chapter_duration_minutes']} minutes"


# --------------------------------------------------------------------------- #
# Question / Task Inventory (extraction-completeness audit)
# --------------------------------------------------------------------------- #

def _store_inventory(job: models.UploadJob, artifacts: dict) -> None:
    """Persist the generation-time inventory + mined Types on the upload job."""
    inventory = artifacts.get("question_task_inventory") or {}
    mined = artifacts.get("mined_types") or {}
    final_grounding = artifacts.get(
        grounding_certificate.FINAL_CERTIFICATE_FIELD
    )
    if (
        not inventory.get("items")
        and not mined.get("types")
        and not isinstance(final_grounding, dict)
    ):
        return
    stored = {
        "items": inventory.get("items", []),
        "stats": inventory.get("stats", {}),
        "mined_types": mined.get("types", []),
    }
    if inventory.get("split_parents"):
        # A split question's parent must survive every persistence surface,
        # or a later refresh re-mints it beside its fragments (job 15).
        stored["split_parents"] = copy.deepcopy(inventory["split_parents"])
    reading = artifacts.get("chapter_reading")
    if isinstance(reading, dict):
        # Pass 1 provenance rides the durable job state so the coverage
        # ledger in the diagnostics export can account for the reading.
        stored["chapter_reading"] = {
            "provenance": copy.deepcopy(reading.get("provenance") or {}),
            "dropped_furniture": list(
                reading.get("dropped_furniture") or []
            )[:400],
            "census_rows": len(reading.get("census") or []),
        }
    certification_key = generation._PLACEMENT_CERTIFICATIONS_KEY
    certification_ledger = mined.get(certification_key)
    if isinstance(certification_ledger, dict):
        # Keep the established list-shaped ``mined_types`` field for CSV and
        # bundle compatibility while retaining the exact qid -> host evidence
        # needed to audit a successful job after its checkpoint is cleared.
        stored[certification_key] = copy.deepcopy(certification_ledger)
    type_case_ledger = generation._resolved_type_case_qid_placement_ledger(
        inventory,
        mined,
    )
    if isinstance(type_case_ledger, dict):
        stored[
            generation._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY
        ] = copy.deepcopy(type_case_ledger)
    if isinstance(final_grounding, dict):
        stored[
            grounding_certificate.FINAL_CERTIFICATE_FIELD
        ] = copy.deepcopy(final_grounding)
    job.question_inventory = stored
    _store_containers(job, artifacts)


def _store_containers(job: models.UploadJob, artifacts: dict) -> None:
    """Persist the §4 Phase-1.2 container projections beside the run.

    Pure projection of recorded verdicts (chapter-reading census, the
    canonical + its furniture ledgers, the inventory) into
    ``<artifact_dir>/source.containers.json`` so the views — and the
    verbatim dropped-furniture lines of both lanes, uncapped — ship in
    diagnostics. Best-effort: a missing canonical or artifact directory
    never blocks the run (the projection is rebuildable from durable
    inputs).
    """
    from . import canonical_source_phase2 as phase2
    from . import containers

    try:
        canonical = phase2.active_canonical()
        if not isinstance(canonical, dict):
            canonical = {}
        artifact_dir = None
        helper = getattr(uploads, "source_artifact_directory", None)
        if callable(helper) and getattr(job, "id", None):
            artifact_dir = helper(int(job.id))
        containers.persist_containers(
            artifact_dir,
            census=artifacts.get("chapter_reading"),
            canonical=canonical,
            inventory=artifacts.get("question_task_inventory") or {},
        )
    except Exception as exc:  # noqa: BLE001 — diagnostics must never block
        progress.log(
            f"Container projections were not persisted ({exc}); the "
            "views remain rebuildable from the durable artifacts.",
            level="warning",
        )


def _stable_checkpoint_value(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _generation_target_identity(chapter: models.Chapter) -> dict[str, str]:
    """Stable chapter identity that survives DB rebuilds and preview deploys."""
    return {
        field: _stable_checkpoint_value(getattr(chapter, field, ""))
        for field in (
            "board", "grade", "subject", "unit",
            "chapter_title", "chapter_code",
        )
    }


def _legacy_generation_checkpoint_fingerprint(
    job: models.UploadJob, chapter: models.Chapter,
) -> str:
    """Fingerprint emitted by schema-v2 checkpoints before stable identities."""
    payload = (
        "post-learning-checkpoint-v1\0"
        + "\0".join(str(value or "") for value in (
            chapter.id,
            chapter.board,
            chapter.grade,
            chapter.subject,
            chapter.unit,
            chapter.chapter_title,
            chapter.chapter_code,
            job.mmd_text,
        ))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_instruction_set_sha256(instruction_set_sha256: str | None) -> str:
    """Resolve the instruction identity a fingerprint binds to.

    ``None`` (callers that do not assemble: tests, legacy
    checkpoints without the stored field) resolves to the hash of the EMPTY
    instruction set over the current frozen core — so even those
    fingerprints move when a frozen-core prompt changes, which is the
    governance point (docs/aegis-restructure.md §8.1).
    """
    if instruction_set_sha256 is None:
        return instruction_architect.empty_set_sha256()
    return str(instruction_set_sha256 or "")


def _generation_checkpoint_fingerprint(
    job: models.UploadJob, chapter: models.Chapter,
    *,
    instruction_set_sha256: str | None = None,
) -> str:
    """Semantic input fingerprint, intentionally independent of DB/git IDs.

    v3 appends the Architect's ``instruction_set_sha256``: a checkpoint made
    under one instruction set can never resume a run under another
    (validator mirror: ``checkpoints._expected_fingerprint``).
    """
    identity = _generation_target_identity(chapter)
    payload = (
        "concept-generation-checkpoint-v3\0"
        + "\0".join([
            _stable_checkpoint_value(job.learning_kind or "post"),
            *(identity[field] for field in (
                "board", "grade", "subject", "unit",
                "chapter_title", "chapter_code",
            )),
            str(job.mmd_text or ""),
            _run_instruction_set_sha256(instruction_set_sha256),
        ])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checkpoint_matches_generation(
    checkpoint: dict, *,
    job: models.UploadJob,
    chapter: models.Chapter,
    instruction_set_sha256: str | None = None,
) -> bool:
    if not generation._valid_concept_checkpoint(checkpoint):
        return False
    return _checkpoint_identity_matches_generation(
        checkpoint,
        job=job,
        chapter=chapter,
        instruction_set_sha256=instruction_set_sha256,
    )


def _checkpoint_identity_matches_generation(
    checkpoint: dict,
    *,
    job: models.UploadJob,
    chapter: models.Chapter,
    instruction_set_sha256: str | None = None,
) -> bool:
    """Match source/target identity independently of stage compatibility.

    A deployment can intentionally invalidate every saved stage contract.  A
    same-source envelope must then normalize to an empty restart instead of
    being misreported as a different upload or chapter. The fingerprint
    binds the Architect's instruction identity too, so a checkpoint made
    under a different instruction set never matches (rewind, not replay).
    """

    stable_fingerprint = _generation_checkpoint_fingerprint(
        job, chapter, instruction_set_sha256=instruction_set_sha256)
    target_identity = _generation_target_identity(chapter)
    if checkpoint.get("checkpoint_format") == generation._CONCEPT_CHECKPOINT_FORMAT:
        return bool(
            checkpoint.get("schema_version")
            == generation._CONCEPT_CHECKPOINT_SCHEMA
            and checkpoint.get("fingerprint") == stable_fingerprint
            and checkpoint.get("target_identity") == target_identity
        )
    if checkpoint.get("schema_version") == generation._LEGACY_CONCEPT_CHECKPOINT_SCHEMA:
        return bool(
            checkpoint.get("fingerprint")
            == _legacy_generation_checkpoint_fingerprint(job, chapter)
            and checkpoint.get("target_chapter_id") == chapter.id
        )
    # Direct schema-v3 entries were briefly supported before the history
    # envelope.  Accept their stable fingerprint, retaining the old target-id
    # check only when no stable identity was saved.
    return bool(
        checkpoint.get("schema_version")
        == generation._CONCEPT_CHECKPOINT_SCHEMA
        and checkpoint.get("fingerprint") == stable_fingerprint
        and (
            checkpoint.get("target_identity") == target_identity
            or (
                not checkpoint.get("target_identity")
                and checkpoint.get("target_chapter_id") == chapter.id
            )
        )
    )


def _merge_generation_checkpoint_history(
    stored: dict | None,
    checkpoint: dict,
    *,
    fingerprint: str,
    target_identity: dict[str, str],
    target_chapter_id: int,
    instruction_set_sha256: str | None = None,
) -> dict:
    """Keep the newest completed artifact per stage in one portable envelope.

    A callback may durably remove a rejected stage by sending the control event
    ``{"checkpoint_action": "discard_stage", "stage": "<stage>"}``. Optional
    diagnostic fields such as ``reason`` are ignored and are never persisted as
    checkpoint history.
    """
    history = [
        copy.deepcopy(entry)
        for entry in generation._concept_checkpoint_entries(stored)
        if isinstance(entry, dict) and str(entry.get("stage") or "").strip()
    ]
    stage = str(checkpoint.get("stage") or "").strip()
    checkpoint_action = str(
        checkpoint.get("checkpoint_action") or "").strip()
    if checkpoint_action:
        if checkpoint_action != "discard_stage":
            raise ValueError(
                f"unsupported generation checkpoint action: "
                f"{checkpoint_action!r}"
            )
        if not stage:
            raise ValueError(
                "generation checkpoint discard_stage requires a stage"
            )
        history = [
            entry for entry in history
            if str(entry.get("stage") or "") != stage
        ]
        if not history:
            return {}
        # A discard control event is not itself a checkpoint. Mirror the
        # furthest remaining durable stage so API/UI consumers immediately see
        # the checkpoint that the next retry will actually resume from.
        newest = max(
            enumerate(history),
            key=lambda indexed: (
                generation._checkpoint_order(
                    str(indexed[1].get("stage") or "")),
                indexed[0],
            ),
        )[1]
        checkpoint = newest
    else:
        source_resolution_applied = bool(
            stage == "source_graph_review"
            and checkpoint.get("source_review_resolution_applied")
        )
        if source_resolution_applied:
            # Applying a verified-PDF block changes semantic_source_sha256.
            # Every concept stage was derived from the old text
            # and must be rebuilt. Keep only the ready Phase 3 graph so its
            # already-paid hierarchy and critic work remains reusable.
            source_order = generation._checkpoint_order(
                "source_graph_review")
            history = [
                entry for entry in history
                if generation._checkpoint_order(
                    str(entry.get("stage") or "")
                ) <= source_order
            ]
        if stage == "source_topic_review":
            # A missing numbered source topic invalidates every concept,
            # inventory, Type, and final artifact derived after topology. Do
            # not let an older 81%/98% entry outrank the new 35% human gate on
            # resume; retaining it would replay the exact omission that caused
            # the pause.
            topology_order = generation._checkpoint_order(stage)
            history = [
                entry for entry in history
                if generation._checkpoint_order(
                    str(entry.get("stage") or "")
                ) <= topology_order
            ]
        history = [
            entry for entry in history
            if str(entry.get("stage") or "") != stage
        ]
        history.append(copy.deepcopy(checkpoint))
        # Source verification may pause before semantic generation even while
        # later completed concept work (for example the 81% Type checkpoint)
        # remains reusable. Keep the source-review stage in history, but mirror
        # the furthest completed stage at the envelope top level so the UI and
        # resume policy never appear to regress from 81% to 5%.
        checkpoint = max(
            enumerate(history),
            key=lambda indexed: (
                generation._checkpoint_order(
                    str(indexed[1].get("stage") or "")),
                indexed[0],
            ),
        )[1]
    merged = {
        "schema_version": generation._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoint_format": generation._CONCEPT_CHECKPOINT_FORMAT,
        "fingerprint": fingerprint,
        # The instruction identity the fingerprint was computed under; the
        # bundle validator recomputes the fingerprint from this stored value
        # (checkpoints._expected_fingerprint), and resume compares against
        # the CURRENT assembly — mismatch rewinds instead of replaying.
        "instruction_set_sha256": _run_instruction_set_sha256(
            instruction_set_sha256),
        "target_identity": copy.deepcopy(target_identity),
        # Informational only for schema v3; compatibility uses target_identity.
        "target_chapter_id": target_chapter_id,
        # Mirror newest metadata at the top level for the existing API/UI.
        "stage": checkpoint.get("stage", ""),
        "stage_order": checkpoint.get("stage_order", -1),
        "stage_schema_version": checkpoint.get("stage_schema_version", 1),
        "stage_label": checkpoint.get("stage_label", ""),
        "saved_at": checkpoint.get("saved_at", ""),
        "progress": checkpoint.get("progress", 0.0),
        "checkpoints": history,
    }
    merged = _copy_human_decision_ledger(merged, stored)
    return _consume_applied_human_decisions(merged, checkpoint)


def _compatible_generation_checkpoint_envelope(
    stored: dict | None,
    *,
    fingerprint: str,
    target_identity: dict[str, str],
    target_chapter_id: int,
    instruction_set_sha256: str | None = None,
) -> dict:
    """Prune unusable history and mirror the stage this deployment will use."""
    history = [
        copy.deepcopy(entry)
        for entry in generation._concept_checkpoint_entries(stored)
        if generation._compatible_concept_checkpoint_entry(
            entry,
            # UploadJob checkpoints feed publication, not the standalone
            # semantic transformer. An uncertified terminal row is never the
            # durable stage mirrored for Resume in a live run.
            require_final_grounding=config.use_live_generation(),
        )
    ]
    if not history:
        return {}
    candidate = {
        "schema_version": generation._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoint_format": generation._CONCEPT_CHECKPOINT_FORMAT,
        "checkpoints": history,
    }
    newest = generation._newest_compatible_concept_checkpoint(candidate)
    if newest is None:
        return {}
    stage = str(newest.get("stage") or "")
    normalized = {
        "schema_version": generation._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoint_format": generation._CONCEPT_CHECKPOINT_FORMAT,
        "fingerprint": fingerprint,
        "instruction_set_sha256": _run_instruction_set_sha256(
            instruction_set_sha256),
        "target_identity": copy.deepcopy(target_identity),
        "target_chapter_id": target_chapter_id,
        "stage": stage,
        "stage_order": newest.get(
            "stage_order", generation._checkpoint_order(stage)),
        "stage_schema_version": newest.get("stage_schema_version", 1),
        "stage_label": newest.get("stage_label", ""),
        "saved_at": newest.get("saved_at", ""),
        "progress": newest.get("progress", 0.0),
        "checkpoints": history,
    }
    normalized = _copy_human_decision_ledger(normalized, stored)
    return _prune_human_decisions_after_checkpoint_fallback(
        normalized,
        retained_stage=stage,
    )


def _persist_compatible_generation_checkpoint_mirror(
    db: Session,
    job: models.UploadJob,
    *,
    fingerprint: str,
    target_identity: dict[str, str],
    target_chapter_id: int,
    instruction_set_sha256: str | None = None,
) -> tuple[dict | None, dict]:
    """Persist the actual fallback stage before a resumed run can fail."""
    stored = copy.deepcopy(job.generation_checkpoint or {})
    normalized = _compatible_generation_checkpoint_envelope(
        stored,
        fingerprint=fingerprint,
        target_identity=target_identity,
        target_chapter_id=target_chapter_id,
        instruction_set_sha256=instruction_set_sha256,
    )
    if not normalized and _source_replacement_required(stored):
        # A user's explicit source-authority decision remains terminal even if
        # a deployment invalidated the only serialized generation stage.  Do
        # not erase that stop merely to represent an empty compatible history.
        return stored, {}
    resumed = generation._newest_compatible_concept_checkpoint(
        normalized) or {}
    if normalized != stored:
        original_count = len(
            generation._concept_checkpoint_entries(stored))
        retained_count = len(
            generation._concept_checkpoint_entries(normalized))
        label = (
            resumed.get("stage_label")
            or resumed.get("stage")
            or "the beginning"
        )
        job.generation_checkpoint = normalized
        job.detail = (
            f"Checkpoint compatibility refreshed; retry resumes from "
            f"{label}."
        )
        db.commit()
        drive_checkpoints.schedule_checkpoint_backup(job.id)
        progress.log(
            "Persisted compatible checkpoint fallback "
            f"'{label}' before resume; removed "
            f"{max(0, original_count - retained_count)} incompatible "
            "stage(s).",
            level="warning",
        )
    return (normalized or None), resumed


def _checkpoint_mismatch_message(
    checkpoint: dict,
    *,
    expected_fingerprint: str,
    expected_target: dict[str, str],
) -> str:
    saved_target = checkpoint.get("target_identity")
    saved_fingerprint = str(checkpoint.get("fingerprint") or "")
    return (
        "The saved generation checkpoint does not match the selected "
        "chapter or converted source and has been preserved. "
        f"Expected target={expected_target}, source={expected_fingerprint[:12]}; "
        f"saved target={saved_target or '(legacy/unknown)'}, "
        f"source={saved_fingerprint[:12] or '(unknown)'}. "
        "Select the matching chapter/source, or explicitly start over to clear "
        "the saved checkpoint."
    )




def _semantic_recovery_source_text(raw_source: str) -> str:
    """Use the verified semantic graph rendering as GPT repair evidence."""
    try:
        from . import canonical_source_phase3 as phase3

        session = phase3.active_session()
        graph = (
            phase3.active_graph()
            or (
                session.get("graph")
                if isinstance(session, dict)
                else None
            )
        )
        canonical = (
            session.get("canonical")
            if isinstance(session, dict)
            else None
        )
    except Exception:
        graph = None
        canonical = None
    if isinstance(graph, dict) and isinstance(canonical, dict):
        # Once a verified source graph exists, silently substituting an older
        # Phase 2/raw rendering would make repair evidence disagree with the
        # checkpoint identity. Rendering failure is therefore a hard stop.
        rendered = phase3.render_semantic_source(graph, canonical)
        if not str(rendered or "").strip():
            raise RuntimeError(
                "verified semantic source graph rendered empty during recovery"
            )
        return str(rendered)
    # Phase 2.2 overlays are also verified source evidence. This helper is
    # fail-safe: the immutable converted source remains the final fallback.
    try:
        semantic = canonical_source_phase22.active_semantic_source(raw_source)
        if str(semantic or "").strip():
            return str(semantic)
    except Exception:
        pass
    return str(raw_source or "")







def _stage_concept_workbook(
    db: Session,
    target: Path,
    concept_ids: list[int],
) -> tuple[Path, dict[str, int]]:
    """Write a sibling workbook copy before any concept transaction commits."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        suffix=target.suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    staged = Path(staged_name)
    try:
        if target.exists():
            shutil.copy2(target, staged)
        else:
            # ``append_concepts`` creates a canonical workbook when the path
            # does not exist.
            staged.unlink()
        # ``sibling_for`` only NAMES any retained pre-migration copy. Without
        # it the copy inherits ``mkstemp``'s dot-hidden random stem, so the
        # recorded recovery path points at a hidden file that does not say
        # which workbook it came from and every run leaves a fresh orphan.
        written = writer.append_concepts(
            db, staged, concept_ids, sibling_for=target)
        return staged, written
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _publish_staged_workbook(staged: Path, target: Path) -> None:
    """Atomically replace the canonical workbook with its staged sibling."""
    workbook_sync.atomic_publish(staged, target)


def _commit_and_publish_concept_workbook(
    db: Session,
    target: Path,
    concept_ids: list[int],
) -> dict[str, int]:
    """Stage, commit, and publish while holding the shared workbook lock.

    Ordering is transactional-outbox shaped: any interrupted earlier
    publication is completed first, the intent to publish this staged sibling
    is durably recorded before the database commits, and only then is the
    staged workbook published and the intent cleared. A failure before the
    commit rolls everything back; a failure after the commit leaves the
    staged workbook queued in the outbox, so the export converges with the
    committed database state instead of silently diverging.
    """
    staged_workbook: Path | None = None
    committed = False
    with workbook_sync.output_workbook_lock():
        if workbook_sync.recover_pending_publication(target):
            progress.log(
                "Completed a previously interrupted workbook publication "
                "before staging new concept rows.",
                level="warning",
            )
        try:
            staged_workbook, written = _stage_concept_workbook(
                db,
                target,
                concept_ids,
            )
            workbook_sync.record_publication_intent(staged_workbook, target)
            db.commit()
            committed = True
            _publish_staged_workbook(staged_workbook, target)
            workbook_sync.clear_publication_intent(target)
            staged_workbook = None
            return written
        except Exception as exc:
            if not committed:
                db.rollback()
                workbook_sync.clear_publication_intent(target)
                if staged_workbook is not None:
                    staged_workbook.unlink(missing_ok=True)
                raise
            # The database commit is durable and cannot be undone here. The
            # staged workbook and its outbox record survive, so the next
            # workbook operation completes this exact publication.
            progress.log(
                "Concept rows are committed but workbook publication was "
                f"interrupted ({exc}); the staged workbook remains queued "
                "and will be published on the next workbook operation.",
                level="error",
            )
            raise workbook_sync.WorkbookPublicationPending(
                "concept deposit is committed; workbook publication is "
                f"queued for automatic completion: {exc}"
            ) from exc


def _deposit_and_publish_concepts(
    db: Session,
    *,
    chapter_id: int,
    records: list[dict],
    pre_post: str,
    source_book: str,
    inventory: dict | None = None,
    mined_types: dict | None = None,
    source_text: str = "",
    final_grounding_certificate: dict | None = None,
    grounding_audit_job: models.UploadJob | None = None,
) -> tuple[list[int], list[int], dict]:
    """Serialize final dedupe, DB commit, and shared workbook publication."""
    # Phase 2.2 may have verified source-visible text that Mathpix omitted.
    # Deposit validation sees the same derived semantic source as concept
    # extraction, while UploadJob.mmd_text remains the immutable audit copy.
    source_text = canonical_source_phase22.active_semantic_source(source_text)
    with workbook_sync.output_workbook_lock():
        certificate_sink: dict = {}
        if grounding_audit_job is not None:
            # The caller has just attached generation artifacts to this job.
            # Flush them before expiring stale chapter relationships so the
            # concept/workbook transaction retains both the input certificate
            # and the deposited-certificate audit record.
            db.flush()
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            raise ValueError("target chapter not found")
        created_ids, merged_ids = _deposit_concepts(
            db,
            chapter,
            records,
            pre_post,
            source_book,
            inventory=inventory,
            mined_types=mined_types,
            source_text=source_text,
            final_grounding_certificate=final_grounding_certificate,
            grounding_certificate_sink=certificate_sink,
        )
        if (
            pre_post == "Post"
            and final_grounding_certificate is not None
            and not isinstance(certificate_sink.get("certificate"), dict)
        ):
            raise DepositValidationError(
                "post-learning deposit did not produce a verified final "
                "grounding certificate"
            )
        deposited_certificate = certificate_sink.get("certificate")
        if (
            isinstance(deposited_certificate, dict)
            and grounding_audit_job is not None
        ):
            inventory_audit = copy.deepcopy(
                grounding_audit_job.question_inventory or {}
            )
            inventory_audit[
                _DEPOSITED_GROUNDING_CERTIFICATE_KEY
            ] = copy.deepcopy(deposited_certificate)
            grounding_audit_job.question_inventory = inventory_audit
        active_ids = set(created_ids + merged_ids)
        _sync_chapter_topic_summary(
            chapter,
            _chapter_meta_summary(
                chapter,
                active_ids,
                pre_post=pre_post,
            ),
            active_concept_ids=active_ids,
            pre_post=pre_post,
        )
        written = _commit_and_publish_concept_workbook(
            db,
            config.BULK_IMPORT_OUTPUT,
            created_ids + merged_ids,
        )
        if isinstance(certificate_sink.get("certificate"), dict):
            written["grounding_certificate"] = copy.deepcopy(
                certificate_sink["certificate"]
            )
        return created_ids, merged_ids, written


_INVENTORY_CSV_COLUMNS = [
    "qid", "order_index", "source_kind", "source_label", "parent_source_label",
    "topic_hint", "page_hint", "subpart_label", "requires_visual",
    "requires_context", "normalized_task", "raw_task",
    "raw_solution_or_answer", "shared_context", "image_urls", "content_objects",
    "classified", "mined_type_ids", "mined_type_titles",
]
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe_cell(value):
    """Prevent spreadsheet formula execution when a CSV is opened directly."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def inventory_csv(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> str:
    """Render the stored Question / Task Inventory as CSV.

    One row per extracted question/task, with the mined Type(s) each item was
    classified into — so completeness of both extraction and classification
    can be audited at a glance.
    """
    import csv
    import io
    import json

    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    data = job.question_inventory or {}
    items = data.get("items", [])
    mined_types = data.get("mined_types", [])
    if not items:
        # A release-first job keeps its inventory inside the staged release
        # payload; the download must serve it from there (the webpage's
        # Question/Task Inventory button 404ed on every fresh run).
        from .build_concepts_release import RELEASE_KEY

        release = data.get(RELEASE_KEY) or {}
        release_inventory = release.get("question_task_inventory") or {}
        items = release_inventory.get("items", [])
        release_types = release.get("mined_types") or {}
        if isinstance(release_types, dict):
            mined_types = release_types.get("types", [])
        elif isinstance(release_types, list):
            mined_types = release_types
    if not items:
        raise ValueError(
            "no question/task inventory stored for this job — run a live "
            "concept generation first")

    types_by_qid: dict[str, list[tuple[str, str]]] = {}
    for t in mined_types:
        tid = (t.get("type_id") or "").strip()
        title = (t.get("type_title") or "").strip()
        qids = set(t.get("source_question_ids") or [])
        for case in t.get("case_prompts") or []:
            if not isinstance(case, dict):
                continue
            if case.get("source_question_id"):
                qids.add(case["source_question_id"])
            for ex in case.get("examples") or []:
                if isinstance(ex, dict) and ex.get("source_question_id"):
                    qids.add(ex["source_question_id"])
        for qid in qids:
            types_by_qid.setdefault((qid or "").strip(), []).append((tid, title))

    buf = io.StringIO()
    writer_ = csv.DictWriter(buf, fieldnames=_INVENTORY_CSV_COLUMNS, extrasaction="ignore")
    writer_.writeheader()
    for item in items:
        qid = (item.get("qid") or "").strip()
        assigned = types_by_qid.get(qid, [])
        row = {col: item.get(col, "") for col in _INVENTORY_CSV_COLUMNS}
        row["content_objects"] = json.dumps(
            item.get("content_objects") or {}, ensure_ascii=False)
        row["image_urls"] = ", ".join(
            str(u) for u in (item.get("image_urls") or []) if u)
        row["requires_visual"] = "yes" if item.get("requires_visual") else "no"
        row["requires_context"] = "yes" if item.get("requires_context") else "no"
        row["classified"] = "yes" if assigned else "no"
        row["mined_type_ids"] = ", ".join(tid for tid, _ in assigned if tid)
        row["mined_type_titles"] = "; ".join(title for _, title in assigned if title)
        writer_.writerow({
            column: _csv_safe_cell(value)
            for column, value in row.items()
        })
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Post Learning
# --------------------------------------------------------------------------- #

def _retire_obsolete_machine_metadata_pause(
    db: Session,
    job: models.UploadJob,
    checkpoint: dict | None,
    pending: dict,
) -> bool:
    """Retire only a hash-proven legacy compiler-metadata false positive."""

    if (
        str(pending.get("kind") or "") != "phase3_source_graph_review"
        or str((pending.get("item") or {}).get("type_id") or "")
        != "semantic_source_rich_text"
    ):
        return False
    stored = copy.deepcopy(job.generation_checkpoint or {})
    if (
        not isinstance(checkpoint, dict)
        or str(stored.get("fingerprint") or "")
        != str(checkpoint.get("fingerprint") or "")
    ):
        return False
    stored_pending = _pending_human_decision(stored)
    decision_id = str(pending.get("decision_id") or "")
    context_hash = str(pending.get("context_hash") or "")
    if not isinstance(stored_pending, dict) or (
        str(stored_pending.get("decision_id") or "") != decision_id
        or str(stored_pending.get("context_hash") or "") != context_hash
    ):
        return False
    source_stage = generation._newest_compatible_concept_checkpoint(
        stored,
        allowed_stages={"source_graph_review"},
    )
    if not isinstance(source_stage, dict):
        return False
    if (
        str(source_stage.get("source_review_decision_id") or "")
        != decision_id
        or str(source_stage.get("source_review_context_hash") or "")
        != context_hash
    ):
        return False
    graph = source_stage.get("source_review_graph")
    if not isinstance(graph, dict):
        return False

    from . import canonical_source_phase2 as phase2
    from . import canonical_source_phase3 as phase3

    canonical = phase2.active_canonical()
    if not isinstance(canonical, dict):
        canonical, _report = phase2._load_or_refresh_for_job(job)
    if not phase3.machine_metadata_source_review_is_obsolete(
        graph,
        canonical=canonical,
        decision_id=decision_id,
        context_hash=context_hash,
    ):
        return False

    ledger = _human_decision_ledger(stored)
    ledger["pending"] = None
    ledger["deferred_assignment_unit_ids"] = []
    stored[_HUMAN_DECISIONS_KEY] = ledger
    detail = (
        "Retired an obsolete compiler-metadata source pause; generation will "
        "reuse the verified semantic graph and continue."
    )
    result = db.execute(
        update(models.UploadJob)
        .where(
            models.UploadJob.id == job.id,
            models.UploadJob.generation_checkpoint
            == copy.deepcopy(job.generation_checkpoint or {}),
        )
        .values(
            generation_checkpoint=copy.deepcopy(stored),
            detail=detail,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        db.refresh(job)
        if isinstance(checkpoint, dict):
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(job.generation_checkpoint or {}))
        return False
    db.commit()
    db.refresh(job)
    drive_checkpoints.schedule_checkpoint_backup(job.id)
    if isinstance(checkpoint, dict):
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(job.generation_checkpoint or {}))
    progress.log(
        "Removed the legacy compiler-metadata false positive from the saved "
        "source checkpoint; no model request was started.",
        level="success",
    )
    return True


def _existing_human_decision_pause(
    db: Session,
    job: models.UploadJob,
    checkpoint: dict | None,
    *,
    agent_resolution_ids: set[str] | None = None,
    owner_sub: str | None = None,
) -> dict | None:
    """Resume a saved directive, try one upgraded resolver, or return pause."""
    pending = _pending_human_decision(checkpoint)
    if pending is None:
        return None
    agent_review = pending.get("agent_review")
    if (
        agent_resolution_ids is not None
        and isinstance(agent_review, dict)
        and agent_review.get("status") == "resolved"
    ):
        # The agent response was committed before its directive was recorded.
        # Recreate that directive without another provider request, seal it as
        # consumed, and expose it only to this process's immediate continuation.
        resolution = _record_human_semantic_decision_locked(
            db,
            job,
            str(pending.get("decision_id") or ""),
            choice=str(agent_review.get("choice") or ""),
            instruction=str(agent_review.get("instruction") or ""),
            target_id=str(agent_review.get("target_id") or ""),
            target_concept_id=str(
                agent_review.get("target_concept_id") or ""
            ),
            resolved_by="agent",
            resolution_status="consumed",
        )
        agent_resolution_ids.add(str(
            resolution["resolved_decision"]["decision_id"]
        ))
        if isinstance(checkpoint, dict):
            refreshed_checkpoint = copy.deepcopy(
                job.generation_checkpoint or {}
            )
            checkpoint.clear()
            checkpoint.update(refreshed_checkpoint)
        progress.log(
            "Reused the saved autonomous semantic resolution; no second "
            "resolution-agent request was started.",
            level="success",
        )
        return None
    if _retire_obsolete_machine_metadata_pause(
        db,
        job,
        checkpoint,
        pending,
    ):
        return None
    pending = _pending_human_decision(checkpoint)
    if pending is None:
        return None
    # A newly created pause gets its first bounded review in
    # _run_with_human_decision_pause. Resume only upgrades a terminal legacy
    # review; it must not turn an intentionally agent-disabled pause into a
    # surprise paid request later.
    if agent_resolution_ids is not None and isinstance(
        pending.get("agent_review"), dict
    ):
        resolved_id = _autonomously_resolve_pending_decision(
            db,
            job,
            pending,
            owner_sub=owner_sub,
        )
        if resolved_id:
            agent_resolution_ids.add(resolved_id)
            if isinstance(checkpoint, dict):
                refreshed_checkpoint = copy.deepcopy(
                    job.generation_checkpoint or {}
                )
                checkpoint.clear()
                checkpoint.update(refreshed_checkpoint)
            return None
        if isinstance(checkpoint, dict):
            refreshed_checkpoint = copy.deepcopy(
                job.generation_checkpoint or {}
            )
            checkpoint.clear()
            checkpoint.update(refreshed_checkpoint)
        pending = _pending_human_decision(job.generation_checkpoint)
        if pending is None:
            return None
    # Last choke point on the resume path: a saved pause whose resolver
    # pathways are exhausted still continues with the safest offered action
    # rather than waiting for a manual review click.
    continued_id = _apply_last_resort_safe_continuation(
        db,
        job,
        pending,
        owner_sub=owner_sub,
    )
    if continued_id is None:
        # Q13 (seam F48): before the retired halt fires, The Fixer picks
        # among ALL routes — user-only ones included — once, recorded and
        # flagged. Only protocol impossibility falls through to the raise.
        continued_id = _apply_fixer_resolution_choice(
            db,
            job,
            pending,
            owner_sub=owner_sub,
        )
    if continued_id:
        if agent_resolution_ids is not None:
            agent_resolution_ids.add(continued_id)
        if isinstance(checkpoint, dict):
            refreshed_checkpoint = copy.deepcopy(
                job.generation_checkpoint or {}
            )
            checkpoint.clear()
            checkpoint.update(refreshed_checkpoint)
        return None
    # Always raises; there is no pause to fall through to.
    _raise_if_unattended_cannot_pause(pending)
    raise AssertionError(  # pragma: no cover - defensive
        "generation reached a pause that no longer exists"
    )


def _agent_review_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_review_history(
    checkpoint: dict | None,
    *,
    include_pending: bool = True,
) -> list[dict]:
    rows: list[dict] = []
    ledger = _human_decision_ledger(checkpoint)
    pending = ledger.get("pending")
    if include_pending and isinstance(pending, dict):
        review = pending.get("agent_review")
        if isinstance(review, dict):
            rows.append(copy.deepcopy(review))
    rows.extend(
        copy.deepcopy(review)
        for review in ledger.get("agent_review_history") or []
        if isinstance(review, dict)
    )
    for resolution in ledger.get("resolutions") or []:
        if not isinstance(resolution, dict):
            continue
        original = resolution.get("pending_decision")
        review = (
            original.get("agent_review")
            if isinstance(original, dict) else None
        )
        if isinstance(review, dict):
            rows.append(copy.deepcopy(review))
    return rows


def _persist_pending_agent_review(
    db: Session,
    job: models.UploadJob,
    *,
    decision_id: str,
    context_hash: str,
    review: dict,
    owner_sub: str | None,
    persist_usage: bool = False,
) -> dict:
    """Update only the current pending review, preserving its immutable hash."""

    usage: dict | None = None
    if persist_usage:
        # Account for the provider response before making its directive
        # reusable. A crash after this commit leaves request_started (human
        # fallback) rather than a terminal verdict with missing billed usage.
        usage = uploads.persist_current_openai_usage(
            db, job.id, owner_sub=owner_sub
        )
    db.refresh(job)
    original_checkpoint = copy.deepcopy(job.generation_checkpoint or {})
    checkpoint = copy.deepcopy(original_checkpoint)
    ledger = _human_decision_ledger(checkpoint)
    pending = ledger.get("pending")
    if (
        not isinstance(pending, dict)
        or str(pending.get("decision_id") or "") != decision_id
        or str(pending.get("context_hash") or "") != context_hash
    ):
        raise HumanDecisionConflictError(
            "the autonomous semantic review context is no longer pending"
        )
    validated_review = schemas.AgentSemanticReview.model_validate(
        review
    ).model_dump()
    next_status = str(validated_review.get("status") or "")
    if next_status == "resolved":
        if not autonomous_resolution.is_automatable_choice(
            validated_review.get("choice")
        ):
            raise ValueError(
                "an autonomous review cannot resolve a user-only choice"
            )
        if str(validated_review.get("instruction") or "").strip():
            raise ValueError(
                "an autonomous review instruction must be empty; "
                "explanatory prose belongs in reason"
            )
    next_issue_key = str(validated_review.get("issue_key") or "")
    next_capability_key = str(
        validated_review.get("capability_key") or ""
    )
    current_review = pending.get("agent_review")
    if next_status == "request_started" and isinstance(current_review, dict):
        current_status = str(current_review.get("status") or "")
        current_capability_key = str(
            current_review.get("capability_key") or ""
        )
        if (
            current_status not in {"escalated", "unavailable"}
            or not next_capability_key
            or next_capability_key == current_capability_key
        ):
            raise HumanDecisionConflictError(
                "this semantic review was already claimed"
            )
        review_history = [
            copy.deepcopy(row)
            for row in ledger.get("agent_review_history") or []
            if isinstance(row, dict)
        ]
        if len(review_history) >= _MAX_AGENT_REVIEW_HISTORY:
            raise HumanDecisionConflictError(
                "the autonomous semantic review history is full"
            )
        review_history.append(copy.deepcopy(current_review))
        ledger["agent_review_history"] = review_history
    if next_status in {"resolved", "unavailable"}:
        if (
            not isinstance(current_review, dict)
            or current_review.get("status") != "request_started"
            or str(current_review.get("issue_key") or "") != next_issue_key
            or (
                str(current_review.get("capability_key") or "")
                != next_capability_key
            )
        ):
            raise HumanDecisionConflictError(
                "the semantic review result has no matching dispatch claim"
            )
    if next_status == "escalated" and isinstance(current_review, dict):
        if (
            str(current_review.get("issue_key") or "") != next_issue_key
            or (
                str(current_review.get("capability_key") or "")
                != next_capability_key
            )
        ):
            raise HumanDecisionConflictError(
                "the semantic escalation does not match the claimed issue"
            )
    pending = copy.deepcopy(pending)
    pending["agent_review"] = validated_review
    if usage is not None:
        pending["cumulative_usage"] = copy.deepcopy(usage)
    ledger["pending"] = pending
    checkpoint[_HUMAN_DECISIONS_KEY] = ledger
    detail = (
        "Aegis is reviewing the saved semantic discrepancy once."
        if review.get("status") == "request_started"
        else "Generation paused after Aegis's bounded semantic review."
    )
    claimed = db.execute(
        update(models.UploadJob)
        .where(
            models.UploadJob.id == job.id,
            models.UploadJob.generation_checkpoint == original_checkpoint,
        )
        .values(
            generation_checkpoint=copy.deepcopy(checkpoint),
            detail=detail,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        db.refresh(job)
        raise HumanDecisionConflictError(
            "another worker already claimed or changed this semantic review"
        )
    db.commit()
    db.refresh(job)
    drive_checkpoints.schedule_checkpoint_backup(job.id)
    return copy.deepcopy(pending)


def _autonomously_resolve_pending_decision(
    db: Session,
    job: models.UploadJob,
    pending: dict,
    *,
    owner_sub: str | None,
) -> str | None:
    """Resolve one distinct issue or leave its existing human pause intact."""

    deterministic_result = (
        autonomous_resolution.verified_source_patch_resolution(pending)
    )
    if deterministic_result is None and not autonomous_resolution.enabled():
        return None
    checkpoint = copy.deepcopy(job.generation_checkpoint or {})
    issue_key = autonomous_resolution.issue_key(pending)
    capability_key = autonomous_resolution.capability_key(
        pending, checkpoint=checkpoint
    )
    offered_candidate_count = len([
        row for row in pending.get("candidates") or []
        if isinstance(row, dict)
    ])
    workspace_audit = {
        "capability_key": capability_key,
        "workspace_hash": capability_key,
        "offered_candidate_count": offered_candidate_count,
        # request_started proves the dispatch claim, not how much of the
        # packet the provider ultimately received. The terminal review stores
        # the actual post-compaction detail count.
        "inspected_candidate_count": 0,
    }
    current_review = pending.get("agent_review")
    upgrading_current_review = False
    if isinstance(current_review, dict):
        current_status = str(current_review.get("status") or "")
        current_capability_key = str(
            current_review.get("capability_key") or ""
        )
        if (
            current_status == "escalated"
            and str(current_review.get("reason") or "").startswith(
                "Aegis saved an autonomous action, but the previous worker "
                "ended before a later checkpoint proved"
            )
        ):
            # This is not a critic pathway turnover: the identical decision
            # reappeared after a crash at an unknowable boundary. Reapplying
            # it could duplicate a downstream write, so preserve the explicit
            # crash-recovery stop instead of billing or mutating again.
            return None
        if current_status in {"request_started", "resolved"}:
            # Unknown outcomes and saved directives are never replayed.
            return None
        if (
            current_status not in {"escalated", "unavailable"}
            or current_capability_key == capability_key
        ):
            return None
        upgrading_current_review = True
    history = _agent_review_history(checkpoint)
    attempted = [row for row in history if isinstance(row, dict)]
    prior_history = _agent_review_history(
        checkpoint,
        include_pending=False,
    )
    if upgrading_current_review and any(
        str(row.get("issue_key") or "") == issue_key
        and str(row.get("capability_key") or "") == capability_key
        for row in prior_history
    ):
        progress.log(
            "This exact upgraded resolver workspace was already inspected; "
            "Aegis kept its checkpoint without repeating the same request.",
            level="warning",
        )
        return None
    same_scope_attempts = [
        row for row in attempted
        if str(row.get("issue_key") or "") == issue_key
    ]
    # Unattended completion: when a guard below would otherwise pause for a
    # human, the safest server-offered bounded action (the one the review UI
    # highlights, or an explicit keep candidate) is applied deterministically
    # instead — no model request, full audit retained. Decisions whose only
    # meaningful action is user-only (source replacement, custom instruction)
    # yield no safe option and still pause.
    safe_option = (
        autonomous_resolution.safe_continuation_option(pending)
        if autonomous_resolution.unattended_completion_enabled()
        else None
    )

    def _forced_safe_result(guard_reason: str) -> "autonomous_resolution.ResolutionResult":
        assert safe_option is not None
        return autonomous_resolution.ResolutionResult(
            status="resolved",
            reason=(
                f"Safe continuation: {guard_reason} Aegis applied the "
                "safest server-offered bounded action "
                f"({safe_option['choice']}) instead of pausing for review."
            )[:8000],
            choice=safe_option["choice"],
            target_id=safe_option["target_id"],
            target_concept_id=safe_option["target_concept_id"],
        )

    forced_safe: "autonomous_resolution.ResolutionResult | None" = None
    if deterministic_result is None and any(
        str(row.get("capability_key") or "") == capability_key
        for row in same_scope_attempts
    ):
        # Identical evidence + critic state + prior-pathway history is a true
        # loop. A changed critic conclusion, evidence set, or prior action has
        # a different capability key and is sent back to the resolver below.
        if safe_option is not None:
            forced_safe = _forced_safe_result(
                "this exact semantic workspace was already inspected and "
                "escalated once; repeating the identical model request "
                "cannot produce a different verdict."
            )
        else:
            progress.log(
                "This exact semantic workspace was already inspected; Aegis "
                "did not repeat an identical model request.",
                level="warning",
            )
            return None
    if (
        forced_safe is None
        and deterministic_result is None
        and len(same_scope_attempts)
        >= autonomous_resolution.maximum_pathway_turns()
    ):
        reason = (
            "This semantic scope exhausted its distinct autonomous repair "
            "pathways."
        )
        if safe_option is not None:
            forced_safe = _forced_safe_result(reason)
        else:
            now = _agent_review_timestamp()
            reason = (
                reason
                + " The checkpoint is preserved because continuing would "
                "repeat an ineffective action rather than improve the output."
            )
            if not upgrading_current_review:
                _persist_pending_agent_review(
                    db,
                    job,
                    decision_id=pending["decision_id"],
                    context_hash=pending["context_hash"],
                    review={
                        "status": "escalated",
                        "resolver_version": (
                            autonomous_resolution.RESOLVER_VERSION
                        ),
                        "issue_key": issue_key,
                        **workspace_audit,
                        "started_at": now,
                        "completed_at": now,
                        "reason": reason,
                    },
                    owner_sub=owner_sub,
                )
            progress.log(reason, level="warning")
            return None
    if (
        forced_safe is None
        and deterministic_result is None
        and len(attempted) >= autonomous_resolution.maximum_decisions()
    ):
        reason = "This run reached its autonomous decision safety cap."
        if safe_option is not None:
            forced_safe = _forced_safe_result(reason)
        else:
            now = _agent_review_timestamp()
            reason = (
                reason
                + " The next semantic choice is saved for you without "
                "another model request."
            )
            if not upgrading_current_review:
                _persist_pending_agent_review(
                    db,
                    job,
                    decision_id=pending["decision_id"],
                    context_hash=pending["context_hash"],
                    review={
                        "status": "escalated",
                        "resolver_version": (
                            autonomous_resolution.RESOLVER_VERSION
                        ),
                        "issue_key": issue_key,
                        **workspace_audit,
                        "started_at": now,
                        "completed_at": now,
                        "reason": reason,
                    },
                    owner_sub=owner_sub,
                )
            progress.log(reason, level="warning")
            return None

    started_at = _agent_review_timestamp()
    try:
        pending = _persist_pending_agent_review(
            db,
            job,
            decision_id=pending["decision_id"],
            context_hash=pending["context_hash"],
            review={
                "status": "request_started",
                "resolver_version": autonomous_resolution.RESOLVER_VERSION,
                "issue_key": issue_key,
                **workspace_audit,
                "started_at": started_at,
                "reason": (
                    (
                        "The decision and checkpoint were saved before local "
                        "working-source patch validation."
                    )
                    if deterministic_result is not None
                    else (
                        "The decision and checkpoint were saved before a "
                        "deterministic safe continuation was applied."
                    )
                    if forced_safe is not None
                    else (
                        "The decision and checkpoint were saved before this "
                        "one bounded autonomous review started."
                    )
                ),
            },
            owner_sub=owner_sub,
        )
    except HumanDecisionConflictError:
        db.refresh(job)
        progress.log(
            "Another worker already claimed this saved resolution review; "
            "this worker did not start a model request.",
            level="warning",
        )
        return None
    if forced_safe is not None:
        progress.log(
            forced_safe.reason + " No model request was started.",
            level="warning",
        )
        result = forced_safe
    elif deterministic_result is not None:
        progress.set_progress(
            job.checkpoint_progress,
            label=(
                "Canonical source — applying verified working-source patch"
                if deterministic_result.resolved
                else "Canonical source — saved patch needs your review"
            ),
        )
        progress.log(
            (
                "Aegis verified one hash-sealed working-source repair "
                "locally; no GPT request or hierarchy retry was started."
                if deterministic_result.resolved
                else (
                    f"{deterministic_result.reason} No GPT request or "
                    "hierarchy retry was started."
                )
            ),
            level="warning",
        )
        result = deterministic_result
    else:
        progress.set_progress(
            job.checkpoint_progress,
            label="Resolution agent — reviewing discrepancy",
        )
        progress.log(
            "Resolution agent is checking the canonical working source, "
            "checkpoint, candidates, Types and QIDs; it may expand the exact "
            "issue evidence once before choosing the safest continuation.",
            level="warning",
        )
        result = autonomous_resolution.resolve_pending(
            pending,
            # Generation consumes the verified Phase 2.2/Phase 3 rendering,
            # not the immutable Mathpix audit copy.  Resolution must inspect
            # that same source contract or a displaced raw-MMD sidebar can
            # hide the very blocks that support the generated claim.
            source_text=_semantic_recovery_source_text(job.mmd_text),
            checkpoint=copy.deepcopy(job.generation_checkpoint or {}),
        )
    if (
        not result.resolved
        and forced_safe is None
        and deterministic_result is None
        and safe_option is not None
    ):
        # The bounded review escalated, but a safe server-offered action
        # exists. Unattended completion applies it — with the original
        # escalation verdict preserved verbatim in the audited reason —
        # instead of pausing the run for a manual review click.
        continuation_reason = (
            f"Safe continuation after escalation: {result.reason} "
            "Aegis applied the safest server-offered bounded action "
            f"({safe_option['choice']}) so generation completes without "
            "manual review."
        )[:8000]
        progress.log(continuation_reason, level="warning")
        result = autonomous_resolution.ResolutionResult(
            status="resolved",
            reason=continuation_reason,
            confidence=result.confidence,
            evidence_refs=result.evidence_refs,
            choice=safe_option["choice"],
            target_id=safe_option["target_id"],
            target_concept_id=safe_option["target_concept_id"],
            workspace_hash=result.workspace_hash,
            offered_candidate_count=result.offered_candidate_count,
            inspected_candidate_count=result.inspected_candidate_count,
        )
    completed_at = _agent_review_timestamp()
    final_workspace_audit = {
        "capability_key": capability_key,
        "workspace_hash": result.workspace_hash or capability_key,
        "offered_candidate_count": (
            result.offered_candidate_count or offered_candidate_count
        ),
        "inspected_candidate_count": result.inspected_candidate_count,
    }
    final_review = {
        "status": result.status,
        "resolver_version": autonomous_resolution.RESOLVER_VERSION,
        "issue_key": issue_key,
        **final_workspace_audit,
        "started_at": started_at,
        "completed_at": completed_at,
        "reason": result.reason,
        "confidence": result.confidence,
        "evidence_refs": list(result.evidence_refs),
        "review_flags": list(result.review_flags),
        "choice": result.choice or None,
        "instruction": result.instruction,
        "target_id": result.target_id,
        "target_concept_id": result.target_concept_id,
    }
    try:
        pending = _persist_pending_agent_review(
            db,
            job,
            decision_id=pending["decision_id"],
            context_hash=pending["context_hash"],
            review=final_review,
            owner_sub=owner_sub,
            persist_usage=deterministic_result is None,
        )
    except HumanDecisionConflictError:
        db.refresh(job)
        progress.log(
            "The saved semantic decision changed while the bounded review was "
            "finishing; Aegis kept the newer state and did not overwrite it.",
            level="warning",
        )
        return None
    if not result.resolved:
        progress.set_progress(
            job.checkpoint_progress,
            label="Paused for your decision",
        )
        progress.log(result.reason, level="warning")
        return None
    try:
        recorded = _record_human_semantic_decision_locked(
            db,
            job,
            pending["decision_id"],
            choice=result.choice,
            instruction=result.instruction,
            target_id=result.target_id,
            target_concept_id=result.target_concept_id,
            resolved_by="agent",
            resolution_status="consumed",
        )
    except (HumanDecisionConflictError, ValueError) as exc:
        fallback = {
            **final_review,
            "status": "escalated",
            "reason": (
                "The proposed action failed Aegis's deterministic decision "
                f"validation ({type(exc).__name__}); choose the saved action."
            ),
            "choice": None,
            "target_id": "",
            "target_concept_id": "",
        }
        _persist_pending_agent_review(
            db,
            job,
            decision_id=pending["decision_id"],
            context_hash=pending["context_hash"],
            review=fallback,
            owner_sub=owner_sub,
        )
        progress.log(fallback["reason"], level="warning")
        return None
    decision_id = str(recorded["resolved_decision"]["decision_id"])
    progress.log(
        (
            "Applied the verified patch only to the derived working MMD; the "
            "raw upload is unchanged and generation is continuing from the "
            "saved checkpoint."
        )
        if deterministic_result is not None
        else (
            "Resolution agent applied one source-supported action and "
            "generation is continuing from the saved checkpoint."
        ),
        level="success",
    )
    return decision_id


def _apply_last_resort_safe_continuation(
    db: Session,
    job: models.UploadJob,
    pending: dict,
    *,
    owner_sub: str | None,
) -> str | None:
    """Continue unattended when every resolver pathway declined to decide.

    This is the single choke point that makes "no manual review" an
    invariant rather than a property of individual branches. Whatever caused
    the resolver to decline — it is disabled, the identical workspace was
    already inspected, a safety cap fired, a crash-recovery guard fired, the
    review escalated, or another worker holds the claim — the run still
    continues by recording the safest server-offered bounded action.

    It returns None only when no bounded automatable action exists (for
    example a source replacement, which no automation may perform), or when
    unattended completion is switched off.
    """

    if not autonomous_resolution.unattended_completion_enabled():
        return None
    safe_option = autonomous_resolution.safe_continuation_option(pending)
    if safe_option is None:
        return None
    review = pending.get("agent_review")
    declined_reason = (
        str(review.get("reason") or "").strip()
        if isinstance(review, dict) else ""
    )
    try:
        recorded = _record_human_semantic_decision_locked(
            db,
            job,
            str(pending.get("decision_id") or ""),
            choice=safe_option["choice"],
            instruction="",
            target_id=safe_option["target_id"],
            target_concept_id=safe_option["target_concept_id"],
            resolved_by="agent",
            resolution_status="consumed",
        )
    except (HumanDecisionConflictError, ValueError) as exc:
        # The decision changed underneath this worker, or the safest offered
        # action failed deterministic validation. Both are real stops.
        progress.log(
            "Aegis could not apply a safe continuation for the saved "
            f"semantic decision ({type(exc).__name__}: {exc}).",
            level="warning",
        )
        return None
    decision_id = str(recorded["resolved_decision"]["decision_id"])
    # This wording is load-bearing: concept_revisions scans the run log for
    # "placed by best judgement" to show a reviewer exactly which placements
    # Aegis was least sure of. Changing it silently hides them.
    progress.log(
        "Placed by best judgement: no autonomous review pathway remained"
        + (f" ({declined_reason})" if declined_reason else "")
        + f". Aegis applied the safest offered action ({safe_option['choice']}"
        + (
            f" -> {safe_option['target_id'] or safe_option['target_concept_id']}"
            if safe_option["target_id"] or safe_option["target_concept_id"]
            else ""
        )
        + ") for "
        + _decision_identity_text(pending)
        + " and generation continued. Review this placement in the delivered "
        "output and correct it there if it is wrong.",
        level="warning",
    )
    return decision_id


class SemanticResolutionCyclesExhausted(RuntimeError):
    """One equivalent semantic decision returned after every allowed repair."""


def _max_equivalent_resolution_attempts() -> int:
    raw = os.environ.get(
        "AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS",
        os.environ.get("AEGIS_MAX_RESOLUTION_CYCLES", "12"),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 12
    return max(1, min(200, value))


def _decision_equivalence_key(pending: dict) -> str:
    """Bind a recurring issue to its material evidence, not volatile IDs."""

    candidates = []
    for raw in pending.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        coverage = str(raw.get("coverage") or "")
        gap = str(raw.get("gap") or "")
        candidates.append({
            "target_id": str(raw.get("target_id") or ""),
            "concept_id": str(raw.get("concept_id") or ""),
            "action": _stable_checkpoint_value(raw.get("action")),
            "title": _stable_checkpoint_value(raw.get("title")),
            "topic": _stable_checkpoint_value(raw.get("topic")),
            "coverage_sha256": hashlib.sha256(
                coverage.encode("utf-8")
            ).hexdigest(),
            "gap_sha256": hashlib.sha256(
                gap.encode("utf-8")
            ).hexdigest(),
            "source_block_ids": sorted({
                str(value) for value in raw.get("source_block_ids") or []
                if str(value)
            }),
            "source_topic_id": str(raw.get("source_topic_id") or ""),
            "target_topic_id": str(raw.get("target_topic_id") or ""),
            "boundary_relation": _stable_checkpoint_value(
                raw.get("boundary_relation")
            ),
            "source_kind": _stable_checkpoint_value(raw.get("source_kind")),
            "source_page": _stable_checkpoint_value(raw.get("source_page")),
            "text_sha256": str(raw.get("text_sha256") or ""),
            "binding_hash": str(raw.get("binding_hash") or ""),
        })
    evidence = [
        {
            "evidence_id": str(raw.get("evidence_id") or ""),
            "page": _stable_checkpoint_value(raw.get("page")),
            "label": _stable_checkpoint_value(raw.get("label")),
            "text": _stable_checkpoint_value(raw.get("text")),
        }
        for raw in pending.get("evidence") or []
        if isinstance(raw, dict)
    ]
    options = [
        {
            "choice": str(raw.get("choice") or ""),
            "target_id": str(raw.get("target_id") or ""),
            "target_concept_id": str(
                raw.get("target_concept_id") or ""
            ),
        }
        for raw in pending.get("options") or []
        if isinstance(raw, dict)
    ]
    patch = pending.get("source_patch")
    patch_identity = {
        key: str(patch.get(key) or "")
        for key in (
            "version", "kind", "source_contract_hash",
            "semantic_context_hash", "before_sha256", "after_sha256",
            "patch_hash",
        )
    } if isinstance(patch, dict) else {}
    material = {
        "issue_key": autonomous_resolution.issue_key(pending),
        "disagreement": {
            key: _stable_checkpoint_value(pending.get(key))
            for key in ("conflict", "diagnosis", "decision_question")
        },
        "candidates": sorted(
            candidates,
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ),
        ),
        "evidence": sorted(
            evidence,
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ),
        ),
        "options": sorted(
            options,
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ),
        ),
        "source_patch": patch_identity,
    }
    return hashlib.sha256(json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _equivalent_agent_resolution_count(
    checkpoint: dict | None,
    pending: dict,
) -> int:
    """Count only prior autonomous repairs for this exact material issue."""

    wanted_equivalence = _decision_equivalence_key(pending)
    count = 0
    for row in _human_decision_ledger(checkpoint).get("resolutions") or []:
        if (
            not isinstance(row, dict)
            or str(row.get("resolved_by") or "") != "agent"
            or str(row.get("status") or "") not in {"ready", "consumed"}
        ):
            continue
        stored_equivalence = str(row.get("equivalence_key") or "")
        if stored_equivalence:
            if stored_equivalence == wanted_equivalence:
                count += 1
            continue
        # PR #149 rows predate the material equivalence seal, and their compact
        # snapshots discard most evidence and candidates. Issue identity alone
        # cannot prove that a new candidate is materially equivalent. Grant one
        # fresh post-upgrade budget; every newly recorded row carries the exact
        # equivalence key and is charged durably on later resumes.
        continue
    return count


def _issue_agent_resolution_count(
    checkpoint: dict | None,
    scope_key: str,
) -> int:
    """Count prior autonomous repairs for one semantic scope.

    Unlike :func:`_equivalent_agent_resolution_count` this ignores candidate
    identities entirely. Regenerating a concept_id produces a new *equivalence*
    key but the same *issue* key, so this is the count that can retire a scope
    the pipeline keeps re-raising under fresh identities.
    """

    if not scope_key:
        return 0
    count = 0
    for row in _human_decision_ledger(checkpoint).get("resolutions") or []:
        if (
            not isinstance(row, dict)
            or str(row.get("resolved_by") or "") != "agent"
            or str(row.get("status") or "") not in {"ready", "consumed"}
        ):
            continue
        stored = str(row.get("issue_key") or "")
        if not stored:
            original = row.get("pending_decision")
            if isinstance(original, dict):
                stored = autonomous_resolution.issue_key(original)
        if stored == scope_key:
            count += 1
    return count


def _carry_forward_exhausted_scope(
    db: Session,
    job: models.UploadJob,
    pending: dict,
    *,
    owner_sub: str | None,
    reason: str,
) -> str | None:
    """Settle a scope that has spent its budget, instead of ending the run.

    A ceiling exists because a scope that keeps returning will not converge.
    That is a reason to stop *spending on it*, not a reason to throw away the
    other forty concepts: the run has usually done the expensive work by the
    time a ceiling fires, and a stop hands the reviewer nothing to read.

    So the decision is carried -- nothing is applied, the source is untouched
    -- and flagged in the delivered output. This is the same judgement as
    ``carry_forward`` for a decision with no automatable route, applied to a
    decision that had routes and exhausted them.
    """

    if not autonomous_resolution.unattended_completion_enabled():
        return None
    try:
        recorded = _record_human_semantic_decision_locked(
            db,
            job,
            str(pending.get("decision_id") or ""),
            choice=autonomous_resolution.CARRY_FORWARD_CHOICE,
            instruction="",
            target_id="",
            target_concept_id="",
            resolved_by="agent",
            resolution_status="consumed",
        )
    except (HumanDecisionConflictError, ValueError) as exc:
        progress.log(
            "Aegis could not carry forward the exhausted semantic scope "
            f"({type(exc).__name__}: {exc}).",
            level="warning",
        )
        return None
    # Load-bearing wording: concept_revisions scans for "placed by best
    # judgement" to show the reviewer which placements Aegis was least sure of.
    progress.log(
        "Placed by best judgement: " + reason + " Aegis carried this decision "
        "without applying anything for "
        + _decision_identity_text(pending)
        + ", and generation continued. Review this placement in the delivered "
        "output and correct it there if it is wrong.",
        level="warning",
    )
    return str(recorded["resolved_decision"]["decision_id"])


def _settle_exhausted_ceiling(
    db: Session,
    job: models.UploadJob,
    pending: dict,
    *,
    owner_sub: str | None,
    reason: str,
    ceiling: str,
) -> str | None:
    """Q13: a returned already-carried scope still continues, best-judged.

    Reached when a scope's budget is spent AND it was already carried
    forward once (or the carry itself could not be recorded). The old
    behavior was ``SemanticResolutionCyclesExhausted`` — a halt Q13
    retires: instead the existing best-judgement machinery applies the
    least-destructive offered action (or carries again), flagged with the
    ceiling's name, and generation continues. Termination stays
    guaranteed by the ledger-safety mechanics (``_MAX_HUMAN_DECISIONS``),
    which this deliberately does not touch. Returns ``None`` only when
    unattended completion is off or the action cannot be recorded — the
    original ceilings then end the run (genuine impossibility).
    """

    if not autonomous_resolution.unattended_completion_enabled():
        return None
    option = (
        autonomous_resolution.best_judgement_option(pending)
        or autonomous_resolution.carry_forward_option()
    )
    try:
        recorded = _record_human_semantic_decision_locked(
            db,
            job,
            str(pending.get("decision_id") or ""),
            choice=option["choice"],
            instruction="",
            target_id=option["target_id"],
            target_concept_id=option["target_concept_id"],
            resolved_by="agent",
            resolution_status="consumed",
        )
    except (HumanDecisionConflictError, ValueError) as exc:
        progress.log(
            "Aegis could not settle the exhausted semantic ceiling "
            f"({type(exc).__name__}: {exc}).",
            level="warning",
        )
        return None
    # Load-bearing wording: concept_revisions scans for "placed by best
    # judgement" to surface these placements to the reviewer.
    progress.log(
        "Placed by best judgement: " + reason
        + f" [fixer: ceiling={ceiling}] Aegis applied the least-destructive "
        f"offered action ({option['choice']}"
        + (
            f" -> {option['target_id'] or option['target_concept_id']}"
            if option["target_id"] or option["target_concept_id"]
            else ""
        )
        + ") for "
        + _decision_identity_text(pending)
        + ", and generation continued. Review this placement in the "
        "delivered output and correct it there if it is wrong.",
        level="warning",
    )
    return str(recorded["resolved_decision"]["decision_id"])


def _apply_fixer_resolution_choice(
    db: Session,
    job: models.UploadJob,
    pending: dict,
    *,
    owner_sub: str | None,
) -> str | None:
    """Q13 retires the ``UnattendedDecisionUnavailable`` halt (seam F48).

    Reached when every automatic pathway declined and the safest-action
    fallback could not be recorded — classically because only user-only
    routes (replace_source / custom_instruction) remained. The Fixer
    reads the decision's full context and picks among ALL choices,
    including user-only ones, with a rationale: it may write the custom
    instruction itself, or settle the decision by carrying it forward.
    ``replace_source`` stays mechanically inapplicable — no automation
    may synthesize a replacement document — so the checker refuses it
    and a Fixer that cannot name an applicable route is protocol
    impossibility: the caller's raise then ends the run as before.
    """

    if not autonomous_resolution.unattended_completion_enabled():
        return None
    from .phase3 import fixer as p3_fixer
    from .phase3 import kernel as p3_kernel

    provider = p3_fixer.default_provider()
    if provider is None:
        return None
    options = [
        row for row in pending.get("options") or []
        if isinstance(row, dict)
    ]
    offered = sorted({
        str(row.get("choice") or "")
        for row in options
        if str(row.get("choice") or "")
    })
    allowed = sorted(
        (set(offered) | {autonomous_resolution.CARRY_FORWARD_CHOICE})
        - {"replace_source"}
    )
    payload = {
        "fixer": True,
        "blocked_check": [
            "every automatic resolution pathway declined this semantic "
            "decision and the remaining offered routes require a person; "
            "under Q13 the run must not stop mid-run — one recorded, "
            "flagged decision is required"
        ],
        "contract": {
            "kind": "fixer.resolution_choice",
            "rule": (
                "pick exactly ONE applicable route: one of the offered "
                "choices, or carry_forward (settle the decision by "
                "changing nothing). replace_source is never applicable "
                "— no automation may synthesize a replacement document. "
                "custom_instruction requires you to write the exact "
                "instruction. Response schema: {\"choice\", "
                "\"target_id\", \"target_concept_id\", \"instruction\", "
                "\"rationale\"}"
            ),
        },
        "decision": {
            "kind": str(pending.get("kind") or ""),
            "phase": str(pending.get("phase") or ""),
            "conflict": str(pending.get("conflict") or "")[:2000],
            "diagnosis": str(pending.get("diagnosis") or "")[:2000],
            "decision_question": str(
                pending.get("decision_question") or ""
            )[:2000],
            "options": [
                {
                    "choice": str(row.get("choice") or ""),
                    "label": str(row.get("label") or ""),
                    "target_id": str(row.get("target_id") or ""),
                    "target_concept_id": str(
                        row.get("target_concept_id") or ""
                    ),
                }
                for row in options
            ],
            "candidates": [
                {
                    "target_id": str(row.get("target_id") or ""),
                    "concept_id": str(row.get("concept_id") or ""),
                    "action": str(row.get("action") or ""),
                    "title": str(row.get("title") or "")[:200],
                    "topic": str(row.get("topic") or "")[:200],
                    "coverage": str(row.get("coverage") or "")[:600],
                    "gap": str(row.get("gap") or "")[:600],
                }
                for row in pending.get("candidates") or []
                if isinstance(row, dict)
            ],
            "evidence": [
                {
                    "evidence_id": str(row.get("evidence_id") or ""),
                    "label": str(row.get("label") or "")[:200],
                    "text": str(row.get("text") or "")[:800],
                }
                for row in pending.get("evidence") or []
                if isinstance(row, dict)
            ],
        },
    }

    def _check(response):
        defects: list[str] = []
        choice = str(response.get("choice") or "").strip()
        if choice not in allowed:
            defects.append(
                f"choice {choice or '<empty>'!r} is not applicable; "
                "choose one of: " + ", ".join(allowed)
            )
        if choice == "custom_instruction" and not str(
            response.get("instruction") or ""
        ).strip():
            defects.append(
                "custom_instruction requires a non-empty instruction"
            )
        if not str(response.get("rationale") or "").strip():
            defects.append("rationale is required")
        return defects

    try:
        decision = p3_kernel.decide(
            kind="fixer.resolution_choice",
            unit_id=str(pending.get("decision_id") or "decision"),
            envelope_sha256=str(pending.get("context_hash") or ""),
            payload=payload,
            provider=provider,
            checker=_check,
            store=generation._phase3_fixer_store(),
            policy_version=p3_fixer.FIXER_POLICY_VERSION,
        )
    except p3_kernel.ContractError as exc:
        progress.log(
            "The Fixer could not produce a mechanically applicable "
            f"resolution choice ({exc}); the run ends as before.",
            level="warning",
        )
        return None
    response = decision.get("response") or {}
    choice = str(response.get("choice") or "")
    instruction = (
        str(response.get("instruction") or "").strip()
        if choice == "custom_instruction"
        else ""
    )
    rationale = " ".join(str(response.get("rationale") or "").split())[:240]
    try:
        recorded = _record_human_semantic_decision_locked(
            db,
            job,
            str(pending.get("decision_id") or ""),
            choice=choice,
            instruction=instruction,
            target_id=str(response.get("target_id") or ""),
            target_concept_id=str(response.get("target_concept_id") or ""),
            resolved_by="agent",
            resolution_status="consumed",
            fixer_decision=True,
        )
    except (HumanDecisionConflictError, ValueError) as exc:
        progress.log(
            "Aegis could not record the Fixer's resolution choice "
            f"({type(exc).__name__}: {exc}).",
            level="warning",
        )
        return None
    # Load-bearing wording: concept_revisions scans for "placed by best
    # judgement" to surface these decisions to the reviewer.
    progress.log(
        "Placed by best judgement: every automatic pathway declined and "
        "only user-only routes remained. [fixer: "
        f"decided={choice}"
        + (f" — {rationale}" if rationale else "")
        + "] for "
        + _decision_identity_text(pending)
        + ", and generation continued. Review this decision in the "
        "delivered output and correct it there if it is wrong.",
        level="warning",
    )
    return str(recorded["resolved_decision"]["decision_id"])


def _issue_pathways_exhausted(*, attempts: int, maximum: int) -> bool:
    return attempts >= maximum


def _raise_if_issue_pathways_exhausted(
    pending: dict,
    *,
    attempts: int,
    maximum: int,
) -> None:
    """Stop a scope that keeps returning under regenerated identities.

    Reached only when the scope could be neither carried forward nor
    settled by best judgement (Q13, seams F46/F47) — unattended completion
    is off, or the ledger refused every recordable action — so there is
    genuinely nothing left to do but end the run with the reason recorded.
    """

    if attempts < maximum:
        return
    message = (
        f"Generation stopped after {attempts} autonomous repair attempt(s) for "
        f"the same semantic scope [{_decision_identity_text(pending)}]. The "
        "scope kept returning with regenerated candidate identities, so its "
        "distinct repair pathways are exhausted and further attempts would "
        "not converge. Raise AEGIS_AUTONOMOUS_RESOLUTION_MAX_PATHWAY_TURNS "
        "only after correcting that decision pathway."
    )
    progress.log(message, level="error")
    raise SemanticResolutionCyclesExhausted(message)


def _decision_identity_text(pending: dict) -> str:
    """Name a decision in the run log so a loop is diagnosable."""

    item = pending.get("item") if isinstance(pending.get("item"), dict) else {}
    parts = [
        f"kind={str(pending.get('kind') or '') or 'unknown'}",
        f"phase={str(pending.get('phase') or '') or 'unknown'}",
    ]
    for label, value in (
        ("unit", item.get("unit_id")),
        ("type", item.get("type_id")),
        ("topic", item.get("topic")),
    ):
        if str(value or "").strip():
            parts.append(f"{label}={str(value).strip()}")
    try:
        parts.append(
            f"issue={autonomous_resolution.issue_key(pending)[:12]}")
    except Exception:
        pass
    return ", ".join(parts)


def _raise_if_equivalent_resolution_attempts_exhausted(
    pending: dict,
    *,
    attempts: int,
    maximum: int,
) -> None:
    if attempts < maximum:
        return
    message = (
        f"Generation stopped after {attempts} verified autonomous repair "
        "attempt(s) for the same material decision ["
        f"{_decision_identity_text(pending)}]. The last allowed repair was "
        "rerun and the equivalent decision returned, so another repair would "
        "repeat a proven-ineffective path. Raise "
        "AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS only after correcting that "
        "decision pathway."
    )
    progress.log(message, level="error")
    raise SemanticResolutionCyclesExhausted(message)


def _raise_if_unattended_cannot_pause(pending: dict) -> None:
    """End an unattended run instead of parking it in ``awaiting_decision``.

    Reached only when every automatic pathway declined, the decision's
    remaining routes all require a person, AND The Fixer (Q13, seam F48)
    could not produce — or record — a mechanically applicable choice
    either. A source replacement can never be synthesized.

    This is unconditional. Generation has no mid-run pause in any
    configuration, and no setting restores one: a stalled job is worse than a
    failed one because nothing is watching it and it holds its worker
    indefinitely. The run stops with the reason recorded, and the reviewer
    corrects the delivered output afterwards through the revision loop
    (:mod:`app.services.concept_revisions`) instead of mid-run.
    """

    choices = sorted({
        str(row.get("choice") or "")
        for row in pending.get("options") or []
        if isinstance(row, dict) and str(row.get("choice") or "")
    })
    # Listing every offered choice claimed that automatable routes such as
    # accept_recommended required a person, which sent diagnosis after the
    # wrong thing. Name what actually remains, and say the automatic
    # pathways declined rather than were never offered.
    user_only = [
        value for value in choices
        if value in autonomous_resolution.USER_ONLY_CHOICES
    ]
    automatable = [value for value in choices if value not in user_only]
    message = (
        "Unattended generation stopped: every automatic pathway declined this "
        "decision"
        + (
            f" (automatable routes offered: {', '.join(automatable)})"
            if automatable
            else ""
        )
        + ", leaving only actions a person must take ("
        + (", ".join(user_only) or "none")
        + "). Aegis will not alter the uploaded document by itself. Upload a "
        "corrected document and convert it again."
    )
    progress.log(message, level="error")
    raise UnattendedDecisionUnavailable(message)


def _source_replacement_required(checkpoint: dict | None) -> bool:
    """Whether a source-review answer terminally requires a new conversion."""

    return any(
        str(row.get("choice") or "") == "replace_source"
        and str(row.get("kind") or "") in {
            "phase3_source_graph_review",
            "source_topic_coverage_review",
        }
        for row in _ready_human_decisions(checkpoint)
        if isinstance(row, dict)
    )


def _raise_if_source_replacement_required(
    checkpoint: dict | None,
) -> None:
    if _source_replacement_required(checkpoint):
        raise ValueError(
            "Source replacement is required before generation can continue. "
            "Replace or correct the uploaded file and convert it again; Aegis "
            "will not mutate the source automatically."
        )


def _persist_pending_human_decision(
    db: Session,
    job: models.UploadJob,
    raw_pending: dict,
    *,
    fingerprint: str,
    target_chapter_id: int,
    owner_sub: str | None,
) -> dict:
    """Pause durably without replacing the latest completed stage."""
    db.rollback()
    db.refresh(job)
    checkpoint = copy.deepcopy(job.generation_checkpoint or {})
    if not generation._valid_concept_checkpoint(checkpoint):
        raise RuntimeError(
            "human semantic decision could not be paused because no "
            "compatible generation checkpoint is available")
    if str(checkpoint.get("fingerprint") or "") != fingerprint:
        raise RuntimeError(
            "human semantic decision context no longer matches the saved "
            "generation checkpoint")

    pending_payload = copy.deepcopy(raw_pending)
    pending_payload["checkpoint_progress"] = float(
        job.checkpoint_progress or 0.0)
    pending = _normalize_pending_human_decision(
        pending_payload,
        cumulative_usage=openai_usage.visible_summary(),
    )
    ledger = _human_decision_ledger(checkpoint)
    existing_pending = ledger.get("pending")
    if isinstance(existing_pending, dict):
        if (
            str(existing_pending.get("decision_id") or "")
            != str(pending.get("decision_id") or "")
        ):
            raise RuntimeError(
                "another human semantic decision is already pending"
            )
        if (
            str(existing_pending.get("context_hash") or "")
            != str(pending.get("context_hash") or "")
        ):
            raise RuntimeError(
                "the repeated semantic decision identity has different context"
            )
        # Idempotent concurrent replay: the caller observes the saved review
        # and cannot start a duplicate provider request.
        return copy.deepcopy(existing_pending)
    resolutions = [
        copy.deepcopy(entry)
        for entry in ledger.get("resolutions") or []
        if isinstance(entry, dict)
    ]
    if len(resolutions) >= _MAX_HUMAN_DECISIONS:
        raise RuntimeError(
            "human semantic decision history reached its safety limit")
    replayed_agent_review: dict | None = None
    retained_resolutions: list[dict] = []
    for entry in resolutions:
        if entry.get("decision_id") == pending["decision_id"]:
            original = entry.get("pending_decision")
            review = (
                original.get("agent_review")
                if isinstance(original, dict) else None
            )
            if (
                entry.get("resolved_by") == "agent"
                and entry.get("status") == "consumed"
                and isinstance(review, dict)
                and review.get("status") == "resolved"
            ):
                # The prior worker durably sealed the agent directive before
                # rerunning, but no later gate checkpoint proves whether a
                # downstream paid request started. Restore the original gate
                # identity for an explicit user decision instead of replaying
                # the agent or leaving resume permanently broken.
                replayed_agent_review = copy.deepcopy(review)
                continue
            raise RuntimeError(
                "a previously resolved human decision was requested again; "
                "the saved resolution was not consumed")
        retained_resolutions.append(entry)
    resolutions = retained_resolutions
    if replayed_agent_review is not None:
        review_history = [
            copy.deepcopy(row)
            for row in ledger.get("agent_review_history") or []
            if isinstance(row, dict)
        ]
        if len(review_history) < _MAX_AGENT_REVIEW_HISTORY:
            # Preserve proof that an agent action already ran. The escalated
            # pending clone below is a crash-safe human fallback, not an
            # invitation for a newer resolver to repeat the action.
            review_history.append(copy.deepcopy(replayed_agent_review))
        ledger["agent_review_history"] = review_history
        now = _agent_review_timestamp()
        replayed_agent_review.update({
            "status": "escalated",
            "completed_at": now,
            "reason": (
                "Aegis saved an autonomous action, but the previous worker "
                "ended before a later checkpoint proved whether its directed "
                "request started. Choose how to continue; the action will not "
                "be replayed or billed automatically."
            ),
            "confidence": 0.0,
            "evidence_refs": [],
            "choice": None,
            "instruction": "",
            "target_id": "",
            "target_concept_id": "",
        })
        pending["agent_review"] = replayed_agent_review
        pending = schemas.PendingSemanticDecision.model_validate(
            pending
        ).model_dump()

    checkpoint[_HUMAN_DECISIONS_KEY] = {
        "version": _HUMAN_DECISIONS_VERSION,
        "context": {
            "fingerprint": fingerprint,
            "target_chapter_id": target_chapter_id,
        },
        "pending": pending,
        "deferred_assignment_unit_ids": copy.deepcopy(
            pending.get("deferred_assignment_unit_ids") or []
        ),
        "agent_review_history": copy.deepcopy(
            ledger.get("agent_review_history") or []
        ),
        "resolutions": resolutions,
    }
    job.generation_checkpoint = checkpoint
    job.status = "converted"
    job.detail = (
        "Generation saved a semantic decision checkpoint before any bounded "
        "resolution review."
    )
    # This commits both the decision state and all billable usage observed
    # before the pause. The outer run wrapper may safely persist once more.
    usage = uploads.persist_current_openai_usage(
        db, job.id, owner_sub=owner_sub)
    pending["cumulative_usage"] = copy.deepcopy(usage)
    checkpoint = copy.deepcopy(job.generation_checkpoint or {})
    checkpoint[_HUMAN_DECISIONS_KEY]["pending"] = copy.deepcopy(pending)
    job.generation_checkpoint = checkpoint
    db.commit()
    drive_checkpoints.schedule_checkpoint_backup(job.id)
    progress.set_progress(
        job.checkpoint_progress,
        label="Paused for your decision",
    )
    progress.log(
        "Saved the semantic decision before resolution ["
        + _decision_identity_text(pending)
        + "]. Any enabled autonomous review starts only after this commit "
        "and runs once.",
        level="warning",
    )
    return pending


def _run_with_human_decision_pause(
    operation,
    *,
    db: Session,
    job: models.UploadJob,
    fingerprint: str,
    target_chapter_id: int,
    owner_sub: str | None,
    initial_agent_resolution_ids: set[str] | None = None,
) -> tuple[dict | None, object | None]:
    """Run a pipeline with bounded agent-first decision handling.

    Two independent ceilings bound this loop, and both are required:

    * per *equivalence key* — the identical workspace returning again means the
      last repair was proven ineffective;
    * per *issue key* — the same semantic scope, however its candidate
      identities were regenerated.

    Only the second guarantees termination. The equivalence key hashes
    candidate ``target_id``/``concept_id``, so a stage that regenerates those
    between passes mints a new key every iteration, resets its own budget, and
    spins forever at the Type-assignment boundary. The issue key is deliberately
    independent of regenerated identities, so it is the identity that can
    actually retire a scope.
    """

    active_ids = set(initial_agent_resolution_ids or set())
    local_attempts: dict[str, int] = {}
    issue_attempts: dict[str, int] = {}
    # A scope may be carried forward once. If it returns even after being
    # settled, the phase is not honouring the carried answer and continuing
    # would spin, so the ceiling ends the run as it always did.
    carried_scopes: set[str] = set()
    maximum = _max_equivalent_resolution_attempts()
    issue_maximum = autonomous_resolution.maximum_pathway_turns()

    def _settle_pending(
        raw_pending: dict, *, checkpoint_before_pause: dict,
    ) -> str:
        """Persist and settle one decision; return its resolution id.

        Success returns the settled decision id; every failure path raises.
        Shared by two callers: the replay path (a rejection batch settles
        each decision in turn, then the pipeline replays once) and the
        in-place path (Phase 3.3 settles a conflict mid-run and re-asks
        only the affected topic, with no replay at all). Both charge the
        same equivalence and issue budgets.
        """

        pending = _persist_pending_human_decision(
            db,
            job,
            raw_pending,
            fingerprint=fingerprint,
            target_chapter_id=target_chapter_id,
            owner_sub=owner_sub,
        )
        equivalence_key = _decision_equivalence_key(pending)
        attempts = max(
            local_attempts.get(equivalence_key, 0),
            _equivalent_agent_resolution_count(
                checkpoint_before_pause, pending
            ),
            _equivalent_agent_resolution_count(
                job.generation_checkpoint, pending
            ),
        )
        scope_key = autonomous_resolution.issue_key(pending)
        scope_attempts = max(
            issue_attempts.get(scope_key, 0),
            _issue_agent_resolution_count(
                checkpoint_before_pause, scope_key
            ),
            _issue_agent_resolution_count(
                job.generation_checkpoint, scope_key
            ),
        )

        def _charge() -> None:
            local_attempts[equivalence_key] = attempts + 1
            issue_attempts[scope_key] = scope_attempts + 1

        # A spent budget means stop paying for this scope -- not throw
        # away the other forty concepts. Carry the decision, flag it,
        # and move on; the ceilings end the run only when carrying is
        # impossible too.
        exhausted_reason = ""
        if _issue_pathways_exhausted(
            attempts=attempts, maximum=maximum
        ):
            exhausted_reason = (
                f"{attempts} verified repair attempt(s) for the same "
                "material decision were spent without converging."
            )
        elif _issue_pathways_exhausted(
            attempts=scope_attempts, maximum=issue_maximum
        ):
            exhausted_reason = (
                f"{scope_attempts} repair attempt(s) for the same "
                "semantic scope were spent and it kept returning."
            )
        if exhausted_reason and scope_key not in carried_scopes:
            carried = _carry_forward_exhausted_scope(
                db, job, pending,
                owner_sub=owner_sub,
                reason=exhausted_reason,
            )
            if carried:
                carried_scopes.add(scope_key)
                _charge()
                active_ids.add(carried)
                return carried
        if exhausted_reason:
            # Q13 (seams F46/F47): a scope that returns even after being
            # carried is settled by the least-destructive offered action
            # — flagged with the ceiling's name — and generation
            # continues; the ledger-safety limit still bounds the loop.
            # The original ceilings end the run only when nothing can be
            # recorded (unattended off, or the ledger refused the write).
            ceiling = (
                "equivalent_attempts"
                if _issue_pathways_exhausted(
                    attempts=attempts, maximum=maximum
                )
                else "pathway_turns"
            )
            settled_id = _settle_exhausted_ceiling(
                db, job, pending,
                owner_sub=owner_sub,
                reason=exhausted_reason,
                ceiling=ceiling,
            )
            if settled_id:
                _charge()
                active_ids.add(settled_id)
                return settled_id
            _raise_if_equivalent_resolution_attempts_exhausted(
                pending, attempts=attempts, maximum=maximum,
            )
            _raise_if_issue_pathways_exhausted(
                pending,
                attempts=scope_attempts,
                maximum=issue_maximum,
            )

        resolved_id = _autonomously_resolve_pending_decision(
            db,
            job,
            pending,
            owner_sub=owner_sub,
        )
        if resolved_id:
            _charge()
            active_ids.add(resolved_id)
            return resolved_id
        current = _pending_human_decision(job.generation_checkpoint)
        continued_id = _apply_last_resort_safe_continuation(
            db,
            job,
            current or pending,
            owner_sub=owner_sub,
        )
        if continued_id:
            _charge()
            active_ids.add(continued_id)
            return continued_id
        # Q13 (seam F48): before the retired halt fires, The Fixer picks
        # among ALL routes — user-only ones included — once, recorded and
        # flagged. Only protocol impossibility falls through to the raise.
        fixed_id = _apply_fixer_resolution_choice(
            db,
            job,
            current or pending,
            owner_sub=owner_sub,
        )
        if fixed_id:
            _charge()
            active_ids.add(fixed_id)
            return fixed_id
        # Always raises; there is no pause to fall through to.
        _raise_if_unattended_cannot_pause(current or pending)
        raise AssertionError(  # pragma: no cover - defensive
            "generation reached a pause that no longer exists"
        )

    while True:
        token = _ACTIVE_AGENT_RESOLUTION_IDS.set(frozenset(active_ids))
        try:
            return None, operation()
        except semantic_recovery.HumanDecisionRequired as exc:
            checkpoint_before_pause = copy.deepcopy(
                job.generation_checkpoint or {}
            )
            batch = [exc.pending_decision, *exc.companion_pending_decisions]
            if len(batch) > 1:
                progress.log(
                    f"Settling {len(batch)} independent semantic decision(s) "
                    "from one rejection batch before replaying, instead of "
                    "one replay per decision.",
                )
            for raw_pending in batch:
                _settle_pending(
                    raw_pending,
                    checkpoint_before_pause=checkpoint_before_pause,
                )
        finally:
            _ACTIVE_AGENT_RESOLUTION_IDS.reset(token)


def record_human_semantic_decision(
    db: Session,
    job_id: int,
    decision_id: str,
    *,
    choice: str,
    instruction: str = "",
    target_id: str = "",
    target_concept_id: str = "",
    owner_sub: str | None = None,
) -> dict:
    """Atomically record one owner-authorized answer; resume remains explicit."""
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    # ``pre`` stays in the accepted domain for the same reason the checkpoint
    # routes keep it (see ``checkpoints.resumable_jobs``): it is stored public
    # contract this repo has no migration system to drop. Since restructure
    # step 7 no route can resume a legacy ``pre`` job, so a decision recorded
    # against one is stored but will not be consumed by a resumed run — it is
    # kept recorded rather than refused so the operator's answer is never
    # thrown away, and step 8 decides where those jobs go.
    if job.learning_kind not in {"post", "pre"}:
        raise uploads.UploadJobNotFound("upload job not found")
    try:
        with uploads.exclusive_job_operation(job_id):
            db.refresh(job)
            return _record_human_semantic_decision_locked(
                db,
                job,
                decision_id,
                choice=choice,
                instruction=instruction,
                target_id=target_id,
                target_concept_id=target_concept_id,
            )
    except uploads.JobAlreadyRunningError as exc:
        raise HumanDecisionConflictError(str(exc)) from exc


def _decision_choice_is_recordable(pending: Mapping, choice: str) -> bool:
    """Is ``choice`` a route the server sanctioned for this decision?

    Recording is gated on the options the decision actually offered, so a
    caller cannot apply a mutation the server never put on the table.

    ``carry_forward`` is the one exemption, and deliberately so. It is never a
    server-offered option because it is not a route through the decision at
    all: it is Aegis settling the decision by changing nothing, reached when
    every offered route is one no automation may take. There is no mutation for
    the server to sanction, so there is nothing for the gate to protect. Until
    this exemption existed the last-resort continuation was rejected here and
    the run stopped -- the exact outcome carrying forward was built to prevent.
    """

    offered = {
        str(option.get("choice") or "")
        for option in pending.get("options") or []
        if isinstance(option, Mapping)
    }
    if choice == autonomous_resolution.CARRY_FORWARD_CHOICE:
        return True
    return not offered or choice in offered


def _record_human_semantic_decision_locked(
    db: Session,
    job: models.UploadJob,
    decision_id: str,
    *,
    choice: str,
    instruction: str,
    target_id: str,
    target_concept_id: str,
    resolved_by: str = "human",
    resolution_status: str = "ready",
    fixer_decision: bool = False,
) -> dict:
    """Mutate a decision ledger while ``exclusive_job_operation`` is held.

    ``fixer_decision`` marks a Fixer resolution-choice verdict (Q13, seam
    F48): it may record a ``custom_instruction`` the Fixer wrote itself —
    the one user-only route a model can genuinely take. ``replace_source``
    stays forbidden for every automated caller: no automation may alter
    the uploaded document.
    """
    if job.status == "generated":
        raise HumanDecisionConflictError(
            "this upload has already been generated")
    decision_id = str(decision_id or "").strip()
    if not _HUMAN_DECISION_ID_RE.fullmatch(decision_id):
        raise ValueError("decision_id has an invalid format")
    submission = schemas.HumanSemanticDecisionRequest.model_validate({
        "choice": choice,
        "instruction": instruction,
        "target_id": target_id,
        "target_concept_id": target_concept_id,
    })
    choice = submission.choice
    instruction = submission.instruction.strip()
    target_id = submission.target_id.strip()
    target_concept_id = submission.target_concept_id.strip()
    if resolved_by not in {"human", "agent"}:
        raise ValueError("resolved_by must be human or agent")
    if resolution_status not in {"ready", "consumed"}:
        raise ValueError("resolution_status must be ready or consumed")
    if choice == "custom_instruction" and not instruction:
        raise ValueError("instruction is required for custom_instruction")
    if resolved_by == "agent":
        if choice == "replace_source":
            raise ValueError(
                "an agent cannot record a source replacement"
            )
        if fixer_decision and choice == "custom_instruction":
            # Q13 (seam F48): the Fixer may write the instruction itself;
            # the ordinary submission validation above already required
            # the instruction to be non-empty.
            pass
        else:
            if not autonomous_resolution.is_automatable_choice(choice):
                raise ValueError(
                    "an agent cannot record a source replacement or custom "
                    "instruction"
                )
            if instruction:
                raise ValueError(
                    "an agent instruction must be empty; explanatory prose "
                    "belongs in the validated review reason"
                )

    checkpoint = copy.deepcopy(job.generation_checkpoint or {})
    ledger = _human_decision_ledger(checkpoint)
    resolutions = [
        copy.deepcopy(entry)
        for entry in ledger.get("resolutions") or []
        if isinstance(entry, dict)
    ]
    pending = ledger.get("pending")
    if not isinstance(pending, dict):
        already_recorded = any(
            row.get("decision_id") == decision_id for row in resolutions)
        raise HumanDecisionConflictError(
            "this semantic decision has already been recorded"
            if already_recorded
            else "this semantic decision is stale or is no longer pending")
    if pending.get("decision_id") != decision_id:
        raise HumanDecisionConflictError(
            "this semantic decision is stale; answer the current decision")

    context = ledger.get("context")
    if (
        not isinstance(context, dict)
        or context.get("fingerprint") != checkpoint.get("fingerprint")
        or context.get("target_chapter_id")
        != checkpoint.get("target_chapter_id")
    ):
        raise HumanDecisionConflictError(
            "the semantic decision context is stale; reopen the current job")

    offered: dict[str, dict] = {
        str(option.get("choice") or ""): option
        for option in pending.get("options") or []
        if isinstance(option, dict)
    }
    if not _decision_choice_is_recordable(pending, choice):
        raise ValueError(
            f"choice {choice!r} is not available for this decision")
    candidate_ids = [
        str(candidate.get("concept_id") or "")
        for candidate in pending.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("concept_id")
    ]
    candidate_target_ids = {
        str(candidate.get("target_id") or "")
        for candidate in pending.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("target_id")
    }
    if choice == "expand_existing" and not target_concept_id:
        target_concept_id = str(
            (offered.get(choice) or {}).get("target_concept_id") or "")
    if choice in {"expand_existing", "select_existing"}:
        if not target_concept_id:
            raise ValueError(
                "target_concept_id is required for an existing-concept choice")
        if candidate_ids and target_concept_id not in set(candidate_ids):
            raise ValueError(
                "target_concept_id must identify one of the pending decision "
                "candidates")
    if choice == "accept_recommended":
        recommended_target_id = str(
            (offered.get(choice) or {}).get("target_id") or "")
        if target_id and target_id != recommended_target_id:
            raise ValueError(
                "target_id must match the pending recommended candidate")
        target_id = recommended_target_id
    if choice in {"accept_recommended", "select_candidate"}:
        # Transitional clients used the concept-specific field for every
        # candidate selector. Treat it only as an alias, then enforce exact
        # membership in the server-offered generic target set below.
        if not target_id and target_concept_id:
            target_id = target_concept_id
        if not target_id:
            raise ValueError(
                "target_id is required for a source-review candidate choice")
        if not candidate_target_ids or target_id not in candidate_target_ids:
            raise ValueError(
                "target_id must identify one of the pending decision "
                "candidates")

    resolved_at = datetime.now(timezone.utc).isoformat()
    resolution = {
        "decision_id": decision_id,
        "context_hash": str(pending.get("context_hash") or ""),
        "equivalence_key": _decision_equivalence_key(pending),
        "choice": choice,
        "instruction": instruction,
        "target_id": target_id,
        "target_concept_id": target_concept_id,
        "resolved_at": resolved_at,
        "status": resolution_status,
        "consumed_at": resolved_at if resolution_status == "consumed" else "",
        "resolved_by": resolved_by,
        "pending_decision": _compact_resolved_pending_decision(
            pending,
            choice=choice,
            target_id=target_id,
            target_concept_id=target_concept_id,
        ),
    }
    resolutions.append(resolution)
    checkpoint[_HUMAN_DECISIONS_KEY] = {
        "version": _HUMAN_DECISIONS_VERSION,
        "context": copy.deepcopy(context),
        "pending": None,
        "deferred_assignment_unit_ids": copy.deepcopy(
            pending.get("deferred_assignment_unit_ids") or []
        ),
        "agent_review_history": copy.deepcopy(
            ledger.get("agent_review_history") or []
        ),
        "resolutions": resolutions,
    }
    job.generation_checkpoint = checkpoint
    job.status = "converted"
    source_replacement_required = choice == "replace_source"
    job.detail = (
        "Replace or correct the uploaded source, convert it again, and then "
        "start generation. Aegis did not mutate the source."
        if source_replacement_required
        else (
            "Semantic decision recorded. Resume generation from the saved "
            "checkpoint when ready."
        )
    )
    db.commit()
    db.refresh(job)
    drive_checkpoints.schedule_checkpoint_backup(job.id)
    return {
        "job_id": job.id,
        "status": "decision_recorded",
        "resume_required": not source_replacement_required,
        "resolved_decision": {
            key: value for key, value in resolution.items()
            if key not in {"status", "pending_decision"}
        },
    }


def create_post_learning_job(
    db: Session, *, filename: str, raw_bytes: bytes, source_book: str = "",
    owner_sub: str | None = None,
) -> models.UploadJob:
    """Stage the file only — conversion to MMD is a separate explicit step."""
    job = models.UploadJob(
        owner_sub=uploads.normalize_owner_sub(owner_sub),
        module="build_concepts", upload_type="document", learning_kind="post",
        filename=Path(filename).name, mmd_text="", status="uploaded",
        source_book=source_book.strip(),
    )
    return uploads.persist_new_job(db, job, raw_bytes)


def _job_source_label(job: models.UploadJob) -> str:
    """Use an explicit book name, or retain the uploaded filename identity."""
    explicit = str(job.source_book or "").strip()
    if explicit:
        return explicit
    filename = Path(str(job.filename or "")).name.strip()
    stem = Path(filename).stem.strip()
    fallback = stem or filename or "Uploaded source"
    # ``sources`` is currently a delimiter-separated legacy field. A raw
    # filename comma/semicolon would be parsed as multiple fake books.
    fallback = re.sub(r"[,;\r\n]+", " - ", fallback)
    fallback = re.sub(r"\s+", " ", fallback).strip(" -")
    return fallback or "Uploaded source"


def generate_post_learning(
    db: Session,
    job_id: int,
    target_chapter_id: int,
    *,
    owner_sub: str | None = None,
) -> dict:
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
        learning_kind="post",
    )
    if job.status != "converted":
        if job.status == "generated":
            raise ValueError(
                "this upload has already been generated; start a new upload")
        raise ValueError("convert the uploaded document to MMD before generating")
    chapter = db.get(models.Chapter, target_chapter_id)
    if not chapter:
        raise ValueError("upload job or target chapter not found")
    if not job.mmd_text:
        raise ValueError("convert the uploaded document to MMD before generating")
    progress.log(f"Post-learning generation into chapter '{chapter.chapter_title}'.")
    artifacts: dict = {}
    # The Architect (docs/aegis-restructure.md §8.1): assemble this run's
    # instruction set before any fingerprint or checkpoint identity is
    # computed — its hash joins every decision key, so a changed instruction
    # set can never silently reuse old verdicts. Assembly failure fails
    # closed here, pre-spend, before generation begins (The Fixer covers
    # mid-run blocks only and deliberately does not apply). The set is
    # persisted into the artifact directory and ships in diagnostics.
    artifact_dir = None
    artifact_dir_helper = getattr(uploads, "source_artifact_directory", None)
    if callable(artifact_dir_helper):
        try:
            artifact_dir = artifact_dir_helper(int(job.id))
        except Exception:  # noqa: BLE001 - diagnostics dir must never block
            artifact_dir = None
    instruction_set = instruction_architect.ensure_instruction_set(
        metadata={
            "board": chapter.board,
            "grade": chapter.grade,
            "subject": chapter.subject,
            "unit": chapter.unit,
            "chapter_title": chapter.chapter_title,
            "chapter_id": chapter.id,
            "chapter_code": chapter.chapter_code,
            "learning_kind": "Post",
            "source_book": job.source_book or "",
        },
        source_text=str(job.mmd_text or ""),
        artifact_dir=artifact_dir,
    )
    instruction_hash = str(
        instruction_set.get("instruction_set_sha256") or "")
    instruction_slots = copy.deepcopy(instruction_set.get("slots") or {})
    for flag in instruction_set.get("review_flags") or []:
        progress.log(f"Architect review flag: {flag}", level="warning")
    progress.log(
        "Instruction set "
        f"{instruction_hash[:12]} assembled "
        f"({instruction_set.get('slots_source')}); its hash joins every "
        "decision identity for this run."
    )
    fingerprint = _generation_checkpoint_fingerprint(
        job, chapter, instruction_set_sha256=instruction_hash)
    target_identity = _generation_target_identity(chapter)
    stored_checkpoint = job.generation_checkpoint or {}
    resume_checkpoint = (
        stored_checkpoint
        if _checkpoint_identity_matches_generation(
            stored_checkpoint, job=job, chapter=chapter,
            instruction_set_sha256=instruction_hash)
        else None
    )
    if stored_checkpoint and resume_checkpoint is None:
        message = _checkpoint_mismatch_message(
            stored_checkpoint,
            expected_fingerprint=fingerprint,
            expected_target=target_identity,
        )
        progress.log(message, level="error")
        raise ValueError(message)
    resumed: dict = {}
    if resume_checkpoint:
        resume_checkpoint, resumed = (
            _persist_compatible_generation_checkpoint_mirror(
                db,
                job,
                fingerprint=fingerprint,
                target_identity=target_identity,
                target_chapter_id=target_chapter_id,
                instruction_set_sha256=instruction_hash,
            )
        )
    initial_agent_resolution_ids: set[str] = set()
    existing_pause = _existing_human_decision_pause(
        db,
        job,
        resume_checkpoint,
        agent_resolution_ids=initial_agent_resolution_ids,
        owner_sub=owner_sub,
    )
    if existing_pause is not None:
        return existing_pause
    _raise_if_source_replacement_required(resume_checkpoint)
    if resume_checkpoint:
        stage_label = (
            resumed.get("stage_label")
            or resumed.get("stage")
            or "saved stage"
        )
        progress.log(
            f"Resuming from checkpoint '{stage_label}'; every earlier "
            "compatible completed stage will be reused.",
            level="success",
        )

    def save_checkpoint(checkpoint: dict) -> None:
        discarded_stage = (
            str(checkpoint.get("stage") or "").strip()
            if checkpoint.get("checkpoint_action") == "discard_stage"
            else ""
        )
        durable = _merge_generation_checkpoint_history(
            job.generation_checkpoint,
            checkpoint,
            fingerprint=fingerprint,
            target_identity=target_identity,
            target_chapter_id=target_chapter_id,
            instruction_set_sha256=instruction_hash,
        )
        job.generation_checkpoint = durable
        if discarded_stage:
            next_retry = (
                "retry resumes from the newest remaining compatible stage"
                if durable
                else "retry restarts generation from the beginning"
            )
            job.detail = (
                f"Discarded invalid generation checkpoint "
                f"'{discarded_stage}'; {next_retry}."
            )
            uploads.persist_current_openai_usage(
                db, job.id, owner_sub=owner_sub
            )
            drive_checkpoints.schedule_checkpoint_backup(job.id)
            progress.log(
                f"Discarded durable checkpoint stage: {discarded_stage}.",
                level="warning",
            )
            return
        if (
            "question_task_inventory" in checkpoint
            or "mined_types" in checkpoint
        ):
            _store_inventory(job, {
                "question_task_inventory": checkpoint.get(
                    "question_task_inventory") or {},
                "mined_types": checkpoint.get("mined_types") or {},
            })
        if (
            checkpoint.get("source_review_resolution_applied")
            or str(checkpoint.get("stage") or "").strip()
            == "source_topic_review"
        ):
            # These snapshots were derived from topology/source text that is
            # now known to be incomplete. Clear it in the same durable update
            # as the review checkpoint, after any generic payload handling so
            # a malformed review checkpoint cannot repopulate stale inventory.
            job.question_inventory = {}
        label = (
            checkpoint.get("stage_label")
            or checkpoint.get("stage")
            or "completed stage"
        )
        job.detail = (
            f"Generation checkpoint saved at {label}; retry resumes from "
            "the newest compatible stage."
        )
        # Commit the checkpoint and cumulative billing usage atomically. The
        # usage helper is idempotent within this run, so successive automatic
        # checkpoints cannot count earlier responses again.
        uploads.persist_current_openai_usage(
            db, job.id, owner_sub=owner_sub
        )
        drive_checkpoints.schedule_checkpoint_backup(job.id)
        progress.log(
            f"Saved durable checkpoint: {label} "
            f"({float(checkpoint.get('progress') or 0.0):.0%}).",
            level="success",
        )

    recovery_metadata = {
        "subject": chapter.subject,
        "board": chapter.board,
        "grade": chapter.grade,
        "unit": chapter.unit,
        "chapter_title": chapter.chapter_title,
        "chapter_id": chapter.id,
        "chapter_code": chapter.chapter_code,
        "learning_kind": "Post",
        # The Architect's run instructions: the hash joins the sealed
        # envelope, the kernel decision keys, semantic_context_hash and the
        # pre-spend gate identities; the slots render into every prompt.
        "instruction_set_sha256": instruction_hash,
        "instruction_slots": instruction_slots,
    }
    def generation_attempt() -> list[dict]:
        # Every automatic checkpoint is durable. On retry, obtain the
        # current envelope rather than retaining the entry captured before the
        # first attempt, and clear only non-durable in-memory artifacts.
        artifacts.clear()
        current_resume = (
            copy.deepcopy(job.generation_checkpoint)
            if _checkpoint_matches_generation(
                job.generation_checkpoint or {},
                job=job,
                chapter=chapter,
                instruction_set_sha256=instruction_hash,
            )
            else resume_checkpoint
        )
        with _human_decision_resolution_context(current_resume):
            return generation.concepts_from_mmd(
                job.mmd_text,
                **recovery_metadata,
                artifacts=artifacts,
                resume_checkpoint=current_resume,
                checkpoint_callback=save_checkpoint,
            )

    def generation_and_deposit_attempt() -> tuple[
        list[dict], list[int], list[int], dict
    ]:
        records = generation_attempt()
        _store_inventory(job, artifacts)
        final_grounding = artifacts.get(
            grounding_certificate.FINAL_CERTIFICATE_FIELD
        )
        if config.use_live_generation() and not isinstance(
            final_grounding, dict
        ):
            raise DepositValidationError(
                "live post-learning output has no final payload/evidence "
                "grounding certificate"
            )
        try:
            created_ids, merged_ids, written = _deposit_and_publish_concepts(
                db,
                chapter_id=target_chapter_id,
                records=records,
                pre_post="Post",
                source_book=_job_source_label(job),
                inventory=artifacts.get("question_task_inventory"),
                mined_types=artifacts.get("mined_types"),
                source_text=job.mmd_text,
                final_grounding_certificate=final_grounding,
                grounding_audit_job=job,
            )
        except DepositValidationError:
            db.rollback()
            db.refresh(job)
            newest = generation._newest_compatible_concept_checkpoint(
                job.generation_checkpoint)
            if newest and newest.get("stage") == "final_content_ready":
                # Never select the exact terminal payload that the deposit
                # validator just rejected. Live recovery repairs an earlier
                # semantic checkpoint in this run; dry/manual resume likewise
                # starts from the newest remaining compatible stage.
                save_checkpoint({
                    "checkpoint_action": "discard_stage",
                    "stage": "final_content_ready",
                    "reason": "deterministic deposit validation failed",
                })
            raise
        except Exception:
            # A failed deposit must leave no partially materialized concepts.
            # The durable generation checkpoint was committed separately, so
            # refresh it after rollback for bounded same-run semantic recovery.
            db.rollback()
            db.refresh(job)
            raise
        return records, created_ids, merged_ids, written

    paused, pipeline_result = _run_with_human_decision_pause(
        generation_and_deposit_attempt,
        db=db,
        job=job,
        fingerprint=fingerprint,
        target_chapter_id=target_chapter_id,
        owner_sub=owner_sub,
        initial_agent_resolution_ids=initial_agent_resolution_ids,
    )
    if paused is not None:
        return paused
    records, created_ids, merged_ids, written = pipeline_result

    deposited_grounding = written.get("grounding_certificate")
    if config.use_live_generation() and not isinstance(
        deposited_grounding, dict
    ):
        raise DepositValidationError(
            "live post-learning publication completed without a verified "
            "payload/evidence grounding certificate"
        )
    if isinstance(deposited_grounding, dict):
        inventory_audit = copy.deepcopy(job.question_inventory or {})
        inventory_audit[_DEPOSITED_GROUNDING_CERTIFICATE_KEY] = copy.deepcopy(
            deposited_grounding
        )
        job.question_inventory = inventory_audit
    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.generation_checkpoint = {}
    job.detail = (
        f"created {len(created_ids)} post-learning concepts, "
        f"merged sources into {len(merged_ids)} existing"
    )
    db.commit()
    progress.set_progress(1.0, label="Done")
    progress.log(
        f"Created {len(created_ids)} post-learning concepts "
        f"({len(merged_ids)} merged).", level="success")
    progress.log(f"Output workbook path: {config.BULK_IMPORT_OUTPUT}")
    for issue in written.get("issues") or []:
        # Recorded, flagged, and visible to the reviewer — never a halt (Q13).
        progress.log(
            f"Bulk Import workbook issue [{issue.get('code')}]: "
            f"{issue.get('detail')}",
            level="warning",
        )
    migration = written.get("layout_migration")
    if migration:
        progress.log(
            "Migrated the output workbook from layout "
            f"{migration.get('source_layout_id')} to "
            f"{migration.get('target_layout_id')}; the pre-migration workbook "
            f"is retained at {migration.get('sibling_path')}.",
            level="warning",
        )
    return {
        "job_id": job_id,
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
        "inventory_items": len((job.question_inventory or {}).get("items", [])),
        "output_certificate_sha256": str(
            (deposited_grounding or {}).get("certificate_sha256") or ""
        ),
        # Q16's recorded receipt and the issues it produced ride the run's own
        # result: a migration that dropped a value is a release issue with a
        # recorded Fixer decision, not a halt.
        "workbook_issues": list(written.get("issues") or []),
        "workbook_fixer_decisions": list(
            written.get("fixer_decisions") or []),
        "layout_migration": written.get("layout_migration"),
    }
