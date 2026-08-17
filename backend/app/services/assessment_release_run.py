"""End-to-end assessment release orchestration for one generated job.

The glue between the finished Build Concepts job and the two output files:

    source atoms  ->  cell classification (author + critic)
                  ->  semantic question/answer/rubric materialization
                  ->  Open/Specific answer-space verdict
                  ->  marking decomposition, duration, and keyboard verdict
                  ->  one-home routing (author + critic)
                  ->  level verdicts  ->  variant clustering
                  ->  group descriptions  ->  touched-group QA
                  ->  assessment Master Refiner
                  ->  immutable release  ->  atomic dual publication

Every semantic stage already exists; this module only sequences them,
carries identities, and never decides content itself. All model calls are
injectable per stage so the whole pipeline is testable offline; the live
defaults inside each stage module are used when nothing is injected.

Fail-closed composition: structural impossibility raises with its exact
diagnostic. Semantic dissent and Fixer decisions ship with stable warning
codes and row-private audit evidence. Nothing is dropped anywhere in the
chain.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import models
from . import assessment_answer_restriction as answer_restriction
from . import assessment_blueprint
from . import assessment_cells as cell_decisions
from . import assessment_grouping as grouping
from . import assessment_marking as marking
from . import assessment_materialization as materialization
from . import assessment_quality as quality
from . import assessment_release as rel
from . import assessment_release_snapshot as release_snapshot
from . import assessment_release_service as release_service
from . import assessment_routing as routing
from . import assessment_source_inventory as source_inventory
from . import assessment_profile
from . import build_concepts_release
from . import progress, uploads
from . import release_refiner
from .phase3 import envelope as phase3_envelope
from .phase3 import kernel

_CELL_AUDIT_FIELD = "_aegis_assessment_cell_verdict"
_MATERIALIZATION_AUDIT_FIELD = "_aegis_assessment_materialization"
_ANSWER_RESTRICTION_AUDIT_FIELD = (
    "_aegis_assessment_answer_restriction"
)
_MARKING_AUDIT_FIELD = "_aegis_assessment_marking"
_ROUTE_AUDIT_FIELD = "_aegis_assessment_route"
_LEVEL_AUDIT_FIELD = "_aegis_assessment_level_verdict"
_CLUSTER_AUDIT_FIELD = "_aegis_assessment_variant_cluster"
_DESCRIPTION_AUDIT_FIELD = "_aegis_assessment_group_description"
_QUALITY_AUDIT_FIELD = "_aegis_assessment_group_quality"
_MASTER_REFINEMENT_AUDIT_FIELD = (
    "_aegis_assessment_master_refinement"
)

_CELL_WARNING = "assessment_cell_review"
_MATERIALIZATION_WARNING = "assessment_materialization_review"
_ANSWER_RESTRICTION_WARNING = "assessment_answer_restriction_review"
_MARKING_WARNING = "assessment_marking_review"
_ROUTE_WARNING = "assessment_route_review"
_LEVEL_WARNING = "assessment_level_review"
_CLUSTER_WARNING = "assessment_variant_cluster_review"
_DESCRIPTION_WARNING = "assessment_group_description_review"
_QUALITY_WARNING = "assessment_group_quality_review"
_MASTER_REFINEMENT_WARNING = "assessment_master_refiner_review"

_CELLS_SNAPSHOT = "source.phase3-assessment-cells.json"
_MATERIALIZATIONS_SNAPSHOT = "source.phase3-assessment-materializations.json"
_ANSWER_RESTRICTIONS_SNAPSHOT = (
    "source.phase3-assessment-answer-restrictions.json"
)
_MARKINGS_SNAPSHOT = "source.phase3-assessment-markings.json"
_ROUTES_SNAPSHOT = "source.phase3-assessment-routes.json"
_LEVELS_SNAPSHOT = "source.phase3-assessment-levels.json"
_GROUPS_SNAPSHOT = "source.phase3-assessment-groups.json"
_MASTER_REFINEMENTS_SNAPSHOT = (
    "source.phase3-assessment-master-refinements.json"
)


class ReleaseRunError(ValueError):
    """The job cannot enter the release pipeline at all."""


def _authority_pair(
    authorities: Mapping[str, Any], key: str,
) -> tuple[Any | None, Any | None]:
    """Return one injected author/critic pair without inventing defaults."""

    value = authorities.get(key, ())
    if isinstance(value, (tuple, list)):
        return (
            value[0] if value else None,
            value[1] if len(value) > 1 else None,
        )
    return (value, None) if callable(value) else (None, None)


def _stable_authority(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project a decision onto its timestamp-free release authority."""

    authority = result.get("authority")
    source = authority if isinstance(authority, Mapping) else {}
    if not source and isinstance(result.get("decision"), Mapping):
        source = result["decision"]
    decision_key = str(
        source.get("decision_key") or source.get("key") or ""
    )
    policy_version = str(source.get("policy_version") or "")
    review_flags = [
        str(flag)
        for flag in source.get("review_flags") or []
        if str(flag).strip()
    ]
    projected: dict[str, Any] = {}
    if decision_key:
        projected["decision_key"] = decision_key
    if policy_version:
        projected["policy_version"] = policy_version
    if review_flags:
        projected["review_flags"] = review_flags
    if bool(source.get("fixer")):
        projected["fixer"] = True
    return projected


def _needs_review(result: Mapping[str, Any]) -> bool:
    authority = _stable_authority(result)
    return bool(
        result.get("flags")
        or authority.get("review_flags")
        or authority.get("fixer")
    )


def _append_warning(record: dict, warning: str) -> None:
    flags = [str(flag) for flag in record.get("flags") or []]
    if warning not in flags:
        flags.append(warning)
    record["flags"] = flags


def _candidate_rows_exactly(
    stage: str,
    candidates: list[Mapping],
    rows: list[Mapping],
) -> dict[str, Mapping]:
    """Bind one ordered pass result to every candidate, fail-closed."""

    expected = [
        str(candidate.get("candidate_id") or "")
        for candidate in candidates
    ]
    returned: list[str] = []
    by_id: dict[str, Mapping] = {}
    duplicates: set[str] = set()
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ReleaseRunError(
                f"{stage} row {position} is not an object"
            )
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id in by_id:
            duplicates.add(candidate_id)
        returned.append(candidate_id)
        by_id[candidate_id] = row
    if returned != expected or duplicates or len(by_id) != len(expected):
        raise ReleaseRunError(
            f"{stage} changed candidate coverage or order: "
            f"expected={expected!r}; returned={returned!r}; "
            f"duplicates={sorted(duplicates)!r}"
        )
    return by_id


def _job_artifact_directory(job_id: int) -> Path | None:
    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper):
        return None
    try:
        directory = Path(helper(int(job_id)))
    except (OSError, TypeError, ValueError):
        return None
    return directory if directory.is_dir() else None


def _decision_context(
    job_id: int,
    *,
    envelope_sha256: str | None,
    decision_store: kernel.DecisionStore | None,
) -> tuple[str, kernel.DecisionStore, Path | None]:
    """Resolve one verified envelope/store pair for every assessment pass.

    Production uses the job's sealed Phase-3 wrapper and durable decision
    directory. Tests may inject both identities together; accepting only one
    would make replay provenance ambiguous, so that is a mechanical error.
    """

    if (envelope_sha256 is None) != (decision_store is None):
        raise ReleaseRunError(
            "envelope_sha256 and decision_store must be supplied together"
        )
    if envelope_sha256 is not None and decision_store is not None:
        envelope_sha = str(envelope_sha256).strip()
        if not envelope_sha:
            raise ReleaseRunError("envelope_sha256 must not be empty")
        store_directory = getattr(decision_store, "_directory", None)
        snapshot_directory = (
            Path(store_directory).parent if store_directory is not None
            else _job_artifact_directory(job_id)
        )
        return envelope_sha, decision_store, snapshot_directory

    artifact_directory = _job_artifact_directory(job_id)
    if artifact_directory is None:
        raise ReleaseRunError(
            "the job has no durable Phase-3 artifact directory"
        )
    envelope_path = artifact_directory / "source.phase3-envelope.json"
    try:
        wrapper = json.loads(envelope_path.read_text(encoding="utf-8"))
        if not isinstance(wrapper, Mapping):
            raise ValueError("the envelope wrapper is not an object")
        envelope = phase3_envelope.validate(wrapper.get("envelope") or {})
    except (OSError, TypeError, ValueError) as exc:
        raise ReleaseRunError(
            "the job's sealed Phase-3 envelope is unavailable or invalid: "
            f"{exc}"
        ) from exc
    envelope_sha = str(envelope.get("envelope_sha256") or "").strip()
    if not envelope_sha:
        raise ReleaseRunError("the verified Phase-3 envelope has no seal")
    return (
        envelope_sha,
        kernel.DecisionStore(artifact_directory / "phase3-decisions"),
        artifact_directory,
    )


def _concept_evidence(concept: Mapping[str, Any]) -> dict[str, Any]:
    concept_key = str(concept.get("concept_key") or "")
    return {
        "concept_key": concept_key,
        "concept_id": concept_key,
        "concept_machine_id": _label_base(concept),
        "concept_title": str(concept.get("concept_title") or ""),
        "concept_display_name": str(
            concept.get("concept_display_name") or ""
        ),
        "teaching_description": str(
            concept.get("teaching_description")
            or concept.get("concept_details")
            or ""
        ),
        "parent_concept": str(concept.get("parent_concept") or ""),
        "keywords": str(concept.get("keywords") or ""),
    }


def _group_evidence(group: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic group state only; private audits never become new evidence."""

    private = {
        _CLUSTER_AUDIT_FIELD,
        _DESCRIPTION_AUDIT_FIELD,
        _QUALITY_AUDIT_FIELD,
        "authority",
        "flags",
    }
    return {
        key: value for key, value in group.items() if key not in private
    }


def _learner_text_snapshot(candidates: list[Mapping]) -> list[tuple]:
    return [
        (
            str(candidate.get("candidate_id") or ""),
            candidate.get("question"),
            candidate.get("question_text"),
        )
        for candidate in candidates
    ]


def _assert_learner_text_unchanged(
    before: list[tuple], candidates: list[Mapping],
) -> None:
    after = _learner_text_snapshot(candidates)
    if after == before:
        return
    before_by_id = {str(row[0]): row[1:] for row in before}
    after_by_id = {str(row[0]): row[1:] for row in after}
    changed = sorted(
        candidate_id
        for candidate_id in set(before_by_id) | set(after_by_id)
        if before_by_id.get(candidate_id) != after_by_id.get(candidate_id)
    )
    raise grouping.GroupingError(
        "assessment grouping altered immutable learner-facing question text"
        + (f": {changed}" if changed else "")
    )


def _write_snapshot(
    directory: Path | None, filename: str, payload: Mapping[str, Any],
) -> None:
    if directory is None:
        return
    from . import canonical_source_phase3 as phase3_core

    try:
        phase3_core._atomic_write(
            directory / filename,
            json.dumps(
                dict(payload), ensure_ascii=False, indent=1, sort_keys=True
            ),
        )
    except OSError as exc:
        raise ReleaseRunError(
            f"could not persist assessment decision snapshot {filename}: "
            f"{exc}"
        ) from exc


def _snapshot_levels(
    directory: Path | None,
    *,
    envelope_sha256: str,
    candidates: list[Mapping],
) -> None:
    rows = []
    for candidate in candidates:
        audit = candidate.get(_LEVEL_AUDIT_FIELD)
        if not isinstance(audit, Mapping):
            continue
        rows.append({
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "concept_key": str(candidate.get("concept_key") or ""),
            "tier": str(audit.get("tier") or ""),
            "rationale": str(audit.get("rationale") or ""),
            "flags": list(audit.get("flags") or []),
            "authority": dict(audit.get("authority") or {}),
        })
    _write_snapshot(
        directory,
        _LEVELS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "levels": rows,
            "levels_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_groups(
    directory: Path | None,
    *,
    envelope_sha256: str,
    groups: list[Mapping],
) -> None:
    rows = []
    for group in groups:
        rows.append({
            "group_key": str(group.get("group_key") or ""),
            "concept_key": str(group.get("concept_key") or ""),
            "tier": str(group.get("group_type") or ""),
            "family": str(group.get("family") or ""),
            "member_candidate_ids": list(
                group.get("member_candidate_ids") or []
            ),
            "semantic_description": str(
                group.get("semantic_description") or ""
            ),
            "flags": list(group.get("flags") or []),
            _CLUSTER_AUDIT_FIELD: dict(
                group.get(_CLUSTER_AUDIT_FIELD) or {}
            ),
            _DESCRIPTION_AUDIT_FIELD: dict(
                group.get(_DESCRIPTION_AUDIT_FIELD) or {}
            ),
            _QUALITY_AUDIT_FIELD: dict(
                group.get(_QUALITY_AUDIT_FIELD) or {}
            ),
        })
    _write_snapshot(
        directory,
        _GROUPS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "groups": rows,
            "groups_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_cells(
    directory: Path | None,
    *,
    envelope_sha256: str,
    cells: list[Mapping],
) -> None:
    rows = [
        {
            "cell_id": str(cell.get("cell_id") or ""),
            "accepted_source_qids": list(
                cell.get("accepted_source_qids") or []
            ),
            "sheet_kind": str(cell.get("sheet_kind") or ""),
            "question_category": str(
                cell.get("question_category") or ""
            ),
            "cognitive_skill": str(cell.get("cognitive_skill") or ""),
            "difficulty": str(cell.get("difficulty") or ""),
            "marks": cell.get("marks"),
            "flags": list(cell.get("flags") or []),
            "audit": dict(cell.get(_CELL_AUDIT_FIELD) or {}),
        }
        for cell in cells
    ]
    _write_snapshot(
        directory,
        _CELLS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "cells": rows,
            "cells_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_materializations(
    directory: Path | None,
    *,
    envelope_sha256: str,
    candidates: list[Mapping],
) -> None:
    rows = [
        {
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "source_atom_ids": list(candidate.get("source_atom_ids") or []),
            "blueprint_cell_id": str(
                candidate.get("blueprint_cell_id") or ""
            ),
            "flags": list(candidate.get("flags") or []),
            "audit": dict(
                candidate.get(_MATERIALIZATION_AUDIT_FIELD) or {}
            ),
        }
        for candidate in candidates
    ]
    _write_snapshot(
        directory,
        _MATERIALIZATIONS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "materializations": rows,
            "materializations_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_answer_restrictions(
    directory: Path | None,
    *,
    envelope_sha256: str,
    candidates: list[Mapping],
) -> None:
    rows = []
    for candidate in candidates:
        audit = candidate.get(_ANSWER_RESTRICTION_AUDIT_FIELD)
        if not isinstance(audit, Mapping):
            continue
        rows.append({
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "answer_restriction": str(
                candidate.get("answer_restriction") or ""
            ),
            "restriction_reason": str(
                candidate.get("restriction_reason") or ""
            ),
            "answer_space_contract": str(
                audit.get("answer_space_contract") or ""
            ),
            "required_elements": list(
                audit.get("required_elements") or []
            ),
            "accepted_variations": list(
                audit.get("accepted_variations") or []
            ),
            "evidence": str(audit.get("evidence") or ""),
            "rationale": str(audit.get("rationale") or ""),
            "registry": dict(audit.get("registry") or {}),
            "flags": list(audit.get("flags") or []),
            "authority": dict(audit.get("authority") or {}),
        })
    _write_snapshot(
        directory,
        _ANSWER_RESTRICTIONS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "answer_restrictions": rows,
            "answer_restrictions_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_markings(
    directory: Path | None,
    *,
    envelope_sha256: str,
    candidates: list[Mapping],
) -> None:
    rows = []
    for candidate in candidates:
        audit = candidate.get(_MARKING_AUDIT_FIELD)
        if not isinstance(audit, Mapping):
            continue
        rows.append({
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "marks": candidate.get("marks"),
            "question_duration": candidate.get("question_duration"),
            "math_keyboard": str(candidate.get("math_keyboard") or ""),
            "answers": list(candidate.get("answers") or []),
            "sub_questions": list(candidate.get("sub_questions") or []),
            "rationale": str(audit.get("rationale") or ""),
            "blueprint_authority": dict(
                audit.get("blueprint_authority") or {}
            ),
            "flags": list(audit.get("flags") or []),
            "authority": dict(audit.get("authority") or {}),
        })
    _write_snapshot(
        directory,
        _MARKINGS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "markings": rows,
            "markings_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_master_refinements(
    directory: Path | None,
    *,
    envelope_sha256: str,
    payload: Mapping[str, Any],
) -> None:
    """Persist stable candidate/group Refiner audits beside the release."""

    rows: list[dict[str, Any]] = []
    for unit_kind, collection, id_field in (
        ("candidate", "candidates", "candidate_id"),
        ("group", "groups", "group_key"),
    ):
        for record in payload.get(collection) or []:
            audit = record.get(_MASTER_REFINEMENT_AUDIT_FIELD)
            if not isinstance(audit, Mapping):
                continue
            rows.append({
                "unit_kind": unit_kind,
                "unit_id": str(record.get(id_field) or ""),
                "status": str(audit.get("status") or ""),
                "changed_paths": list(audit.get("changed_paths") or []),
                "rationale": str(audit.get("rationale") or ""),
                "review_flags": list(audit.get("review_flags") or []),
                "fixer": bool(audit.get("fixer")),
                "authority": {
                    "decision_key": str(audit.get("decision_key") or ""),
                    "policy_version": str(
                        audit.get("policy_version") or ""
                    ),
                },
            })
    diff = dict(payload.get("refinements") or {})
    stable = {"rows": rows, "diff": diff}
    _write_snapshot(
        directory,
        _MASTER_REFINEMENTS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "rows": rows,
            "diff": diff,
            "rows_sha256": rel.sha256_json(stable),
        },
    )


def _snapshot_routes(
    directory: Path | None,
    *,
    envelope_sha256: str,
    source_concept_release_sha256: str,
    placements: list[Mapping],
) -> None:
    rows = [
        {
            "candidate_id": str(placement.get("candidate_id") or ""),
            "concept_key": placement.get("concept_key"),
            "basis": str(placement.get("basis") or ""),
            "evidence": str(placement.get("evidence") or ""),
            "rationale": str(placement.get("rationale") or ""),
            "flags": list(placement.get("flags") or []),
            "authority": dict(placement.get("authority") or {}),
        }
        for placement in placements
    ]
    _write_snapshot(
        directory,
        _ROUTES_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "source_concept_release_sha256": (
                source_concept_release_sha256
            ),
            "placements": rows,
            "placements_sha256": rel.sha256_json(rows),
        },
    )


def _bind_explicit_cells(
    atoms: list[Mapping],
    blueprint_cells: list[Mapping],
    *,
    profile: Mapping,
    concept_keys: set[str],
) -> list[dict]:
    """Mechanically bind an explicit one-to-one blueprint without spend."""

    if len(blueprint_cells) != len(atoms):
        raise ReleaseRunError(
            f"blueprint provides {len(blueprint_cells)} cells for "
            f"{len(atoms)} source atoms; reuse cells pair one-to-one"
        )
    cells = [dict(cell) for cell in blueprint_cells]
    assessment_blueprint.validate_cells(
        cells,
        strict_profile=True,
    )
    # Slice 3 cannot widen the existing Output-02 wire.  A future profile may
    # support Subjective only when materialization and publication do too.
    allowed_kinds = tuple(rel.SHEET_KINDS)
    defects: list[str] = []
    appears_in = str(profile.get("appears_in") or "").strip()
    if not appears_in:
        defects.append("assessment profile has no appears_in value")
    for position, (atom, cell) in enumerate(zip(atoms, cells), start=1):
        cell_id = str(cell.get("cell_id") or "")
        source_qid = str(atom.get("source_qid") or "")
        if int(cell.get("count") or 0) != 1:
            defects.append(f"{cell_id}: reuse count must equal 1")
        if cell.get("sheet_kind") not in allowed_kinds:
            defects.append(
                f"{cell_id}: sheet_kind is not allowed by the profile"
            )
        if not str(cell.get("question_category") or "").strip():
            defects.append(f"{cell_id}: missing question_category")
        if cell.get("cognitive_skill") not in bi.COGNITIVE_SKILLS:
            defects.append(f"{cell_id}: unknown cognitive_skill")
        if cell.get("difficulty") not in bi.DIFFICULTY_LEVELS:
            defects.append(f"{cell_id}: unknown difficulty")
        accepted = [
            str(value) for value in cell.get("accepted_source_qids") or []
            if str(value).strip()
        ]
        if accepted and accepted != [source_qid]:
            defects.append(
                f"{cell_id}: source binding must be exactly {source_qid!r}"
            )
        cell["accepted_source_qids"] = [source_qid]
        cell["appears_in"] = [appears_in]
        constraint = cell.get("concept_key")
        if constraint is None:
            constraint = cell.get("concept_id")
        if constraint is not None:
            if not isinstance(constraint, str) or constraint not in concept_keys:
                defects.append(
                    f"{cell_id}: concept constraint must name one staged "
                    "release concept key"
                )
            else:
                cell["concept_key"] = constraint
        authority = {
            "decision_key": "",
            "policy_version": cell_decisions.CELL_POLICY_VERSION,
            "review_flags": [],
            "mechanical_basis": "explicit_blueprint",
        }
        cell["source_policy"] = "reuse"
        cell["flags"] = []
        cell["authority"] = authority
        cell[_CELL_AUDIT_FIELD] = {
            "rationale": "explicit validated blueprint cell",
            "flags": [],
            "authority": authority,
        }
        if position > len(atoms):
            defects.append(f"{cell_id}: has no source atom")
    if defects:
        raise ReleaseRunError(
            "explicit assessment blueprint failed validation: "
            + "; ".join(defects[:20])
        )
    return cells


# --------------------------------------------------------------------------- #
# Label reservation (spec §8.5): source order, append-only continuation
# --------------------------------------------------------------------------- #

def _label_base(concept: Mapping[str, Any]) -> str:
    explicit = str(concept.get("concept_machine_id") or "").strip()
    if explicit:
        return explicit
    match = release_service._TITLE_TAG_RE.search(
        str(concept.get("concept_title") or "")
    )
    if match:
        return match.group(1).strip()
    raise ReleaseRunError("staged release concept has no machine identity")


def _next_label_index(db: Session, base: str) -> int:
    """Continue numbering after every label already committed for this base."""
    prefix = f"{base} Q"
    taken = [
        q.question_label
        for q in db.query(models.Question)
        .filter(models.Question.question_label.startswith(prefix))
    ]
    highest = 0
    for label in taken:
        tail = label[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run_release_for_job(
    db: Session,
    job_id: int,
    *,
    owner_sub: str,
    blueprint_cells: list[dict] | None = None,
    profile: Mapping | str | None = None,
    authorities: Mapping[str, Any] | None = None,
    envelope_sha256: str | None = None,
    decision_store: kernel.DecisionStore | None = None,
    supersedes: models.AssessmentRelease | None = None,
) -> models.AssessmentRelease:
    """Run the complete assessment pipeline for one generated job.

    ``authorities`` optionally injects (author, critic) call pairs per stage
    — keys: cells, materialize, answer_restriction, marking, route, level,
    cluster, describe, qa, refiner, plus an optional one-call ``fixer`` tuple.
    Absent keys use each stage's live default. Tests inject
    ``envelope_sha256`` and ``decision_store`` together; production verifies
    the job's sealed envelope and uses its durable store.
    Returns the published release; its readiness says whether the database
    upload is open, and its manifest carries every flag and unplaced item.
    """
    authorities = dict(authorities or {})
    profile = assessment_profile.resolve(profile)
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    staged_release = build_concepts_release.release_payload(job)
    if staged_release is None:
        raise ReleaseRunError(
            "this job has no staged Output-01 concept release; stage the "
            "concept release before building Output 02"
        )
    try:
        bridge = release_snapshot.build(db, job, staged_release)
    except release_snapshot.SnapshotError as exc:
        raise ReleaseRunError(str(exc)) from exc
    inventory = bridge["question_task_inventory"]
    if not (inventory.get("items") or []):
        raise ReleaseRunError(
            "the staged Output-01 release has no question/task inventory"
        )
    chapter_id = int(bridge["snapshot"].get("target_chapter_id") or 0)
    if not chapter_id:
        raise ReleaseRunError(
            "the staged Output-01 release has no target chapter identity"
        )

    envelope_sha, store, snapshot_directory = _decision_context(
        job.id,
        envelope_sha256=envelope_sha256,
        decision_store=decision_store,
    )

    meta = dict(bridge["metadata"])
    source_release_sha = str(
        bridge["source_concept_release_sha256"]
    )
    concept_payload = list(bridge["concepts"])
    concept_records_by_key = dict(bridge["concept_records_by_key"])
    concept_keys = set(concept_records_by_key)
    fixer = _authority_pair(authorities, "fixer")[0]

    # Stage 1-2 — freeze the lossless source inventory.
    progress.log("Assessment release: freezing the source inventory.")
    built = source_inventory.build_source_atoms(
        inventory,
        source_document_hash=str(bridge["source_document_hash"]),
    )
    atoms = built["atoms"]

    # Stage 3 — explicit cells are a mechanically validated zero-spend path;
    # otherwise each source atom receives one cached kernel verdict.
    if blueprint_cells is not None:
        cells = _bind_explicit_cells(
            atoms,
            blueprint_cells,
            profile=profile,
            concept_keys=concept_keys,
        )
    else:
        progress.log(
            f"Classifying {len(atoms)} source atom(s) into blueprint cells.")
        cell_provider, cell_critic = _authority_pair(authorities, "cells")
        cells = cell_decisions.decide_cells(
            atoms,
            meta=meta,
            profile=profile,
            envelope_sha256=envelope_sha,
            provider=cell_provider,
            critic=cell_critic,
            store=store,
            fixer=fixer,
        )
        for cell in cells:
            cell[_CELL_AUDIT_FIELD] = {
                "rationale": str(cell.get("rationale") or ""),
                "flags": list(cell.get("flags") or []),
                "authority": dict(cell.get("authority") or {}),
            }
    _snapshot_cells(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        cells=cells,
    )

    # Stage 4 — materialize complete semantic candidates (zero-loss inside).
    progress.log(f"Materializing {len(atoms)} assessment candidate(s).")
    materialize_provider, materialize_critic = _authority_pair(
        authorities, "materialize"
    )
    materialized = materialization.materialize_candidates(
        list(zip(atoms, cells)),
        meta=meta,
        context={
            "source_concept_release_sha256": source_release_sha,
            "released_hierarchy": bridge["snapshot"],
        },
        envelope_sha256=envelope_sha,
        provider=materialize_provider,
        critic=materialize_critic,
        store=store,
        fixer=fixer,
    )
    candidates = materialized["candidates"]
    if len(candidates) != len(atoms) or len(candidates) != len(cells):
        raise ReleaseRunError(
            "assessment materialization did not preserve atom/cell cardinality"
        )
    for candidate, atom, cell in zip(candidates, atoms, cells):
        if candidate.get("source_atom_ids") != [atom.get("source_qid")] or (
            str(candidate.get("blueprint_cell_id") or "")
            != str(cell.get("cell_id") or "")
        ):
            raise ReleaseRunError(
                "assessment materialization changed an obligation identity"
            )
        materialization_needs_review = _needs_review(candidate)
        candidate["route_evidence"] = atom.get("route_evidence") or {}
        candidate[_CELL_AUDIT_FIELD] = dict(
            cell.get(_CELL_AUDIT_FIELD) or {}
        )
        if cell.get("concept_key"):
            candidate["blueprint_concept_key"] = str(cell["concept_key"])
        if _needs_review(cell):
            _append_warning(candidate, _CELL_WARNING)
        if materialization_needs_review:
            _append_warning(candidate, _MATERIALIZATION_WARNING)
    _snapshot_materializations(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        candidates=candidates,
    )

    learner_text_before = _learner_text_snapshot(candidates)

    # Stage 5 — decide Open/Specific from the complete, unweighted answer
    # space and the authoritative v2.0 registry.  The registry is evidence,
    # never an executable lookup; there is no local or sheet-kind default.
    progress.log(
        f"Classifying the answer space of {len(candidates)} candidate(s)."
    )
    restriction_provider, restriction_critic = _authority_pair(
        authorities, "answer_restriction"
    )
    restriction_rows = answer_restriction.decide_restrictions(
        candidates,
        meta=meta,
        envelope_sha256=envelope_sha,
        provider=restriction_provider,
        critic=restriction_critic,
        store=store,
        fixer=fixer,
    )
    restriction_by_candidate = _candidate_rows_exactly(
        "assessment answer restriction", candidates, restriction_rows
    )
    for candidate in candidates:
        verdict = restriction_by_candidate[candidate["candidate_id"]]
        candidate["answer_restriction"] = str(
            verdict.get("answer_restriction") or ""
        )
        candidate["restriction_reason"] = str(
            verdict.get("restriction_reason") or ""
        )
        candidate[_ANSWER_RESTRICTION_AUDIT_FIELD] = {
            "answer_restriction": candidate["answer_restriction"],
            "restriction_reason": candidate["restriction_reason"],
            "answer_space_contract": str(
                verdict.get("answer_space_contract") or ""
            ),
            "required_elements": list(
                verdict.get("required_elements") or []
            ),
            "accepted_variations": list(
                verdict.get("accepted_variations") or []
            ),
            "evidence": str(verdict.get("evidence") or ""),
            "rationale": str(verdict.get("rationale") or ""),
            "registry": dict(verdict.get("registry") or {}),
            "flags": list(verdict.get("flags") or []),
            "authority": _stable_authority(verdict),
        }
        if _needs_review(verdict):
            _append_warning(candidate, _ANSWER_RESTRICTION_WARNING)
    _assert_learner_text_unchanged(learner_text_before, candidates)
    _snapshot_answer_restrictions(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        candidates=candidates,
    )

    # Stage 6 — allocate the explicit cell's canonical marks without changing
    # the materializer's semantic answer space.  Duration and keyboard mode
    # are authored here too; the workbook renderer has no local defaults.
    progress.log(f"Authoring marking for {len(candidates)} candidate(s).")
    marking_provider, marking_critic = _authority_pair(
        authorities, "marking"
    )
    marking_rows = marking.decide_markings(
        list(zip(candidates, cells)),
        meta=meta,
        envelope_sha256=envelope_sha,
        provider=marking_provider,
        critic=marking_critic,
        store=store,
        fixer=fixer,
    )
    marking_by_candidate = _candidate_rows_exactly(
        "assessment marking", candidates, marking_rows
    )
    for candidate in candidates:
        verdict = marking_by_candidate[candidate["candidate_id"]]
        candidate["marks"] = verdict.get("marks")
        candidate["question_duration"] = verdict.get("question_duration")
        candidate["math_keyboard"] = str(
            verdict.get("math_keyboard") or ""
        )
        candidate["answers"] = list(verdict.get("answers") or [])
        candidate["sub_questions"] = list(
            verdict.get("sub_questions") or []
        )
        candidate[_MARKING_AUDIT_FIELD] = {
            "marks": candidate["marks"],
            "question_duration": candidate["question_duration"],
            "math_keyboard": candidate["math_keyboard"],
            "rationale": str(verdict.get("rationale") or ""),
            "blueprint_authority": dict(
                verdict.get("blueprint_authority") or {}
            ),
            "flags": list(verdict.get("flags") or []),
            "authority": _stable_authority(verdict),
        }
        if _needs_review(verdict):
            _append_warning(candidate, _MARKING_WARNING)
    _assert_learner_text_unchanged(learner_text_before, candidates)
    _snapshot_markings(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        candidates=candidates,
    )

    # Stage 7 — route only across the immutable staged Output-01 concepts.
    progress.log(
        f"Routing {len(candidates)} candidate(s) across "
        f"{len(concept_payload)} concept(s).")
    route_provider, route_critic = _authority_pair(authorities, "route")
    routed = routing.route_candidates(
        candidates,
        concept_payload,
        meta=meta,
        envelope_sha256=envelope_sha,
        source_concept_release_sha256=source_release_sha,
        provider=route_provider,
        critic=route_critic,
        store=store,
        fixer=fixer,
    )
    placements = routed["placements"]
    placement_by_candidate = {
        p["candidate_id"]: p for p in placements
    }
    if list(placement_by_candidate) != [
        str(candidate.get("candidate_id") or "") for candidate in candidates
    ]:
        raise ReleaseRunError(
            "assessment routing changed candidate coverage or order"
        )
    for candidate in candidates:
        placement = placement_by_candidate[candidate["candidate_id"]]
        route_audit = {
            "concept_key": placement.get("concept_key"),
            "basis": str(placement.get("basis") or ""),
            "evidence": str(placement.get("evidence") or ""),
            "rationale": str(placement.get("rationale") or ""),
            "flags": list(placement.get("flags") or []),
            "authority": dict(placement.get("authority") or {}),
        }
        candidate[_ROUTE_AUDIT_FIELD] = route_audit
        if placement.get("flags"):
            _append_warning(candidate, _ROUTE_WARNING)
    _snapshot_routes(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        source_concept_release_sha256=source_release_sha,
        placements=placements,
    )

    # Stage 8 — a recorded level verdict for every valid routed candidate.
    # No blueprint label is executable tier logic. An unresolved home remains
    # explicitly unplaced.
    eligible: list[dict] = []
    for candidate in candidates:
        placement = placement_by_candidate.get(candidate["candidate_id"])
        concept_key = placement.get("concept_key") if placement else None
        if concept_key is None or concept_key not in concept_records_by_key:
            # An uncertified home is never grouped or labelled: the best
            # evidence-bound route stays visible on the placement record,
            # and the candidate rides the manifest's unplaced ledger,
            # blocking database upload (spec §14).
            continue
        candidate["concept_key"] = str(concept_key)
        eligible.append(candidate)

    level_provider, level_critic = _authority_pair(authorities, "level")
    level_rows = grouping.decide_levels(
        [
            {
                "candidate": candidate,
                "concept": _concept_evidence(
                    concept_records_by_key[str(candidate["concept_key"])]
                ),
            }
            for candidate in eligible
        ],
        meta=meta,
        envelope_sha256=envelope_sha,
        provider=level_provider,
        critic=level_critic,
        store=store,
        fixer=fixer,
    )
    expected_level_ids = [
        str(candidate.get("candidate_id") or "") for candidate in eligible
    ]
    returned_level_ids: list[str] = []
    duplicate_level_ids: set[str] = set()
    level_by_candidate: dict[str, Mapping[str, Any]] = {}
    for level in level_rows:
        if not isinstance(level, Mapping):
            raise grouping.GroupingError(
                "assessment level verdict is not an object"
            )
        candidate_id = str(level.get("candidate_id") or "")
        if candidate_id in level_by_candidate:
            duplicate_level_ids.add(candidate_id)
        returned_level_ids.append(candidate_id)
        level_by_candidate[candidate_id] = level
    level_report = rel.zero_loss_report(
        expected_level_ids, returned_level_ids, []
    )
    if (
        not level_report["holds"]
        or duplicate_level_ids
        or len(returned_level_ids) != len(expected_level_ids)
    ):
        raise grouping.GroupingError(
            "assessment level coverage mismatch: "
            f"missing={level_report['missing']}; "
            f"unexpected={level_report['unexpected']}; "
            f"duplicates={sorted(duplicate_level_ids)}"
        )

    buckets: dict[tuple[str, str], list[dict]] = {}
    for candidate in eligible:
        candidate_id = str(candidate.get("candidate_id") or "")
        level = level_by_candidate[candidate_id]
        tier = str(level.get("tier") or "")
        if tier not in grouping.TIER_CODES:
            raise grouping.GroupingError(
                f"assessment level for {candidate_id!r} has unknown tier "
                f"{tier!r}"
            )
        level_flags = [
            str(flag) for flag in level.get("flags") or []
            if str(flag).strip()
        ]
        candidate[_LEVEL_AUDIT_FIELD] = {
            "tier": tier,
            "rationale": str(level.get("rationale") or ""),
            "flags": level_flags,
            "authority": _stable_authority(level),
        }
        if _needs_review(level):
            _append_warning(candidate, _LEVEL_WARNING)
        concept_key = str(candidate["concept_key"])
        buckets.setdefault((concept_key, tier), []).append(candidate)

    _assert_learner_text_unchanged(learner_text_before, candidates)
    _snapshot_levels(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        candidates=candidates,
    )

    # Stage 9 — decide every concept+tier partition, then assemble every
    # occupied group before any later pass sees the family set.
    cluster_provider, cluster_critic = _authority_pair(
        authorities, "cluster"
    )
    tier_order = {tier: index for index, tier in enumerate(grouping.TIER_CODES)}

    groups: list[dict] = []
    for (concept_key, tier), members in sorted(
        buckets.items(),
        key=lambda item: (item[0][0], tier_order[item[0][1]]),
    ):
        concept = concept_records_by_key[concept_key]
        concept_evidence = _concept_evidence(concept)
        clustered = grouping.cluster_tier(
            members,
            concept=concept_evidence,
            tier=tier,
            existing_groups=[],
            meta=meta,
            envelope_sha256=envelope_sha,
            provider=cluster_provider,
            critic=cluster_critic,
            store=store,
            fixer=fixer,
        )
        members_by_id = {m["candidate_id"]: m for m in members}
        machine = _label_base(concept)
        for sequence, family in enumerate(clustered["families"], start=1):
            member_ids = [
                str(candidate_id)
                for candidate_id in family.get("member_candidate_ids") or []
            ]
            unknown = sorted(set(member_ids) - set(members_by_id))
            if unknown:
                raise grouping.GroupingError(
                    f"variant family names unknown candidates: {unknown}"
                )
            record = grouping.group_record(
                concept_id=concept_key,
                concept_machine_id=machine,
                concept_name=str(concept.get("concept_display_name") or ""),
                tier=tier,
                sequence=sequence,
                member_candidate_ids=member_ids,
                family=family.get("family", ""),
                flags=[],
                authority=_stable_authority(clustered),
            )
            record["concept_key"] = concept_key
            record[_CLUSTER_AUDIT_FIELD] = {
                "family": str(family.get("family") or ""),
                "member_candidate_ids": member_ids,
                "flags": [
                    str(flag) for flag in clustered.get("flags") or []
                    if str(flag).strip()
                ],
                "authority": _stable_authority(clustered),
            }
            if _needs_review(clustered):
                _append_warning(record, _CLUSTER_WARNING)
            for member_id in member_ids:
                member = members_by_id[member_id]
                member["group_key"] = record["group_key"]
                member[_CLUSTER_AUDIT_FIELD] = {
                    "group_key": record["group_key"],
                    "family": str(family.get("family") or ""),
                    "flags": [
                        str(flag)
                        for flag in clustered.get("flags") or []
                        if str(flag).strip()
                    ],
                    "authority": _stable_authority(clustered),
                }
                if _needs_review(clustered):
                    _append_warning(member, _CLUSTER_WARNING)
                placement = placement_by_candidate[member["candidate_id"]]
                placement["group_key"] = record["group_key"]
            groups.append(record)

    duplicate_group_keys = rel.duplicate_group_keys(groups)
    if duplicate_group_keys:
        raise grouping.GroupingError(
            f"duplicate group_key values: {duplicate_group_keys}"
        )
    grouped_ids = [
        str(candidate_id)
        for group in groups
        for candidate_id in group.get("member_candidate_ids") or []
    ]
    duplicate_grouped_ids: set[str] = set()
    seen_grouped_ids: set[str] = set()
    for candidate_id in grouped_ids:
        if candidate_id in seen_grouped_ids:
            duplicate_grouped_ids.add(candidate_id)
        seen_grouped_ids.add(candidate_id)
    grouping_report = rel.zero_loss_report(
        expected_level_ids, grouped_ids, []
    )
    if (
        not grouping_report["holds"]
        or duplicate_grouped_ids
        or len(grouped_ids) != len(expected_level_ids)
    ):
        raise grouping.GroupingError(
            "assessment candidate-to-group coverage mismatch: "
            f"missing={grouping_report['missing']}; "
            f"unexpected={grouping_report['unexpected']}; "
            f"duplicates={sorted(duplicate_grouped_ids)}"
        )

    # Stage 10 — describe only after the complete family partition exists.
    describe_provider, describe_critic = _authority_pair(
        authorities, "describe"
    )
    all_members_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in eligible
    }
    for record in groups:
        concept_key = str(record["concept_key"])
        concept = concept_records_by_key[concept_key]
        family_members = [
            all_members_by_id[str(candidate_id)]
            for candidate_id in record.get("member_candidate_ids") or []
        ]
        description = grouping.describe_group(
            _group_evidence(record),
            family_members,
            concept=_concept_evidence(concept),
            meta=meta,
            envelope_sha256=envelope_sha,
            provider=describe_provider,
            critic=describe_critic,
            store=store,
            fixer=fixer,
        )
        record["semantic_description"] = str(
            description.get("description") or ""
        )
        record[_DESCRIPTION_AUDIT_FIELD] = {
            "description": record["semantic_description"],
            "flags": [
                str(flag) for flag in description.get("flags") or []
                if str(flag).strip()
            ],
            "authority": _stable_authority(description),
        }
        if _needs_review(description):
            _append_warning(record, _DESCRIPTION_WARNING)

    # Stage 11 — every group receives the complete, symmetric same-home/tier
    # sibling context. QA only flags and never changes the authored records.
    qa_provider, qa_critic = _authority_pair(authorities, "qa")
    quality_groups = [_group_evidence(group) for group in groups]
    for record in groups:
        concept_key = str(record["concept_key"])
        concept = concept_records_by_key[concept_key]
        family_members = [
            all_members_by_id[str(candidate_id)]
            for candidate_id in record.get("member_candidate_ids") or []
        ]
        siblings = [
            {
                "group": sibling,
                "members": [
                    all_members_by_id[str(candidate_id)]
                    for candidate_id in sibling.get("member_candidate_ids")
                    or []
                ],
            }
            for sibling in quality_groups
            if sibling.get("group_key") != record.get("group_key")
            and sibling.get("concept_key") == record.get("concept_key")
            and sibling.get("group_type") == record.get("group_type")
        ]
        review = quality.review_group(
            _group_evidence(record),
            family_members,
            siblings=siblings,
            concept=_concept_evidence(concept),
            meta=meta,
            envelope_sha256=envelope_sha,
            provider=qa_provider,
            critic=qa_critic,
            store=store,
            fixer=fixer,
        )
        review_flags = [
            dict(flag) if isinstance(flag, Mapping) else str(flag)
            for flag in review.get("flags") or []
        ]
        record[_QUALITY_AUDIT_FIELD] = {
            "quality_review": str(review.get("quality_review") or ""),
            "flags": review_flags,
            "authority": _stable_authority(review),
        }
        if (
            _needs_review(review)
            or str(review.get("quality_review") or "") == "flagged"
        ):
            _append_warning(record, _QUALITY_WARNING)

    _assert_learner_text_unchanged(learner_text_before, candidates)
    _snapshot_groups(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        groups=groups,
    )

    # Stage 8.5 — labels from accepted source order, append-only.
    label_cursor: dict[str, int] = {}
    for candidate in candidates:
        concept_key = str(candidate.get("concept_key") or "")
        if not concept_key:
            continue
        concept = concept_records_by_key[concept_key]
        base = _label_base(concept)
        if base not in label_cursor:
            label_cursor[base] = _next_label_index(db, base)
        candidate["question_label"] = f"{base} Q{label_cursor[base]:02d}"
        label_cursor[base] += 1

    payload = {
        "source_concept_release_sha256": source_release_sha,
        "concept_snapshot": bridge["snapshot"],
        "source_atoms": atoms,
        "blueprint_cells": cells,
        "candidates": candidates,
        "groups": groups,
        "placements": placements,
    }

    # Stage 12 — read the actual rendered Master and polish only the explicit
    # answer/rubric/group-description whitelist.  The assessment-only module
    # decides each unit once, rolls back any identity/arithmetic/read-back
    # regression, and never blocks this release: unavailable units remain
    # authored byte-stable with visible review flags.
    progress.log(
        "Assessment release: refining final Master prose with immutable "
        "question and marking identities."
    )
    master_refiner_learner_text_before = _learner_text_snapshot(
        payload["candidates"]
    )
    refiner_provider, refiner_critic = _authority_pair(
        authorities, "refiner"
    )
    refined_records, refinement_diff, refinement_flags = (
        release_refiner.refine_release(
            [payload],
            metadata={**meta, "assessment_profile": profile},
            provider=refiner_provider,
            critic=refiner_critic,
            store=store,
            output_kind="assessment_master",
            envelope_sha256=envelope_sha,
            fixer=fixer,
        )
    )
    if len(refined_records) == 1 and isinstance(
        refined_records[0], Mapping
    ):
        payload = dict(refined_records[0])
    # The seam itself is never-raising; a malformed delegated return is the
    # one impossible local shape. Keep the original payload and surface the
    # mechanics-owned warning instead of blocking publication.
    else:
        for record in payload["candidates"]:
            _append_warning(record, _MASTER_REFINEMENT_WARNING)
        for record in payload["groups"]:
            if record.get("member_candidate_ids"):
                _append_warning(record, _MASTER_REFINEMENT_WARNING)
        refinement_diff = {
            "policy_version": "assessment-master-refiner-1",
            "decision_policies": {},
            "output_kind": "assessment_master",
            "changes": [],
            "review_flags": [
                "assessment Master Refiner returned no complete payload"
            ],
            "summary": (
                "assessment Master Refiner returned no complete payload; "
                "unrefined release staged"
            ),
            "resealed_after_refinement": False,
        }
        refinement_flags = list(refinement_diff["review_flags"])

    # Defense at the orchestration boundary: the outer Refiner seam also
    # catches import/delegation bugs. Its generic fallback preserves the
    # payload but cannot know assessment row shapes, so translate any such
    # release-level failure into the same visible per-row warning/audit here.
    # Normal per-unit Refiner flags already carry their own audit and are not
    # widened to unaffected siblings.
    missing_audit_units = []
    if refinement_flags:
        for unit_kind, collection, id_field, policy_version in (
            (
                "candidate",
                "candidates",
                "candidate_id",
                "assessment-master-refiner-candidate-1",
            ),
            (
                "group",
                "groups",
                "group_key",
                "assessment-master-refiner-group-1",
            ),
        ):
            for record in payload.get(collection) or []:
                if (
                    unit_kind == "group"
                    and not list(record.get("member_candidate_ids") or [])
                ):
                    # Required empty/NA shells validate and render, but they
                    # are deliberately not Master Refiner decision units.
                    continue
                if isinstance(
                    record.get(_MASTER_REFINEMENT_AUDIT_FIELD), Mapping
                ):
                    continue
                missing_audit_units.append(
                    (unit_kind, record, id_field, policy_version)
                )
    if missing_audit_units:
        reason = "; ".join(
            str(flag) for flag in refinement_flags if str(flag).strip()
        ) or "assessment Master Refiner unavailable"
        for unit_kind, record, _id_field, policy_version in (
            missing_audit_units
        ):
            _append_warning(record, _MASTER_REFINEMENT_WARNING)
            record[_MASTER_REFINEMENT_AUDIT_FIELD] = {
                "unit_kind": unit_kind,
                "decision_key": "",
                "policy_version": policy_version,
                "changed_paths": [],
                "rationale": reason,
                "review_flags": list(refinement_flags),
                "fixer": False,
                "status": "unavailable",
            }
        refinement_diff = {
            "policy_version": "assessment-master-refiner-1",
            "decision_policies": {
                "assessment.master_refiner.candidate": (
                    "assessment-master-refiner-candidate-1"
                ),
                "assessment.master_refiner.group": (
                    "assessment-master-refiner-group-1"
                ),
            },
            "output_kind": "assessment_master",
            "changes": [],
            "review_flags": list(refinement_flags),
            "summary": reason,
            "resealed_after_refinement": False,
        }
    payload["refinements"] = dict(refinement_diff)
    candidates = list(payload.get("candidates") or [])
    groups = list(payload.get("groups") or [])
    _assert_learner_text_unchanged(
        master_refiner_learner_text_before, candidates
    )
    _snapshot_master_refinements(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        payload=payload,
    )
    release = release_service.create_release(
        db,
        chapter_id=chapter_id,
        payload=payload,
        job_id=job.id,
        owner_sub=owner_sub,
        supersedes=supersedes,
    )
    release = release_service.publish_release(db, release)
    progress.log(
        f"Assessment release {release.release_uid} v{release.version} "
        f"published: readiness "
        f"{(release.diagnostics or {}).get('readiness')!r}.",
        level="success",
    )
    return release
