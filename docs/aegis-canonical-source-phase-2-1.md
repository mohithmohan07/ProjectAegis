# Aegis Canonical Source Document: Phase 2.1

## Purpose

Phase 2.1 hardens the source-critical Build Concepts cutover after the first
production RNE run exposed three classes of source drift that Phase 2 did not
block:

1. a numbered main section could disappear while its `2.1`–`2.4` subsections
   survived;
2. a plain-text task cue could be missed, or a task could swallow glossary and
   narrative text;
3. a nearby Figure could be attached to the wrong task, or a generic subject
   word could trigger an unsafe Figure-number correction.

The immutable Mathpix MMD remains the audit source. Phase 2.1 does not silently
invent missing textbook wording. A source with unresolved structural loss is
blocked before paid Type mining.

## Source gates

Build Concepts is blocked when any of the following is detected:

- numbered subsections whose numbered parent section is missing;
- a gap in an established numbered chapter sequence;
- a plain-text `Discuss`, `Activity`, `Project`, or similar cue that does not map
  to exactly one canonical task;
- glossary, narrative, or Mathpix layout text remaining inside a task prompt;
- a Figure enclosed by a source task boundary but rejected by task/visual
  compatibility, leaving it orphaned;
- the existing Phase 2 QID, identity, order, rich-text, or visual gates.

The source report records these as `phase21_issues`. Generation remains disabled
until the MMD is corrected or reconverted successfully.

## Deterministic repairs

Phase 2.1 may repair only source-preserving representation defects:

- recover a task whose cue and prompt are present as plain text in one block;
- trim text after the first contiguous task prompt;
- separate raw Figure/image ownership from teacher-facing display ownership;
- resolve a Figure contradiction only when one unique nearby creator/person
  surname proves the target, such as `Hübner` matching `Julius Hübner`;
- reject generic subject matches such as `Germania` as Figure-repair evidence;
- attach a referenced table or chart as explicit shared context through block IDs.

A missing question cannot be reconstructed from a caption. An orphan Figure
therefore blocks generation rather than being assigned to the preceding task.

## Type taxonomy contract

A consolidated mined Type is one allocation unit. Its Cases are no longer split
into independent Type-placement units during the Phase 2.1 path. After placement,
the final Type/Case wire format is rebuilt from the mined taxonomy while exact
source Example coverage is held constant.

The final cleanup also removes duplicated `Activity — Activity —` and
`Project — Project —` prefixes and normalises the existing learner-analysis
section contract.

## RNE acceptance

The complete RNE fixture must produce:

- six numbered chapter sections in source order;
- 26 sequential source tasks;
- clean Friedrich List and women’s-rights prompts;
- Fig. 6 attached only to the Club of Thinkers question;
- the Hübner task displayed with Fig. 18 while preserving raw Fig. 17 wording;
- the Frankfurt task preserved with Fig. 10, never rewritten to Fig. 19;
- Box 3 linked as shared context for the Veit Germania task;
- no blocking Phase 2.1 issue.

A production-shaped corruption with the Section 2 heading and Club of Thinkers
question removed must fail before concept generation with section-gap and orphan-
Figure diagnostics.
