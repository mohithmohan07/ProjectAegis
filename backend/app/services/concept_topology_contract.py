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
PREMAP_SNAPSHOT = "source.phase3-prelearn-map.json"
PREQUESTIONS_SNAPSHOT = "source.phase3-prelearn-questions.json"


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


PHASE3_ENVELOPE_SNAPSHOT = "source.phase3-envelope.json"


def _sidecar_identity_defect(directory: "Path", pre_map: object) -> str:
    """The recorded map's run identity against the envelope beside it.

    Register Q29: the sidecar restore used to read whatever directory the
    process-scoped session pointed at and hand the result over as THIS
    run's Pre authority. The map now records the source contract it was
    sealed on (``premap.run_identity``); when the same directory holds a
    sealed envelope, the two must name one source. A map that recorded
    nothing (written before the field existed) or a directory with no
    envelope leaves nothing to compare — dormant, stated, never guessed.
    Only the source contract is compared: the envelope seal legitimately
    moves when the Architect's instruction set is reassembled for the
    same source, and that is a replay decision, not another run.
    """
    import json

    from . import generation

    identity = generation.pre_release_run_identity(pre_map)
    if identity is None:
        return ""
    try:
        wrapper = json.loads(
            (directory / PHASE3_ENVELOPE_SNAPSHOT).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return ""
    env = (
        wrapper.get("envelope")
        if isinstance(wrapper, dict) and "envelope" in wrapper
        else wrapper
    )
    if not isinstance(env, dict):
        return ""
    recorded = str(identity.get("source_contract_hash") or "").strip()
    current = str(env.get("source_contract_hash") or "").strip()
    if not recorded or not current or recorded == current:
        return ""
    return (
        f"{PREMAP_SNAPSHOT} was authored for another source than the "
        f"envelope beside it (source contract {recorded[:12]}… on the map, "
        f"{current[:12]}… in {PHASE3_ENVELOPE_SNAPSHOT}); the recorded Pre "
        "map is not this run's authority"
    )


def restored_pre_release(
    artifact_dir: "str | Path | None" = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read the recorded Pre map/questions for a terminal-checkpoint resume.

    A legacy ``final_content_ready`` entry has no in-checkpoint Pre authority,
    so its two sidecars are the only faithful shortcut around replaying Phase
    3.  Presence is mechanical: an authored empty mapping is valid, while a
    missing or unreadable file is not interpreted as "the chapter needs no
    Pre-Learning".

    ``artifact_dir`` is the directory of the JOB being restored, handed over
    by the caller (register Q29). It used to be read off the process-scoped
    Phase 3 session alone, which is how a run could restore another job's
    sidecars as its own Pre authority. The session is now only the fallback
    for a caller that holds no job, and the recorded map's source contract
    is checked against the envelope in the same directory either way.
    """

    import json
    from pathlib import Path

    from . import canonical_source_phase3 as phase3_core

    if not artifact_dir:
        session = phase3_core.active_session() or {}
        artifact_dir = session.get("artifact_dir") if isinstance(
            session, dict
        ) else None
    if not artifact_dir:
        return None, []

    loaded: dict[str, dict[str, Any]] = {}
    defects: list[str] = []
    for field, filename in (
        ("pre_map", PREMAP_SNAPSHOT),
        ("pre_questions", PREQUESTIONS_SNAPSHOT),
    ):
        path = Path(artifact_dir) / filename
        try:
            if not path.is_file():
                defects.append(f"{filename} is absent")
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            defects.append(
                f"{filename} could not be read "
                f"({type(exc).__name__}: {exc})"
            )
            continue
        if not isinstance(value, dict):
            defects.append(
                f"{filename} contains {type(value).__name__}, not an object"
            )
            continue
        loaded[field] = value

    if "pre_map" in loaded:
        identity_defect = _sidecar_identity_defect(
            Path(artifact_dir), loaded["pre_map"]
        )
        if identity_defect:
            defects.append(identity_defect)
            # Another run's map recovers nothing for this one: neither
            # the map nor a Q4 replay keyed on it is this run's authority.
            return None, defects

    if "pre_map" in loaded and "pre_questions" not in loaded:
        # Only Q4 is missing. Re-enter that exact pass over the recorded map
        # and sealed envelope instead of replaying all of Phase 3. The kernel
        # recomputes the original decision keys, so a healthy store returns
        # the already-paid decisions; when the store is absent, the ordinary
        # model-backed pass authors the missing questions. Nothing here
        # derives questions from Post content.
        try:
            from .phase3 import envelope as envelope_mod
            from .phase3 import kernel
            from .phase3 import prequestions
            from .phase3 import runner

            wrapper = json.loads(
                (Path(artifact_dir) / "source.phase3-envelope.json")
                .read_text(encoding="utf-8")
            )
            env = envelope_mod.validate(
                wrapper.get("envelope")
                if isinstance(wrapper, dict) and "envelope" in wrapper
                else wrapper
            )
            decision_store = kernel.DecisionStore(
                Path(artifact_dir) / "phase3-decisions"
            )

            class _ReplayCacheMiss(RuntimeError):
                pass

            def cache_miss(_request):
                raise _ReplayCacheMiss()

            try:
                # Supplying a provider avoids the live-credential preflight;
                # it is never called when every exact decide-once key exists.
                recovered = prequestions.build(
                    env,
                    loaded["pre_map"],
                    provider=cache_miss,
                    author_provider=cache_miss,
                    critic=None,
                    store=decision_store,
                )
            except _ReplayCacheMiss:
                # At least one paid decision is genuinely absent. Re-enter the
                # ordinary live pass so the model, not recovery code, authors
                # only that missing semantic output.
                recovered = prequestions.build(
                    env,
                    loaded["pre_map"],
                    store=decision_store,
                )
            write_status = runner._atomic_pre_snapshot(
                recovered,
                Path(artifact_dir) / "phase3-decisions",
                PREQUESTIONS_SNAPSHOT,
            )
            loaded["pre_questions"] = recovered
            defects = [
                defect for defect in defects
                if not defect.startswith(PREQUESTIONS_SNAPSHOT)
            ]
        except Exception as exc:  # noqa: BLE001 - caller performs full replay
            defects.append(
                "selective Pre-question replay failed "
                f"({type(exc).__name__}: {exc})"
            )
            write_status = None
    else:
        write_status = None

    if defects or set(loaded) != {"pre_map", "pre_questions"}:
        return None, defects
    return {
        **loaded,
        "snapshot_writes": {
            "map": {"filename": PREMAP_SNAPSHOT, "state": "legacy_read"},
            "questions": write_status or {
                "filename": PREQUESTIONS_SNAPSHOT, "state": "legacy_read",
            },
        },
    }, []



def _inventory_qid_set(inventory: object) -> tuple[str, ...]:
    """The sorted QID identity of one Question/Task Inventory.

    Pure identity mechanics: which QIDs exist, nothing about what they
    mean. Two inventories with the same QID set may differ byte-wise
    (resume refreshes rewrite text/anchors) and still share sealed
    Phase 3 decisions; a different SET can never be covered by them.
    """
    items = (
        inventory.get("items") if isinstance(inventory, dict) else None
    ) or []
    return tuple(sorted({
        str(item.get("qid") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }))


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
    from . import model_provider

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

            sealed_qids = _inventory_qid_set(stored.get("inventory"))
            current_qids = _inventory_qid_set(
                kwargs.get("question_task_inventory")
            )
            identity_matches = (
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
            )
            if identity_matches and sealed_qids != current_qids:
                # [measured] job "Patterns" (owner report, 2026-08-29):
                # the resume-time inventory refresh yielded a different
                # QID set than the sealed envelope's, but the seal was
                # reused anyway. Host then replayed certifications for
                # the OLD set only, so the placement-certification
                # completeness rule (exact set equality) rejected every
                # terminal checkpoint the run wrote — the release staged
                # non-terminal, the Master gate refused Outputs 02/04,
                # and each further Resume repeated the same near-free
                # loop forever. A changed QID set can never be covered
                # by the sealed decisions: re-key Phase 3 once, pay for
                # the delta, and converge.
                added = len(set(current_qids) - set(sealed_qids))
                removed = len(set(sealed_qids) - set(current_qids))
                generation.progress.log(
                    "The refreshed Question/Task Inventory's QID set "
                    f"differs from the sealed Phase 3 envelope's ({added} "
                    f"added, {removed} removed): the sealed decisions "
                    "cannot certify the current inventory, so Phase 3 "
                    "re-runs under a new envelope instead of looping on a "
                    "non-terminal checkpoint. Unchanged decisions that "
                    "share content may still replay from the store.",
                    level="warning",
                )
            elif identity_matches:
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
        from .phase3 import pre_coverage as p3_coverage
        from . import prelearning_capture_policy
        from . import source_topic_policy

        env = p3_envelope.build(
            graph=graph,
            canonical=session.get("canonical") or {},
            skeleton_rows=list(out),
            inventory=kwargs.get("question_task_inventory") or {},
            mined_types=kwargs.get("mined_types") or {},
            # Register Q30: the owner's Pre coverage rule is a frozen run
            # variable, recorded on the envelope so it is inside the seal
            # and every decision key. A reused sealed envelope keeps the
            # rule (or the absence) it was sealed with — decide-once.
            metadata={
                **p3_coverage.stamp(kwargs.get("meta") or {}),
                **({model_provider.PROFILE_KEY: model_provider.bound_profile()}
                   if model_provider.bound_profile() is not None else {}),
                # New Phase 3 boundaries adopt the complete-evidence/atomic
                # capture contract. Reused envelopes above remain verbatim,
                # including the absence of this key on historical runs.
                prelearning_capture_policy.KEY: prelearning_capture_policy.VERSION,
                prelearning_capture_policy.BOUNDARY_KEY: prelearning_capture_policy.BOUNDARY_VERSION,
                "source_topic_policy_version": source_topic_policy.SOURCE_TOPIC_POLICY_VERSION,
            },
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
