"""One-shot, evidence-bounded semantic decision agent.

The agent is deliberately narrower than generation.  It may select only a
server-offered action and target, it receives bounded source/checkpoint
evidence, and it must abstain when that evidence is incomplete or ambiguous.
The orchestration layer owns durable exactly-once dispatch state.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .. import config
from . import canonical_source_phase22 as phase22
from . import generation
from . import semantic_confidence_policy as confidence_policy


RESOLVER_VERSION = "semantic-resolution-agent-1"
_ISSUE_KEY_VERSION = 1
_DEFAULT_MAX_DECISIONS = 6
_DEFAULT_SOURCE_CHARS = 16_000
_MAX_PACKET_CHARS = 48_000
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ResolutionResult:
    status: str
    reason: str
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    choice: str = ""
    instruction: str = ""
    target_id: str = ""
    target_concept_id: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"


def enabled() -> bool:
    raw = os.environ.get("AEGIS_AUTONOMOUS_RESOLUTION_ENABLED", "1")
    opted_in = raw.strip().lower() in {"1", "true", "yes", "on"}
    return opted_in and config.use_live_generation() and config.has_openai()


def maximum_decisions() -> int:
    raw = os.environ.get(
        "AEGIS_AUTONOMOUS_RESOLUTION_MAX_DECISIONS",
        str(_DEFAULT_MAX_DECISIONS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_DECISIONS
    return max(0, min(25, value))


def issue_key(pending: Mapping[str, Any]) -> str:
    """Identify a semantic scope across regenerated follow-up decision IDs."""

    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    material = {
        # This schema identity is deliberately independent of the resolver or
        # model version. Deploying new code must not rebill an unresolved scope.
        "issue_key_version": _ISSUE_KEY_VERSION,
        "kind": _normal(pending.get("kind")),
        "phase": _normal(pending.get("phase")),
        "item": {
            "unit_id": _normal(item.get("unit_id")),
            "type_id": _normal(item.get("type_id")),
            "qids": sorted({
                _normal(value) for value in item.get("qids") or [] if value
            }),
            "topic": _normal(item.get("topic")),
        },
    }
    return hashlib.sha256(json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\x00", "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…"


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().casefold()


def _source_limit() -> int:
    raw = os.environ.get(
        "AEGIS_AUTONOMOUS_RESOLUTION_SOURCE_CHARS",
        str(_DEFAULT_SOURCE_CHARS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_SOURCE_CHARS
    return max(4_000, min(40_000, value))


def _search_needles(pending: Mapping[str, Any]) -> list[str]:
    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    raw: list[object] = [
        item.get("unit_id"), item.get("type_id"), item.get("type_title"),
        item.get("topic"), *(item.get("qids") or []),
        *(item.get("questions") or []),
    ]
    for key in ("evidence", "candidates"):
        for row in pending.get(key) or []:
            if not isinstance(row, Mapping):
                continue
            raw.extend(row.get(field) for field in (
                "label", "text", "target_id", "concept_id", "title",
                "topic", "coverage", "gap",
            ))
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = _SPACE_RE.sub(" ", str(value or "")).strip()
        if len(text) < 5:
            continue
        # Long evidence often differs only in punctuation.  Its first and last
        # distinctive phrases provide useful non-prefix retrieval anchors.
        variants = [text]
        words = text.split()
        if len(words) > 12:
            variants.extend((" ".join(words[:10]), " ".join(words[-10:])))
        for variant in variants:
            key = variant.casefold()
            if key not in seen:
                seen.add(key)
                result.append(variant)
    return result[:80]


def _mmd_windows(
    source_text: str,
    pending: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Retrieve issue-local windows anywhere in the MMD, including its tail."""

    source = str(source_text or "")
    if not source:
        return []
    folded = source.casefold()
    spans: list[tuple[int, int]] = []
    for needle in _search_needles(pending):
        position = folded.find(needle.casefold())
        if position < 0:
            # Whitespace-normalized exact phrases remain bounded and improve
            # matches against line-wrapped MMD without fuzzy guessing.
            words = [re.escape(word) for word in needle.split()[:12] if word]
            if len(words) >= 3:
                match = re.search(r"\s+".join(words), source, flags=re.I)
                position = match.start() if match else -1
        if position < 0:
            continue
        start = max(0, position - 700)
        end = min(len(source), position + max(len(needle), 1) + 900)
        if any(start <= old_end + 200 and end >= old_start - 200
               for old_start, old_end in spans):
            spans = [
                (min(start, old_start), max(end, old_end))
                if start <= old_end + 200 and end >= old_start - 200
                else (old_start, old_end)
                for old_start, old_end in spans
            ]
        else:
            spans.append((start, end))
        if len(spans) >= 10:
            break
    matched_issue = bool(spans)
    if not spans:
        # A small prefix is diagnostic context only; absence of a true match is
        # recorded separately and can never by itself authorize auto-apply.
        spans = [(0, min(len(source), 1_800))]
    budget = _source_limit()
    out: list[dict[str, str]] = []
    used = 0
    for index, (start, end) in enumerate(sorted(spans), start=1):
        text = source[start:end]
        if used + len(text) > budget:
            text = text[:max(0, budget - used)]
        if not text:
            break
        out.append({
            "evidence_id": f"MMD-WINDOW-{index:03d}",
            "source_offsets": f"{start}:{start + len(text)}",
            "text": text,
            "issue_match": matched_issue,
        })
        used += len(text)
        if used >= budget:
            break
    return out


def _bounded_rows(value: object, *, maximum: int, chars: int) -> list[dict]:
    out: list[dict] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, Mapping):
            continue
        row: dict[str, Any] = {}
        for raw_key, raw_value in raw.items():
            key = _compact_text(raw_key, 96)
            if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
                value_to_add: Any = (
                    _compact_text(raw_value, min(600, max(80, chars // 3)))
                    if isinstance(raw_value, str)
                    else raw_value
                )
            else:
                value_to_add = _compact_text(
                    json.dumps(raw_value, ensure_ascii=False, default=str),
                    min(800, max(120, chars // 2)),
                )
            candidate = {**row, key: value_to_add}
            if len(json.dumps(candidate, ensure_ascii=False, default=str)) > chars:
                continue
            row = candidate
        if not row:
            row = {"summary": _compact_text(
                json.dumps(dict(raw), ensure_ascii=False, default=str),
                max(80, chars - 32),
            )}
        out.append(row)
        if len(out) >= maximum:
            break
    return out


def _compact_string_list(
    values: object,
    *,
    maximum: int,
    chars: int,
) -> list[str]:
    return [
        _compact_text(value, chars)
        for value in (values if isinstance(values, list) else [])[:maximum]
    ]


def _model_pending_decision(pending: Mapping[str, Any]) -> dict[str, Any]:
    """Keep decision semantics while excluding unbounded UI/audit payloads."""

    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    options: list[dict[str, Any]] = []
    priority_target_ids: set[str] = set()
    priority_concept_ids: set[str] = set()
    for raw in pending.get("options") or []:
        if not isinstance(raw, Mapping):
            continue
        target_id = _compact_text(raw.get("target_id"), 512)
        target_concept_id = _compact_text(
            raw.get("target_concept_id"), 256
        )
        if target_id:
            priority_target_ids.add(target_id)
        if target_concept_id:
            priority_concept_ids.add(target_concept_id)
        options.append({
            "choice": _compact_text(raw.get("choice"), 64),
            "label": _compact_text(raw.get("label"), 320),
            "recommended": bool(raw.get("recommended")),
            "target_id": target_id,
            "target_concept_id": target_concept_id,
        })
        if len(options) >= 16:
            break

    raw_candidates = [
        row for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
    ]
    ordered_candidates = sorted(
        enumerate(raw_candidates),
        key=lambda pair: (
            0 if (
                str(pair[1].get("target_id") or "") in priority_target_ids
                or str(pair[1].get("concept_id") or "")
                in priority_concept_ids
            ) else 1,
            pair[0],
        ),
    )
    candidates = [{
        "target_id": _compact_text(row.get("target_id"), 512),
        "concept_id": _compact_text(row.get("concept_id"), 256),
        "title": _compact_text(row.get("title"), 420),
        "topic": _compact_text(row.get("topic"), 420),
        "coverage": _compact_text(row.get("coverage"), 650),
        "gap": _compact_text(row.get("gap"), 650),
    } for _index, row in ordered_candidates[:8]]

    return {
        "decision_id": _compact_text(pending.get("decision_id"), 128),
        "context_hash": _compact_text(pending.get("context_hash"), 64),
        "kind": _compact_text(pending.get("kind"), 128),
        "phase": _compact_text(pending.get("phase"), 128),
        "conflict": _compact_text(pending.get("conflict"), 1_200),
        "diagnosis": _compact_text(pending.get("diagnosis"), 1_200),
        "decision_question": _compact_text(
            pending.get("decision_question"), 1_200
        ),
        "checkpoint_progress": pending.get("checkpoint_progress", 0.0),
        "item": {
            "unit_id": _compact_text(item.get("unit_id"), 512),
            "type_id": _compact_text(item.get("type_id"), 512),
            "type_title": _compact_text(item.get("type_title"), 512),
            "qids": _compact_string_list(
                item.get("qids"), maximum=20, chars=160
            ),
            "questions": _compact_string_list(
                item.get("questions"), maximum=6, chars=600
            ),
            "topic": _compact_text(item.get("topic"), 800),
        },
        "candidates": candidates,
        "deferred_assignment_unit_ids": _compact_string_list(
            pending.get("deferred_assignment_unit_ids"),
            maximum=30,
            chars=128,
        ),
        "options": options,
    }


def _relevant_checkpoint_context(
    checkpoint: Mapping[str, Any] | None,
    pending: Mapping[str, Any],
) -> dict[str, Any]:
    stage = generation._newest_compatible_concept_checkpoint(
        dict(checkpoint or {})
    ) or {}
    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    wanted_qids = {_normal(value) for value in item.get("qids") or [] if value}
    wanted_topic = _normal(item.get("topic"))
    wanted_titles = {
        _normal(row.get("title"))
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("title")
    }

    inventory = stage.get("question_task_inventory")
    inventory_items = (
        inventory.get("items")
        if isinstance(inventory, Mapping) else []
    )
    relevant_inventory = []
    for row in inventory_items or []:
        if not isinstance(row, Mapping):
            continue
        encoded = _normal(json.dumps(dict(row), ensure_ascii=False, default=str))
        if (
            not wanted_qids and not wanted_topic
            or _normal(row.get("qid")) in wanted_qids
            or (wanted_topic and wanted_topic in encoded)
            or any(title and title in encoded for title in wanted_titles)
        ):
            relevant_inventory.append(dict(row))

    mined = stage.get("mined_types")
    types = mined.get("types") if isinstance(mined, Mapping) else []
    relevant_types = []
    wanted_type = _normal(item.get("type_id"))
    for row in types or []:
        if not isinstance(row, Mapping):
            continue
        encoded = _normal(json.dumps(dict(row), ensure_ascii=False, default=str))
        if (
            not wanted_type and not wanted_qids and not wanted_topic
            or (wanted_type and _normal(row.get("type_id")) == wanted_type)
            or any(qid and qid in encoded for qid in wanted_qids)
            or (wanted_topic and wanted_topic in encoded)
        ):
            relevant_types.append(dict(row))

    relevant_records = []
    for row in stage.get("records") or []:
        if not isinstance(row, Mapping):
            continue
        encoded = _normal(json.dumps(dict(row), ensure_ascii=False, default=str))
        if (
            not wanted_topic and not wanted_titles
            or (wanted_topic and wanted_topic in encoded)
            or any(title and title in encoded for title in wanted_titles)
        ):
            relevant_records.append(dict(row))

    return {
        "stage": str(stage.get("stage") or ""),
        "saved_at": str(stage.get("saved_at") or ""),
        "records": _bounded_rows(relevant_records, maximum=4, chars=1_100),
        "question_inventory": _bounded_rows(
            relevant_inventory, maximum=5, chars=900
        ),
        "mined_types": _bounded_rows(
            relevant_types, maximum=4, chars=1_100
        ),
    }


def build_packet(
    pending: Mapping[str, Any],
    *,
    source_text: str,
    checkpoint: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], set[str]]:
    model_pending = _model_pending_decision(pending)
    evidence: list[dict[str, str]] = []
    for index, row in enumerate(pending.get("evidence") or [], start=1):
        if not isinstance(row, Mapping):
            continue
        evidence.append({
            "evidence_id": f"PENDING-EVIDENCE-{index:03d}",
            "label": _compact_text(row.get("label"), 512),
            "page": _compact_text(row.get("page"), 128),
            "text": _compact_text(row.get("text"), 1_200),
        })
        if len(evidence) >= 6:
            break
    windows = _mmd_windows(source_text, pending)
    true_mmd_match = any(
        row.get("issue_match") is True for row in windows
    )
    packet = {
        "resolver_version": RESOLVER_VERSION,
        "issue_key": issue_key(pending),
        "source_identity": {
            "mmd_sha256": hashlib.sha256(
                str(source_text or "").encode("utf-8")
            ).hexdigest(),
            "checkpoint_fingerprint": str(
                (checkpoint or {}).get("fingerprint") or ""
            ),
        },
        "task": (
            "Resolve only when one offered action is clearly supported by the "
            "supplied evidence. Otherwise ask the user."
        ),
        "pending_decision": model_pending,
        "checkpoint_context": _relevant_checkpoint_context(
            checkpoint, pending
        ),
        "source_evidence": evidence,
        "mmd_windows": windows,
        "evidence_status": {
            "has_pending_evidence": bool(evidence),
            "has_issue_matched_mmd": bool(true_mmd_match),
        },
        "constraints": {
            "choose_only_offered_action": True,
            "choose_only_offered_target": True,
            "preserve_source_wording_qids_figures_topics_and_types": True,
            "never_replace_source_automatically": True,
            "never_invent_custom_instruction": True,
            "abstain_on_uncertainty": True,
        },
    }
    encoded = json.dumps(packet, ensure_ascii=False, default=str)
    if len(encoded) > _MAX_PACKET_CHARS:
        packet["checkpoint_context"]["records"] = []
        packet["checkpoint_context"]["question_inventory"] = []
        packet["checkpoint_context"]["mined_types"] = []
    if len(json.dumps(packet, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        packet["source_evidence"] = [
            {**row, "text": _compact_text(row.get("text"), 500)}
            for row in packet["source_evidence"][:3]
        ]
        remaining = 7_000
        bounded_windows = []
        for row in packet["mmd_windows"]:
            text = _compact_text(row.get("text"), min(2_500, remaining))
            if not text:
                break
            bounded_windows.append({**row, "text": text})
            remaining -= len(text)
            if remaining <= 0:
                break
        packet["mmd_windows"] = bounded_windows
    if len(json.dumps(packet, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        compact_pending = packet["pending_decision"]
        compact_pending["conflict"] = _compact_text(
            compact_pending.get("conflict"), 500
        )
        compact_pending["diagnosis"] = _compact_text(
            compact_pending.get("diagnosis"), 500
        )
        compact_pending["decision_question"] = _compact_text(
            compact_pending.get("decision_question"), 500
        )
        compact_pending["item"]["questions"] = (
            compact_pending["item"]["questions"][:3]
        )
        compact_pending["deferred_assignment_unit_ids"] = []
        compact_pending["candidates"] = [
            {
                **row,
                "title": _compact_text(row.get("title"), 200),
                "topic": _compact_text(row.get("topic"), 200),
                "coverage": _compact_text(row.get("coverage"), 240),
                "gap": _compact_text(row.get("gap"), 240),
            }
            for row in compact_pending["candidates"][:6]
        ]
    if len(json.dumps(packet, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        # The fixed schema fields and all offered actions stay intact. Optional
        # prose is reduced before any matched source window is discarded.
        packet["source_evidence"] = packet["source_evidence"][:1]
        packet["pending_decision"]["candidates"] = [
            {
                "target_id": row.get("target_id", ""),
                "concept_id": row.get("concept_id", ""),
                "title": _compact_text(row.get("title"), 160),
                "topic": _compact_text(row.get("topic"), 160),
                "coverage": "",
                "gap": "",
            }
            for row in packet["pending_decision"]["candidates"][:4]
        ]
        packet["mmd_windows"] = [
            {**row, "text": _compact_text(row.get("text"), 2_000)}
            for row in packet["mmd_windows"][:1]
        ]
    if len(json.dumps(packet, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        raise ValueError("autonomous semantic resolution packet exceeds safety cap")

    evidence_refs = {
        str(row.get("evidence_id") or "")
        for row in packet["source_evidence"]
        if row.get("evidence_id")
    }
    evidence_refs.update(
        str(row.get("evidence_id") or "")
        for row in packet["mmd_windows"]
        if row.get("evidence_id") and row.get("issue_match") is True
    )
    return packet, evidence_refs


def _response_schema(
    pending: Mapping[str, Any],
    evidence_refs: set[str],
) -> dict[str, Any]:
    choices = list(dict.fromkeys(
        str(row.get("choice") or "")
        for row in pending.get("options") or []
        if isinstance(row, Mapping) and row.get("choice")
    ))
    target_ids = list(dict.fromkeys(
        str(row.get("target_id") or "")
        for row in [*(pending.get("candidates") or []), *(pending.get("options") or [])]
        if isinstance(row, Mapping) and row.get("target_id")
    ))
    concept_ids = list(dict.fromkeys(
        str(row.get("concept_id") or row.get("target_concept_id") or "")
        for row in [*(pending.get("candidates") or []), *(pending.get("options") or [])]
        if isinstance(row, Mapping)
        and (row.get("concept_id") or row.get("target_concept_id"))
    ))
    refs = sorted(evidence_refs) or ["NONE"]
    return {
        "name": "aegis_autonomous_semantic_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "disposition": {
                    "type": "string", "enum": ["apply", "ask_human"]
                },
                "choice": {"type": "string", "enum": ["", *choices]},
                "target_id": {
                    "type": "string", "enum": ["", *target_ids]
                },
                "target_concept_id": {
                    "type": "string", "enum": ["", *concept_ids]
                },
                "instruction": {"type": "string", "maxLength": 4000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string", "maxLength": 8000},
                "evidence_refs": {
                    "type": "array",
                    "maxItems": min(100, len(refs)),
                    "items": {"type": "string", "enum": refs},
                },
                "uncertainties": {
                    "type": "array", "maxItems": 20,
                    "items": {"type": "string", "maxLength": 1000},
                },
            },
            "required": [
                "disposition", "choice", "target_id", "target_concept_id",
                "instruction", "confidence", "reason", "evidence_refs",
                "uncertainties",
            ],
            "additionalProperties": False,
        },
    }


def _provider_call(
    *,
    packet: dict[str, Any],
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    return phase22._openai_multimodal_json(
        system=(
            "You are Aegis's bounded semantic resolution agent. Use only the "
            "evidence and opaque IDs supplied. Do not repair content, invent a "
            "target, weaken an integrity rule, or choose source replacement. "
            "Select apply only when exactly one offered action is clearly "
            "supported; otherwise ask_human and state the unresolved ambiguity."
        ),
        prompt=json.dumps(packet, ensure_ascii=False),
        pages=[],
        response_schema=response_schema,
        purpose="concept_validation",
        max_tokens=3_000,
        single_attempt=True,
    )


def _gate_for(pending: Mapping[str, Any], *, choice: str = ""):
    kind = _normal(pending.get("kind"))
    phase = _normal(pending.get("phase")).replace("phase ", "")
    if (
        any(token in kind for token in (
            "source", "grounding", "blueprint", "topology"
        ))
        or phase in {"3.1", "3.2", "31", "32"}
        or choice == "create_new"
    ):
        return confidence_policy.ConfidenceGate.SOURCE_CRITICAL
    return confidence_policy.ConfidenceGate.SEMANTIC


def _validate_response(
    response: Mapping[str, Any],
    *,
    pending: Mapping[str, Any],
    evidence_refs: set[str],
) -> ResolutionResult:
    reason = _compact_text(response.get("reason"), 8_000).strip()
    try:
        confidence = float(response.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    refs = tuple(dict.fromkeys(
        str(value) for value in response.get("evidence_refs") or [] if value
    ))
    uncertainties = [
        str(value).strip()
        for value in response.get("uncertainties") or []
        if str(value).strip()
    ]
    if str(response.get("disposition") or "") != "apply":
        return ResolutionResult(
            status="escalated",
            reason=reason or "The resolution agent found more than one defensible action.",
            confidence=confidence,
            evidence_refs=refs,
        )

    choice = str(response.get("choice") or "")
    instruction = str(response.get("instruction") or "").strip()
    target_id = str(response.get("target_id") or "")
    target_concept_id = str(response.get("target_concept_id") or "")
    offered = {
        str(row.get("choice") or ""): row
        for row in pending.get("options") or []
        if isinstance(row, Mapping) and row.get("choice")
    }
    if choice not in offered:
        return ResolutionResult("escalated", "The agent selected an action that was not offered.")
    if choice in {"replace_source", "custom_instruction"}:
        return ResolutionResult(
            "escalated",
            "This action changes the source or introduces new instructions and requires the user.",
            confidence,
            refs,
        )
    if instruction:
        return ResolutionResult(
            "escalated",
            "The agent added an instruction outside the bounded offered action.",
            confidence,
            refs,
        )
    if uncertainties:
        return ResolutionResult(
            "escalated",
            reason or "The resolution agent reported unresolved uncertainty.",
            confidence,
            refs,
        )
    if not refs or any(ref not in evidence_refs for ref in refs):
        return ResolutionResult(
            "escalated",
            "The proposed action was not tied to supplied evidence.",
            confidence,
            refs,
        )
    gate = _gate_for(pending, choice=choice)
    if (
        gate is confidence_policy.ConfidenceGate.SOURCE_CRITICAL
        and not any(ref.startswith("MMD-WINDOW-") for ref in refs)
    ):
        return ResolutionResult(
            "escalated",
            "This source-critical action lacks issue-matched canonical MMD evidence.",
            confidence,
            refs,
        )
    if not confidence_policy.accepts(confidence, gate):
        threshold = confidence_policy.threshold_text(gate)
        return ResolutionResult(
            "escalated",
            reason or f"Confidence did not meet the {threshold} safety threshold.",
            confidence,
            refs,
        )

    option = offered[choice]
    candidate_target_ids = {
        str(row.get("target_id") or "")
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("target_id")
    }
    candidate_concept_ids = {
        str(row.get("concept_id") or "")
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("concept_id")
    }
    if choice == "accept_recommended":
        expected = str(option.get("target_id") or "")
        if target_id and target_id != expected:
            return ResolutionResult("escalated", "The recommended target identity changed.")
        target_id = expected
    if choice in {"accept_recommended", "select_candidate"}:
        if not target_id or target_id not in candidate_target_ids:
            return ResolutionResult(
                "escalated",
                "The selected target is not a supplied candidate.",
            )
    if choice == "expand_existing" and not target_concept_id:
        target_concept_id = str(option.get("target_concept_id") or "")
    if choice in {"expand_existing", "select_existing"}:
        if not target_concept_id or target_concept_id not in candidate_concept_ids:
            return ResolutionResult(
                "escalated",
                "The selected concept is not a supplied candidate.",
            )
    if not reason:
        return ResolutionResult(
            "escalated",
            "The proposed action did not include an evidence-based reason.",
        )
    return ResolutionResult(
        status="resolved",
        reason=reason,
        confidence=confidence,
        evidence_refs=refs,
        choice=choice,
        target_id=target_id,
        target_concept_id=target_concept_id,
    )


def resolve_pending(
    pending: Mapping[str, Any],
    *,
    source_text: str,
    checkpoint: Mapping[str, Any] | None,
    provider: Callable[..., Mapping[str, Any]] | None = None,
) -> ResolutionResult:
    """Make one physical provider request and deterministically vet its action."""

    try:
        packet, evidence_refs = build_packet(
            pending,
            source_text=source_text,
            checkpoint=checkpoint,
        )
        schema = _response_schema(packet["pending_decision"], evidence_refs)
        raw = (provider or _provider_call)(
            packet=packet,
            response_schema=schema,
        )
    except Exception as exc:
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The one bounded autonomous review could not complete "
                f"({type(exc).__name__}). Your saved decision is still available."
            ),
        )
    if not isinstance(raw, Mapping):
        return ResolutionResult(
            status="unavailable",
            reason="The autonomous review returned no structured decision.",
        )
    try:
        return _validate_response(
            raw,
            pending=pending,
            evidence_refs=evidence_refs,
        )
    except Exception as exc:
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The bounded autonomous review could not be validated "
                f"({type(exc).__name__}). Your saved decision is still available."
            ),
        )
