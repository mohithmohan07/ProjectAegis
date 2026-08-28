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


_GENERIC_QUESTION_FORMATS: dict[str, dict[str, dict[str, Any]]] = {
    "objective": {
        "Multiple Choice Question": {},
        "Assertion & Reasons": {},
        "True/False": {},
        "Fill in the Blanks": {},
    },
    "subjective": {
        "Fill in the Blanks": {},
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
_ENGLISH_SUBJECT_ALIASES = (
    "english", "english language", "english literature",
)


# Workbook geometry is a run-profile fact, just like the set of enabled
# sheets.  The default stays on the committed MES reference layout.  The
# 2026-08-27 MSBSHSE Grade-6 audit records a distinct Master layout: each
# entity band has an ``is_update_*`` column and Descriptive restores the
# concept-source column.  One English Post workbook additionally needs 30
# Descriptive rubric slots.  Those are declarative capabilities selected by
# metadata + lane; no renderer branch names a subject or chapter.
DEFAULT_MASTER_WORKBOOK_CONTRACT: dict[str, Any] = {
    "contract_id": "reference-master-1",
    "include_update_fields": False,
    "include_descriptive_concept_source": False,
    "descriptive_answer_slots": 10,
    "natural_label_aggregates": False,
    "aggregate_rendered_questions_only": False,
}

MSBSHSE_GRADE_6_MASTER_WORKBOOK_CONTRACT: dict[str, Any] = {
    "contract_id": "msbshse-grade-6-master-2026-08-27",
    "include_update_fields": True,
    "include_descriptive_concept_source": True,
    "descriptive_answer_slots": 10,
    "natural_label_aggregates": True,
    "aggregate_rendered_questions_only": True,
}

MSBSHSE_GRADE_6_MASTER_WORKBOOK_OVERRIDES: tuple[dict[str, Any], ...] = (
    {
        "metadata_match": {
            "board": _MSBSHSE_BOARD_ALIASES,
            "grade": _GRADE_6_ALIASES,
            "subject": _ENGLISH_SUBJECT_ALIASES,
        },
        "learning_phases": ("post",),
        "overrides": {
            "contract_id": (
                "msbshse-grade-6-english-post-master-2026-08-27"
            ),
            "descriptive_answer_slots": 30,
        },
    },
)


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
        "subject": ("math", "maths", "mathematics"),
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


# The English audit supplies only its exact authoring taxonomy.  Empty rule
# mappings are deliberate: unlike Mathematics, the evidence does not define
# an English marks or duration contract, so later stages remain free to use
# their existing authored/marking values without inventing restrictions.
MSBSHSE_GRADE_6_ENGLISH_FORMAT_POLICY: dict[str, Any] = {
    "policy_id": "msbshse-grade-6-english-2026-08-27",
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "grade": _GRADE_6_ALIASES,
        "subject": _ENGLISH_SUBJECT_ALIASES,
    },
    "formats_by_sheet": {
        "objective": {
            "Multiple Choice Question": {},
        },
        "subjective": {
            "Fill in the Blanks": {},
        },
        "descriptive": {
            "Very Short Answer Questions": {},
            "Short Answer Type (2 Marks)": {},
            "Short Answer Type (3 Marks)": {},
            "Long Answer Type (4 Marks)": {},
            "Composition Writing": {},
        },
    },
}

MSBSHSE_GRADE_6_RUN_PROFILE_OVERRIDE: dict[str, Any] = {
    "metadata_match": {
        "board": _MSBSHSE_BOARD_ALIASES,
        "grade": _GRADE_6_ALIASES,
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
    # through ``sheet_kinds`` below.
    "sheet_kinds": ("objective", "descriptive"),
    # Fields that ship blank in this school's workbooks regardless of what
    # upstream rows carry (spec-step8 T12/M4; Q5/§6 settles the layout, the
    # profile settles the fill practice).  Read ONLY through
    # ``forced_blank_fields`` below.
    "forced_blank_fields": ("chapter_duration", "question_disclaimer"),
    # The wire value Master rows carry in ``question_source`` when a
    # candidate declares none. T10-7 item 5 (S11): it names the ORIGIN
    # SYSTEM, not a school — [measured] every gold row carries
    # "UpSchool DB" — so the origin-system default lives here rather than
    # being an empty string a declared-none candidate turns into a blank
    # learner-visible cell.
    "question_source": "UpSchool DB",
    # The wire value this school's Master rows carry in ``group_status``
    # when a group declares none.
    "group_status": "Active",
    # Output-role geometry. Concept files always use the committed reference
    # schema; only Master rendering reads this contract.
    "master_workbook": DEFAULT_MASTER_WORKBOOK_CONTRACT,
    "master_workbook_overrides": (
        MSBSHSE_GRADE_6_MASTER_WORKBOOK_OVERRIDES
    ),
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
    "assessment_format_overrides": (
        MSBSHSE_GRADE_6_MATHEMATICS_FORMAT_POLICY,
        MSBSHSE_GRADE_6_ENGLISH_FORMAT_POLICY,
    ),
    # Program metadata can select a complete run-level widening without
    # changing the pinned reference-1 defaults or reinterpreting historical
    # partial profile records. The Grade-6 MSBSHSE source set uses the
    # Subjective sheet and carries its authored chapter duration.
    "run_profile_overrides": (MSBSHSE_GRADE_6_RUN_PROFILE_OVERRIDE,),
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

    try:
        slots = int(contract.get("descriptive_answer_slots", 10))
    except (TypeError, ValueError):
        slots = 10
    contract["descriptive_answer_slots"] = max(10, slots)
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
) -> float | None:
    """Resolve prescribed minutes without inventing a semantic basis count.

    Matrix policies use the recorded difficulty.  Per-subpoint policies need
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


def name(profile: Mapping | str | None = None) -> str:
    """The profile's registered name."""

    return str(_value(profile, "name"))
