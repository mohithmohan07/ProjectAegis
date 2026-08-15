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
    # The reference program uses Objective and Descriptive rows only; the
    # Subjective sheet stays header-only.
    "allow_subjective_rows": False,
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
