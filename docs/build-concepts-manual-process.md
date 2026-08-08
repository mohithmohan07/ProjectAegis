# The Build Concepts Process — As Done By Hand

Authoritative statement of the Build Concepts pipeline's intended behaviour,
as specified by Mohith Mohan (Founder, UpSchool) for Project Aegis. It is the
process an expert would follow doing the job manually, and the pipeline's job
is to mirror it. These are product decisions, not engineering preferences.

Where this document and an implementation disagree, this document is correct
and the implementation is a defect. It composes with
`concept-placement-rules.md`: every placement decision named below is governed
by the rules there (teaching order, print position, split vs. new concept,
Type identity, unattended operation).

Two ground rules frame everything below:

* **Nothing here is deterministic parsing.** Textbooks vary too much across
  boards, subjects and grades for pattern-matching to read them. Reading,
  classifying and normalising source material is model work. Deterministic
  code is reserved for bookkeeping: ledgers, provenance, ordering maths,
  coverage accounting.
* **The target is scale.** Tens of curriculums, hundreds of schools,
  thousands of chapters. Every step must run unattended (Rule 1 of the
  placement rules) and cost a bounded, predictable number of model calls per
  chapter — never an open-ended argument loop.

---

## The five steps

### Step 1 — Read the chapter, then break it down

Read the source the way a person reads it: recognise what every piece of the
document *is* before deciding anything about it. Every block is one of:

* **prose** — teaching text
* **heading** — a topic or section boundary
* **figure** — an image, map, diagram, or illustration, with its caption
* **activity** — a source-set activity or exercise embedded in the flow
* **info hub** — a boxed aside, "do you know?", biography box, source excerpt
* **question** — anything that asks the learner to do something
* **furniture** — running headers, footers, page numbers, watermarks,
  reprint lines: layout debris that carries no teaching content

Then break the chapter into **topics**, and topics into **concepts** —
quantified units of learning. Page numbers, print position and reading order
are recorded as provenance and are never evidence (Rule 4a).

Every concept carries its **learner analysis**: an *Achieving Mastery* line
closing its Description (what the learner can do once the concept is
mastered) and a *Misconception/ Error Analysis* section holding a genuine
incorrect belief and a genuine process error — two distinct meanings, never
filler. These are part of what a concept *is*, not decoration added later.

### Step 2 — Accumulate all images, then place each one

Collect **every** figure in the chapter into one pool, no matter where it was
printed. An illustration may sit pages away from its material purely because
of printing technicalities. Place each one with the topic and concept whose
content it depicts. First page or last page — irrelevant.

### Step 3 — Accumulate all activities and info hubs, then place each one

Same pooling, same test. An activity belongs with the material it exercises;
an info hub belongs with the material it enriches.

### Step 4 — Accumulate all questions, then polish them

Collect **every** question in the chapter into one inventory, each with a
stable QID.

Then **polish**. Textbook phrasing is often unusable as a standalone test
item — *"Look at the figure once again and guess why…"* presumes the book is
open at that page. The polished form is a properly phrased, self-contained
question, with the referenced figure or illustration from the textbook
attached to it so it stands on its own.

Polishing rules:

* The polished question is a **derived artifact**. The original wording is
  preserved beside it, and the polished form carries the source QID as
  provenance.
* A question that genuinely spans more than one concept or topic may be
  **split semantically**: `QID009` becomes `QID009#a`, `QID009#b`, … — each
  fragment a self-contained question placed independently, every fragment
  carrying the parent QID. The fragments together must cover everything the
  original asked. (Wire format: fragments mint a dotted numeric suffix on
  the parent QID — `QINV-0009.1`, `QINV-0009.2` — the id shape the
  pipeline's tooling already parses for sub-questions.)
* Polished wording ships **flagged for review**, the same way as every other
  best-judgement output (Rule 1, amended). The reviewer corrects wording in
  the delivered workbook; the run never waits on it.

### Step 5 — Classify into Types and Cases, then allot

With the polished inventory in hand, and **only** in this order (Rule 5):

1. Classify every question into a **Type**, and a **Case** where one applies
   — each with a proper written definition.
2. Questions sit as **Examples** under their Cases.
3. Then allot: each Case and Example places individually onto whatever
   concept and topic it belongs to. A Type is a chapter-level identity and
   spans topics; it is never split to make its parts fit one (the
   Napoleon/Mussolini/Hitler example in the placement rules). A task needing
   more than one topic goes to the **latest in teaching order**.

---

## The completion test: coverage, not certainty

The manual worker's definition of done is simple: **everything is covered
well**. That is the run's only blocking check.

At the end of a run, every item in the source is accounted for, exactly once,
in one of two states:

|State|Meaning|
|-|-|
|**Placed**|It sits on a topic/concept (or, for questions, under a Type/Case) with its provenance recorded|
|**Flagged**|It is in the output, placed by best judgement, with a note saying what was uncertain|

The accounting covers: every non-furniture block, every figure, every
activity and info hub, every QID and every QID fragment. Furniture is listed
as dropped, with what it said.

It also covers the per-concept learner analysis: **every shipped concept has
its Achieving Mastery line and a Misconception/ Error Analysis section with
distinct, non-filler meanings.** A concept missing either is flagged in the
output for the reviewer — never silently shipped bare, and never dropped
for it.

### Decide once

A placement or host decision is made **exactly once**, by one judgment
applying the written rules. A second opinion — an independent critic's
rejection, a "needs review" request, a directive from an earlier round —
is **recorded on the row as a review flag and never blocks, escalates, or
replays**. Ownership questions are only ever asked about single-claim
units: a compound question is split into fragments first, so "who solely
owns a thing that is two things?" cannot be asked. No human resolves
anything mid-run; uniformity comes from one decision procedure, fixed
tie-breaks, and cached decisions — not from repeated adjudication, whose
outcome depends on loop accidents.

What the completion test is **not**: a mid-run adversarial gate. A placement
another pass disagrees with is a *flag*, not a *rejection*. Nothing argues an
item out of the output; nothing loops until two models agree; nothing stops
the run because a single placement could not be proven beyond doubt. A run
fails only when it genuinely cannot proceed (source unreadable, provider
down, quota exhausted) — and then it says so. It never waits (Rule 1).

---

## Mapping to pipeline passes

|Step|Pass|Status when this document was adopted|
|-|-|-|
|1 (read + break down)|**Pass 1 — Chapter Reading**: a model pass classifies and normalises every block before any compiler touches the text|New|
|1 (topics/concepts)|Phase 3 hierarchy provider|Exists; keeps|
|2–3 (pooled placement)|Pooled placement over the full inventory, under the shared placement rules block|Partially existed; rebuilt as pooled|
|4 (polish + split)|Question polishing pass, QID`#a` splitting|Did not exist|
|5 (classify → allot)|Type mining + Case allotment under Rule 5|Existed; consumes the polished inventory|
|Completion|Coverage ledger, replacing mid-run adversarial blocking|Replaces the committee layer|

What is deliberately retained as deterministic bookkeeping — not parsing:
QID/blocking provenance ledgers, `compute_placement` teaching-order maths,
checkpoint/resume, the reviewer flag loop, the shared `PLACEMENT_RULES`
prompt block and its drift-guard tests.
