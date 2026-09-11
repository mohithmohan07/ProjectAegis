"""Owner-approved output values; no question classification lives here.

The 11 September CMS attachment is a closed output vocabulary. Model authors
still decide which approved category and cognitive skill fit the evidence.
The older 8 September projection remains available through frozen v1 policies;
new decisions are never repaired by aliases or a locally guessed category.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

VERSION = "assessment-output-vocabulary-2026-09-11-v2"
LEGACY_VERSION = "assessment-output-vocabulary-2026-09-08-v1"
POLICY_KEY = "_assessment_output_vocabulary"
FORMAT_SNAPSHOT_KEY = "_assessment_format_policy_snapshot"
GROUP_LABELS = ("Basic", "Intermediate", "Advanced")

# Exact visible labels in the owner's attached workbook, in source order.
# Only two attachment-cell boundary artefacts were removed: the trailing tab
# in Question Categories!A15 and trailing space in Question Categories!A26.
QUESTION_CATEGORIES = (
    "Match the following Questions",
    "Case Based Questions",
    "Composition Writing",
    "Assertion & Reasons Type",
    "Sentence Transformation",
    "Extract based on Map Survey",
    "Numerical/application based",
    "Long Answer Type (5 Marks)",
    "Locating and Plotting on map",
    "Choose the ODD one Out",
    "Passage based questions",
    "Long Answer Type (4 Marks)",
    "Short Answer Type (2 Marks)",
    "True or False",
    "Error correction",
    "Identifying the following",
    "Fill in the Blanks",
    "Rearrange the following words",
    "Extract based question",
    "Long Answer Type (6 Marks)",
    "Short Answer Type (3 Marks)",
    "Multiple Choice Question",
    "Very Short Answer Questions",
    "Name the following",
    "Read the Explanations and give structure",
    "Reading Comprehension",
)
QUESTION_SOURCES = (
    "UpSchool DB", "Selina", "NCERT", "NON - NCERT", "Oswaal",
    "K State (Extra)", "Seed to Plant", "Balbharati", "RS Aggarwal",
)
COGNITIVE_SKILLS = (
    "Remember", "Understand", "Apply", "Analyse", "Evaluate", "Create",
)

# These adapt existing FORMAT CONFIGURATION keys only. They are never used on
# a model verdict or workbook cell. In particular, bare Long Answer and Short
# Answer do not imply any of the owner's mark-specific category choices.
FORMAT_CATEGORY_ALIASES = {
    "Match the Following": "Match the following Questions",
    "Assertion & Reasons": "Assertion & Reasons Type",
    "Locating and Plotting on Map": "Locating and Plotting on map",
    "Passage Based Questions": "Passage based questions",
    "True/False": "True or False",
    "Error Correction": "Error correction",
    "Fill in the blanks": "Fill in the Blanks",
    "Extract Based Question": "Extract based question",
    "Extract Based Questions": "Extract based question",
    "Very Short Answer": "Very Short Answer Questions",
}

# Literal contracts named by the approved labels, not a marks-to-category
# classifier. The API chooses the category first; its declared marks follow.
CATEGORY_FIXED_MARKS = {
    "Short Answer Type (2 Marks)": 2,
    "Short Answer Type (3 Marks)": 3,
    "Long Answer Type (4 Marks)": 4,
    "Long Answer Type (5 Marks)": 5,
    "Long Answer Type (6 Marks)": 6,
}

# Only exact, documented labels are aliases. No stemming, case guessing,
# keyword matching, mark stripping or reading of the question is permitted.
CATEGORY_ALIASES = {
    "Fill in the Blanks": "Fill in the blanks",
    "Assertion & Reasons Type": "Assertion & Reasons",
    "True or False": "True/False",
    "Extract Based Question": "Extract Based Questions",
}


def legacy_snapshot() -> dict[str, Any]:
    """Read historical v1 workbooks without adopting the new generation rule."""
    return {
        "version": LEGACY_VERSION,
        "category_aliases": copy.deepcopy(CATEGORY_ALIASES),
        "group_labels": list(GROUP_LABELS),
        "classification_owner": "model_or_explicit_blueprint",
        "tier_owner": "model",
    }


def snapshot() -> dict[str, Any]:
    return {
        "version": VERSION,
        "question_categories": list(QUESTION_CATEGORIES),
        "question_sources": list(QUESTION_SOURCES),
        "cognitive_skills": list(COGNITIVE_SKILLS),
        "category_aliases": {},
        "format_category_aliases": copy.deepcopy(FORMAT_CATEGORY_ALIASES),
        "category_fixed_marks": copy.deepcopy(CATEGORY_FIXED_MARKS),
        "group_labels": list(GROUP_LABELS),
        "classification_owner": "model_or_explicit_blueprint",
        "tier_owner": "model",
        "source_provenance": {
            "title": "CMS clean-up: Finalised Question Categories_ Sources_ Cognitive Skills",
            "ranges": {
                "question_categories": "Question Categories!A2:A27",
                "question_sources": "Question Sources!A2:A10",
                "cognitive_skills": "Cognitive Skills!A2:A7",
            },
            "boundary_whitespace_only": {
                "Question Categories!A15": "removed trailing tab",
                "Question Categories!A26": "removed trailing space",
            },
        },
        "label_contract": (
            "Only the exact approved question_categories, question_sources and "
            "cognitive_skills values may appear in generated output fields. "
            "No synonyms, abbreviations, capitalization changes, combined "
            "values or new labels. Models choose meaning from complete "
            "evidence; invalid values require recorded API repair."
        ),
    }


def is_current(policy: Mapping[str, Any] | None) -> bool:
    """Whether a vocabulary snapshot carries the closed CMS contract."""
    return isinstance(policy, Mapping) and policy.get("version") == VERSION


def policy_errors(policy: Mapping[str, Any] | None) -> list[str]:
    """Refuse altered known-version registries without adopting their values.

    The version identifies the owner's exact immutable contract, not an
    invitation for an incoming profile to add labels or rewrite prompt rules.
    Historical versions retain their recorded policies and are not upgraded.
    """
    if not is_current(policy):
        return []
    expected = snapshot()
    errors = [
        f"output_vocabulary policy field {key!r} does not match the "
        f"authoritative {VERSION} snapshot"
        for key, value in expected.items()
        if policy.get(key) != value
    ]
    unexpected = set(policy) - set(expected)
    if unexpected:
        errors.append(
            "output_vocabulary policy has undeclared fields: "
            + ", ".join(sorted(str(key) for key in unexpected))
        )
    return errors


def require_valid_policy(policy: Mapping[str, Any] | None) -> None:
    """Validate the frozen contract before exposing it to any API author."""
    errors = policy_errors(policy)
    if errors:
        raise ValueError("; ".join(errors))


def instruction(policy: Mapping[str, Any] | None) -> str:
    """Give authors, reviewers and recovery the same versioned field rule."""
    if not is_current(policy):
        return ""
    require_valid_policy(policy)
    return (
        "OWNER-APPROVED CMS OUTPUT VOCABULARY. "
        + str(policy.get("label_contract") or "")
        + " This restriction applies to metadata fields, not to words in the "
        "question, source evidence, answers or explanations. Select category "
        "and cognitive skill from the complete task evidence under the "
        "existing response-mechanism policy. Never infer a label from "
        "typography, length, neighbouring questions, or a local fallback. "
        "The approved source label must truthfully reflect the recorded "
        "provenance; an unavailable label is not permission to invent or "
        "misattribute a source. Exact allowed question_categories: "
        + repr(list(policy.get("question_categories", ())))
        + ". Exact allowed question_sources: "
        + repr(list(policy.get("question_sources", ())))
        + ". Exact allowed cognitive_skills: "
        + repr(list(policy.get("cognitive_skills", ())))
        + "."
    )


def field_errors(
    record: Mapping[str, Any], policy: Mapping[str, Any] | None,
    *, include_source: bool = False,
) -> list[str]:
    """Check exact populated-question fields, without correcting their meaning.

    Callers identify actual question/blueprint rows; this deliberately does not
    recurse into children that may inherit metadata from their parent.
    """
    if not is_current(policy):
        return []
    errors = policy_errors(policy)
    authoritative_values = {
        "question_categories": QUESTION_CATEGORIES,
        "question_sources": QUESTION_SOURCES,
        "cognitive_skills": COGNITIVE_SKILLS,
    }
    fields = [("question_category", "question_categories")]
    skill_fields = [
        field for field in ("cognitive_skill", "cognitive_skills")
        if field in record
    ] or ["cognitive_skill"]
    fields.extend((field, "cognitive_skills") for field in skill_fields)
    if include_source:
        fields.append(("question_source", "question_sources"))
    for field, vocabulary_key in fields:
        allowed = authoritative_values[vocabulary_key]
        value = record.get(field)
        if not isinstance(value, str) or value not in allowed:
            errors.append(
                f"{field} must be one exact approved value from "
                f"{tuple(allowed)} (got {value!r})"
            )
    if (
        record.get("question_category") == "True or False"
        and "sheet_kind" in record
        and record.get("sheet_kind") != "subjective"
    ):
        errors.append("question_category 'True or False' requires sheet_kind 'subjective'")
    return errors


def category_label(value: Any, policy: Mapping[str, Any] | None) -> str:
    """Preserve new decisions exactly; replay historical declared aliases."""
    text = str(value or "")
    if is_current(policy):
        return text
    aliases = (policy or {}).get("category_aliases")
    if not isinstance(aliases, Mapping):
        return text
    return str(aliases.get(text, text))


def format_policy(
    original: Mapping[str, Any], vocabulary: Mapping[str, Any],
) -> dict[str, Any]:
    """Project configuration keys while retaining their complete time rules."""
    if is_current(vocabulary):
        return _approved_format_policy(original, vocabulary)
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


def _approved_format_policy(
    original: Mapping[str, Any], vocabulary: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose the closed catalogue without a local question/lane classifier.

    The response-mechanism author and critic choose the actual sheet and
    category from evidence. Generic profiles expose the complete approved
    catalogue except the explicit True/False lane rule. Explicit local format
    restrictions remain an intersection with that catalogue. Existing
    calibration stays attached to its exact sheet and category.
    """
    require_valid_policy(vocabulary)
    result = copy.deepcopy(dict(original))
    categories = QUESTION_CATEGORIES
    aliases = FORMAT_CATEGORY_ALIASES
    fixed_marks = CATEGORY_FIXED_MARKS
    formats = original.get("formats_by_sheet", {})
    generic = original.get("policy_id") == "generic-cms"
    projected: dict[str, Any] = {}
    excluded = copy.deepcopy(original.get("excluded_configuration_labels", []))
    sheets = ("objective", "subjective", "descriptive") if generic else tuple(formats)
    for sheet in sheets:
        configured = formats.get(sheet, {}) if isinstance(formats, Mapping) else {}
        retained: dict[str, Any] = {}
        if isinstance(configured, Mapping):
            for label, rule in configured.items():
                target = aliases.get(label, label)
                if target not in categories or (
                    target == "True or False" and sheet != "subjective"
                ):
                    excluded.append({
                        "sheet_kind": sheet, "label": label,
                        "rule": copy.deepcopy(rule),
                    })
                    continue
                if target in retained and retained[target] != rule:
                    raise ValueError(
                        f"assessment format aliases carry conflicting rules on {sheet!r}: {target!r}"
                    )
                retained[target] = copy.deepcopy(rule)
        sheet_formats: dict[str, Any] = {}
        for category in categories if generic else retained:
            if category == "True or False" and sheet != "subjective":
                continue
            rule = copy.deepcopy(retained.get(category, {}))
            if generic and category in fixed_marks:
                # The owner-defined literal mark total is a schema contract.
                # Existing matching matrices remain unchanged; an explicit
                # incompatible profile cannot silently change that total.
                required = {"mode": "fixed", "allowed": (fixed_marks[category],)}
                marks_rule = rule.get("marks")
                if marks_rule and (
                    marks_rule.get("mode") != "fixed"
                    or tuple(marks_rule.get("allowed", ())) != required["allowed"]
                ):
                    raise ValueError(
                        f"assessment format marks conflict with approved label {category!r} on {sheet!r}"
                    )
                rule["marks"] = required
            sheet_formats[category] = rule
        if not generic and configured and not sheet_formats:
            raise ValueError(
                "assessment format configuration has no approved categories "
                f"remaining on {sheet!r} for {original.get('policy_id')!r}"
            )
        projected[sheet] = sheet_formats
    if not generic and not any(projected.values()):
        raise ValueError(
            "assessment format configuration has no approved categories "
            f"for {original.get('policy_id')!r}"
        )
    result["formats_by_sheet"] = projected
    result["output_vocabulary_version"] = vocabulary["version"]
    result["label_contract"] = vocabulary["label_contract"]
    result["response_mechanism_contract"] = (
        "The API chooses sheet_kind from the complete response mechanism and "
        "then a compatible exact approved category. A category's availability "
        "does not classify the task or authorize changing its response demand. "
        "True or False remains Subjective. Retain existing per-category mark "
        "and duration rules; use API calibration when no duration rule exists."
    )
    result["excluded_configuration_labels"] = excluded
    result["calibration"] = {
        "mode": "retained_exact_rules_else_api_calibrated",
        "marks": "Use the exact declared category contract when present; otherwise API-authored for the complete task.",
        "question_duration": "Use the exact retained duration rule when present; otherwise API-authored for the learner grade.",
        "scope": "Approved category availability does not imply an official local mark/duration matrix.",
    }
    return result
