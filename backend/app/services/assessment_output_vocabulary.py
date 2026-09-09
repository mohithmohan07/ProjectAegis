"""Exact presentation labels; no question classification lives here.

The owner clarified on 8 September 2026 that deterministic field values
means the spelling of an already-selected category/group. Model authors
still decide category, difficulty and tier. A run snapshots this small
wire vocabulary so a historical release is never silently renamed.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

VERSION = "assessment-output-vocabulary-2026-09-08-v1"
POLICY_KEY = "_assessment_output_vocabulary"
FORMAT_SNAPSHOT_KEY = "_assessment_format_policy_snapshot"
GROUP_LABELS = ("Basic", "Intermediate", "Advanced")

# Only exact, documented labels are aliases. No stemming, case guessing,
# keyword matching, mark stripping or reading of the question is permitted.
CATEGORY_ALIASES = {
    "Fill in the Blanks": "Fill in the blanks",
    "Assertion & Reasons Type": "Assertion & Reasons",
    "True or False": "True/False",
    "Extract Based Question": "Extract Based Questions",
}


def snapshot() -> dict[str, Any]:
    return {
        "version": VERSION,
        "category_aliases": copy.deepcopy(CATEGORY_ALIASES),
        "group_labels": list(GROUP_LABELS),
        "classification_owner": "model_or_explicit_blueprint",
        "tier_owner": "model",
    }


def category_label(value: Any, policy: Mapping[str, Any] | None) -> str:
    """Rename only an exact declared label; preserve unknowns for validation."""
    text = str(value or "")
    aliases = (policy or {}).get("category_aliases")
    if not isinstance(aliases, Mapping):
        return text
    return str(aliases.get(text, text))


def format_policy(
    original: Mapping[str, Any], vocabulary: Mapping[str, Any],
) -> dict[str, Any]:
    """Rename category keys while retaining their complete mark/time rules."""
    result = copy.deepcopy(dict(original))
    formats = original.get("formats_by_sheet")
    if not isinstance(formats, Mapping):
        return result
    projected: dict[str, Any] = {}
    for sheet, categories in formats.items():
        if not isinstance(categories, Mapping):
            projected[str(sheet)] = copy.deepcopy(categories)
            continue
        canonical: dict[str, Any] = {}
        for label, rule in categories.items():
            target = category_label(label, vocabulary)
            if target in canonical:
                raise ValueError(
                    f"assessment category aliases collide on {sheet!r}: {target!r}"
                )
            canonical[target] = copy.deepcopy(rule)
        projected[str(sheet)] = canonical
    result["formats_by_sheet"] = projected
    result["output_vocabulary_version"] = vocabulary.get("version")
    result["label_contract"] = (
        "Select a category from the exact keys for its sheet. Serialize that "
        "key verbatim; no abbreviation, synonym, capitalization change or "
        "new task-specific category. Category meaning remains an API decision."
    )
    if result.get("policy_id") == "generic-cms":
        result["calibration"] = {
            "mode": "generic_api_calibrated",
            "marks": "API-authored for complete task, grade and response demand",
            "question_duration": "API-authored in minutes for the stated learner grade",
            "scope": (
                "No exact local mark/duration matrix matched. Do not borrow a "
                "sample school's matrix or claim an official local calibration. "
                "Chapter duration follows its separate contract."
            ),
        }
    return result
