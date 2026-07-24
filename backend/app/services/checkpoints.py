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

from sqlalchemy.orm import Session

from .. import models
from . import generation, uploads


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

_TARGET_FIELDS = (
    "board",
    "grade",
    "subject",
    "unit",
    "chapter_title",
    "chapter_code",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
_USAGE_INTS = {
    "request_count": MAX_REQUEST_COUNT,
    "input_tokens": MAX_TOKEN_COUNT,
    "cached_input_tokens": MAX_TOKEN_COUNT,
    "uncached_input_tokens": MAX_TOKEN_COUNT,
    "output_tokens": MAX_TOKEN_COUNT,
    "reasoning_tokens": MAX_TOKEN_COUNT,
    "total_tokens": MAX_TOKEN_COUNT,
}
_USAGE_TOP_KEYS = {
    "model", "models", *_USAGE_INTS, "estimated_cost_usd", "currency",
    "pricing_complete", "pricing_as_of", "pricing_source",
}
_USAGE_MODEL_KEYS = {
    "model", *_USAGE_INTS, "estimated_cost_usd",
    "pricing_complete", "pricing_source",
}


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
) -> str:
    material = (
        "concept-generation-checkpoint-v2\0"
        + "\0".join([
            _stable(learning_kind or "post"),
            *(target[field] for field in _TARGET_FIELDS),
            mmd_text,
        ])
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
    if digest != _expected_fingerprint(learning_kind, target, mmd_text):
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
        "pre_draft", "pre_audited",
    ):
        if field in entry and not isinstance(entry[field], dict):
            raise ValueError(f"{path}.{field} must be an object")
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
    if known and not generation._compatible_concept_checkpoint_entry(entry):
        raise ValueError(f"{path} is not a compatible checkpoint stage")


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
        if not history:
            raise ValueError(f"{path}.checkpoints must not be empty")
        stages: set[str] = set()
        for index, entry in enumerate(history):
            _validate_checkpoint_entry(
                entry, f"{path}.checkpoints[{index}]")
            stage = entry["stage"]
            if stage in stages:
                raise ValueError(
                    f"{path}.checkpoints contains duplicate stage {stage!r}")
            stages.add(stage)
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
    if not generation._valid_concept_checkpoint(value):
        raise ValueError(
            f"{path} does not contain a compatible completed stage")


def _validate_inventory(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    _exact_keys(
        value,
        {"items", "stats", "mined_types"},
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
        required=allowed if model_row else set(),
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
    input_tokens = value.get("input_tokens")
    if cached is not None and input_tokens is not None and cached > input_tokens:
        raise ValueError(f"{path}.cached_input_tokens exceeds input_tokens")
    reasoning = value.get("reasoning_tokens")
    output = value.get("output_tokens")
    if reasoning is not None and output is not None and reasoning > output:
        raise ValueError(f"{path}.reasoning_tokens exceeds output_tokens")


def _validate_usage(value: Any, path: str) -> None:
    _validate_usage_row(value, path, model_row=False)
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
    _exact_keys(value, _JOB_KEYS, path)
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


def export_bundle(db: Session, job_id: int) -> tuple[str, bytes]:
    job = uploads.get_job(db, job_id)
    if job.module != "build_concepts":
        raise ValueError("only Build Concepts uploads support checkpoint export")
    if not (job.mmd_text or "").strip():
        raise ValueError("convert the upload to MMD before exporting a checkpoint")

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
) -> models.UploadJob:
    payload = _read_bundle(raw_bytes)
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
        module="build_concepts",
        upload_type=job_data["upload_type"],
        learning_kind=learning_kind,
        source_book=job_data["source_book"],
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
        db.commit()
        db.refresh(imported)
    except Exception:
        db.rollback()
        raise
    return imported


def clear_checkpoint(db: Session, job_id: int) -> models.UploadJob:
    job = uploads.get_job(db, job_id)
    if job.module != "build_concepts":
        raise ValueError("only Build Concepts uploads support checkpoints")
    job.generation_checkpoint = {}
    job.detail = "Saved generation checkpoint cleared."
    try:
        db.commit()
        db.refresh(job)
    except Exception:
        db.rollback()
        raise
    return job
