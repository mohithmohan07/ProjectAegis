"""Exact assessment blueprint compilation (MES spec §5.2, Stage 3).

The blueprint is a hard contract: every released question fulfills one
explicit cell, and generation produces exactly the blueprint obligations —
never a Cartesian product resolved implicitly inside a generation loop.

This module is deterministic mechanics the spec assigns to local code:
compiling explicit cells, exact counts and totals, per-kind/category/Bloom/
difficulty distributions, and obligation expansion. Whether a QUESTION
fulfills its cell is a model judgment and lives in the authoring/critic
stages — never here.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable, Mapping

from . import assessment_profile
from . import assessment_release as rel

# Legacy Build-Assessments sheet kinds. A strict assessment profile (e.g.
# the reference school's) may forbid Subjective rows — that restriction
# binds its blueprint cells, not legacy concept-mapping sessions, which may
# still author all three.
LEGACY_SHEET_KINDS = ("objective", "subjective", "descriptive")


class BlueprintError(ValueError):
    """The blueprint cannot be compiled into exact obligations."""


def _cell_id(seed: str) -> str:
    return "CELL-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def compile_cells_from_batches(
    batches: Iterable,
    *,
    concepts: Iterable | None = None,
    default_marks: Mapping[str, float] | None = None,
    strict_profile: bool = False,
) -> list[dict]:
    """Compile stacked legacy BlueprintBatch rows into explicit cells.

    Each (concept x skill x difficulty x category) combination becomes ONE
    first-class cell with a stable ``cell_id``, an exact ``count``, and its
    concept constraint — so the complete obligation list exists before any
    generation happens, is validated as a whole, and every created question
    is stamped with the cell it fulfills. The old behavior resolved this
    product implicitly inside the generation loop; nothing was compiled,
    validated, or stamped.
    """
    marks_by_kind = dict(default_marks or {})
    cells: list[dict] = []
    concept_list = list(concepts) if concepts is not None else [None]
    if not concept_list:
        concept_list = [None]
    for batch_index, batch in enumerate(batches, start=1):
        kind = str(getattr(batch, "question_type", "") or "")
        for concept in concept_list:
            concept_id = getattr(concept, "id", None)
            for skill in batch.cognitive_skills or []:
                for difficulty in batch.difficulty_levels or []:
                    for category in batch.categories or []:
                        seed = "|".join(str(part) for part in (
                            batch_index, concept_id, kind, skill,
                            difficulty, category,
                        ))
                        cells.append({
                            "cell_id": _cell_id(seed),
                            "sheet_kind": kind,
                            "question_category": category,
                            "cognitive_skill": skill,
                            "difficulty": difficulty,
                            # Marks are semantic. A caller either supplies a
                            # recorded value or completes this cell through a
                            # model decision before validation; the compiler
                            # never invents one from sheet kind.
                            "marks": marks_by_kind.get(kind),
                            "count": max(
                                int(getattr(batch, "num_questions", 1) or 1),
                                1),
                            "appears_in": list(
                                getattr(batch, "appears_in", None) or []),
                            "concept_id": concept_id,
                            "source_policy": "generate",
                            "strict_profile": bool(strict_profile),
                        })
    return cells


def validate_cells(
    cells: list[dict], *, strict_profile: bool = False,
    profile: Mapping | str | None = None,
) -> None:
    """Stage-3 validation before any generation spend (spec §6 Stage 3).

    Exact counts, totals, and structural rules. Raises with every defect
    named; a blueprint that cannot be validated never reaches a model.

    ``profile`` is the RUN profile (spec-step8 B2): without it a strict
    validation resolved ``DEFAULT_PROFILE`` and rejected a widened
    profile's cells before staging ever saw them.
    """
    errors: list[str] = []
    seen_ids: set[str] = set()
    allowed_kinds = (
        assessment_profile.sheet_kinds(profile) if strict_profile
        else LEGACY_SHEET_KINDS
    )
    for cell in cells:
        cell_id = str(cell.get("cell_id") or "")
        if not cell_id:
            errors.append("cell without cell_id")
        elif cell_id in seen_ids:
            errors.append(f"duplicate cell_id {cell_id}")
        seen_ids.add(cell_id)
        if cell.get("sheet_kind") not in allowed_kinds:
            errors.append(
                f"{cell_id}: sheet_kind must be one of {allowed_kinds} "
                f"(got {cell.get('sheet_kind')!r})")
        try:
            if int(cell.get("count") or 0) < 1:
                errors.append(f"{cell_id}: count must be >= 1")
        except (TypeError, ValueError):
            errors.append(f"{cell_id}: count not an integer")
        try:
            marks = float(cell.get("marks"))
            if not math.isfinite(marks) or marks <= 0:
                errors.append(
                    f"{cell_id}: marks must be finite and positive")
        except (TypeError, ValueError):
            errors.append(f"{cell_id}: marks not numeric")
    if errors:
        raise BlueprintError(
            "blueprint failed exact validation: " + "; ".join(errors[:20]))


def totals(cells: list[dict]) -> dict:
    """Exact obligation totals and distributions for Stage-3 review."""
    by_kind: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_skill: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    total_questions = 0
    total_marks = 0.0
    for cell in cells:
        count = int(cell.get("count") or 0)
        total_questions += count
        total_marks += float(cell.get("marks") or 0) * count
        for bucket, key in (
            (by_kind, "sheet_kind"),
            (by_category, "question_category"),
            (by_skill, "cognitive_skill"),
            (by_difficulty, "difficulty"),
        ):
            value = str(cell.get(key) or "")
            bucket[value] = bucket.get(value, 0) + count
    return {
        "cells": len(cells),
        "total_questions": total_questions,
        "total_marks": total_marks,
        "by_sheet_kind": by_kind,
        "by_category": by_category,
        "by_cognitive_skill": by_skill,
        "by_difficulty": by_difficulty,
    }


def obligations(cells: list[dict]) -> list[str]:
    """One obligation identity per required question instance.

    These identities feed the zero-loss invariant: obligations = accepted
    rows + explicitly flagged rows, and no blueprint cell may disappear.
    """
    out: list[str] = []
    for cell in cells:
        for instance in range(1, int(cell.get("count") or 0) + 1):
            out.append(f"{cell.get('cell_id')}#{instance}")
    return out


def blueprint_sha256(cells: list[dict]) -> str:
    return rel.sha256_json(cells)
