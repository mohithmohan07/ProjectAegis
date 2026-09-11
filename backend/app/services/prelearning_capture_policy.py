"""Frozen, additive policy for complete and atomic prerequisite capture."""
from __future__ import annotations

from typing import Any, Mapping

from . import prelearning_foundation_policy as foundation
from . import generation_quality_policy as quality

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
    fields.update(quality.fields(env))
    return fields


def boundary_instruction(payload: Mapping[str, Any]) -> str:
    suffix = ""
    if payload.get(BOUNDARY_KEY) == BOUNDARY_VERSION:
        suffix += "\n" + BOUNDARY_INSTRUCTION
    return (
        suffix + foundation.instruction(payload)
        + ("\n" + QUALITY_INSTRUCTION if quality.active(payload) else "")
    )


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


QUALITY_INSTRUCTION = """\
COMPLETE, BOUNDED PRE-LEARNING COVERAGE
Read the supplied evidence for all necessary prior capabilities, not only the
most prominent concept or the final exercise. Source explanations, worked
examples, individual in-text tasks, representations, tables, diagrams, activity
demands and error evidence may reveal different fundamentals. A source item is
an audit address, not automatically a prerequisite. Decide its actual demand;
retain supported prior learning and explicitly distinguish new chapter teaching,
supplied directions and unsupported assumptions. When two evidence addresses
need the same capability, reuse that capability rather than create two concepts.
Do not infer completeness merely because every existing capture was accounted
for: the source may contain a needed fundamental that no earlier capture named.

Capture, authority and map must preserve every retained independent capability
through descriptions and mastery. The retained_atoms supplied downstream are
the accepted scope of a prerequisite, not optional suggestions. Do not compress
different capabilities into a vague 'basics' label or let a prominent capability
erase its companions. Merge only semantic duplicates, with their evidence
preserved, and retain meaningful distinctions without repeating their teaching.

Question planning and authorship must cover the complete retained prerequisite
scope, including its retained atoms, using the reviewed Pre concept evidence.
Explain in the coverage-plan rationale which capability each planned check
diagnoses, and in each question rationale which retained capability it verifies.
The critic checks for unassessed retained capabilities as well as repetition,
inflated scope and hidden demands in answers or rubrics. Master stages preserve
this accepted diagnostic scope and do not reselect or import source questions.

Completeness is not a target count or a demand for extra complexity. Keep Grade
1 readiness small and simple where the source supports that boundary; do not
apply that minimal scope to older learners. Preserve the existing prior-learning
eligibility boundary at every grade. An evidence-supported empty Pre set is
valid. No current-chapter teaching, generic bank or extra tier is added to make
an output look fuller. All semantic choices remain with the API and its
independent advisory critic; preserve existing review and Fixer stages.
"""
