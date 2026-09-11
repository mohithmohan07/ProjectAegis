"""Reconcile a reviewed Post Types/Cases surface.

The Concept review workbook is the authority for the reviewed Post question
set.  This module is intentionally pure so the HTTP/file workflow can validate
the entire replacement before it writes a staged release version.  The
original source inventory is copied into each retained question; only the
reviewer's route rows and visible example wording are applied.

``reviewed_rows`` is the normalized projection of the workbook's ``Type Case
Routing`` sheet.  A caller that did not receive that sheet should pass
``None`` and leave the current payload untouched.  An empty list is different:
it is an explicit empty reviewed set and therefore removes every Post item.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


class ReviewedQuestionSetError(ValueError):
    """The reviewed Types/Cases surface cannot be reconciled safely."""


def canonical_concept_review_rows(payload, concept_rows):
    """Use model-owned identity and boundaries for edited Concept Details."""
    from .concept_question_review import review_canonical_questions
    return review_canonical_questions(payload, list(concept_rows))


def synchronize_reviewed_catalog(payload: dict[str, Any], reviewed_rows) -> None:
    """Project accepted model placements into every active catalogue reader.

    Call after applying the workbook's Concept edits to a candidate payload.
    The caller still owns the transaction. Original evidence remains in the
    immutable predecessor and this receipt; only accepted question identities
    participate in the new catalogue. No text parser chooses membership or
    ownership. Host-specific IDs distinguish one logical Type/Case deliberately
    placed on different Concepts without changing its reviewed wording.
    """
    if not getattr(reviewed_rows, "receipt", None):
        return
    from . import build_concepts_release as bcr

    inventory = payload.get("question_task_inventory") or {}
    items = inventory.get("items") or []
    records = payload.get("records") or []
    if len(items) != len(reviewed_rows):
        raise ReviewedQuestionSetError("reviewed catalogue and accepted question counts differ")
    old_types = {str(row.get("type_id") or ""): row
                 for row in bcr._type_rows(payload.get("mined_types"))}
    old_cases = {(type_id, str(case.get("case_id") or "")): case
                 for type_id, row in old_types.items()
                 for case in row.get("case_prompts") or [] if isinstance(case, Mapping)}
    audit = payload.setdefault("review_question_audit", {})
    audit["previous_catalog"] = {
        "mined_types": copy.deepcopy(payload.get("mined_types") or {}),
        "type_case_rows": copy.deepcopy(payload.get("type_case_rows") or []),
        "record_placements": [{
            "record_index": index,
            **{field: copy.deepcopy(row.get(field)) for field in (
                bcr.RELEASE_ROW_QIDS_FIELD, bcr.RELEASE_ROW_ROUTES_FIELD,
                "_type_case_qid_host_placement_manifest", "_activity_hub_qids",
                "_aegis_hub_placements",
            )},
        } for index, row in enumerate(records)],
    }

    def minted(kind: str, *seed) -> str:
        encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, default=str)
        return kind + "REV-" + hashlib.sha256(encoded.encode()).hexdigest()[:16]

    hosted: dict[str, set[int]] = {}
    for item, row in zip(items, reviewed_rows, strict=True):
        index = row.get("concept_row_index")
        if type(index) is not int or not 0 <= index < len(records):
            raise ReviewedQuestionSetError("reviewed question has no exact staged Concept target")
        _verify_reviewed_question_quote(row, records[index])
        type_id = str(row.get("type_id") or "")
        if type_id:
            hosted.setdefault(type_id, set()).add(index)

    for record in records:
        record[bcr.RELEASE_ROW_QIDS_FIELD] = []
        record[bcr.RELEASE_ROW_ROUTES_FIELD] = []
        record["_type_case_qid_host_placement_manifest"] = {"placements": {}}
        record["_activity_hub_qids"] = []
        record["_aegis_hub_placements"] = []

    catalog: dict[str, dict[str, Any]] = {}
    cases: dict[tuple[str, str], dict[str, Any]] = {}
    for item, row in zip(items, reviewed_rows, strict=True):
        index = row["concept_row_index"]
        record = records[index]
        qid = _question_id(item)
        source_type = str(row.get("type_id") or "")
        source_case = str(row.get("case_id") or "")
        old_type = old_types.get(source_type) or {}
        old_case = old_cases.get((source_type, source_case)) or {}
        title = str(row.get("type_title") or bcr._definition(old_type, "type_title", "title", "name"))
        definition = str(row.get("type_definition") or bcr._definition(
            old_type, "type_definition", "definition", "type_description", "description", "method_definition"))
        case_definition = str(row.get("case_definition") or bcr._definition(
            old_case, "case_definition", "definition", "case_prompt", "prompt", "case_title", "description"))
        type_id = source_type
        if not type_id or len(hosted.get(type_id, set())) > 1:
            type_id = minted("TYPE", source_type, index, title, definition)
        case_id = source_case or minted("CASE", type_id, index, case_definition)
        # A source Case carried to a host alias is also an explicitly linked
        # host identity; this prevents cross-Concept Case collisions.
        if source_type and type_id != source_type and source_case:
            case_id = minted("CASE", type_id, source_case, index)
        section = str(row.get("placement_section") or "types")
        is_activity = section in {"activity", "info_hub"}
        owner = str(record.get("_semantic_topic_id") or record.get("topic") or "")
        type_row = catalog.setdefault(type_id, {
            "type_id": type_id, "type_title": title, "type_definition": definition,
            "owner_topic_ids": [owner] if owner else [], "source_question_ids": [],
            "case_prompts": [], "reviewed_source_type_id": source_type,
        })
        if type_row["type_title"] != title or type_row["type_definition"] != definition:
            raise ReviewedQuestionSetError(f"reviewed Type {source_type or type_id} has inconsistent definitions")
        case_key = (type_id, case_id)
        if case_key not in cases:
            cases[case_key] = {
                "case_id": case_id, "case_definition": case_definition,
                "owner_topic_ids": [owner] if owner else [], "source_question_ids": [],
                "examples": [], "is_activity": is_activity,
                "reviewed_source_case_id": source_case,
            }
            type_row["case_prompts"].append(cases[case_key])
        case_row = cases[case_key]
        if case_row["case_definition"] != case_definition or case_row["is_activity"] != is_activity:
            raise ReviewedQuestionSetError(f"reviewed Case {source_case or case_id} has inconsistent definitions or hosts")
        type_row["source_question_ids"].append(qid)
        case_row["source_question_ids"].append(qid)
        case_row["examples"].append({"source_question_id": qid, "prompt": str(row["question_text"])})
        target = item.setdefault("_aegis_reviewed_target", {})
        target.update({
            "concept_row_index": index,
            "concept_title": str(record.get("concept_title") or record.get("concept") or ""),
            "topic": str(record.get("topic") or ""),
            "type_id": type_id, "case_id": case_id,
            "source_type_id": source_type, "source_case_id": source_case,
            "type_title": title, "type_definition": definition,
            "case_definition": case_definition, "placement_section": section,
        })
        item["reviewed_type_id"], item["reviewed_case_id"] = type_id, case_id
        item["placement_section"] = section
        record[bcr.RELEASE_ROW_QIDS_FIELD].append(qid)
        marker = f"{type_id}::{case_id}"
        if marker not in record[bcr.RELEASE_ROW_ROUTES_FIELD]:
            record[bcr.RELEASE_ROW_ROUTES_FIELD].append(marker)
        record["_type_case_qid_host_placement_manifest"]["placements"][qid] = {
            "qid": qid, "type_id": type_id, "case_id": case_id,
            "host_disposition": "activity_info_hub" if is_activity else "type_case_example",
        }
        if is_activity:
            record["_activity_hub_qids"].append(qid)

    payload["mined_types"] = {"types": list(catalog.values())}
    inventory["mined_types"] = copy.deepcopy(payload["mined_types"])
    route_rows, _catalog_issues, routes = bcr.audit_type_cases(payload["mined_types"], inventory)
    payload["type_case_rows"] = route_rows
    inventory["type_case_rows"] = copy.deepcopy(route_rows)
    # Supersede only the old catalogue audit. Other findings retain their
    # original policy and remain visible; none is cleared merely by upload.
    old_issues = list(payload.get("issues") or [])
    superseded = [issue for issue in old_issues if issue.get("phase") == "type_case_release"]
    audit["previous_catalog_findings"] = copy.deepcopy(superseded)
    payload["issues"] = [issue for issue in old_issues if issue.get("phase") != "type_case_release"]
    payload["issues"].extend(bcr._recomputed_identity_issues(payload, records))
    payload["records"] = bcr._annotate_records(records, payload["issues"], routes)
    inventory["reviewed_source_questions"] = copy.deepcopy(audit)
    payload["question_task_inventory"] = inventory


def _text(value: Any) -> str:
    return str(value or "").strip()


def _concept_details_sha256(value: Any) -> str:
    """Hash the raw Concept Details cell used as the quote authority."""

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _verify_reviewed_question_quote(
    row: Mapping[str, Any], record: Mapping[str, Any],
) -> None:
    """Prove a reviewed quote against its exact staged Concept cell.

    Canonical Concept review can return a question split into ordered source
    spans when excluded material sits between wording parts.  The old
    catalog handoff only accepted a contiguous substring and therefore
    rejected an otherwise valid author verdict after the caller rebound the
    edited row to its staged record index.  This check remains mechanical:
    digest the target raw cell, materialize each exact span in order, and
    compare only the reversible rendered view to the returned concatenation.
    """

    source = str(record.get("concept_details") or "")
    expected_digest = row.get("concept_details_sha256")
    spans = row.get("question_text_spans")
    if spans is not None and not isinstance(spans, list):
        raise ReviewedQuestionSetError(
            "reviewed question spans must be an array of exact source strings"
        )
    spans_present = isinstance(spans, list) and bool(spans)
    if spans_present and expected_digest is None:
        raise ReviewedQuestionSetError(
            "reviewed question spans require a Concept Details digest"
        )
    if spans_present and any(not isinstance(span, str) for span in spans):
        raise ReviewedQuestionSetError(
            "reviewed question spans must contain only exact source strings"
        )
    if expected_digest is not None:
        expected = str(expected_digest or "").strip().lower()
        actual = _concept_details_sha256(source)
        if not expected or expected != actual:
            raise ReviewedQuestionSetError(
                "reviewed question Concept Details digest disagrees with its "
                "exact staged target"
            )

    text = str(row.get("question_text") or "")
    if spans_present:
        from .concept_question_quote import materialize_spans, view

        materialized = materialize_spans(source, spans)
        if materialized is None or view(materialized) != view(text):
            raise ReviewedQuestionSetError(
                "reviewed question spans are not ordered exact quotes from "
                "their staged Concept Details target"
            )
        return
    if not text.strip() or text not in source:
        raise ReviewedQuestionSetError(
            "reviewed question quote does not occur at its accepted Concept target"
        )


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        # The workbook writer uses comma separation for these audit cells.
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_text(item) for item in value if _text(item)]
    return []


def _question_id(row: Mapping[str, Any]) -> str:
    return _text(
        row.get("qid")
        or row.get("source_qid")
        or row.get("question_id")
        or row.get("pre_question_id")
    )


def _stable_reviewer_id(row: Mapping[str, Any], used: set[str]) -> str:
    basis = json.dumps(
        {
            "prompt": _text(row.get("example_prompt") or row.get("question_text")),
            "type_id": _text(row.get("type_id")),
            "case_id": _text(row.get("case_id")),
            "owner_topic_ids": _list(row.get("owner_topic_ids")),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    stem = "QREV-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    candidate = stem
    suffix = 2
    while candidate in used:
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


def _route_identity(row: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    return (
        _text(row.get("type_id")),
        _text(row.get("case_id")),
        tuple(_list(row.get("owner_topic_ids"))),
    )


def _original_route_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    result: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for row in rows:
        if _text(row.get("row_kind")).lower() != "example":
            continue
        qid = _text(row.get("example_qid"))
        if qid and qid not in result:
            result[qid] = _route_identity(row)
    return result


def _copy_reviewed_wording(item: dict[str, Any], prompt: str) -> None:
    """Make the visible workbook wording reach every source-atom field."""
    prompt = str(prompt)
    if not prompt.strip():
        raise ReviewedQuestionSetError("every retained Example must have a prompt")
    from . import generation_quality_policy as quality
    prior_wording = str(item.get("frozen_task_text") or item.get("polished_task")
                        or item.get("normalized_task") or item.get("raw_task") or "")
    if quality.active(item) and prior_wording != prompt:
        # This direct surface edits the complete Example; no separate context
        # decision was provided. Do not retain a stale context projection from
        # before that explicit wording edit. The full old item is in the audit.
        item["learner_context"] = ""
    # These are the fields used by the source inventory and the freeze seam in
    # different historical payloads.  Keeping them synchronized prevents the
    # Master from resurrecting a stale polished/frozen value.
    for field in (
        "raw_task", "normalized_task", "polished_task", "frozen_task_text",
        "normalized_public_text", "question_text",
    ):
        if field in item or field in {"raw_task", "question_text"}:
            item[field] = prompt


def reconcile_post_review(
    payload: Mapping[str, Any],
    reviewed_rows: Sequence[Mapping[str, Any]] | None,
    *,
    now: str | None = None,
) -> dict[str, Any] | None:
    """Return the reviewed inventory/routes, or ``None`` when no sheet exists.

    The function does not mutate ``payload``.  Existing source identities are
    retained byte-for-byte except for explicitly edited Example wording.  A
    row marked ``reviewer_added`` gets a ``QREV-*`` identity and provenance;
    an unknown unmarked identity is refused.  The returned route rows preserve
    the workbook order, so moving an Example between Types/Cases or changing
    its order is reflected by the next Master snapshot.
    """
    if reviewed_rows is None:
        return None

    raw_inventory = payload.get("question_task_inventory")
    inventory = copy.deepcopy(dict(raw_inventory or {})) if isinstance(raw_inventory, Mapping) else {}
    original_items = [
        copy.deepcopy(dict(item))
        for item in inventory.get("items") or []
        if isinstance(item, Mapping)
    ]
    original_by_id: dict[str, dict[str, Any]] = {}
    for item in original_items:
        qid = _question_id(item)
        if not qid:
            raise ReviewedQuestionSetError("the source inventory contains a question without a QID")
        if qid in original_by_id:
            raise ReviewedQuestionSetError(f"the source inventory repeats question {qid!r}")
        original_by_id[qid] = item

    original_routes = [
        copy.deepcopy(dict(row))
        for row in payload.get("type_case_rows") or []
        if isinstance(row, Mapping)
    ]
    original_route_map = _original_route_map(original_routes)
    # Early workbook readers exposed only Example rows.  Keep accepting that
    # projection while retaining the original Type/Case definition rows; a
    # caller that has the complete sheet replaces all route rows below.
    has_parent_rows = any(
        _text(row.get("row_kind") or row.get("kind")).lower() in {"type", "case"}
        for row in reviewed_rows
        if isinstance(row, Mapping)
    )
    if not has_parent_rows:
        route_rows: list[dict[str, Any]] = [
            copy.deepcopy(row) for row in original_routes
            if _text(row.get("row_kind") or row.get("kind")).lower() in {"type", "case"}
        ]
    else:
        route_rows = []
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    represented_sources: set[str] = set()
    grouped_sources: list[dict[str, Any]] = []
    added: list[str] = []
    edited: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    used_ids = set(original_by_id)

    for raw in reviewed_rows:
        if not isinstance(raw, Mapping):
            raise ReviewedQuestionSetError("a reviewed Types/Cases row is not an object")
        row = copy.deepcopy(dict(raw))
        kind = _text(row.get("row_kind") or row.get("kind")).lower()
        if kind not in {"type", "case", "example", "reviewer_added"}:
            raise ReviewedQuestionSetError(f"unknown reviewed Types/Cases row kind {kind!r}")
        # Keep all Type/Case edits in the accepted snapshot.  They are the
        # placement authority for the exact question set.
        route = {
            key: copy.deepcopy(row.get(key))
            for key in (
                "row_kind", "type_id", "type_title", "type_definition",
                "case_id", "case_definition", "owner_topic_ids", "qids",
                "example_qid", "example_prompt", "example_number",
                "is_activity", "audit_status", "error",
                "source_qids", "source_dependency_reviews",
            )
            if key in row
        }
        route["row_kind"] = kind
        route["owner_topic_ids"] = _list(row.get("owner_topic_ids"))
        qids = _list(row.get("qids"))
        qid = _text(row.get("example_qid") or row.get("qid") or row.get("source_qid"))
        if kind in {"example", "reviewer_added"}:
            from .concept_review_context import source_group_ids, apply_source_group
            members = source_group_ids(dict(row, source_qid=qid)) if (
                "source_qids" in row or kind == "example"
            ) else []
            if len(members) != len(set(members)) or any(member in represented_sources for member in members):
                raise ReviewedQuestionSetError("a source question belongs to more than one accepted question")
            if any(member not in original_by_id for member in members):
                raise ReviewedQuestionSetError("a reviewed source group names an unknown source question")
            if "source_qids" in row and ((qid and (not members or members[0] != qid)) or (not qid and members)):
                raise ReviewedQuestionSetError("the accepted source group must start with its primary question ID")
            # An ordinary Example without an identity is ambiguous.  The
            # canonical Concept Details parser may leave an unresolved
            # Example blank, but the author/critic adapter must explicitly
            # classify it as reviewer_added before reconciliation can mint a
            # QREV identity.  This prevents a malformed/partial workbook
            # from silently becoming a new Post question.
            if kind == "reviewer_added":
                qid = _stable_reviewer_id(row, used_ids)
                route["row_kind"] = kind
                added.append(qid)
            if qid in selected_ids:
                raise ReviewedQuestionSetError(f"reviewed question {qid!r} appears more than once")
            if qid not in original_by_id and kind != "reviewer_added":
                raise ReviewedQuestionSetError(
                    f"reviewed Example names unknown source question {qid!r}; "
                    "mark intentional additions as reviewer_added"
                )
            prompt = _text(row.get("example_prompt") or row.get("question_text"))
            if qid in original_by_id:
                item = copy.deepcopy(original_by_id[qid])
            else:
                item = {
                    "qid": qid,
                    "source_kind": "reviewer_added",
                    "source_label": "Reviewer added",
                    "provenance": "reviewer_added",
                    "reviewer_added": True,
                }
            before = {field: copy.deepcopy(item.get(field)) for field in (
                "raw_task", "normalized_task", "polished_task", "frozen_task_text",
                "normalized_public_text", "question_text",
                "learner_context", "generation_quality_policy",
            )}
            _copy_reviewed_wording(item, prompt)
            apply_source_group(item, row, original_by_id)
            represented_sources.update(members)
            if len(members) > 1:
                grouped_sources.append({
                    "identity": qid,
                    "source_qids": copy.deepcopy(members),
                    "source_dependency_reviews": copy.deepcopy(row.get("source_dependency_reviews") or []),
                    "source_evidence": [copy.deepcopy(original_by_id[member]) for member in members],
                })
            after = {field: copy.deepcopy(item.get(field)) for field in before}
            if before != after:
                edited.append({"identity": qid, "before": before, "after": after})
            item["qid"] = qid
            item["_review_identity"] = qid
            if kind == "reviewer_added":
                item["reviewer_provenance"] = {
                    "kind": "reviewer_added",
                    "type_id": _text(row.get("type_id")),
                    "case_id": _text(row.get("case_id")),
                    "owner_topic_ids": _list(row.get("owner_topic_ids")),
                }
            selected.append(item)
            selected_ids.add(qid)
            used_ids.add(qid)
            route["example_qid"] = qid
            route["qids"] = [qid]
            route["example_prompt"] = prompt
            if qid in original_route_map:
                before_route = original_route_map[qid]
                after_route = _route_identity(route)
                if before_route != after_route:
                    moved.append({
                        "identity": qid,
                        "from": {
                            "type_id": before_route[0],
                            "case_id": before_route[1],
                            "owner_topic_ids": list(before_route[2]),
                        },
                        "to": {
                            "type_id": after_route[0],
                            "case_id": after_route[1],
                            "owner_topic_ids": list(after_route[2]),
                        },
                    })
        else:
            route["qids"] = qids
        route_rows.append(route)

    # Parent Type/Case qid lists are projections, not a second source of
    # membership. Recompute them from the reviewed Examples so an omitted
    # Example cannot remain reachable through stale parent cells.
    by_type: dict[str, list[str]] = {}
    by_case: dict[tuple[str, str], list[str]] = {}
    for route in route_rows:
        if route.get("row_kind") not in {"example", "reviewer_added"}:
            continue
        qid = _text(route.get("example_qid"))
        type_id = _text(route.get("type_id"))
        case_id = _text(route.get("case_id"))
        if qid and type_id:
            by_type.setdefault(type_id, []).append(qid)
        if qid and type_id and case_id:
            by_case.setdefault((type_id, case_id), []).append(qid)
    for route in route_rows:
        kind = route.get("row_kind")
        type_id = _text(route.get("type_id"))
        case_id = _text(route.get("case_id"))
        if kind == "type":
            route["qids"] = list(dict.fromkeys(by_type.get(type_id, [])))
        elif kind == "case":
            route["qids"] = list(dict.fromkeys(by_case.get((type_id, case_id), [])))

    omitted = [qid for qid in original_by_id if qid not in selected_ids | represented_sources]
    stamp = now or datetime.now(timezone.utc).isoformat()
    audit = {
        "version": "reviewed-types-cases-1",
        "original_ids": list(original_by_id),
        "reviewed_ids": [item["qid"] for item in selected],
        "omitted": omitted,
        "added": added,
        "moved": moved,
        "edited": edited,
        **({"grouped": grouped_sources} if grouped_sources else {}),
        "reviewed_at": stamp,
    }
    inventory["items"] = selected
    inventory["reviewed_source_questions"] = copy.deepcopy(audit)
    return {
        "question_task_inventory": inventory,
        "type_case_rows": route_rows,
        "review_question_audit": audit,
        "original_source_questions": original_items,
    }
