# Aegis — working rules

## Rule 0: The Master Governing Contract v2.0 is the output specification

`docs/aegis-master-governing-contract-v2.md` (document ID
AEGIS-MGC-2.0-20260904, adopted 2026-09-04, register entry Q26 in
docs/aegis-restructure.md) is the binding specification for what every run
produces: the four outputs, their schema (update-aware 72/380/149 columns,
every `is_update_*` cell exact `No`, ` | ` list delimiter, `<br>` line
breaks), lane routing (True/False on Subjective, label-free Objective
explanations, identical Descriptive model answers), rubric rules (0.5 or 1
per criterion; English-only bracket tags), identity grammar, KaTeX/asset
rules and the release gates. Where an older register entry, SOP, prompt,
validator or sample disagrees, the contract governs and the conflict is
recorded in the register — never blended, never silently defaulted. The
contract is board-, publication- and grade-agnostic: a board-specific
category/marks/duration profile is an explicit, versioned layer on top of it,
never a precondition for any contract behaviour.

The contract's semantic boundary (§37) and Rule 1 below are the same rule.
Its fail-closed release stance applies to the RELEASE (the database write and
the "done" verdict): a run still completes and stages every artefact with its
blockers named (Q13); what it may not do is publish or call itself done while
a blocker stands.

## Rule 1: No deterministic judgment. Ever.

**Every decision that requires judgment goes through the model. Not a rule, not
a regex, not a threshold, not a keyword list.**

This is not a preference to weigh against others. It is the constraint the
codebase is built on, and it is not negotiable when writing new code here.

Concretely, do NOT introduce:

* regexes or keyword vocabularies that classify content
  (`_HEADING_ONLY_RE`, "is this a cue?", "is this filler?")
* numeric thresholds that decide meaning
  (min chars, min concepts per topic, coverage ratios, "too short to be real")
* volume-derived structure — topic counts, concept counts, or question counts
  scaled from character/token length, chunk count, or page count
* shape-matching heuristics standing in for "what does the book mean here"

Instead: give the model the source evidence and the question, take its verdict,
and — following this codebase's existing pattern — have an **independent second
pass verify** before anything is dropped, merged, or rewritten (its dissent is
an advisory review flag, never a gate — Q10). When the model does not
positively decide mid-run, the block goes to **The Fixer** (§8.2 of
docs/aegis-restructure.md, decided by Q13 amending Q7 in the §12 register):
one recorded, flagged, content-addressed best-judgment decision with the full
context of the block, and the run completes. Nothing is ever guessed
*silently*, nothing is lost, finished work always ships. Only the pre-spend
source-integrity pauses (source review, source-topic recovery, Type
granularity) and genuine impossibility — source unreadable, provider down,
quota exhausted, a decision that cannot be made mechanically applicable — may
stop a run. One recorded extension of that list (Q24, §12 register,
2026-08-29): an unattended run that could settle a rich-text source pending
only with the measured dead-end `carry_forward` fails fast with the named
remedy (reconvert the PDF) instead of spending into a downstream refusal —
the source, as converted, is unusable, which is the impossibility clause
applied honestly. Silently losing a learner's question is never recoverable.

### Why

Textbooks vary too much across boards, subjects, and grades for shape-matching
to read them. Every deterministic shortcut that has gone into this pipeline has
eventually mis-read a real book:

* a bold-vs-heading rule hid every question from generation (24 → 0, PR 208/211)
* `topic_count * MIN_PER_TOPIC` pressured thin lower-grade chapters into
  inventing concepts to satisfy arithmetic
* re-deriving topics from headings loses topics the outline already decided
* "too short to be a real task" cannot tell a mangled question from a
  section banner — only the source can

Lower grades are where this bites hardest: less content per topic, so anything
keyed to volume systematically under-serves exactly the students the chapter is
written for.

### What is still allowed to be deterministic

Mechanics, not meaning: parsing, ID assignment, caching, ordering, atomic
writes, schema validation, and **gates that refuse to accept a broken
artifact**. A strict check that detects a defect is fine — it makes no
judgment about the content, it just declines to guess. But mid-run a detected
defect routes to The Fixer's recorded decision rather than halting (Q13);
a gate may only stop the run outright at the pre-spend pauses or on genuine
impossibility. What must not be deterministic is the decision about what the
source *means*.
