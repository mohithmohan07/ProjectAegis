"""Frozen, additive policy for complete and atomic prerequisite capture."""
from __future__ import annotations

from typing import Any, Mapping

from . import prelearning_foundation_policy as foundation
from . import generation_quality_policy as quality
from . import generation_repair_policy as repair

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
    fields.update(repair.fields(env))
    from . import reviewed_file_workflow_policy
    fields.update(reviewed_file_workflow_policy.fields(env))
    return fields


def boundary_instruction(payload: Mapping[str, Any]) -> str:
    suffix = ""
    if payload.get(BOUNDARY_KEY) == BOUNDARY_VERSION:
        suffix += "\n" + BOUNDARY_INSTRUCTION
    return (
        suffix + foundation.instruction(payload)
        + ("\n" + QUALITY_INSTRUCTION if quality.active(payload) else "")
        + repair_instruction(payload)
    )


def repair_instruction(payload: Mapping[str, Any]) -> str:
    return "\n" + REPAIR_INSTRUCTION if repair.active(payload) else ""


def assessment_instruction(payload: Mapping[str, Any]) -> str:
    """Apply accepted-scope guidance only to an explicitly identified Pre lane."""

    if not repair.active(payload):
        return ""
    output_kind = payload.get("output_kind")
    if output_kind == "pre_concepts_release":
        is_pre = True
    else:
        is_pre = False
        metadata = payload.get("metadata")
        for source in (payload, metadata if isinstance(metadata, Mapping) else {}):
            lane = next((source.get(key) for key in (
                "pre_post_learning", "learning_kind", "pre_post",
            ) if source.get(key)), None)
            if lane is not None:
                is_pre = str(lane).strip().lower() in {"pre", "pre-learning", "pre_learning"}
                break
    if not is_pre:
        return ""
    return (
        repair_instruction(payload) + "\n" + repair.PRE_ASSESSMENT_INSTRUCTION
        + "\nCoverage planning belongs to the upstream planner. This pass "
        "performs only its assigned materialization, review or refinement; "
        "it cannot add, drop or reclassify accepted concepts or questions. "
        "Preserve frozen question wording, options, multipart grouping and "
        "the adopted answer contract. A Refiner may edit only its existing "
        "prose whitelist. Record an unrepairable scope concern as advisory "
        "evidence, without restoring superseded prerequisite content."
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


REPAIR_INSTRUCTION = """\
PRE-LEARNING CALIBRATION AND TEACHABLE SCOPE
Apply this clarification to the earlier prior-learning boundary. A separate
curriculum document, an explicit statement 'previously learned', or an exact
prior school year is NOT required to recognise a supported prerequisite. The
source's observable assumptions together with the supplied learner grade and
subject can support an API judgment that a bounded capability is prior learning.
State the source demand, the capability it assumes, and the basis for that
inference. Distinguish inference from supplied fact; never invent a syllabus,
curriculum record, prior chapter or school year. Grade alone and general
usefulness alone are insufficient. Do not reject a source-supported inference
solely because no external curriculum record was supplied.

Distinguish a transferable capability from this chapter's specific application.
For example, reading a familiar graph's labelled axes is different from learning
this chapter's findings, and following sequence, cause or quoted evidence in a
short text is different from analysing this chapter's particular biography.
These are candidates for source-based judgment, never compulsory Pre topics.
Judge whether the source assumes, recalls or newly teaches the capability. A
brief supporting definition or reminder is not automatically new teaching just
because its words occur in the current chapter. Nor is every explained term a
prerequisite. Keep genuinely new chapter knowledge and unsupported extensions
in their appropriate lane, with an explicit disposition when excluded from Pre.

Every retained prerequisite must express concrete knowledge or an observable
capability that can be taught and diagnosed independently. 'Remember having
studied X', 'recognise X as a previously studied topic', and vague 'introductory
study' are not sufficient scope or mastery. Describe the supported fundamental
itself at an appropriate level. Do not reduce an accepted substantive capability
to awareness of a term simply because its exact prior-year attribution is
unknown. Descriptions teach that bounded fundamental; mastery states what the
learner can do with it. Analysis diagnoses learner misunderstanding of that
knowledge, not compliance with a pipeline scope rule. Preserve distinct retained
fundamentals without generic banks, quotas or forced difficulty tiers.

The independent critic checks both unjustified rejection and scope expansion:
whether a claimed chapter-taught disposition confuses application or recap with
new teaching, whether documentary provenance was incorrectly made mandatory,
and whether the retained description and mastery have assessable substance.
Resolve disputes through the existing API authority and review/Fixer stages;
no local classifier or automatic retention follows from these examples. Keep
Grade 1 foundations small, familiar and single-demand under Q44; do not apply
that minimal scope indiscriminately to older learners. An evidence-supported
empty Pre set remains valid.
"""
