"""Pass 3 — Assemble: deterministic projection, zero model calls.

Everything semantic was decided by Settle and Host. Assemble is pure
bookkeeping: it renders each concept's hosted Types into the house
``// Types:`` section of ``concept_details``, routes every QID to its
host row, and accounts for every inventory item. Any inconsistency here
is a bug in our code, so this pass raises immediately — there is no
provider to correct, and deliberately no Fixer either: with the kernel
fixer seam upstream (Q13) these raises should be unreachable; the one
exception is the deposit-pipeline fixpoint, where convergent drift is
adopted mechanically with review flags and only oscillation raises.
"""
from __future__ import annotations

import copy
import os
import re
from typing import Any, Mapping

_ANALYSIS_SPLIT = re.compile(
    r"\s*//\s*Misconception/?\s*Error Analysis:\s*", re.IGNORECASE
)

# Stamped into every sealed row's ``_source_grounding_version``. The value
# is kept byte-identical to the one the retired legacy grounding stack
# stamped so resumed checkpoints and recorded artifacts keep verifying;
# certificates recompute from the record, so the string is provenance,
# never a gate.
GROUNDING_VERSION = "phase3.8-certified-required-grounding-5"

# Row-private audit field (Q1): the LA-item ids the chapter analysis
# inventory allotted to this row. Rides the release payload for the
# reviewer's audit; stripped before DB upload (build_concepts_release).
# The rendered §5 line carries NO visible id — the ids live here only.
ANALYSIS_ALLOTMENTS_FIELD = "_aegis_analysis_allotments"


class AssemblyError(RuntimeError):
    """Deterministic assembly hit an internal inconsistency."""


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _host_key(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("topic_id") or entry.get("_semantic_topic_id") or ""),
        _normal(entry.get("concept_title")).casefold(),
    )


def _type_catalog(env: Mapping[str, Any]) -> tuple[dict, dict]:
    types: dict[str, dict[str, str]] = {}
    cases: dict[tuple[str, str], dict[str, Any]] = {}
    for mined in env["mined_types"].get("types") or []:
        if not isinstance(mined, Mapping):
            continue
        type_id = str(mined.get("type_id") or "")
        types[type_id] = {
            "title": _normal(mined.get("type_title")),
            "definition": _normal(mined.get("type_description")),
        }
        for case in mined.get("case_prompts") or []:
            if not isinstance(case, Mapping):
                continue
            examples = []
            for row in case.get("examples") or []:
                if not isinstance(row, Mapping):
                    continue
                prompt = _normal(row.get("example_prompt"))
                if not prompt:
                    continue
                examples.append({
                    "qid": str(row.get("source_question_id") or ""),
                    "prompt": prompt,
                })
            cases[(type_id, str(case.get("case_id") or ""))] = {
                "title": _normal(case.get("case_title")),
                "examples": examples,
            }
    return types, cases


def render_types_section(
    hosted: Mapping[str, Mapping[str, list[str]]],
    *,
    types: Mapping[str, Mapping[str, str]],
    cases: Mapping[tuple[str, str], Mapping[str, Any]],
) -> str:
    """Render the Types section for one concept's hosted case examples.

    ``hosted`` maps type_id -> case_id -> the example prompts whose own
    QUESTIONS were placed on this concept. A Case whose questions span
    concepts appears on each destination with only that concept's
    examples — the visible text always agrees with per-question routing.
    """

    def _ordinal(identifier: str, prefix: str) -> int:
        # Continuous chapter-wide numbering: TYPE-0013 is "Type 13" on
        # every concept that hosts it, mirroring the mined taxonomy.
        try:
            return int(identifier.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            raise AssemblyError(
                f"cannot number {prefix} from identifier {identifier!r}"
                " (unreachable after fixer seam — report if hit)"
            )

    pieces: list[str] = []
    for type_id in sorted(hosted):
        mined = types.get(type_id)
        if mined is None:
            raise AssemblyError(
                f"hosted unit references unknown {type_id}"
                " (unreachable after fixer seam — report if hit)"
            )
        piece = f"Type {_ordinal(type_id, 'Type'):02d}: {mined['title']}"
        if mined["definition"]:
            piece += f" — {mined['definition']}"
        for case_id in sorted(hosted[type_id]):
            if not case_id:
                for example in hosted[type_id][case_id]:
                    piece += f" Example: {example}"
                continue
            case = cases.get((type_id, case_id))
            if case is None:
                raise AssemblyError(
                    f"hosted unit references unknown {type_id}::{case_id}"
                    " (unreachable after fixer seam — report if hit)"
                )
            piece += f" Case {_ordinal(case_id, 'Case'):02d}: {case['title']}"
            for example in hosted[type_id][case_id]:
                piece += f" Example: {example}"
        pieces.append(piece)
    return " ".join(pieces)


def _join_analysis_texts(texts: list[str]) -> str:
    """Merge several allotted item texts into one section component.

    Item-id order is preserved by the caller; a missing terminal
    punctuation mark gets a period so the merged body reads as
    sentences (the same join the deterministic normalizer applies).
    """
    joined = ""
    for text in texts:
        text = _normal(text)
        if not text:
            continue
        if joined and not re.search(r"[.!?;]\s*$", joined):
            joined += "."
        joined = f"{joined} {text}" if joined else text
    return joined


def stamp_analysis_allotments(
    ordered_rows: list[dict[str, Any]],
    analysis: Mapping[str, Any] | None,
    row_by_place_id: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Q1 stamping: the section exists only where an item was allotted.

    ``ordered_rows`` is the positional ``[*settled, *new_concepts]``
    list; ``row_by_place_id`` resolves the analyse pass's positional
    concept ids to those same row objects. For every allotted row the
    item texts are merged into ONE canonical
    ``// Misconception/ Error Analysis:`` section (Misconceptions
    before Error Analysis, items in item-id order) with NO visible
    item id — the ids ride the row-private audit field instead. Any
    row arriving with a learner-analysis section not backed by the
    inventory (a legacy or checkpoint row) has it removed with a
    review flag recording the removed text verbatim — generated
    prose, never source content, and never removed silently.

    Returns ``(allotments, inventory_items)`` for coverage accounting.
    R4 is mechanics here: every inventory item must be allotted to
    exactly one existing, non-culmination row or assembly raises.
    """

    from .. import concept_refiner as cr
    from .. import katex_rules as kr

    result = dict(analysis or {})
    inventory = [
        dict(item)
        for item in result.get("inventory") or []
        if isinstance(item, Mapping)
    ]
    allotments = {
        str(item_id): str(concept_id)
        for item_id, concept_id in (result.get("allotments") or {}).items()
    }
    item_flags = {
        str(item_id): [str(flag) for flag in flags]
        for item_id, flags in (result.get("review_flags") or {}).items()
        if isinstance(flags, list)
    }
    item_by_id = {
        str(item.get("item_id") or ""): item for item in inventory
    }
    unallotted = sorted(set(item_by_id) - set(allotments))
    if unallotted:
        raise AssemblyError(
            "analysis inventory item(s) reached assemble without an "
            "allotment (R4 exact-once): " + ", ".join(unallotted)
            + " (unreachable after fixer seam — report if hit)"
        )
    unknown = sorted(set(allotments) - set(item_by_id))
    if unknown:
        raise AssemblyError(
            "analysis allotment(s) name item(s) missing from the "
            "inventory: " + ", ".join(unknown)
            + " (unreachable after fixer seam — report if hit)"
        )

    items_by_row: dict[int, list[dict[str, Any]]] = {}
    for item_id in sorted(allotments):
        row = row_by_place_id.get(allotments[item_id])
        if row is None:
            raise AssemblyError(
                f"analysis allotment for {item_id} names a concept that "
                f"does not exist: {allotments[item_id][:40]}"
                " (unreachable after fixer seam — report if hit)"
            )
        if cr.is_culmination(_normal(row.get("concept_title"))):
            raise AssemblyError(
                f"analysis allotment for {item_id} names a culmination "
                "row; culminations never receive an inventory item"
                " (unreachable after fixer seam — report if hit)"
            )
        items_by_row.setdefault(id(row), []).append(item_by_id[item_id])

    for row in ordered_rows:
        details = str(row.get("concept_details") or "")
        sections = cr.split_sections(details)
        existing = [
            (label, content)
            for label, content in sections
            if cr.is_learner_analysis_label(label)
        ]
        items = items_by_row.get(id(row))
        if not existing and items is None:
            continue  # unallotted row without a section: nothing to do
        kept = [
            (label, content)
            for label, content in sections
            if not cr.is_learner_analysis_label(label)
        ]
        removed = " ".join(
            f"{label}: {content}".strip()
            for label, content in existing
            if _normal(content)
        )
        if removed:
            # R4/Q1: the inventory is the only analysis mechanism, so a
            # section it does not back is removed — flagged with the
            # exact text, never silently.
            row.setdefault("review_flags", []).append(
                "Q1: removed a learner-analysis section not backed by "
                "the chapter inventory (the inventory is the only "
                "analysis mechanism); removed text (verbatim): "
                + removed
            )
        if items is not None:
            misconceptions = _join_analysis_texts([
                str(item.get("text") or "")
                for item in items
                if str(item.get("kind") or "") == "misconception"
            ])
            error_analyses = _join_analysis_texts([
                str(item.get("text") or "")
                for item in items
                if str(item.get("kind") or "") == "error_analysis"
            ])
            combined: list[str] = []
            if misconceptions:
                combined.append(f"Misconceptions: {misconceptions}")
            if error_analyses:
                combined.append(f"Error Analysis: {error_analyses}")
            if combined:
                kept.append((
                    "Misconception/ Error Analysis",
                    kr.repair_unwrapped_math("; ".join(combined)),
                ))
            row[ANALYSIS_ALLOTMENTS_FIELD] = [
                str(item.get("item_id") or "") for item in items
            ]
            for item in items:
                for flag in item_flags.get(
                    str(item.get("item_id") or ""), []
                ):
                    flags = row.setdefault("review_flags", [])
                    if flag not in flags:
                        flags.append(flag)
        row["concept_details"] = cr.join_sections(kept)
    return allotments, inventory


def _inject_types(details: str, types_body: str) -> str:
    """Insert the Types section after mastery, before learner analysis."""

    if not types_body:
        return details
    parts = _ANALYSIS_SPLIT.split(details, maxsplit=1)
    injected = parts[0].rstrip() + " // Types: " + types_body
    if len(parts) == 2:
        injected += " // Misconception/ Error Analysis: " + parts[1]
    return injected


_RENDERED_CASE_SEGMENT_RE = re.compile(
    r"\bCase\s+\d{1,2}:\s*(?P<body>.*?)"
    r"(?=\b(?:Miscellaneous\s+)?Type\s+\d{1,2}:|\bCase\s+\d{1,2}:|$)",
    re.IGNORECASE | re.DOTALL,
)
_RENDERED_EXAMPLE_MARKER_RE = re.compile(
    r"\bExamples?(?:\s+0*\d+)?\s*:", re.IGNORECASE,
)


def _types_body_of(details: object) -> str:
    from .. import concept_refiner as cr

    for label, content in cr.split_sections(str(details or "")):
        if label.strip().lower().startswith("type"):
            return content.strip()
    return ""


def audit_case_uniqueness(
    records: list[Mapping[str, Any]],
    *,
    cases: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
    expected_examples: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic Q2 uniqueness audit (mechanics, no judgment).

    Closes the fan-out, text-collapse, and shell holes the map named:

    (a) each rendered (type_id, case_id) identity — recorded in the
        ``_aegis_release_type_case_routes`` audit field — appears on
        exactly ONE row (the per-destination split mints distinct
        identities, so a repeat is a defect);
    (b) each QID appears exactly once chapter-wide BY QID: once in the
        routed ``_aegis_release_qids`` markers, and — when
        ``expected_examples`` (qid + prompt) is given — its prompt
        occupies rendered Example slots exactly as many times as
        distinct QIDs carry that wording (identical wording no longer
        collapses two questions into one render);
    (c) no example-less Case shell survives: a rendered Case with no
        Example whose catalog Case HAS examples is a shell left by
        dedupe (a Case with no catalog examples is the taxonomy's own
        legitimate exampleless mark).

    Returns findings ``{"code", "message", "row_indexes", "qids"}``;
    violations are release-audit errors — recorded, never silently
    repaired.
    """

    findings: list[dict[str, Any]] = []

    identity_rows: dict[tuple[str, str], set[int]] = {}
    for index, row in enumerate(records):
        for route in row.get("_aegis_release_type_case_routes") or []:
            parts = str(route).split("::")
            if len(parts) >= 2 and parts[1]:
                identity_rows.setdefault(
                    (parts[0], parts[1]), set()
                ).add(index)
    for (type_id, case_id), indexes in sorted(identity_rows.items()):
        if len(indexes) > 1:
            titles = ", ".join(
                repr(_normal(records[i].get("concept_title"))[:60])
                for i in sorted(indexes)
            )
            findings.append({
                "code": "duplicate_case_identity",
                "message": (
                    f"case identity {type_id}::{case_id} renders on "
                    f"{len(indexes)} rows ({titles}); each rendered Case "
                    "lands on exactly one concept (Q2)"
                ),
                "row_indexes": sorted(indexes),
                "qids": [],
            })

    qid_rows: dict[str, set[int]] = {}
    for index, row in enumerate(records):
        for qid in row.get("_aegis_release_qids") or []:
            qid_rows.setdefault(str(qid), set()).add(index)
    for qid, indexes in sorted(qid_rows.items()):
        if len(indexes) > 1:
            findings.append({
                "code": "duplicate_qid_route",
                "message": (
                    f"{qid} is routed to {len(indexes)} rows; every QID "
                    "has exactly one final destination (Rule C)"
                ),
                "row_indexes": sorted(indexes),
                "qids": [qid],
            })

    if expected_examples:
        from .. import concept_validator as cv
        from .. import generation

        qids_by_key: dict[str, set[str]] = {}
        for example in expected_examples:
            qid = str(example.get("qid") or "").strip()
            prompt = str(example.get("prompt") or "")
            if not qid or not prompt:
                continue
            if cv._example_too_short(prompt):
                # Mirrors the coverage contract's participation rule: a
                # stub prompt cannot become a valid rendered Example.
                continue
            key = generation._inventory_coverage_key(prompt)
            if key:
                qids_by_key.setdefault(key, set()).add(qid)
        counts = generation._rendered_inventory_example_counts(
            [dict(row) for row in records], set(qids_by_key)
        )
        for key, qids in sorted(qids_by_key.items()):
            rendered = counts.get(key, 0)
            if rendered != len(qids):
                findings.append({
                    "code": "qid_render_count_mismatch",
                    "message": (
                        f"{len(qids)} source question(s) "
                        f"({', '.join(sorted(qids))}) carry this wording "
                        f"but it occupies {rendered} rendered Example "
                        "slot(s); each QID renders exactly once (Q2 — "
                        "coverage is keyed by QID, identical wording "
                        "never collapses)"
                    ),
                    "row_indexes": [],
                    "qids": sorted(qids),
                })

    if cases is not None:
        titles_with_examples: dict[str, list[tuple[str, str]]] = {}
        titles_without: set[str] = set()
        for (type_id, case_id), case in cases.items():
            key = _normal(case.get("title")).casefold()
            if not key:
                continue
            if case.get("examples"):
                titles_with_examples.setdefault(key, []).append(
                    (type_id, case_id)
                )
            else:
                titles_without.add(key)
        for index, row in enumerate(records):
            body = _types_body_of(row.get("concept_details"))
            if not body:
                continue
            for match in _RENDERED_CASE_SEGMENT_RE.finditer(body):
                segment = match.group("body") or ""
                if _RENDERED_EXAMPLE_MARKER_RE.search(segment):
                    continue
                title_key = _normal(segment).casefold()
                if not title_key or title_key in titles_without:
                    continue
                owners = titles_with_examples.get(title_key)
                if not owners:
                    continue
                owner_ids = ", ".join(
                    f"{type_id}::{case_id}" for type_id, case_id in owners
                )
                example_qids = sorted({
                    str(example.get("qid") or "")
                    for type_id, case_id in owners
                    for example in (
                        cases[(type_id, case_id)].get("examples") or []
                    )
                    if str(example.get("qid") or "")
                })
                carriers = sorted({
                    _normal(records[i].get("concept_title"))[:60]
                    for qid in example_qids
                    for i in qid_rows.get(qid, set())
                    if i != index
                })
                findings.append({
                    "code": "example_less_case_shell",
                    "message": (
                        f"row {index} "
                        f"({_normal(row.get('concept_title'))[:60]!r}) "
                        f"renders the example-less Case shell "
                        f"{_normal(segment)[:80]!r} ({owner_ids}); its "
                        "Example(s) live on: "
                        + (", ".join(carriers) or "no routed row")
                    ),
                    "row_indexes": [index],
                    "qids": example_qids,
                })
    return findings


def assemble(
    env: Mapping[str, Any],
    settled_rows: list[Mapping[str, Any]],
    host_result: Mapping[str, Any],
    placements: Mapping[str, Any] | None = None,
    analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project settled rows + host maps + placements into release rows.

    ``placements`` is the Phase 2.2 placement pass's output (place.py).
    Its verdicts are stamped onto the target rows as release-audit
    markers BEFORE the deposit pipeline runs, so the pipeline's hub
    normalizer renders them and every later deterministic replay (the
    deposit boundary, the Refiner's parity pipeline) sees the same
    binding riding the rows themselves.

    ``analysis`` is the Phase 2.4/4.3 chapter inventory pass's output
    (analyse.py, Q1). Its allotments are stamped the same way: the
    rendered ``// Misconception/ Error Analysis:`` section exists only
    on rows that received an item; the item ids ride the row-private
    ``_aegis_analysis_allotments`` audit field.
    """

    from . import place as place_mod
    from .. import concept_refiner as cr

    types, cases = _type_catalog(env)
    rows = [copy.deepcopy(dict(row)) for row in settled_rows]
    new_rows = [
        copy.deepcopy(dict(new_row))
        for new_row in host_result.get("new_concepts") or []
    ]
    # Resolve the place pass's positional concept ids back to row objects
    # over the SAME ordered list the pass saw ([*settled, *new_concepts]);
    # holding object references makes the resolution independent of the
    # culmination-preserving insert below.
    place_result = dict(placements or {})
    row_by_place_id = dict(zip(
        place_mod.mint_concept_ids([*rows, *new_rows]), [*rows, *new_rows]
    ))
    figure_pool = {
        str(block_id): dict(meta)
        for block_id, meta in (place_result.get("figure_pool") or {}).items()
        if isinstance(meta, Mapping)
    }
    place_flags = {
        str(ref): [str(flag) for flag in flags]
        for ref, flags in (place_result.get("review_flags") or {}).items()
        if isinstance(flags, list)
    }

    def _placed_row(item_ref: str, concept_id: str) -> dict[str, Any]:
        row = row_by_place_id.get(str(concept_id))
        if row is None:
            raise AssemblyError(
                f"placement for {item_ref} names a concept that does not "
                f"exist: {str(concept_id)[:40]}"
                " (unreachable after fixer seam — report if hit)"
            )
        return row

    for qid, concept_id in sorted(
        (place_result.get("hub_placements") or {}).items()
    ):
        row = _placed_row(str(qid), str(concept_id))
        marks = row.setdefault("_aegis_hub_placements", [])
        if str(qid) not in marks:
            marks.append(str(qid))
            marks.sort()
        for flag in place_flags.get(str(qid), []):
            row.setdefault("review_flags", []).append(flag)

    figure_dispositions: dict[str, dict[str, str]] = {}
    for block_id, verdict in sorted(
        (place_result.get("figure_placements") or {}).items()
    ):
        block_id = str(block_id)
        verdict = str(verdict)
        rationale = str(
            (place_result.get("rationales") or {}).get(block_id) or ""
        )
        if verdict == place_mod.FIGURE_DISPOSITION:
            # R4: a disposition is a recorded verdict, never a silent
            # drop — it ships in coverage and in the place snapshot.
            figure_dispositions[block_id] = {
                "disposition": verdict,
                "rationale": rationale,
            }
            continue
        row = _placed_row(block_id, verdict)
        meta = figure_pool.get(block_id) or {}
        caption = str(meta.get("caption") or "")
        marks = row.setdefault("_aegis_figure_placements", [])
        for url in meta.get("urls") or [""]:
            entry = {
                "block_id": block_id,
                "url": str(url),
                "caption": caption,
            }
            if entry not in marks:
                marks.append(entry)
        marks.sort(key=lambda entry: (
            str(entry.get("block_id") or ""), str(entry.get("url") or "")
        ))
        for flag in place_flags.get(block_id, []):
            row.setdefault("review_flags", []).append(flag)

    # Q1: stamp the chapter analysis inventory's allotments before the
    # deterministic pipeline — the rendered section exists only where an
    # item was allotted; an arriving unbacked section is removed with a
    # verbatim review flag (never silently).
    analysis_allotments, analysis_inventory = stamp_analysis_allotments(
        [*rows, *new_rows], analysis, row_by_place_id
    )

    # A new concept joins its topic BEFORE the topic's culmination row: the
    # culmination must stay last in its topic, and the certificate seals the
    # row order, so a downstream reorder would break the lineage.
    for new_row in new_rows:
        topic = _normal(new_row.get("topic")).casefold()
        insert_at = len(rows)
        for index, row in enumerate(rows):
            if (
                _normal(row.get("topic")).casefold() == topic
                and cr.is_culmination(_normal(row.get("concept_title")))
            ):
                insert_at = index
                break
        rows.insert(insert_at, new_row)

    row_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _host_key(row)
        if key in row_by_key:
            raise AssemblyError(
                "two rows share a topic and title: "
                + str(row.get("concept_title"))[:80]
                + " (unreachable after fixer seam — report if hit)"
            )
        row_by_key[key] = row

    flags_by_key: dict[tuple[str, str], list[str]] = {}
    for unit_id, entry in (host_result.get("host_map") or {}).items():
        key = _host_key(entry)
        if key not in row_by_key:
            raise AssemblyError(
                f"host entry for {unit_id} names a row that does not "
                "exist: " + str(entry.get("concept_title"))[:80]
                + " (unreachable after fixer seam — report if hit)"
            )
        for flag in entry.get("review_flags") or []:
            flags_by_key.setdefault(key, []).append(str(flag))

    # Each EXAMPLE renders at its own question's destination (qid_map),
    # so the visible Types text always agrees with per-question routing —
    # reviewers found the old whole-Case pooling contradicted correct
    # placements. A Case (or Type) whose questions span concepts appears
    # on each destination with only that concept's examples; an example
    # whose qid was never routed stays with its unit's host.
    qid_map = host_result.get("qid_map") or {}
    sections_by_key: dict[
        tuple[str, str], dict[str, dict[str, list[str]]]
    ] = {}
    routes_by_key: dict[tuple[str, str], set[str]] = {}
    case_qids_by_dest: dict[
        tuple[tuple[str, str], str, str], list[str]
    ] = {}
    for unit_id, entry in (host_result.get("host_map") or {}).items():
        parts = str(unit_id).split("::")
        type_id = parts[0]
        case_id = parts[1] if len(parts) > 1 else ""
        unit_key = _host_key(entry)
        case_examples = (cases.get((type_id, case_id)) or {}).get(
            "examples"
        ) or []
        if not case_examples:
            # A unit with no examples still marks its Type/Case on the
            # host so the taxonomy stays visible somewhere.
            sections_by_key.setdefault(unit_key, {}).setdefault(
                type_id, {}
            ).setdefault(case_id, [])
            routes_by_key.setdefault(unit_key, set()).add(str(unit_id))
            continue
        for example in case_examples:
            qid = example["qid"]
            destination = qid_map.get(qid) if qid else None
            dest_key = (
                _host_key(destination)
                if isinstance(destination, Mapping)
                else unit_key
            )
            if dest_key not in row_by_key:
                raise AssemblyError(
                    f"qid {qid} routes to a row that does not exist: "
                    + str((destination or entry).get("concept_title"))[:80]
                    + " (unreachable after fixer seam — report if hit)"
                )
            sections_by_key.setdefault(dest_key, {}).setdefault(
                type_id, {}
            ).setdefault(case_id, []).append(example["prompt"])
            routes_by_key.setdefault(dest_key, set()).add(str(unit_id))
            if qid:
                case_qids_by_dest.setdefault(
                    (dest_key, type_id, case_id), []
                ).append(qid)

    # Q2 conformance at Case/Example granularity: Host's per-QID routing
    # authority stands, so one mined Case's Examples may land on several
    # concepts. Rendering that one Case identity on several rows would
    # break "each Case lands on exactly one concept", so assemble SPLITS
    # the Case identity per destination (pure mechanics): the first
    # destination in row order keeps the original id; every further
    # destination gets a freshly minted CASE-NNNN (ordinals continue the
    # chapter's own numbering so the existing ``_ordinal`` machinery
    # renders them), with the Case definition copied verbatim. A Type
    # identity thus renders under several concepts only through distinct
    # Cases — exactly Rule B and Q2. Every split is recorded in the
    # route audit field, in coverage, and as review flags on every
    # destination row.
    row_index_by_key = {
        _host_key(row): index for index, row in enumerate(rows)
    }
    max_case_ordinal = 0
    for _type_id, existing_case_id in cases:
        try:
            max_case_ordinal = max(
                max_case_ordinal,
                int(str(existing_case_id).rsplit("-", 1)[1]),
            )
        except (IndexError, ValueError):
            continue
    case_destinations: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for dest_key, hosted in sections_by_key.items():
        for type_id, case_map in hosted.items():
            for case_id in case_map:
                if case_id and case_map[case_id]:
                    case_destinations.setdefault(
                        (type_id, case_id), []
                    ).append(dest_key)
    case_splits: list[dict[str, Any]] = []
    for (type_id, case_id), dest_keys in sorted(case_destinations.items()):
        unique_dests = sorted(
            set(dest_keys),
            key=lambda key: row_index_by_key.get(key, len(rows)),
        )
        if len(unique_dests) < 2:
            continue
        original = cases.get((type_id, case_id)) or {
            "title": "", "examples": [],
        }
        split_record: dict[str, Any] = {
            "type_id": type_id,
            "case_id": case_id,
            "destinations": [],
        }
        rendered_ids: dict[tuple[str, str], str] = {}
        for ordinal, dest_key in enumerate(unique_dests, start=1):
            hosted_cases = sections_by_key[dest_key][type_id]
            prompts_here = list(hosted_cases.get(case_id) or [])
            if ordinal == 1:
                rendered_case_id = case_id
            else:
                max_case_ordinal += 1
                rendered_case_id = f"CASE-{max_case_ordinal:04d}"
                hosted_cases[rendered_case_id] = hosted_cases.pop(case_id)
                prompt_set = set(prompts_here)
                cases[(type_id, rendered_case_id)] = {
                    # The definition is copied verbatim — a split never
                    # rewrites what the Case means.
                    "title": original.get("title") or "",
                    "examples": [
                        dict(example)
                        for example in original.get("examples") or []
                        if example.get("prompt") in prompt_set
                    ],
                }
                routes = routes_by_key.setdefault(dest_key, set())
                for route_id in sorted(routes):
                    route_parts = str(route_id).split("::")
                    if (
                        len(route_parts) > 1
                        and route_parts[0] == type_id
                        and route_parts[1] == case_id
                    ):
                        routes.discard(route_id)
                routes.add(
                    f"{type_id}::{rendered_case_id}::split-of:{case_id}"
                )
            rendered_ids[dest_key] = rendered_case_id
            split_record["destinations"].append({
                "case_id": rendered_case_id,
                "topic_id": dest_key[0],
                "concept_title": _normal(
                    row_by_key[dest_key].get("concept_title")
                ),
                "qids": sorted(
                    case_qids_by_dest.get(
                        (dest_key, type_id, case_id), []
                    )
                ),
            })
        siblings = {
            dest_key: ", ".join(
                f"{rendered_ids[other]} on "
                f"'{_normal(row_by_key[other].get('concept_title'))[:60]}'"
                for other in unique_dests
                if other != dest_key
            )
            for dest_key in unique_dests
        }
        for dest_key in unique_dests:
            row_by_key[dest_key].setdefault("review_flags", []).append(
                f"Q2 case split: {type_id}::{case_id}'s Examples route "
                f"to {len(unique_dests)} concepts, so its identity was "
                "split per destination; this row renders "
                f"{rendered_ids[dest_key]} (definition copied verbatim); "
                f"sibling Case identit(ies): {siblings[dest_key]}"
            )
        case_splits.append(split_record)

    from .. import katex_rules as kr

    for key, hosted in sections_by_key.items():
        row = row_by_key[key]
        section = render_types_section(hosted, types=types, cases=cases)
        # Mined Type/Case titles can carry bare TeX tokens (a_n, S_n) that
        # the taxonomy miner failed to wrap; the deterministic repair wraps
        # only unambiguous math and never rewrites wrapped content, so the
        # rendered section always meets the [Katex] wire contract.
        row["concept_details"] = _inject_types(
            str(row.get("concept_details") or ""),
            kr.repair_unwrapped_math(section),
        )
        row["_aegis_release_type_case_routes"] = sorted(routes_by_key[key])
    for key, flags in flags_by_key.items():
        row = row_by_key[key]
        row["review_flags"] = [*(row.get("review_flags") or []), *flags]
    for qid, entry in qid_map.items():
        key = _host_key(entry)
        if key not in row_by_key:
            raise AssemblyError(
                f"qid {qid} routes to a row that does not exist: "
                + str(entry.get("concept_title"))[:80]
                + " (unreachable after fixer seam — report if hit)"
            )
        row = row_by_key[key]
        qids = row.setdefault("_aegis_release_qids", [])
        if qid not in qids:
            qids.append(qid)
    for row in rows:
        if row.get("_aegis_release_qids"):
            row["_aegis_release_qids"] = sorted(row["_aegis_release_qids"])

    routed: list[str] = []
    unrouted: list[dict[str, str]] = []
    for item in env["inventory"].get("items") or []:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "")
        if not qid:
            continue
        if qid in qid_map:
            routed.append(qid)
        else:
            unrouted.append({
                "qid": qid,
                "source_kind": str(item.get("source_kind") or ""),
                "topic_id": str(
                    item.get("_semantic_topic_id")
                    or item.get("source_location_topic_id")
                    or ""
                ),
            })

    # The certificate seals row ORDER and row CONTENT, and the caller runs
    # deterministic content passes (learner analysis, mastery lines, the
    # canonical culmination recap, rich-text canonicalization) after this
    # boundary. Run those exact passes here, pre-seal, and require them to
    # be a fixpoint — otherwise the caller would mutate a sealed row and
    # the lineage check would correctly refuse the payload (attempt 10:
    # 'ordered grounded concept set changed after verification'). The
    # deposit boundary re-runs its own deterministic cleanup over the
    # sealed payload and requires it to be FULLY idempotent with what
    # cleared the final gate, so the sealed rows must be a fixpoint of
    # the DEPOSIT pipeline as well (staging: Title Case cleanup broke
    # row identity at the deposit-time certificate check).
    def _deposit_deterministic_pipeline(
        candidate_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from .. import concept_cleanup
        from .. import concept_refiner
        from .. import concept_validator as cv
        from .. import generation

        meta = env.get("metadata") or {}
        out_rows = [
            concept_cleanup.clean_concept_record(dict(row))
            for row in candidate_rows
        ]
        out_rows = concept_cleanup.filter_review_violations(
            out_rows,
            subject=str(meta.get("subject") or ""),
            board=str(meta.get("board") or ""),
            chapter_title=str(meta.get("chapter_title") or ""),
        )
        out_rows = concept_refiner.refine_chapter(out_rows)
        out_rows = cv.ensure_valid_learner_analysis(out_rows)
        out_rows = generation._ensure_mastery_lines_via_api(
            out_rows, meta={}, use_api=False
        )
        out_rows = generation._ensure_terminal_culmination_contract(
            out_rows
        )
        out_rows = generation._canonicalize_concept_rich_text(out_rows)
        out_rows = generation._normalize_activity_hubs_from_inventory(
            out_rows, dict(env["inventory"]), dict(env["mined_types"])
        )
        out_rows = generation._enforce_rendered_inventory_coverage(
            out_rows, dict(env["inventory"]), dict(env["mined_types"])
        )
        out_rows = generation._canonicalize_concept_rich_text(out_rows)
        out_rows = concept_refiner.renumber_types_continuously(out_rows)
        out_rows = cv.ensure_valid_learner_analysis(out_rows)
        return out_rows

    rows = _deposit_deterministic_pipeline(rows)
    replayed = _deposit_deterministic_pipeline(copy.deepcopy(rows))
    # Seam F10 (Q13): convergent drift is mechanics, not a judgment call.
    # Iterate the deterministic pipeline (bounded) toward its own
    # fixpoint; adopt a converged row set with a review flag on every
    # changed row. Only genuine oscillation — a deterministic-code
    # defect the Fixer must never paper over (it never edits code) —
    # still raises below.
    normalization_passes = 0
    while replayed != rows and normalization_passes < 2:
        if len(replayed) == len(rows):
            for index, (before, after) in enumerate(zip(rows, replayed)):
                if before == after:
                    continue
                changed_field = next(
                    (
                        field
                        for field in sorted(
                            set(before) | set(after), key=str
                        )
                        if before.get(field) != after.get(field)
                    ),
                    "",
                )
                after["review_flags"] = [
                    *(after.get("review_flags") or []),
                    "assembly output normalized by the deterministic "
                    "deposit pipeline; "
                    f"{changed_field or 'row content'} adjusted",
                ]
        normalization_passes += 1
        rows = replayed
        replayed = _deposit_deterministic_pipeline(copy.deepcopy(rows))
    if replayed != rows:
        changed = [
            index
            for index, (before, after) in enumerate(zip(rows, replayed))
            if before != after
        ]
        dump_path = ""
        try:
            import json as _json
            import tempfile

            handle, dump_path = tempfile.mkstemp(
                prefix="aegis-assemble-fixpoint-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as dump:
                _json.dump(
                    {"rows": rows, "replayed": replayed},
                    dump,
                    ensure_ascii=False,
                    indent=1,
                    default=str,
                )
        except OSError:  # pragma: no cover - diagnostics never block
            dump_path = ""
        detail = ""
        if changed:
            first = changed[0]
            before, after = rows[first], replayed[first]
            fields = sorted(
                set(before) | set(after),
                key=str,
            )
            for field in fields:
                if before.get(field) != after.get(field):
                    detail = (
                        f"; row {first} "
                        f"({_normal(before.get('concept_title'))[:40]!r}) "
                        f"field {field!r}: "
                        f"{str(before.get(field))[:160]!r} -> "
                        f"{str(after.get(field))[:160]!r}"
                    )
                    break
        raise AssemblyError(
            "assembled rows are not stable under the deterministic "
            "deposit pipeline (changed row indexes: "
            + ",".join(str(index) for index in changed[:8])
            + f"; row count {len(rows)} -> {len(replayed)})"
            + detail
            + (f"; full row dump: {dump_path}" if dump_path else "")
            + "; sealing them would break the certificate at the "
            "deposit boundary"
        )

    # Q2 deterministic uniqueness audit at assemble end (mechanics): any
    # violation is a release-audit error — recorded on the involved rows
    # and in coverage, never silently repaired and never a halt (Q13).
    prompt_by_qid: dict[str, str] = {}
    for case in cases.values():
        for example in case.get("examples") or []:
            qid = str(example.get("qid") or "")
            if qid and qid not in prompt_by_qid:
                prompt_by_qid[qid] = str(example.get("prompt") or "")
    case_audit = audit_case_uniqueness(
        rows,
        cases=cases,
        expected_examples=[
            {"qid": qid, "prompt": prompt}
            for qid, prompt in sorted(prompt_by_qid.items())
        ],
    )
    for finding in case_audit:
        for index in finding.get("row_indexes") or []:
            if 0 <= index < len(rows):
                rows[index].setdefault("review_flags", []).append(
                    "Q2 release-audit error "
                    f"[{finding.get('code')}]: {finding.get('message')}"
                )

    # Host may have created new concepts; the deposit chain verifies the
    # certificate lineage against the FINAL payload, so re-stamp and
    # re-seal the complete row set (attempt 7: attested 63, payload 68).
    from .. import grounding_certificate

    known_blocks = {
        str(row.get("block_id") or "")
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }
    for number, row in enumerate(rows, start=1):
        row["_source_grounding_concept_id"] = f"CONCEPT-GROUND-{number:04d}"
        row["_source_grounding_version"] = GROUNDING_VERSION
    grounding_certificate.seal_records(
        rows,
        source_contract_hash=str(env.get("source_contract_hash") or ""),
        semantic_topology_sha256=(
            grounding_certificate.semantic_topology_sha256(env["graph"])
        ),
        allowed_block_ids=known_blocks,
    )

    return {
        "rows": rows,
        "coverage": {
            "items": len(routed) + len(unrouted),
            "routed_qids": sorted(routed),
            "unrouted": unrouted,
            # Phase 2.2 accounting (R4): every pooled item's verdict.
            "hub_placements": dict(
                place_result.get("hub_placements") or {}
            ),
            "figure_placements": dict(
                place_result.get("figure_placements") or {}
            ),
            "figure_dispositions": figure_dispositions,
            # Q1 accounting (R4): every LA-item, allotted exactly once.
            "learner_analysis": {
                "inventory": [dict(item) for item in analysis_inventory],
                "allotments": dict(analysis_allotments),
            },
            # Q2 accounting: every per-destination Case identity split,
            # and the deterministic uniqueness audit's findings.
            "case_splits": case_splits,
            "case_audit": case_audit,
        },
    }
