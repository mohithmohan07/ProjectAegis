"""School/program assessment profiles.

The assessment pipeline is school-agnostic: source atoms, blueprint cells,
authoring, routing, grouping, rendering, and publication know nothing about
any particular school. Everything a school's paper format dictates — the
appears-in wire value, whether Subjective rows may carry data, whether
automatic secondary placements exist — lives in a profile, never in code.

The default profile carries the values of the reference school (MES) whose
question papers and bulk-import workbooks defined the initial contract.
They were provided as a reference for how assessments are built; another
school is another profile, not another pipeline.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from ..bulk_import import layouts


# Master Governing Contract v2.0 §21 (True/False override): every True or
# False item is a Subjective row with one placeholder-bound accepted answer.
# The category therefore exists ONLY on the Subjective sheet, in every
# profile; a legacy Objective spelling is not a legal cell.
_GENERIC_QUESTION_FORMATS: dict[str, dict[str, dict[str, Any]]] = {
    "objective": {
        "Multiple Choice Question": {},
        "Assertion & Reasons": {},
        "Fill in the Blanks": {},
    },
    "subjective": {
        "Fill in the Blanks": {},
        "True/False": {},
        "Very Short Answer": {},
        "Short Answer": {},
        "Sentence Transformation": {},
        "Error Correction": {},
    },
    "descriptive": {
        "Long Answer": {},
        "Case Based Questions": {},
        "Passage Based Questions": {},
        "Extract Based Questions": {},
        "Composition Writing": {},
    },
}

_MSBSHSE_BOARD_ALIASES = (
    "mh",
    "mh board",
    "maharashtra",
    "maharashtra (msbshse)",
    "maharashtra board",
    "maharashtra state board",
    "maharashtra state board of secondary and higher secondary education",
    "msbshse",
)
_GRADE_6_ALIASES = (
    "6", "06", "class 6", "class 06", "grade 6", "grade 06",
    "standard 6", "standard 06", "std 6", "std 06",
)
_MATHEMATICS_SUBJECT_ALIASES = (
    "math", "maths", "mathematics",
)
_ENGLISH_SUBJECT_ALIASES = (
    "english", "english language", "english literature", "english grammar",
)
# The owner's Question Duration Matrix (2026-08-29) shares one sheet
# between Mathematics and Physics; Physics is its own alias set so plain
# "Science" never silently inherits a physics contract.
_PHYSICS_SUBJECT_ALIASES = (
    "physics",
)
_SOCIAL_SCIENCE_SUBJECT_ALIASES = (
    "social science", "social sciences", "social studies", "sst",
    "history", "geography", "civics", "history and civics",
    "history-civics", "political science", "economics",
)


# Workbook geometry is a run-profile fact, just like the set of enabled
# sheets.  Master Governing Contract v2.0 §14 makes the UPDATE-AWARE schema
# universal: every entity band carries an ``is_update_*`` column and
# Descriptive carries the concept-source column, on every output of every
# board (72 / 380 / 149 columns).  It was first evidenced by the 2026-08-27
# Grade-6 audit workbooks, but it is not a board fact.  Only an explicitly
# frozen profile may widen the Descriptive rubric capacity (the 440-column
# English Post variant); a capacity limit never drops content — it is
# resolved by an accepted wider profile or the row is blocked.  These are
# declarative capabilities selected by metadata + lane; no renderer branch
# names a subject or chapter.
# Register Q27 (2026-09-04): the owner's physical CMS template carries 30
# Descriptive answer blocks on every subject and lane, so the universal
# contract renders the 72/440/149 geometry (``update-aware-master-2``) and
# no profile widens or narrows it — the former English-Post-only 440-column
# variant is simply the universal shape now.
DEFAULT_MASTER_WORKBOOK_CONTRACT: dict[str, Any] = {
    "contract_id": "update-aware-master-2",
    "include_update_fields": True,
    "include_descriptive_concept_source": True,
    "descriptive_answer_slots": layouts.UNIVERSAL_DESCRIPTIVE_ANSWER_SLOTS,
    "natural_label_aggregates": False,
    "aggregate_rendered_questions_only": False,
}

MSBSHSE_GRADE_6_MASTER_WORKBOOK_CONTRACT: dict[str, Any] = {
    # Re-frozen 2026-09-04 on the universal 30-slot geometry (Q27); the
    # aggregate rules below are the board layer, the width is not.
    "contract_id": "msbshse-grade-6-master-2026-09-04",
    "include_update_fields": True,
    "include_descriptive_concept_source": True,
    "descriptive_answer_slots": layouts.UNIVERSAL_DESCRIPTIVE_ANSWER_SLOTS,
    "natural_label_aggregates": True,
    "aggregate_rendered_questions_only": True,
}

# Retired by Q27: the 440-column Descriptive is no longer an English Post
# variant but the universal geometry, so there is nothing left for a
# subject-and-lane override to widen. The names stay importable and empty.
ENGLISH_POST_MASTER_WORKBOOK_OVERRIDES: tuple[dict[str, Any], ...] = ()
# Historical name, kept for callers that imported it.
MSBSHSE_GRADE_6_MASTER_WORKBOOK_OVERRIDES = ENGLISH_POST_MASTER_WORKBOOK_OVERRIDES


# The assessment-format policy supplied in the 2026-08-27 concept-mapping
# audit for Maharashtra Board Class 6 Mathematics.  Category names are the
# canonical spellings from the log's duration table.  A policy row describes
# schema facts only: which sheet owns a category, its mark contract, and the
# duration contract a later marking pass must apply.  It never classifies a
# question locally.
MSBSHSE_GRADE_6_MATHEMATICS_FORMAT_POLICY: dict[str, Any] = {
    "policy_id": "msbshse-grade-6-mathematics-2026-08-27",
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "grade": _GRADE_6_ALIASES,
        "subject": _MATHEMATICS_SUBJECT_ALIASES,
    },
    "difficulty_labels": {
        # Aegis's wire vocabulary -> the audit log's vocabulary.
        "Less": "Easy",
        "Moderate": "Medium",
        "High": "Hard",
    },
    "formats_by_sheet": {
        "objective": {
            "Multiple Choice Question": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1,
                        "Moderate": 1,
                        "High": 1,
                    },
                },
            },
            "Match the Following": {
                "marks": {
                    "mode": "per_subpoint", "marks_per_subpoint": 1,
                    "max_subpoints": 1,
                },
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
            # With options.  The same category on Subjective is the no-option
            # form; the materialization stage owns that semantic distinction.
            "Fill in the blanks": {
                "marks": {
                    "mode": "per_subpoint", "marks_per_subpoint": 1,
                    "max_subpoints": 1,
                },
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
        },
        "subjective": {
            # Without options (audit rule 9).
            "Fill in the blanks": {
                "marks": {"mode": "per_subpoint", "marks_per_subpoint": 1},
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
            # Contract v2.0 §21/§23.1: True or False is a Subjective item with
            # one placeholder-bound accepted answer (formerly an Objective
            # category in this profile; the audited marks/duration contract
            # is unchanged).
            "True or False": {
                "marks": {
                    "mode": "per_subpoint", "marks_per_subpoint": 1,
                    "max_subpoints": 1,
                },
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
        },
        "descriptive": {
            "Very Short Answer Questions": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1,
                        "Moderate": 1,
                        "High": 2,
                    },
                },
            },
            "Short Answer Type (2 Marks)": {
                "marks": {"mode": "fixed", "allowed": (2,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 2,
                        "Moderate": 2,
                        "High": 3,
                    },
                },
            },
            "Short Answer Type (3 Marks)": {
                "marks": {"mode": "fixed", "allowed": (3,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4,
                        "Moderate": 5,
                        "High": 6,
                    },
                },
            },
            "Long Answer Type (4 Marks)": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5,
                        "Moderate": 6,
                        "High": 7,
                    },
                },
            },
            "Long Answer Type (5 Marks)": {
                "marks": {"mode": "fixed", "allowed": (5,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5,
                        "Moderate": 7,
                        "High": 7,
                    },
                },
            },
        },
    },
}


# The Grade-6 English taxonomy stays the audit's exact authoring set (the
# corrected files' closed list; owner scope ruling 2026-08-29: Class-6
# restrictions layer on top of the board-wide matrix below).  What changed
# on 2026-08-29 is that the owner's Question Duration Matrix now supplies
# the marks and duration contracts for these categories, so English marking
# obeys the same prescribed table Mathematics already did.
MSBSHSE_GRADE_6_ENGLISH_FORMAT_POLICY: dict[str, Any] = {
    "policy_id": "msbshse-grade-6-english-2026-08-29",
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "grade": _GRADE_6_ALIASES,
        "subject": _ENGLISH_SUBJECT_ALIASES,
    },
    "difficulty_labels": {
        "Less": "Easy",
        "Moderate": "Medium",
        "High": "Hard",
    },
    "formats_by_sheet": {
        "objective": {
            "Multiple Choice Question": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 1,
                    },
                },
            },
        },
        "subjective": {
            "Fill in the Blanks": {
                "marks": {"mode": "fixed", "allowed": (1, 4)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        1: {"Less": 1, "Moderate": 1, "High": 1},
                        4: {"Less": 4, "Moderate": 5, "High": 5},
                    },
                },
            },
        },
        "descriptive": {
            "Very Short Answer Questions": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 2,
                    },
                },
            },
            "Short Answer Type (2 Marks)": {
                "marks": {"mode": "fixed", "allowed": (2,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 2, "Moderate": 2, "High": 3,
                    },
                },
            },
            "Short Answer Type (3 Marks)": {
                "marks": {"mode": "fixed", "allowed": (3,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4, "Moderate": 5, "High": 6,
                    },
                },
            },
            "Long Answer Type (4 Marks)": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5, "Moderate": 6, "High": 7,
                    },
                },
            },
            "Composition Writing": {
                "marks": {"mode": "fixed", "allowed": (5, 10, 20)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        5: {"Less": 7, "Moderate": 8, "High": 10},
                        10: {"Less": 10, "Moderate": 10, "High": 15},
                        20: {"Less": 15, "Moderate": 15, "High": 20},
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Board-wide format policies from the owner's Question Duration Matrix
# (uploaded 2026-08-29).  Scope ruling: the matrix binds MH Board (MSBSHSE)
# at EVERY grade, per subject; grade-specific policies above are listed
# first in ``assessment_format_overrides`` and therefore win for their
# grades (Class-6 keeps its ban on Case Based / Assertion & Reasons and its
# audited closed sets).  Category names are the matrix's exact spellings.
# ---------------------------------------------------------------------------

MSBSHSE_MATHEMATICS_PHYSICS_FORMAT_POLICY: dict[str, Any] = {
    # The matrix's "Math and Physics" sheet. Match the Following, True or
    # False, and Fill in the blanks are not matrix rows — the owner ruled
    # (2026-08-29) they stay allowed at 1 minute per sub-point, as in the
    # audited Class-6 Mathematics contract.
    "policy_id": "msbshse-mathematics-physics-2026-08-29",
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "subject": _MATHEMATICS_SUBJECT_ALIASES + _PHYSICS_SUBJECT_ALIASES,
    },
    "difficulty_labels": {
        "Less": "Easy",
        "Moderate": "Medium",
        "High": "Hard",
    },
    "formats_by_sheet": {
        "objective": {
            "Multiple Choice Question": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 1,
                    },
                },
            },
            "Assertion & Reasons Type": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 1,
                    },
                },
            },
            "Match the Following": {
                "marks": {
                    "mode": "per_subpoint", "marks_per_subpoint": 1,
                    "max_subpoints": 1,
                },
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
            "Fill in the blanks": {
                "marks": {
                    "mode": "per_subpoint", "marks_per_subpoint": 1,
                    "max_subpoints": 1,
                },
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
        },
        "subjective": {
            "Fill in the blanks": {
                "marks": {"mode": "per_subpoint", "marks_per_subpoint": 1},
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
            # Contract v2.0 §21/§23.1: True or False is Subjective.
            "True or False": {
                "marks": {
                    "mode": "per_subpoint", "marks_per_subpoint": 1,
                    "max_subpoints": 1,
                },
                "duration": {
                    "mode": "per_subpoint",
                    "minutes_per_subpoint": 1,
                },
            },
        },
        "descriptive": {
            "Very Short Answer Questions": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 2,
                    },
                },
            },
            "Short Answer Type (2 Marks)": {
                "marks": {"mode": "fixed", "allowed": (2,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 2, "Moderate": 2, "High": 3,
                    },
                },
            },
            "Short Answer Type (3 Marks)": {
                "marks": {"mode": "fixed", "allowed": (3,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4, "Moderate": 5, "High": 6,
                    },
                },
            },
            "Long Answer Type (4 Marks)": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5, "Moderate": 6, "High": 7,
                    },
                },
            },
            "Long Answer Type (5 Marks)": {
                "marks": {"mode": "fixed", "allowed": (5,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5, "Moderate": 7, "High": 7,
                    },
                },
            },
            "Case Based Questions": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4, "Moderate": 5, "High": 6,
                    },
                },
            },
        },
    },
}

MSBSHSE_ENGLISH_FORMAT_POLICY: dict[str, Any] = {
    # The matrix's "English" sheet, complete — the wider taxonomy (Extract
    # Based, Reading Comprehension, Sentence Transformation, Error
    # Correction, 6-mark Long Answer) applies board-wide; Grade 6 keeps its
    # audited closed set via the policy above.
    "policy_id": "msbshse-english-2026-08-29",
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "subject": _ENGLISH_SUBJECT_ALIASES,
    },
    "difficulty_labels": {
        "Less": "Easy",
        "Moderate": "Medium",
        "High": "Hard",
    },
    "formats_by_sheet": {
        "objective": {
            "Multiple Choice Question": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 1,
                    },
                },
            },
        },
        "subjective": {
            "Fill in the Blanks": {
                "marks": {"mode": "fixed", "allowed": (1, 4)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        1: {"Less": 1, "Moderate": 1, "High": 1},
                        4: {"Less": 4, "Moderate": 5, "High": 5},
                    },
                },
            },
        },
        "descriptive": {
            "Very Short Answer Questions": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 2,
                    },
                },
            },
            "Sentence Transformation": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 2,
                    },
                },
            },
            "Error Correction": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 2,
                    },
                },
            },
            "Short Answer Type (2 Marks)": {
                "marks": {"mode": "fixed", "allowed": (2,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 2, "Moderate": 2, "High": 3,
                    },
                },
            },
            "Short Answer Type (3 Marks)": {
                "marks": {"mode": "fixed", "allowed": (3,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4, "Moderate": 5, "High": 6,
                    },
                },
            },
            "Long Answer Type (4 Marks)": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5, "Moderate": 6, "High": 7,
                    },
                },
            },
            "Long Answer Type (6 Marks)": {
                "marks": {"mode": "fixed", "allowed": (6,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 8, "Moderate": 9, "High": 10,
                    },
                },
            },
            "Extract Based Question": {
                "marks": {"mode": "fixed", "allowed": (5, 16)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        5: {"Less": 6, "Moderate": 7, "High": 8},
                        16: {"Less": 15, "Moderate": 15, "High": 20},
                    },
                },
            },
            "Reading Comprehension": {
                "marks": {"mode": "fixed", "allowed": (10, 20)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        10: {"Less": 12, "Moderate": 14, "High": 16},
                        20: {"Less": 15, "Moderate": 15, "High": 20},
                    },
                },
            },
            "Composition Writing": {
                "marks": {"mode": "fixed", "allowed": (5, 10, 20)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        5: {"Less": 7, "Moderate": 8, "High": 10},
                        10: {"Less": 10, "Moderate": 10, "High": 15},
                        20: {"Less": 15, "Moderate": 15, "High": 20},
                    },
                },
            },
        },
    },
}

MSBSHSE_SOCIAL_SCIENCE_FORMAT_POLICY: dict[str, Any] = {
    # The matrix's "Social Science" sheet.
    "policy_id": "msbshse-social-science-2026-08-29",
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "subject": _SOCIAL_SCIENCE_SUBJECT_ALIASES,
    },
    "difficulty_labels": {
        "Less": "Easy",
        "Moderate": "Medium",
        "High": "Hard",
    },
    "formats_by_sheet": {
        "objective": {
            "Multiple Choice Question": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 1,
                    },
                },
            },
            "Assertion & Reasons Type": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 1,
                    },
                },
            },
        },
        "descriptive": {
            "Very Short Answer Questions": {
                "marks": {"mode": "fixed", "allowed": (1,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 1, "Moderate": 1, "High": 2,
                    },
                },
            },
            "Short Answer Type (2 Marks)": {
                "marks": {"mode": "fixed", "allowed": (2,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 2, "Moderate": 2, "High": 3,
                    },
                },
            },
            "Short Answer Type (3 Marks)": {
                "marks": {"mode": "fixed", "allowed": (3,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4, "Moderate": 5, "High": 6,
                    },
                },
            },
            "Long Answer Type (4 Marks)": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5, "Moderate": 6, "High": 7,
                    },
                },
            },
            "Long Answer Type (5 Marks)": {
                "marks": {"mode": "fixed", "allowed": (5,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 5, "Moderate": 7, "High": 7,
                    },
                },
            },
            "Long Answer Type (6 Marks)": {
                "marks": {"mode": "fixed", "allowed": (6,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 8, "Moderate": 9, "High": 10,
                    },
                },
            },
            "Case Based Questions": {
                "marks": {"mode": "fixed", "allowed": (4,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 4, "Moderate": 5, "High": 6,
                    },
                },
            },
            "Extract based on Map Survey": {
                "marks": {"mode": "fixed", "allowed": (10,)},
                "duration": {
                    "mode": "matrix",
                    "minutes_by_difficulty": {
                        "Less": 8, "Moderate": 8, "High": 10,
                    },
                },
            },
            "Locating and Plotting on Map": {
                "marks": {"mode": "fixed", "allowed": (3, 10)},
                "duration": {
                    "mode": "marks_matrix",
                    "minutes_by_marks": {
                        3: {"Less": 4, "Moderate": 4, "High": 4},
                        10: {"Less": 9, "Moderate": 9, "High": 9},
                    },
                },
            },
        },
    },
}

MSBSHSE_GRADE_6_RUN_PROFILE_OVERRIDE: dict[str, Any] = {
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "grade": _GRADE_6_ALIASES,
        # The audited natural-order label aggregates are evidenced for
        # Mathematics and English; the three-sheet, update-aware geometry
        # itself is now universal (contract v2.0), so this override only
        # carries what remains profile-specific.
        "subject": _MATHEMATICS_SUBJECT_ALIASES,
    },
    "overrides": {
        "sheet_kinds": ("objective", "descriptive", "subjective"),
        "forced_blank_fields": ("question_disclaimer",),
        "master_workbook": MSBSHSE_GRADE_6_MASTER_WORKBOOK_CONTRACT,
    },
}

MSBSHSE_GRADE_6_ENGLISH_RUN_PROFILE_OVERRIDE: dict[str, Any] = {
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "grade": _GRADE_6_ALIASES,
        "subject": _ENGLISH_SUBJECT_ALIASES,
    },
    "overrides": {
        "sheet_kinds": ("objective", "descriptive", "subjective"),
        "forced_blank_fields": ("question_disclaimer",),
        "master_workbook": MSBSHSE_GRADE_6_MASTER_WORKBOOK_CONTRACT,
    },
}

DEFAULT_PROFILE: dict = {
    "name": "reference-1",
    # The exact wire value the reference workbooks carry; never expanded or
    # normalized by the writer.
    "appears_in": "Pre/Post-Worksheet/Test",
    # Which sheet kinds this school's papers use.  It SUPERSEDES the former
    # ``allow_subjective_rows`` boolean (spec-step8 T12/M6b): a boolean
    # cannot express a three-value set, and two keys answering one question
    # is the defect M6 forbids one paragraph above itself.  Read ONLY
    # through ``sheet_kinds`` below.  Contract v2.0 §12/§21: every output
    # carries the three sheets and the Subjective lane (deterministic Fill
    # in the Blanks, True or False) is universal, so the default enables
    # all three; a profile may only narrow it explicitly.
    "sheet_kinds": ("objective", "descriptive", "subjective"),
    # Fields that ship blank in this school's workbooks regardless of what
    # upstream rows carry (spec-step8 T12/M4; Q5/§6 settles the layout, the
    # profile settles the fill practice).  Read ONLY through
    # ``forced_blank_fields`` below.  Contract v2.0 §32.1: the chapter
    # duration is frozen once per chapter and repeated identically across
    # all four outputs, so it is never forced blank; ``question_disclaimer``
    # stays blank unless a profile explicitly requires content (§18).
    "forced_blank_fields": ("question_disclaimer",),
    # Contract v2.0 §18: ``question_source`` is a mandatory per-run scalar
    # naming the publication (the run's frozen source book), stamped on
    # every candidate by the release run.  The historical origin-system
    # constant ("UpSchool DB") is no longer a default: a candidate that
    # reaches the renderer without a source is a release blocker
    # (``question_source_missing``), never a borrowed value.
    "question_source": "",
    # The wire value this school's Master rows carry in ``group_status``
    # when a group declares none.
    "group_status": "Active",
    # Output-role geometry. Concept files always use the committed reference
    # schema; only Master rendering reads this contract.
    "master_workbook": DEFAULT_MASTER_WORKBOOK_CONTRACT,
    "master_workbook_overrides": ENGLISH_POST_MASTER_WORKBOOK_OVERRIDES,
    # Automatic secondary QuestionTag placements are off; a future profile
    # may enable explicit, audited secondaries.
    "automatic_secondary_tags": False,
    # The generic CMS vocabulary remains byte-for-byte the vocabulary used
    # before format policies existed.  Metadata-matched overrides below can
    # narrow it and attach mark/duration contracts without teaching the cell
    # service board-specific prose.
    "assessment_format": {
        "policy_id": "generic-cms",
        "formats_by_sheet": _GENERIC_QUESTION_FORMATS,
    },
    # First match wins: grade-scoped policies come before the board-wide
    # Question Duration Matrix policies (owner scope ruling 2026-08-29), so
    # Class 6 keeps its audited closed sets — including the ban on Case
    # Based / Assertion & Reasons — while every other MH Board grade gets
    # the matrix's subject contract.
    "assessment_format_overrides": (
        MSBSHSE_GRADE_6_MATHEMATICS_FORMAT_POLICY,
        MSBSHSE_GRADE_6_ENGLISH_FORMAT_POLICY,
        MSBSHSE_MATHEMATICS_PHYSICS_FORMAT_POLICY,
        MSBSHSE_ENGLISH_FORMAT_POLICY,
        MSBSHSE_SOCIAL_SCIENCE_FORMAT_POLICY,
    ),
    # Conclusive program metadata can select a complete run-level widening
    # without changing the pinned reference-1 defaults or reinterpreting
    # historical partial profile records.  Only the audited Grade-6 MSBSHSE
    # Mathematics and English sources use this Subjective-sheet/layout
    # contract; other subjects keep the generic profile.
    "run_profile_overrides": (
        MSBSHSE_GRADE_6_RUN_PROFILE_OVERRIDE,
        MSBSHSE_GRADE_6_ENGLISH_RUN_PROFILE_OVERRIDE,
    ),
}

_PROFILES: dict[str, dict] = {
    DEFAULT_PROFILE["name"]: DEFAULT_PROFILE,
}


def get_profile(name: str | None = None) -> dict:
    if name is None:
        return copy.deepcopy(DEFAULT_PROFILE)
    profile = _PROFILES.get(name)
    if profile is None:
        raise KeyError(f"unknown assessment profile {name!r}")
    return copy.deepcopy(profile)


def resolve(profile: Mapping | str | None) -> dict:
    """Accept a profile dict, a registered name, or None (default)."""
    if profile is None:
        return copy.deepcopy(DEFAULT_PROFILE)
    if isinstance(profile, str):
        return get_profile(profile)
    return copy.deepcopy(dict(profile))


def resolve_for_metadata(
    profile: Mapping | str | None,
    metadata: Mapping[str, Any] | None,
) -> dict:
    """Resolve one run profile and apply only conclusive metadata overrides."""

    resolved = resolve(profile)
    # A resolved profile is persisted with its selector metadata and can pass
    # through this boundary again during release/build orchestration.  Start
    # with those carried selectors so an absent (or empty) metadata payload
    # cannot silently erase a previously conclusive run.  A partial explicit
    # payload may fill a selector that was not previously known, but it may
    # not retarget a persisted run.  The latter would leave already-applied
    # sheet/workbook overrides stale, so conflicting values (including an
    # explicit clear) fail closed instead of producing a hybrid profile.
    carried_metadata = resolved.get("_resolved_metadata")
    run_metadata = (
        copy.deepcopy(dict(carried_metadata))
        if isinstance(carried_metadata, Mapping)
        else {}
    )
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            field = str(key)
            if (
                field in ("board", "grade", "subject")
                and field in run_metadata
                and not _same_metadata_selector(
                    resolved, field, run_metadata[field], value,
                )
            ):
                raise ValueError(
                    "cannot retarget resolved assessment profile selector "
                    f"{field!r}: carried {run_metadata[field]!r}, "
                    f"explicit {value!r}"
                )
            # Preserve the carried wire spelling for an equivalent selector;
            # this makes same-run re-entry byte-idempotent across aliases.
            if field not in run_metadata:
                run_metadata[field] = copy.deepcopy(value)
    run_overrides = resolved.get("run_profile_overrides")
    if run_overrides is None:
        run_overrides = DEFAULT_PROFILE["run_profile_overrides"]
    for candidate in run_overrides or ():
        if not isinstance(candidate, Mapping) or not _matches_metadata(
            candidate, run_metadata,
        ):
            continue
        overrides = candidate.get("overrides")
        if isinstance(overrides, Mapping):
            for key in (
                "sheet_kinds", "forced_blank_fields", "master_workbook",
            ):
                if key in overrides:
                    resolved[key] = copy.deepcopy(overrides[key])
        break
    # The resolved profile is persisted with the release and later reaches
    # the deterministic workbook renderer without another directory read.
    # Carry only the exact selector fields the declarative workbook override
    # needs; this is metadata transport, never a subject-specific decision.
    resolved["_resolved_metadata"] = {
        key: copy.deepcopy(run_metadata.get(key))
        for key in ("board", "grade", "subject")
        if _metadata_token(run_metadata.get(key))
    }
    return resolved


def register(profile: Mapping) -> dict:
    """Register one profile under its own name and return it.

    Used by the test-only ``reference-2`` of T12/M8, which exists to prove
    the plumbing reaches every site.  It must never become a second pinned
    school.
    """

    entry = copy.deepcopy(dict(profile))
    name = str(entry.get("name") or "").strip()
    if not name:
        raise KeyError("a registered assessment profile needs a name")
    _PROFILES[name] = entry
    return copy.deepcopy(entry)


# --------------------------------------------------------------------------- #
# The accessors (spec-step8 T12/M5b) — every reader goes through one of these
# --------------------------------------------------------------------------- #
# ``decide_cells`` deliberately does NOT call ``resolve``: it type-checks the
# Mapping and reads it with ``.get``, and
# ``tests/golden/rne_assessment_candidates.json`` records the profile its run
# executed under as ``{name, appears_in, allow_subjective_rows}``.  That
# golden is one of the eleven §4 forbids moving, so a partial profile that
# carries none of the keys below must still resolve — hence a per-key
# fallback to ``DEFAULT_PROFILE`` rather than a merging ``resolve()``, which
# would additionally disable ``_profile_payload``'s live ``appears_in`` gate.


def _value(profile: Mapping | str | None, key: str):
    if profile is None:
        return DEFAULT_PROFILE[key]
    if isinstance(profile, str):
        profile = get_profile(profile)
    value = profile.get(key)
    return DEFAULT_PROFILE[key] if value is None else value


def sheet_kinds(profile: Mapping | str | None = None) -> tuple[str, ...]:
    """The sheet kinds this profile's papers use."""

    return tuple(_value(profile, "sheet_kinds") or DEFAULT_PROFILE["sheet_kinds"])


def forced_blank_fields(
    profile: Mapping | str | None = None,
) -> tuple[str, ...]:
    """The fields this profile ships blank whatever the row carries."""

    return tuple(_value(profile, "forced_blank_fields"))


def question_source(profile: Mapping | str | None = None) -> str:
    """The ``question_source`` wire value for a candidate declaring none."""

    return str(_value(profile, "question_source"))


def group_status(profile: Mapping | str | None = None) -> str:
    """The ``group_status`` wire value for a group declaring none."""

    return str(_value(profile, "group_status"))


def appears_in(profile: Mapping | str | None = None) -> str:
    """The exact ``question_appears_in`` wire value."""

    return str(_value(profile, "appears_in"))


def _metadata_token(value: Any) -> str:
    """One comparison token for declarative profile metadata aliases.

    Collapsing whitespace and case is wire normalization, not a semantic
    classification.  In particular, this accessor never guesses a board,
    grade, or subject from a title or filename.
    """

    return " ".join(str(value or "").split()).casefold()


def _matches_metadata(
    policy: Mapping[str, Any], metadata: Mapping[str, Any],
) -> bool:
    match = policy.get("metadata_match")
    if not isinstance(match, Mapping) or not match:
        return False
    for field, raw_aliases in match.items():
        actual = _metadata_token(metadata.get(str(field)))
        if not actual:
            return False
        if isinstance(raw_aliases, str):
            aliases = (raw_aliases,)
        else:
            try:
                aliases = tuple(raw_aliases)
            except TypeError:
                aliases = (raw_aliases,)
        if actual not in {_metadata_token(alias) for alias in aliases}:
            return False
    return True


def _same_metadata_selector(
    profile: Mapping | str | None,
    field: str,
    carried: Any,
    explicit: Any,
) -> bool:
    """Whether two wire values select the same declarative profile lane.

    Exact normalized values are always equivalent.  Aliases are equivalent
    only when a profile policy declares them in the same selector set; this
    keeps re-entry generic and avoids encoding board/subject knowledge in the
    resolver.  Empty/null values never equal a populated carried selector.
    """

    carried_token = _metadata_token(carried)
    explicit_token = _metadata_token(explicit)
    if not carried_token or not explicit_token:
        return carried_token == explicit_token
    if carried_token == explicit_token:
        return True

    for collection_key in (
        "run_profile_overrides",
        "assessment_format_overrides",
        "master_workbook_overrides",
    ):
        for candidate in _value(profile, collection_key) or ():
            if not isinstance(candidate, Mapping):
                continue
            match = candidate.get("metadata_match")
            if not isinstance(match, Mapping) or field not in match:
                continue
            raw_aliases = match[field]
            if isinstance(raw_aliases, str):
                aliases = (raw_aliases,)
            else:
                try:
                    aliases = tuple(raw_aliases)
                except TypeError:
                    aliases = (raw_aliases,)
            alias_tokens = {_metadata_token(alias) for alias in aliases}
            if carried_token in alias_tokens and explicit_token in alias_tokens:
                return True
    return False


def master_workbook_contract(
    profile: Mapping | str | None = None,
    *,
    learning_phase: str = "",
) -> dict[str, Any]:
    """Resolve deterministic Master-workbook geometry for one run/lane.

    The run-level base contract is selected by ``resolve_for_metadata`` and
    survives publication inside the persisted profile. Narrow overrides may
    additionally match that carried metadata and the explicitly observed
    learning lane. Concept-file geometry never reads this function.

    Invalid partial values fall back to the pinned reference capacity. A
    smaller capacity cannot remove columns from the committed base layout, so
    the minimum is ten Descriptive answer slots.
    """

    selected = _value(profile, "master_workbook")
    contract = (
        copy.deepcopy(dict(selected))
        if isinstance(selected, Mapping)
        else copy.deepcopy(DEFAULT_MASTER_WORKBOOK_CONTRACT)
    )
    resolved_profile = resolve(profile)
    metadata = resolved_profile.get("_resolved_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    phase = _metadata_token(learning_phase)
    for candidate in _value(profile, "master_workbook_overrides") or ():
        if not isinstance(candidate, Mapping) or not _matches_metadata(
            candidate, metadata,
        ):
            continue
        phases = tuple(
            _metadata_token(value)
            for value in candidate.get("learning_phases") or ()
        )
        if phases and phase not in phases:
            continue
        overrides = candidate.get("overrides")
        if isinstance(overrides, Mapping):
            contract.update(copy.deepcopy(dict(overrides)))
        break

    floor = layouts.UNIVERSAL_DESCRIPTIVE_ANSWER_SLOTS
    try:
        slots = int(contract.get("descriptive_answer_slots", floor))
    except (TypeError, ValueError):
        slots = floor
    # Q27: the universal template capacity is a floor a profile may not
    # narrow (a capacity limit never drops content — contract §14).
    contract["descriptive_answer_slots"] = max(floor, slots)
    contract["include_update_fields"] = bool(
        contract.get("include_update_fields", False)
    )
    contract["include_descriptive_concept_source"] = bool(
        contract.get("include_descriptive_concept_source", False)
    )
    contract["natural_label_aggregates"] = bool(
        contract.get("natural_label_aggregates", False)
    )
    contract["aggregate_rendered_questions_only"] = bool(
        contract.get("aggregate_rendered_questions_only", False)
    )
    return contract


def assessment_format_policy(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the exact assessment-format policy for one run.

    A metadata override replaces the generic format vocabulary; it does not
    merge in categories that the matched board policy does not permit.  When
    no override matches, the generic CMS policy is returned unchanged.
    Matching is exact over the aliases declared by the profile.
    """

    selected = _value(profile, "assessment_format")
    if isinstance(metadata, Mapping):
        # Supplying metadata is an explicit lookup and must not be
        # contaminated by selectors carried from a different run.
        run_metadata = metadata
    else:
        resolved_profile = resolve(profile)
        carried_metadata = resolved_profile.get("_resolved_metadata")
        run_metadata = (
            carried_metadata if isinstance(carried_metadata, Mapping) else {}
        )
    for candidate in _value(profile, "assessment_format_overrides") or ():
        if isinstance(candidate, Mapping) and _matches_metadata(
            candidate, run_metadata
        ):
            selected = candidate
            break
    if not isinstance(selected, Mapping):
        return {}
    policy = copy.deepcopy(dict(selected))
    # The aliases select a policy; they are not part of the authoring
    # contract and need not consume prompt tokens or invite reinterpretation.
    policy.pop("metadata_match", None)
    return policy


def question_formats(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return sheet -> exact category -> mark/duration rules."""

    policy = assessment_format_policy(profile, metadata)
    formats = policy.get("formats_by_sheet")
    if not isinstance(formats, Mapping):
        return {}
    return copy.deepcopy(dict(formats))


def question_categories(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Return the ordered, exact category vocabulary for every sheet."""

    return {
        str(sheet_kind): tuple(str(category) for category in categories)
        for sheet_kind, categories in question_formats(
            profile, metadata
        ).items()
        if isinstance(categories, Mapping)
    }


def question_format_rule(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    sheet_kind: str,
    question_category: str,
) -> dict[str, Any]:
    """Return one category's declarative contract, or an empty mapping."""

    formats = question_formats(profile, metadata)
    sheet = formats.get(str(sheet_kind))
    if not isinstance(sheet, Mapping):
        return {}
    rule = sheet.get(str(question_category))
    return copy.deepcopy(dict(rule)) if isinstance(rule, Mapping) else {}


def question_marks_rule(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    sheet_kind: str,
    question_category: str,
) -> dict[str, Any]:
    """Return one category's mark contract for cell validation."""

    rule = question_format_rule(
        profile,
        metadata,
        sheet_kind=sheet_kind,
        question_category=question_category,
    ).get("marks")
    return copy.deepcopy(dict(rule)) if isinstance(rule, Mapping) else {}


def question_duration_rule(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    sheet_kind: str,
    question_category: str,
) -> dict[str, Any]:
    """Return one category's duration contract for the marking stage."""

    rule = question_format_rule(
        profile,
        metadata,
        sheet_kind=sheet_kind,
        question_category=question_category,
    ).get("duration")
    return copy.deepcopy(dict(rule)) if isinstance(rule, Mapping) else {}


def question_duration_minutes(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    sheet_kind: str,
    question_category: str,
    difficulty: str,
    basis_count: int | None = None,
    marks: int | float | None = None,
) -> float | None:
    """Resolve prescribed minutes without inventing a semantic basis count.

    Matrix policies use the recorded difficulty.  Marks-matrix policies (the
    owner's 2026-08-29 Question Duration Matrix carries categories whose
    minutes depend on the mark tier, e.g. Composition Writing at 5/10/20
    marks) additionally need the recorded marks.  Per-subpoint policies need
    the caller to supply the independently authored, mechanically validated
    subpoint count.  Generic categories deliberately return ``None`` because
    the pre-policy behavior leaves duration to the marking verdict.
    """

    rule = question_duration_rule(
        profile,
        metadata,
        sheet_kind=sheet_kind,
        question_category=question_category,
    )
    mode = str(rule.get("mode") or "")
    if mode == "matrix":
        minutes = rule.get("minutes_by_difficulty")
        if not isinstance(minutes, Mapping):
            return None
        value = minutes.get(str(difficulty))
    elif mode == "marks_matrix":
        tiers = rule.get("minutes_by_marks")
        if isinstance(marks, bool) or not isinstance(marks, (int, float)):
            return None
        if not isinstance(tiers, Mapping) or float(marks) != int(marks):
            return None
        # A policy authored in Python keys tiers by int; one that crossed a
        # JSON boundary keys them by string.  Same table either way.
        tier = tiers.get(int(marks), tiers.get(str(int(marks))))
        if not isinstance(tier, Mapping):
            return None
        value = tier.get(str(difficulty))
    elif mode == "per_subpoint":
        if isinstance(basis_count, bool) or not isinstance(basis_count, int):
            return None
        if basis_count <= 0:
            return None
        per_subpoint = rule.get("minutes_per_subpoint")
        if isinstance(per_subpoint, bool):
            return None
        try:
            value = float(per_subpoint) * basis_count
        except (TypeError, ValueError):
            return None
    else:
        return None
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def automatic_secondary_tags(profile: Mapping | str | None = None) -> bool:
    """Whether this profile permits automatic secondary placements."""

    return bool(_value(profile, "automatic_secondary_tags"))


# --------------------------------------------------------------------------- #
# English rubric-tag containment (contract v2.0 §28, Appendix C)
# --------------------------------------------------------------------------- #
# Controlled bracket tags are REQUIRED on every populated textual rubric
# criterion of an ENGLISH Descriptive item and FORBIDDEN everywhere else —
# in every other field of an English item and in every rubric of every other
# subject.  Whether a run is an English run is read from the run's frozen
# subject metadata through the same alias set the format policies use; it is
# metadata matching, never a judgment about content.  The tag on any given
# criterion is the model's choice from the registry; code only validates the
# syntax and the containment.

ENGLISH_RUBRIC_TAGS: tuple[str, ...] = (
    "content", "evidence", "reasoning", "organisation", "language",
    "creativity", "accuracy",
)


def _subject_is_english(subject: Any) -> bool:
    """Whether the run's frozen subject names English.

    Identity mechanics over the run metadata (never content): the token
    equals a registered alias, or opens with the word ``english`` — so an
    "English L1" or "English Language and Literature" run is English and
    never silently resolves to the tag-free non-English rule (§2).
    """
    token = _metadata_token(subject)
    if token in {_metadata_token(alias) for alias in _ENGLISH_SUBJECT_ALIASES}:
        return True
    return token == "english" or token.startswith("english ")


def rubric_tag_policy(
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The tag containment rule for one run's subject metadata.

    Returns ``{"required": bool, "tags": [...]}``: required (with the
    registry) for an English run, forbidden (empty registry) otherwise.
    Handed to the materialization author and critic as evidence and read by
    the mechanical validators.
    """
    subject = (metadata or {}).get("subject") if isinstance(metadata, Mapping) else None
    required = _subject_is_english(subject)
    return {
        "required": required,
        "tags": list(ENGLISH_RUBRIC_TAGS) if required else [],
        "syntax": "[tag]: <observable credit-bearing criterion>",
    }


def rubric_tags_required(
    profile: Mapping | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Whether the run's Descriptive rubric criteria carry English tags."""
    if isinstance(metadata, Mapping) and _metadata_token(
        metadata.get("subject")
    ):
        return _subject_is_english(metadata.get("subject"))
    resolved = resolve(profile)
    carried = resolved.get("_resolved_metadata")
    if isinstance(carried, Mapping):
        return _subject_is_english(carried.get("subject"))
    return False


def name(profile: Mapping | str | None = None) -> str:
    """The profile's registered name."""

    return str(_value(profile, "name"))
