"""Phase 3.4 resilient structured-output and hierarchy batching contract.

A live Social Science acceptance run verified every original PDF page, then
failed before semantic graph creation because the all-section hierarchy response
reached ``max_completion_tokens``.  The shared multimodal helper retried the
same request three times with the same budget, so Resume could only reproduce
the truncation.

This contract makes structured output resumable rather than brittle:

* completion-limit responses are retried with a larger budget and progressively
  more compact reasoning while preserving the same strict JSON schema;
* a fully parsed strict-schema object is accepted even when the provider reports
  ``finish_reason=length`` after the final closing brace;
* Phase 3 hierarchy classification and criticism are split into contiguous,
  independently cached batches.  Every batch sees a compact, complete section
  directory, while body evidence is limited to its targets and the adjacent or
  ancestor sections needed to interpret them.  Any canonical section ID remains
  available as a parent without repeatedly sending every non-target body;
* every section is still classified and independently criticised exactly once;
  payload bounding does not substitute a deterministic role guess for review;
* successful batches survive a later failure, so Resume starts at the first
  uncached batch rather than replaying the chapter;
* progress and terminal errors name the actual purpose/schema instead of calling
  every downstream structured request "source adjudication".

No source text, QID, Figure, image, KaTeX, topic identity, or confidence gate is
relaxed.  This is transport/protocol recovery only; semantic decisions remain
independently verified by the existing Phase 3 critic.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

from .. import config
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3
from . import progress

_CONTRACT_VERSION = 2
_TURNOVER_VERSION = "phase3.4-structured-output-turnover-2"
_HIERARCHY_CACHE_FILENAME = "source.phase34-hierarchy-batch-cache.json"
_ALLOWED_HIERARCHY_ROLES = (
    "chapter_heading",
    "main_topic",
    "subtopic",
    "activity",
    "exercise",
    "source_extract",
    "glossary",
    "box",
    "content_heading",
    "other",
)
_REASONING_DOWNGRADE = {
    "xhigh": "high",
    "high": "medium",
    "medium": "low",
    "low": "low",
}


class _TerminalStructuredOutputError(RuntimeError):
    """A structured response could not be completed within the bounded turnover."""


def _purpose_label(purpose: object) -> str:
    return str(purpose or "structured output").replace("_", " ")


def _schema_name(response_schema: dict[str, Any]) -> str:
    return str(response_schema.get("name") or "unnamed_json_schema")


def _completion_cap(initial: int) -> int:
    configured = max(
        4000,
        int(os.environ.get(
            "AEGIS_STRUCTURED_JSON_MAX_COMPLETION_TOKENS", "65536"
        )),
    )
    return max(int(initial), min(131072, configured))


def _max_truncation_retries() -> int:
    return max(
        1,
        min(
            6,
            int(os.environ.get(
                "AEGIS_STRUCTURED_JSON_MAX_TRUNCATION_RETRIES", "4"
            )),
        ),
    )


def _hierarchy_batch_size() -> int:
    return max(
        4,
        min(
            24,
            int(os.environ.get("AEGIS_PHASE3_HIERARCHY_BATCH_SECTIONS", "10")),
        ),
    )


def _target_evidence_chars() -> int:
    return max(
        900,
        min(
            24_000,
            int(os.environ.get(
                "AEGIS_PHASE34_TARGET_SECTION_EVIDENCE_CHARS", "6000"
            )),
        ),
    )


def _context_evidence_chars() -> int:
    return max(
        300,
        min(
            6_000,
            int(os.environ.get(
                "AEGIS_PHASE34_CONTEXT_SECTION_EVIDENCE_CHARS", "1600"
            )),
        ),
    )


def _adjacent_section_count() -> int:
    return max(
        0,
        min(
            2,
            int(os.environ.get("AEGIS_PHASE34_ADJACENT_SECTIONS", "1")),
        ),
    )


def _downgraded_reasoning(value: object, steps: int) -> str:
    effort = str(value or "")
    for _ in range(max(0, int(steps))):
        effort = _REASONING_DOWNGRADE.get(effort, effort)
    return effort


def _resilient_openai_multimodal_json(
    *,
    system: str,
    prompt: str,
    pages: list[phase22.EvidencePage],
    response_schema: dict[str, Any],
    purpose: str = "source_adjudication",
    max_tokens: int = phase22._MAX_OUTPUT_TOKENS,
    single_attempt: bool = False,
) -> dict[str, Any]:
    """Strict-schema call with adaptive completion-limit turnover.

    The first request is byte-for-byte policy-compatible with the old helper.
    Only a real truncation changes the budget/reasoning policy.  Transient API
    failures retain the existing Aegis retry/backoff contract.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        OpenAI,
        RateLimitError,
    )
    from aegis_pipeline.openai_policy import chat_request_policy
    from . import generation, openai_usage

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for page in pages:
        extracted = phase22._compact(page.text, limit=6000)
        content.append({
            "type": "text",
            "text": (
                f"{page.evidence_id} (audit PDF page {page.page_number})\n"
                f"Extracted text layer:\n{extracted}"
            ),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": page.image_data_url, "detail": "high"},
        })

    transient_errors = (
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
    )
    base_policy = chat_request_policy(purpose, model=config.OPENAI_MODEL)
    client = OpenAI(timeout=config.OPENAI_REQUEST_TIMEOUT_SECONDS, max_retries=0)
    gate = generation._get_openai_gate()
    current_budget = max(1000, int(max_tokens or 0))
    completion_cap = _completion_cap(current_budget)
    max_turnovers = 0 if single_attempt else _max_truncation_retries()
    truncations = 0
    transient = 0
    hard = 0
    last_error: Exception | None = None
    schema = _schema_name(response_schema)
    label = _purpose_label(purpose)

    while True:
        request_policy = dict(base_policy)
        if truncations and request_policy.get("reasoning_effort"):
            request_policy["reasoning_effort"] = _downgraded_reasoning(
                request_policy["reasoning_effort"], truncations
            )
        recovery_instruction = ""
        if truncations:
            recovery_instruction = (
                "\n\nSTRUCTURED OUTPUT RECOVERY: The previous strict-schema "
                "response reached its completion limit. Return the same JSON "
                "schema completely and compactly. Keep evidence/reason strings "
                "brief, do not restate the input, and spend tokens on completing "
                "every required opaque ID exactly once."
            )
        try:
            generation._acquire_openai_slot(gate, purpose=purpose)
            try:
                response = client.chat.completions.create(
                    **request_policy,
                    messages=[
                        {
                            "role": "system",
                            "content": str(system or "") + recovery_instruction,
                        },
                        {"role": "user", "content": content},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": response_schema,
                    },
                    max_completion_tokens=current_budget,
                )
            finally:
                gate.release()
            try:
                openai_usage.record_response(
                    response, requested_model=request_policy["model"]
                )
            except Exception:
                pass

            choice = response.choices[0]
            finish_reason = str(getattr(choice, "finish_reason", "") or "")
            message = choice.message
            refusal = str(getattr(message, "refusal", "") or "").strip()
            raw = getattr(message, "content", "") or ""
            if not isinstance(raw, str):
                raw = str(raw)

            parsed: object | None = None
            parse_error: Exception | None = None
            try:
                parsed = json.loads(raw or "{}")
            except Exception as exc:  # JSONDecodeError plus compatible clients
                parse_error = exc

            # Some providers mark the response as length-limited after emitting a
            # complete closing brace.  A parsed strict-schema object is usable; all
            # semantic/identity validators still run at the caller boundary.
            if isinstance(parsed, dict):
                if finish_reason == "length":
                    progress.log(
                        "Accepted a complete strict-schema response despite a "
                        f"provider length marker ({label}, schema {schema}).",
                        level="warning",
                    )
                return parsed

            if refusal:
                raise ValueError(
                    f"OpenAI {label} refused schema {schema}: {refusal[:500]}"
                )

            if finish_reason == "length":
                if (
                    truncations >= max_turnovers
                    or current_budget >= completion_cap
                ):
                    raise _TerminalStructuredOutputError(
                        f"OpenAI {label} schema {schema} remained truncated after "
                        f"{truncations + 1} completion-limit response(s) at "
                        f"{current_budget} tokens"
                    )
                previous_budget = current_budget
                current_budget = min(
                    completion_cap,
                    max(previous_budget * 2, previous_budget + 4000),
                )
                truncations += 1
                progress.log(
                    f"OpenAI {label} schema {schema} reached the completion limit "
                    f"at {previous_budget} tokens; retrying with "
                    f"{current_budget} tokens and compact structured reasoning "
                    f"({truncations}/{max_turnovers}).",
                    level="warning",
                )
                continue

            if parsed is not None:
                raise ValueError(
                    f"OpenAI {label} schema {schema} returned "
                    f"{type(parsed).__name__}, expected an object"
                )
            if finish_reason and finish_reason != "stop":
                raise ValueError(
                    f"OpenAI {label} schema {schema} ended with "
                    f"finish_reason={finish_reason!r}"
                )
            raise ValueError(
                f"OpenAI {label} schema {schema} returned invalid JSON: "
                f"{parse_error!r}"
            )
        except _TerminalStructuredOutputError:
            raise
        except generation.OpenAIQueueTimeoutError:
            raise
        except transient_errors as exc:
            code = generation._openai_error_code(exc)
            if code == "insufficient_quota":
                raise RuntimeError(
                    f"OpenAI quota exhausted during {label}"
                ) from exc
            if single_attempt:
                raise RuntimeError(
                    f"OpenAI {label} single attempt failed: {exc!r}"
                ) from exc
            transient += 1
            last_error = exc
            if transient > config.OPENAI_TRANSIENT_RETRIES:
                raise RuntimeError(
                    f"OpenAI unavailable during {label} after "
                    f"{transient - 1} transient retries: {exc!r}"
                ) from exc
            delay = generation._transient_backoff(exc, transient)
            progress.log(
                f"OpenAI {label} busy ({type(exc).__name__}); retrying in "
                f"{delay:.0f}s.",
                level="warning",
            )
            time.sleep(delay)
        except Exception as exc:
            if single_attempt:
                raise RuntimeError(
                    f"OpenAI {label} schema {schema} single attempt failed: "
                    f"{exc!r}"
                ) from exc
            hard += 1
            last_error = exc
            if hard >= 3:
                raise RuntimeError(
                    f"OpenAI {label} schema {schema} failed after "
                    f"{hard} bounded protocol attempt(s): {last_error!r}"
                ) from exc
            progress.log(
                f"OpenAI {label} schema {schema} returned an unusable structured "
                f"response ({exc}); retrying bounded protocol attempt "
                f"{hard + 1}/3.",
                level="warning",
            )
            time.sleep(2)


def _artifact_dir() -> Path | None:
    session = phase3.active_session()
    if not isinstance(session, dict) or not session.get("artifact_dir"):
        return None
    return Path(session["artifact_dir"])


def _read_cache() -> dict[str, Any]:
    directory = _artifact_dir()
    if directory is None:
        return {}
    path = directory / _HIERARCHY_CACHE_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("version") != _TURNOVER_VERSION:
        return {}
    return value


def _write_cache_entry(key: str, value: dict[str, Any]) -> None:
    directory = _artifact_dir()
    if directory is None:
        return
    path = directory / _HIERARCHY_CACHE_FILENAME
    cache = _read_cache()
    if cache.get("version") != _TURNOVER_VERSION:
        cache = {"version": _TURNOVER_VERSION, "entries": {}}
    cache.setdefault("entries", {})[key] = {
        "created_at": time.time(),
        "model": str(config.OPENAI_MODEL),
        "result": copy.deepcopy(value),
    }
    phase3._atomic_write(
        path,
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _cache_key(
    *,
    kind: str,
    payload: dict[str, Any],
    target_ids: list[str],
) -> str:
    return phase3._sha256_json({
        "version": _TURNOVER_VERSION,
        "compiler": phase3.COMPILER_VERSION,
        "model": str(config.OPENAI_MODEL),
        "kind": kind,
        "payload_sha256": phase3._sha256_json(payload),
        "target_ids": list(target_ids),
    })


def _cached_result(key: str) -> dict[str, Any] | None:
    entry = (_read_cache().get("entries") or {}).get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("result"), dict):
        return None
    return copy.deepcopy(entry["result"])


def _hierarchy_schema(
    target_ids: list[str],
    all_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "phase34_semantic_hierarchy_batch",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "minItems": len(target_ids),
                    "maxItems": len(target_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_id": {
                                "type": "string",
                                "enum": target_ids,
                            },
                            "role": {
                                "type": "string",
                                "enum": list(_ALLOWED_HIERARCHY_ROLES),
                            },
                            "parent_section_id": {
                                "type": "string",
                                "enum": ["", *all_ids],
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 4,
                            },
                        },
                        "required": [
                            "section_id",
                            "role",
                            "parent_section_id",
                            "confidence",
                            "evidence",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["sections"],
            "additionalProperties": False,
        },
    }


def _hierarchy_critic_schema(
    target_ids: list[str],
    all_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "phase34_semantic_hierarchy_critic_batch",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["verified", "repair_required"],
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "repairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_id": {
                                "type": "string",
                                "enum": target_ids,
                            },
                            "role": {
                                "type": "string",
                                "enum": list(_ALLOWED_HIERARCHY_ROLES),
                            },
                            "parent_section_id": {
                                "type": "string",
                                "enum": ["", *all_ids],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": [
                            "section_id",
                            "role",
                            "parent_section_id",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            "required": ["verdict", "confidence", "repairs", "issues"],
            "additionalProperties": False,
        },
    }


def _validate_hierarchy_batch(
    value: dict[str, Any],
    *,
    target_ids: list[str],
    all_ids: set[str],
) -> list[dict[str, Any]]:
    rows = value.get("sections") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Phase 3.4 hierarchy batch returned no sections array")
    target = set(target_ids)
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Phase 3.4 hierarchy batch returned a non-object row")
        section_id = str(row.get("section_id") or "")
        parent_id = str(row.get("parent_section_id") or "")
        role = str(row.get("role") or "")
        if section_id not in target or section_id in seen:
            raise ValueError(
                "Phase 3.4 hierarchy batch changed or duplicated section identity"
            )
        if parent_id and parent_id not in all_ids:
            raise ValueError("Phase 3.4 hierarchy batch invented a parent section")
        if role not in _ALLOWED_HIERARCHY_ROLES:
            raise ValueError("Phase 3.4 hierarchy batch returned an invalid role")
        seen.add(section_id)
        normalized.append(copy.deepcopy(row))
    if seen != target:
        raise ValueError(
            "Phase 3.4 hierarchy batch omitted section IDs: "
            + ", ".join(sorted(target - seen))
        )
    by_id = {str(row["section_id"]): row for row in normalized}
    return [by_id[section_id] for section_id in target_ids]


def _validate_critic_batch(
    value: dict[str, Any],
    *,
    target_ids: list[str],
    all_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Phase 3.4 hierarchy critic returned no object")
    verdict = str(value.get("verdict") or "")
    if verdict not in {"verified", "repair_required"}:
        raise ValueError("Phase 3.4 hierarchy critic returned an invalid verdict")
    target = set(target_ids)
    seen: set[str] = set()
    repairs: list[dict[str, Any]] = []
    for row in value.get("repairs") or []:
        if not isinstance(row, dict):
            raise ValueError("Phase 3.4 hierarchy critic returned a non-object repair")
        section_id = str(row.get("section_id") or "")
        parent_id = str(row.get("parent_section_id") or "")
        role = str(row.get("role") or "")
        if section_id not in target or section_id in seen:
            raise ValueError(
                "Phase 3.4 hierarchy critic repaired an untargeted/duplicate ID"
            )
        if parent_id and parent_id not in all_ids:
            raise ValueError("Phase 3.4 hierarchy critic invented a parent section")
        if role not in _ALLOWED_HIERARCHY_ROLES:
            raise ValueError("Phase 3.4 hierarchy critic returned an invalid role")
        seen.add(section_id)
        repairs.append(copy.deepcopy(row))
    confidence = float(value.get("confidence") or 0.0)
    issues = [
        str(item).strip()
        for item in value.get("issues") or []
        if str(item).strip()
    ]
    return {
        "verdict": verdict,
        "confidence": confidence,
        "repairs": repairs,
        "issues": issues,
    }


_SECTION_DIRECTORY_FIELDS = (
    "section_id",
    "title",
    "level",
    "source_order",
    "source_start",
    "baseline_role",
    "parent_section_id",
    "phase3_virtual",
    "structural_number",
)


def _ordered_payload_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact ordered hierarchy directory carried by Phase 3."""
    return [
        copy.deepcopy(row)
        for row in payload.get("sections") or []
        if isinstance(row, dict) and str(row.get("section_id") or "")
    ]


def _section_directory(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project every section to identity/structure only, never body text."""
    directory: list[dict[str, Any]] = []
    for position, section in enumerate(sections, start=1):
        row = {
            key: copy.deepcopy(section[key])
            for key in _SECTION_DIRECTORY_FIELDS
            if key in section
        }
        row["section_id"] = str(section.get("section_id") or "")
        row.setdefault("title", str(section.get("title") or ""))
        row.setdefault("level", int(section.get("level") or 1))
        row.setdefault(
            "source_order", int(section.get("source_order") or position)
        )
        row.setdefault("source_start", int(section.get("source_start") or 0))
        row.setdefault(
            "baseline_role", str(section.get("baseline_role") or "other")
        )
        directory.append(row)
    return directory


def _canonical_section_bodies(
    sections: list[dict[str, Any]],
    selected_ids: set[str],
) -> dict[str, str]:
    """Read durable section bodies without embedding them in the directory.

    The ordinary classification payload intentionally carries only a short
    excerpt.  When a live canonical session is available, this helper projects
    the exact durable block text for the small target/context set selected
    below.  The canonical artifact is never modified or clipped.
    """
    bodies = {
        str(section.get("section_id") or ""): str(section.get("excerpt") or "")
        for section in sections
        if str(section.get("section_id") or "") in selected_ids
    }
    session = phase3.active_session()
    canonical = (
        session.get("canonical")
        if isinstance(session, dict) and isinstance(session.get("canonical"), dict)
        else None
    )
    if not isinstance(canonical, dict):
        return bodies
    blocks_by_id = {
        str(block.get("block_id") or ""): block
        for block in canonical.get("blocks") or []
        if isinstance(block, dict) and str(block.get("block_id") or "")
    }
    canonical_sections = {
        str(section.get("section_id") or ""): section
        for section in canonical.get("sections") or []
        if isinstance(section, dict) and str(section.get("section_id") or "")
    }
    for section_id in bodies:
        canonical_section = canonical_sections.get(section_id)
        if canonical_section is None:
            continue
        pieces: list[str] = []
        for block_id in canonical_section.get("block_ids") or []:
            block = blocks_by_id.get(str(block_id))
            if not block or str(block.get("kind") or "") in {
                "layout",
                "heading",
                "navigation",
            }:
                continue
            text = str(
                block.get("display_text") or block.get("raw_text") or ""
            ).strip()
            if text:
                pieces.append(" ".join(text.split()))
        if pieces:
            bodies[section_id] = " ".join(pieces)
    return bodies


def _ancestor_by_section_id(
    sections: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Infer physical heading ancestors without making a semantic decision."""
    ancestors: dict[str, list[str]] = {}
    stack: list[tuple[int, str]] = []
    known = {
        str(section.get("section_id") or "")
        for section in sections
    }
    for section in sections:
        section_id = str(section.get("section_id") or "")
        level = max(1, int(section.get("level") or 1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        lineage = [value for _depth, value in stack]
        declared = str(section.get("parent_section_id") or "")
        if declared and declared in known and declared not in lineage:
            lineage.append(declared)
        ancestors[section_id] = lineage
        stack.append((level, section_id))
    return ancestors


def _context_section_ids(
    sections: list[dict[str, Any]],
    target_ids: list[str],
    *,
    proposed_hierarchy: list[dict[str, Any]] | None = None,
) -> tuple[set[str], set[str]]:
    """Return target-adjacent and ancestor/declared-parent evidence IDs."""
    ordered_ids = [str(row.get("section_id") or "") for row in sections]
    index_by_id = {section_id: index for index, section_id in enumerate(ordered_ids)}
    target = set(target_ids)
    adjacent: set[str] = set()
    ancestors: set[str] = set()
    lineage_by_id = _ancestor_by_section_id(sections)
    radius = _adjacent_section_count()
    for section_id in target_ids:
        index = index_by_id.get(section_id)
        if index is None:
            continue
        for offset in range(1, radius + 1):
            if index - offset >= 0:
                adjacent.add(ordered_ids[index - offset])
            if index + offset < len(ordered_ids):
                adjacent.add(ordered_ids[index + offset])
        ancestors.update(lineage_by_id.get(section_id) or [])

    # A critic must see the body evidence for a proposed parent even when it is
    # outside the immediate physical window.  The full directory still makes
    # every other ID available as a legal parent.
    for row in proposed_hierarchy or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("section_id") or "") not in target:
            continue
        parent_id = str(row.get("parent_section_id") or "")
        if parent_id in index_by_id:
            ancestors.add(parent_id)
            ancestors.update(lineage_by_id.get(parent_id) or [])
    adjacent -= target
    ancestors -= target
    adjacent -= ancestors
    return adjacent, ancestors


def _bounded_body(value: object, *, limit: int) -> tuple[str, bool]:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text, True
    # Retain both the opening role cue and terminal continuation.  The exact
    # durable body remains addressable by section ID and hash for a targeted
    # follow-up/human decision; unrelated section bodies are never sent.
    tail = max(120, limit // 4)
    head = max(120, limit - tail - 45)
    return (
        text[:head].rstrip()
        + " [AEGIS BOUNDED SECTION EVIDENCE] "
        + text[-tail:].lstrip(),
        False,
    )


def _section_evidence(
    sections: list[dict[str, Any]],
    target_ids: list[str],
    *,
    proposed_hierarchy: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    target = set(target_ids)
    adjacent, ancestors = _context_section_ids(
        sections,
        target_ids,
        proposed_hierarchy=proposed_hierarchy,
    )
    selected = target | adjacent | ancestors
    bodies = _canonical_section_bodies(sections, selected)
    evidence: list[dict[str, Any]] = []
    for section in sections:
        section_id = str(section.get("section_id") or "")
        if section_id not in selected:
            continue
        body = bodies.get(section_id, "")
        normalized_body = " ".join(str(body or "").split())
        is_target = section_id in target
        bounded, complete = _bounded_body(
            normalized_body,
            limit=(
                _target_evidence_chars()
                if is_target
                else _context_evidence_chars()
            ),
        )
        relationship = (
            "target"
            if is_target
            else "ancestor_or_proposed_parent"
            if section_id in ancestors
            else "adjacent"
        )
        evidence.append({
            "section_id": section_id,
            "relationship": relationship,
            "title": str(section.get("title") or ""),
            "level": int(section.get("level") or 1),
            "baseline_role": str(section.get("baseline_role") or "other"),
            "body_evidence": bounded,
            "body_chars": len(normalized_body),
            "body_sha256": phase3._sha256_text(normalized_body),
            "body_complete": complete,
        })
    return evidence


def _compact_proposed_hierarchy(
    rows: list[dict[str, Any]],
    target_ids: list[str],
) -> list[dict[str, Any]]:
    target = set(target_ids)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact = {
            "section_id": str(row.get("section_id") or ""),
            "role": str(row.get("role") or ""),
            "parent_section_id": str(row.get("parent_section_id") or ""),
            "confidence": float(row.get("confidence") or 0.0),
        }
        if compact["section_id"] in target:
            compact["evidence"] = [
                str(value)
                for value in row.get("evidence") or []
                if str(value)
            ][:4]
        out.append(compact)
    return out


def _classification_payload_for_batch(
    payload: dict[str, Any],
    target_ids: list[str],
) -> dict[str, Any]:
    sections = _ordered_payload_sections(payload)
    return {
        "metadata": copy.deepcopy(payload.get("metadata") or {}),
        "section_directory": _section_directory(sections),
        "section_evidence": _section_evidence(sections, target_ids),
        "verified_pdf_headings": copy.deepcopy(
            payload.get("verified_pdf_headings") or []
        ),
        "allowed_roles": list(_ALLOWED_HIERARCHY_ROLES),
        "target_section_ids": list(target_ids),
        "instruction": (
            "Classify only target_section_ids and return each target exactly once. "
            "section_directory is the complete ordered identity/structure map; "
            "section_evidence contains body text only for targets and necessary "
            "adjacent/ancestor context. A target may use any canonical section_id "
            "from section_directory as parent_section_id."
        ),
    }


def _critic_payload_for_batch(
    payload: dict[str, Any],
    target_ids: list[str],
) -> dict[str, Any]:
    sections = _ordered_payload_sections(payload)
    proposed = [
        row for row in payload.get("proposed_hierarchy") or []
        if isinstance(row, dict)
    ]
    return {
        "metadata": copy.deepcopy(payload.get("metadata") or {}),
        "section_directory": _section_directory(sections),
        "section_evidence": _section_evidence(
            sections,
            target_ids,
            proposed_hierarchy=proposed,
        ),
        "verified_pdf_headings": copy.deepcopy(
            payload.get("verified_pdf_headings") or []
        ),
        "allowed_roles": list(_ALLOWED_HIERARCHY_ROLES),
        "proposed_hierarchy": _compact_proposed_hierarchy(
            proposed,
            target_ids,
        ),
        "target_section_ids": list(target_ids),
        "instruction": (
            "Audit only target_section_ids. Use the complete compact directory "
            "and proposed hierarchy for identity/order/parent context, and use "
            "section_evidence for target plus necessary adjacent/ancestor body "
            "evidence. Return repairs only for targets. Parent IDs may refer to "
            "any canonical section in section_directory."
        ),
    }


def _classify_hierarchy_batched(payload: dict[str, Any]) -> dict[str, Any]:
    sections = [
        row for row in payload.get("sections") or []
        if isinstance(row, dict) and str(row.get("section_id") or "")
    ]
    all_ids = [str(row.get("section_id") or "") for row in sections]
    if not all_ids:
        return {"sections": []}
    size = _hierarchy_batch_size()
    batch_count = (len(all_ids) + size - 1) // size
    progress.log(
        f"Phase 3 hierarchy classification will process {len(all_ids)} section(s) "
        f"in {batch_count} resumable strict-schema batch(es).",
        level="warning" if batch_count > 1 else "success",
    )
    combined: dict[str, dict[str, Any]] = {}
    all_id_set = set(all_ids)
    system = (
        "You are the Aegis Phase 3.4 semantic hierarchy classifier. Classify "
        "only the supplied target_section_ids. Use section_directory as the "
        "complete ordered identity/structure map and section_evidence as the "
        "target plus adjacent/ancestor body context. Main topics "
        "are durable teaching divisions. Activity, Discuss, Source, glossary, "
        "boxes, review questions, and editorial labels are not main topics. "
        "Preserve physical order, use only opaque IDs, and return every target "
        "exactly once with concise evidence."
    )
    for batch_index, start in enumerate(range(0, len(all_ids), size), start=1):
        target_ids = all_ids[start:start + size]
        batch_payload = _classification_payload_for_batch(payload, target_ids)
        key = _cache_key(
            kind="classifier",
            payload=batch_payload,
            target_ids=target_ids,
        )
        cached = _cached_result(key)
        cache_state = "hit" if cached is not None else "miss"
        response = cached
        if response is None:
            response = phase22._openai_multimodal_json(
                system=system,
                prompt=json.dumps(batch_payload, ensure_ascii=False, indent=2),
                pages=[],
                response_schema=_hierarchy_schema(target_ids, all_ids),
                purpose="concept_mapping",
                max_tokens=max(6000, min(18000, len(target_ids) * 480 + 3000)),
            )
        rows = _validate_hierarchy_batch(
            response,
            target_ids=target_ids,
            all_ids=all_id_set,
        )
        if cached is None:
            _write_cache_entry(key, {"sections": rows})
        for row in rows:
            combined[str(row["section_id"])] = row
        progress.log(
            f"Verified Phase 3 hierarchy classification batch "
            f"{batch_index}/{batch_count} ({len(target_ids)} section(s); "
            f"cache {cache_state}).",
            level="success",
        )
    return {"sections": [combined[section_id] for section_id in all_ids]}


def _critic_hierarchy_batched(payload: dict[str, Any]) -> dict[str, Any]:
    sections = [
        row for row in payload.get("sections") or []
        if isinstance(row, dict) and str(row.get("section_id") or "")
    ]
    all_ids = [str(row.get("section_id") or "") for row in sections]
    if not all_ids:
        return {
            "verdict": "verified",
            "confidence": 1.0,
            "repairs": [],
            "issues": [],
        }
    size = _hierarchy_batch_size()
    batch_count = (len(all_ids) + size - 1) // size
    progress.log(
        f"Phase 3 hierarchy criticism will independently audit {len(all_ids)} "
        f"section(s) in {batch_count} resumable batch(es).",
        level="warning" if batch_count > 1 else "success",
    )
    all_id_set = set(all_ids)
    all_repairs: list[dict[str, Any]] = []
    all_issues: list[str] = []
    confidence = 1.0
    needs_repair = False
    system = (
        "You are the independent Aegis Phase 3.4 hierarchy critic. Audit only "
        "target_section_ids against the complete compact section_directory, "
        "selected section_evidence, and proposed hierarchy. Detect promoted "
        "activities/sources/glossaries, lost numbered "
        "parents, wrong parent links, and order drift. Use only supplied IDs. "
        "Return concise repairs only for targeted IDs and never rewrite source."
    )
    for batch_index, start in enumerate(range(0, len(all_ids), size), start=1):
        target_ids = all_ids[start:start + size]
        batch_payload = _critic_payload_for_batch(payload, target_ids)
        key = _cache_key(
            kind="critic",
            payload=batch_payload,
            target_ids=target_ids,
        )
        cached = _cached_result(key)
        cache_state = "hit" if cached is not None else "miss"
        response = cached
        if response is None:
            response = phase22._openai_multimodal_json(
                system=system,
                prompt=json.dumps(batch_payload, ensure_ascii=False, indent=2),
                pages=[],
                response_schema=_hierarchy_critic_schema(target_ids, all_ids),
                purpose="concept_validation",
                max_tokens=max(5000, min(14000, len(target_ids) * 360 + 2500)),
            )
        normalized = _validate_critic_batch(
            response,
            target_ids=target_ids,
            all_ids=all_id_set,
        )
        if cached is None:
            _write_cache_entry(key, normalized)
        confidence = min(confidence, float(normalized["confidence"]))
        if (
            normalized["verdict"] == "repair_required"
            or normalized["repairs"]
            or normalized["issues"]
        ):
            needs_repair = True
        all_repairs.extend(copy.deepcopy(normalized["repairs"]))
        all_issues.extend(normalized["issues"])
        progress.log(
            f"Verified Phase 3 hierarchy critic batch {batch_index}/{batch_count} "
            f"({len(target_ids)} section(s); cache {cache_state}).",
            level="success",
        )
    return {
        "verdict": "repair_required" if needs_repair else "verified",
        "confidence": confidence,
        "repairs": all_repairs,
        "issues": list(dict.fromkeys(all_issues)),
    }


def install() -> None:
    if (
        getattr(phase22, "_PHASE34_STRUCTURED_OUTPUT_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    phase22._PHASE34_ORIGINAL_OPENAI_MULTIMODAL_JSON = (
        phase22._openai_multimodal_json
    )
    phase3._PHASE34_ORIGINAL_CLASSIFY_HIERARCHY = (
        phase3._classify_hierarchy_via_openai
    )
    phase3._PHASE34_ORIGINAL_CRITIC_HIERARCHY = (
        phase3._critic_hierarchy_via_openai
    )

    phase22._openai_multimodal_json = _resilient_openai_multimodal_json
    phase3._classify_hierarchy_via_openai = _classify_hierarchy_batched
    phase3._critic_hierarchy_via_openai = _critic_hierarchy_batched
    phase22._PHASE34_STRUCTURED_OUTPUT_CONTRACT_VERSION = _CONTRACT_VERSION
