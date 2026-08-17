"""Immutable Output-01 hierarchy used by the assessment release.

The staged Build Concepts release is the sole concept authority for Output 02.
Persisted Topic and Concept rows are deliberately absent from this projection;
the target Chapter contributes directory metadata only.  Private release keys
join concepts, groups, placements, and questions without entering workbook
cells.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .. import models
from . import assessment_release as rel
from . import build_concepts_release_files


class SnapshotError(ValueError):
    """The staged concept release cannot form an immutable hierarchy."""


_POSITION_FIELDS = frozenset({
    "bbox",
    "example_number",
    "page",
    "page_hint",
    "position",
    "printer_page",
    "row",
    "row_index",
    "row_number",
    "source_end",
    "source_order",
    "source_page",
    "source_paper_number",
    "source_start",
    "_phase32_segment_order",
    "_phase32_source_order",
})


def _semantic_evidence(value: Any) -> Any:
    """Copy complete staged meaning while removing position-only proxies."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic_evidence(raw)
            for key, raw in value.items()
            if str(key) not in _POSITION_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_evidence(raw) for raw in value]
    return copy.deepcopy(value)


def source_release_sha256(payload: Mapping[str, Any]) -> str:
    """Seal exactly the staged material that can affect Output 02."""

    return rel.sha256_json({
        "version": copy.deepcopy(payload.get("version")),
        "target_chapter_id": copy.deepcopy(payload.get("target_chapter_id")),
        "learning_kind": copy.deepcopy(payload.get("learning_kind")),
        "source_book": copy.deepcopy(payload.get("source_book")),
        "filename": copy.deepcopy(payload.get("filename")),
        "source_document_hash": copy.deepcopy(
            payload.get("source_document_hash")
        ),
        "directory_metadata": copy.deepcopy(
            payload.get("directory_metadata") or {}
        ),
        "records": copy.deepcopy(payload.get("records") or []),
        "question_task_inventory": copy.deepcopy(
            payload.get("question_task_inventory") or {}
        ),
        "chapter_meta": copy.deepcopy(payload.get("chapter_meta") or {}),
        "target_identity": copy.deepcopy(payload.get("target_identity") or {}),
        "mined_types": copy.deepcopy(payload.get("mined_types") or {}),
        "type_case_rows": copy.deepcopy(payload.get("type_case_rows") or []),
    })


def _record_identity(record: Mapping[str, Any], position: int) -> dict[str, Any]:
    return {
        "position": int(position),
        "semantic_topic_id": str(record.get("_semantic_topic_id") or ""),
        "origin_concept_id": str(
            record.get("_phase32_origin_concept_id")
            or record.get("_source_grounding_concept_id")
            or record.get("_semantic_concept_id")
            or ""
        ),
        "segment_order": copy.deepcopy(record.get("_phase32_segment_order")),
    }


def build(
    db,
    job: models.UploadJob,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the shared hierarchy, route pool, and release provenance."""

    release = copy.deepcopy(dict(payload))
    source_document_hash = str(
        release.get("source_document_hash") or ""
    ).strip()
    if not source_document_hash.startswith("sha256:"):
        raise SnapshotError(
            "staged concept release has no frozen source-document hash"
        )
    release_sha = source_release_sha256(release)
    try:
        chapter, concepts, records = (
            build_concepts_release_files.transient_release_hierarchy(
                db, job, payload=release
            )
        )
    except ValueError as exc:
        raise SnapshotError(str(exc)) from exc
    if len(concepts) != len(records):
        raise SnapshotError(
            "staged concept records and transient concepts differ in count"
        )

    concept_rows: list[dict[str, Any]] = []
    route_concepts: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for position, (concept, record) in enumerate(
        zip(concepts, records), start=1
    ):
        concept_key = f"release:{release_sha[:20]}:{position:04d}"
        if concept_key in seen_keys:
            raise SnapshotError(f"duplicate release concept key {concept_key!r}")
        seen_keys.add(concept_key)
        machine_id = f"REL{release_sha[:12].upper()}C{position:03d}"
        title = str(concept.concept_title or "").strip()
        display_name = str(concept.concept_display_name or "").strip()
        if not title or not display_name:
            raise SnapshotError(
                f"staged concept row {position} has no explicit display name"
            )
        identity = _record_identity(record, position)
        row = {
            "concept_key": concept_key,
            "concept_machine_id": machine_id,
            "release_row_identity": identity,
            "concept_title": title,
            "concept_display_name": display_name,
            "parent_concept": str(concept.parent_concept or ""),
            "concept_details": str(concept.concept_details or ""),
            "keywords": str(concept.keywords or ""),
            "related_concepts": str(concept.related_concepts or ""),
            "digicards": str(concept.digicards or ""),
            "concept_source": str(concept.sources or ""),
        }
        concept_rows.append(row)
        route_row = {
            "concept_key": concept_key,
            "concept_title": title,
            "concept_display_name": display_name,
            "teaching_description": str(concept.concept_details or ""),
            "concept_details": str(concept.concept_details or ""),
            "parent_concept": str(concept.parent_concept or ""),
            "keywords": str(concept.keywords or ""),
            "related_concepts": str(concept.related_concepts or ""),
            "digicards": str(concept.digicards or ""),
            "concept_source": str(concept.sources or ""),
            "released_record": _semantic_evidence(record),
        }
        if isinstance(record.get("is_culmination"), bool):
            route_row["is_culmination"] = bool(record["is_culmination"])
        route_concepts.append(route_row)
        provenance.append({
            "concept_key": concept_key,
            "release_row_identity": identity,
        })

    rows_by_topic_object: dict[int, list[dict[str, Any]]] = {}
    topic_objects: dict[int, models.Topic] = {}
    for concept, row in zip(concepts, concept_rows):
        topic = concept.topic
        marker = id(topic)
        topic_objects[marker] = topic
        rows_by_topic_object.setdefault(marker, []).append(row)
    ordered_topics = sorted(
        topic_objects.items(),
        key=lambda item: (int(item[1].source_order or 0), item[1].topic_title),
    )
    snapshot_topics = []
    for marker, topic in ordered_topics:
        snapshot_topics.append({
            "topic_title": str(topic.topic_title or ""),
            "topic_display_name": str(
                topic.topic_display_name or topic.topic_title or ""
            ),
            "pre_post_learning": str(topic.pre_post_learning or ""),
            "topic_concept_labels": "",
            "related_topics": str(topic.related_topics or ""),
            "topic_description": str(topic.topic_description or ""),
            "concepts": rows_by_topic_object[marker],
        })

    snapshot = {
        "source_concept_release_sha256": release_sha,
        "target_chapter_id": int(release.get("target_chapter_id") or 0),
        "concept_provenance": provenance,
        "chapter": {
            "chapter_title": str(chapter.chapter_title or ""),
            "chapter_display_name": str(
                chapter.chapter_display_name or chapter.chapter_title or ""
            ),
            "pre_topics": str(chapter.pre_topics or ""),
            "post_topics": str(chapter.post_topics or ""),
            "chapter_description": str(chapter.chapter_description or ""),
        },
        "topics": snapshot_topics,
    }
    metadata = {
        "subject": str(chapter.subject or ""),
        "board": str(chapter.board or ""),
        "grade": str(chapter.grade or ""),
        "unit": str(chapter.unit or ""),
        "chapter_title": str(chapter.chapter_title or ""),
        "chapter_code": str(chapter.chapter_code or ""),
    }
    question_task_inventory = copy.deepcopy(
        release.get("question_task_inventory") or {}
    )
    staged_mined_types = copy.deepcopy(release.get("mined_types") or {})
    inventory_mined_types = question_task_inventory.get("mined_types")
    if isinstance(inventory_mined_types, Mapping) and isinstance(
        staged_mined_types, Mapping
    ):
        merged_mined_types = copy.deepcopy(dict(inventory_mined_types))
        merged_mined_types.update(staged_mined_types)
        question_task_inventory["mined_types"] = merged_mined_types
    elif staged_mined_types:
        question_task_inventory["mined_types"] = staged_mined_types
    question_task_inventory["type_case_rows"] = copy.deepcopy(
        release.get("type_case_rows") or []
    )

    return {
        "source_concept_release_sha256": release_sha,
        "source_document_hash": source_document_hash,
        "snapshot": snapshot,
        "concepts": route_concepts,
        "concepts_by_key": {
            row["concept_key"]: row for row in route_concepts
        },
        "concept_records_by_key": {
            row["concept_key"]: row for row in concept_rows
        },
        "metadata": metadata,
        "question_task_inventory": question_task_inventory,
    }
