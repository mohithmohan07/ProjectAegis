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
    for type_id in sorted(by_type):
        mined = types.get(type_id)
        if mined is None:
            raise AssemblyError(f"hosted unit references unknown {type_id}")
        piece = f"Type {_ordinal(type_id, 'Type'):02d}: {mined['title']}"
        if mined["definition"]:
            piece += f" — {mined['definition']}"
        for case_id in sorted(by_type[type_id]):
            case = cases.get((type_id, case_id))
            if case is None:
                raise AssemblyError(
                    f"hosted unit references unknown {type_id}::{case_id}"
                )
            piece += f" Case {_ordinal(case_id, 'Case'):02d}: {case['title']}"
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

    # House contract: a Type lives on exactly ONE concept host. If cases
    # of the same Type were hosted on different concepts, consolidate the
    # whole Type onto its majority host.
    host_by_type: dict[str, tuple[str, str]] = {}
    for key, unit_ids in units_by_key.items():
        for unit_id in unit_ids:
            type_id = unit_id.split("::")[0]
            host_by_type.setdefault(type_id, key)
    counts: dict[tuple[str, tuple[str, str]], int] = {}
    for key, unit_ids in units_by_key.items():
        for unit_id in unit_ids:
            type_id = unit_id.split("::")[0]
            counts[(type_id, key)] = counts.get((type_id, key), 0) + 1
    for type_id in list(host_by_type):
        best = max(
            (k for (t, k) in counts if t == type_id),
            key=lambda k: counts[(type_id, k)],
        )
        host_by_type[type_id] = best
    consolidated: dict[tuple[str, str], list[str]] = {}
    for key, unit_ids in units_by_key.items():
        for unit_id in unit_ids:
            type_id = unit_id.split("::")[0]
            consolidated.setdefault(host_by_type[type_id], []).append(unit_id)
    units_by_key = consolidated

    # House contract: a culmination recap names every concept of its topic.
    from .. import concept_refiner as cr

    titles_by_topic: dict[str, list[str]] = {}
    for row in rows:
        title = _normal(row.get("concept_title"))
        if not cr.is_culmination(title):
            titles_by_topic.setdefault(
                str(row.get("_semantic_topic_id") or ""), []
            ).append(title)
    for row in rows:
        if cr.is_culmination(_normal(row.get("concept_title"))):
            titles = titles_by_topic.get(
                str(row.get("_semantic_topic_id") or ""), []
            )
            if titles:
                row["concept_details"] = (
                    "Description: Recap of " + "; ".join(titles) + "."
                )

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

    # Host may have created new concepts; the deposit chain verifies the
    # certificate lineage against the FINAL payload, so re-stamp and
    # re-seal the complete row set (attempt 7: attested 63, payload 68).
    from .. import canonical_source_phase31_grounding_contract as phase31
    from .. import grounding_certificate

    known_blocks = {
        str(row.get("block_id") or "")
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }
    for number, row in enumerate(rows, start=1):
        row["_source_grounding_concept_id"] = f"CONCEPT-GROUND-{number:04d}"
        row["_source_grounding_version"] = phase31._GROUNDING_VERSION
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
