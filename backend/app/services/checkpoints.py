"""Portable, integrity-checked Build Concepts checkpoint bundles."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from . import (
    autonomous_resolution,
    generation,
    grounding_certificate,
    uploads,
)


BUNDLE_FORMAT = "aegis-concept-checkpoint"
BUNDLE_SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 25 * 1024 * 1024

# A byte limit alone does not stop a checksum-valid payload from containing an
# unreasonable nested shape.  These deliberately generous limits are above a
# normal chapter run while keeping database/browser work bounded.
MAX_MMD_CHARS = 16_000_000
MAX_NESTED_STRING_CHARS = 2_000_000
MAX_TOTAL_STRING_CHARS = 24_000_000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 300_000
MAX_COLLECTION_ITEMS = 20_000
MAX_CHECKPOINT_STAGES = 32
MAX_CHECKPOINT_RECORDS = 5_000
MAX_GENERATION_LOG_EVENTS = 1_200
MAX_LOG_MESSAGE_CHARS = 32_000
MAX_TOKEN_COUNT = 10**15
MAX_REQUEST_COUNT = 10**9
MAX_ESTIMATED_COST_USD = 10**9
MAX_RESUMABLE_JOBS = 20

# FROZEN serialization order: this tuple feeds the checkpoint
# fingerprint mirror and the exact-keys schema, so it changes only with
# a fingerprint version bump — never casually. It must stay equal to
# models.CHECKPOINT_TARGET_IDENTITY_FIELDS (the live vocabulary the
# writer and the decision-key projections share); a regression pins the
# equality so divergence is a conscious, versioned act.
_TARGET_FIELDS = (
    "board",
    "grade",
    "subject",
    "unit",
    "chapter_title",
    "chapter_code",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECISION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# Phase 3.8 issue statuses that mean "no further paid pass for this issue".
# ``exhausted`` is the spelling used before the terminal disposition existed
# and is still accepted so older exported bundles remain importable.
_PHASE38_TERMINAL_STATUSES = frozenset({"exhausted", "disposed"})
_PHASE38_STATUSES = _PHASE38_TERMINAL_STATUSES | {
    "active", "final_verification_pending",
}
# Kept as one list, not two. This used to restate the resolver's automatable
# choices, and the copies drifted: ``carry_forward`` was added to the resolver
# and not here, so a run that carried any decision failed its own bundle
# validation and Aegis could not reimport a checkpoint it had just written.
_AGENT_AUTOMATABLE_CHOICES = autonomous_resolution.AUTOMATABLE_CHOICES
_BUNDLE_KEYS = {
    "format", "bundle_schema_version", "exported_at",
    "payload_sha256", "payload",
}
_PAYLOAD_KEYS = {
    "job", "generation_checkpoint", "question_inventory",
    "openai_usage", "generation_log",
}
_JOB_KEYS = {
    "module", "upload_type", "learning_kind", "source_book", "filename",
    "mmd_text", "deposit_scope_type", "deposit_scope_ids",
}
_OPTIONAL_JOB_KEYS = {"chapter_duration_minutes"}
_USAGE_INTS = {
    "request_count": MAX_REQUEST_COUNT,
    "input_tokens": MAX_TOKEN_COUNT,
    "cached_input_tokens": MAX_TOKEN_COUNT,
    "cache_write_tokens": MAX_TOKEN_COUNT,
    "uncached_input_tokens": MAX_TOKEN_COUNT,
    "output_tokens": MAX_TOKEN_COUNT,
    "reasoning_tokens": MAX_TOKEN_COUNT,
    "total_tokens": MAX_TOKEN_COUNT,
}
_USAGE_TOP_KEYS = {
    "model", "models", *_USAGE_INTS, "estimated_cost_usd", "currency",
    "pricing_complete", "pricing_as_of", "pricing_source",
    # Cumulative run wall-clock and the per-(stage, lane) ledger — added
    # 2026-08-28 (owner request: one cumulative cost + time record, stage
    # wise). Optional, so pre-existing bundles stay valid.
    "elapsed_seconds", "stages",
}
_USAGE_MODEL_KEYS = {
    "model", *_USAGE_INTS, "estimated_cost_usd",
    "pricing_complete", "pricing_source",
}
_USAGE_STAGE_INTS = {
    key: maximum
    for key, maximum in _USAGE_INTS.items()
    if key != "uncached_input_tokens"
}
_USAGE_STAGE_KEYS = {
    "stage", "lane", *_USAGE_STAGE_INTS, "estimated_cost_usd",
    "pricing_complete", "first_ts", "last_ts", "elapsed_seconds",
}
# A generous ceiling for run wall-clock (one year) and epoch timestamps.
MAX_ELAPSED_SECONDS = 366 * 24 * 60 * 60
MAX_EPOCH_TS = 10**11


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8")


def _exact_keys(
    value: dict,
    allowed: set[str],
    path: str,
    *,
    required: set[str] | None = None,
) -> None:
    required = allowed if required is None else required
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    if missing:
        raise ValueError(f"{path} is missing field(s): {', '.join(missing)}")
    if extra:
        raise ValueError(
            f"{path} contains unsupported field(s): {', '.join(extra)}")


def _string(
    value: Any,
    path: str,
    maximum: int,
    *,
    nonempty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if len(value) > maximum:
        raise ValueError(
            f"{path} exceeds the {maximum:,}-character limit")
    if nonempty and not value.strip():
        raise ValueError(f"{path} must not be empty")
    return value


def _integer(
    value: Any,
    path: str,
    maximum: int,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{path} must be between {minimum:,} and {maximum:,}")
    return value


def _number(
    value: Any,
    path: str,
    maximum: float,
    *,
    minimum: float = 0.0,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(
            f"{path} must be a finite number between {minimum} and {maximum}")
    return result


def _timestamp(value: Any, path: str) -> str:
    text = _string(value, path, 64, nonempty=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{path} must include a timezone")
    return text


def _validate_json_budget(root: Any) -> None:
    """Apply one bounded recursive budget before hashing or persistence."""
    stack: list[tuple[Any, int, str]] = [(root, 0, "$")]
    nodes = 0
    text_chars = 0
    while stack:
        value, depth, path = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError(
                f"checkpoint JSON exceeds the {MAX_JSON_NODES:,}-node limit")
        if depth > MAX_JSON_DEPTH:
            raise ValueError(
                f"{path} exceeds the maximum JSON depth of {MAX_JSON_DEPTH}")
        if isinstance(value, dict):
            if len(value) > MAX_COLLECTION_ITEMS:
                raise ValueError(f"{path} contains too many fields")
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise ValueError(f"{path} contains an invalid field name")
                stack.append((item, depth + 1, f"{path}.{key}"))
        elif isinstance(value, list):
            if len(value) > MAX_COLLECTION_ITEMS:
                raise ValueError(f"{path} contains too many items")
            for index, item in enumerate(value):
                stack.append((item, depth + 1, f"{path}[{index}]"))
        elif isinstance(value, str):
            limit = (
                MAX_MMD_CHARS
                if path == "$.payload.job.mmd_text"
                else MAX_NESTED_STRING_CHARS
            )
            if len(value) > limit:
                raise ValueError(
                    f"{path} exceeds the {limit:,}-character limit")
            text_chars += len(value)
            if text_chars > MAX_TOTAL_STRING_CHARS:
                raise ValueError("checkpoint JSON exceeds its text-size limit")
        elif value is None or isinstance(value, bool):
            continue
        elif isinstance(value, int):
            if abs(value) > 10**18:
                raise ValueError(f"{path} contains an out-of-range integer")
        elif isinstance(value, float):
            if not math.isfinite(value) or abs(value) > 10**18:
                raise ValueError(f"{path} contains an out-of-range number")
        else:
            raise ValueError(f"{path} contains an unsupported JSON value")


def _stable(value: str) -> str:
    """Schema-v3 normalization; changing it requires a new bundle schema."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _expected_fingerprint(
    learning_kind: str,
    target: dict[str, str],
    mmd_text: str,
    instruction_set_sha256: str,
) -> str:
    """Mirror of ``build_concepts._generation_checkpoint_fingerprint`` (v3)."""
    material = (
        "concept-generation-checkpoint-v3\0"
        + "\0".join([
            _stable(learning_kind or "post"),
            *(target[field] for field in _TARGET_FIELDS),
            mmd_text,
            str(instruction_set_sha256 or ""),
        ])
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _checkpoint_instruction_set_sha256(checkpoint: dict, path: str) -> str:
    """The instruction identity a checkpoint's fingerprint binds (§8.1).

    An envelope without the stored field validates against the EMPTY
    instruction set over the current frozen core — so legacy (v2) bundles
    fail their fingerprint check and rewind rather than resuming under
    instructions that no longer exist, and even null-assembly fingerprints
    move when a frozen-core prompt changes.
    """
    value = checkpoint.get("instruction_set_sha256", "")
    if not isinstance(value, str):
        raise ValueError(f"{path}.instruction_set_sha256 must be a string")
    if value and not _SHA256_RE.fullmatch(value):
        raise ValueError(
            f"{path}.instruction_set_sha256 must be a lowercase SHA-256")
    if value:
        return value
    from . import instruction_architect

    return instruction_architect.empty_set_sha256()


def _validate_target(
    checkpoint: dict,
    *,
    learning_kind: str,
    mmd_text: str,
    path: str,
) -> None:
    digest = _string(
        checkpoint.get("fingerprint"),
        f"{path}.fingerprint",
        64,
        nonempty=True,
    )
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{path}.fingerprint must be a lowercase SHA-256")
    target = checkpoint.get("target_identity")
    if not isinstance(target, dict):
        raise ValueError(f"{path}.target_identity must be an object")
    _exact_keys(target, set(_TARGET_FIELDS), f"{path}.target_identity")
    for field in _TARGET_FIELDS:
        value = _string(
            target[field], f"{path}.target_identity.{field}", 512)
        if value != _stable(value):
            raise ValueError(
                f"{path}.target_identity.{field} is not normalized")
    if digest != _expected_fingerprint(
        learning_kind,
        target,
        mmd_text,
        _checkpoint_instruction_set_sha256(checkpoint, path),
    ):
        raise ValueError(
            f"{path}.fingerprint does not match the converted source and "
            "target identity"
        )
    if "target_chapter_id" in checkpoint:
        _integer(
            checkpoint["target_chapter_id"],
            f"{path}.target_chapter_id",
            2**63 - 1,
            minimum=1,
        )


def _object_list(value: Any, path: str, maximum: int) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{path} exceeds the {maximum:,}-item limit")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{path} must contain only objects")
    return value


def _validate_placement_certifications(
    value: Any,
    *,
    inventory_items: list[dict],
    path: str,
) -> None:
    """Validate the durable qid-to-host authority without trusting its shape."""
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    _exact_keys(value, {"version", "hosts"}, path)
    version = _integer(
        value["version"],
        f"{path}.version",
        100,
        minimum=1,
    )
    if version != generation._PLACEMENT_CERTIFICATION_VERSION:
        raise ValueError(f"{path}.version is not supported")

    inventory_qids: set[str] = set()
    for index, item in enumerate(inventory_items):
        qid = _string(
            item.get("qid"),
            f"{path} inventory item [{index}].qid",
            128,
            nonempty=True,
        )
        if qid in inventory_qids:
            raise ValueError(
                f"{path} inventory contains duplicate qid {qid!r}")
        inventory_qids.add(qid)

    hosts = value["hosts"]
    if not isinstance(hosts, dict):
        raise ValueError(f"{path}.hosts must be an object")
    if len(hosts) > MAX_COLLECTION_ITEMS:
        raise ValueError(
            f"{path}.hosts exceeds the "
            f"{MAX_COLLECTION_ITEMS:,}-item limit"
        )

    host_qids: set[str] = set()
    host_fields = {
        "topic",
        "topic_key",
        "concept",
        "concept_key",
        "is_culmination",
        "basis",
    }
    for index, (qid_value, entry) in enumerate(hosts.items()):
        qid = _string(
            qid_value,
            f"{path}.hosts key [{index}]",
            128,
            nonempty=True,
        )
        host_qids.add(qid)
        host_path = f"{path}.hosts[{qid!r}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{host_path} must be an object")
        _exact_keys(entry, host_fields, host_path)
        _string(entry["topic"], f"{host_path}.topic", 2_048, nonempty=True)
        _string(
            entry["topic_key"],
            f"{host_path}.topic_key",
            2_048,
            nonempty=True,
        )
        _string(
            entry["concept"],
            f"{host_path}.concept",
            2_048,
            nonempty=True,
        )
        _string(
            entry["concept_key"],
            f"{host_path}.concept_key",
            2_048,
            nonempty=True,
        )
        if not isinstance(entry["is_culmination"], bool):
            raise ValueError(
                f"{host_path}.is_culmination must be a boolean")
        _string(
            entry["basis"],
            f"{host_path}.basis",
            256,
            nonempty=True,
        )
        if not generation._placement_certification_entry_is_valid(entry):
            raise ValueError(
                f"{host_path} does not contain a normalized host identity")

    if host_qids != inventory_qids:
        raise ValueError(
            f"{path}.hosts must exactly cover the inventory qids "
            f"(missing={len(inventory_qids - host_qids)}, "
            f"unknown={len(host_qids - inventory_qids)})"
        )


def _validate_checkpoint_entry(entry: Any, path: str) -> None:
    """Validate fields consumed when a stage is selected for resumption."""
    if not isinstance(entry, dict):
        raise ValueError(f"{path} must be an object")
    schema = _integer(
        entry.get("schema_version"),
        f"{path}.schema_version",
        100,
        minimum=1,
    )
    stage = _string(
        entry.get("stage"), f"{path}.stage", 128, nonempty=True)
    if "stage_schema_version" in entry:
        _integer(
            entry["stage_schema_version"],
            f"{path}.stage_schema_version",
            100,
            minimum=1,
        )
    if "stage_order" in entry:
        _integer(entry["stage_order"], f"{path}.stage_order", 10_000)
    if "saved_at" in entry:
        _timestamp(entry["saved_at"], f"{path}.saved_at")
    if "progress" in entry:
        _number(entry["progress"], f"{path}.progress", 1.0)
    if "stage_label" in entry:
        _string(entry["stage_label"], f"{path}.stage_label", 512)

    for field in ("records", "base_records"):
        if field in entry:
            _object_list(
                entry[field], f"{path}.{field}", MAX_CHECKPOINT_RECORDS)
    for field in ("method_row_snapshot", "skeleton_method_row_snapshot"):
        if field in entry:
            snapshots = _object_list(
                entry[field], f"{path}.{field}", MAX_CHECKPOINT_RECORDS)
            for index, snapshot in enumerate(snapshots):
                if "row" in snapshot and not isinstance(snapshot["row"], dict):
                    raise ValueError(
                        f"{path}.{field}[{index}].row must be an object")
    for field in (
        "question_task_inventory", "mined_types",
        "pre_draft", "pre_audited", "source_review_graph",
    ):
        if field in entry and not isinstance(entry[field], dict):
            raise ValueError(f"{path}.{field} must be an object")
    if "source_review_context_hash" in entry:
        context_hash = _string(
            entry["source_review_context_hash"],
            f"{path}.source_review_context_hash",
            64,
            nonempty=True,
        )
        if not _SHA256_RE.fullmatch(context_hash):
            raise ValueError(
                f"{path}.source_review_context_hash must be a lowercase "
                "SHA-256")
    for field in (
        "source_review_resolution_applied",
        "source_review_metadata_sanitization_applied",
    ):
        if field in entry and not isinstance(entry[field], bool):
            raise ValueError(f"{path}.{field} must be a boolean")
    certification_key = generation._PLACEMENT_CERTIFICATIONS_KEY
    mined_types = entry.get("mined_types")
    if (
        isinstance(mined_types, dict)
        and certification_key in mined_types
    ):
        inventory = entry.get("question_task_inventory")
        if not isinstance(inventory, dict):
            raise ValueError(
                f"{path}.question_task_inventory must accompany "
                f"{path}.mined_types.{certification_key}"
            )
        inventory_items = _object_list(
            inventory.get("items", []),
            f"{path}.question_task_inventory.items",
            MAX_COLLECTION_ITEMS,
        )
        _validate_placement_certifications(
            mined_types[certification_key],
            inventory_items=inventory_items,
            path=f"{path}.mined_types.{certification_key}",
        )
    if "completed_chunks" in entry:
        chunks = _object_list(
            entry["completed_chunks"], f"{path}.completed_chunks", 1_000)
        for index, chunk in enumerate(chunks):
            chunk_path = f"{path}.completed_chunks[{index}]"
            if "records" in chunk:
                _object_list(
                    chunk["records"],
                    f"{chunk_path}.records",
                    MAX_CHECKPOINT_RECORDS,
                )

    known = (
        stage in generation._CONCEPT_CHECKPOINT_STAGES
        or (
            schema == generation._LEGACY_CONCEPT_CHECKPOINT_SCHEMA
            and stage == generation._CONCEPT_CHECKPOINT_STAGE
        )
    )
    if known and not generation._compatible_concept_checkpoint_entry(
        entry,
        # Portable v6 post-Type / v7 terminal entries remain valid migration
        # candidates.  Accepting them here does not make them ordinary resume
        # authority: the production compatibility mirror and
        # ``concepts_from_mmd`` retain them only through the explicit legacy
        # Pre-sidecar migration path. Current v7/v8 entries still require the
        # complete in-checkpoint Pre bundle.
        allow_legacy_pre_release=True,
    ):
        raise ValueError(f"{path} is not a compatible checkpoint stage")


def _validate_human_decisions(
    value: Any,
    *,
    checkpoint: dict,
    path: str,
) -> None:
    try:
        ledger = schemas.HumanDecisionLedger.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"{path} is invalid: {exc.errors()[0]['msg']}") from exc
    context = ledger.context
    if context.fingerprint != checkpoint.get("fingerprint"):
        raise ValueError(
            f"{path}.context.fingerprint does not match the checkpoint")
    if (
        checkpoint.get("target_chapter_id") is not None
        and context.target_chapter_id != checkpoint.get("target_chapter_id")
    ):
        raise ValueError(
            f"{path}.context.target_chapter_id does not match the checkpoint")
    resolved_ids: set[str] = set()
    pending_rows = [ledger.pending] if ledger.pending is not None else []
    for index, pending in enumerate([
        *pending_rows,
        *(row.pending_decision for row in ledger.resolutions),
    ]):
        pending_path = (
            f"{path}.pending" if index == 0 and ledger.pending is not None
            else f"{path}.resolutions.pending_decision[{index}]"
        )
        if (
            not _DECISION_ID_RE.fullmatch(pending.decision_id)
            or not _SHA256_RE.fullmatch(pending.context_hash)
        ):
            raise ValueError(
                f"{pending_path} has an invalid decision identity")
        _validate_usage(
            pending.cumulative_usage,
            f"{pending_path}.cumulative_usage",
        )
        review = pending.agent_review
        if review is not None:
            if not _SHA256_RE.fullmatch(review.issue_key):
                raise ValueError(
                    f"{pending_path}.agent_review.issue_key must be a "
                    "lowercase SHA-256"
                )
            for field, digest in (
                ("capability_key", review.capability_key),
                ("workspace_hash", review.workspace_hash),
            ):
                if digest and not _SHA256_RE.fullmatch(digest):
                    raise ValueError(
                        f"{pending_path}.agent_review.{field} must be a "
                        "lowercase SHA-256 when present"
                    )
            _timestamp(
                review.started_at,
                f"{pending_path}.agent_review.started_at",
            )
            if review.status == "request_started":
                if review.completed_at:
                    raise ValueError(
                        f"{pending_path}.agent_review.completed_at must be "
                        "empty while request_started"
                    )
            else:
                if not review.completed_at:
                    raise ValueError(
                        f"{pending_path}.agent_review.completed_at must not "
                        "be empty after review"
                    )
                _timestamp(
                    review.completed_at,
                    f"{pending_path}.agent_review.completed_at",
                )
            if review.status == "resolved" and review.choice is None:
                raise ValueError(
                    f"{pending_path}.agent_review.choice must not be empty "
                    "for a resolved review"
                )
            if review.status == "resolved":
                if review.choice not in _AGENT_AUTOMATABLE_CHOICES:
                    raise ValueError(
                        f"{pending_path}.agent_review.choice is not approved "
                        "for autonomous execution"
                    )
                if review.instruction.strip():
                    raise ValueError(
                        f"{pending_path}.agent_review.instruction must be "
                        "empty for an autonomous review"
                    )
        candidate_ids = {
            row.concept_id for row in pending.candidates if row.concept_id
        }
        candidate_target_ids = {
            row.target_id for row in pending.candidates if row.target_id
        }
        if len(candidate_ids) != sum(
            bool(row.concept_id) for row in pending.candidates
        ):
            raise ValueError(
                f"{pending_path}.candidates contains duplicate concept IDs")
        if len(candidate_target_ids) != sum(
            bool(row.target_id) for row in pending.candidates
        ):
            raise ValueError(
                f"{pending_path}.candidates contains duplicate target IDs")
        choices = [row.choice for row in pending.options]
        if len(choices) != len(set(choices)):
            raise ValueError(
                f"{pending_path}.options contains duplicate choices")
        for option in pending.options:
            if (
                option.target_concept_id
                and candidate_ids
                and option.target_concept_id not in candidate_ids
            ):
                raise ValueError(
                    f"{pending_path}.options targets a non-candidate concept")
            if (
                option.target_id
                and candidate_target_ids
                and option.target_id not in candidate_target_ids
            ):
                raise ValueError(
                    f"{pending_path}.options targets a non-candidate item")

    for index, review in enumerate(ledger.agent_review_history):
        review_path = f"{path}.agent_review_history[{index}]"
        if not _SHA256_RE.fullmatch(review.issue_key):
            raise ValueError(
                f"{review_path}.issue_key must be a lowercase SHA-256"
            )
        for field, digest in (
            ("capability_key", review.capability_key),
            ("workspace_hash", review.workspace_hash),
        ):
            if digest and not _SHA256_RE.fullmatch(digest):
                raise ValueError(
                    f"{review_path}.{field} must be a lowercase SHA-256 "
                    "when present"
                )
        _timestamp(review.started_at, f"{review_path}.started_at")
        if review.status == "request_started":
            if review.completed_at:
                raise ValueError(
                    f"{review_path}.completed_at must be empty while "
                    "request_started"
                )
        elif not review.completed_at:
            raise ValueError(
                f"{review_path}.completed_at must not be empty after review"
            )
        else:
            _timestamp(review.completed_at, f"{review_path}.completed_at")
        if review.status == "resolved":
            if review.choice is None:
                raise ValueError(
                    f"{review_path}.choice must not be empty for a "
                    "resolved review"
                )
            if review.choice not in _AGENT_AUTOMATABLE_CHOICES:
                raise ValueError(
                    f"{review_path}.choice is not approved for autonomous "
                    "execution"
                )
            if review.instruction.strip():
                raise ValueError(
                    f"{review_path}.instruction must be empty for an "
                    "autonomous review"
                )

    for index, resolution in enumerate(ledger.resolutions):
        resolution_path = f"{path}.resolutions[{index}]"
        if resolution.decision_id in resolved_ids:
            raise ValueError(
                f"{path}.resolutions contains a duplicate decision_id")
        resolved_ids.add(resolution.decision_id)
        if (
            resolution.choice == "custom_instruction"
            and not resolution.instruction.strip()
        ):
            raise ValueError(
                f"{resolution_path}.instruction must not be empty")
        if (
            resolution.equivalence_key
            and not _SHA256_RE.fullmatch(resolution.equivalence_key)
        ):
            raise ValueError(
                f"{resolution_path}.equivalence_key must be a lowercase "
                "SHA-256 when present"
            )
        _timestamp(resolution.resolved_at, f"{resolution_path}.resolved_at")
        if resolution.status == "consumed":
            if not resolution.consumed_at:
                raise ValueError(
                    f"{resolution_path}.consumed_at must not be empty")
            _timestamp(
                resolution.consumed_at,
                f"{resolution_path}.consumed_at",
            )
        elif resolution.consumed_at:
            raise ValueError(
                f"{resolution_path}.consumed_at requires consumed status")
        repair_signature = resolution.superseded_by_repair_signature
        if resolution.status == "superseded":
            if not _SHA256_RE.fullmatch(repair_signature):
                raise ValueError(
                    f"{resolution_path}.superseded_by_repair_signature must "
                    "be a lowercase SHA-256"
                )
        elif repair_signature:
            raise ValueError(
                f"{resolution_path}.superseded_by_repair_signature requires "
                "superseded status"
            )
        original = resolution.pending_decision
        if (
            original.decision_id != resolution.decision_id
            or original.context_hash != resolution.context_hash
        ):
            raise ValueError(
                f"{resolution_path} does not match its pending decision")
        if resolution.resolved_by == "agent":
            review = original.agent_review
            if review is None or review.status != "resolved":
                raise ValueError(
                    f"{resolution_path} is agent-resolved without a "
                    "validated agent review"
                )
            if resolution.status not in {"consumed", "superseded"}:
                raise ValueError(
                    f"{resolution_path} agent resolution must be durably "
                    "sealed as consumed or superseded"
                )
            if resolution.choice not in _AGENT_AUTOMATABLE_CHOICES:
                raise ValueError(
                    f"{resolution_path}.choice is not approved for "
                    "autonomous execution"
                )
            if resolution.instruction.strip():
                raise ValueError(
                    f"{resolution_path}.instruction must be empty for an "
                    "agent resolution"
                )
            if (
                review.choice != resolution.choice
                or review.instruction != resolution.instruction
                or review.target_id != resolution.target_id
                or review.target_concept_id
                != resolution.target_concept_id
            ):
                raise ValueError(
                    f"{resolution_path} does not match its validated agent "
                    "review directive"
                )
        candidate_ids = {
            row.concept_id for row in original.candidates if row.concept_id
        }
        candidate_target_ids = {
            row.target_id for row in original.candidates if row.target_id
        }
        if resolution.choice in {"expand_existing", "select_existing"}:
            if not resolution.target_concept_id:
                raise ValueError(
                    f"{resolution_path}.target_concept_id must not be empty")
            if (
                candidate_ids
                and resolution.target_concept_id not in candidate_ids
            ):
                raise ValueError(
                    f"{resolution_path}.target_concept_id is not a candidate")
        if resolution.choice in {
            "accept_recommended", "select_candidate",
        }:
            if not resolution.target_id:
                raise ValueError(
                    f"{resolution_path}.target_id must not be empty")
            if (
                not candidate_target_ids
                or resolution.target_id not in candidate_target_ids
            ):
                raise ValueError(
                    f"{resolution_path}.target_id is not a candidate")
    if (
        ledger.pending is not None
        and ledger.pending.decision_id in resolved_ids
    ):
        raise ValueError(
            f"{path}.pending has already been resolved")


def _validate_semantic_recovery_dispatches(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    _exact_keys(value, {"version", "attempts"}, path)
    version = _integer(value["version"], f"{path}.version", 100, minimum=1)
    if version != 1:
        raise ValueError(f"{path}.version is not supported")
    attempts = _object_list(value["attempts"], f"{path}.attempts", 100)
    issue_keys: set[str] = set()
    required_fields = {
        "issue_key", "failure_signature", "status", "started_at",
        "completed_at", "failure_type", "stage",
    }
    fields = {
        *required_fields,
        "candidate_payload_hash",
        "verified_at",
    }
    for index, row in enumerate(attempts):
        row_path = f"{path}.attempts[{index}]"
        _exact_keys(row, fields, row_path, required=required_fields)
        issue_key = _string(
            row["issue_key"], f"{row_path}.issue_key", 64, nonempty=True
        )
        signature = _string(
            row["failure_signature"],
            f"{row_path}.failure_signature",
            64,
            nonempty=True,
        )
        if not _SHA256_RE.fullmatch(issue_key):
            raise ValueError(f"{row_path}.issue_key must be a lowercase SHA-256")
        if not _SHA256_RE.fullmatch(signature):
            raise ValueError(
                f"{row_path}.failure_signature must be a lowercase SHA-256"
            )
        if issue_key in issue_keys:
            raise ValueError(f"{path}.attempts contains a duplicate issue_key")
        issue_keys.add(issue_key)
        status = _string(
            row["status"], f"{row_path}.status", 32, nonempty=True
        )
        if status not in {"request_started", "applied", "succeeded"}:
            raise ValueError(f"{row_path}.status is not supported")
        _timestamp(row["started_at"], f"{row_path}.started_at")
        if status in {"applied", "succeeded"}:
            _timestamp(row["completed_at"], f"{row_path}.completed_at")
            candidate_hash = _string(
                row.get("candidate_payload_hash"),
                f"{row_path}.candidate_payload_hash",
                64,
                nonempty=True,
            )
            if not _SHA256_RE.fullmatch(candidate_hash):
                raise ValueError(
                    f"{row_path}.candidate_payload_hash must be a "
                    "lowercase SHA-256"
                )
        elif row["completed_at"]:
            raise ValueError(
                f"{row_path}.completed_at requires applied or succeeded status"
            )
        elif row.get("candidate_payload_hash"):
            raise ValueError(
                f"{row_path}.candidate_payload_hash requires applied status"
            )
        if status == "succeeded":
            _timestamp(row.get("verified_at"), f"{row_path}.verified_at")
        elif row.get("verified_at"):
            raise ValueError(
                f"{row_path}.verified_at requires succeeded status"
            )
        _string(
            row["failure_type"],
            f"{row_path}.failure_type",
            256,
            nonempty=True,
        )
        _string(row["stage"], f"{row_path}.stage", 128)


def _validate_phase38_issue_bucket(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    fields = {
        "candidate_history", "attempts", "signatures", "feedback",
        "final_verification_pending", "status", "terminal_reason",
    }
    _exact_keys(value, fields, path)
    history = value["candidate_history"]
    if not isinstance(history, list) or len(history) > 24:
        raise ValueError(
            f"{path}.candidate_history must contain at most 24 hashes"
        )
    if len(history) != len(set(history)):
        raise ValueError(f"{path}.candidate_history contains duplicates")
    for index, digest in enumerate(history):
        digest = _string(
            digest, f"{path}.candidate_history[{index}]", 64,
            nonempty=True,
        )
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(
                f"{path}.candidate_history[{index}] must be a lowercase "
                "SHA-256"
            )
    _integer(value["attempts"], f"{path}.attempts", 1_000, minimum=0)
    signatures = value["signatures"]
    if not isinstance(signatures, dict) or len(signatures) > 100:
        raise ValueError(f"{path}.signatures must contain at most 100 entries")
    for signature, count in signatures.items():
        if not isinstance(signature, str) or not _SHA256_RE.fullmatch(signature):
            raise ValueError(
                f"{path}.signatures keys must be lowercase SHA-256 values"
            )
        _integer(count, f"{path}.signatures.{signature}", 1_000, minimum=1)
    feedback = value["feedback"]
    if not isinstance(feedback, dict) or len(feedback) > 5_000:
        raise ValueError(f"{path}.feedback must contain at most 5,000 entries")
    for concept_id, instruction in feedback.items():
        _string(str(concept_id), f"{path}.feedback key", 128, nonempty=True)
        _string(instruction, f"{path}.feedback.{concept_id}", 16_000)
    if not isinstance(value["final_verification_pending"], bool):
        raise ValueError(f"{path}.final_verification_pending must be boolean")
    status = _string(value["status"], f"{path}.status", 64, nonempty=True)
    if status not in _PHASE38_STATUSES:
        raise ValueError(f"{path}.status is not supported")
    terminal_reason = _string(
        value["terminal_reason"], f"{path}.terminal_reason", 8_000
    )
    if status == "final_verification_pending" and not value[
        "final_verification_pending"
    ]:
        raise ValueError(
            f"{path}.final_verification_pending must be true for its status"
        )
    if status in _PHASE38_TERMINAL_STATUSES and not terminal_reason.strip():
        raise ValueError(
            f"{path}.terminal_reason is required when {status}")


def _validate_phase38_convergence(value: Any, path: str) -> None:
    """Validate the bounded, JSON-only per-job Phase 3.8 ledger."""

    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    legacy_fields = {
        "version", "contract", "scope", "source_contract_hash",
        "base_candidate_sha256", "candidate_sha256", "candidate_history",
        "attempts", "signatures", "suppressed_resolution_ids", "feedback",
        "final_verification_pending", "status", "terminal_reason",
    }
    extension_fields = {
        "issue_buckets", "active_issue_key", "legacy_unscoped_issue",
        "dispatch_status", "dispatch_sequence",
        "dispatch_candidate_sha256", "dispatch_issue_key",
        "dispatch_decision_id", "dispatch_decision_context_hash",
    }
    # ``disposition`` names the deterministic terminal rewrite that produced
    # the final candidate. It is independent of the all-or-nothing extension
    # set above, so a ledger written before it existed still validates.
    _exact_keys(
        value,
        legacy_fields | extension_fields | {"disposition"},
        path,
        required=legacy_fields,
    )
    if _integer(value["version"], f"{path}.version", 100, minimum=1) != 1:
        raise ValueError(f"{path}.version is not supported")
    _string(value["contract"], f"{path}.contract", 128, nonempty=True)
    _string(value["scope"], f"{path}.scope", 512, nonempty=True)
    for field in (
        "source_contract_hash",
        "base_candidate_sha256",
        "candidate_sha256",
    ):
        digest = _string(value[field], f"{path}.{field}", 64)
        if digest and not _SHA256_RE.fullmatch(digest):
            raise ValueError(
                f"{path}.{field} must be a lowercase SHA-256 when present"
            )
    history = value["candidate_history"]
    if not isinstance(history, list) or len(history) > 24:
        raise ValueError(
            f"{path}.candidate_history must contain at most 24 hashes"
        )
    if len(history) != len(set(history)):
        raise ValueError(f"{path}.candidate_history contains duplicates")
    for index, digest in enumerate(history):
        digest = _string(
            digest, f"{path}.candidate_history[{index}]", 64,
            nonempty=True,
        )
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(
                f"{path}.candidate_history[{index}] must be a lowercase "
                "SHA-256"
            )
    _integer(value["attempts"], f"{path}.attempts", 1_000, minimum=0)
    signatures = value["signatures"]
    if not isinstance(signatures, dict) or len(signatures) > 100:
        raise ValueError(f"{path}.signatures must contain at most 100 entries")
    for signature, count in signatures.items():
        if not isinstance(signature, str) or not _SHA256_RE.fullmatch(signature):
            raise ValueError(
                f"{path}.signatures keys must be lowercase SHA-256 values"
            )
        _integer(count, f"{path}.signatures.{signature}", 1_000, minimum=1)
    suppressed = value["suppressed_resolution_ids"]
    if not isinstance(suppressed, list) or len(suppressed) > 5_000:
        raise ValueError(
            f"{path}.suppressed_resolution_ids must contain at most 5,000 IDs"
        )
    for index, decision_id in enumerate(suppressed):
        decision_id = _string(
            decision_id,
            f"{path}.suppressed_resolution_ids[{index}]",
            128,
            nonempty=True,
        )
        if not _DECISION_ID_RE.fullmatch(decision_id):
            raise ValueError(
                f"{path}.suppressed_resolution_ids[{index}] is invalid"
            )
    feedback = value["feedback"]
    if not isinstance(feedback, dict) or len(feedback) > 5_000:
        raise ValueError(f"{path}.feedback must contain at most 5,000 entries")
    for concept_id, instruction in feedback.items():
        _string(str(concept_id), f"{path}.feedback key", 128, nonempty=True)
        _string(instruction, f"{path}.feedback.{concept_id}", 16_000)
    if not isinstance(value["final_verification_pending"], bool):
        raise ValueError(f"{path}.final_verification_pending must be boolean")
    status = _string(value["status"], f"{path}.status", 64, nonempty=True)
    if status not in _PHASE38_STATUSES:
        raise ValueError(f"{path}.status is not supported")
    terminal_reason = _string(
        value["terminal_reason"], f"{path}.terminal_reason", 8_000
    )
    if status == "final_verification_pending" and not value[
        "final_verification_pending"
    ]:
        raise ValueError(
            f"{path}.final_verification_pending must be true for its status"
        )
    if status in _PHASE38_TERMINAL_STATUSES and not terminal_reason.strip():
        raise ValueError(
            f"{path}.terminal_reason is required when {status}")
    if "disposition" in value:
        _string(value["disposition"], f"{path}.disposition", 128)

    present_extensions = extension_fields & set(value)
    if not present_extensions:
        return
    if present_extensions != extension_fields:
        missing = ", ".join(sorted(extension_fields - present_extensions))
        raise ValueError(
            f"{path} is missing Phase 3.8 extension field(s): {missing}"
        )
    buckets = value["issue_buckets"]
    if not isinstance(buckets, dict) or len(buckets) > 100:
        raise ValueError(f"{path}.issue_buckets must contain at most 100 issues")
    for issue_key, bucket in buckets.items():
        if not isinstance(issue_key, str) or not _SHA256_RE.fullmatch(issue_key):
            raise ValueError(
                f"{path}.issue_buckets keys must be lowercase SHA-256 values"
            )
        _validate_phase38_issue_bucket(
            bucket, f"{path}.issue_buckets.{issue_key}"
        )
    active_issue_key = _string(
        value.get("active_issue_key"),
        f"{path}.active_issue_key",
        64,
    )
    if active_issue_key and active_issue_key not in buckets:
        raise ValueError(
            f"{path}.active_issue_key does not identify an issue bucket"
        )
    if buckets and not active_issue_key:
        raise ValueError(f"{path}.active_issue_key is required for issue buckets")
    legacy_unscoped = value.get("legacy_unscoped_issue")
    if not isinstance(legacy_unscoped, bool):
        raise ValueError(f"{path}.legacy_unscoped_issue must be boolean")
    if legacy_unscoped and (buckets or active_issue_key):
        raise ValueError(
            f"{path}.legacy_unscoped_issue cannot coexist with issue buckets"
        )
    if active_issue_key:
        active = buckets[active_issue_key]
        for field in (
            "candidate_history", "attempts", "signatures", "feedback",
            "final_verification_pending", "status", "terminal_reason",
        ):
            if value[field] != active[field]:
                raise ValueError(
                    f"{path}.{field} must mirror the active issue bucket"
                )

    dispatch_status = _string(
        value.get("dispatch_status"),
        f"{path}.dispatch_status",
        32,
        nonempty=True,
    )
    if dispatch_status not in {
        "idle", "request_started", "decision_returned"
    }:
        raise ValueError(f"{path}.dispatch_status is not supported")
    dispatch_sequence = _integer(
        value.get("dispatch_sequence"),
        f"{path}.dispatch_sequence",
        1_000_000,
        minimum=0,
    )
    dispatch_candidate = _string(
        value.get("dispatch_candidate_sha256"),
        f"{path}.dispatch_candidate_sha256",
        64,
    )
    dispatch_issue_key = _string(
        value.get("dispatch_issue_key"),
        f"{path}.dispatch_issue_key",
        64,
    )
    dispatch_decision_id = _string(
        value.get("dispatch_decision_id"),
        f"{path}.dispatch_decision_id",
        128,
    )
    dispatch_decision_context_hash = _string(
        value.get("dispatch_decision_context_hash"),
        f"{path}.dispatch_decision_context_hash",
        64,
    )
    if dispatch_candidate and not _SHA256_RE.fullmatch(dispatch_candidate):
        raise ValueError(
            f"{path}.dispatch_candidate_sha256 must be a lowercase SHA-256"
        )
    if dispatch_issue_key and not _SHA256_RE.fullmatch(dispatch_issue_key):
        raise ValueError(
            f"{path}.dispatch_issue_key must be a lowercase SHA-256"
        )
    if dispatch_status in {"request_started", "decision_returned"}:
        if dispatch_sequence < 1 or not dispatch_candidate:
            raise ValueError(
                f"{path}.{dispatch_status} requires a sequence and candidate "
                "hash"
            )
        if dispatch_issue_key and dispatch_issue_key != active_issue_key:
            raise ValueError(
                f"{path}.dispatch_issue_key must match the active issue"
            )
    if dispatch_status == "decision_returned":
        if not _DECISION_ID_RE.fullmatch(dispatch_decision_id):
            raise ValueError(
                f"{path}.dispatch_decision_id is invalid"
            )
        if not _SHA256_RE.fullmatch(dispatch_decision_context_hash):
            raise ValueError(
                f"{path}.dispatch_decision_context_hash must be a lowercase "
                "SHA-256"
            )
    elif dispatch_decision_id or dispatch_decision_context_hash:
        raise ValueError(
            f"{path}.{dispatch_status} cannot retain decision identity"
        )
    if dispatch_status == "idle" and (
        dispatch_candidate or dispatch_issue_key
    ):
        raise ValueError(
            f"{path}.idle dispatch cannot retain candidate or issue identity"
        )


def _validate_checkpoint(
    value: Any,
    *,
    learning_kind: str,
    mmd_text: str,
    path: str,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if not value:
        return

    is_envelope = (
        value.get("checkpoint_format")
        == generation._CONCEPT_CHECKPOINT_FORMAT
    )
    control_only = value.get("phase38_control_only") is True
    if "phase38_control_only" in value and not control_only:
        raise ValueError(f"{path}.phase38_control_only must be true")
    if "checkpoint_format" in value and not is_envelope:
        raise ValueError(f"{path}.checkpoint_format is not supported")
    if is_envelope:
        if value.get("schema_version") != generation._CONCEPT_CHECKPOINT_SCHEMA:
            raise ValueError(f"{path}.schema_version is not compatible")
        history = _object_list(
            value.get("checkpoints"),
            f"{path}.checkpoints",
            MAX_CHECKPOINT_STAGES,
        )
        if not history and not control_only:
            raise ValueError(f"{path}.checkpoints must not be empty")
        if control_only and history:
            raise ValueError(
                f"{path}.phase38_control_only cannot contain saved stages"
            )
        if control_only and "phase38_convergence" not in value:
            raise ValueError(
                f"{path}.phase38_control_only requires its convergence ledger"
            )
        stages: set[str] = set()
        for index, entry in enumerate(history):
            _validate_checkpoint_entry(
                entry, f"{path}.checkpoints[{index}]")
            stage = entry["stage"]
            if stage in stages:
                raise ValueError(
                    f"{path}.checkpoints contains duplicate stage {stage!r}")
            stages.add(stage)
        if not control_only:
            active_stage = _string(
                value.get("stage"), f"{path}.stage", 128, nonempty=True)
            if active_stage not in stages:
                raise ValueError(
                    f"{path}.stage does not identify a saved history entry")
    else:
        # A bare stage without target metadata can never match a resumed run.
        if value.get("schema_version") != generation._CONCEPT_CHECKPOINT_SCHEMA:
            raise ValueError(
                f"{path} must be a schema-v3 checkpoint envelope or "
                "direct entry"
            )
        _validate_checkpoint_entry(value, path)

    _validate_target(
        value,
        learning_kind=learning_kind,
        mmd_text=mmd_text,
        path=path,
    )
    if "human_decisions" in value:
        _validate_human_decisions(
            value["human_decisions"],
            checkpoint=value,
            path=f"{path}.human_decisions",
        )
    if "semantic_recovery_dispatches" in value:
        _validate_semantic_recovery_dispatches(
            value["semantic_recovery_dispatches"],
            f"{path}.semantic_recovery_dispatches",
        )
    if "phase38_convergence" in value:
        _validate_phase38_convergence(
            value["phase38_convergence"],
            f"{path}.phase38_convergence",
        )
    if control_only:
        return
    if generation._newest_compatible_concept_checkpoint(
        value,
        allow_legacy_pre_release=True,
    ) is None:
        raise ValueError(
            f"{path} does not contain a compatible completed stage")


def _validate_inventory(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    certification_key = generation._PLACEMENT_CERTIFICATIONS_KEY
    final_grounding_key = grounding_certificate.FINAL_CERTIFICATE_FIELD
    deposited_grounding_key = "deposited_grounding_certificate"
    type_case_ledger_key = generation._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY
    _exact_keys(
        value,
        {
            "items",
            "stats",
            "mined_types",
            certification_key,
            type_case_ledger_key,
            final_grounding_key,
            deposited_grounding_key,
        },
        path,
        required=set(),
    )
    items = _object_list(
        value.get("items", []), f"{path}.items", MAX_COLLECTION_ITEMS)
    stats = value.get("stats", {})
    if not isinstance(stats, dict) or len(stats) > 256:
        raise ValueError(f"{path}.stats must be an object of at most 256 fields")
    for key, count in stats.items():
        _string(key, f"{path}.stats key", 128)
        _integer(count, f"{path}.stats.{key}", MAX_COLLECTION_ITEMS)
    _object_list(
        value.get("mined_types", []),
        f"{path}.mined_types",
        MAX_COLLECTION_ITEMS,
    )
    if certification_key in value:
        _validate_placement_certifications(
            value[certification_key],
            inventory_items=items,
            path=f"{path}.{certification_key}",
        )
    validated_type_case_ledger = None
    if type_case_ledger_key in value:
        validated_type_case_ledger = (
            generation._valid_type_case_qid_placement_ledger(
                value[type_case_ledger_key]
            )
        )
        if validated_type_case_ledger is None:
            raise ValueError(
                f"{path}.{type_case_ledger_key} is not a certified, "
                "self-addressed Type/Case QID placement ledger"
            )
    verified_certificates: dict[str, dict[str, Any]] = {}
    for certificate_key in (
        final_grounding_key,
        deposited_grounding_key,
    ):
        if certificate_key not in value:
            continue
        try:
            verified_certificates[certificate_key] = (
                grounding_certificate.verify_certificate_envelope(
                    value[certificate_key],
                    type_case_qid_placement_ledger=(
                        validated_type_case_ledger
                    ),
                )
            )
        except grounding_certificate.GroundingCertificateError as exc:
            raise ValueError(f"{path}.{certificate_key} is invalid: {exc}") from exc
    if len(verified_certificates) == 2:
        incoming = verified_certificates[final_grounding_key]
        deposited = verified_certificates[deposited_grounding_key]
        if incoming != deposited:
            raise ValueError(
                f"{path}.{deposited_grounding_key}.certificate_sha256 does "
                "not match the exact generation-time payload, grounding, "
                "placement, and Type/Case QID-host certificate"
            )
    del items  # shape check only


def _validate_usage_row(
    value: Any,
    path: str,
    *,
    model_row: bool,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    allowed = _USAGE_MODEL_KEYS if model_row else _USAGE_TOP_KEYS
    _exact_keys(
        value,
        allowed,
        path,
        # ``cache_write_tokens`` was added without changing the v1 bundle
        # schema. Older exports remain valid and are interpreted as zero
        # cache-write tokens by the usage merger.
        required=(
            allowed - {"cache_write_tokens"}
            if model_row
            else set()
        ),
    )
    for field, maximum in _USAGE_INTS.items():
        if field in value:
            _integer(value[field], f"{path}.{field}", maximum)
    if "model" in value:
        _string(
            value["model"],
            f"{path}.model",
            256,
            nonempty=model_row,
        )
    if "estimated_cost_usd" in value and value["estimated_cost_usd"] is not None:
        _number(
            value["estimated_cost_usd"],
            f"{path}.estimated_cost_usd",
            MAX_ESTIMATED_COST_USD,
        )
    if "pricing_complete" in value and not isinstance(
        value["pricing_complete"], bool
    ):
        raise ValueError(f"{path}.pricing_complete must be a boolean")
    if "pricing_source" in value:
        _string(value["pricing_source"], f"{path}.pricing_source", 2_048)
    if not model_row:
        if "currency" in value and value["currency"] != "USD":
            raise ValueError(f"{path}.currency must be USD")
        if "pricing_as_of" in value:
            _string(value["pricing_as_of"], f"{path}.pricing_as_of", 64)

    cached = value.get("cached_input_tokens")
    cache_write = value.get("cache_write_tokens")
    input_tokens = value.get("input_tokens")
    if cached is not None and input_tokens is not None and cached > input_tokens:
        raise ValueError(f"{path}.cached_input_tokens exceeds input_tokens")
    if (
        cached is not None
        and cache_write is not None
        and input_tokens is not None
        and cached + cache_write > input_tokens
    ):
        raise ValueError(
            f"{path}.cached_input_tokens + cache_write_tokens exceeds "
            "input_tokens"
        )
    reasoning = value.get("reasoning_tokens")
    output = value.get("output_tokens")
    if reasoning is not None and output is not None and reasoning > output:
        raise ValueError(f"{path}.reasoning_tokens exceeds output_tokens")


def _validate_stage_row(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    _exact_keys(value, _USAGE_STAGE_KEYS, path, required=set())
    for name in ("stage", "lane"):
        if name in value:
            _string(value[name], f"{path}.{name}", 512, nonempty=False)
    for name, maximum in _USAGE_STAGE_INTS.items():
        if name in value:
            _integer(value[name], f"{path}.{name}", maximum)
    if (
        "estimated_cost_usd" in value
        and value["estimated_cost_usd"] is not None
    ):
        _number(
            value["estimated_cost_usd"],
            f"{path}.estimated_cost_usd",
            MAX_ESTIMATED_COST_USD,
        )
    if "pricing_complete" in value and not isinstance(
        value["pricing_complete"], bool
    ):
        raise ValueError(f"{path}.pricing_complete must be a boolean")
    for name in ("first_ts", "last_ts"):
        if name in value:
            _number(value[name], f"{path}.{name}", MAX_EPOCH_TS)
    if "elapsed_seconds" in value:
        _number(
            value["elapsed_seconds"],
            f"{path}.elapsed_seconds",
            MAX_ELAPSED_SECONDS,
        )


def _validate_usage(value: Any, path: str) -> None:
    _validate_usage_row(value, path, model_row=False)
    if "elapsed_seconds" in value:
        _number(
            value["elapsed_seconds"],
            f"{path}.elapsed_seconds",
            MAX_ELAPSED_SECONDS,
        )
    stages = value.get("stages")
    if stages is not None:
        stage_rows = _object_list(stages, f"{path}.stages", 512)
        for index, row in enumerate(stage_rows):
            _validate_stage_row(row, f"{path}.stages[{index}]")
    models = value.get("models")
    if models is None:
        return
    rows = _object_list(models, f"{path}.models", 256)
    for index, row in enumerate(rows):
        _validate_usage_row(
            row, f"{path}.models[{index}]", model_row=True)


def _validate_log(value: Any, path: str) -> None:
    events = _object_list(value, path, MAX_GENERATION_LOG_EVENTS)
    for index, event in enumerate(events):
        event_path = f"{path}[{index}]"
        kind = event.get("type")
        if kind == "log":
            _exact_keys(
                event,
                {"type", "level", "message", "ts", "error"},
                event_path,
                required={"type", "level", "message"},
            )
            level = _string(
                event["level"], f"{event_path}.level", 16, nonempty=True)
            if level not in {
                "info", "success", "warn", "warning", "error", "debug",
            }:
                raise ValueError(f"{event_path}.level is not supported")
            _string(
                event["message"],
                f"{event_path}.message",
                MAX_LOG_MESSAGE_CHARS,
            )
            if "error" in event:
                details = event["error"]
                if not isinstance(details, dict):
                    raise ValueError(f"{event_path}.error must be an object")
                _exact_keys(
                    details,
                    {"exception_type", "reason", "frames"},
                    f"{event_path}.error",
                )
                _string(
                    details["exception_type"],
                    f"{event_path}.error.exception_type",
                    160,
                    nonempty=True,
                )
                _string(
                    details["reason"],
                    f"{event_path}.error.reason",
                    4_000,
                )
                frames = _object_list(
                    details["frames"], f"{event_path}.error.frames", 8)
                for frame_index, frame in enumerate(frames):
                    frame_path = (
                        f"{event_path}.error.frames[{frame_index}]")
                    _exact_keys(
                        frame,
                        {"file", "line", "function"},
                        frame_path,
                    )
                    _string(frame["file"], f"{frame_path}.file", 1_024)
                    _integer(
                        frame["line"],
                        f"{frame_path}.line",
                        10_000_000,
                        minimum=1,
                    )
                    _string(
                        frame["function"],
                        f"{frame_path}.function",
                        160,
                    )
        elif kind == "step":
            _exact_keys(event, {"type", "label", "ts"}, event_path)
            _string(event["label"], f"{event_path}.label", 2_048)
        elif kind == "progress":
            _exact_keys(
                event, {"type", "value", "label", "ts"}, event_path)
            _number(event["value"], f"{event_path}.value", 1.0)
            _string(event["label"], f"{event_path}.label", 2_048)
        else:
            raise ValueError(f"{event_path}.type is not supported")
        if "ts" in event:
            _number(event["ts"], f"{event_path}.ts", 10**13)


def _validate_job(value: Any, path: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    # ``chapter_duration_minutes`` (contract v2.0 §32.1's explicit upload
    # variable) is optional so bundles written before it existed restore.
    _exact_keys(
        value, _JOB_KEYS | _OPTIONAL_JOB_KEYS, path, required=_JOB_KEYS,
    )
    duration = value.get("chapter_duration_minutes", 0)
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        raise ValueError(
            f"{path}.chapter_duration_minutes must be a non-negative integer"
        )
    if value["module"] != "build_concepts":
        raise ValueError("checkpoint bundle is not for Build Concepts")
    if value["upload_type"] != "document":
        raise ValueError(f"{path}.upload_type must be document")
    kind = _string(
        value["learning_kind"],
        f"{path}.learning_kind",
        16,
        nonempty=True,
    )
    if kind not in {"post", "pre"}:
        raise ValueError("checkpoint bundle has an invalid learning kind")
    _string(value["source_book"], f"{path}.source_book", 128)
    filename = _string(value["filename"], f"{path}.filename", 255)
    if filename and Path(filename).name != filename:
        raise ValueError(f"{path}.filename must not contain a directory path")
    mmd_text = _string(
        value["mmd_text"],
        f"{path}.mmd_text",
        MAX_MMD_CHARS,
        nonempty=True,
    )
    if not mmd_text.strip():
        raise ValueError("checkpoint bundle does not contain converted MMD")
    if value["deposit_scope_type"] not in {"chapter", "topic", "concept"}:
        raise ValueError(f"{path}.deposit_scope_type is invalid")
    scope_ids = value["deposit_scope_ids"]
    if not isinstance(scope_ids, list) or len(scope_ids) > MAX_COLLECTION_ITEMS:
        raise ValueError(f"{path}.deposit_scope_ids must be a bounded array")
    for index, scope_id in enumerate(scope_ids):
        _integer(
            scope_id,
            f"{path}.deposit_scope_ids[{index}]",
            2**63 - 1,
            minimum=1,
        )
    return kind, mmd_text


def _validate_payload(payload: Any) -> tuple[dict, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("checkpoint bundle payload is missing")
    _exact_keys(payload, _PAYLOAD_KEYS, "payload")
    job, kind_and_text = payload["job"], _validate_job(
        payload["job"], "payload.job")
    kind, mmd_text = kind_and_text
    _validate_checkpoint(
        payload["generation_checkpoint"],
        learning_kind=kind,
        mmd_text=mmd_text,
        path="payload.generation_checkpoint",
    )
    _validate_inventory(payload["question_inventory"], "payload.question_inventory")
    _validate_usage(payload["openai_usage"], "payload.openai_usage")
    _validate_log(payload["generation_log"], "payload.generation_log")
    return job, kind, mmd_text


def _portable_payload(job: models.UploadJob) -> dict:
    return {
        "job": {
            "module": job.module,
            "upload_type": job.upload_type,
            "learning_kind": job.learning_kind,
            "source_book": job.source_book,
            "chapter_duration_minutes": int(
                getattr(job, "chapter_duration_minutes", 0) or 0
            ),
            "filename": job.filename,
            "mmd_text": job.mmd_text,
            "deposit_scope_type": job.deposit_scope_type,
            "deposit_scope_ids": list(job.deposit_scope_ids or []),
        },
        "generation_checkpoint": copy.deepcopy(
            job.generation_checkpoint or {}),
        "question_inventory": copy.deepcopy(job.question_inventory or {}),
        "openai_usage": copy.deepcopy(job.openai_usage or {}),
        "generation_log": copy.deepcopy(job.generation_log or []),
    }


def _rebind_phase38_convergence_scope(
    checkpoint: dict,
    *,
    job_id: int,
) -> dict:
    """Move a portable convergence budget into the imported job namespace."""

    rebound = copy.deepcopy(checkpoint)
    ledger = rebound.get("phase38_convergence")
    if not isinstance(ledger, dict):
        return rebound
    fingerprint = str(rebound.get("fingerprint") or "")
    updated = copy.deepcopy(ledger)
    updated["scope"] = f"upload-job:{int(job_id)}:{fingerprint}"
    rebound["phase38_convergence"] = updated
    return rebound


def _contains_unresolved_source_review(checkpoint: Any) -> bool:
    return any(
        str(entry.get("stage") or "") == "source_graph_review"
        for entry in generation._concept_checkpoint_entries(checkpoint)
        if isinstance(entry, dict)
    )


def _export_bundle_for_job(job: models.UploadJob) -> tuple[str, bytes]:
    """Serialize a job already authorized by its caller."""
    if not (job.mmd_text or "").strip():
        raise ValueError("convert the upload to MMD before exporting a checkpoint")
    if _contains_unresolved_source_review(job.generation_checkpoint):
        raise ValueError(
            "an unresolved source-review checkpoint cannot be exported: "
            "its verified original-PDF evidence is machine-bound. Resolve the "
            "source decision or replace the source before exporting."
        )

    payload = _portable_payload(job)
    _validate_json_budget({"payload": payload})
    _validate_payload(payload)
    bundle = {
        "format": BUNDLE_FORMAT,
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "payload_sha256": hashlib.sha256(_json_bytes(payload)).hexdigest(),
        "payload": payload,
    }
    stem = Path(job.filename or f"job-{job.id}").stem
    safe_stem = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in stem
    ).strip("-") or f"job-{job.id}"
    filename = f"{safe_stem}.aegis-checkpoint.json"
    return filename, _json_bytes(bundle, pretty=True)


def export_bundle(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> tuple[str, bytes]:
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    return _export_bundle_for_job(job)


def export_bundle_for_internal_backup(
    db: Session,
    job_id: int,
) -> tuple[str, bytes]:
    """Trusted worker-only export; public API paths must use ``export_bundle``."""
    job = db.query(models.UploadJob).filter(
        models.UploadJob.id == job_id,
        models.UploadJob.module == "build_concepts",
    ).one_or_none()
    if job is None:
        raise uploads.UploadJobNotFound("upload job not found")
    return _export_bundle_for_job(job)


def _read_bundle(raw_bytes: bytes) -> dict:
    if not raw_bytes:
        raise ValueError("checkpoint file is empty")
    if len(raw_bytes) > MAX_IMPORT_BYTES:
        raise ValueError("checkpoint file exceeds the 25 MB import limit")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"invalid JSON number {value}")

    try:
        bundle = json.loads(
            raw_bytes.decode("utf-8-sig"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (
        UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError,
    ) as exc:
        raise ValueError("checkpoint file is not valid UTF-8 JSON") from exc
    if not isinstance(bundle, dict):
        raise ValueError("checkpoint bundle must be a JSON object")
    _validate_json_budget(bundle)
    _exact_keys(bundle, _BUNDLE_KEYS, "bundle")
    if bundle["format"] != BUNDLE_FORMAT:
        raise ValueError("this is not an Aegis concept checkpoint bundle")
    version = bundle["bundle_schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != BUNDLE_SCHEMA_VERSION
    ):
        raise ValueError(f"unsupported checkpoint bundle version: {version!r}")
    _timestamp(bundle["exported_at"], "bundle.exported_at")
    expected = _string(
        bundle["payload_sha256"],
        "bundle.payload_sha256",
        64,
        nonempty=True,
    )
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError("bundle.payload_sha256 must be a lowercase SHA-256")
    payload = bundle["payload"]
    actual = hashlib.sha256(_json_bytes(payload)).hexdigest()
    if expected != actual:
        raise ValueError("checkpoint bundle checksum does not match its contents")
    _validate_payload(payload)
    return payload


def import_bundle(
    db: Session,
    raw_bytes: bytes,
    *,
    expected_learning_kind: str = "",
    owner_sub: str | None = None,
) -> models.UploadJob:
    payload = _read_bundle(raw_bytes)
    if _contains_unresolved_source_review(
        payload.get("generation_checkpoint")
    ):
        raise ValueError(
            "an unresolved source-review checkpoint cannot be imported "
            "without its verified original-PDF evidence. Resolve it on the "
            "original job or replace and reconvert the source."
        )
    job_data, learning_kind, mmd_text = _validate_payload(payload)
    expected_kind = expected_learning_kind.strip().lower()
    if expected_kind and expected_kind not in {"post", "pre"}:
        raise ValueError("restore flow has an invalid learning kind")
    if expected_kind and learning_kind != expected_kind:
        raise ValueError(
            f"this is a {learning_kind}-learning checkpoint; restore it from "
            f"the {learning_kind.title()} Learning flow"
        )

    imported = models.UploadJob(
        owner_sub=uploads.normalize_owner_sub(owner_sub),
        module="build_concepts",
        upload_type=job_data["upload_type"],
        learning_kind=learning_kind,
        source_book=job_data["source_book"],
        chapter_duration_minutes=int(
            job_data.get("chapter_duration_minutes") or 0
        ),
        filename=job_data["filename"] or "restored.mmd",
        mmd_text=mmd_text,
        deposit_scope_type=job_data["deposit_scope_type"],
        deposit_scope_ids=copy.deepcopy(job_data["deposit_scope_ids"]),
        status="converted",
        result_ids=[],
        question_inventory=copy.deepcopy(payload["question_inventory"]),
        generation_checkpoint=copy.deepcopy(
            payload["generation_checkpoint"]),
        generation_log=copy.deepcopy(payload["generation_log"]),
        openai_usage=copy.deepcopy(payload["openai_usage"]),
        detail=(
            "Portable checkpoint restored. Choose the matching chapter and "
            "resume generation."
        ),
    )
    try:
        db.add(imported)
        # The bundle's source job ID is intentionally not portable. Allocate
        # this job's identity first, then preserve the exact convergence
        # counters/status while rebinding only their isolation namespace.
        db.flush()
        imported.generation_checkpoint = _rebind_phase38_convergence_scope(
            imported.generation_checkpoint,
            job_id=imported.id,
        )
        db.commit()
        db.refresh(imported)
    except Exception:
        db.rollback()
        raise
    return imported


def clear_checkpoint(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> models.UploadJob:
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    with uploads.exclusive_job_operation(job.id):
        db.refresh(job)
        from . import generation_recovery
        generation_recovery.require_mutation_allowed(
            job, operation="clear this run's saved checkpoint"
        )
        job.generation_checkpoint = {}
        job.detail = "Saved generation checkpoint cleared."
        try:
            db.commit()
            db.refresh(job)
        except Exception:
            db.rollback()
            raise
        # Local import avoids the checkpoints <-> Drive module import cycle.
        from . import drive_checkpoints
        drive_checkpoints.schedule_checkpoint_backup(job.id)
    return job


def _backfill_legacy_non_resumable_recoveries(
    db: Session,
    *,
    owner_sub: str | None,
    learning_kind: str,
) -> set[int]:
    """Persist typed recovery for exact pre-marker Q24 journal records.

    Job 97 already carried Q24's full structured identity in its durable error
    log but predates ``_aegis_generation_recovery``.  Reading that exact
    fingerprint is mechanical compatibility.  The returned ids also let this
    request exclude the jobs if a persistence failure rolls the backfill back.
    """

    candidates = (
        db.query(models.UploadJob)
        .filter(
            models.UploadJob.owner_sub
            == uploads.normalize_owner_sub(owner_sub),
            models.UploadJob.module == "build_concepts",
            models.UploadJob.learning_kind == learning_kind,
            models.UploadJob.status.notin_(("released", "generated")),
        )
        .all()
    )
    legacy_ids: set[int] = set()
    changed = False
    for job in candidates:
        if (
            not isinstance(job.generation_checkpoint, dict)
            or not job.generation_checkpoint.get("stage")
        ):
            continue
        inventory = dict(job.question_inventory or {})
        if isinstance(
            inventory.get(models.GENERATION_RECOVERY_INVENTORY_KEY), dict,
        ):
            continue
        recovery = job.generation_recovery
        if recovery.get("resume_allowed") is not False:
            continue
        legacy_ids.add(int(job.id))
        inventory[models.GENERATION_RECOVERY_INVENTORY_KEY] = copy.deepcopy(
            recovery
        )
        # Mirror the current writer so a later release/manifest reader sees
        # the same recovery contract as the job endpoint.
        from . import build_concepts_release as release

        post = inventory.get(release.RELEASE_KEY)
        if isinstance(post, dict):
            marked_post = copy.deepcopy(post)
            marked_post["generation_recovery"] = copy.deepcopy(recovery)
            inventory[release.RELEASE_KEY] = marked_post
        job.question_inventory = inventory
        changed = True
    if changed:
        try:
            db.commit()
        except Exception:
            db.rollback()
    return legacy_ids


def resumable_jobs(
    db: Session,
    *,
    owner_sub: str | None = None,
    learning_kind: str,
) -> tuple[list[dict], int]:
    """Resumable Build Concepts checkpoints owned by ``owner_sub``.

    The ``post|pre`` domain is kept deliberately after restructure step 7
    retired the legacy pre-learning generation lane: it is public API contract
    (a closed union in the frontend client), it is part of every stored
    checkpoint fingerprint, and the backing column cannot be dropped because
    this repo has no migration system.

    KNOWN CONSEQUENCE OF THAT RETIREMENT, recorded so the contract does not
    read as more than it is: the ``pre`` arm still lists legacy ``pre`` jobs
    that hold a saved checkpoint, but NO route can resume one any more — the
    pre-learning generate route is gone and the post route filters
    ``learning_kind == "post"``. Such a job is listed and stranded, not
    resumable. Nothing is lost (the checkpoint and its deposited rows stay on
    disk); it simply cannot be continued by the retired lane. Whether the pre
    arm should report these as non-resumable, or migrate them onto the Phase
    03 lane, is step 8's decision — this function is deliberately left
    reporting what is stored rather than silently hiding the rows.
    """
    kind = str(learning_kind or "").strip().lower()
    if kind not in {"post", "pre"}:
        raise ValueError("learning_kind must be post or pre")
    legacy_non_resumable_ids = _backfill_legacy_non_resumable_recoveries(
        db,
        owner_sub=owner_sub,
        learning_kind=kind,
    )
    checkpoint = models.UploadJob.generation_checkpoint
    stage = checkpoint["stage"].as_string()
    saved_at = checkpoint["saved_at"].as_string()
    progress_value = checkpoint["progress"].as_float()
    target_identity = checkpoint["target_identity"]
    recovery = models.UploadJob.question_inventory[
        models.GENERATION_RECOVERY_INVENTORY_KEY
    ]
    recovery_resume_allowed = recovery["resume_allowed"].as_boolean()
    filters = (
        models.UploadJob.owner_sub == uploads.normalize_owner_sub(owner_sub),
        models.UploadJob.module == "build_concepts",
        models.UploadJob.learning_kind == kind,
        stage.is_not(None),
        stage != "",
        # A finished run is not resumable. Its checkpoint stays stored (the
        # durable artifact re-release replays from), but offering it here
        # made the UI re-prompt "Resume this run?" forever after completion.
        # SQL twin of ``models.UploadJob.checkpoint_available``.
        models.UploadJob.status.notin_(("released", "generated")),
        # Q24 preserves the paid checkpoint for diagnosis/export but records
        # that replaying it is a proven dead end. Missing legacy metadata
        # keeps the established resumable behavior; only an explicit false
        # removes the row from discovery.
        or_(
            recovery_resume_allowed.is_(None),
            recovery_resume_allowed.is_(True),
        ),
        *(
            (models.UploadJob.id.notin_(legacy_non_resumable_ids),)
            if legacy_non_resumable_ids
            else ()
        ),
    )
    total = int(
        db.query(func.count(models.UploadJob.id)).filter(*filters).scalar() or 0
    )
    rows = (
        db.query(
            models.UploadJob.id,
            models.UploadJob.module,
            models.UploadJob.learning_kind,
            models.UploadJob.filename,
            models.UploadJob.status,
            stage.label("checkpoint_stage"),
            saved_at.label("checkpoint_saved_at"),
            progress_value.label("checkpoint_progress"),
            target_identity.label("checkpoint_target_identity"),
            models.UploadJob.created_at,
        )
        .filter(*filters)
        .order_by(
            saved_at.desc(),
            models.UploadJob.created_at.desc(),
            models.UploadJob.id.desc(),
        )
        .limit(MAX_RESUMABLE_JOBS)
        .all()
    )
    items = [
        {
            "id": row.id,
            "module": row.module,
            "learning_kind": row.learning_kind,
            "filename": row.filename,
            "status": row.status,
            "checkpoint_available": True,
            "checkpoint_stage": row.checkpoint_stage or "",
            "checkpoint_saved_at": row.checkpoint_saved_at or "",
            "checkpoint_progress": float(row.checkpoint_progress or 0.0),
            "checkpoint_target_identity": (
                row.checkpoint_target_identity
                if isinstance(row.checkpoint_target_identity, dict)
                else {}
            ),
            "generation_running": uploads.is_job_running(row.id),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return items, total
