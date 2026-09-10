"""Frozen, additive policy for complete and atomic prerequisite capture."""
from __future__ import annotations

from typing import Any, Mapping

from . import prelearning_foundation_policy as foundation

KEY = "_prelearning_capture_policy"
VERSION = "prelearn-evidence-atomic-2026-09-09"

# Additive owner amendment: do not change Q37's recorded payloads or keys.
BOUNDARY_KEY = "_prelearning_boundary_policy"
BOUNDARY_VERSION = "prelearn-prior-only-2026-09-09"
BOUNDARY_INSTRUCTION = (
    "Owner amendment Q39: retain only knowledge genuinely necessary BEFORE "
    "the chapter and supported as previously acquired at the learner's "
    "earlier grade/year level. In each retained prerequisite's rationale, "
    "identify the source demand, why the learner needs this capability, "
    "and the evidence or supplied curriculum context supporting prior "
    "learning. Mere usefulness, subject association, chapter position, "
    "familiarity to an adult, or a desired concept count is insufficient. "
    "Do not invent curriculum history. New vocabulary explicitly taught "
    "here, current story events/characters, and concepts introduced by an "
    "opening scene belong to Post. For example, Frederic Sorrieu's opening "
    "vision in The Rise of Nationalism in Europe is substantive chapter "
    "teaching, not automatically prior knowledge because it opens the text. "
    "A genuine recap may evidence prior learning; judge its teaching "
    "function, never its Introduction/Summary banner. Preserve all source "
    "content in its appropriate Post concept even when it is ineligible "
    "for Pre. Capture and merge may retain uncertain candidates as evidence; "
    "the existing final authority must explicitly dispose unsupported "
    "candidates as insufficient_evidence, current teaching as chapter_taught, "
    "and unnecessary additions as not_required_for_this_chapter. Preserve "
    "every atom and its reason in the audit; uncertainty is not permission "
    "to ship extra Pre teaching. Distinguish a supplied tick/write/number "
    "instruction from an already learned capability needed to perform it. "
    "Do not add generic procedure concepts for understanding worksheet "
    "directions, or manufacture prerequisites for a first-year learner. "
    "For Pre map, description, mastery, analysis and generated questions, "
    "stay within the retained prerequisite's exact scope and prior level; "
    "do not enrich it with new chapter content, future-grade extensions, "
    "or extra demands to fill the fixed question coverage. Keep necessary "
    "distinct fundamentals even when familiar. The independent critic "
    "checks necessity, prior-learning evidence, omitted fundamentals and "
    "leakage explicitly. Topic names describe the retained capability, "
    "never generic Introduction, Summary or Exercises containers."
)


def boundary_fields(env: Mapping[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    if (env.get("metadata") or {}).get(BOUNDARY_KEY) == BOUNDARY_VERSION:
        fields[BOUNDARY_KEY] = BOUNDARY_VERSION
    fields.update(foundation.fields(env))
    return fields


def boundary_instruction(payload: Mapping[str, Any]) -> str:
    suffix = ""
    if payload.get(BOUNDARY_KEY) == BOUNDARY_VERSION:
        suffix += "\n" + BOUNDARY_INSTRUCTION
    return suffix + foundation.instruction(payload)


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
