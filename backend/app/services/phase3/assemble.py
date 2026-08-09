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
import re
from typing import Any, Mapping

_ANALYSIS_SPLIT = re.compile(
    r"\s*//\s*Misconception/?\s*Error Analysis:\s*", re.IGNORECASE
)


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
    cases: dict[tuple[str, str], dict[str, str]] = {}
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
            example = ""
            for row in case.get("examples") or []:
                if isinstance(row, Mapping) and _normal(
                    row.get("example_prompt")
                ):
                    example = _normal(row.get("example_prompt"))
                    break
            cases[(type_id, str(case.get("case_id") or ""))] = {
                "title": _normal(case.get("case_title")),
                "example": example,
            }
    return types, cases


def render_types_section(
    unit_ids: list[str],
    *,
    types: Mapping[str, Mapping[str, str]],
    cases: Mapping[tuple[str, str], Mapping[str, str]],
) -> str:
    """Render the house Types section for one concept's hosted units."""

    by_type: dict[str, list[str]] = {}
    for unit_id in unit_ids:
        parts = unit_id.split("::")
        type_id = parts[0]
        case_id = parts[1] if len(parts) > 1 else ""
        by_type.setdefault(type_id, [])
        if case_id:
            by_type[type_id].append(case_id)

    pieces: list[str] = []
    for type_number, type_id in enumerate(sorted(by_type), start=1):
        mined = types.get(type_id)
        if mined is None:
            raise AssemblyError(f"hosted unit references unknown {type_id}")
        piece = f"Type {type_number:02d}: {mined['title']}"
        if mined["definition"]:
            piece += f" — {mined['definition']}"
        for case_number, case_id in enumerate(
            sorted(by_type[type_id]), start=1
        ):
            case = cases.get((type_id, case_id))
            if case is None:
                raise AssemblyError(
                    f"hosted unit references unknown {type_id}::{case_id}"
                )
            piece += f" Case {case_number:02d}: {case['title']}"
            if case["example"]:
                piece += f" Example: {case['example']}"
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

    types, cases = _type_catalog(env)
    rows = [copy.deepcopy(dict(row)) for row in settled_rows]
    rows.extend(
        copy.deepcopy(dict(row))
        for row in host_result.get("new_concepts") or []
    )

    row_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _host_key(row)
        if key in row_by_key:
            raise AssemblyError(
                "two rows share a topic and title: "
                + str(row.get("concept_title"))[:80]
            )
        row_by_key[key] = row

    units_by_key: dict[tuple[str, str], list[str]] = {}
    flags_by_key: dict[tuple[str, str], list[str]] = {}
    for unit_id, entry in (host_result.get("host_map") or {}).items():
        key = _host_key(entry)
        if key not in row_by_key:
            raise AssemblyError(
                f"host entry for {unit_id} names a row that does not "
                "exist: " + str(entry.get("concept_title"))[:80]
            )
        units_by_key.setdefault(key, []).append(str(unit_id))
        for flag in entry.get("review_flags") or []:
            flags_by_key.setdefault(key, []).append(str(flag))

    for key, unit_ids in units_by_key.items():
        row = row_by_key[key]
        section = render_types_section(
            sorted(unit_ids), types=types, cases=cases
        )
        row["concept_details"] = _inject_types(
            str(row.get("concept_details") or ""), section
        )
        row["_aegis_release_type_case_routes"] = sorted(unit_ids)
    for key, flags in flags_by_key.items():
        row = row_by_key[key]
        row["review_flags"] = [*(row.get("review_flags") or []), *flags]

    qid_map = host_result.get("qid_map") or {}
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

    return {
        "rows": rows,
        "coverage": {
            "items": len(routed) + len(unrouted),
            "routed_qids": sorted(routed),
            "unrouted": unrouted,
        },
    }
