"""Immutable staged concept-release hierarchy used by the assessment release.

The staged Build Concepts release is the sole concept authority for the
lane's Master File — Output 02 off Output 01, Output 04 off Output 03 (OD4).
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
from . import directory
from . import identity


class SnapshotError(ValueError):
    """The staged concept release cannot form an immutable hierarchy.

    Since spec-step8 S9 this means GENUINE IMPOSSIBILITY only. A row that
    cannot be carried into the snapshot is skipped and recorded in the
    bridge's ``defects`` — see ``build``'s docstring for which of its six
    raises is which, and why.
    """


# The two row-level codes ``build`` records instead of raising.
SNAPSHOT_ROW_UNADDRESSABLE = "snapshot_row_unaddressable"
SNAPSHOT_ROW_UNNAMED = "snapshot_row_unnamed"


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
    """Return the shared hierarchy, route pool, and release provenance.

    SIX RAISES, AND WHICH IS WHICH (spec-step8 D8.4 / S9). ``SnapshotError``
    now means only "this snapshot genuinely cannot be built"; a row that
    cannot be carried is skipped and RECORDED, so Outputs 02/04 build from
    the sound rows instead of one row costing every question in the
    chapter.

    * GENUINE IMPOSSIBILITY, kept — no frozen ``sha256:`` source-document
      hash. It is the seal every downstream hash verification joins on, it
      cannot be minted here without inventing provenance, and D8.4 names
      this exact raise as the one ``SnapshotError`` retained.
    * GENUINE IMPOSSIBILITY, kept — the ``ValueError`` arm around the
      hierarchy. After S9 that call raises for exactly one reason, "this
      upload has no staged release", and a snapshot of nothing is not a
      snapshot.
    * GENUINE IMPOSSIBILITY, kept — concepts and records differing in
      count. The two lists are built one-to-one by the same loop, so this
      can only be a coding fault in the projection, and every
      ``zip(concepts, records)`` below would silently mis-pair a question
      with another concept's identity. Nothing here can be repaired by
      dropping a row.
    * GENUINE IMPOSSIBILITY, kept — a duplicate ``concept_key``. The key
      is minted from the release hash plus the row's own position, so a
      collision means positions repeated; every later join is by that key.
    * RECORDABLE DEFECT, de-raised — a row with no machine identity. It is
      one row that cannot be addressed (T9 B1: a thing that must be
      addressable cannot be addressed). Skipping it costs that concept's
      questions and names them; raising cost the whole Master.
    * RECORDABLE DEFECT, de-raised — a row with no explicit display name.
      Same shape, same cost, same remedy. The row reaches no sheet, is
      named, and the DB write is refused through ``defects``.

    ``defects`` rides the returned bridge and reaches the assessment
    lane's publication act through ``create_release``'s
    ``frozen["errors"]`` → ``diagnostics["payload_errors"]`` →
    ``_readiness`` BLOCKED — the transport T5-1's duplicate
    ``question_label`` already uses. It is NOT routed through
    ``build_concepts_release.structural_defects``, whose payload is the
    concept lane's ``records`` (D8.5b).
    """

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
        chapter, concepts, records, hierarchy_defects = (
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
    defects: list[dict[str, Any]] = [
        dict(finding) for finding in hierarchy_defects
    ]

    concept_rows: list[dict[str, Any]] = []
    route_concepts: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    # The rows that were actually CARRIED, parallel to ``concept_rows``. It
    # exists because a skipped row must not shift the topic join below by
    # one — the defect S9 would otherwise trade the raise for.
    carried: list[models.Concept] = []
    for position, (concept, record) in enumerate(
        zip(concepts, records), start=1
    ):
        concept_key = f"release:{release_sha[:20]}:{position:04d}"
        if concept_key in seen_keys:
            raise SnapshotError(f"duplicate release concept key {concept_key!r}")
        seen_keys.add(concept_key)
        # T4-4: the machine id comes off the PERSISTED column, not the
        # release hash. It used to be f"REL{release_sha[:12].upper()}C###", so
        # editing one character of any staged content re-minted every concept
        # id — and with it every ``group_key`` and every ``question_label``
        # built off it. ``source_release_sha256`` no longer participates in
        # identity at all. Transient staged rows carry the id
        # ``transient_release_hierarchy`` stamped, which is the same string
        # the publication will persist.
        machine_id = identity.machine_id_for_concept(concept)
        if not machine_id:
            # DE-RAISED (see the docstring): one unaddressable row, named.
            defects.append({
                "code": SNAPSHOT_ROW_UNADDRESSABLE,
                "position": int(position),
                "message": (
                    f"staged concept row {position} has no machine "
                    "identity, so no question can name it as its home and "
                    "the row carries no question into Output 02/04"
                ),
            })
            continue
        title = str(concept.concept_title or "").strip()
        display_name = str(concept.concept_display_name or "").strip()
        if not title or not display_name:
            # DE-RAISED (see the docstring): one unnameable row, named.
            defects.append({
                "code": SNAPSHOT_ROW_UNNAMED,
                "position": int(position),
                "concept_machine_id": machine_id,
                "message": (
                    f"staged concept row {position} has no explicit display "
                    "name, so no learner-visible group name can be composed "
                    "for it and the row carries no question into Output "
                    "02/04"
                ),
            })
            continue
        row_identity = _record_identity(record, position)
        row = {
            "concept_key": concept_key,
            "concept_machine_id": machine_id,
            "release_row_identity": row_identity,
            # The Pre lane's mechanical join: a staged Pre row carries the
            # PRC id its generated questions were authored to
            # (``_pre_concept_id``, stage_pre_release), and this is the one
            # place that id meets the minted concept_key — the generated
            # lane's cell decisions translate through it. Post rows carry
            # no such field and read "".
            "pre_concept_id": str(record.get("_pre_concept_id") or ""),
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
        carried.append(concept)
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
            "release_row_identity": row_identity,
        })

    rows_by_topic_object: dict[int, list[dict[str, Any]]] = {}
    topic_objects: dict[int, models.Topic] = {}
    for concept, row in zip(carried, concept_rows):
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
            # The topic's persisted identity (spec-step8 B4); the renderer
            # composes the title cell off it and strips it from cells.
            "topic_machine_id": identity.machine_id_for_topic(topic),
            "topic_display_name": str(
                topic.topic_display_name or topic.topic_title or ""
            ),
            "pre_post_learning": str(topic.pre_post_learning or ""),
            # §6:509-512's Topic→Concept join, filled on 23/23 populated
            # gold rows and hard-coded "" here until now (spec-step8 T3.7).
            # Composed off the SHARED ``identity.titled`` over this topic's
            # own concept rows, so the roster reads exactly as each
            # concept's own title cell does.
            "topic_concept_labels": ", ".join(
                identity.titled(
                    row["concept_title"], row["concept_machine_id"])
                for row in rows_by_topic_object[marker]
            ),
            "related_topics": str(topic.related_topics or ""),
            "topic_description": str(topic.topic_description or ""),
            "concepts": rows_by_topic_object[marker],
        })

    # The chapter band's book token, the writer's own derivation: the
    # first concept source that names a publication, anywhere in the
    # chapter.
    chapter_book = ""
    for chapter_topic in chapter.topics:
        for sibling in chapter_topic.concepts:
            chapter_book = directory.primary_book_source(sibling.sources)
            if chapter_book:
                break
        if chapter_book:
            break

    snapshot = {
        "source_concept_release_sha256": release_sha,
        "target_chapter_id": int(release.get("target_chapter_id") or 0),
        "concept_provenance": provenance,
        "chapter": {
            # The tagged "Name (machine id)" form, mirroring the concept
            # workbook writer's chapter band — the owner's review found
            # the Master's chapter cell was the one untagged identity in
            # the file. Composed with the same authority the writer uses;
            # the reader's ``strip_title_tag`` round-trips it.
            "chapter_title": directory.chapter_titled_cell(
                str(chapter.chapter_title or ""),
                str(chapter.board or ""),
                str(chapter.grade or ""),
                str(chapter.subject or ""),
                book=chapter_book,
            ),
            "chapter_display_name": str(
                chapter.chapter_display_name or chapter.chapter_title or ""
            ),
            # Carried, not omitted (spec-step8 T12/M4): the profile's
            # ``forced_blank_fields`` decides whether it ships, and a key
            # the snapshot never carries leaves that lever with nothing to
            # un-blank.
            "chapter_duration": str(chapter.chapter_duration or ""),
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
        # S9: what could not be carried, named. READ by
        # ``assessment_release_run`` (which stamps it onto the payload as
        # ``concept_snapshot_defects``) and from there by
        # ``assessment_release_service.create_release``, which appends it to
        # ``frozen["errors"]`` so the assessment lane's DB write is refused
        # while all four downloads stay open.
        "defects": defects,
    }
