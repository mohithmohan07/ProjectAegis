# Aegis — working rules

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
