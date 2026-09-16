"""Versioned, envelope-frozen paired corrections and concept coverage.

Under this policy every Misconception/Error Analysis inventory item (Post
``analyse`` and Pre ``preanalyse``) carries ``correction`` — what is
actually true, or the correct step — beside its ``text``, paired one to
one, and the section renders numbered pairs. Instruction-only: the model
authors the correction, the checker refuses only an empty one, and the
composer numbers declared fields. Frozen on NEW envelopes by
``concept_topology_contract`` exactly like ``prelearning_foundation_policy``;
a sealed envelope without ``KEY`` keeps byte-identical payloads, decision
keys, checker and render (register Q68).

V2 additionally authors source-grounded pairs for ordinary concepts the
chapter inventory did not cover. V1 retains its original sparse inventory,
payloads, decision keys and author/critic instructions.
"""
from __future__ import annotations

from typing import Any, Mapping

KEY = "_analysis_correction_policy"
VERSION_V1 = "analysis-correction-2026-09-13"
VERSION = "analysis-correction-2026-09-16-v2"
SUPPORTED_VERSIONS = frozenset({VERSION_V1, VERSION})

AUTHOR_INSTRUCTION = (
    "CORRECTION POLICY (named in this request): every item also carries "
    "\"correction\". text is the incorrect belief or the faulty step ITSELF "
    "— the false claim or wrong move stated in its own words (no actor "
    "prefix is required); correction is one or two sentences stating what "
    "is actually true, or the correct step, grounded on the same evidence "
    "the item cites. Every item carries both halves, paired one to one; a "
    "correction is never a bare negation or restatement of its text, and "
    "carries no 'Correction:' label of its own. Under this policy the stated "
    "response schema for this request is {\"items\": [{\"item_id\", \"kind\": "
    "\"misconception|error_analysis\", \"text\", \"correction\", \"evidence\", "
    "\"rationale\"}]}. Do not number an item's text or correction yourself — "
    "the output numbers them — and do not write a section label such as "
    "'Misconceptions:', 'Error Analysis:', 'Common mistake:' or 'Possible "
    "error:' inside either half; state the belief, the step, or what is true "
    "directly."
)
CRITIC_INSTRUCTION = (
    "CORRECTION POLICY (named in this request): also flag a correction that "
    "is untrue, unsupported by the cited evidence, or merely negates or "
    "restates its item's text, and any item whose correction is missing."
)


COVERAGE_AUTHOR_INSTRUCTION = (
    "Complete the named concept's misconception coverage using the full "
    "evidence and settled teaching supplied. This policy requires a meaningful "
    "misconception with a paired correction for every ordinary concept; "
    "culmination rows are not targets. The earlier chapter inventory is kept, "
    "and this request addresses a concept it did not cover. Author the "
    "incorrect belief a learner could plausibly hold about THIS concept and "
    "the accurate explanation that resolves it. Judge plausibility, grounding, "
    "distinctness and the useful amount of content from the source, never "
    "generic filler, a canned misconception or a false claim about what the "
    "textbook says. A misconception need not be printed explicitly in the "
    "source: its correction must follow from the supplied teaching. Preserve "
    "the concept's grade and learning scope; a simple concept needs a simple, "
    "specific pair, not advanced terminology or extra teaching. In Pre, use "
    "only the captured prerequisite and Pre teaching, never introduce the "
    "current chapter's new teaching. Read the existing analysis to avoid "
    "repeating its pairs. Return JSON {\"items\": [{\"kind\": "
    "\"misconception|error_analysis\", \"text\": \"incorrect belief or step\", "
    "\"correction\": \"what is true and why\", \"evidence\": "
    "\"specific supplied evidence references\", \"rationale\": "
    "\"why this pair is plausible and belongs to this concept\"}]}. "
    "Include a misconception; additional distinct items are your judgment. "
    "Do not supply item IDs, numbering or section labels: these are composed "
    "after this recorded decision. Every field is required and non-empty."
)
COVERAGE_CRITIC_INSTRUCTION = (
    "Independently review the proposed misconception coverage for the named "
    "concept against ALL the supplied evidence and existing analysis. Flag "
    "an implausible misconception, generic or copied filler, a correction "
    "that merely negates/restates its belief, unsupported or inaccurate "
    "teaching, an item belonging to another concept, or complexity outside "
    "the supplied grade and Pre/Post scope. The requirement is a meaningful "
    "misconception with a paired correction on each ordinary concept. "
    "Preserve the author's judgment in the response and report disagreement "
    "as advisory issues, never silently rewrite an item. Return JSON with "
    "verdict (verified|revise), confidence, and issues (an array of strings)."
)


def version(value: Mapping[str, Any] | None) -> str:
    """Read the recorded stamp; never upgrade a sealed v1 envelope."""
    if not isinstance(value, Mapping):
        return ""
    stamp = value.get(KEY)
    if isinstance(stamp, str) and stamp in SUPPORTED_VERSIONS:
        return stamp
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        stamp = metadata.get(KEY)
        if isinstance(stamp, str) and stamp in SUPPORTED_VERSIONS:
            return stamp
    return ""


def active(value: Mapping[str, Any] | None) -> bool:
    """Both policy versions require paired corrections."""
    return bool(version(value))


def covers_every_concept(value: Mapping[str, Any] | None) -> bool:
    """Only newly frozen v2 envelopes require complete concept coverage."""
    return version(value) == VERSION


def fields(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Carry the stamp onto a payload without minting it on historical ones."""
    recorded = version(value)
    return {KEY: recorded} if recorded else {}


def rules_sentence(value: Mapping[str, Any] | None) -> str:
    """The additive payload-rules sentence; empty when the policy is absent."""
    return AUTHOR_INSTRUCTION + " " if active(value) else ""


def author_instruction(payload: Mapping[str, Any] | None) -> str:
    return "\n" + AUTHOR_INSTRUCTION if active(payload) else ""


def critic_instruction(payload: Mapping[str, Any] | None) -> str:
    return "\n" + CRITIC_INSTRUCTION if active(payload) else ""


def suffix(payload: Mapping[str, Any] | None) -> str:
    """Readable policy marker for the recorded decision's policy_version."""
    recorded = version(payload)
    return ";" + recorded if recorded else ""
