"""Pass 3 — Assemble: deterministic projection, zero model calls.

Everything semantic was decided by Settle and Host. Assemble is pure
bookkeeping: it renders each concept's hosted Types into the house
``// Types:`` section of ``concept_details``, routes every QID to its
host row, and accounts for every inventory item. Any inconsistency here
is a bug in our code, so this pass raises immediately — there is no
provider to correct.
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
            )

    pieces: list[str] = []
    for type_id in sorted(hosted):
        mined = types.get(type_id)
        if mined is None:
            raise AssemblyError(f"hosted unit references unknown {type_id}")
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
                )
            piece += f" Case {_ordinal(case_id, 'Case'):02d}: {case['title']}"
            for example in hosted[type_id][case_id]:
                piece += f" Example: {example}"
        pieces.append(piece)
    return " ".join(pieces)


def _inject_types(details: str, types_body: str) -> str:
    """Insert the Types section after mastery, before learner analysis."""

    if not types_body:
        return details
    parts = _ANALYSIS_SPLIT.split(details, maxsplit=1)
    injected = parts[0].rstrip() + " // Types: " + types_body
    if len(parts) == 2:
        injected += " // Misconception/ Error Analysis: " + parts[1]
    return injected


def assemble(
    env: Mapping[str, Any],
    settled_rows: list[Mapping[str, Any]],
    host_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Project settled rows + host maps into release-ready rows."""

    from .. import concept_refiner as cr

    types, cases = _type_catalog(env)
    rows = [copy.deepcopy(dict(row)) for row in settled_rows]
    # A new concept joins its topic BEFORE the topic's culmination row: the
    # culmination must stay last in its topic, and the certificate seals the
    # row order, so a downstream reorder would break the lineage.
    for new_row in host_result.get("new_concepts") or []:
        new_row = copy.deepcopy(dict(new_row))
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
            )
        row_by_key[key] = row

    flags_by_key: dict[tuple[str, str], list[str]] = {}
    for unit_id, entry in (host_result.get("host_map") or {}).items():
        key = _host_key(entry)
        if key not in row_by_key:
            raise AssemblyError(
                f"host entry for {unit_id} names a row that does not "
                "exist: " + str(entry.get("concept_title"))[:80]
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
                )
            sections_by_key.setdefault(dest_key, {}).setdefault(
                type_id, {}
            ).setdefault(case_id, []).append(example["prompt"])
            routes_by_key.setdefault(dest_key, set()).add(str(unit_id))

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
        out_rows = concept_cleanup.dedupe_similar_titles_chapter_wide(
            out_rows
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

    rows = cr.set_culmination_recap(rows)
    rows = _deposit_deterministic_pipeline(rows)
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
        },
    }
