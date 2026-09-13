"""Versioned, envelope-frozen policy: a paired correction on every analysis item.

Under this policy every Misconception/Error Analysis inventory item (Post
``analyse`` and Pre ``preanalyse``) carries ``correction`` — what is
actually true, or the correct step — beside its ``text``, paired one to
one, and the section renders numbered pairs. Instruction-only: the model
authors the correction, the checker refuses only an empty one, and the
composer numbers declared fields. Frozen on NEW envelopes by
``concept_topology_contract`` exactly like ``prelearning_foundation_policy``;
a sealed envelope without ``KEY`` keeps byte-identical payloads, decision
keys, checker and render (register Q68).
"""
from __future__ import annotations

from typing import Any, Mapping

KEY = "_analysis_correction_policy"
VERSION = "analysis-correction-2026-09-13"

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


def active(value: Mapping[str, Any] | None) -> bool:
    """True when a payload, or an envelope's metadata, names the policy."""
    if not isinstance(value, Mapping):
        return False
    if value.get(KEY) == VERSION:
        return True
    metadata = value.get("metadata")
    return isinstance(metadata, Mapping) and metadata.get(KEY) == VERSION


def fields(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Carry the stamp onto a payload without minting it on historical ones."""
    return {KEY: VERSION} if active(value) else {}


def rules_sentence(value: Mapping[str, Any] | None) -> str:
    """The additive payload-rules sentence; empty when the policy is absent."""
    return AUTHOR_INSTRUCTION + " " if active(value) else ""


def author_instruction(payload: Mapping[str, Any] | None) -> str:
    return "\n" + AUTHOR_INSTRUCTION if active(payload) else ""


def critic_instruction(payload: Mapping[str, Any] | None) -> str:
    return "\n" + CRITIC_INSTRUCTION if active(payload) else ""


def suffix(payload: Mapping[str, Any] | None) -> str:
    """Readable policy marker for the recorded decision's policy_version."""
    return ";" + VERSION if active(payload) else ""
