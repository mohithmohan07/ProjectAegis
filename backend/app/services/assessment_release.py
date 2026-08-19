"""Release-domain mechanics for MES assessment releases.

The MES specification (§5, §13, §14) builds everything around an immutable
``AssessmentRelease``: the Concept File and the Master File are projections
of one frozen release object, never of mutable database rows.

Everything in this module is deterministic *mechanics* the spec explicitly
allows local code to enforce (§4): schemas and enums, exact IDs and hashes,
exact-once primary placement, group-identity uniqueness, state transitions,
and the zero-loss invariant. Nothing here makes a pedagogical decision —
semantic authorship and acceptance belong to the API author/critic stages.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from . import assessment_profile

# --------------------------------------------------------------------------- #
# Release state machine (spec §5.6)
# --------------------------------------------------------------------------- #

RELEASE_STATES = (
    "draft",
    "materialized",
    "validated_with_flags",
    "ready_for_upload",
    "publication_pending",
    "uploaded",
    "superseded",
)

# A release only ever moves forward; ``superseded`` is reachable from every
# non-final state because a new version can replace an old one at any point.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"materialized", "superseded"}),
    "materialized": frozenset(
        {"validated_with_flags", "ready_for_upload", "superseded"}),
    # Released-with-warnings stays uploadable: only a BLOCKED readiness
    # refuses the database (spec §13.3); flags ride along visibly.
    "validated_with_flags": frozenset(
        {"ready_for_upload", "publication_pending", "superseded"}),
    "ready_for_upload": frozenset({"publication_pending", "superseded"}),
    "publication_pending": frozenset(
        {"uploaded", "ready_for_upload", "superseded"}),
    "uploaded": frozenset({"superseded"}),
    "superseded": frozenset(),
}


class ReleaseStateError(ValueError):
    """An illegal release-state transition was requested."""


def advance_state(current: str, target: str) -> str:
    """Validate one state transition; returns the target on success."""
    if current not in _ALLOWED_TRANSITIONS:
        raise ReleaseStateError(f"unknown release state {current!r}")
    if target not in RELEASE_STATES:
        raise ReleaseStateError(f"unknown release state {target!r}")
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ReleaseStateError(
            f"illegal release transition {current!r} -> {target!r}")
    return target


# --------------------------------------------------------------------------- #
# Content hashing
# --------------------------------------------------------------------------- #

def canonical_json(value: Any) -> str:
    """Stable JSON serialization for hashing (sorted keys, no whitespace)."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_release_uid() -> str:
    return f"REL-{uuid.uuid4().hex[:20]}"


# --------------------------------------------------------------------------- #
# Record schemas (spec §5.1–§5.5) — presence/enum validation only
# --------------------------------------------------------------------------- #

GROUP_TYPES = ("Basic", "Intermediate", "Advanced")

# The reference workbooks mark the correct Objective option "Yes" (wrong
# options "No"); older internal data used "1"/"true". All are accepted as
# the correct-option marker; nothing else is.
_CORRECT_OPTION_MARKERS = frozenset({"1", "true", "yes"})


def is_correct_option(value) -> bool:
    return str(value or "").strip().lower() in _CORRECT_OPTION_MARKERS
# ``SHEET_KINDS`` used to be re-declared here as "the default profile's
# sheet kinds, for sites with no profile in hand". [measured] it had ZERO
# readers under ``app/`` and ``tests/`` — a declaration with no reader is
# what C10's key-with-no-reader pin exists to catch, so it is gone.
# ``assessment_profile.sheet_kinds(profile)`` is the one owner (T12/M5b).
ANSWER_RESTRICTIONS = ("Open", "Specific")

_SOURCE_ATOM_REQUIRED = (
    "source_qid", "source_document_hash", "source_kind", "raw_text",
)
_BLUEPRINT_CELL_REQUIRED = (
    "cell_id", "sheet_kind", "question_category", "cognitive_skill",
    "difficulty", "marks", "count", "appears_in", "source_policy",
)
# ``source_atom_ids`` is deliberately NOT in this tuple.  ``_missing``
# tests ``str(record.get(field)).strip() == ""``, and ``str([])`` is
# ``"[]"`` — non-empty — so an empty list passed this check by string
# coercion rather than by design.  A generated candidate legitimately
# carries ``[]`` (it reuses no source question), and resting that lane on
# a coercion accident means a future tightening of ``_missing`` breaks it
# silently.  The field is therefore checked explicitly below, where the
# rule is written down: a list of non-empty ids, empty only when the
# candidate declares ``source_policy == "generate"``.
_CANDIDATE_REQUIRED = (
    "candidate_id", "blueprint_cell_id", "question",
    "question_text", "sheet_kind", "question_category", "cognitive_skill",
    "difficulty", "marks", "question_duration", "answer_restriction",
    "restriction_reason",
)

# The one value of a candidate's ``source_policy`` that permits an empty
# ``source_atom_ids``. Read here and in ``assessment_release_run``'s
# generated lane from this single owner so the two can never drift.
GENERATED_SOURCE_POLICY = "generate"
_PLACEMENT_REQUIRED = (
    "candidate_id", "concept_id", "group_key", "evidence",
)
_GROUP_REQUIRED = (
    "group_key", "concept_id", "group_type", "group_sequence", "group_name",
    "group_display_name",
)


def _missing(record: Mapping, required: Iterable[str]) -> list[str]:
    return [
        field for field in required
        if str(record.get(field) if record.get(field) is not None else "")
        .strip() == ""
        and record.get(field) not in (0, 0.0)
    ]


def validate_source_atom(atom: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(atom, _SOURCE_ATOM_REQUIRED)]
    for asset in atom.get("assets") or []:
        if not str(asset.get("url") or "").startswith("https://"):
            errors.append(
                f"asset without HTTPS url on {atom.get('source_qid')}")
        if not str(asset.get("alt") or "").strip():
            errors.append(f"asset without alt on {atom.get('source_qid')}")
    return errors


def validate_blueprint_cell(
    cell: Mapping, profile: Mapping | str | None = None,
) -> list[str]:
    errors = [
        f"missing {f}" for f in _missing(cell, _BLUEPRINT_CELL_REQUIRED)
    ]
    # The allowed set is the PROFILE's, not a school baked into a message
    # (spec-step8 T12/M6): what a school's papers use is a profile fact.
    allowed = assessment_profile.sheet_kinds(profile)
    if cell.get("sheet_kind") not in allowed:
        errors.append(
            f"sheet_kind must be one of {allowed} "
            f"(got {cell.get('sheet_kind')!r})")
    return errors


def validate_candidate(
    candidate: Mapping, profile: Mapping | str | None = None,
) -> list[str]:
    errors = [f"missing {f}" for f in _missing(candidate, _CANDIDATE_REQUIRED)]
    # Explicit, deliberate, and independent of ``_missing``'s string
    # coercion (see ``_CANDIDATE_REQUIRED``).  Identity accounting only:
    # it reads no meaning out of the ids, it just states which lane may
    # carry none.
    atom_ids = candidate.get("source_atom_ids")
    if not isinstance(atom_ids, list):
        errors.append("source_atom_ids must be a list")
    elif any(not str(value or "").strip() for value in atom_ids):
        errors.append("source_atom_ids must not contain an empty id")
    elif not atom_ids and str(
        candidate.get("source_policy") or ""
    ) != GENERATED_SOURCE_POLICY:
        errors.append(
            "source_atom_ids may be empty only on a candidate that declares "
            f"source_policy {GENERATED_SOURCE_POLICY!r}"
        )
    kind = candidate.get("sheet_kind")
    allowed_kinds = assessment_profile.sheet_kinds(profile)
    if kind not in allowed_kinds:
        errors.append(
            f"sheet_kind must be one of {allowed_kinds} "
            f"(got {kind!r})")
    restriction = candidate.get("answer_restriction")
    if restriction not in ANSWER_RESTRICTIONS:
        # Never silently default an unknown restriction (spec §3.5).
        errors.append(
            f"answer_restriction must be one of {ANSWER_RESTRICTIONS} "
            f"(got {restriction!r})")
    if not str(candidate.get("restriction_reason") or "").strip():
        errors.append("restriction_reason must be non-empty")
    if candidate.get("question") != candidate.get("question_text"):
        errors.append("question must equal question_text")

    def finite(value: Any) -> Decimal | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text or text.startswith(("+", "-")):
                return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not number.is_finite():
            return None
        try:
            wire_number = float(number)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(wire_number):
            return None
        if number != 0 and wire_number == 0:
            return None
        if Decimal(str(wire_number)) != number:
            return None
        return number

    marks = finite(candidate.get("marks"))
    if marks is None or marks <= 0:
        errors.append("marks must be finite and positive")
        marks = Decimal(0)
    duration = finite(candidate.get("question_duration"))
    if duration is None or duration <= 0:
        errors.append("question_duration must be finite and positive")

    raw_answers = candidate.get("answers")
    if not isinstance(raw_answers, list):
        errors.append("answers must be an array")
        answers: list[Mapping] = []
    else:
        answers = []
        for position, answer in enumerate(raw_answers, start=1):
            if not isinstance(answer, Mapping):
                errors.append(f"answer {position} is not an object")
            else:
                answers.append(answer)
    raw_subquestions = candidate.get("sub_questions")
    if not isinstance(raw_subquestions, list):
        errors.append("sub_questions must be an array")
        subquestions: list[Mapping] = []
    else:
        subquestions = []
        for position, subquestion in enumerate(raw_subquestions, start=1):
            if not isinstance(subquestion, Mapping):
                errors.append(f"subquestion {position} is not an object")
            else:
                subquestions.append(subquestion)

    keyboard = candidate.get("math_keyboard")
    if kind == "objective":
        if keyboard != "":
            errors.append("objective math_keyboard must be exactly blank")
        correct = [
            answer for answer in answers
            if is_correct_option(answer.get("correct_answer"))
        ]
        if len(correct) != 1:
            errors.append(
                f"objective requires exactly one correct option (got "
                f"{len(correct)})"
            )
        for position, answer in enumerate(answers, start=1):
            weight = finite(answer.get("answer_weightage"))
            if weight is None:
                errors.append(
                    f"objective answer {position} weightage must be finite"
                )
                continue
            expected = marks if answer in correct else Decimal(0)
            if weight != expected:
                errors.append(
                    f"objective answer {position} weightage {weight:g} != "
                    f"{expected:g}"
                )
        if subquestions:
            errors.append("objective candidate must not have subquestions")
    elif kind == "descriptive":
        if keyboard not in {"Yes", "No"}:
            errors.append("descriptive math_keyboard must be exactly Yes or No")
        if not answers:
            errors.append("descriptive candidate has no rubric blocks")
        weights: list[Decimal] = []
        for position, answer in enumerate(answers, start=1):
            weight = finite(answer.get("answer_weightage"))
            if weight is None or weight <= 0:
                errors.append(
                    f"descriptive answer {position} weightage must be "
                    "finite and positive"
                )
            else:
                weights.append(weight)
        weight_sum = sum(weights, Decimal(0))
        if len(weights) == len(answers) and weight_sum != marks:
            errors.append(
                f"descriptive answer weightage sum {weight_sum:g} != "
                f"marks {marks:g}"
            )
        if subquestions:
            sub_marks: list[Decimal] = []
            for position, subquestion in enumerate(subquestions, start=1):
                sub_mark = finite(subquestion.get("marks"))
                if sub_mark is None or sub_mark <= 0:
                    errors.append(
                        f"subquestion {position} marks must be finite and "
                        "positive"
                    )
                    continue
                sub_marks.append(sub_mark)
                keywords = subquestion.get("keywords")
                if not isinstance(keywords, list):
                    errors.append(
                        f"subquestion {position} keywords must be an array"
                    )
                    continue
                if not keywords:
                    # Keyword slots are optional wire capacity.  When the
                    # semantic rubric uses them, every weight is mandatory
                    # and must sum to the subquestion marks below.
                    continue
                keyword_weights: list[Decimal] = []
                for keyword_position, keyword in enumerate(
                    keywords, start=1
                ):
                    if not isinstance(keyword, Mapping):
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} is not an object"
                        )
                        continue
                    weight = finite(keyword.get("weightage"))
                    if weight is None or weight <= 0:
                        errors.append(
                            f"subquestion {position} keyword "
                            f"{keyword_position} weightage must be finite "
                            "and positive"
                        )
                    else:
                        keyword_weights.append(weight)
                if (
                    len(keyword_weights) == len(keywords)
                    and sum(keyword_weights, Decimal(0)) != sub_mark
                ):
                    errors.append(
                        f"subquestion {position} keyword weightage sum "
                        f"{sum(keyword_weights, Decimal(0)):g} != "
                        "subquestion marks "
                        f"{sub_mark:g}"
                    )
            if (
                len(sub_marks) == len(subquestions)
                and sum(sub_marks, Decimal(0)) != marks
            ):
                errors.append(
                    f"subquestion marks sum {sum(sub_marks, Decimal(0)):g} "
                    "!= marks "
                    f"{marks:g}"
                )
    return errors


def validate_placement(
    placement: Mapping, profile: Mapping | str | None = None,
) -> list[str]:
    errors = [f"missing {f}" for f in _missing(placement, _PLACEMENT_REQUIRED)]
    secondary = placement.get("secondary_placements")
    # Whether automatic secondary placements exist is the profile's EXISTING
    # ``automatic_secondary_tags`` key (spec-step8 T12/M6) — not a second key,
    # and not a school name in a message.
    if secondary and not assessment_profile.automatic_secondary_tags(profile):
        errors.append(
            "secondary_placements must be empty under profile "
            f"{assessment_profile.name(profile)!r} (got {len(secondary)})")
    return errors


def validate_group(group: Mapping) -> list[str]:
    errors = [f"missing {f}" for f in _missing(group, _GROUP_REQUIRED)]
    if group.get("group_type") not in GROUP_TYPES:
        errors.append(
            f"group_type must be one of {GROUP_TYPES} "
            f"(got {group.get('group_type')!r})")
    return errors


# --------------------------------------------------------------------------- #
# Identity and placement invariants (spec §3.7, §5.5, §7.5)
# --------------------------------------------------------------------------- #

def group_identity(group: Mapping) -> tuple:
    """The uniqueness unit: (concept_id, group_type, group_key)."""
    return (
        group.get("concept_id"),
        str(group.get("group_type") or ""),
        str(group.get("group_key") or ""),
    )


def duplicate_group_identities(groups: Iterable[Mapping]) -> list[tuple]:
    seen: set[tuple] = set()
    duplicates: list[tuple] = []
    for group in groups:
        identity = group_identity(group)
        if identity in seen and identity not in duplicates:
            duplicates.append(identity)
        seen.add(identity)
    return duplicates


def duplicate_group_keys(groups: Iterable[Mapping]) -> list[str]:
    """Return repeated internal keys, regardless of concept or tier.

    ``group_key`` is the release-wide machine identity.  Reusing one key for
    a different home is structural corruption, not a semantic concern that a
    later judgment pass may accept with a flag.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for group in groups:
        group_key = str(group.get("group_key") or "")
        if not group_key:
            continue
        if group_key in seen and group_key not in duplicates:
            duplicates.append(group_key)
        seen.add(group_key)
    return duplicates


# --------------------------------------------------------------------------- #
# The staged home/shape verdict (spec-step8 T7.5, D8.5b)
# --------------------------------------------------------------------------- #
# A candidate the renderer cannot write to a data row is decided at STAGING,
# recorded once on the release, refused at the publication act by machinery
# that already exists, and merely REPORTED by the renderer.
#
# Why it cannot live in the renderer: ``render_master_file`` runs at
# PROJECTION time, inside ``publish_release``, after the payload is frozen —
# so a discovery made there reaches no readiness computation, and a raise
# made there costs every row on every sheet of all four outputs.  These
# codes are computed here instead, from the four inputs the renderer uses,
# and ride ``freeze_payload``'s existing ``errors`` transport into
# ``diagnostics["payload_errors"]`` → ``_readiness`` BLOCKED → the upload
# refusal.  One vocabulary, one transport, one refusal.
#
# It counts identities and compares key sets against literal tuples.  It
# judges nothing about content, so it is a gate under CLAUDE.md's "what is
# still allowed to be deterministic", not a judgment under Rule 1.

UNRESOLVED_QUESTION_HOME = "unresolved_question_home"
GROUP_HOME_DISAGREEMENT = "group_home_disagreement"
SHEET_KIND_NOT_RENDERABLE = "sheet_kind_not_renderable"
GROUP_CONCEPT_HOME_UNKNOWN = "group_concept_home_unknown"
GROUP_HOME_UNNAMED = "group_home_unnamed"
GROUP_VISIBLE_NAME_MISMATCH = "group_visible_name_mismatch"
RENDER_SHAPE_OVERFLOW = "render_shape_overflow"

QUESTION_HOME_CODES = (
    UNRESOLVED_QUESTION_HOME,
    GROUP_HOME_DISAGREEMENT,
    SHEET_KIND_NOT_RENDERABLE,
    GROUP_CONCEPT_HOME_UNKNOWN,
    GROUP_HOME_UNNAMED,
    GROUP_VISIBLE_NAME_MISMATCH,
    RENDER_SHAPE_OVERFLOW,
)

# Two GROUP-identity codes for the renderer's evidence ledger
# (``issues["group_defects"]``). They are deliberately NOT question-home
# codes and NOT in ``QUESTION_HOME_CODES``: a blank or duplicate
# ``group_key`` is refused at freeze by ``validate_group`` /
# ``duplicate_group_keys`` and again by ``validate_master_file``'s
# read-back, so nothing here decides anything. They exist because the
# renderer used to tag both with ``UNRESOLVED_QUESTION_HOME`` — a
# candidate-level code on a group-level defect, which put the wrong
# vocabulary in front of a reviewer reading ``manifest.json``.
GROUP_KEY_BLANK = "group_key_blank"
GROUP_KEY_DUPLICATE = "group_key_duplicate"

GROUP_LEDGER_CODES = (
    GROUP_KEY_BLANK,
    GROUP_KEY_DUPLICATE,
    GROUP_CONCEPT_HOME_UNKNOWN,
)


def _finding(code: str, message: str, **fields: Any) -> dict:
    finding = {"code": code, "message": message}
    finding.update({k: v for k, v in fields.items()})
    return finding


def candidate_home_finding(
    candidate: Mapping,
    concept_keys,
    groups_by_key: Mapping,
    renderable,
    profile_name: str = "",
) -> dict | None:
    """THE decision "can this candidate be written to a data row".

    ONE implementation, two callers: ``unresolved_question_homes`` below
    (which stages it, so readiness and the upload refusal see it) and
    ``assessment_workbook.render_master_file`` (which records the same
    verdict in the issues manifest's unplaced ledger as the evidence
    channel).  They used
    to be two inline copies of the same three branches reading the same
    four inputs — and they had already drifted: the staged copy compared
    against ``RENDERABLE_SHEET_KINDS`` while the renderer compared against
    ``sheet_for_kind()``, which is that tuple INTERSECTED with the layout's
    sheet-name map.  [measured] under a layout carrying no Descriptive
    sheet the renderer dropped every descriptive candidate while the staged
    verdict returned nothing, so readiness read ``ready`` and the database
    write proceeded with those questions in no data row.  That is the R4
    hole this slice exists to close, one layer up, and two implementations
    of one decision is what T1 exists to end.

    ``renderable`` is the caller's own renderable-kind set, so the drift
    cannot come back: the renderer passes ``tuple(sheet_for_kind())`` and
    the staging pass passes the same.

    Returns one finding dict, or ``None`` when the candidate can be placed.
    It compares key sets and membership only — a gate under CLAUDE.md's
    "mechanics, not meaning", never a judgment about content.
    """

    candidate_id = str(candidate.get("candidate_id") or "")
    label = str(candidate.get("question_label") or "")
    concept_key = str(candidate.get("concept_key") or "")
    group_key = str(candidate.get("group_key") or "")
    identity = label or candidate_id or "candidate"
    named = {"candidate_id": candidate_id, "question_label": label}

    if (
        concept_key not in concept_keys
        or group_key not in groups_by_key
        or not label
    ):
        return _finding(
            UNRESOLVED_QUESTION_HOME,
            f"{identity}: unresolved home concept/group placement "
            f"(concept_key {concept_key!r}, group_key {group_key!r})",
            concept_key=concept_key, group_key=group_key, **named)

    group_concept_key = str(groups_by_key[group_key].get("concept_key") or "")
    if group_concept_key != concept_key:
        return _finding(
            GROUP_HOME_DISAGREEMENT,
            f"{identity}: concept_key {concept_key!r} does not match "
            f"group {group_key!r} home {group_concept_key!r}",
            concept_key=concept_key, group_key=group_key, **named)

    kind = str(candidate.get("sheet_kind") or "")
    if kind not in renderable:
        return _finding(
            SHEET_KIND_NOT_RENDERABLE,
            f"{identity}: sheet_kind {kind!r} is outside the kinds this "
            f"step renders {tuple(renderable)}",
            sheet_kind=kind, profile=profile_name,
            renderable_sheet_kinds=list(renderable), **named)
    return None


def unresolved_question_homes(
    snapshot: Mapping, profile: Mapping | str | None = None,
) -> list[dict]:
    """Every home/shape defect the Master renderer used to raise or drop.

    A PURE function of the frozen snapshot's ``candidates``, ``groups`` and
    ``concept_key`` set plus the profile's allowed sheet kinds — the same
    four inputs ``assessment_workbook.render_master_file`` reads, so it can
    decide at STAGING what the renderer used to discover at projection time.

    Returns a list of ``{"code", "message", …identity}`` findings.  The
    caller appends them to the frozen payload's ``errors``.
    """

    # Local imports: ``assessment_workbook`` and ``assessment_grouping``
    # both import this module, so the renderable set, the layout's own
    # column caps and the learner-visible group-name composition are read
    # here rather than declared twice.  Both edges are one-way and taken at
    # call time.
    from ..bulk_import import assessment_workbook as workbook
    from . import assessment_grouping as grouping

    findings: list[dict] = []

    concept_names: dict[str, str] = {}
    for topic in snapshot.get("topics") or []:
        for concept in topic.get("concepts") or []:
            concept_names[str(concept.get("concept_key") or "")] = str(
                concept.get("concept_display_name") or ""
            ).strip()

    groups_by_key: dict[str, Mapping] = {}
    for group in snapshot.get("groups") or []:
        group_key = str(group.get("group_key") or "")
        if not group_key or group_key in groups_by_key:
            # Blank and duplicate ``group_key`` are already refused at
            # freeze by ``validate_group``/``duplicate_group_keys`` and
            # again by ``validate_master_file``'s read-back; naming them a
            # third time here would be a third vocabulary for one defect.
            continue
        groups_by_key[group_key] = group

    for group_key, group in groups_by_key.items():
        concept_key = str(group.get("concept_key") or "")
        if concept_key not in concept_names:
            findings.append(_finding(
                GROUP_CONCEPT_HOME_UNKNOWN,
                f"group {group_key!r} has unknown concept home "
                f"{concept_key!r}",
                group_key=group_key, concept_key=concept_key))
            continue
        concept_name = concept_names[concept_key]
        if not concept_name:
            findings.append(_finding(
                GROUP_HOME_UNNAMED,
                f"group {group_key!r} home {concept_key!r} has no explicit "
                "concept_display_name",
                group_key=group_key, concept_key=concept_key))
            continue
        tier = str(group.get("group_type") or "")
        if tier not in GROUP_TYPES or tier not in grouping.TIER_CODES:
            # ``validate_group`` already refuses this at freeze.
            continue
        # ONE owner for the learner-visible composition.  It used to be
        # spelled inline here as ``f"{concept_name} — {tier}"`` while
        # ``assessment_grouping.friendly_group_name`` composed the value
        # that is actually written — so a change to the separator would
        # have falsely flagged every group of every release and blocked
        # every one of them.  The two guards above are exactly the two
        # conditions ``friendly_group_name`` raises on, so this call cannot
        # raise and the function stays pure.
        visible_name = grouping.friendly_group_name(concept_name, tier)
        if (
            str(group.get("group_name") or "") != visible_name
            or str(group.get("group_display_name") or "") != visible_name
        ):
            findings.append(_finding(
                GROUP_VISIBLE_NAME_MISMATCH,
                f"group {group_key!r} visible names must both equal "
                f"{visible_name!r}",
                group_key=group_key, concept_key=concept_key))

    allowed_kinds = assessment_profile.sheet_kinds(profile)
    # ``sheet_for_kind()``, NOT ``RENDERABLE_SHEET_KINDS``: the renderer
    # places a candidate iff ``sheet_for_kind()`` names a sheet for its
    # kind, and that map is the constant INTERSECTED with the layout's own
    # sheet-name map.  Reading the un-intersected constant here was a
    # second source for one question — see ``candidate_home_finding``.
    renderable = tuple(workbook.sheet_for_kind())
    profile_name = assessment_profile.name(profile)
    caps = (
        ("answers", workbook.MAX_OBJECTIVE_OPTIONS, ("objective",)),
        ("answers", workbook.MAX_DESCRIPTIVE_ANSWERS, ("descriptive",)),
        ("sub_questions", workbook.MAX_SUBQUESTIONS, ("descriptive",)),
    )

    for candidate in snapshot.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        label = str(candidate.get("question_label") or "")
        identity = label or candidate_id or "candidate"
        home = candidate_home_finding(
            candidate, concept_names, groups_by_key, renderable, profile_name)
        if home is not None:
            if (
                home["code"] == SHEET_KIND_NOT_RENDERABLE
                and home["sheet_kind"] not in allowed_kinds
            ):
                # A kind the PROFILE does not allow at all is already
                # refused at freeze by ``validate_candidate``. Naming it
                # here as well would be two vocabularies for one defect —
                # the negative control the reference profile pins. The
                # renderer still records it in the issues manifest's
                # unplaced ledger, so nothing about it is silent.
                continue
            findings.append(home)
            continue
        kind = str(candidate.get("sheet_kind") or "")
        for field, cap, kinds in caps:
            if kind not in kinds:
                continue
            actual = len([
                item for item in candidate.get(field) or []
                if isinstance(item, Mapping)
            ])
            if actual > cap:
                findings.append(_finding(
                    RENDER_SHAPE_OVERFLOW,
                    f"{identity}: {actual} {field} exceeds the layout's "
                    f"{cap} slots",
                    candidate_id=candidate_id, question_label=label,
                    field=field, cap=cap, actual=actual))
        if kind == "descriptive":
            for n, sub in enumerate(candidate.get("sub_questions") or [], 1):
                if not isinstance(sub, Mapping):
                    continue
                actual = len([
                    k for k in sub.get("keywords") or []
                    if isinstance(k, Mapping)
                ])
                if actual > workbook.MAX_SUBQUESTION_KEYWORDS:
                    findings.append(_finding(
                        RENDER_SHAPE_OVERFLOW,
                        f"{identity}: subquestion {n} has {actual} keyword "
                        f"slots, the layout carries "
                        f"{workbook.MAX_SUBQUESTION_KEYWORDS}",
                        candidate_id=candidate_id, question_label=label,
                        field=f"sub_questions[{n}].keywords",
                        cap=workbook.MAX_SUBQUESTION_KEYWORDS, actual=actual))
    return findings


def duplicate_question_labels(candidates: Iterable[Mapping]) -> list[str]:
    """Return repeated question labels inside one release.

    ``question_label`` is the learner-facing machine identity of a question.
    Reusing one label for a different question is structural corruption, not a
    semantic concern that a later judgment pass may accept with a flag: the
    database write has exactly one row per label, so the second question does
    not get stored differently — it does not get stored at all, and a learner
    question that vanishes with no record is the one loss R4 forbids.

    Seen HERE, at freeze, the collision reaches ``_readiness`` as a payload
    error, the release goes BLOCKED, every download still ships, and only the
    database write is refused (T9's principle; the same transport
    ``duplicate_group_keys`` already rides).

    Blank labels are refused one at a time at the upload, by name; they are not
    collisions with each other and are skipped here.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for candidate in candidates:
        label = str(candidate.get("question_label") or "")
        if not label:
            continue
        if label in seen and label not in duplicates:
            duplicates.append(label)
        seen.add(label)
    return duplicates


def primary_placement_errors(placements: Iterable[Mapping]) -> list[str]:
    """Exactly one home concept and group per question (spec §3.7)."""
    errors: list[str] = []
    seen: dict[str, tuple] = {}
    for placement in placements:
        candidate_id = str(placement.get("candidate_id") or "")
        home = (
            placement.get("concept_id"),
            str(placement.get("group_key") or ""),
        )
        if candidate_id in seen:
            errors.append(
                f"{candidate_id}: more than one primary placement "
                f"({seen[candidate_id]} and {home})")
            continue
        seen[candidate_id] = home
    return errors


# --------------------------------------------------------------------------- #
# Zero-loss invariant (spec §14)
# --------------------------------------------------------------------------- #

def zero_loss_report(
    obligations: Iterable[str],
    accepted: Iterable[str],
    flagged: Iterable[str],
) -> dict:
    """source obligations = accepted rows + explicitly flagged rows.

    No QID, subpart, OR branch, or blueprint cell may disappear. Returns the
    exact identities that break the invariant so a defect is named, never
    hidden.
    """
    obligation_set = {str(o) for o in obligations if str(o).strip()}
    accepted_set = {str(a) for a in accepted if str(a).strip()}
    flagged_set = {str(f) for f in flagged if str(f).strip()}
    covered = accepted_set | flagged_set
    return {
        "missing": sorted(obligation_set - covered),
        "unexpected": sorted(covered - obligation_set),
        "double_counted": sorted(accepted_set & flagged_set),
        "holds": (
            obligation_set == covered and not (accepted_set & flagged_set)
        ),
    }


# --------------------------------------------------------------------------- #
# Release construction
# --------------------------------------------------------------------------- #

def freeze_payload(
    payload: Mapping, profile: Mapping | str | None = None,
) -> dict:
    """Validate and hash a complete release payload.

    Returns {"errors": [...], "hashes": {...}}. Mechanical gates only: any
    semantic concern lives in the payload's own flags, which are preserved,
    never resolved here.
    """
    errors: list[str] = []
    for atom in payload.get("source_atoms") or []:
        errors.extend(validate_source_atom(atom))
    for cell in payload.get("blueprint_cells") or []:
        errors.extend(validate_blueprint_cell(cell, profile))
    for candidate in payload.get("candidates") or []:
        errors.extend(validate_candidate(candidate, profile))
    for group in payload.get("groups") or []:
        errors.extend(validate_group(group))
    for placement in payload.get("placements") or []:
        errors.extend(validate_placement(placement, profile))
    for identity in duplicate_group_identities(payload.get("groups") or []):
        errors.append(f"duplicate group identity {identity}")
    for group_key in duplicate_group_keys(payload.get("groups") or []):
        errors.append(f"duplicate group_key {group_key!r}")
    for label in duplicate_question_labels(payload.get("candidates") or []):
        errors.append(f"duplicate question_label {label!r}")
    errors.extend(
        primary_placement_errors(payload.get("placements") or []))
    return {
        "errors": errors,
        "hashes": {
            "payload": sha256_json(payload),
            "source_atoms": sha256_json(payload.get("source_atoms") or []),
            "blueprint_cells": sha256_json(
                payload.get("blueprint_cells") or []),
            "candidates": sha256_json(payload.get("candidates") or []),
            "groups": sha256_json(payload.get("groups") or []),
            "placements": sha256_json(payload.get("placements") or []),
        },
    }
