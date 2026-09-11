"""Step 03 — the team's reviewed Master file, applied verbatim and published.

The owner's three-step workflow ends with "the team then reviews, edits,
make changes for master files and then upload the files to cms and also to
the data base". Step 2 renders each lane's Master as an immutable
``AssessmentRelease`` version; this module owns the two explicit acts that
follow it:

* ``submit_reviewed_master`` — the reviewer's edited Master workbook (the
  same layout they downloaded) is parsed, every cell the reviewer changed
  in the Question band is reversed through the exact projection
  ``assessment_workbook._question_record`` / ``_row_values`` wrote it with,
  and the result is frozen as a NEW release version that supersedes the
  Step 2 one (contract v2.0 §38 stage 12, §44). A reviewer's edit is a human
  decision applied verbatim (CLAUDE.md Rule 1): nothing here calls a model,
  and nothing here judges whether the new wording is good. The mechanical
  gates (schema, marking arithmetic, KaTeX/asset validation, the closed
  Q45 vocabulary) still run on the new version through
  ``assessment_release_service.publish_release`` and may make its
  readiness BLOCKED — the upload is never refused for that; the publish
  act below is what refuses, naming the issues.

* ``publish_reviewed_master`` — the lane's live Master version (the
  reviewed one when accepted, else Step 2's) goes to the database through
  the one existing path, ``upload_master_to_database``, and its newly
  published questions are appended to the shared CMS output workbook
  through the same transactional-outbox shape the Concept publication uses
  (§40: one idempotent transaction).

Additions are refused here by design. A reviewer-authored row carries no
source provenance (``source_atom_ids``, blueprint cell, restriction
rationale) and the Post Master is source-only (Q39/Q41); the documented
path for a new question is Step 02's Concept file, whose independent API
author and Fixer extract it (Q49). The refusal names that path.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import assessment_workbook, workbook_sync, writer
from ..bulk_import.presentation import is_equation_field
from . import assessment_profile
from . import assessment_release as rel
from . import assessment_release_service as release_service
from . import build_concepts_release as concept_release
from . import column_spec, generation_recovery, release_core, uploads

_LOGGER = logging.getLogger(__name__)

# The versioned policy every reviewed Master round records (Rule 0: new
# policies are versioned; historical versions keep their recorded contract).
POLICY_VERSION = "master-review-2026-09-11-v1"
LANE_STATUS_REVIEWED = "reviewed"
LANE_STATUS_PUBLISHED = "published"

_ANSWER_BLOCK_RE = re.compile(r"^answer_type_(\d+)$")
_SUBQUESTION_RE = re.compile(r"^sub_question_(\d+)$")
_STEM_SENTINEL = "AEGIS-QUESTION-STEM"

# Question-band scalar cells and the candidate field each one projects.
_SCALAR_FIELDS: dict[str, str] = {
    "question_category": "question_category",
    "cognitive_skills": "cognitive_skill",
    "question_source": "question_source",
    "question_disclaimer": "question_disclaimer",
    "question_duration": "question_duration",
    "question_appears_in": "question_appears_in",
    "answer_restriction": "answer_restriction",
    "level_of_difficulty": "difficulty",
    "marks": "marks",
    "math_keyboard": "math_keyboard",
    "display_answer": "display_answer",
    "answer_explanation": "answer_explanation",
}
_RICH_TEXT_SCALARS = frozenset({
    "question_disclaimer", "display_answer", "answer_explanation",
})
_NUMERIC_SCALARS = frozenset({"question_duration", "marks"})
_GROUP_IDENTITY_FIELDS = (
    "group_name", "group_display_name", "group_status", "group_type",
    "group_question_labels", "related_digicards",
)
_CORRECT_MARKERS = {"1": "1", "true": "1", "yes": "1", "0": "0", "false": "0", "no": "0"}
# Cells the renderer composes from the rows themselves (label aggregates and
# tier roll-ups). They follow every omission mechanically and are never a
# reviewer's edit, so a stale copy of one is not worth a flag.
_DERIVED_AGGREGATE_FIELDS = frozenset({
    "topic_concept_labels", "concept_question_labels", "group_question_labels",
    "basic_groups", "intermediate_groups", "advanced_groups",
})


class MasterReviewError(Exception):
    """Base class; the API maps subclasses to HTTP statuses."""


class MasterReviewNotFound(MasterReviewError):
    """The job has no live published Master release for the lane (404)."""


class MasterReviewConflict(MasterReviewError):
    """The job is not at the Step 03 boundary, or an act is refused (409)."""


class MasterReviewRefused(MasterReviewError):
    """The uploaded file cannot be applied mechanically (422)."""


# --------------------------------------------------------------------------- #
# Shared mechanics
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _numeric(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return number if number.is_finite() else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _cells_equal(before: Any, after: Any) -> bool:
    """Exact cell equality; numbers compare by value, never by spelling.

    Excel stores a whole number typed into a text-bearing cell as a number,
    so ``"1"`` and ``1`` are one value. Everything else is byte equality.
    """
    left, right = _numeric(before), _numeric(after)
    if left is not None and right is not None:
        return left == right
    return _text(before) == _text(after)


def _populated(value: Any) -> bool:
    return bool(_text(value).strip())


def _numeric_value(cell: Any) -> Any:
    """A numeric cell's stored value, or the reviewer's text verbatim.

    A value that does not parse stays exactly as typed so the freeze gate
    names it ("marks must be finite and positive") instead of a guessed
    number replacing it.
    """
    if isinstance(cell, bool):
        return cell
    if isinstance(cell, (int, float)):
        return cell
    text = _text(cell)
    converted = assessment_workbook._numeric_cell(text)
    return converted


def _rich_value(cell: Any, *, raw_equation: bool = False) -> str:
    """The reader's exact inverse of the ``<br>`` projection (contract §17)."""
    return bi.from_workbook_rich_text(_text(cell), raw_equation=raw_equation)


def _type_value(cell: Any) -> str:
    return bi.normalize_answer_type(_text(cell).strip())


def _types_equal(before: Any, after: Any) -> bool:
    return _type_value(before) == _type_value(after)


def _correct_value(cell: Any, flags: list[str], *, label: str, field: str) -> str:
    text = _text(cell).strip()
    if not text:
        return ""
    marker = _CORRECT_MARKERS.get(text.lower())
    if marker is None:
        flags.append(
            f"{label}: {field} value {text!r} is not a recognised Yes/No "
            "marker; it was kept verbatim and reads as a wrong option"
        )
        return text
    return marker


def _correct_equal(before: Any, after: Any) -> bool:
    if _populated(before) != _populated(after):
        return False
    return rel.is_correct_option(before) == rel.is_correct_option(after)


def _release_issues(release: models.AssessmentRelease) -> list[str]:
    diagnostics = release.diagnostics or {}
    read_back = diagnostics.get("read_back") or {}
    issues: list[str] = []
    for item in (
        list(diagnostics.get("payload_errors") or [])
        + list(read_back.get("master_errors") or [])
        + list(read_back.get("concepts_errors") or [])
    ):
        text = str(item)
        if text not in issues:
            issues.append(text)
    return issues


def _release_identity(release: models.AssessmentRelease) -> dict[str, Any]:
    return {
        "release_id": int(release.id),
        "release_uid": str(release.release_uid),
        "version": int(release.version),
    }


def _profile(release: models.AssessmentRelease) -> dict:
    return assessment_profile.resolve(
        release_service._run_profile(release.provider_identity)
    )


def _review_gate(job: models.UploadJob) -> dict[str, Any]:
    state = concept_release.concept_review_state(job)
    if not state:
        raise MasterReviewConflict(
            "this upload has no Concept review gate; it predates the "
            "three-step workflow and publishes through its existing release "
            "routes"
        )
    return state


def _require_step_three(
    job: models.UploadJob, state: Mapping[str, Any], *, operation: str,
) -> None:
    status = str(state.get("status") or "")
    if status not in {
        concept_release.CONCEPT_REVIEW_MASTER_READY,
        concept_release.CONCEPT_REVIEW_PUBLISHED,
    }:
        raise MasterReviewConflict(
            f"the Master files are not ready for review (Concept review "
            f"status is {status!r}); build the Master files from the "
            f"reviewed Concept files before you {operation}"
        )
    if uploads.is_job_running(job.id):
        raise MasterReviewConflict(
            "generation is running for this upload; wait for the active "
            f"run to finish before you {operation}"
        )
    generation_recovery.require_mutation_allowed(job, operation=operation)


def _live_release(
    db: Session, job: models.UploadJob, lane: str,
) -> models.AssessmentRelease:
    release = release_core.latest_release_for_lane(db, job.id, lane)
    if release is None:
        raise MasterReviewNotFound(
            f"this upload has no live {lane} Master release; build the "
            "Master files first"
        )
    if release.owner_sub != job.owner_sub:
        raise MasterReviewNotFound("release not found")
    return release


def _published_master_bytes(
    release: models.AssessmentRelease, profile: Mapping,
) -> bytes:
    """The exact workbook the reviewer downloaded, hash-verified.

    Falls back to re-rendering the frozen snapshot when the on-disk file is
    missing or drifted: the projection is deterministic, so the expected
    cells are the same either way.
    """
    directory = Path((release.publication or {}).get("directory") or "")
    path = directory / release_service.MASTER_FILENAME
    expected = str((release.workbook_hashes or {}).get("master_xlsx") or "")
    if directory and path.is_file() and not path.is_symlink():
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() == expected:
            return data
    output = assessment_workbook.build_dual_output(
        release.concept_snapshot, profile,
    )
    return output["master_xlsx"]


def _band_fields(fields: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Hierarchy / Group / Question band field lists of one sheet."""
    question_start = fields.index("question_label")
    group_start = min(
        fields.index(name)
        for name in ("group_name", "group_display_name")
        if name in fields
    )
    return (
        fields[:group_start],
        fields[group_start:question_start],
        fields[question_start:],
    )


def _has_question_band(row: Mapping[str, Any], question_fields: list[str]) -> bool:
    return any(
        _populated(row.get(field))
        for field in question_fields
        if field not in assessment_workbook._UPDATE_FIELD_PRESENCE
    )


def _index_rows(parsed: Mapping[str, Any]) -> dict[str, tuple[str, dict, int]]:
    """``question_label -> (sheet, row, row_number)`` for question rows."""
    index: dict[str, tuple[str, dict, int]] = {}
    for sheet in assessment_workbook.SHEET_ORDER:
        data = (parsed.get("sheets") or {}).get(sheet) or {}
        fields = list(data.get("fields") or [])
        if "question_label" not in fields:
            continue
        _hierarchy, _group, question_fields = _band_fields(fields)
        rows = data.get("rows") or []
        numbers = data.get("row_numbers") or list(range(3, 3 + len(rows)))
        for row, number in zip(rows, numbers):
            if not _has_question_band(row, question_fields):
                continue
            label = _text(row.get("question_label")).strip()
            if label and label not in index:
                index[label] = (sheet, row, int(number))
    return index


# --------------------------------------------------------------------------- #
# The reverse projection (one candidate row)
# --------------------------------------------------------------------------- #

class _Lineage:
    """The superseded versions' rendered cells, read lazily and once.

    A derived cell (``question_text``) that a reviewer never touched stays
    at an EARLIER version's composition when the file they re-upload was
    edited before that version existed. Recognising it as stale is set
    membership against the lineage's own renderings — mechanics, never a
    guess about which wording the reviewer meant.
    """

    def __init__(self, db: Session, release: models.AssessmentRelease, profile: Mapping) -> None:
        self._db = db
        self._release = release
        self._profile = profile
        self._index: dict[str, list[Any]] | None = None

    def question_text_values(self, label: str) -> list[Any]:
        if self._index is None:
            index: dict[str, list[Any]] = {}
            rows = (
                self._db.query(models.AssessmentRelease)
                .filter(
                    models.AssessmentRelease.release_uid == self._release.release_uid,
                    models.AssessmentRelease.version < self._release.version,
                )
                .order_by(models.AssessmentRelease.version.desc())
                .all()
            )
            for row in rows:
                try:
                    parsed = assessment_workbook.parse_workbook(
                        _published_master_bytes(row, self._profile)
                    )
                except Exception:  # noqa: BLE001 — a lost artefact is no cell
                    continue
                for known, (_sheet, cells, _number) in _index_rows(parsed).items():
                    index.setdefault(known, []).append(cells.get("question_text"))
            self._index = index
        return list(self._index.get(label, []))


class _RowContext:
    """Everything the reverse mapping of one row needs, resolved once."""

    def __init__(
        self, *, profile: Mapping, schema: Mapping[str, Any],
        source_book: str,
    ) -> None:
        self.profile = profile
        self.schema = schema
        self.source_book = source_book
        self.slots = int(schema["descriptive_answer_slots"])
        self.column_policy = column_spec.from_profile(
            assessment_profile.resolve(profile)
        )

    def question_text_parts(self, candidate: Mapping, sheet: str) -> tuple[str, str] | None:
        """``(prefix, suffix)`` the renderer composes around the stem."""
        probe = copy.deepcopy(dict(candidate))
        probe["question"] = _STEM_SENTINEL
        probe["question_text"] = _STEM_SENTINEL
        record = assessment_workbook._question_record(
            probe, sheet, self.profile,
            descriptive_answer_slots=self.slots,
            source_book=self.source_book,
        )
        text = str(record.get("question_text") or "")
        index = text.find(_STEM_SENTINEL)
        if index < 0:
            return None
        return text[:index], text[index + len(_STEM_SENTINEL):]


def _block_numbers(fields: list[str], pattern: re.Pattern) -> list[int]:
    return sorted(
        int(match.group(1))
        for match in (pattern.match(field) for field in fields)
        if match
    )


def _answer_cells(sheet: str, number: int) -> dict[str, str]:
    """Candidate answer field -> workbook cell name for one answer block."""
    if sheet == "Objective":
        return {
            "answer_type": f"answer_type_{number}",
            "answer_content": f"answer_content_{number}",
            "correct_answer": f"correct_answer_{number}",
            "answer_weightage": f"answer_weightage_{number}",
        }
    if sheet == "Subjective":
        return {
            "answer_type": f"answer_type_{number}",
            "answer_content": f"answer_{number}",
            "answer_weightage": f"weightage_{number}",
            "placeholder": f"placeholder_{number}",
        }
    return {
        "answer_type": f"answer_type_{number}",
        "answer_weightage": f"answer_weightage_{number}",
        "answer_content": f"answer_content_{number}",
    }


def _block_populated(row: Mapping[str, Any], cells: Mapping[str, str]) -> bool:
    return _populated(row.get(cells["answer_type"])) or _populated(
        row.get(cells["answer_content"])
    )


def _reverse_answers(
    sheet: str, candidate: Mapping, edited: dict, expected: Mapping[str, Any],
    row: Mapping[str, Any], fields: list[str], *, label: str,
    edits: list[dict], flags: list[str],
) -> None:
    """Rebuild ``answers`` from the answer blocks, block by block.

    Block ``n`` is ``answers[n-1]`` in the renderer, so an existing block
    keeps its dict and takes only the cells that changed; a newly filled
    block becomes a new answer; a blanked block removes its answer. Every
    changed cell is recorded verbatim.
    """
    numbers = _block_numbers(fields, _ANSWER_BLOCK_RE)
    original = [
        dict(answer) for answer in (candidate.get("answers") or [])
        if isinstance(answer, Mapping)
    ]
    rebuilt: list[dict] = []
    changed_any = False
    for number in numbers:
        cells = _answer_cells(sheet, number)
        was = _block_populated(expected, cells)
        now = _block_populated(row, cells)
        changed_cells = [
            (field, cell) for field, cell in cells.items()
            if not (
                _types_equal(expected.get(cell), row.get(cell))
                if field == "answer_type"
                else _correct_equal(expected.get(cell), row.get(cell))
                if field == "correct_answer"
                else _cells_equal(expected.get(cell), row.get(cell))
            )
        ]
        for _field, cell in changed_cells:
            edits.append({
                "question_label": label,
                "field": cell,
                "before": _json_safe(expected.get(cell)),
                "after": _json_safe(row.get(cell)),
            })
        if changed_cells:
            changed_any = True
        if not now:
            continue
        base = (
            copy.deepcopy(original[number - 1])
            if was and number - 1 < len(original)
            else {}
        )
        apply_fields = (
            list(cells) if not base else [field for field, _cell in changed_cells]
        )
        for field in apply_fields:
            cell = cells[field]
            value = row.get(cell)
            if field == "answer_type":
                base[field] = _type_value(value)
            elif field == "answer_content":
                base[field] = _rich_value(
                    value, raw_equation=is_equation_field(cell, row),
                )
            elif field == "correct_answer":
                base[field] = _correct_value(value, flags, label=label, field=cell)
            elif field == "answer_weightage":
                base[field] = _numeric_value(value)
            elif field == "placeholder":
                base[field] = _text(value).strip()
        if sheet == "Subjective" and not _text(base.get("answer_display")).strip():
            # The workbook's ``answer_display_N`` is the CMS literal ``Yes``
            # (contract §23), so a new block carries no display text of its
            # own; the accepted answer is shown, and the choice is recorded.
            base["answer_display"] = str(base.get("answer_content") or "")
            flags.append(
                f"{label}: answer block {number} is new; its answer_display "
                "was set to its accepted answer text"
            )
        rebuilt.append(base)
    if changed_any:
        edited["answers"] = rebuilt


def _reverse_sub_questions(
    candidate: Mapping, edited: dict, expected: Mapping[str, Any],
    row: Mapping[str, Any], fields: list[str], *, label: str,
    edits: list[dict], flags: list[str],
) -> None:
    numbers = _block_numbers(fields, _SUBQUESTION_RE)
    if not numbers:
        return
    original = [
        dict(sub) for sub in (candidate.get("sub_questions") or [])
        if isinstance(sub, Mapping)
    ]
    rebuilt: list[dict] = []
    changed_any = False
    for number in numbers:
        text_cell = f"sub_question_{number}"
        marks_cell = f"sub_question_marks_{number}"
        keyword_numbers = sorted(
            int(match.group(1))
            for match in (
                re.match(rf"^sq{number}_answer_type_(\d+)$", field)
                for field in fields
            )
            if match
        )
        block_cells = [text_cell, marks_cell] + [
            f"sq{number}_{name}_{m}"
            for m in keyword_numbers
            for name in ("answer_type", "weightage", "keyword")
        ]
        changed_cells = [
            cell for cell in block_cells
            if not (
                _types_equal(expected.get(cell), row.get(cell))
                if "_answer_type_" in cell
                else _cells_equal(expected.get(cell), row.get(cell))
            )
        ]
        for cell in changed_cells:
            edits.append({
                "question_label": label,
                "field": cell,
                "before": _json_safe(expected.get(cell)),
                "after": _json_safe(row.get(cell)),
            })
        if changed_cells:
            changed_any = True
        was = _populated(expected.get(text_cell))
        now = _populated(row.get(text_cell))
        if not now:
            continue
        base = (
            copy.deepcopy(original[number - 1])
            if was and number - 1 < len(original)
            else {"text": "", "marks": "", "keywords": []}
        )
        if text_cell in changed_cells or not was:
            base["text"] = _rich_value(row.get(text_cell))
        if marks_cell in changed_cells or not was:
            base["marks"] = _numeric_value(row.get(marks_cell))
        keyword_changed = any(cell.startswith(f"sq{number}_") for cell in changed_cells)
        if keyword_changed or not was:
            original_keywords = [
                dict(keyword) for keyword in (base.get("keywords") or [])
                if isinstance(keyword, Mapping)
            ]
            keywords: list[dict] = []
            for m in keyword_numbers:
                type_cell = f"sq{number}_answer_type_{m}"
                weight_cell = f"sq{number}_weightage_{m}"
                keyword_cell = f"sq{number}_keyword_{m}"
                keyword_was = _populated(expected.get(type_cell)) or _populated(
                    expected.get(keyword_cell)
                )
                keyword_now = _populated(row.get(type_cell)) or _populated(
                    row.get(keyword_cell)
                )
                if not keyword_now:
                    continue
                keyword = (
                    copy.deepcopy(original_keywords[m - 1])
                    if keyword_was and m - 1 < len(original_keywords)
                    else {}
                )
                if type_cell in changed_cells or not keyword:
                    keyword["answer_type"] = _type_value(row.get(type_cell))
                if weight_cell in changed_cells or "weightage" not in keyword:
                    keyword["weightage"] = _numeric_value(row.get(weight_cell))
                if keyword_cell in changed_cells or "keyword" not in keyword:
                    keyword["keyword"] = _rich_value(
                        row.get(keyword_cell),
                        raw_equation=is_equation_field(keyword_cell, row),
                    )
                keywords.append(keyword)
            base["keywords"] = keywords
        rebuilt.append(base)
    if changed_any:
        edited["sub_questions"] = rebuilt


def _reverse_question_text(
    context: _RowContext, sheet: str, edited: dict, expected: Mapping[str, Any],
    row: Mapping[str, Any], *, label: str, question_changed: bool,
    edits: list[dict], flags: list[str], lineage_values: list[Any] = (),
) -> None:
    """Reduce an edited ``question_text`` cell back to the stem it carries.

    The cell is a composition: the stem plus the lettered options
    (Objective), the stem with each ``$$a$$`` shown as a blank (Subjective),
    or the stem plus every labelled child (Descriptive). A change that is
    exactly the re-composition of the edited stem/options is not a second
    edit; any other change is reduced by stripping the exact composed
    prefix/suffix (or re-inserting the placeholders), and applied to the
    stem. When the reduction is not mechanical the cell is applied
    verbatim to ``question_text`` and the contract gate
    ("question must equal question_text") names the disagreement.
    """
    cell = "question_text"
    if _cells_equal(expected.get(cell), row.get(cell)):
        return
    if any(_cells_equal(value, row.get(cell)) for value in lineage_values):
        # The cell is an earlier version's composition the reviewer never
        # touched; the ``question`` cell owns the stem.
        flags.append(
            f"{label}: question_text still carries an earlier version's "
            "composition and was ignored; the question cell owns the stem"
        )
        return
    projected = assessment_workbook._question_record(
        edited, sheet, context.profile,
        descriptive_answer_slots=context.slots,
        source_book=context.source_book,
    )
    projected_cell = assessment_workbook._cell_value(
        projected.get("question_text"), context=f"{sheet}:{cell}",
    )
    from ..bulk_import.presentation import canonical_cell_value

    if _cells_equal(canonical_cell_value(projected_cell), row.get(cell)):
        # The cell is exactly what the edited stem/options re-compose to.
        return
    edits.append({
        "question_label": label,
        "field": cell,
        "before": _json_safe(expected.get(cell)),
        "after": _json_safe(row.get(cell)),
    })
    internal = _rich_value(row.get(cell))
    parts = context.question_text_parts(edited, sheet)
    stem: str | None = None
    if parts is not None:
        prefix, suffix = parts
        if internal.startswith(prefix) and internal.endswith(suffix):
            middle = internal[len(prefix):]
            middle = middle[: len(middle) - len(suffix)] if suffix else middle
            stem = middle
            if sheet == "Subjective":
                placeholders = [
                    str(answer.get("placeholder") or "").strip()
                    for answer in (edited.get("answers") or [])
                    if isinstance(answer, Mapping)
                    and len(str(answer.get("placeholder") or "").strip()) == 1
                    and "a" <= str(answer.get("placeholder") or "").strip() <= "t"
                ]
                blank = assessment_workbook._SUBJECTIVE_BLANK
                if middle.count(blank) == len(placeholders):
                    for placeholder in placeholders:
                        middle = middle.replace(blank, f"$${placeholder}$$", 1)
                    stem = middle
                elif placeholders:
                    stem = None
    if stem is None:
        edited["question_text"] = internal
        flags.append(
            f"{label}: question_text could not be reduced mechanically to "
            "its question stem; it was kept verbatim and the contract gate "
            "(question must equal question_text) names the disagreement"
        )
        return
    if question_changed and str(edited.get("question") or "") != stem:
        edited["question_text"] = stem
        flags.append(
            f"{label}: the edited question and question_text cells "
            "disagree about the stem; both were kept verbatim and the "
            "contract gate names the disagreement"
        )
        return
    edited["question"] = stem
    edited["question_text"] = stem


def _reverse_row(
    context: _RowContext, candidate: Mapping, sheet: str,
    expected: Mapping[str, Any], row: Mapping[str, Any], fields: list[str],
    *, edits: list[dict], flags: list[str],
    group_edits: dict[str, dict[str, Any]],
    lineage: _Lineage | None = None,
) -> dict:
    """Apply one reviewed row to its candidate; returns the edited copy."""
    label = str(candidate.get("question_label") or "")
    edited = copy.deepcopy(dict(candidate))
    hierarchy_fields, group_fields, question_fields = _band_fields(fields)

    def _record(cell: str) -> None:
        edits.append({
            "question_label": label,
            "field": cell,
            "before": _json_safe(expected.get(cell)),
            "after": _json_safe(row.get(cell)),
        })

    # Hierarchy cells are the published Concept file's, never the Master's.
    touched_hierarchy = [
        field for field in hierarchy_fields
        if field not in assessment_workbook._UPDATE_FIELD_PRESENCE
        and field not in _DERIVED_AGGREGATE_FIELDS
        and not _cells_equal(expected.get(field), row.get(field))
    ]
    if touched_hierarchy:
        flags.append(
            f"{label}: Chapter/Topic/Concept cells changed "
            f"({', '.join(touched_hierarchy)}) were not applied; those "
            "columns are governed by the published Concept file (Step 02)"
        )
    # Group band: only the description is the group's own prose.
    group_key = str(candidate.get("group_key") or "")
    if "group_description" in group_fields and not _cells_equal(
        expected.get("group_description"), row.get("group_description"),
    ):
        proposed = _rich_value(row.get("group_description"))
        previous = group_edits.get(group_key)
        if previous is not None and previous["after"] != proposed:
            flags.append(
                f"{label}: group {group_key!r} description differs between "
                "its rows; the first reviewed value was kept"
            )
        elif previous is None:
            group_edits[group_key] = {
                "group_key": group_key,
                "field": "group_description",
                "before": _json_safe(expected.get("group_description")),
                "after": proposed,
                "question_label": label,
            }
    touched_group = [
        field for field in _GROUP_IDENTITY_FIELDS
        if field in group_fields
        and field not in _DERIVED_AGGREGATE_FIELDS
        and not _cells_equal(expected.get(field), row.get(field))
    ]
    if touched_group:
        flags.append(
            f"{label}: Group cells changed ({', '.join(touched_group)}) "
            "were not applied; the group name is its machine identity and "
            "the roll-ups are recomputed from the rows"
        )
    # Question-band scalars.
    for cell, field in _SCALAR_FIELDS.items():
        if cell not in question_fields:
            continue
        if _cells_equal(expected.get(cell), row.get(cell)):
            continue
        _record(cell)
        value = row.get(cell)
        if cell in _NUMERIC_SCALARS:
            edited[field] = _numeric_value(value)
        elif cell in _RICH_TEXT_SCALARS:
            edited[field] = _rich_value(value)
        else:
            edited[field] = _text(value)
    question_changed = not _cells_equal(expected.get("question"), row.get("question"))
    if question_changed:
        _record("question")
        stem = _rich_value(row.get("question"))
        edited["question"] = stem
        edited["question_text"] = stem
    # Answer blocks.
    multipart_projection = (
        sheet == "Descriptive"
        and bool(candidate.get("sub_questions"))
        and context.column_policy.get("multipart_parent_projection")
        == "ordered_child_union"
    )
    if multipart_projection:
        touched = [
            cell for number in _block_numbers(fields, _ANSWER_BLOCK_RE)
            for cell in _answer_cells(sheet, number).values()
            if not _cells_equal(expected.get(cell), row.get(cell))
        ]
        if touched:
            flags.append(
                f"{label}: parent rubric cells ({', '.join(touched)}) are a "
                "projection of the sub-question criteria and were not "
                "applied; edit the sq columns instead"
            )
    else:
        _reverse_answers(
            sheet, candidate, edited, expected, row, question_fields,
            label=label, edits=edits, flags=flags,
        )
    if sheet == "Descriptive":
        _reverse_sub_questions(
            candidate, edited, expected, row, question_fields,
            label=label, edits=edits, flags=flags,
        )
    _reverse_question_text(
        context, sheet, edited, expected, row, label=label,
        question_changed=question_changed, edits=edits, flags=flags,
        lineage_values=(
            lineage.question_text_values(label) if lineage is not None else []
        ),
    )
    return edited


# --------------------------------------------------------------------------- #
# Submit: the reviewed Master becomes a new release version
# --------------------------------------------------------------------------- #

def _parse_upload(data: bytes) -> dict:
    if not data:
        raise MasterReviewRefused("the uploaded file is empty")
    try:
        return assessment_workbook.parse_workbook(data)
    except Exception as exc:  # noqa: BLE001 — openpyxl raises many types
        raise MasterReviewRefused(
            "the uploaded file is not a readable .xlsx workbook; upload the "
            "edited Master file exactly as downloaded"
        ) from exc


def _chapter_id(release: models.AssessmentRelease, job: models.UploadJob) -> int:
    staged = (release.payload or {}).get("concept_snapshot")
    if isinstance(staged, Mapping) and staged.get("target_chapter_id"):
        return int(staged["target_chapter_id"])
    snapshot = release.concept_snapshot or {}
    if snapshot.get("target_chapter_id"):
        return int(snapshot["target_chapter_id"])
    scope = job.deposit_scope_ids or []
    if scope:
        return int(scope[0])
    raise MasterReviewConflict(
        "this Master release records no target chapter; it cannot be "
        "re-versioned from a reviewed file"
    )


_BLUEPRINT_MIRRORED_FIELDS = (
    "question_category", "cognitive_skill", "difficulty", "marks",
)


def _mirror_blueprint_cells(
    payload: dict, candidate_by_label: Mapping[str, Mapping],
    edited_by_label: Mapping[str, Mapping], candidates: list[Mapping],
    flags: list[str],
) -> list[dict]:
    """Carry a reviewed category/skill/difficulty/marks into its lone cell."""
    cells = [
        cell for cell in payload.get("blueprint_cells") or []
        if isinstance(cell, dict)
    ]
    if not cells:
        return []
    cells_by_id = {str(cell.get("cell_id") or ""): cell for cell in cells}
    references: dict[str, list[str]] = {}
    for candidate in candidates:
        references.setdefault(
            str(candidate.get("blueprint_cell_id") or ""), [],
        ).append(str(candidate.get("candidate_id") or ""))
    derived: list[dict] = []
    for label, edited in edited_by_label.items():
        original = candidate_by_label.get(label) or {}
        changed = [
            field for field in _BLUEPRINT_MIRRORED_FIELDS
            if edited.get(field) != original.get(field)
        ]
        if not changed:
            continue
        cell_id = str(edited.get("blueprint_cell_id") or "")
        cell = cells_by_id.get(cell_id)
        if cell is None:
            continue
        if len(references.get(cell_id, [])) != 1:
            flags.append(
                f"{label}: blueprint cell {cell_id!r} is shared by "
                f"{len(references.get(cell_id, []))} questions, so its "
                f"{', '.join(changed)} were left as recorded; the contract "
                "gate names the disagreement"
            )
            continue
        for field in changed:
            derived.append({
                "question_label": label,
                "cell_id": cell_id,
                "field": field,
                "before": _json_safe(cell.get(field)),
                "after": _json_safe(edited.get(field)),
            })
            cell[field] = copy.deepcopy(edited.get(field))
    return derived


def _restore_lineage(
    db: Session, current: models.AssessmentRelease,
    failed: models.AssessmentRelease, *, previous_state: str,
) -> None:
    """Put the Step 2 version back at the head when publishing v+1 failed."""
    try:
        failed.state = "superseded"
        failed.superseded_at = datetime.utcnow()
        current.state = previous_state
        current.superseded_at = None
        db.commit()
    except Exception:  # noqa: BLE001 — best effort, never masks the cause
        db.rollback()
        _LOGGER.warning(
            "could not restore the Master lineage after a failed reviewed "
            "publication release_uid=%s", current.release_uid, exc_info=True,
        )


def _unchanged_round(
    db: Session, job: models.UploadJob, release: models.AssessmentRelease,
    state: Mapping[str, Any], previous_lane_state: Mapping[str, Any], *,
    lane: str, filename: str, digest: str, accepted: list[str],
    flags: list[str], owner_sub: str,
) -> dict[str, Any]:
    """Record a confirming upload that changes nothing; no new version."""
    if digest not in accepted:
        accepted.append(digest)
    lane_state = {
        **previous_lane_state,
        "lane": lane,
        "policy_version": POLICY_VERSION,
        "status": (
            LANE_STATUS_PUBLISHED
            if str(previous_lane_state.get("status") or "") == LANE_STATUS_PUBLISHED
            and int(previous_lane_state.get("version") or 0) == int(release.version)
            else LANE_STATUS_REVIEWED
        ),
        "filename": filename,
        "sha256": digest,
        "uploaded_at": _now(),
        "owner": str(owner_sub or job.owner_sub or ""),
        **_release_identity(release),
        "changed_fields": [],
        "omitted": [],
        "added": [],
        "flags": list(flags),
        "readiness": str((release.diagnostics or {}).get("readiness") or ""),
        "unchanged": True,
        "accepted_sha256s": list(accepted),
    }
    job.detail = (
        f"Reviewed {lane} Master file {filename!r} matches the current "
        f"version v{release.version}; no new version was created."
    )
    marker = concept_release.update_concept_review_state(
        db, job, master_review={lane: lane_state},
    )
    return {
        "lane": lane,
        "filename": filename,
        "input_sha256": digest,
        **_release_identity(release),
        "round_recorded": False,
        "changed_fields": [],
        "omitted_questions": [],
        "added_questions": [],
        "readiness": lane_state["readiness"],
        "issues": _release_issues(release) + list(flags),
        "master_review": lane_state,
        "review_workflow": marker,
    }


def submit_reviewed_master(
    db: Session,
    job: models.UploadJob,
    *,
    lane: object,
    workbook_bytes: bytes,
    filename: str = "",
    owner_sub: str = "",
) -> dict[str, Any]:
    """Apply the reviewer's edited Master workbook as a new release version."""

    resolved = concept_release.normalize_lane(lane)
    db.refresh(job)
    state = _review_gate(job)
    _require_step_three(job, state, operation="submit a reviewed Master file")
    release = _live_release(db, job, resolved)
    if not (release.publication or {}).get("directory"):
        raise MasterReviewConflict(
            f"the {resolved} Master release {release.release_uid} "
            f"v{release.version} is not published yet; rebuild the Master "
            "files before submitting a reviewed file"
        )
    safe_name = Path(str(filename or "")).name or "reviewed-master.xlsx"
    digest = hashlib.sha256(workbook_bytes).hexdigest()
    previous_lane_state = dict(
        (state.get("master_review") or {}).get(resolved) or {}
    )
    # The files already accepted FOR THIS VERSION: the rendered download
    # itself and every upload that produced or confirmed it. An identical
    # upload is the same decision, so it is answered without a diff.
    accepted = (
        list(previous_lane_state.get("accepted_sha256s") or [])
        if int(previous_lane_state.get("version") or 0) == int(release.version)
        and str(previous_lane_state.get("release_uid") or "") == str(release.release_uid)
        else []
    )
    rendered_digest = str((release.workbook_hashes or {}).get("master_xlsx") or "")
    if rendered_digest and rendered_digest not in accepted:
        accepted.insert(0, rendered_digest)
    if digest in accepted:
        return _unchanged_round(
            db, job, release, state, previous_lane_state,
            lane=resolved, filename=safe_name, digest=digest,
            accepted=accepted, flags=[], owner_sub=owner_sub,
        )
    profile = _profile(release)
    snapshot = release.concept_snapshot or {}
    schema = assessment_workbook.output_schema("master", profile, snapshot)

    parsed = _parse_upload(workbook_bytes)
    header_errors = assessment_workbook._header_errors(parsed, schema)
    if header_errors:
        raise MasterReviewRefused(
            "the uploaded workbook does not have the downloaded Master "
            "layout (" + "; ".join(header_errors) + "); keep the three "
            "sheets, their order and their header rows exactly as downloaded"
        )

    expected_parsed = assessment_workbook.parse_workbook(
        _published_master_bytes(release, profile)
    )
    expected_index = _index_rows(expected_parsed)
    candidates = [
        dict(candidate)
        for candidate in (release.payload or {}).get("candidates") or []
        if isinstance(candidate, Mapping)
    ]
    candidate_by_label = {
        str(candidate.get("question_label") or ""): candidate
        for candidate in candidates
        if str(candidate.get("question_label") or "")
    }
    context = _RowContext(
        profile=profile, schema=schema,
        source_book=str(snapshot.get("source_book") or ""),
    )
    lineage = _Lineage(db, release, profile)

    refusals: list[str] = []
    edits: list[dict] = []
    flags: list[str] = []
    group_edits: dict[str, dict[str, Any]] = {}
    edited_by_label: dict[str, dict] = {}
    seen_labels: set[str] = set()
    for sheet in assessment_workbook.SHEET_ORDER:
        data = parsed["sheets"][sheet]
        fields = list(data["fields"])
        _hierarchy, _group, question_fields = _band_fields(fields)
        rows = data["rows"]
        numbers = data.get("row_numbers") or list(range(3, 3 + len(rows)))
        for row, number in zip(rows, numbers):
            if not _has_question_band(row, question_fields):
                continue
            label = _text(row.get("question_label")).strip()
            if not label or label not in candidate_by_label or label not in expected_index:
                refusals.append(
                    f"{sheet} row {number}: question_label {label!r} matches "
                    f"no question in Master release {release.release_uid} "
                    f"v{release.version}"
                )
                continue
            if label in seen_labels:
                refusals.append(
                    f"{sheet} row {number}: question_label {label!r} appears "
                    "more than once in the uploaded file"
                )
                continue
            seen_labels.add(label)
            expected_sheet, expected_row, _expected_number = expected_index[label]
            if expected_sheet != sheet:
                refusals.append(
                    f"{sheet} row {number}: {label!r} is a {expected_sheet} "
                    "question in this release; the sheet decides its answer "
                    "layout, so a question cannot change sheet in Step 03"
                )
                continue
            edited_by_label[label] = _reverse_row(
                context, candidate_by_label[label], sheet, expected_row, row,
                fields, edits=edits, flags=flags, group_edits=group_edits,
                lineage=lineage,
            )
    if refusals:
        raise MasterReviewRefused(
            "the reviewed Master file was not applied: "
            + "; ".join(refusals)
            + ". Reviewer-authored questions cannot be added in Step 03 — "
            "add them in Step 02's Concept file (Types/Cases) and rebuild "
            "the Master files; to keep an existing question, restore its "
            "question_label exactly as downloaded"
        )

    omitted = [
        {
            "question_label": label,
            "candidate_id": str(candidate_by_label[label].get("candidate_id") or ""),
            "sheet_kind": str(candidate_by_label[label].get("sheet_kind") or ""),
            "group_key": str(candidate_by_label[label].get("group_key") or ""),
            "concept_key": str(candidate_by_label[label].get("concept_key") or ""),
        }
        for label in expected_index
        if label in candidate_by_label and label not in seen_labels
    ]
    group_changes = list(group_edits.values())

    if not edits and not omitted and not group_changes:
        # Nothing to version: the reviewer confirmed the current Master.
        return _unchanged_round(
            db, job, release, state, previous_lane_state,
            lane=resolved, filename=safe_name, digest=digest,
            accepted=accepted, flags=flags, owner_sub=owner_sub,
        )

    # The new payload: the current release's payload with the reviewed
    # candidates and groups. Everything else (source atoms, blueprint,
    # snapshot seal, refinements, audit) is carried untouched.
    payload = copy.deepcopy(dict(release.payload or {}))
    omitted_ids = {item["candidate_id"] for item in omitted}
    omitted_labels = {item["question_label"] for item in omitted}
    new_candidates: list[dict] = []
    for candidate in candidates:
        label = str(candidate.get("question_label") or "")
        if label in omitted_labels:
            continue
        new_candidates.append(edited_by_label.get(label, candidate))
    payload["candidates"] = new_candidates
    # The frozen blueprint cell records the API's category/skill/difficulty/
    # marks decision for its candidate, and the format contract requires the
    # two to agree. A reviewer's edit to one of those fields is the human
    # decision that now owns the cell too — mirrored only when exactly one
    # candidate references the cell (identity accounting, no judgment); a
    # shared cell is left as recorded and the contract gate names it.
    blueprint_cell_edits = _mirror_blueprint_cells(
        payload, candidate_by_label, edited_by_label, new_candidates, flags,
    )
    remaining_groups = {
        str(candidate.get("group_key") or "") for candidate in new_candidates
    }
    emptied_groups = {
        item["group_key"] for item in omitted
    } - remaining_groups
    new_groups: list[dict] = []
    removed_groups: list[str] = []
    for group in payload.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        record = copy.deepcopy(dict(group))
        group_key = str(record.get("group_key") or "")
        if "member_candidate_ids" in record and isinstance(
            record.get("member_candidate_ids"), list,
        ):
            record["member_candidate_ids"] = [
                candidate_id for candidate_id in record["member_candidate_ids"]
                if str(candidate_id) not in omitted_ids
            ]
        if group_key in emptied_groups and int(record.get("group_sequence") or 0) != 1:
            # A variant group emptied by the omission; the required BG01/
            # IG01/AG01 shells stay (they are structure, not rows).
            removed_groups.append(group_key)
            continue
        if group_key in group_edits:
            record["semantic_description"] = group_edits[group_key]["after"]
        new_groups.append(record)
    payload["groups"] = new_groups
    if isinstance(payload.get("placements"), list):
        payload["placements"] = [
            placement for placement in payload["placements"]
            if not (
                isinstance(placement, Mapping)
                and str(placement.get("candidate_id") or "") in omitted_ids
            )
        ]
    review_record = {
        "policy_version": POLICY_VERSION,
        "lane": resolved,
        "sha256": digest,
        "filename": safe_name,
        "uploaded_at": _now(),
        "owner": str(owner_sub or job.owner_sub or ""),
        "supersedes": _release_identity(release),
        "edits": edits,
        "group_edits": group_changes,
        "blueprint_cell_edits": blueprint_cell_edits,
        "omitted": omitted,
        "removed_groups": removed_groups,
        "added": [],
        "flags": flags,
    }
    history = list(payload.get("master_review_history") or [])
    if isinstance(payload.get("master_review"), Mapping):
        history.append(copy.deepcopy(dict(payload["master_review"])))
    payload["master_review_history"] = history
    payload["master_review"] = review_record
    provider_identity = copy.deepcopy(dict(release.provider_identity or {}))
    provider_identity["master_review"] = {
        "policy_version": POLICY_VERSION,
        "sha256": digest,
        "filename": safe_name,
        "uploaded_at": review_record["uploaded_at"],
        "owner": review_record["owner"],
        "edit_count": len(edits),
        "group_edit_count": len(group_changes),
        "omitted_count": len(omitted),
        "added_count": 0,
        "supersedes": _release_identity(release),
    }
    previous_state = str(release.state)
    new_release = release_service.create_release(
        db,
        chapter_id=_chapter_id(release, job),
        payload=payload,
        job_id=job.id,
        owner_sub=release.owner_sub,
        supersedes=release,
        lane=resolved,
        layout_id=str(release.layout_id or release_core.layout_id()),
        provider_identity=provider_identity,
    )
    try:
        new_release = release_service.publish_release(db, new_release)
    except Exception as exc:
        db.rollback()
        _restore_lineage(db, release, new_release, previous_state=previous_state)
        if isinstance(exc, release_service.UploadRefused):
            raise MasterReviewConflict(str(exc)) from exc
        raise MasterReviewError(
            f"the reviewed {resolved} Master could not be published as "
            f"v{release.version + 1} ({type(exc).__name__}); the current "
            f"version v{release.version} remains live"
        ) from exc

    readiness = str((new_release.diagnostics or {}).get("readiness") or "")
    issues = _release_issues(new_release) + flags
    # The new version's own accepted set: the file that produced it plus its
    # rendered download.
    accepted = [digest]
    rendered_digest = str((new_release.workbook_hashes or {}).get("master_xlsx") or "")
    if rendered_digest and rendered_digest not in accepted:
        accepted.append(rendered_digest)
    lane_state = {
        "lane": resolved,
        "policy_version": POLICY_VERSION,
        "status": LANE_STATUS_REVIEWED,
        "filename": safe_name,
        "sha256": digest,
        "uploaded_at": review_record["uploaded_at"],
        "owner": review_record["owner"],
        **_release_identity(new_release),
        "supersedes": _release_identity(release),
        "changed_fields": edits,
        "group_edits": group_changes,
        "omitted": omitted,
        "removed_groups": removed_groups,
        "added": [],
        "flags": flags,
        "readiness": readiness,
        "issues": issues,
        "rounds": int(previous_lane_state.get("rounds") or 0) + 1,
        "accepted_sha256s": accepted,
        "unchanged": False,
    }
    if isinstance(previous_lane_state.get("published"), Mapping):
        # The receipt of the version that WAS published stays readable; the
        # publish act compares identities, so this version is not "done".
        lane_state["published"] = copy.deepcopy(dict(previous_lane_state["published"]))
    master_outputs = copy.deepcopy(dict(state.get("master_outputs") or {}))
    lane_output = dict(master_outputs.get(resolved) or {})
    if lane_output:
        lane_output.update(_release_identity(new_release))
        lane_output["readiness"] = readiness
        master_outputs[resolved] = lane_output
    status_update = None
    if str(state.get("status") or "") == concept_release.CONCEPT_REVIEW_PUBLISHED:
        # A lane has a new, unpublished version again.
        status_update = concept_release.CONCEPT_REVIEW_MASTER_READY
    job.detail = (
        f"Reviewed {resolved} Master file {safe_name!r} accepted as "
        f"v{new_release.version} ({len(edits)} cell edit(s), "
        f"{len(omitted)} omitted question(s)); readiness {readiness!r}."
    )
    marker = concept_release.update_concept_review_state(
        db, job,
        status=status_update,
        master_review={resolved: lane_state},
        master_outputs=master_outputs if lane_output else None,
    )
    return {
        "lane": resolved,
        "filename": safe_name,
        "input_sha256": digest,
        **_release_identity(new_release),
        "round_recorded": True,
        "changed_fields": edits,
        "omitted_questions": omitted,
        "added_questions": [],
        "readiness": readiness,
        "issues": issues,
        "master_review": lane_state,
        "review_workflow": marker,
    }


# --------------------------------------------------------------------------- #
# Publish: database write plus the shared CMS workbook append
# --------------------------------------------------------------------------- #

def _stage_master_questions_workbook(
    db: Session, target: Path, question_ids: list[int],
) -> tuple[Path, dict[str, Any]]:
    """Append the rows to a sibling copy of the shared workbook."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{target.stem}-master-",
        suffix=target.suffix or ".xlsx",
        dir=target.parent,
    )
    os.close(descriptor)
    staged = Path(staged_name)
    try:
        if target.exists():
            shutil.copy2(target, staged)
        else:
            # ``append_questions`` creates a canonical workbook when the
            # path does not exist.
            staged.unlink()
        written = writer.append_questions(db, staged, question_ids)
        return staged, written
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _append_master_questions_to_cms_workbook(
    db: Session, target: Path, question_ids: list[int],
) -> dict[str, Any]:
    """Mirror ``build_concepts._commit_and_publish_concept_workbook``.

    The database half was committed by ``upload_master_to_database``; this
    half records its intent durably before the atomic publish, so a
    failure after the intent leaves the staged sibling queued for the next
    workbook operation (``recover_pending_publication``) instead of a
    silently divergent export. Repeating the publish act converges: the
    database write is idempotent and ``append_questions`` skips placements
    the workbook already carries.
    """
    target = Path(target)
    receipt: dict[str, Any] = {
        "path": target.name,
        "question_ids": list(question_ids),
    }
    with workbook_sync.output_workbook_lock():
        if workbook_sync.recover_pending_publication(target):
            receipt["recovered_pending_publication"] = True
        staged: Path | None = None
        intent_recorded = False
        try:
            staged, written = _stage_master_questions_workbook(
                db, target, question_ids,
            )
            workbook_sync.record_publication_intent(staged, target)
            intent_recorded = True
            workbook_sync.atomic_publish(staged, target)
            workbook_sync.clear_publication_intent(target)
            staged = None
            receipt.update(written)
            receipt["status"] = "published"
            return receipt
        except Exception as exc:  # noqa: BLE001 — reported, never masked
            if not intent_recorded:
                workbook_sync.clear_publication_intent(target)
                if staged is not None:
                    staged.unlink(missing_ok=True)
            _LOGGER.error(
                "Master questions are committed but the CMS workbook append "
                "was interrupted (%s); %s",
                exc,
                "the staged workbook remains queued"
                if intent_recorded else "repeat the publish act to complete it",
            )
            receipt["status"] = "queued"
            receipt["queued_reason"] = (
                "the database write is complete; the CMS workbook append was "
                f"interrupted ({type(exc).__name__}) and "
                + (
                    "remains queued for automatic completion"
                    if intent_recorded
                    else "completes on the next publish act"
                )
            )
            return receipt


def _published_question_ids(
    db: Session, release: models.AssessmentRelease,
) -> list[int]:
    labels = sorted({
        str(candidate.get("question_label") or "")
        for candidate in (release.concept_snapshot or {}).get("candidates") or []
        if isinstance(candidate, Mapping) and str(candidate.get("question_label") or "")
    })
    if not labels:
        return []
    rows = (
        db.query(models.Question)
        .filter(models.Question.question_label.in_(labels))
        .order_by(models.Question.id)
        .all()
    )
    return [
        int(row.id) for row in rows
        if str((row.route_audit or {}).get("release_uid") or "")
        == str(release.release_uid)
    ]


def _every_lane_published(
    db: Session, job: models.UploadJob, state: Mapping[str, Any],
    master_review: Mapping[str, Any],
) -> bool:
    lanes = list(state.get("available_lanes") or state.get("required_lanes") or [])
    if not lanes:
        return False
    for lane in lanes:
        live = release_core.latest_release_for_lane(db, job.id, lane)
        if live is None:
            return False
        published = (master_review.get(lane) or {}).get("published")
        if not isinstance(published, Mapping):
            return False
        if (
            str(published.get("release_uid") or "") != str(live.release_uid)
            or int(published.get("version") or 0) != int(live.version)
            or str((published.get("cms_workbook") or {}).get("status") or "")
            != "published"
        ):
            return False
    return True


def publish_reviewed_master(
    db: Session,
    job: models.UploadJob,
    *,
    lane: object,
    owner_sub: str = "",
) -> dict[str, Any]:
    """Write the lane's live Master to the database and the CMS workbook.

    A second round on a lane that was already published is the same act: the
    reviewed version carries the same ``release_uid``, and the database write
    UPDATES the rows its earlier version published instead of passing over
    them. The receipt names what was created, what was updated and what was
    skipped because it was already identical; a label the write can neither
    create nor update refuses the act (409) rather than recording a
    publication that did not happen.
    """

    resolved = concept_release.normalize_lane(lane)
    db.refresh(job)
    state = _review_gate(job)
    _require_step_three(job, state, operation="publish the reviewed Master file")
    concept_payload = concept_release.release_payload(job, lane=resolved)
    if concept_payload is None or not (
        concept_payload.get("summary") or {}
    ).get("database_uploaded"):
        raise MasterReviewConflict(
            f"Publish the {resolved} Concept file first; the Master's groups "
            "and questions attach to its published concepts"
        )
    release = _live_release(db, job, resolved)
    master_review = dict(state.get("master_review") or {})
    lane_state = dict(master_review.get(resolved) or {})
    recorded = lane_state.get("published")
    if (
        isinstance(recorded, Mapping)
        and str(recorded.get("release_uid") or "") == str(release.release_uid)
        and int(recorded.get("version") or 0) == int(release.version)
        and str((recorded.get("cms_workbook") or {}).get("status") or "") == "published"
    ):
        return {
            "lane": resolved,
            **_release_identity(release),
            "database": copy.deepcopy(dict(recorded.get("database") or {})),
            "cms_workbook": copy.deepcopy(dict(recorded.get("cms_workbook") or {})),
            "publication_status": "published",
            "master_review": lane_state,
            "review_workflow": state,
        }

    try:
        database = release_service.upload_master_to_database(
            db, release, owner_sub=str(owner_sub or job.owner_sub or ""),
        )
    except release_service.ReleaseNotFound as exc:
        raise MasterReviewNotFound(str(exc)) from exc
    except release_service.UploadRefused as exc:
        raise MasterReviewConflict(
            f"the {resolved} Master release {release.release_uid} "
            f"v{release.version} was not published: {exc}"
        ) from exc
    db.refresh(release)
    question_ids = _published_question_ids(db, release)
    cms_workbook = _append_master_questions_to_cms_workbook(
        db, config.BULK_IMPORT_OUTPUT, question_ids,
    )
    updated_labels = [str(label) for label in (database.get("labels_updated") or [])]
    if updated_labels:
        # Honest about the CMS half of a SECOND round: the shared workbook is
        # written by the append-only writer, which skips a (label, placement)
        # it already carries. The database rows above now carry the reviewer's
        # edit; the workbook rows those labels already had were not rewritten
        # by this act, so the receipt says so instead of implying the export
        # was re-rendered.
        cms_workbook = {
            **cms_workbook,
            "labels_updated_in_database": list(updated_labels),
            "existing_rows_not_appended": (
                "the shared workbook already carried these question "
                "placements, so the append-only CMS writer added no row for "
                "them and refreshed only the category, cognitive-skill and "
                "source cells it keeps current on a row it already has"
            ),
        }
    publication_status = (
        "published" if cms_workbook.get("status") == "published" else "queued"
    )
    published = {
        "uploaded_at": _now(),
        "owner": str(owner_sub or job.owner_sub or ""),
        **_release_identity(release),
        "database": _json_safe(database),
        "cms_workbook": _json_safe(cms_workbook),
    }
    lane_state = {
        **lane_state,
        "lane": resolved,
        "policy_version": lane_state.get("policy_version") or POLICY_VERSION,
        **_release_identity(release),
        "status": (
            LANE_STATUS_PUBLISHED
            if publication_status == "published"
            else str(lane_state.get("status") or LANE_STATUS_REVIEWED)
        ),
        "published": published,
    }
    master_review[resolved] = lane_state
    status_update = (
        concept_release.CONCEPT_REVIEW_PUBLISHED
        if _every_lane_published(db, job, state, master_review)
        else None
    )
    job.detail = (
        f"{resolved.capitalize()} Master v{release.version} published: "
        f"{int(database.get('questions_created') or 0)} question(s) written "
        f"and {int(database.get('questions_updated') or 0)} updated in the "
        f"database; CMS workbook append {publication_status}."
    )
    marker = concept_release.update_concept_review_state(
        db, job, status=status_update, master_review={resolved: lane_state},
    )
    return {
        "lane": resolved,
        **_release_identity(release),
        "database": _json_safe(database),
        "cms_workbook": _json_safe(cms_workbook),
        "publication_status": publication_status,
        "master_review": lane_state,
        "review_workflow": marker,
    }
