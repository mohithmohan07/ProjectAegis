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

from typing import Mapping

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
    # The wire value this school's Master rows carry in ``question_source``
    # when a candidate declares none.
    "question_source": "",
    # The wire value this school's Master rows carry in ``group_status``
    # when a group declares none.
    "group_status": "Active",
    # Automatic secondary QuestionTag placements are off; a future profile
    # may enable explicit, audited secondaries.
    "automatic_secondary_tags": False,
}

_PROFILES: dict[str, dict] = {
    DEFAULT_PROFILE["name"]: DEFAULT_PROFILE,
}


def get_profile(name: str | None = None) -> dict:
    if name is None:
        return dict(DEFAULT_PROFILE)
    profile = _PROFILES.get(name)
    if profile is None:
        raise KeyError(f"unknown assessment profile {name!r}")
    return dict(profile)


def resolve(profile: Mapping | str | None) -> dict:
    """Accept a profile dict, a registered name, or None (default)."""
    if profile is None:
        return dict(DEFAULT_PROFILE)
    if isinstance(profile, str):
        return get_profile(profile)
    return dict(profile)


def register(profile: Mapping) -> dict:
    """Register one profile under its own name and return it.

    Used by the test-only ``reference-2`` of T12/M8, which exists to prove
    the plumbing reaches every site.  It must never become a second pinned
    school.
    """

    entry = dict(profile)
    name = str(entry.get("name") or "").strip()
    if not name:
        raise KeyError("a registered assessment profile needs a name")
    _PROFILES[name] = entry
    return dict(entry)


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


def automatic_secondary_tags(profile: Mapping | str | None = None) -> bool:
    """Whether this profile permits automatic secondary placements."""

    return bool(_value(profile, "automatic_secondary_tags"))


def name(profile: Mapping | str | None = None) -> str:
    """The profile's registered name."""

    return str(_value(profile, "name"))
