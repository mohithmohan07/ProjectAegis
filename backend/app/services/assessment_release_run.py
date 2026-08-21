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

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import models
from . import assessment_answer_restriction as answer_restriction
from . import assessment_blueprint
from . import assessment_cells as cell_decisions
from . import assessment_dedup
from . import assessment_grouping as grouping
from . import assessment_prelearning_claim as prelearning_claim
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
from . import identity
from . import progress, uploads
from . import question_image_grid
from . import release_core
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
_IMAGE_GRID_AUDIT_FIELD = "_aegis_image_consolidation"

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
_IMAGE_GRID_WARNING = "assessment_image_grid_review"

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


GENERATED_SOURCE_POLICY = rel.GENERATED_SOURCE_POLICY

# The explicit marker a generated pre-learning item carries out of this
# lane and into ``models.Question.origin``.  Until now the de-facto
# "this was generated" marker was an EMPTY ``source_qid`` string, which
# is far too weak to rest a barrier on: every field is empty before it is
# filled.  This value is positively minted, only ever by the generated
# lane, and the barrier checks for it.
GENERATED_QUESTION_ORIGIN = release_service.QUESTION_ORIGIN_PRE_LEARNING


class ReleaseRunError(ValueError):
    """The job cannot enter the release pipeline at all."""


class GeneratedLaneError(ReleaseRunError):
    """A generated-question release cannot be bound mechanically."""


class SourceQuestionLeak(ReleaseRunError):
    """A generated release carries a current-chapter source question.

    Fail-closed, and it is allowed to be: this is identity accounting
    over ids this pipeline minted — "a gate that refuses to accept a
    broken artifact" (CLAUDE.md) — never a judgment about what any
    question means.  The semantic half of the same question ("is this
    generated item a source question reworded?") is a model verdict whose
    dissent flags, and lives in the generation pass's critic.
    """


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
        profile=profile,
    )
    # The profile answers this, through the one accessor (spec-step8
    # T12/M5b).
    allowed_kinds = assessment_profile.sheet_kinds(profile)
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


def source_inventory_qids(inventory: Mapping[str, Any]) -> list[str]:
    """Every current-chapter QID, read through the Pre lane's own reader.

    ``premap.inventory_qids`` is the one owner of "which fields of an
    inventory item carry a question identity" (it also reads the umbrella
    ``parent_qid`` a sub-part hangs under), so it is reused rather than
    re-derived here. A known set of minted ids — never a pattern over
    anything QID-shaped.
    """

    from .phase3 import premap

    return premap.inventory_qids({"inventory": dict(inventory or {})})


def _generated_lane_source_qids(
    job: models.UploadJob, staged: list[str],
) -> list[str]:
    """The chapter QIDs the Pre lane's leak accounting runs against.

    The source lane reads this set out of the release it routes against,
    because for that lane the inventory IS the material. The generated
    lane cannot: the Pre release payload deliberately carries no chapter
    question identity at all — "no QID from the chapter's question/task
    inventory may appear anywhere in a Pre row or the Pre release
    payload" (owner steer, 17 Aug 2026), which
    ``build_concepts_release.stage_pre_release`` implements by staging an
    empty ``question_task_inventory``.

    So the set is read from the two places that hold it WITHOUT putting a
    source question in a Pre artefact: the job's own question/task
    inventory column, and the POST sibling slot staged on the same job —
    an accepted, immutable snapshot of the same chapter inventory. Both
    are unioned with whatever the staged payload carried, so the
    accounting set can only ever GROW. That direction is the one that
    matters: a barrier reading a narrower set than the chapter actually
    has is blind, and blind is the failure the barrier exists to prevent.
    """

    qids: list[str] = list(staged)
    inventories: list[Mapping[str, Any]] = [dict(job.question_inventory or {})]
    post = build_concepts_release.release_payload(
        job, lane=build_concepts_release.LANE_POST
    )
    if post:
        inventories.append(dict(post.get("question_task_inventory") or {}))
    for inventory in inventories:
        for qid in source_inventory_qids(inventory):
            if qid not in qids:
                qids.append(qid)
    return qids


def generated_question_records(
    pre_questions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Flatten ``phase3.prequestions.build``'s result into release order.

    Its ``questions`` map is keyed by pre-concept and each concept's list
    is already in the authored order its own coverage plan asked for;
    this preserves both. Mechanics only — it selects and orders, it
    decides nothing. Blocked pre-concepts simply have no questions to
    carry; they already ship flagged from the generation pass.
    """

    records: list[dict[str, Any]] = []
    questions = (pre_questions or {}).get("questions")
    if not isinstance(questions, Mapping):
        return records
    for pre_concept_id in questions:
        for row in questions.get(pre_concept_id) or []:
            if not isinstance(row, Mapping):
                continue
            records.append({
                "pre_question_id": str(row.get("pre_question_id") or ""),
                "pre_concept_id": str(
                    row.get("pre_concept_id") or pre_concept_id
                ),
                "question_id": str(row.get("question_id") or ""),
                "question_text": str(row.get("question_text") or ""),
                "answer": str(row.get("answer") or ""),
                "rationale": str(row.get("rationale") or ""),
            })
    return records


def _generated_cell_id(pre_question_id: str) -> str:
    """One cell identity per generated question — minted, never supplied.

    Spec T6's first trap: ``assessment_materialization._candidate_id``
    seeds on ``(source_qid, cell_id)``, so with ``atom=None`` every
    question sharing a cell collapses onto ONE candidate_id and
    materialization raises "obligations repeat candidate_id".  The
    decided sidestep is one cell per generated question, and it is pinned
    here structurally rather than left to a caller convention: the cell's
    identity IS the generated question's identity, so two questions can
    never share a cell and one question can never own two.  (The other
    route — an instance seed inside ``_candidate_id``, which
    ``assessment_blueprint.obligations`` already anticipates with
    ``#{instance}`` — is deliberately NOT taken: it would move a seed
    every recorded Output-02 candidate id depends on.)
    """

    return "CELL-" + hashlib.sha256(
        rel.canonical_json(["generated", pre_question_id]).encode("utf-8")
    ).hexdigest()[:16]


def _blocked_generated_candidate(
    question: Mapping, defect: str
) -> dict[str, Any]:
    """A BLOCKED marker for a generated question that cannot route.

    The same shape the materialization seam mints for an obligation that
    exhausted its corrections (``assessment_materialization``'s blocked
    return), so the one exclusion seam, the one payload ledger
    (``materialization_blocked``), and the one ``release_state`` flag
    cover both. Minted before any cell verdict is paid for: the question's
    identity is its ``pre_question_id`` and the cell identity that WOULD
    have been minted for it, so the record stays addressable.
    """

    pre_question_id = str(question.get("pre_question_id") or "")
    cell_id = _generated_cell_id(pre_question_id)
    return {
        "candidate_id": cell_id,
        "assessment_eligibility": materialization.BLOCKED_ELIGIBILITY,
        "source_atom_ids": [],
        "blueprint_cell_id": cell_id,
        "sheet_kind": "",
        "source_policy": GENERATED_SOURCE_POLICY,
        "origin": GENERATED_QUESTION_ORIGIN,
        "generated_question": {
            "pre_question_id": pre_question_id,
            "pre_concept_id": str(question.get("pre_concept_id") or ""),
            "question_id": str(question.get("question_id") or ""),
            "question_text": str(question.get("question_text") or ""),
            "answer": str(question.get("answer") or ""),
            "rationale": str(question.get("rationale") or ""),
        },
        "flags": [
            "generated question blocked before its cell verdict: " + defect
        ],
    }


def _bind_generated_cells(
    questions: list[Mapping],
    blueprint_cells: list[Mapping] | None,
    *,
    profile: Mapping,
    concept_keys: set[str],
) -> list[dict]:
    """Bind one blueprint cell to each GENERATED question, without spend.

    The parallel of ``_bind_explicit_cells`` for the generated lane, and
    deliberately its parallel rather than a widening of it: the atom
    binding is absent (there is no source question to bind), the concept
    constraint it validates optionally is REQUIRED here so routing stays
    mechanical and free, ``accepted_source_qids`` must be empty, and the
    cell records ``source_policy`` "generate".
    """

    cells_in = [dict(cell) for cell in blueprint_cells or []]
    if len(cells_in) != len(questions):
        raise GeneratedLaneError(
            f"blueprint provides {len(cells_in)} cells for "
            f"{len(questions)} generated question(s); the generated lane "
            "pairs them one-to-one"
        )
    appears_in = str(profile.get("appears_in") or "").strip()
    defects: list[str] = []
    if not appears_in:
        defects.append("assessment profile has no appears_in value")
    allowed_kinds = assessment_profile.sheet_kinds(profile)

    cells: list[dict] = []
    seen_question_ids: set[str] = set()
    for position, (question, cell) in enumerate(
        zip(questions, cells_in), start=1
    ):
        if not isinstance(question, Mapping):
            defects.append(f"generated question {position} is not an object")
            continue
        pre_question_id = str(question.get("pre_question_id") or "").strip()
        if not pre_question_id:
            defects.append(
                f"generated question {position} has no pre_question_id"
            )
            continue
        if pre_question_id in seen_question_ids:
            defects.append(
                f"generated question id {pre_question_id!r} appears twice"
            )
            continue
        seen_question_ids.add(pre_question_id)
        for field in ("question_text", "answer"):
            if not str(question.get(field) or "").strip():
                defects.append(f"{pre_question_id}: empty {field}")

        cell_id = _generated_cell_id(pre_question_id)
        cell["cell_id"] = cell_id
        # Guarded because this runs BEFORE ``validate_cells`` (which owns
        # the robust integer check, ``assessment_blueprint.py:112-116``);
        # ``_bind_explicit_cells`` can call ``int()`` bare only because it
        # validates first.  An unguarded ``int()`` here would leave a bare
        # ValueError out of a path documented as fail-closed, and
        # ``api/build_assessments.py:437`` maps only ``ReleaseRunError``,
        # so a non-numeric count would surface as a 500 instead of a 400
        # naming the broken cell.
        try:
            declared_count = int(cell.get("count") or 1)
        except (TypeError, ValueError):
            defects.append(
                f"{cell_id}: count {cell.get('count')!r} is not an integer"
            )
        else:
            if declared_count != 1:
                defects.append(
                    f"{cell_id}: a generated cell carries exactly one question"
                )
        cell["count"] = 1
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
        if accepted:
            # Fail closed, not "ignore and overwrite": a generated cell
            # that names a source question is a broken artifact.
            defects.append(
                f"{cell_id}: a generated cell accepts no source question, "
                f"but names {accepted!r}"
            )
        cell["accepted_source_qids"] = []
        cell["appears_in"] = [appears_in]
        constraint = cell.get("concept_key")
        if constraint is None:
            constraint = cell.get("concept_id")
        if not isinstance(constraint, str) or constraint not in concept_keys:
            defects.append(
                f"{cell_id}: a generated cell must name one staged release "
                "concept key, so its question routes mechanically"
            )
        else:
            cell["concept_key"] = constraint
        decided_authority = dict(cell.get("authority") or {})
        if str(decided_authority.get("decision_key") or "").strip():
            # The cell carries a recorded verdict
            # (``decide_generated_cells``): its authority and any critic
            # dissent ride through — overwriting them here would drop the
            # dissent exactly where Q10 requires it recorded.
            authority = decided_authority
            decided_flags = [
                str(flag) for flag in cell.get("flags") or []
                if str(flag).strip()
            ]
            decided_rationale = (
                str(cell.get("rationale") or "").strip()
                or "generated pre-learning question cell"
            )
        else:
            authority = {
                "decision_key": "",
                "policy_version": cell_decisions.CELL_POLICY_VERSION,
                "review_flags": [],
                "mechanical_basis": "generated_question",
            }
            decided_flags = []
            decided_rationale = "generated pre-learning question cell"
        cell["source_policy"] = GENERATED_SOURCE_POLICY
        # The generated question rides its own cell, which is what the
        # materializer is handed as ``blueprint_cell``.  It is the
        # obligation's content: with no source atom there is nowhere else
        # per-obligation evidence can travel, and inventing a pseudo-atom
        # to carry it was explicitly rejected (spec T7) because
        # ``validate_source_atom`` requires a source_qid, a document hash
        # and a source kind — a pseudo-atom either fails honestly or is
        # filled with lies about provenance.
        cell["generated_question"] = {
            "pre_question_id": pre_question_id,
            "pre_concept_id": str(question.get("pre_concept_id") or ""),
            "question_id": str(question.get("question_id") or ""),
            "question_text": str(question.get("question_text") or ""),
            "answer": str(question.get("answer") or ""),
            "rationale": str(question.get("rationale") or ""),
        }
        cell["flags"] = list(decided_flags)
        cell["authority"] = authority
        cell[_CELL_AUDIT_FIELD] = {
            "rationale": decided_rationale,
            "flags": list(decided_flags),
            "authority": authority,
        }
        cells.append(cell)

    if defects:
        raise GeneratedLaneError(
            "generated assessment blueprint failed validation: "
            + "; ".join(defects[:20])
        )
    assessment_blueprint.validate_cells(
        cells, strict_profile=True, profile=profile,
    )
    return cells


# --------------------------------------------------------------------------- #
# The leak barrier (owner steer, 17 Aug 2026, point 3)
# --------------------------------------------------------------------------- #

# Channels the barrier deliberately does NOT scan, each for a recorded
# reason rather than by omission.  ``premap``/``prequestions`` record the
# same rule for the same reason and slice C1 fixed a real bug here: a
# critic asked "is this generated item a source question reworded?" may
# answer most usefully by naming the source question it resembles, and a
# guard that scanned critic prose would turn that ADVISORY dissent into
# the hardest gate in the pipeline — replaying the refusal forever, at
# zero spend, out of a content-addressed decision store, which Q10
# forbids in as many words.  So the barrier runs over the AUTHORED
# artefact and never over the auditor's account of it.  Nothing a learner
# would see rides any of these: they carry review and audit prose about
# the artefact, never its content.
_LEAK_GUARD_SKIPPED_KEYS = frozenset({
    "authority",
    "flags",
    "refinements",
    "review_flags",
})


def _authored_surface(
    value: Any, *, audit_keys: frozenset[str] = _LEAK_GUARD_SKIPPED_KEYS,
) -> Any:
    """Project a release payload onto the content it authored.

    Drops every review/audit channel (see ``_LEAK_GUARD_SKIPPED_KEYS``
    and the row-private ``_aegis_*`` markers, which hold each pass's
    rationale, critic dissent and decision authority).  The Refiner's
    ``refinements`` diff goes with them: it is an audit of changes whose
    authored values are already present on the candidates themselves,
    which ARE scanned — so nothing escapes accounting by being refined.

    ``audit_keys`` names those channels, and is a parameter because the
    two Pre outputs have different ones: this payload records dissent
    under ``flags``, while Output 03's records it under ``issues``.  Both
    boundaries share this one projection rather than each growing its own
    notion of "authored", so the two can never drift into disagreeing
    about what a source identity is.  It is always paired with the same
    set handed to ``_redact_audit_source_qids`` — a channel skipped by
    one and not redacted by the other would be a hole.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _authored_surface(entry, audit_keys=audit_keys)
            for key, entry in value.items()
            if str(key) not in audit_keys
            and not str(key).startswith("_aegis_")
        }
    if isinstance(value, (list, tuple)):
        return [
            _authored_surface(entry, audit_keys=audit_keys) for entry in value
        ]
    return value


def _redact_audit_source_qids(
    value: Any,
    source_qids: list[str],
    *,
    inside_audit: bool = False,
    audit_keys: frozenset[str] = _LEAK_GUARD_SKIPPED_KEYS,
) -> Any:
    """Drop source-QID tokens from the audit channels the barrier skips.

    The other half of the ordering rule, and the reason skipping those
    channels does not weaken the steer's "no QID anywhere in the Pre
    release payload".  Refusing on auditor prose would brick a finished
    run forever (Q10, and the reason ``_LEAK_GUARD_SKIPPED_KEYS``
    exists); dropping an id token from it is plainly mechanically
    applicable, which is the test CLAUDE.md sets for acting instead of
    stopping.  This is the SAME act ``premap._redact_ids`` performs on
    upstream provenance and ``prequestions`` performs on a recorded
    block, reused rather than re-implemented.

    Only string VALUES are rewritten. Every audit map in this payload is
    keyed by a fixed field name, never by an id, so nothing is keyed out
    of existence.

    ``audit_keys`` must be the SAME set handed to ``_authored_surface``
    for the same payload: this function decides what gets redacted and
    that one decides what gets refused, and a channel that one treats as
    audit while the other treats as authored is either an unredacted leak
    or a run bricked by auditor prose.
    """

    from .phase3 import premap

    if isinstance(value, Mapping):
        return {
            str(key): _redact_audit_source_qids(
                entry,
                source_qids,
                inside_audit=(
                    inside_audit
                    or str(key) in audit_keys
                    or str(key).startswith("_aegis_")
                ),
                audit_keys=audit_keys,
            )
            for key, entry in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _redact_audit_source_qids(
                entry,
                source_qids,
                inside_audit=inside_audit,
                audit_keys=audit_keys,
            )
            for entry in value
        ]
    if inside_audit and isinstance(value, str):
        return premap._redact_ids(value, list(source_qids))
    return value


def _refuse_source_questions(
    payload: Mapping[str, Any],
    *,
    source_qids: list[str],
    where: str,
) -> None:
    """Refuse any current-chapter source question in a generated release.

    Two mechanical halves, both identity accounting over ids this
    pipeline minted:

    * every candidate carries ``source_atom_ids == []``, an empty
      ``source_qid``, and positively declares ``source_policy
      "generate"`` — a marker minted only by ``_bind_generated_cells``,
      never an absence;
    * no QID from the chapter's own question/task inventory appears
      anywhere in the authored artefact (``premap._refuse_source_qids``,
      the SAME guard the rest of the Pre lane draws, not a second one).

    Fail-closed, which CLAUDE.md explicitly permits for "gates that
    refuse to accept a broken artifact": a source QID with two final
    homes also breaks Q2's exactly-once, and there is no correction that
    keeps both.

    **That fail-closed choice is deliberate, and this is its cost.**  The
    owner steer of 17 Aug 2026 states it directly for this check —
    "identity accounting, not judgment … and it fails closed" — and spec
    T7 decided it.  Recorded here rather than left as an omission: the
    marker half reads values this function's own lane minted, so it can
    only fire on a coding fault; the id half reads authored model text,
    and a model verdict is content-addressed, so if the materialization
    author ever emitted a chapter QID the refusal would replay at zero
    spend on every retry until an operator bumps the pass's
    ``policy_version`` or clears the affected decision from the store.
    Two things keep that a provider fault rather than a reachable one.
    First, the generated lane hands the author no channel carrying a
    chapter QID: its inputs are the generated question (``premap``
    already redacts ids out of it) and the concept snapshot, which is now
    refused PRE-SPEND in ``run_release_for_job`` precisely so it cannot
    become that channel.  Second, the refusal names the QID it found, so
    the operator step is mechanical.

    The alternative considered and NOT taken was moving this accounting
    into the per-unit ``checker`` handed to ``kernel.decide``, which would
    buy correction attempts and Fixer routing.  It is rejected because it
    would widen the SHARED materialization pass that Output 02 also runs
    — the widening spec T7 replaced with a bypass — to carry a Pre-lane
    concern, and because spending Fixer calls to re-author around a
    leaked identity treats as negotiable the one thing the steer made
    mechanical.
    """

    from .phase3 import premap

    defects: list[str] = []
    for candidate in payload.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        if list(candidate.get("source_atom_ids") or []):
            defects.append(
                f"{candidate_id} reuses source atom(s) "
                f"{list(candidate.get('source_atom_ids'))!r}"
            )
        if str(candidate.get("source_qid") or "").strip():
            defects.append(
                f"{candidate_id} carries source_qid "
                f"{candidate.get('source_qid')!r}"
            )
        if str(
            candidate.get("source_policy") or ""
        ) != GENERATED_SOURCE_POLICY:
            defects.append(
                f"{candidate_id} does not declare source_policy "
                f"{GENERATED_SOURCE_POLICY!r}"
            )
        if str(candidate.get("origin") or "") != GENERATED_QUESTION_ORIGIN:
            defects.append(
                f"{candidate_id} does not declare origin "
                f"{GENERATED_QUESTION_ORIGIN!r}"
            )
    for cell in payload.get("blueprint_cells") or []:
        if list(cell.get("accepted_source_qids") or []):
            defects.append(
                f"{cell.get('cell_id')!r} accepts source question(s) "
                f"{list(cell.get('accepted_source_qids'))!r}"
            )
        if str(cell.get("source_policy") or "") != GENERATED_SOURCE_POLICY:
            defects.append(
                f"{cell.get('cell_id')!r} does not declare source_policy "
                f"{GENERATED_SOURCE_POLICY!r}"
            )
    if list(payload.get("source_atoms") or []):
        defects.append(
            "the release carries "
            f"{len(list(payload.get('source_atoms')))} source atom(s)"
        )
    if defects:
        raise SourceQuestionLeak(
            f"{where} carries current-chapter source material: "
            + "; ".join(defects[:20])
            + "; no question is ever lifted out of the source into a "
            "Pre-Learning artefact (owner steer, 17 Aug 2026)"
        )
    premap._refuse_source_qids(
        _authored_surface(payload), list(source_qids), where=where,
    )


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
    """Continue numbering after every label already committed for this base.

    The body moved to ``identity.next_label_index`` (T5-3) so the Build
    Assessments lane runs the identical max-scan instead of its own count.
    This name is kept as the local seam.
    """
    return identity.next_label_index(db, base)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run_pre_release_for_job(
    db: Session,
    job_id: int,
    *,
    owner_sub: str,
    **kwargs,
) -> models.AssessmentRelease:
    """Output 04 — the Pre Master, from this job's staged Pre release.

    The one supported way to run the Pre lane: it reads the GENERATED
    questions out of the staged Output-03 payload (where
    ``build_concepts_release.stage_pre_release`` put them, projected from
    the same accepted snapshot as Output 03 itself) and hands them to the
    generated lane. No path here can reach the chapter's own questions.

    Refuses when the job has no staged Pre release at all. It does NOT
    refuse a Pre release that authored zero questions, and the two states
    are kept distinguishable rather than collapsed: a Pre release with
    concept rows and no generated question runs here and ships an empty
    Output 04.

    Zero ROWS runs too, since spec-step8 S9 — and the illustration that
    used to sit here ("the export refuses it") is gone with the raise it
    described. A chapter that assumes NOTHING stages a Pre release with no
    rows, and [measured] that now builds a real, empty Output 04 in state
    ``ready_for_upload``: ``transient_release_hierarchy`` records instead
    of raising, so nothing between here and the Master takes the artefact
    away. Whether that empty release may be WRITTEN is a separate question
    with a separate answer — premap's recorded ``pre_lane_verdict``, read
    by ``build_concepts_release.structural_defects`` — which is exactly
    the emptiness/corruption split D8.1 exists to keep apart.
    """

    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    questions = build_concepts_release.staged_generated_questions(job)
    if questions is None:
        # OD4 numbering: the Pre concept file is Output 01, its Master 02.
        raise ReleaseRunError(
            "this job has no staged Output-01 Pre-Learning concept release; "
            "stage the Pre release before building Output 02"
        )
    return run_release_for_job(
        db,
        job_id,
        owner_sub=owner_sub,
        lane=build_concepts_release.LANE_PRE,
        generated_questions=questions,
        **kwargs,
    )


def run_release_for_job(
    db: Session,
    job_id: int,
    *,
    owner_sub: str,
    lane: object = build_concepts_release.LANE_POST,
    blueprint_cells: list[dict] | None = None,
    generated_questions: list[Mapping] | None = None,
    profile: Mapping | str | None = None,
    authorities: Mapping[str, Any] | None = None,
    envelope_sha256: str | None = None,
    decision_store: kernel.DecisionStore | None = None,
    supersedes: models.AssessmentRelease | None = None,
    image_downloader: question_image_grid.Downloader | None = None,
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

    ``lane`` names which staged concept release this run routes against:
    ``"post"`` reads Output 01's slot and builds Output 02, ``"pre"`` reads
    the sibling Output-03 slot and builds Output 04. It is a slot name, not
    a mode: it does NOT by itself select the generated lane, deliberately,
    so that a caller naming the Pre slot without supplying its questions is
    REFUSED rather than quietly served the source lane.

    ``generated_questions`` selects the GENERATED lane (Output 04, spec
    T7): the items are authored questions — ``phase3.prequestions``'
    output, flattened by ``generated_question_records`` — rather than the
    chapter's own. That lane skips stages 1-3 entirely (source-atom
    freeze and per-atom cell verdicts) instead of widening them, because
    both of those hard-assume a source QID and a pseudo-atom carrying a
    fabricated one was explicitly rejected. Every stage after
    materialization is already source-agnostic and runs unchanged. The
    chapter's question/task inventory is read in this lane for ONE
    purpose only — as the set of identities the leak barrier accounts
    against — and never as a source of items.
    """
    authorities = dict(authorities or {})
    generate_lane = generated_questions is not None
    profile = assessment_profile.resolve(profile)
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    # WHICH SLOT this release routes against, and therefore which lane it
    # is. ``staged_lane`` is decided by the KEY the payload came out of —
    # see the barrier below for why that binding, and not any field, is
    # the one that holds.
    staged_release, staged_lane = (
        build_concepts_release.staged_release_for_lane(job, lane)
    )
    if staged_release is None:
        # OD4 numbering: Pre is Outputs 01/02, Post is Outputs 03/04.
        raise ReleaseRunError(
            "this job has no staged "
            + (
                "Output-01 Pre-Learning concept release; stage the Pre "
                "release before building Output 02"
                if build_concepts_release.normalize_lane(lane)
                == build_concepts_release.LANE_PRE
                else "Output-03 concept release; stage the concept release "
                "before building Output 04"
            )
        )
    try:
        bridge = release_snapshot.build(db, job, staged_release)
    except release_snapshot.SnapshotError as exc:
        raise ReleaseRunError(str(exc)) from exc
    inventory = bridge["question_task_inventory"]
    # The identity set the leak barrier accounts against. In the generated
    # lane this is the ONLY thing the chapter's inventory is used for:
    # the staged release carries it (build_concepts stores it on every
    # job, Pre included), and it is precisely the material that must not
    # reach Output 04.
    source_qids = source_inventory_qids(inventory)
    if generate_lane:
        # The Pre release payload holds none of this set, by design (see
        # the helper): it is read from the job column and the Post
        # sibling instead, so that Output 03 can obey the steer's "no QID
        # anywhere in the Pre release payload" without leaving this
        # barrier with nothing to account against.
        source_qids = _generated_lane_source_qids(job, source_qids)
    # THE BARRIER, AND WHAT IT IS BOUND TO.
    #
    # It is bound to the SLOT the staged payload was read out of, never to
    # ``learning_kind`` on the job and never to ``learning_kind`` inside the
    # payload.  That binding is the whole point, and the reason is
    # structural rather than stylistic: the Pre release is a SIBLING KEY on
    # a POST job (spec T2/T3 — every live creation site hardwires
    # ``learning_kind="post"``, and since slice A no live path mints
    # anything else).  So on a real run the job column says "post", and any
    # barrier keyed on it answers "post" for a payload that is
    # unambiguously the Pre one.  A caller that reads the sibling slot and
    # forgets ``generated_questions=`` would then take the SOURCE lane with
    # no barrier at all — which is exactly the leak this barrier exists to
    # close, reintroduced through the front door.
    #
    # ``staged_release_for_lane`` therefore resolves the lane from the key,
    # and unions in the payload's self-declared markers so a Pre payload
    # mis-staged into the POST slot is still caught.  ``payload_lane``
    # normalises those markers the way the rest of the tree does
    # (``build_concepts_release_publication`` and
    # ``build_concepts_release_files`` both ``.strip().lower()`` the same
    # key off the same payload), so the predicate here is never narrower
    # than the lane's own definition elsewhere.
    #
    # Pinned by tests/test_pre_release_lane_wiring.py, which stages a Pre
    # payload whose ``learning_kind`` reads "post" and requires the refusal
    # anyway — that test fails the moment anyone rebinds this to
    # ``learning_kind``.
    if staged_lane == build_concepts_release.LANE_PRE and not generate_lane:
        # The defect this barrier exists to close, refused at its root. A
        # staged PRE release carries the CURRENT chapter's question/task
        # inventory (build_concepts stores it on every job, and the Pre
        # release keeps it deliberately, as the identity set this lane's
        # leak accounting runs against), and the source lane is driven
        # entirely off that inventory — so pointing this pipeline at the
        # Pre payload unchanged materialises current-chapter SOURCE
        # questions into Output 04, which docs/aegis-restructure.md §4
        # Phase 03 and the owner steer of 17 Aug 2026 both forbid.
        raise SourceQuestionLeak(
            "this is a Pre-Learning job, whose assessment questions are "
            "GENERATED for the prerequisite: pass generated_questions. The "
            "chapter's own question/task inventory is never a source of "
            "Pre-Learning items (owner steer, 17 Aug 2026)"
        )
    if not generate_lane and not (inventory.get("items") or []):
        # Post-lane only (the Pre lane generates), so OD4 names Output 03.
        raise ReleaseRunError(
            "the staged Output-03 release has no question/task inventory"
        )
    chapter_id = int(bridge["snapshot"].get("target_chapter_id") or 0)
    if not chapter_id:
        # Both lanes reach this, so per T14 it names NO output number.
        raise ReleaseRunError(
            "the staged concept release has no target chapter identity"
        )
    if generate_lane:
        # PRE-SPEND source-integrity check, and deliberately here rather
        # than only at the barrier's last run.  ``bridge["snapshot"]``
        # becomes ``payload["concept_snapshot"]`` verbatim (see the
        # payload assembly below), so a source QID already present in the
        # STAGED concept release is a defect the final barrier is certain
        # to refuse — but it would refuse it only after every stage,
        # including the Master Refiner, had spent, and it would refuse
        # again on every retry, since the staged payload is immutable and
        # the model verdicts replay out of a content-addressed store.
        # That is a full-cost permanent refusal for a defect that is
        # fully knowable before the first call.  CLAUDE.md reserves
        # stopping a run for exactly this — "the pre-spend
        # source-integrity pauses" — so it is checked once, here, over
        # the same projection the final barrier uses.  It is also the
        # only channel by which the materialization author could ever SEE
        # a chapter QID (the snapshot rides its context), which is what
        # keeps an authored leak a provider fault rather than a
        # reachable one.
        #
        # BOTH inputs are checked, not one. The generated lane has exactly
        # two: the staged concept snapshot, and ``generated_questions`` —
        # a plain argument already in hand at this point, just as
        # knowable, and the one the authored artefact actually trips (a
        # chapter QID inside a staged generated question is refused by the
        # final barrier only after materialization and the critic have
        # spent, and then replays that refusal at zero spend on every
        # retry, because the decisions are content-addressed and the
        # staged payload is immutable). Checking the second alongside the
        # first is free and makes the pre-spend guard cover the lane's
        # whole input surface rather than half of it.
        from .phase3 import premap

        for surface, where in (
            (
                bridge["snapshot"],
                "the staged concept release this generated lane routes "
                "against",
            ),
            (
                {"generated_questions": list(generated_questions or [])},
                "the generated questions this lane was handed",
            ),
        ):
            premap._refuse_source_qids(
                _authored_surface(surface),
                list(source_qids),
                where=where,
            )

    envelope_sha, store, snapshot_directory = _decision_context(
        job.id,
        envelope_sha256=envelope_sha256,
        decision_store=decision_store,
    )
    if snapshot_directory is not None:
        # Each Master lane keeps its OWN durable audit snapshots. The two
        # lanes share one artifact directory and one filename family
        # (source.phase3-assessment-*.json), so unscoped writes meant the
        # second lane's snapshots silently overwrote the first's — a
        # recording loss even when the builds ran sequentially, and the
        # lanes now run concurrently (owner "Go", 2026-08-21).
        snapshot_directory = snapshot_directory / f"assessment-{staged_lane}"

    meta = dict(bridge["metadata"])
    source_release_sha = str(
        bridge["source_concept_release_sha256"]
    )
    concept_payload = list(bridge["concepts"])
    concept_records_by_key = dict(bridge["concept_records_by_key"])
    concept_keys = set(concept_records_by_key)
    fixer = _authority_pair(authorities, "fixer")[0]

    pre_blocked_generated: list[dict] = []
    duplicates_removed: list[dict] = []
    pre_learning_claimed: list[dict] = []
    if generate_lane:
        # Stages 1-3, generated lane — SKIPPED, not widened. No source atom
        # is built and none is classified: both of those stages hard-assume
        # a source QID, and the only ways to widen them are to accept an
        # atom without an identity or to fabricate one. One cell per
        # generated question; the questions themselves ride their cells.
        questions = list(generated_questions or [])
        progress.log(
            f"Assessment release: binding {len(questions)} generated "
            "question(s); no source question enters this lane."
        )
        # A question whose home row the snapshot dropped (skipped as
        # defective), or whose ``pre_concept_id`` two snapshot rows claim,
        # cannot route mechanically. Taking the whole Master down for it
        # would discard every other finished question — the same trade the
        # materialization seam refuses — so such a question BLOCKS ITSELF:
        # it is partitioned out here, before any cell verdict is paid for,
        # and ships as a recorded ``materialization_blocked`` marker with
        # the defect named (Ready-with-flags follows). The one distinct
        # payload-level case stays a loud refusal: a join map that is
        # entirely empty while questions exist means the staged payload
        # predates the ``pre_concept_id`` projection — that is not N
        # per-question defects but one stale artefact, and blocking every
        # question would ship an empty Master for a payload that a re-run
        # of generation re-stages correctly.
        join_map, ambiguous_pre_ids = cell_decisions.concept_keys_by_pre_id(
            concept_records_by_key
        )
        if questions and join_map:
            routable: list[Mapping] = []
            for question in questions:
                pre_cid = (
                    str(question.get("pre_concept_id") or "").strip()
                    if isinstance(question, Mapping)
                    else ""
                )
                if not pre_cid or pre_cid in join_map:
                    # Routable, or an identity defect the cell pass fails
                    # closed on (identity this pipeline minted is never
                    # blocked around).
                    routable.append(question)
                    continue
                if pre_cid in ambiguous_pre_ids:
                    claimants = ", ".join(ambiguous_pre_ids[pre_cid])
                    defect = (
                        f"pre-concept {pre_cid!r} is claimed by "
                        f"{len(ambiguous_pre_ids[pre_cid])} snapshot rows "
                        f"({claimants}); the routing is ambiguous and is "
                        "never resolved first-wins"
                    )
                else:
                    defect = (
                        f"pre-concept {pre_cid!r} is not carried by the "
                        "staged Pre release snapshot (row skipped as "
                        "defective upstream)"
                    )
                pre_blocked_generated.append(
                    _blocked_generated_candidate(question, defect)
                )
            questions = routable
        if pre_blocked_generated:
            progress.log(
                f"Assessment release: {len(pre_blocked_generated)} "
                "generated question(s) cannot route to a snapshot row and "
                "are BLOCKED from this Master, each recorded for review; "
                f"the remaining {len(questions)} question(s) continue.",
                level="warning",
            )
        if blueprint_cells is None and len(questions) > 1:
            # Q15 — one recorded duplicate verdict per pre-learning
            # concept group, run BEFORE any cell verdict is paid for.
            # Sameness is the model's judgment, never string similarity;
            # a removed question rides the payload under
            # ``duplicates_removed`` with its survivor and reason,
            # reviewable, never silent. Grouping is mechanical identity
            # (the ``pre_concept_id`` this pipeline minted); a question
            # with no id keeps its identity defect for the cell pass to
            # fail closed on, and a group of one costs nothing. Only the
            # decided-cells path runs it: an explicit ``blueprint_cells``
            # argument is the zero-spend path whose cells arrive 1:1
            # with the questions, and it stays exactly that.
            dedup_provider, dedup_critic = _authority_pair(
                authorities, "dedup"
            )
            grouped: dict[str, list[Mapping]] = {}
            for question in questions:
                pre_cid = (
                    str(question.get("pre_concept_id") or "").strip()
                    if isinstance(question, Mapping)
                    else ""
                )
                if pre_cid:
                    grouped.setdefault(pre_cid, []).append(question)
            removed_ids: set[str] = set()
            for pre_cid, group in grouped.items():
                if len(group) < 2:
                    continue
                concept = concept_records_by_key.get(
                    join_map.get(pre_cid, "")
                )
                _, removed_records = (
                    assessment_dedup.decide_generated_duplicates(
                        group,
                        concept_id=pre_cid,
                        concept_evidence=(
                            _concept_evidence(concept)
                            if isinstance(concept, Mapping)
                            else None
                        ),
                        meta=meta,
                        envelope_sha256=envelope_sha,
                        provider=dedup_provider,
                        critic=dedup_critic,
                        store=store,
                        fixer=fixer,
                    )
                )
                removed_ids.update(
                    str(record.get("pre_question_id") or "")
                    for record in removed_records
                )
                duplicates_removed.extend(removed_records)
            if duplicates_removed:
                for record in duplicates_removed:
                    progress.log(
                        "Master file: generated question "
                        f"{record.get('pre_question_id')!r} is REMOVED "
                        "as a duplicate of "
                        f"{record.get('survivor_pre_question_id')!r} "
                        "(Q15) and recorded on the release for review: "
                        + str(record.get("reason") or ""),
                        level="warning",
                    )
                questions = [
                    question for question in questions
                    if (
                        str(question.get("pre_question_id") or "")
                        if isinstance(question, Mapping)
                        else ""
                    ) not in removed_ids
                ]
                progress.log(
                    "Assessment release: "
                    f"{len(duplicates_removed)} duplicate generated "
                    f"question(s) removed by recorded verdict; "
                    f"{len(questions)} distinct question(s) continue.",
                    level="warning",
                )
        atoms = []
        if blueprint_cells is None and questions:
            # The supported live path: no external blueprint exists for
            # generated pre-learning questions — they are authored WITHOUT
            # a tier or difficulty by design ("its level is decided later
            # and independently", prequestions.py) — so each question
            # receives its own recorded cell verdict here, the generated
            # lane's parallel of the source lane's per-atom verdicts
            # below. An explicit ``blueprint_cells`` argument remains the
            # zero-spend path for callers that already hold the cells.
            progress.log(
                f"Deciding assessment cells for {len(questions)} generated "
                "question(s)."
            )
            cell_provider, cell_critic = _authority_pair(authorities, "cells")
            blueprint_cells = cell_decisions.decide_generated_cells(
                questions,
                meta=meta,
                profile=profile,
                envelope_sha256=envelope_sha,
                concept_records_by_key=concept_records_by_key,
                provider=cell_provider,
                critic=cell_critic,
                store=store,
                fixer=fixer,
            )
        cells = _bind_generated_cells(
            questions,
            blueprint_cells,
            profile=profile,
            concept_keys=concept_keys,
        )
    else:
        # Stage 1-2 — freeze the lossless source inventory.
        progress.log("Assessment release: freezing the source inventory.")
        built = source_inventory.build_source_atoms(
            inventory,
            source_document_hash=str(bridge["source_document_hash"]),
        )
        atoms = built["atoms"]

        if blueprint_cells is None and atoms:
            # Q18 (owner ruling b, 2026-08-21) — one recorded per-chapter
            # claim verdict BEFORE any cell verdict is paid for: source
            # questions that belong to prerequisite-recap material are
            # pre-learning's territory and leave this Post Master as a
            # recorded, reviewable disposition (``pre_learning_claimed``
            # on the payload; Ready-with-flags). Position is never
            # evidence (the Sorrieu rule); an empty claim is legitimate.
            # The 17-Aug steer stands: a claimed question never enters
            # any Pre artefact — Output 04's leak barrier still refuses
            # every source qid. The explicit ``blueprint_cells`` path
            # stays zero-spend with its 1:1 bind intact.
            claim_provider, claim_critic = _authority_pair(
                authorities, "pre_claim"
            )
            atoms, pre_learning_claimed = (
                prelearning_claim.decide_pre_learning_claims(
                    atoms,
                    meta=meta,
                    envelope_sha256=envelope_sha,
                    provider=claim_provider,
                    critic=claim_critic,
                    store=store,
                    fixer=fixer,
                )
            )
            if pre_learning_claimed:
                for record in pre_learning_claimed:
                    progress.log(
                        "Master file: source question "
                        f"{record.get('source_qid')!r} is claimed by "
                        "pre-learning (Q18) and leaves this Post Master, "
                        "recorded for review: "
                        + str(record.get("reason") or ""),
                        level="warning",
                    )
                progress.log(
                    f"Assessment release: {len(pre_learning_claimed)} "
                    "recap question(s) claimed by pre-learning; "
                    f"{len(atoms)} chapter-teaching question(s) continue.",
                    level="warning",
                )

        # Stage 3 — explicit cells are a mechanically validated zero-spend
        # path; otherwise each source atom receives one cached kernel verdict.
        if blueprint_cells is not None:
            cells = _bind_explicit_cells(
                atoms,
                blueprint_cells,
                profile=profile,
                concept_keys=concept_keys,
            )
        else:
            progress.log(
                f"Classifying {len(atoms)} source atom(s) into blueprint "
                "cells.")
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
    # One obligation per cell in both lanes; the generated lane simply has
    # no atom on its side of the pair, which ``materialize_candidates``
    # accepts by design (``atom=None`` throughout).
    if not generate_lane and len(atoms) != len(cells):
        # ``zip`` truncates, so this must be asserted BEFORE the pairing:
        # otherwise the source lane's obligation list silently absorbs the
        # mismatch and the cardinality gate below — which HEAD wrote as
        # ``len(candidates) != len(atoms)`` — stops detecting the
        # atoms-longer-than-cells direction entirely.  What that direction
        # loses is trailing SOURCE questions, dropped with no flag, which
        # is the R4 silent-loss shape.  Both Post cell producers already
        # guarantee one cell per atom, so this is defence in depth against
        # a bug in them, which is exactly why it must not be quietly
        # dissolved into ``zip``.
        raise ReleaseRunError(
            f"assessment cell classification produced {len(cells)} cell(s) "
            f"for {len(atoms)} source atom(s); every source question owns "
            "exactly one cell"
        )
    obligations: list[tuple[Mapping | None, Mapping]] = (
        [(None, cell) for cell in cells] if generate_lane
        else list(zip(atoms, cells))
    )
    _snapshot_cells(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        cells=cells,
    )

    # Stage 4 — materialize complete semantic candidates (zero-loss inside).
    progress.log(f"Materializing {len(obligations)} assessment candidate(s).")
    materialize_provider, materialize_critic = _authority_pair(
        authorities, "materialize"
    )
    materialized = materialization.materialize_candidates(
        obligations,
        meta=meta,
        profile=profile,
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
    if len(candidates) != len(obligations) or len(candidates) != len(cells):
        raise ReleaseRunError(
            "assessment materialization did not preserve atom/cell cardinality"
        )
    for candidate, (atom, cell) in zip(candidates, obligations):
        expected_atom_ids = [atom.get("source_qid")] if atom is not None else []
        if candidate.get("source_atom_ids") != expected_atom_ids or (
            str(candidate.get("blueprint_cell_id") or "")
            != str(cell.get("cell_id") or "")
        ):
            raise ReleaseRunError(
                "assessment materialization changed an obligation identity"
            )
        materialization_needs_review = _needs_review(candidate)
        candidate["route_evidence"] = (atom or {}).get("route_evidence") or {}
        candidate[_CELL_AUDIT_FIELD] = dict(
            cell.get(_CELL_AUDIT_FIELD) or {}
        )
        if generate_lane:
            # Positive markers, minted here and nowhere else. The barrier
            # below checks for their PRESENCE rather than for the absence
            # of a source id, because an absence is what every unfilled
            # field looks like.
            candidate["source_policy"] = GENERATED_SOURCE_POLICY
            candidate["origin"] = GENERATED_QUESTION_ORIGIN
            candidate["generated_question"] = dict(
                cell.get("generated_question") or {}
            )
        if cell.get("concept_key"):
            candidate["blueprint_concept_key"] = str(cell["concept_key"])
        if _needs_review(cell):
            _append_warning(candidate, _CELL_WARNING)
        if materialization_needs_review:
            _append_warning(candidate, _MATERIALIZATION_WARNING)
    # A BLOCKED obligation — materialization exhausted the bounded
    # corrections AND The Fixer — is excluded HERE, at the obligation
    # seam, because every later stage demands exact ordered coverage of
    # its input (mid-pipeline skipping is not expressible; marking hard
    # fails on empty answers). The exclusion is the loud, recorded kind
    # CLAUDE.md requires: each blocked question rides the payload under
    # ``materialization_blocked`` with every defect named, the log states
    # it at error level, and the finished Master ships with the questions
    # that DID arrive to contract — one impossible question never again
    # takes the whole Master file down.
    blocked_candidates = [
        copy.deepcopy(dict(candidate)) for candidate in candidates
        if str(candidate.get("assessment_eligibility") or "")
        == materialization.BLOCKED_ELIGIBILITY
    ]
    if pre_blocked_generated:
        # The pre-cell partition's blocked questions join the same ledger.
        # They were never in ``candidates``/``obligations``/``cells``, so
        # the index-aligned exclusion below does not involve them; they
        # ride ``materialization_blocked`` and the error-level log with
        # the markers minted here.
        blocked_candidates = blocked_candidates + [
            copy.deepcopy(dict(row)) for row in pre_blocked_generated
        ]
    if blocked_candidates:
        keep = [
            index for index, candidate in enumerate(candidates)
            if str(candidate.get("assessment_eligibility") or "")
            != materialization.BLOCKED_ELIGIBILITY
        ]
        candidates = [candidates[index] for index in keep]
        obligations = [obligations[index] for index in keep]
        cells = [cells[index] for index in keep]
        if not generate_lane:
            atoms = [atoms[index] for index in keep]
        for blocked in blocked_candidates:
            identity_label = (
                (blocked.get("source_atom_ids") or [""])[0]
                or str(
                    (blocked.get("generated_question") or {}).get(
                        "pre_question_id"
                    )
                    or ""
                )
                or str(blocked.get("candidate_id") or "")
            )
            progress.log(
                f"Master file: question {identity_label} could not be "
                "materialized to contract after bounded corrections and "
                "The Fixer; it is BLOCKED from this Master and recorded "
                "for review: "
                + "; ".join(
                    str(flag) for flag in (blocked.get("flags") or [])[:4]
                ),
                level="error",
            )
        progress.log(
            f"Master file continues with {len(candidates)} of "
            f"{len(candidates) + len(blocked_candidates)} question(s); "
            f"{len(blocked_candidates)} blocked question(s) are recorded "
            "on the release for review.",
            level="warning",
        )
    if generate_lane:
        # The barrier's first run, before a single later stage has spent
        # anything: a leak is cheapest to refuse here, and every remaining
        # pass is source-agnostic.
        _refuse_source_questions(
            {
                "candidates": candidates,
                "blueprint_cells": cells,
                "source_atoms": atoms,
            },
            source_qids=source_qids,
            where="the generated assessment materialization",
        )
    _snapshot_materializations(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        candidates=candidates,
    )

    # Owner ruling (2026-08-21, "Build it"): a picture-bank question — two
    # or more canonical image tags in its body, on a non-Objective sheet —
    # ships ONE stitched, labelled grid figure instead of a link per
    # picture. Objective items are exempt: each option must render its own
    # image. Mechanics keyed on the recorded sheet_kind and the tag count,
    # never on what any picture shows; a stitch that cannot complete (a
    # download fails, no public base URL) leaves the text untouched and
    # rides the candidate as a named review flag — never a blocked Master.
    # It runs HERE, before the learner-text freeze, so every later stage
    # and both invariance guards see the final single-figure body.
    image_grid_cache: dict = {}
    stitched_count = 0
    for candidate in candidates:
        if str(candidate.get("sheet_kind") or "") == "objective":
            continue
        if (
            str(candidate.get("assessment_eligibility") or "")
            == materialization.BLOCKED_ELIGIBILITY
        ):
            continue
        grid_audits: list[dict] = []
        for field in ("question", "question_text"):
            new_text, grid_record = (
                question_image_grid.consolidate_images(
                    str(candidate.get(field) or ""),
                    job_id=job.id,
                    downloader=image_downloader,
                    cache=image_grid_cache,
                )
            )
            if grid_record is None:
                continue
            grid_audits.append({"field": field, **grid_record})
            if "error" in grid_record:
                _append_warning(candidate, _IMAGE_GRID_WARNING)
            else:
                candidate[field] = new_text
        if grid_audits:
            candidate[_IMAGE_GRID_AUDIT_FIELD] = grid_audits
            if any("error" not in audit for audit in grid_audits):
                stitched_count += 1
    if stitched_count:
        progress.log(
            f"Assessment release: {stitched_count} picture-bank "
            "question(s) now carry one stitched labelled figure each "
            "(Objective options keep their own images)."
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
        profile=profile,
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

    # Stage 7 — route only across the immutable staged concept-release
    # concepts (this run's own lane; OD4 numbers them 01 or 03).
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
        # S9: the staged rows the snapshot could not carry. Written here so
        # that ``create_release`` — which sees only this payload — can
        # append them to ``frozen["errors"]`` and refuse the assessment
        # lane's database write while every download stays open (Rule E).
        "concept_snapshot_defects": list(bridge.get("defects") or []),
        "source_atoms": atoms,
        "blueprint_cells": cells,
        "candidates": candidates,
        "groups": groups,
        "placements": placements,
        # R4: obligations whose materialization exhausted the bounded
        # corrections AND The Fixer. They shipped as no row — shipping a
        # row that failed its mechanical contract is the broken artifact
        # the checker refuses — but they are never silent: every one is
        # named here with its defects, for review and re-run.
        "materialization_blocked": blocked_candidates,
        # Q15: generated questions the duplicate verdict removed, each
        # with its survivor and reason. A recorded exclusion, not loss —
        # the survivor asks the same question — and it names the release
        # Ready-with-flags so every removal is reviewable.
        "duplicates_removed": duplicates_removed,
        # Q18: source questions the pre-learning claim removed from this
        # Post Master — prerequisite-recap material, each with its reason.
        # A recorded disposition, not loss, and reviewable the same way.
        "pre_learning_claimed": pre_learning_claimed,
    }
    if staged_lane == build_concepts_release.LANE_PRE:
        # Which lane this Master belongs to, stated rather than inferred —
        # and stated only by the lane that needs to state it, so the
        # Output-02 payload keeps the exact shape it has on record.
        # ``concept_snapshot`` already carries the lane per topic; this
        # says it once at the top so the SECOND snapshot writer
        # (``release_service.snapshot_from_chapter``, which reads live
        # Topic rows holding BOTH lanes) can scope itself if a payload
        # ever reaches it without a staged snapshot.
        payload["pre_post_learning"] = "Pre"

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
    if generate_lane:
        # The barrier's second and last run, over the complete release
        # payload immediately before it is frozen — so nothing added
        # between materialization and here (labels, groups, placements,
        # refined prose) can carry a source question in. Redaction of the
        # audit channels runs FIRST and never raises; the refusal then
        # runs over the authored artefact, in that order, so an advisory
        # critic can flag freely and can never brick the run.
        #
        # Deliberate, not incidental: the phase-3 SNAPSHOTS above are
        # written from the PRE-redaction payload, so a critic's dissent
        # keeps the id it named on disk. That is the same audit fidelity
        # the decision store itself holds — an auditor's account of the
        # artefact, not the artefact — and no download route ships it
        # (``build_concepts_release_files.build_diagnostics_zip`` writes
        # the run report, coverage ledger, release payload and blocks,
        # never this directory). The property the steer asks for is about
        # the Pre RELEASE, and that is what is redacted and then refused.
        payload = _redact_audit_source_qids(payload, source_qids)
        _refuse_source_questions(
            payload,
            source_qids=source_qids,
            where="the generated assessment release payload",
        )
    if supersedes is None:
        # THE FREEZE SEAM (spec-step8 T2/S6). One immutable row per lane
        # per run: a second run of the same lane on the same job is a new
        # VERSION that supersedes the first, not an unrelated release that
        # orphans it. Resolved from the job and the lane, so the reviewer's
        # explicit re-build route — the idempotent second act — lands on
        # the same chain the in-run build started.
        #
        # Only when the caller named none: a caller that passes
        # ``supersedes`` has already decided the lineage, and a lane-less
        # legacy release has no chain to join.
        #
        # ``release_chain_head``, not ``latest_release_for_lane``: this is
        # the lineage question, and it must include a superseded row. A
        # chain whose every row is superseded is still that lane's chain,
        # and appending to it keeps the published receipts in one lineage
        # where minting a fresh ``release_uid`` at version 1 would orphan
        # them. The manifest asks the other question, and gets the other
        # answer.
        supersedes = release_core.release_chain_head(
            db, job.id, staged_lane)
    release = release_service.create_release(
        db,
        chapter_id=chapter_id,
        payload=payload,
        job_id=job.id,
        owner_sub=owner_sub,
        supersedes=supersedes,
        lane=staged_lane,
        layout_id=release_core.layout_id(),
        provider_identity=release_core.run_context(
            job,
            lane=staged_lane,
            profile=profile,
            layout_id=release_core.layout_id(),
        ),
    )
    release = release_service.publish_release(db, release)
    progress.log(
        f"Assessment release {release.release_uid} v{release.version} "
        f"published: readiness "
        f"{(release.diagnostics or {}).get('readiness')!r}.",
        level="success",
    )
    return release
