"""Strict public incident projection. Standard library only; safe to import in CI.

Never copy a private diagnostic dictionary or freeform message into this schema.
The public repository receives identities, hashes and machine codes; complete
exception messages and validator evidence remain on the private data volume.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime

SCHEMA_VERSION = 1
_HEX = re.compile(r"^[a-f0-9]{64}$")
_UUID = re.compile(r"^[a-f0-9]{32}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.<>]{0,159}$")
_CODE = re.compile(r"^(?:(?:phase[0-9]+|semantic|source|checkpoint|master|missing|invalid|batch|attempts|question|pre|post|rich_text|topology|diagnostic|capture|validation|provider|storage|generation|chapter|rendered)_[a-z0-9_]{1,100}|raw_latex|raw_math_expression|raw_math_delimiter|unbalanced_braces|unbalanced_math_delimiters|raw_latex_command|invalid_katex|latex_in_text|unsupported_table|unsupported_katex_command|katex_row_spacing|unbalanced_katex|nested_katex|empty_katex|malformed_katex|noncanonical_katex_case|markdown_image|literal_newline_escape|unbalanced_image|noncanonical_image)$")
_POLICY_VERSION = re.compile(r"^(?:owner-(?:generation-quality|generation-repair|stage-model-routing|column-spec)|concepts-then-reviewed-masters)-[0-9]{4}-[0-9]{2}-[0-9]{2}-v[0-9]+$")
_ENTITY = re.compile(r"^(?:(?:QINV|BLK|PRE|POST|CONCEPT|TYPE|CASE|HOST|TOPIC|TOPOLOGY|SEG|GROUP|CELL|CANDIDATE)[-_][A-Z0-9_-]{1,100}|[a-f0-9]{32}|[0-9]{1,12})$")
_PROVIDER_ID = re.compile(r"^(?:req|resp|batch|chatcmpl|wave|attempt)[_-][A-Za-z0-9_-]{1,100}$")
_MODEL = re.compile(r"^(?:gpt|gemini|claude|o1|o3|o4)[-a-zA-Z0-9._]{0,100}$")
_FRAME = re.compile(r"^(?:app|scripts)/[A-Za-z0-9_/-]+\.py$")
ORIGINS = {"upload", "master_lane", "queue", "stream", "historical", "unknown"}
DISPOSITIONS = {"failed", "retry", "blocked"}
POLICIES = {"model_routing_policy", "generation_quality_policy", "generation_repair_policy", "reviewed_file_workflow_policy", "_column_spec_policy"}
EVIDENCE_HASHES = {"expected_pdf_sha256", "actual_pdf_sha256", "expected_source_contract_hash", "actual_source_contract_hash", "expected_semantic_context_hash", "actual_semantic_context_hash"}
EVIDENCE_REASONS = {"original_pdf_changed", "original_pdf_missing", "saved_page_evidence_missing", "source_contract_changed", "semantic_context_changed", "saved_review_seal_invalid"}
_NUMERIC_USAGE = {"request_count", "attempt_count", "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd", "known_usage_estimated_cost_usd", "pending_request_count", "unresolved_usage_request_count"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def _matching(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _integer(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _number(value):
    return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9T:Z.+-]{20,40}", value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _provider_id(value):
    return _matching(value, _PROVIDER_ID)


def _receipt_id(value):
    if not isinstance(value, str):
        return None
    batch, separator, sha = value.partition(":")
    return value if separator and _provider_id(batch) and _matching(sha, _HEX) else None


def project_public_report(private: dict) -> dict:
    """Build every exported field explicitly, then independently validate it."""
    error = _mapping(private.get("error"))
    job = _mapping(private.get("job"))
    source = _mapping(private.get("source"))
    task = _mapping(private.get("task"))
    ledger = _mapping(private.get("usage"))
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_id": private.get("report_id"),
        "related_report_id": _matching(private.get("related_report_id"), _UUID),
        "occurred_at": _timestamp(private.get("occurred_at")),
        "fingerprint": _matching(private.get("fingerprint"), _HEX),
        "origin": private.get("origin") if private.get("origin") in ORIGINS else "unknown",
        "disposition": private.get("disposition") if private.get("disposition") in DISPOSITIONS else "failed",
        "failure_code": _matching(private.get("failure_code"), _CODE),
        "failure_code_sha256": digest(private.get("failure_code") or ""),
        "exception": {
            "type": _matching(error.get("type"), _IDENTIFIER) or "Exception",
            "message_sha256": _matching(error.get("message_sha256"), _HEX) or digest(error.get("message")),
            "traceback_sha256": _matching(error.get("traceback_sha256"), _HEX) or digest(error.get("traceback")),
            "frames": [{"file": row["file"], "line": _integer(row.get("line")),
                        "function": _matching(row.get("function"), _IDENTIFIER) or "unknown"}
                       for row in error.get("frames", [])[-128:]
                       if isinstance(row, dict) and _matching(row.get("file"), _FRAME)
                       and ".." not in row["file"] and _integer(row.get("line")) is not None],
        },
        "run": {
            "job_id": _integer(job.get("id")),
            "run_id_sha256": digest(job.get("run_id") or ""),
            "chapter_id": _integer(job.get("chapter_id")),
            "module": job.get("module") if job.get("module") in {"build_concepts", "build_assessments"} else "unknown",
            "execution_mode": job.get("execution_mode") if job.get("execution_mode") in {"batch", "synchronous"} else "legacy",
            "stage_sha256": digest(private.get("stage") or ""),
            "checkpoint_stage_sha256": digest(job.get("checkpoint_stage") or ""),
            "lane": private.get("lane") if private.get("lane") in {"pre", "post"} else None,
        },
        "source": {key: _matching(source.get(key), _HEX) for key in (
            "mmd_sha256", "filename_sha256", "checkpoint_sha256", "inventory_sha256")},
        "source_evidence": {
            **{key: _matching(_mapping(_mapping(error.get("attributes")).get("evidence_identity")).get(key), _HEX)
               for key in sorted(EVIDENCE_HASHES)},
            "reason": (_mapping(error.get("attributes")).get("reason_code")
                       if _mapping(error.get("attributes")).get("reason_code") in EVIDENCE_REASONS else None),
            "recorded_pdf_sha256s": [value for value in source.get("recorded_pdf_sha256s", [])[:100] if _matching(value, _HEX)],
            "page_evidence_sha256s": [value for value in source.get("page_evidence_sha256s", [])[:100] if _matching(value, _HEX)],
        },
        "queue": {
            **{key: _integer(task.get(key)) for key in ("id", "batch_row_id", "attempt", "max_attempts")},
            "kind": task.get("kind") if task.get("kind") in {"step01", "step02", "publish"} else None,
            "cohort_sha256": digest(task.get("cohort_id") or ""),
            "lease_owner_sha256": digest(task.get("lease_owner") or ""),
            "lease_expires_at": _timestamp(task.get("lease_expires_at")),
        },
        "policies": [{"name": row["name"], "sha256": row["sha256"],
                      "version": _matching(_mapping(row.get("value")).get("version") if isinstance(row.get("value"), dict) else row.get("value"), _POLICY_VERSION)}
                     for row in private.get("policies", [])[:100]
                     if isinstance(row, dict) and row.get("name") in POLICIES
                     and _matching(row.get("sha256"), _HEX)],
        "validation": [],
        "usage": {
            "ledger_sha256": _matching(ledger.get("ledger_sha256"), _HEX) or digest(ledger),
            "totals": {key: _number(_mapping(ledger.get("totals")).get(key)) for key in sorted(_NUMERIC_USAGE)},
            "pricing_complete": _mapping(ledger.get("totals")).get("pricing_complete") is True,
            "usage_complete": _mapping(ledger.get("totals")).get("usage_complete") is True,
            "attempts": [], "omitted_attempts": _integer(ledger.get("omitted_attempts")) or 0,
        },
        "capture": {"truncated": bool(private.get("truncated")),
                    "private_sha256": digest(private)},
    }
    for row in private.get("validation", [])[:500]:
        if not isinstance(row, dict):
            continue
        ids = [value for value in row.get("entity_ids", []) if _matching(value, _ENTITY)]
        report["validation"].append({
            "code": _matching(row.get("code"), _CODE),
            "code_sha256": digest(row.get("code") or ""),
            "entity_ids": ids[:200], "details_sha256": digest(row),
            "issue_codes": [value for value in row.get("issue_codes", [])[:100] if _matching(value, _CODE)],
            "source_identity_hashes": [
                {"qid": item["qid"], "expected_question_sha256": item["expected_question_sha256"]}
                for item in _mapping(row.get("details")).get("source_identities", [])[:200]
                if isinstance(item, dict) and _matching(item.get("qid"), _ENTITY)
                and _matching(item.get("expected_question_sha256"), _HEX)
            ],
        })
    receipts = {row.get("receipt_id"): row for row in ledger.get("paid_receipts", [])
                if isinstance(row, dict) and _receipt_id(row.get("receipt_id"))}
    for row in ledger.get("attempts", [])[-500:]:
        if not isinstance(row, dict):
            continue
        receipt = receipts.get(row.get("receipt_id"), {})
        frozen_cost = _mapping(receipt.get("cost_estimate"))
        report["usage"]["attempts"].append({
            "attempt_id": _matching(row.get("attempt_id"), _UUID),
            "request_id": _provider_id(row.get("request_id")),
            "response_id": _provider_id(row.get("response_id")),
            "batch_id": _provider_id(row.get("batch_id")),
            "wave_id": _provider_id(row.get("wave_id")),
            "request_sha256": _matching(row.get("request_sha256"), _HEX),
            "receipt_id": _receipt_id(row.get("receipt_id")),
            "provider": row.get("provider") if row.get("provider") in {"openai", "gemini", "anthropic"} else None,
            "model": _matching(row.get("actual_model") or row.get("model") or row.get("requested_model"), _MODEL),
            "delivery_mode": row.get("delivery_mode") if row.get("delivery_mode") in {"batch", "synchronous"} else None,
            "reused": row.get("reused") is True,
            "usage_reported": row.get("usage_reported") is True,
            "estimated_cost_usd": _number(frozen_cost.get("estimated_cost_usd") if frozen_cost else row.get("estimated_cost_usd")),
            "receipt_sha256": digest(receipt) if receipt else None,
            "receipt_owner_job_id": _integer(receipt.get("owner_job_id")),
            "input_tokens": _integer(frozen_cost.get("input_tokens")),
            "output_tokens": _integer(frozen_cost.get("output_tokens")),
        })
    validate_public_report(report)
    return report


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("unexpected public incident fields")


def _nullable(value, checker):
    if value is not None and checker(value) is None:
        raise ValueError("invalid public incident value")


def validate_public_report(report: dict) -> None:
    """Reject unknown keys, malformed IDs or text-bearing fields in CI."""
    _keys(report, {"schema_version", "report_id", "related_report_id", "occurred_at", "fingerprint", "origin", "disposition", "failure_code", "failure_code_sha256", "exception", "run", "source", "source_evidence", "queue", "policies", "validation", "usage", "capture"})
    if report["schema_version"] != 1 or not _matching(report["report_id"], _UUID) or not _timestamp(report["occurred_at"]):
        raise ValueError("invalid incident identity")
    _nullable(report["related_report_id"], lambda value: _matching(value, _UUID))
    if report["origin"] not in ORIGINS or report["disposition"] not in DISPOSITIONS:
        raise ValueError("invalid incident lifecycle")
    for key in ("fingerprint", "failure_code_sha256"):
        if not _matching(report[key], _HEX):
            raise ValueError("invalid incident hash")
    _nullable(report["failure_code"], lambda value: _matching(value, _CODE))
    error = report["exception"]
    _keys(error, {"type", "message_sha256", "traceback_sha256", "frames"})
    if not _matching(error["type"], _IDENTIFIER):
        raise ValueError("invalid exception type")
    for key in ("message_sha256", "traceback_sha256"):
        if not _matching(error[key], _HEX):
            raise ValueError("invalid exception hash")
    if not isinstance(error["frames"], list) or len(error["frames"]) > 128:
        raise ValueError("invalid stack frames")
    for row in error["frames"]:
        _keys(row, {"file", "line", "function"})
        if not _matching(row["file"], _FRAME) or ".." in row["file"] or _integer(row["line"]) is None or not _matching(row["function"], _IDENTIFIER):
            raise ValueError("invalid code frame")
    run = report["run"]
    _keys(run, {"job_id", "run_id_sha256", "chapter_id", "module", "execution_mode", "stage_sha256", "checkpoint_stage_sha256", "lane"})
    for key in ("job_id", "chapter_id"):
        _nullable(run[key], _integer)
    for key in ("run_id_sha256", "stage_sha256", "checkpoint_stage_sha256"):
        if not _matching(run[key], _HEX):
            raise ValueError("invalid run hash")
    if run["module"] not in {"build_concepts", "build_assessments", "unknown"} or run["execution_mode"] not in {"batch", "synchronous", "legacy"} or run["lane"] not in {None, "pre", "post"}:
        raise ValueError("invalid run classification")
    _keys(report["source"], {"mmd_sha256", "filename_sha256", "checkpoint_sha256", "inventory_sha256"})
    for value in report["source"].values():
        _nullable(value, lambda item: _matching(item, _HEX))
    evidence = report["source_evidence"]
    _keys(evidence, EVIDENCE_HASHES | {"reason", "recorded_pdf_sha256s", "page_evidence_sha256s"})
    for key in EVIDENCE_HASHES:
        _nullable(evidence[key], lambda item: _matching(item, _HEX))
    if evidence["reason"] is not None and evidence["reason"] not in EVIDENCE_REASONS:
        raise ValueError("invalid source evidence reason")
    for key in ("recorded_pdf_sha256s", "page_evidence_sha256s"):
        if not isinstance(evidence[key], list) or len(evidence[key]) > 100 or any(not _matching(value, _HEX) for value in evidence[key]):
            raise ValueError("invalid source evidence hashes")
    queue = report["queue"]
    _keys(queue, {"id", "batch_row_id", "attempt", "max_attempts", "kind", "cohort_sha256", "lease_owner_sha256", "lease_expires_at"})
    for key in ("id", "batch_row_id", "attempt", "max_attempts"):
        _nullable(queue[key], _integer)
    if queue["kind"] not in {None, "step01", "step02", "publish"}:
        raise ValueError("invalid queue kind")
    for key in ("cohort_sha256", "lease_owner_sha256"):
        if not _matching(queue[key], _HEX):
            raise ValueError("invalid queue hash")
    _nullable(queue["lease_expires_at"], _timestamp)
    if not isinstance(report["policies"], list) or len(report["policies"]) > 100:
        raise ValueError("invalid policies")
    for row in report["policies"]:
        _keys(row, {"name", "sha256", "version"})
        if row["name"] not in POLICIES or not _matching(row["sha256"], _HEX):
            raise ValueError("invalid policy")
        _nullable(row["version"], lambda item: _matching(item, _POLICY_VERSION))
    if not isinstance(report["validation"], list) or len(report["validation"]) > 500:
        raise ValueError("invalid validations")
    for row in report["validation"]:
        _keys(row, {"code", "code_sha256", "entity_ids", "details_sha256", "issue_codes", "source_identity_hashes"})
        _nullable(row["code"], lambda item: _matching(item, _CODE))
        if not _matching(row["code_sha256"], _HEX) or not _matching(row["details_sha256"], _HEX):
            raise ValueError("invalid validation hash")
        if not isinstance(row["entity_ids"], list) or len(row["entity_ids"]) > 200 or any(not _matching(value, _ENTITY) for value in row["entity_ids"]):
            raise ValueError("invalid entity ids")
        if not isinstance(row["issue_codes"], list) or len(row["issue_codes"]) > 100 or any(not _matching(value, _CODE) for value in row["issue_codes"]):
            raise ValueError("invalid issue codes")
        if not isinstance(row["source_identity_hashes"], list) or len(row["source_identity_hashes"]) > 200:
            raise ValueError("invalid question identities")
        for item in row["source_identity_hashes"]:
            _keys(item, {"qid", "expected_question_sha256"})
            if not _matching(item["qid"], _ENTITY) or not _matching(item["expected_question_sha256"], _HEX):
                raise ValueError("invalid question identity")
    usage = report["usage"]
    _keys(usage, {"ledger_sha256", "totals", "pricing_complete", "usage_complete", "attempts", "omitted_attempts"})
    if not _matching(usage["ledger_sha256"], _HEX) or _integer(usage["omitted_attempts"]) is None:
        raise ValueError("invalid usage identity")
    _keys(usage["totals"], _NUMERIC_USAGE)
    for value in usage["totals"].values():
        _nullable(value, _number)
    if type(usage["pricing_complete"]) is not bool or type(usage["usage_complete"]) is not bool or not isinstance(usage["attempts"], list) or len(usage["attempts"]) > 500:
        raise ValueError("invalid usage shape")
    for row in usage["attempts"]:
        _keys(row, {"attempt_id", "request_id", "response_id", "batch_id", "wave_id", "request_sha256", "receipt_id", "provider", "model", "delivery_mode", "reused", "usage_reported", "estimated_cost_usd", "receipt_sha256", "receipt_owner_job_id", "input_tokens", "output_tokens"})
        _nullable(row["attempt_id"], lambda item: _matching(item, _UUID))
        for key in ("request_id", "response_id", "batch_id", "wave_id"):
            _nullable(row[key], _provider_id)
        _nullable(row["request_sha256"], lambda item: _matching(item, _HEX))
        _nullable(row["receipt_id"], _receipt_id)
        _nullable(row["model"], lambda item: _matching(item, _MODEL))
        _nullable(row["estimated_cost_usd"], _number)
        _nullable(row["receipt_sha256"], lambda item: _matching(item, _HEX))
        for key in ("receipt_owner_job_id", "input_tokens", "output_tokens"):
            _nullable(row[key], _integer)
        if row["provider"] not in {None, "openai", "gemini", "anthropic"} or row["delivery_mode"] not in {None, "batch", "synchronous"} or type(row["reused"]) is not bool or type(row["usage_reported"]) is not bool:
            raise ValueError("invalid provider receipt")
    _keys(report["capture"], {"truncated", "private_sha256"})
    if type(report["capture"]["truncated"]) is not bool or not _matching(report["capture"]["private_sha256"], _HEX):
        raise ValueError("invalid capture evidence")


def validate_public_envelope(envelope: dict) -> None:
    _keys(envelope, {"schema_version", "exported_at", "reports", "next_cursor", "total_count"})
    if envelope["schema_version"] != 1 or not _timestamp(envelope["exported_at"]) or _integer(envelope["total_count"]) is None:
        raise ValueError("invalid export envelope")
    cursor = envelope["next_cursor"]
    if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", cursor)):
        raise ValueError("invalid export cursor")
    if not isinstance(envelope["reports"], list) or len(envelope["reports"]) > 10000:
        raise ValueError("invalid export page")
    for report in envelope["reports"]:
        validate_public_report(report)
