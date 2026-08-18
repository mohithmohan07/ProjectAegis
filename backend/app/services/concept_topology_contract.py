"""The 81% seam into the rewritten Phase 3, plus rich-text safeguards.

Type mining still happens early because it can reveal missing method
concepts. Type allocation, however, belongs entirely to the rewritten
Phase 3 (Host decides, Assemble renders) behind the sealed boundary
envelope: ``_run_rewritten_phase3`` is the one place the pipeline
crosses the 81% boundary. The lossless rich-text canonicalization and
exact defect diagnostics here guard every final-row boundary.

The module is installed from ``app.services`` so the contract applies to every Build
Concepts entry point without duplicating the large generation pipeline.
"""
from __future__ import annotations

import copy
import re
from functools import wraps
from types import ModuleType
from typing import Any

_CONTRACT_VERSION = 1

# A model/JSON boundary can return the two characters ``\n`` instead of a line
# break. Convert only delimiter-shaped occurrences. TeX commands such as ``\nu``
# and ``\neq`` do not match this expression.
_LITERAL_LINEBREAK_RE = re.compile(
    r"\\n(?=(?:\s|Achieving\s+Mastery\b|(?:Miscellaneous\s+)?Type\s+\d{1,2}:|"
    r"Case\s+\d{1,2}:|Examples?(?:\s+0*\d+)?\s*:|"
    r"Misconception(?:s)?\b|Error\s+Analysis\b|Activity/Info\s+Hub\b))",
    re.IGNORECASE,
)


def _normalize_literal_linebreaks(value: str) -> str:
    return _LITERAL_LINEBREAK_RE.sub("\n", str(value or ""))


PRELEARN_SNAPSHOT = "source.phase3-prelearn-capture.json"


def restored_prerequisites() -> dict[str, Any] | None:
    """The Phase 03 capture of the run this job's checkpoint came from.

    A restored ``final_content_ready`` checkpoint skips the whole
    rewritten Phase 3, so the in-memory carry below never exists — but
    the run that produced the checkpoint wrote its capture beside the
    decision store (``runner._snapshot_prelearn``). Read it back rather
    than re-billing the run, and return None when there is nothing to
    read so the caller can record the absence EXPLICITLY: "no capture in
    this process" must never be readable as "this chapter has no
    prerequisites" (R4).
    """
    import json
    from pathlib import Path

    from . import canonical_source_phase3 as phase3_core

    session = phase3_core.active_session() or {}
    artifact_dir = session.get("artifact_dir") if isinstance(
        session, dict
    ) else None
    if not artifact_dir:
        return None
    try:
        payload = json.loads(
            (Path(artifact_dir) / PRELEARN_SNAPSHOT).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_rewritten_phase3(
    generation: ModuleType,
    out: list[dict],
    kwargs: dict[str, Any],
    *,
    carry: dict[str, Any] | None = None,
) -> list[dict]:
    """Route everything after the 81% boundary through the rewritten Phase 3.

    Seals the boundary envelope from exactly the material this seam holds
    (docs/phase3-rewrite-spec.md §3), runs Settle → Host → Assemble with
    the decision store in the job's artifact directory, and returns the
    assembled rows — Types embedded in the house format, QIDs routed —
    for the unchanged deposit and release chain downstream.

    ``carry`` is the run's second exit. The return value stays exactly
    ``result["records"]`` — byte-identical for the Post lane, which is
    the only thing the deposit and release chain reads — while every
    OTHER key the run produced (the Phase 03 ``prerequisites`` capture
    above all, doc §4/Q3) is copied into the caller's dict. Before this
    parameter existed those keys were discarded here, so material built
    inside the run had no way out at all.
    """
    import json

    from pathlib import Path

    from . import canonical_source_phase3 as phase3_core
    from .phase3 import envelope as p3_envelope
    from .phase3 import runner as p3_runner

    session = phase3_core.active_session() or {}
    graph = phase3_core.active_graph() or {}
    store_dir = None
    envelope_path = None
    artifact_dir = session.get("artifact_dir")
    if artifact_dir:
        store_dir = Path(artifact_dir) / "phase3-decisions"
        envelope_path = Path(artifact_dir) / "source.phase3-envelope.json"

    # Decide-once extends to the envelope itself: resume-time inventory
    # refreshes and re-mined Type coverage produce a semantically
    # equivalent but byte-different envelope, which changes every
    # decision key and re-bills the entire run (~40 minutes instead of a
    # free replay). Reuse the sealed envelope while the source contract
    # and the boundary skeleton are unchanged.
    skeleton_sha = phase3_core._sha256_json(list(out))
    env = None
    if envelope_path is not None and envelope_path.exists():
        try:
            wrapper = json.loads(envelope_path.read_text(encoding="utf-8"))
            stored = p3_envelope.validate(wrapper.get("envelope") or {})
            from . import grounding_certificate as _gc

            if (
                str(wrapper.get("boundary_skeleton_sha256") or "")
                == skeleton_sha
                and str(stored.get("source_contract_hash") or "")
                == str(graph.get("source_contract_hash") or "")
                # The topology itself must match too: a re-derived
                # semantic graph with the same source contract would
                # otherwise replay decisions sealed against a graph
                # that no longer exists, and the deposit-time drift
                # check would refuse the payload much later.
                and _gc.semantic_topology_sha256(
                    stored.get("graph") or {}
                )
                == _gc.semantic_topology_sha256(graph)
                # The Architect's instruction identity joins the reuse
                # comparison (docs/aegis-restructure.md §8.1): a changed
                # instruction set must never silently replay decisions
                # sealed under the previous instructions.
                and str(
                    (stored.get("metadata") or {}).get(
                        "instruction_set_sha256"
                    ) or ""
                )
                == str(
                    (kwargs.get("meta") or {}).get(
                        "instruction_set_sha256"
                    ) or ""
                )
            ):
                env = stored
                generation.progress.log(
                    "Reusing the sealed Phase 3 envelope "
                    f"{str(env.get('envelope_sha256'))[:12]}; every stored "
                    "decision replays without a model call.",
                    level="success",
                )
        except Exception:  # noqa: BLE001 - a stale artifact never blocks
            env = None
    if env is None:
        env = p3_envelope.build(
            graph=graph,
            canonical=session.get("canonical") or {},
            skeleton_rows=list(out),
            inventory=kwargs.get("question_task_inventory") or {},
            mined_types=kwargs.get("mined_types") or {},
            metadata=kwargs.get("meta") or {},
        )
        if envelope_path is not None:
            try:
                envelope_path.write_text(
                    json.dumps(
                        {
                            "boundary_skeleton_sha256": skeleton_sha,
                            "envelope": env,
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass  # persistence is best-effort; the run proceeds
    result = p3_runner.run(env, store_dir=store_dir)
    summary = result["summary"]
    generation.progress.log(
        "Rewritten Phase 3 complete: "
        f"{summary['row_count']} row(s) settled and assembled, "
        f"{summary['routed_qids']} QID(s) routed, "
        f"{summary['unrouted_items']} inventory item(s) unrouted, "
        f"{summary['flagged_row_count']} row(s) carrying review flags.",
        level="success",
    )
    if carry is not None:
        for key, value in result.items():
            if key != "records":
                carry[key] = copy.deepcopy(value)
    return result["records"]


def _topology_signature(generation: ModuleType, records: list[dict]) -> tuple:
    """Semantic row topology, excluding every renderable Type/Hub field."""
    return tuple(
        (
            str(record.get("topic") or "").strip(),
            str(record.get("parent_concept") or "").strip(),
            str(
                record.get("concept_title") or record.get("concept") or ""
            ).strip(),
            bool(
                generation.cr.is_culmination(
                    record.get("concept_title")
                    or record.get("concept")
                    or ""
                )
            ),
        )
        for record in records
    )


def _strip_source_owned_allocations(
    generation: ModuleType, records: list[dict]
) -> list[dict]:
    """Remove stale Types and Hubs while preserving all semantic row content."""
    cleaned: list[dict] = []
    for raw in records:
        record = copy.deepcopy(raw)
        sections = [
            (label, content)
            for label, content in generation.cr.split_sections(
                record.get("concept_details") or ""
            )
            if not (
                str(label or "").strip().lower().startswith("type")
                or generation.cr.is_activity_hub_label(label)
            )
        ]
        record["concept_details"] = generation.cr.join_sections(sections)
        record.pop("_activity_hub_qids", None)
        cleaned.append(record)
    return cleaned


def _safe_display_text(generation: ModuleType, value: str) -> str:
    """Normalize presentation only; never paraphrase source wording."""
    text = _normalize_literal_linebreaks(value)
    text = generation.kr.canonicalize_rich_text(text)
    repaired = generation.kr.repair_unwrapped_math(text)
    if generation.kr.unwrap_katex(repaired) != generation.kr.unwrap_katex(text):
        raise RuntimeError(
            "deterministic source display repair changed the unwrapped source text"
        )
    return generation.kr.canonicalize_rich_text(repaired).strip()


def _canonicalize_concept_rows(
    generation: ModuleType, records: list[dict]
) -> list[dict]:
    """Canonicalize rows and add only provably lossless math wrappers."""
    original = generation._TOPOLOGY_CONTRACT_ORIGINAL_CANONICALIZE_ROWS
    normalized = [copy.deepcopy(record) for record in records]
    for record in normalized:
        if "concept_details" in record:
            record["concept_details"] = _normalize_literal_linebreaks(
                record.get("concept_details") or ""
            )
    out = original(normalized)
    for record in out:
        details = str(record.get("concept_details") or "")
        defects = set(generation.kr.rich_text_issues(details))
        if not defects or not defects.issubset(
            {"raw_latex", "raw_math_expression"}
        ):
            continue
        repaired = generation.kr.repair_unwrapped_math(details)
        if (
            repaired != details
            and not generation.kr.rich_text_issues(repaired)
            and generation.kr.unwrap_katex(repaired)
            == generation.kr.unwrap_katex(details)
        ):
            record["concept_details"] = repaired
    return out


def _protected_spans(generation: ModuleType, text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = list(
        generation.kr._markdown_code_ranges(text))
    for pattern in (
        generation.kr._KATEX_TAG_RE,
        generation.kr._IMAGE_TAG_RE,
        generation.kr._MARKDOWN_LINK_RE,
    ):
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def _is_unprotected(position: int, spans: list[tuple[int, int]]) -> bool:
    return not any(start <= position < end for start, end in spans)


def rich_text_defect_detail(
    generation: ModuleType, details: str
) -> dict[str, Any]:
    """Return the exact first malformed token, section, offset, and context."""
    value = str(details or "")
    issues = generation.kr.rich_text_issues(value)
    if not issues:
        return {}
    spans = _protected_spans(generation, value)
    candidates: list[tuple[int, str, str]] = []

    for pattern in (
        generation.kr._RAW_LATEX_RE,
        generation.kr._RAW_SCRIPT_TAIL_RE,
    ):
        match = next(
            (
                item
                for item in pattern.finditer(value)
                if _is_unprotected(item.start(), spans)
            ),
            None,
        )
        if match is not None:
            candidates.append((match.start(), "raw_latex", match.group(0)))

    for pattern in (
        *generation.kr._RAW_BLOCK_MATH_PATTERNS,
        generation.kr._SINGLE_DOLLAR_MATH_RE,
    ):
        match = next(
            (
                item
                for item in pattern.finditer(value)
                if _is_unprotected(item.start(), spans)
            ),
            None,
        )
        if match is not None:
            candidates.append(
                (match.start(), "raw_math_delimiter", match.group(0))
            )

    if "raw_math_expression" in issues:
        match = next(
            (
                item
                for item in generation.kr._RAW_EQUATION_RE.finditer(value)
                if _is_unprotected(item.start(), spans)
            ),
            None,
        )
        if match is not None:
            candidates.append(
                (match.start(), "raw_math_expression", match.group(0))
            )

    position, code, matched = min(
        candidates, default=(0, issues[0], "")
    )
    section = "concept_details"
    cursor = 0
    for label, content in generation.cr.split_sections(value):
        start = value.find(content, cursor)
        if start < 0:
            continue
        end = start + len(content)
        if start <= position <= end:
            section = str(label or "concept_details").strip()
            break
        cursor = end
    left = max(0, position - 90)
    right = min(len(value), position + max(1, len(matched)) + 110)
    return {
        "defect": code,
        "match": matched,
        "offset": position,
        "section": section,
        "context": value[left:right],
    }


def install(generation: ModuleType) -> None:
    """Install the contract exactly once."""
    if (
        getattr(generation, "_CONCEPT_TOPOLOGY_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    # Keep originals visible so regression tests can isolate each boundary.
    generation._TOPOLOGY_CONTRACT_ORIGINAL_RUN_STAGES = (
        generation._run_live_concept_pre_final_stages
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT = (
        generation._inventory_task_text
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_HUB_NOTE = (
        generation._compact_activity_hub_note
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_CANONICALIZE_ROWS = (
        generation._canonicalize_concept_rich_text
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT = (
        generation._validation_error_context
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS = (
        generation._final_checkpoint_refresh_reasons
    )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT)
    def inventory_task_text(item: dict) -> str:
        source = generation._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT(item)
        return _safe_display_text(generation, source)

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_HUB_NOTE)
    def compact_hub_note(item: dict) -> str:
        source = generation._TOPOLOGY_CONTRACT_ORIGINAL_HUB_NOTE(item)
        return _safe_display_text(generation, source)

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT)
    def validation_error_context(records: list[dict], error: dict):
        row_index, title, field, snippet = (
            generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT(
                records, error
            )
        )
        if error.get("code") != "rich_text_format" or not (
            0 <= row_index < len(records)
        ):
            return row_index, title, field, snippet
        detail = rich_text_defect_detail(
            generation,
            records[row_index].get(
                error.get("field") or "concept_details", ""
            ),
        )
        if not detail:
            return row_index, title, field, snippet
        return (
            row_index,
            title,
            field,
            (
                f"section={detail['section']!r}; "
                f"defect={detail['defect']!r}; offset={detail['offset']}; "
                f"match={detail['match']!r}; context="
                f"{generation._diagnostic_snippet(detail['context'])!r}"
            ),
        )

    generation._inventory_task_text = inventory_task_text
    generation._compact_activity_hub_note = compact_hub_note
    generation._canonicalize_concept_rich_text = (
        lambda records: _canonicalize_concept_rows(generation, records)
    )
    generation._validation_error_context = validation_error_context
    generation._CONCEPT_TOPOLOGY_CONTRACT_VERSION = _CONTRACT_VERSION
