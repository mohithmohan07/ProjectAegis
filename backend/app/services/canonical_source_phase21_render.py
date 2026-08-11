"""Phase 2.1 mined-Type immutability and final wire-format cleanup."""
from __future__ import annotations

import copy
import re
from typing import Any

_ORPHAN_ANALYSIS_RE = re.compile(
    r"(?im)(?:^|\n)[ \t]*Misconceptions?[ \t]*/[ \t]*(?=\r?$|\r?\n|//)"
)
_HUB_PREFIX_RE = re.compile(
    r"(?i)^\s*(?P<label>Activity|Project|Discuss)\b\s*(?:[—–:-]\s*)?"
)


def intact_type_assignment_units(
    generation,
    types: list[dict],
    canonical: dict[str, Any] | None,
    expand_units=None,
) -> list[dict]:
    """Expand reusable Types into independently routed Case placement units."""
    owners: dict[str, str] = {}
    source_types = [raw for raw in types if isinstance(raw, dict)]
    if not callable(expand_units):
        raise RuntimeError(
            "Phase 2.1 Case-unit expansion requires the unwrapped generation "
            "expander"
        )
    units = expand_units(source_types)
    for unit in units:
        type_id = str(unit.get("type_id") or "").strip()
        qids = generation._type_source_qids(unit)
        for qid in qids:
            previous = owners.get(qid)
            if previous and previous != type_id:
                raise RuntimeError(
                    "Phase 2.1 mined taxonomy has duplicate qid ownership: "
                    f"{qid} belongs to {previous} and {type_id}"
                )
            owners[qid] = type_id
    return units


def clean_activity_hub_content(content: str) -> str:
    """Keep one leading Hub kind and remove every repeated copy of that label."""
    value = str(content or "").strip()
    first = _HUB_PREFIX_RE.match(value)
    if first is None:
        return re.sub(r"\s+", " ", value).strip()
    label = first.group("label").title()
    tail = value[first.end():].lstrip()
    for _ in range(8):
        repeated = _HUB_PREFIX_RE.match(tail)
        if repeated is None or repeated.group("label").casefold() != label.casefold():
            break
        tail = tail[repeated.end():].lstrip()
    tail = re.sub(r"\s+", " ", tail).strip()
    return f"{label} — {tail}".rstrip(" —")


def record_example_qids(generation, record: dict, inventory: dict) -> list[str]:
    qids: list[str] = []
    for item in inventory.get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        key = generation._inventory_coverage_key(
            generation._inventory_task_text(item)
        )
        if key and generation._rendered_inventory_example_counts(
            [record], {key}
        ).get(key, 0):
            qids.append(qid)
    return qids


def filtered_mined_type(
    generation,
    mtype: dict,
    qids: set[str],
    inventory_by_qid: dict[str, dict],
) -> dict | None:
    relevant = [
        qid for qid in generation._type_source_qids(mtype) if qid in qids
    ]
    if not relevant:
        return None
    out = copy.deepcopy(mtype)
    out["source_question_ids"] = relevant
    cases: list[dict] = []
    for raw_case in mtype.get("case_prompts") or []:
        case = (
            {"case_prompt": raw_case}
            if isinstance(raw_case, str)
            else copy.deepcopy(raw_case)
        )
        if not isinstance(case, dict):
            continue
        case_qids = [
            qid for qid in generation._assignment_case_qids(case) if qid in qids
        ]
        examples = [
            copy.deepcopy(example)
            for example in generation._case_examples(case)
            if str(example.get("source_question_id") or "").strip() in case_qids
        ]
        if not case_qids:
            continue
        if not examples:
            examples = [
                {
                    "source_question_id": qid,
                    "example_prompt": generation._inventory_task_text(
                        inventory_by_qid[qid]
                    ),
                }
                for qid in case_qids
                if qid in inventory_by_qid
            ]
        case["examples"] = examples
        case.pop("source_question_id", None)
        cases.append(case)
    if not cases:
        examples = [
            {
                "source_question_id": qid,
                "example_prompt": generation._inventory_task_text(
                    inventory_by_qid[qid]
                ),
            }
            for qid in relevant
            if qid in inventory_by_qid
        ]
        if not examples:
            return None
        cases = [{
            "case_id": "CASE-PHASE21-0001",
            "case_title": "Applying the canonical mined Type to its source task",
            "examples": examples,
        }]
    out["case_prompts"] = cases
    return out


def restore_mined_type_taxonomy(
    generation,
    records: list[dict],
    inventory: dict | None,
    mined_types: dict | None,
) -> list[dict]:
    """Rebuild Type headings and Cases from the immutable mined taxonomy."""
    if (
        not records
        or not isinstance(inventory, dict)
        or not isinstance(mined_types, dict)
    ):
        return records
    inventory_by_qid = {
        str(item.get("qid") or "").strip(): item
        for item in inventory.get("items") or []
        if isinstance(item, dict) and str(item.get("qid") or "").strip()
    }
    mined = [
        item for item in mined_types.get("types") or [] if isinstance(item, dict)
    ]
    if not inventory_by_qid or not mined:
        return records
    expected_keys = {
        generation._inventory_coverage_key(
            generation._inventory_task_text(item)
        )
        for item in inventory_by_qid.values()
        if str(item.get("source_kind") or "").strip().lower()
        not in generation._HUB_INVENTORY_KINDS
    }
    expected_keys.discard("")
    before_counts = generation._rendered_inventory_example_counts(
        records, expected_keys
    )
    out = copy.deepcopy(records)
    changed = 0
    for record in out:
        qids = set(record_example_qids(generation, record, inventory))
        if not qids:
            continue
        fragments: list[str] = []
        fragment_origin_ids: list[str] = []
        number = 0
        for mtype in mined:
            filtered = filtered_mined_type(
                generation, mtype, qids, inventory_by_qid
            )
            if filtered is None:
                continue
            fragment, number = generation._mined_type_to_body(filtered, number)
            if fragment:
                fragments.append(fragment)
                fragment_origin_ids.append(str(
                    mtype.get("type_id") or ""
                ).split("::", 1)[0])
        if not fragments:
            continue
        details = record.get("concept_details") or ""
        sections = [
            (label, content)
            for label, content in generation.cr.split_sections(details)
            if not str(label or "").strip().lower().startswith("type")
        ]
        rebuilt = generation._inject_types(
            generation.cr.join_sections(sections),
            " ".join(fragments),
        )
        if rebuilt != details:
            record["concept_details"] = rebuilt
            changed += 1
        # The restoration above numbers fragments only within one row. Hand
        # the immutable mined-Type identities to the chapter-wide renumbering
        # pass so the same Type and its Case sequence continue across hosts.
        record["_origin_type_id"] = fragment_origin_ids
    after_counts = generation._rendered_inventory_example_counts(out, expected_keys)
    if before_counts != after_counts:
        generation.progress.log(
            "Rejected Phase 2.1 Type taxonomy restoration because exact "
            "source Example coverage changed.",
            level="warning",
        )
        return records
    if changed:
        generation.progress.log(
            "Restored canonical mined Type titles and Cases on "
            f"{changed} concept row(s) without changing source Example coverage.",
            level="success",
        )
    return out


def normalize_final_records(
    generation,
    records: list[dict],
    inventory: dict | None,
    mined_types: dict | None,
) -> list[dict]:
    from .phase3 import runner as phase3_runner

    if phase3_runner.rewrite_enabled():
        # The rewritten Assemble pass renders Types per-question from the
        # sealed mined taxonomy (wire-format repaired); rebuilding them
        # here overwrote that authoritative rendering with unrepaired
        # taxonomy text and re-pooled Examples off their routed rows.
        out = copy.deepcopy(records)
    else:
        out = restore_mined_type_taxonomy(
            generation, records, inventory, mined_types
        )
    cleaned: list[dict] = []
    for raw_record in out:
        record = copy.deepcopy(raw_record)
        details = _ORPHAN_ANALYSIS_RE.sub(
            "\n", str(record.get("concept_details") or "")
        )
        sections: list[tuple[str, str]] = []
        for label, content in generation.cr.split_sections(details):
            if generation.cr.is_activity_hub_label(label):
                content = clean_activity_hub_content(content)
            sections.append((label, content))
        record["concept_details"] = generation.cr.join_sections(sections)
        cleaned.append(record)
    cleaned = generation.cr.renumber_types_continuously(cleaned)
    return generation.cv.ensure_valid_learner_analysis(cleaned)
