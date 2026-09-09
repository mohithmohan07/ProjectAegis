"""Frozen, additive policy for complete and atomic prerequisite capture."""
from __future__ import annotations

from typing import Any, Mapping

KEY = "_prelearning_capture_policy"
VERSION = "prelearn-evidence-atomic-2026-09-09"


def active(env: Mapping[str, Any]) -> bool:
    """An unstamped historical envelope retains its original decisions."""
    return (env.get("metadata") or {}).get(KEY) == VERSION


CAPTURE_INSTRUCTION = (
    "Read every supplied teaching demand, complete task, shared context and "
    "attached figure for knowledge it assumes. Capture separately each "
    "independently teachable and diagnosable fundamental; two capabilities "
    "taught in one lesson need not be one concept. Distinguish a supplied "
    "response instruction from a cognitive skill needed to follow it. Keep "
    "the earlier-grade/year eligibility boundary and record uncertainty. "
    "Do not omit a necessary fundamental merely because it is familiar, "
    "and do not add unnecessary basics or a target number of items. The "
    "critic must explicitly identify source demands whose necessary "
    "fundamentals are absent and cite their evidence IDs."
)

MAP_INSTRUCTION = (
    "Group only fundamentals forming one independently teachable and "
    "diagnosable capability. Sharing a lesson, subject, vocabulary or "
    "downstream use alone is not a reason to merge distinct capabilities. "
    "The critic checks that each retained prerequisite survives with its "
    "full scope and independently assessable mastery; no quota or padding."
)
