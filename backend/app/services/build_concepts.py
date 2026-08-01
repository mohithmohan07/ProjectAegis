"""Module 2: Build Concepts.

  Post Learning — upload a document (any format), convert to MMD, parse it into
  concepts, and deposit them under a chapter.

  Pre Learning — two options:
    A. Upload: upload -> MMD -> derive pre-learning concepts -> deposit.
    B. Use existing Post Learning: pick one or more chapters; their existing
       post-learning concepts drive generation of pre-learning concepts.

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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import config, models, schemas
from .. import bulk_import as bi
from ..bulk_import import workbook_sync, writer
from . import (
    canonical_source_phase22,
    chapter_durations,
    concept_cleanup,
    concept_refiner,
    concept_validator,
    drive_checkpoints,
    generation,
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


_HUMAN_DECISIONS_KEY = "human_decisions"
_HUMAN_DECISIONS_VERSION = 1
_HUMAN_DECISION_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HUMAN_DECISION_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_HUMAN_DECISIONS = 5_000
_MAX_DEFERRED_HUMAN_DECISIONS = 5_000


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
    candidates = [{
        key: str(row.get(key) or "")
        for key in (
            "target_id", "concept_id", "title", "topic", "coverage", "gap",
        )
    } for row in rows("candidates", 100)]
    evidence = [
        {key: str(row.get(key) or "") for key in ("page", "label", "text")}
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


def _applied_human_decision_ids(checkpoint: dict | None) -> set[str]:
    """Decision IDs whose bounded action is durably represented here."""
    if not isinstance(checkpoint, dict):
        return set()
    ids: set[str] = set()
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
    resolutions = _ready_human_decisions(checkpoint)
    durable_resolutions = _human_decisions_with_status(
        checkpoint, statuses={"ready", "consumed"})
    if not resolutions and not durable_resolutions:
        yield
        return
    from . import canonical_source_phase3 as phase3
    from . import canonical_source_phase33_preflight_contract as phase33
    with phase3.human_source_resolution_context(
        copy.deepcopy(resolutions)
    ):
        with phase33.human_resolution_context(copy.deepcopy(resolutions)):
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
    normalized_title = bi.normalize_question_text(display_name)
    for t in chapter.topics:
        stored_title = bi.strip_topic_title(t.topic_title) or t.topic_title
        if (
            t.pre_post_learning == pre_post
            and bi.normalize_question_text(stored_title) == normalized_title
        ):
            # The accepted topology owns presentation as well as placement.
            # Reuse the identity, but do not retain stale casing/numbering.
            t.topic_title = topic_title
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
) -> tuple[list[int], list[int]]:
    """Create concepts under the chapter, reusing existing ones across books.

    Returns (created_ids, merged_ids): a same-learning-kind normalized-title
    match is refreshed from the newly validated record and its sources grow.
    """
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
    records = concept_cleanup.dedupe_similar_titles_chapter_wide(records)
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
                records, inventory, mined_types)
            # Inventory repair deliberately restores exact source wording. In
            # mathematics that wording can contain bare TeX, so canonicalize
            # after the repair as well as before it. Recheck exact coverage
            # once more because Katex wrappers are presentation-only and must
            # never change qid ownership or placement.
            records = generation._canonicalize_concept_rich_text(records)
            records = generation._enforce_rendered_inventory_coverage(
                records, inventory, mined_types)
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
        topic_violations = generation._rendered_inventory_topic_violations(
            records, inventory, mined_types)
        if topic_violations:
            qids = sorted({
                str(item.get("qid") or "").strip()
                for item in topic_violations
                if str(item.get("qid") or "").strip()
            })
            progress.log(
                "Deposit inventory topic validation failed for "
                f"{len(topic_violations)} question/task placement(s).",
                level="error",
            )
            raise DepositValidationError(
                "question/task inventory topic placement failed before "
                "deposit: " + ",".join(qids)
            )
        try:
            generation._validate_final_or_raise(
                records,
                stage="deposit",
                inventory=inventory,
                mined_types=mined_types,
                source_text=source_text,
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
        strict_culmination_recap=pre_post == "Post",
        source_text=source_text if pre_post == "Post" else "",
    )
    classified_fatal = generation._fatal_errors(report)
    # Post deposit is the same terminal boundary as generation's final gate, so
    # use its complete fatal-code policy even when no inventory was supplied.
    # Pre-learning validation deliberately remains tolerant of warnings emitted
    # by Post-only/intermediate contracts.
    fatal = (
        classified_fatal
        if pre_post == "Post"
        else [
            error for error in classified_fatal
            if error.get("severity") == "error"
        ]
    )
    progress.log(
        f"Deposit validation: {len(fatal)} fatal error(s), "
        f"{report['summary'].get('warnings', 0)} warning(s).")
    if fatal:
        codes = ", ".join(sorted({e["code"] for e in fatal}))
        raise DepositValidationError(
            f"concept validation failed before deposit: {codes}")

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

    n_concepts = sum(
        sum(
            1 for concept in topic.concepts
            if not active_concept_ids or concept.id in active_concept_ids
        )
        for topic in topics
    )
    if meta_summary.get("chapter_description"):
        chapter.chapter_description = meta_summary["chapter_description"]
    elif _is_blank(chapter.chapter_description) and topics:
        topic_names = ", ".join(
            bi.strip_topic_title(t.topic_title) or t.topic_title for t in topics)
        chapter.chapter_description = (
            f"This chapter develops {n_concepts} concept(s) across "
            f"{len(topics)} topic(s): {topic_names}."
        )
    finalized = _parse_duration_minutes(chapter.chapter_duration)
    if finalized:
        chapter.chapter_duration = f"{finalized} minutes"
    elif meta_summary.get("chapter_duration_minutes") and _is_blank(chapter.chapter_duration):
        chapter.chapter_duration = f"{meta_summary['chapter_duration_minutes']} minutes"
    elif _is_blank(chapter.chapter_duration) and n_concepts:
        # Rough classroom estimate: ~12 minutes of instruction per concept.
        chapter.chapter_duration = f"{max(40, n_concepts * 12)} minutes"


# --------------------------------------------------------------------------- #
# Question / Task Inventory (extraction-completeness audit)
# --------------------------------------------------------------------------- #

def _store_inventory(job: models.UploadJob, artifacts: dict) -> None:
    """Persist the generation-time inventory + mined Types on the upload job."""
    inventory = artifacts.get("question_task_inventory") or {}
    mined = artifacts.get("mined_types") or {}
    if not inventory.get("items") and not mined.get("types"):
        return
    stored = {
        "items": inventory.get("items", []),
        "stats": inventory.get("stats", {}),
        "mined_types": mined.get("types", []),
    }
    certification_key = generation._PLACEMENT_CERTIFICATIONS_KEY
    certification_ledger = mined.get(certification_key)
    if isinstance(certification_ledger, dict):
        # Keep the established list-shaped ``mined_types`` field for CSV and
        # bundle compatibility while retaining the exact qid -> host evidence
        # needed to audit a successful job after its checkpoint is cleared.
        stored[certification_key] = copy.deepcopy(certification_ledger)
    job.question_inventory = stored


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


def _generation_checkpoint_fingerprint(
    job: models.UploadJob, chapter: models.Chapter,
) -> str:
    """Semantic input fingerprint, intentionally independent of DB/git IDs."""
    identity = _generation_target_identity(chapter)
    payload = (
        "concept-generation-checkpoint-v2\0"
        + "\0".join([
            _stable_checkpoint_value(job.learning_kind or "post"),
            *(identity[field] for field in (
                "board", "grade", "subject", "unit",
                "chapter_title", "chapter_code",
            )),
            str(job.mmd_text or ""),
        ])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checkpoint_matches_generation(
    checkpoint: dict, *,
    job: models.UploadJob,
    chapter: models.Chapter,
) -> bool:
    if not generation._valid_concept_checkpoint(checkpoint):
        return False
    stable_fingerprint = _generation_checkpoint_fingerprint(job, chapter)
    target_identity = _generation_target_identity(chapter)
    if checkpoint.get("checkpoint_format") == generation._CONCEPT_CHECKPOINT_FORMAT:
        return bool(
            checkpoint.get("fingerprint") == stable_fingerprint
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
        checkpoint.get("fingerprint") == stable_fingerprint
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
            # Every concept/pre-learning stage was derived from the old text
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
) -> dict:
    """Prune unusable history and mirror the stage this deployment will use."""
    history = [
        copy.deepcopy(entry)
        for entry in generation._concept_checkpoint_entries(stored)
        if generation._compatible_concept_checkpoint_entry(entry)
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
    return _copy_human_decision_ledger(normalized, stored)


def _persist_compatible_generation_checkpoint_mirror(
    db: Session,
    job: models.UploadJob,
    *,
    fingerprint: str,
    target_identity: dict[str, str],
    target_chapter_id: int,
) -> tuple[dict | None, dict]:
    """Persist the actual fallback stage before a resumed run can fail."""
    stored = copy.deepcopy(job.generation_checkpoint or {})
    normalized = _compatible_generation_checkpoint_envelope(
        stored,
        fingerprint=fingerprint,
        target_identity=target_identity,
        target_chapter_id=target_chapter_id,
    )
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


def _semantic_recovery_topic_ids() -> dict[str, str]:
    """Return current source-graph topic identity without requiring Phase 3."""
    try:
        from . import canonical_source_phase3 as phase3

        graph = phase3.active_graph()
        if not isinstance(graph, dict):
            session = phase3.active_session()
            graph = (
                session.get("graph")
                if isinstance(session, dict)
                else None
            )
    except Exception:
        graph = None
    if not isinstance(graph, dict):
        return {}
    return {
        str(topic.get("title") or "").strip():
        str(topic.get("topic_id") or "").strip()
        for topic in graph.get("topics") or []
        if isinstance(topic, dict)
        and str(topic.get("title") or "").strip()
    }


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


def _semantic_recovery_validation_errors(
    records: list[dict],
) -> list[dict]:
    """Expose row-local validator diagnostics to the recovery scope resolver."""
    try:
        report = concept_validator.validate_concept_rows(
            records,
            allow_types=True,
            require_culmination=False,
            allow_culmination=True,
        )
    except Exception:
        return []
    return [
        error for error in report.get("errors") or []
        if isinstance(error, dict) and error.get("severity") == "error"
    ]


def _persist_semantic_recovery_checkpoint(
    db: Session,
    job: models.UploadJob,
    result: semantic_recovery.RepairResult,
    *,
    fingerprint: str,
    target_identity: dict[str, str],
    target_chapter_id: int,
    owner_sub: str | None,
) -> None:
    """Replace one repaired stage and discard only its dependent later stages."""
    repaired = copy.deepcopy(result.checkpoint)
    repaired["saved_at"] = datetime.now(timezone.utc).isoformat()
    if not generation._compatible_concept_checkpoint_entry(repaired):
        raise RuntimeError(
            "semantic recovery refused to persist an incompatible checkpoint"
        )
    base_order = generation._checkpoint_order(result.base_stage)
    retained = [
        copy.deepcopy(entry)
        for entry in generation._concept_checkpoint_entries(
            job.generation_checkpoint)
        if (
            generation._compatible_concept_checkpoint_entry(entry)
            and generation._checkpoint_order(
                str(entry.get("stage") or "")) < base_order
        )
    ]
    durable: dict = {}
    for entry in [*retained, repaired]:
        durable = _merge_generation_checkpoint_history(
            durable,
            entry,
            fingerprint=fingerprint,
            target_identity=target_identity,
            target_chapter_id=target_chapter_id,
        )
    job.generation_checkpoint = _copy_human_decision_ledger(
        durable, job.generation_checkpoint)
    job.detail = (
        f"Semantic recovery repaired {result.changed_count} bounded unit(s) "
        f"at '{result.base_stage}'; dependent later stages will be replayed."
    )
    # Checkpoint and current provider usage are committed together. A database
    # or filesystem failure propagates and is never converted into GPT repair.
    uploads.persist_current_openai_usage(
        db, job.id, owner_sub=owner_sub)
    drive_checkpoints.schedule_checkpoint_backup(job.id)


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
        written = writer.append_concepts(db, staged, concept_ids)
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
    """Stage, commit, and publish while holding the shared workbook lock."""
    staged_workbook: Path | None = None
    with workbook_sync.output_workbook_lock():
        try:
            staged_workbook, written = _stage_concept_workbook(
                db,
                target,
                concept_ids,
            )
            db.commit()
            _publish_staged_workbook(staged_workbook, target)
            staged_workbook = None
            return written
        except Exception:
            db.rollback()
            if staged_workbook is not None:
                staged_workbook.unlink(missing_ok=True)
            raise


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
) -> tuple[list[int], list[int], dict[str, int]]:
    """Serialize final dedupe, DB commit, and shared workbook publication."""
    # Phase 2.2 may have verified source-visible text that Mathpix omitted.
    # Deposit validation sees the same derived semantic source as concept
    # extraction, while UploadJob.mmd_text remains the immutable audit copy.
    source_text = canonical_source_phase22.active_semantic_source(source_text)
    with workbook_sync.output_workbook_lock():
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
        )
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
    if not items:
        raise ValueError(
            "no question/task inventory stored for this job — run a live "
            "concept generation first")

    types_by_qid: dict[str, list[tuple[str, str]]] = {}
    for t in data.get("mined_types", []):
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

def _awaiting_human_decision_result(
    job: models.UploadJob,
    pending: dict,
) -> dict:
    return {
        "job_id": job.id,
        "status": "awaiting_decision",
        "pending_decision": copy.deepcopy(pending),
        "resume_required": False,
    }


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
) -> dict | None:
    """Return the durable pause without starting another provider request."""
    pending = _pending_human_decision(checkpoint)
    if pending is None:
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
    progress.set_progress(
        job.checkpoint_progress,
        label="Paused for your decision",
    )
    progress.log(
        "This generation is waiting for the saved semantic decision; "
        "no API request was started.",
        level="warning",
    )
    return _awaiting_human_decision_result(job, pending)


def _source_replacement_required(checkpoint: dict | None) -> bool:
    """Whether a source-review answer terminally requires a new conversion."""

    return any(
        str(row.get("choice") or "") == "replace_source"
        and str(row.get("kind") or "") in {
            "phase3_source_graph_review",
            "phase31_source_grounding_semantic_conflict",
            "phase32_concept_blueprint_semantic_conflict",
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
    resolutions = [
        copy.deepcopy(entry)
        for entry in ledger.get("resolutions") or []
        if isinstance(entry, dict)
    ]
    if len(resolutions) >= _MAX_HUMAN_DECISIONS:
        raise RuntimeError(
            "human semantic decision history reached its safety limit")
    for entry in resolutions:
        if entry.get("decision_id") == pending["decision_id"]:
            raise RuntimeError(
                "a previously resolved human decision was requested again; "
                "the saved resolution was not consumed")

    existing_pending = ledger.get("pending")
    if (
        isinstance(existing_pending, dict)
        and existing_pending.get("decision_id") != pending["decision_id"]
    ):
        raise RuntimeError(
            "another human semantic decision is already pending")

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
        "resolutions": resolutions,
    }
    job.generation_checkpoint = checkpoint
    job.status = "converted"
    job.detail = (
        "Generation paused for your semantic decision. No API request is "
        "running while paused."
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
        "Generation paused at the saved checkpoint for a semantic decision; "
        "no automatic semantic retry will run.",
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
) -> tuple[dict | None, object | None]:
    """Run one post/pre pipeline and convert only the typed pause exception."""
    try:
        return None, operation()
    except semantic_recovery.HumanDecisionRequired as exc:
        pending = _persist_pending_human_decision(
            db,
            job,
            exc.pending_decision,
            fingerprint=fingerprint,
            target_chapter_id=target_chapter_id,
            owner_sub=owner_sub,
        )
        return _awaiting_human_decision_result(job, pending), None


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


def _record_human_semantic_decision_locked(
    db: Session,
    job: models.UploadJob,
    decision_id: str,
    *,
    choice: str,
    instruction: str,
    target_id: str,
    target_concept_id: str,
) -> dict:
    """Mutate a decision ledger while ``exclusive_job_operation`` is held."""
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
    if choice == "custom_instruction" and not instruction:
        raise ValueError("instruction is required for custom_instruction")

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
    if offered and choice not in offered:
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

    resolution = {
        "decision_id": decision_id,
        "context_hash": str(pending.get("context_hash") or ""),
        "choice": choice,
        "instruction": instruction,
        "target_id": target_id,
        "target_concept_id": target_concept_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
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
    fingerprint = _generation_checkpoint_fingerprint(job, chapter)
    target_identity = _generation_target_identity(chapter)
    stored_checkpoint = job.generation_checkpoint or {}
    resume_checkpoint = (
        stored_checkpoint
        if _checkpoint_matches_generation(
            stored_checkpoint, job=job, chapter=chapter)
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
    existing_pause = _existing_human_decision_pause(
        db, job, resume_checkpoint)
    if existing_pause is not None:
        return existing_pause
    _raise_if_source_replacement_required(resume_checkpoint)
    if resume_checkpoint:
        resume_checkpoint, resumed = (
            _persist_compatible_generation_checkpoint_mirror(
                db,
                job,
                fingerprint=fingerprint,
                target_identity=target_identity,
                target_chapter_id=target_chapter_id,
            )
        )
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
    }
    recovery_policy = semantic_recovery.RecoveryPolicy.from_environment()

    def generation_attempt() -> list[dict]:
        # Every automatic checkpoint is durable. On recovery, obtain the
        # current envelope rather than retaining the entry captured before the
        # first attempt, and clear only non-durable in-memory artifacts.
        artifacts.clear()
        current_resume = (
            copy.deepcopy(job.generation_checkpoint)
            if _checkpoint_matches_generation(
                job.generation_checkpoint or {},
                job=job,
                chapter=chapter,
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

    def repair_checkpoint(
        checkpoint: dict,
        context: semantic_recovery.RecoveryContext,
    ) -> semantic_recovery.RepairResult | None:
        return semantic_recovery.repair_concept_checkpoint_via_gpt(
            checkpoint,
            context,
            api_call=generation._openai_json,
            metadata=recovery_metadata,
            source_text=_semantic_recovery_source_text(job.mmd_text),
            topic_id_by_title=_semantic_recovery_topic_ids(),
            validation_errors=_semantic_recovery_validation_errors,
            max_rows=recovery_policy.max_rows_per_attempt,
            max_source_chars=recovery_policy.max_source_chars,
        )

    def persist_repair(
        result: semantic_recovery.RepairResult,
        _context: semantic_recovery.RecoveryContext,
    ) -> None:
        _persist_semantic_recovery_checkpoint(
            db,
            job,
            result,
            fingerprint=fingerprint,
            target_identity=target_identity,
            target_chapter_id=target_chapter_id,
            owner_sub=owner_sub,
        )

    def generation_and_deposit_attempt() -> tuple[
        list[dict], list[int], list[int], dict
    ]:
        records = generation_attempt()
        _store_inventory(job, artifacts)
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

    def run_pipeline():
        if config.use_live_generation():
            return semantic_recovery.run_with_semantic_recovery(
                generation_and_deposit_attempt,
                checkpoint_snapshot=lambda: copy.deepcopy(
                    job.generation_checkpoint or {}),
                repair_checkpoint=repair_checkpoint,
                persist_repair=persist_repair,
                policy=recovery_policy,
                log=progress.log,
            )
        # Dry mode is test/development-only and has no GPT repair provider.
        return generation_and_deposit_attempt()

    paused, pipeline_result = _run_with_human_decision_pause(
        run_pipeline,
        db=db,
        job=job,
        fingerprint=fingerprint,
        target_chapter_id=target_chapter_id,
        owner_sub=owner_sub,
    )
    if paused is not None:
        return paused
    records, created_ids, merged_ids, written = pipeline_result

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
    progress.log(
        "Parent concept export: "
        + ("parent_concept column" if written.get("parent_column") else "related_concepts fallback")
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
    }


# --------------------------------------------------------------------------- #
# Pre Learning
# --------------------------------------------------------------------------- #

def create_pre_learning_upload_job(
    db: Session, *, filename: str, raw_bytes: bytes, source_book: str = "",
    owner_sub: str | None = None,
) -> models.UploadJob:
    """Stage the file only — conversion to MMD is a separate explicit step."""
    job = models.UploadJob(
        owner_sub=uploads.normalize_owner_sub(owner_sub),
        module="build_concepts", upload_type="document", learning_kind="pre",
        filename=Path(filename).name, mmd_text="", status="uploaded",
        source_book=source_book.strip(),
    )
    return uploads.persist_new_job(db, job, raw_bytes)


def generate_pre_learning_from_upload(
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
        learning_kind="pre",
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
    progress.log(f"Pre-learning generation for chapter '{chapter.chapter_title}'.")

    # Extract the chapter's concept map first, then derive prerequisites from
    # it. Live mode runs the full dependency-architecture derivation (syllabus
    # filter + auditor pass); dry mode keeps the deterministic framing.
    artifacts: dict = {}
    fingerprint = _generation_checkpoint_fingerprint(job, chapter)
    target_identity = _generation_target_identity(chapter)
    stored_checkpoint = job.generation_checkpoint or {}
    resume_checkpoint = (
        stored_checkpoint
        if _checkpoint_matches_generation(
            stored_checkpoint, job=job, chapter=chapter)
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
    existing_pause = _existing_human_decision_pause(
        db, job, resume_checkpoint)
    if existing_pause is not None:
        return existing_pause
    _raise_if_source_replacement_required(resume_checkpoint)
    if resume_checkpoint:
        resume_checkpoint, resumed = (
            _persist_compatible_generation_checkpoint_mirror(
                db,
                job,
                fingerprint=fingerprint,
                target_identity=target_identity,
                target_chapter_id=target_chapter_id,
            )
        )
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
        # Keep the portable/Drive checkpoint's usage current without adding
        # the active run's already-persisted responses a second time.
        uploads.persist_current_openai_usage(
            db, job.id, owner_sub=owner_sub
        )
        drive_checkpoints.schedule_checkpoint_backup(job.id)
        progress.log(
            f"Saved durable checkpoint: {label} "
            f"({float(checkpoint.get('progress') or 0.0):.1%}).",
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
        "learning_kind": "Pre",
    }
    recovery_policy = semantic_recovery.RecoveryPolicy.from_environment()
    recovery_scope = "post_generation"

    def generation_attempt() -> list[dict]:
        nonlocal recovery_scope
        artifacts.clear()
        current_resume = (
            copy.deepcopy(job.generation_checkpoint)
            if _checkpoint_matches_generation(
                job.generation_checkpoint or {},
                job=job,
                chapter=chapter,
            )
            else resume_checkpoint
        )
        recovery_scope = "post_generation"
        with _human_decision_resolution_context(current_resume):
            base = generation.concepts_from_mmd(
                job.mmd_text,
                subject=chapter.subject,
                board=chapter.board,
                grade=chapter.grade,
                unit=chapter.unit,
                chapter_title=chapter.chapter_title,
                chapter_id=chapter.id,
                chapter_code=chapter.chapter_code,
                learning_kind="Post",
                artifacts=artifacts,
                resume_checkpoint=current_resume,
                checkpoint_callback=save_checkpoint,
                completion_progress=0.98,
            )
        _store_inventory(job, artifacts)
        # The target concept map may have advanced its checkpoint during this
        # attempt. Pre-learning must resume from that current durable envelope,
        # including any repaired draft/audit stage retained from a prior pass.
        pre_resume = (
            copy.deepcopy(job.generation_checkpoint)
            if _checkpoint_matches_generation(
                job.generation_checkpoint or {},
                job=job,
                chapter=chapter,
            )
            else current_resume
        )
        recovery_scope = "pre_generation"
        return generation.pre_learning_from_rows(
            base,
            subject=chapter.subject,
            grade=chapter.grade,
            board=chapter.board,
            chapter_title=chapter.chapter_title,
            unit=chapter.unit,
            resume_checkpoint=pre_resume,
            checkpoint_callback=save_checkpoint,
        )

    def repair_checkpoint(
        checkpoint: dict,
        context: semantic_recovery.RecoveryContext,
    ) -> semantic_recovery.RepairResult | None:
        return semantic_recovery.repair_pre_learning_checkpoint_via_gpt(
            checkpoint,
            context,
            api_call=generation._openai_json,
            metadata=recovery_metadata,
            source_text=_semantic_recovery_source_text(job.mmd_text),
            topic_id_by_title=_semantic_recovery_topic_ids(),
            validation_errors=_semantic_recovery_validation_errors,
            max_rows=recovery_policy.max_rows_per_attempt,
            max_source_chars=recovery_policy.max_source_chars,
            failure_scope=recovery_scope,
        )

    def persist_repair(
        result: semantic_recovery.RepairResult,
        _context: semantic_recovery.RecoveryContext,
    ) -> None:
        _persist_semantic_recovery_checkpoint(
            db,
            job,
            result,
            fingerprint=fingerprint,
            target_identity=target_identity,
            target_chapter_id=target_chapter_id,
            owner_sub=owner_sub,
        )

    def generation_and_deposit_attempt() -> tuple[
        list[dict], list[int], list[int], dict
    ]:
        nonlocal recovery_scope
        pre_records = generation_attempt()
        recovery_scope = "pre_deposit"
        try:
            created_ids, merged_ids, written = _deposit_and_publish_concepts(
                db,
                chapter_id=target_chapter_id,
                records=pre_records,
                pre_post="Pre",
                source_book=_job_source_label(job),
            )
        except Exception:
            # Preserve the separately committed generation checkpoint and
            # remove every partially materialized prerequisite row.
            db.rollback()
            db.refresh(job)
            raise
        return pre_records, created_ids, merged_ids, written

    def run_pipeline():
        if config.use_live_generation():
            return semantic_recovery.run_with_semantic_recovery(
                generation_and_deposit_attempt,
                checkpoint_snapshot=lambda: copy.deepcopy(
                    job.generation_checkpoint or {}),
                repair_checkpoint=repair_checkpoint,
                persist_repair=persist_repair,
                policy=recovery_policy,
                log=progress.log,
            )
        return generation_and_deposit_attempt()

    paused, pipeline_result = _run_with_human_decision_pause(
        run_pipeline,
        db=db,
        job=job,
        fingerprint=fingerprint,
        target_chapter_id=target_chapter_id,
        owner_sub=owner_sub,
    )
    if paused is not None:
        return paused
    pre_records, created_ids, merged_ids, written = pipeline_result

    job.status = "generated"
    job.deposit_scope_type = "chapter"
    job.deposit_scope_ids = [target_chapter_id]
    job.result_ids = created_ids
    job.generation_checkpoint = {}
    job.detail = (
        f"created {len(created_ids)} pre-learning concepts from upload, "
        f"merged sources into {len(merged_ids)} existing"
    )
    db.commit()
    progress.set_progress(1.0, label="Done")
    progress.log(
        f"Created {len(created_ids)} pre-learning concepts "
        f"({len(merged_ids)} merged).", level="success")
    progress.log(f"Output workbook path: {config.BULK_IMPORT_OUTPUT}")
    progress.log(
        "Parent concept export: "
        + ("parent_concept column" if written.get("parent_column") else "related_concepts fallback")
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
    }


def generate_pre_learning_from_existing(
    db: Session, chapter_ids: list[int], source_book: str = "",
) -> dict:
    """Option B: derive pre-learning concepts from existing post-learning chapters."""
    chapters = db.query(models.Chapter).filter(models.Chapter.id.in_(chapter_ids)).all()
    if not chapters:
        raise ValueError("no chapters selected")

    planned: list[tuple[int, list[dict]]] = []
    per_chapter: dict[int, int] = {}
    for chapter in chapters:
        post_concepts = [
            c for t in chapter.topics if t.pre_post_learning == "Post" for c in t.concepts
        ]
        if not post_concepts:
            per_chapter[chapter.id] = 0
            continue
        pre_records = generation.pre_learning_from_concepts(post_concepts)
        planned.append((chapter.id, [
            {
                "topic": rec["topic"],
                "concept_title": rec["concept_title"],
                "parent_concept": rec.get("parent_concept", ""),
                "concept_details": rec["concept_details"],
                "keywords": rec.get("keywords", ""),
            }
            for rec in pre_records
        ]))

    created_ids: list[int] = []
    merged_ids: list[int] = []
    try:
        with workbook_sync.output_workbook_lock():
            db.expire_all()
            for chapter_id, pre_records in planned:
                chapter = db.get(models.Chapter, chapter_id)
                if chapter is None:
                    raise ValueError("target chapter not found")
                created, merged = _deposit_concepts(
                    db, chapter, pre_records, "Pre", source_book)
                created_ids += created
                merged_ids += merged
                active_ids = set(created + merged)
                _sync_chapter_topic_summary(
                    chapter,
                    _chapter_meta_summary(
                        chapter,
                        active_ids,
                        pre_post="Pre",
                    ),
                    active_concept_ids=active_ids,
                    pre_post="Pre",
                )
                per_chapter[chapter.id] = len(created)
            written = _commit_and_publish_concept_workbook(
                db,
                config.BULK_IMPORT_OUTPUT,
                created_ids + merged_ids,
            )
    except Exception:
        db.rollback()
        raise
    return {
        "chapters": len(chapters),
        "concepts_created": len(created_ids),
        "concepts_merged": len(merged_ids),
        "concept_ids": created_ids + merged_ids,
        "per_chapter": per_chapter,
        "rows_appended": written["written"],
        "sources_updated": written["sources_updated"],
        "output_workbook": str(config.BULK_IMPORT_OUTPUT),
    }
