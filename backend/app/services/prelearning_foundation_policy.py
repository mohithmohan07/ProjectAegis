"""Versioned, bounded guidance for fresh Pre-learning foundations.

The policy is deliberately instruction-only.  The supplied source demand and
the sealed chapter metadata remain the authority for whether a capability is
relevant and plausibly prior learning; this module does not classify text or
grade names in Python.
"""
from __future__ import annotations

from typing import Any, Mapping


KEY = "_prelearning_foundation_policy"
VERSION = "prelearn-foundations-2026-09-10"


INSTRUCTION = """\
PRE-LEARNING FOUNDATION BOUNDARY — FRESH POLICY
Use the supplied source, its complete teaching demands and attached visuals,
together with the chapter's board, grade, subject, unit and title metadata.
The model decides whether a capability is both needed for this chapter and
plausibly learned before it; Python code must not infer that decision from a
grade name, wording pattern, keyword, position or count.

For a Grade 1 chapter, a narrow literacy readiness capability may be eligible
when the source actually requires it and the supplied grade context makes it
plausibly previously learned: familiar letters or words, familiar or everyday
vocabulary, basic punctuation and capitalisation such as an initial capital,
or a sentence-ending full stop or question mark where the source expects that
recognition or use.  For these limited Grade 1 readiness
foundations, an explicit earlier-grade curriculum record is not required; use
the source and grade context to judge plausibility without fabricating a
history.  These are permitted examples, not mandatory categories or a
required trio.  Keep only what this chapter genuinely needs.  A chapter may
have no eligible foundations at all; an empty Pre result is valid and must
never be padded or filled from a generic grade-wide bank.  Do not impose these
Grade 1 literacy examples on mathematics or higher grades; their own source
and metadata determine eligibility.  The minimal Grade 1 literacy guidance
below must not reduce the appropriate prerequisite depth of another grade
or impose language concepts on another subject.

For Grade 1 literacy, keep this boundary at simple readiness, without formal
grammar terminology or technical analysis.  At every grade, a new phonics or
language lesson, a newly taught action-word lesson, and the chapter's story
or other newly taught content must not become prior learning.  New chapter
vocabulary and current teaching stay in Post.  A simple recognition or one
short source demand can establish the
relevant readiness; do not add unnecessary explanation, formal terminology or
extended production merely to make a foundation look complete.  A short
assessment may ask the learner to recognise or choose the correct alternative
to test a retained foundation; that response form does not itself create a
new concept.  Answers, answer-space decisions, marking criteria and
refinement must preserve the accepted question's demand rather than expand it.

Apply the same eligibility boundary to capture, atomic authority, Pre concept
grouping, descriptions, mastery, learner analysis, needed-for links and
generated Pre assessments.  Master stages intentionally receive the reviewed
Pre concepts as their prerequisite scope rather than the original chapter
source or curriculum text: do not reselect concepts, invent missing context,
or expand that retained scope; flag a concern when the supplied concept is
insufficient.  Preserve uncertain or unsupported candidates in
the audit and dispose of them explicitly with their evidence; uncertainty is
not permission to ship extra Pre teaching.  Keep a generated Grade 1
assessment to one short demand and response.  For that learner, technical
grammar, an extended why, or hidden explanation may not be added by a rubric
or model answer; an
oversized frozen question is flagged for review and is never rewritten by the
Master.  No tier balance or question count may force extra coverage.  If the
capture is empty, the empty-capture audit must use this same source-and-grade
test: return assumes_nothing when the source supports no eligible prior
foundation, with no Pre question-author spend.  Return capture_incomplete only
when the source specifically evidences an eligible prerequisite the capture
missed, or the source is unusable for this decision; uncertainty about prior
learning alone or a generic ability needed to follow a worksheet direction is
not enough.
"""


def fields(env: Mapping[str, Any]) -> dict[str, str]:
    """Return the policy stamp only for an envelope carrying this version."""

    metadata = env.get("metadata") if isinstance(env, Mapping) else None
    if isinstance(metadata, Mapping) and metadata.get(KEY) == VERSION:
        return {KEY: VERSION}
    return {}


def instruction(payload: Mapping[str, Any]) -> str:
    """Return the policy instruction only for a payload carrying its stamp."""

    if isinstance(payload, Mapping) and payload.get(KEY) == VERSION:
        return "\n" + INSTRUCTION
    return ""


__all__ = ["KEY", "VERSION", "INSTRUCTION", "fields", "instruction"]
