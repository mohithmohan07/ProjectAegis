# The Aegis Restructure

One finalised statement of what Aegis is, how the pipeline runs end to end, what
already exists in the codebase, what gets rebuilt, and the decisions taken. The
*Aegis* document (Aegis.docx, authored by Mohith Mohan, Founder, UpSchool) is
the soul of this spec; everything else serves it.

**Status:** Architecture baseline, 16 August 2026 — adopted with no code
changes. All decisions Q1–Q13 recorded (Q7 amended by Q13; Q8 decided
for-now; Q4 provisional per D3).

**Sources:** Aegis.docx · SOP Bulk-Import Fill Guide · Open/Specific Rubrics
workbook (v1) and the corrected API Policy Registry v2.0 · Question-Paper
Blueprint & Analysis · the repo decision docs · a full six-area codebase map ·
the GPT-authored "Aegis Restructuring Architecture v1.0" (alignment-checked,
verdict in §11).

The interactive rendering of this document lives as a Claude artifact; this
file is the repository record of the same baseline.

---

## §1 · How to read this document

Four documents were consolidated with the decisions already made in the repo
and a full map of the code that exists today:

* **Aegis.docx** — the soul. Where anything below conflicts with it, the
  conflict is surfaced as a numbered decision in §12, never silently resolved.
* **SOP — Filling the Content Bulk-Import Sheet** — the law of the output
  format: the five-level Chapter → Topic → Concept → Group → Question model,
  sheet layouts, label conventions, formatting tokens, QC checklist.
* **Rubrics (Open / Specific)** — the classification framework for
  `answer_restriction`; governed per Decision Q11 by the corrected v2.0
  registry with the Math/Physics families kept Open.
* **Question Paper Blueprint & Analysis** — the marking-scheme rubrics: how
  marks decompose into steps by difficulty, marks and question shape.

Two further documents arrived after the first consolidation and were
alignment-checked line by line: the GPT-authored **Aegis Restructuring
Architecture v1.0** (with its D1–D7 ledger) and the corrected **Open/Specific
API Policy Registry v2.0** workbook (alignment verdict in §11). Their verified
improvements are folded into the sections below; their conflicts with the soul
or with recorded doctrine are in the decision register, never adopted silently.

Repo decisions carried forward as already settled: the placement rules
(Rules 1–6, with Rule 1 amended to ship-anyway), the unattended release and
Case-granular routing rules (Rules A–G), the manual-process doctrine (five
steps, coverage-not-certainty, decide-once), and the phase-3 rewrite
architecture (envelope → Settle → Host → Polish → Assemble → Release). Where
one of these collides with the Aegis document, it appears in §12.

> **Standing directive, applied everywhere:** nothing in this document
> proposes a regex, a keyword list, a numeric threshold, or any deterministic
> judgment about content. Every decision about what the source *means* is a
> model verdict over the API, verified by an independent second pass.
> Deterministic code appears only as plumbing that records, caches, orders and
> renders what the model has already decided.

## §2 · The product in one paragraph

Aegis powers Clarius — UpSchool's product — as its content engine. A chapter
from any textbook, in any format, goes in. The model reads it the way an
expert reads it, breaks it into Topics and Concepts, refines every activity
and info hub, polishes every question, classifies Types and Cases, captures
prerequisites, and writes **four files in the Bulk-Import format**: the
Post-Learning Concept Review, the Post-Learning Master File, the Pre-Learning
Concept Review, and the Pre-Learning Master File. The two Concept Reviews open
in Aegis as rendered, editable pages where the reviewer corrects anything by
hand or by written instruction — applied directly through an API, without
re-running the pipeline. Publication into the database is a separate,
explicit, authenticated act. Nothing is lost, nothing is guessed, everything
is accounted for exactly once.

## §3 · The governing rules

**R1 · Every judgment is a model verdict.** Reading, classifying, sorting,
splitting, placing, polishing, allotting, grouping, marking — all of it
happens through the model API. No regex classifies content. No keyword list
decides what a block is. No threshold decides that something is too short,
too long, too few or too many. No count is derived from characters, tokens,
chunks or pages. Where a rubric or rule document exists (Open/Specific,
blueprint marks, placement rules), it is handed to the model as evidence
alongside the source, and the model gives the verdict.

**R2 · An independent second pass verifies — and only ever flags.** Every
consequential verdict is checked by a separate model call that saw neither
the first call's reasoning nor its incentives — the author/critic pattern
already standard in the codebase. The verifier's answer is itself a model
verdict — "confirmed" or "not confirmed" — never a numeric score compared
against a floor. **Decided (Q10): the critic is an auditor everywhere, never
a judge.** Its dissent becomes a review flag on the shipped item; it never
gates acceptance, never triggers a fresh-author retry, and there is no
adjudicator layer — in the concept pipeline *and* in the assessment lane,
whose current critic-gated retry loops are restructured to this form.

**R3 · Never wait, never lose, decide once — everywhere.** Every decision —
placement, topology, hosting, and freshly authored content alike — gets one
bounded budget: one verdict plus one correction. Second opinions become
review flags, never replays. **Decided (Q7 as amended by Q13):** a run never
halts on a semantic non-decision — at any block, **The Fixer** (§8) takes one
recorded, flagged, best-judgment decision and the run continues to a complete
release. What survives absolutely from fail-closed: content is never deleted,
never guessed *silently*, and never decided twice — every Fixer decision is
explicit, cached, and flagged for the reviewer. A run can fail only on
genuine impossibility: source unreadable, provider down, quota exhausted. It
never waits on a human mid-run, and it never withholds finished work.

**R4 · Exact-once coverage: Placed or Flagged.** Every non-furniture block,
every figure, every activity and info hub, every question ends the run in
exactly one of two states: **Placed** (on a topic/concept, or under a
Type/Case, with provenance) or **Flagged** (in the output, best-judgment
placement, with a note saying what was uncertain). Furniture is listed as
dropped, with what it said. Silent incompleteness is impossible; the coverage
ledger reports, and the exact-once contract blocks.

**R5 · The workbook is the database, append-only; generation never
publishes.** Everything lives in the canonical Bulk-Import format and is
written back append-only. A `question_label`, once uploaded, is never
reassigned. Generation stages a release; a person publishes it — explicitly,
idempotently, with the audit kept (Rule G).

**R6 · Provenance everywhere; print position is never evidence.** Stable IDs
are the identity of everything: `QINV-####` for questions, `BLK-#####` for
blocks, `FIG-#####` for figures, Type/Case IDs, misconception IDs. Page
numbers, reading order and print position are recorded as provenance and
never used as placement evidence (Rule 4a). Source order is not teaching
order.

**R7 · Unattended, bounded, resumable.** The target is scale: thousands of
chapters, unattended, at a bounded and predictable number of model calls per
chapter. Verdicts are cached content-addressed (decide once — a resume is a
free cache walk, never a re-litigation). Checkpoints are durable; a network
drop costs nothing.

### What this purges from the current code

Each deterministic-judgment residue found in the codebase map is removed and
replaced with a model verdict (with advisory critic) or deleted with the
feature it served:

* Pre-learning quota bounds — 4–6 topics, 5–7 concepts per topic
  (`generation.py`, ported from the legacy script). The model decides the
  pre-learning structure from the prerequisites actually captured.
* Legacy 40–60 concepts-per-chapter bounds and the character-length
  "expected row count" warning.
* Heading/filler helper heuristics (`_is_filler_source_topic`,
  `_is_non_topic_heading`, `_is_question_list_heading`) and the "example too
  short" check in question polishing.
* Plain-text cue gates ("Discuss" / "Activity" / "Project" keyword matching
  in the phase-2.1 source layer) — cue recognition becomes a model verdict
  verified against the page.
* String-similarity duplicate detection (0.95 `SequenceMatcher`).
* Numeric confidence floors as acceptance gates (0.920/0.96 style).
  Acceptance becomes: the author's verdict is positive *and* the independent
  verifier confirms; not confirmed → flag. No number decides meaning.
* Deterministically synthesized content: the fallback culmination-row
  synthesizer and any code-composed recap text. Culmination recaps are
  authored by the model from the topic's settled concepts, critic-flagged.
* The difficulty→tier regex map and BG/IG/AG display-name parsing (retired
  with the Apps Script tagging tool).
* The type-granularity ratio thresholds — granularity becomes a model
  verdict on the mined taxonomy.
* The silent "topic-bounded-deterministic" grounding fallback (already
  condemned by the rewrite spec's seam closures — no live API, no run).

**What deterministic code still does** — and only this: store files, assign
sequential IDs to things the model already identified, order rows by verdicts
already given, cache decisions, validate schema shape, render certified
content into the workbook, and refuse to proceed when a contract is broken.
Mechanics, never meaning. The Question/Task inventory is a case in point: the
blocks it enumerates were identified as tasks *by the model* during verified
conversion — the inventory just numbers them in source order so identity is
stable across runs.

Three guards adopted from the GPT blueprint make this rule enforceable:

1. **The no-local-fallback invariant, by name.** No command-word matching,
   keyword matching, regex, first-match behaviour, deterministic default, or
   unlisted-family fallback may ever assign a semantic value. An undecidable
   item ships flagged; it is never defaulted. Written into every
   classification pass's prompt contract and checked in the release audit.
2. **Run-context pinning.** At run start, Aegis pins the provider, author and
   critic models, prompt set, schema and policy versions, and public asset
   origin — immutable for the run and every retry.
3. **The semantic-authority matrix as a maintained artifact.** One table
   naming, per decision boundary, the API author, the API critic, and the
   mechanical-only checks. A boundary with no API author is a defect by
   definition.

## §4 · The pipeline, phase by phase

### Phase 01 — Upload & source normalisation

*Exists today: the GPT PDF-to-ACSD reader, the only converter since Mathpix
was scrapped.* Any source, any textbook material. A text-based PDF is used as
it is; an image-based PDF is converted back to images internally on upload.
Upload stores the file only; conversion and generation are explicit actions;
the PDF is never auto-matched to a chapter. Kept exactly as built: the ACSD
with stable block/section/figure/task IDs, byte-exact reconstruction, and the
artifact bundle per upload.

### Phase 1.1 — Conversion

*Exists today: model page extraction + independent page verification, cached
per PDF hash.* The model reads the chapter PDF page by page and extracts its
contents, tables and images; each extraction batch is verified by an
independent model pass against the same pages. Images are cropped as
source-owned assets and carried by link.

> **Image hosting (Q8, decided — for now):** crops stay in Aegis app data on
> the Fly volume, served from the app's public URL — exactly as built. Two
> guardrails: links embedded in published content must be durable,
> non-expiring public URLs, and the Fly volume is now learner-facing
> infrastructure, so it is backed up. The SOP's UpSchool-environment hosting
> remains a designed later step — every asset carries a content hash and
> manifest entry, so a publication-time URL rewrite can migrate the corpus
> cleanly.

### Phase 1.2 — Sorting contents: the three containers

*Exists today as Pass 1 — Chapter Reading: block classification by the model,
flag-and-continue.* The containers are the sorted views of the model's block
verdicts:

| Container | Holds | Model block classes behind it |
|---|---|---|
| **Container 01** | All information text that corresponds to chapter learning | prose, headings (+ the figures placed with them in Phase 2.2) |
| **Container 02** | Activities, info hubs, "do you know?", facts, project work | activity, info hub (+ their figures) |
| **Container 03** | Questions and assessments, wherever they appear | question — checkpoints, discussion time, review points, ponder, end-of-chapter exercises, activity-embedded questions |

Two rules from the Aegis document govern the sort, both judged by the model:
activity and "do you know?" headers are never topics or concepts on their
own (their information is considered within the concepts, post-polishing);
and Container 01 stays coherent — as detailed as the source, but only the
real topic headers and sub-headers guide the division.

The containers are **projections over one evidence graph, never destructive
buckets**: a block keeps its primary role and may participate in more than
one downstream relationship — an Activity remains Container-02 enrichment
while the learner task embedded inside it is also a Container-03 question
with its own QID. Sorting never deletes a relationship.

Furniture (running heads, page numbers, watermarks) is dropped and listed as
dropped, with what it said (R4).

### Phase 2.1 — Topology & concept detailing

*Exists today as the semantic graph + skeleton + Settle pass; residues purged
per §3.* Container 01 breaks into **Topics**, topics into **Concepts** — for
every subject except languages, a concept is a *quantified learning unit*.
The division is by meaning, never by volume: a lower-grade chapter splits
more discreetly, a higher-grade chapter less so — the model's call, made from
what the book teaches, never from character counts (the MES
three-dimensional-shapes chapter is the reference for treating thin chapters
with respect). Headers and sub-headers are evidence the model weighs, not
rules the code applies. Placement follows the placement rules verbatim
(Rules 2, 3, 4, 4a, 6).

**Concept detailing** writes, per concept, a well-worded refined description
in grade-level vocabulary (never off the chart), closed by an **Achieving
Mastery** line: what it takes to master this concept — something critical,
thought-provoking, or a practice that unlocks it.

**The language mode (English)** — decided in scope per Q9:

* **Poems** — topics by stanza. Under each stanza-topic: a concept per pair
  of lines that conveys a meaning (literal and metaphorical reading, line
  analysis, setup, poetic devices, vocabulary), then a *culmination concept*
  for the stanza (rhyme scheme, the elements understood together). The
  elements covered must not coincide across the three.
* **Prose** — topics at the story's sizeable breaks; each topic breaks into
  *episodes*: quantified, significant plot points, each with a dramatic title
  as the concept name (a TV series and its episodes).
* **The last topic is always "Detailed Analysis of '\<Name>'"** with the
  standard concepts: Theme / Central Idea · Plot / Development of Ideas ·
  Characterisation / Speaker · Setting & Atmosphere · Language & Literary
  Devices · Culmination.
* **Grammar, listening and writing components** printed at the end of the
  chapter are threaded through the prose/poem concepts (tenses observed in
  each concept, if tenses is the component).
* Every language concept also carries its Achieving Mastery line.

### Phase 2.2 — Refining Container 02

All activities, info hubs and figures are pooled chapter-wide and each is
placed by the model with the topic and concept whose content it depicts,
exercises or enriches — never where the printer put it. The refined container
lays every item out as **information + its corresponding image, embedded by
URL** (`[img src="…" alt="…"]`), rendering into the concept's
Activity/Info Hub section.

### Phase 2.3 — Refining Container 03: the question inventory & polish

*Exists today as the Question/Task Inventory + Pass 4 Question Polishing.*

* Every question in the chapter, in any form — text, checkpoints, discussion
  time, review points, ponder, end-of-exercise, inside activities — is
  captured with a stable **QID**.
* Each is **polished** into a properly phrased, self-contained assessment
  item, with the referenced figure attached so it stands on its own.
* **A question is kept to its entirety. It is never split.** Multi-part
  questions stay one question, every part in order. A question whose parts
  genuinely span concepts is a culmination-level item, placed whole.
* The polished form is a derived artifact: original wording preserved beside
  it, source QID as provenance, shipped flagged for review; the run never
  waits on it.
* **Three wording layers**: *raw source* (immutable) → *normalised source*
  (transcription/formatting corrections only, through the source author +
  critic) → *published assessment* (the polished item).
* **The equivalence checklist**: the polishing verifier checks, by name, that
  the published item does not omit a source requirement, add a new one,
  reveal an answer, change the expected response, detach necessary context,
  or alter the visual dependency. Under decide-once, a failed check is a
  review flag — never a blocking gate.
* The model records each item's **source role** (exercise, checkpoint,
  activity instruction, project prompt…) as review metadata. Per the soul,
  this never grants exclusion power: *every* question becomes a polished
  assessment in the Master File.

### Phase 2.4 — Misconceptions & Error Analysis

*Decided (Q1): chapter-level inventory only — the per-concept requirement is
dropped.* Build the chapter's list of **distinct** Misconceptions and Error
Analyses — each a genuine, strong addition to the chapter's learning, each
with its own ID. Error analysis typically surfaces around
practical/experimental work. Misconception (an incorrect belief) and Error
Analysis (a process error) are two distinct meanings, never filler. Allotment
happens in Phase 4.3 — uniquely, without repeating, and *not every concept
receives one*. Consequence: the every-concept learner-analysis contract is
**retired**; a concept's Misconception/Error Analysis section exists only
where an inventory item was allotted to it. Achieving Mastery is unaffected —
every concept still carries its Mastery line.

### Phase 03 — Pre-Learning concept mapping

*Restructured: the separate pre-learning flows are replaced by capture inside
the one Build Concepts run.* While every phase above runs, the model keeps a
running capture of **pre-requisite elements**: things taught in previous
years, vocabulary, and the basics needed to understand a line or concept. At
the end, that capture is built into a complete Pre-Learning concept map with
the same detailing standard as Post-Learning.

* Introduction and review sections at the start of a chapter are usually
  Pre-Learning elements — but the call is the model's, chapter by chapter:
  *The Rise of Nationalism in Europe* opens with Frédéric Sorrieu — a movie
  starting with a scene from later in the plot; treating that as pre-learning
  would lose it entirely. The critic verifies.
* Every Pre concept carries explicit **"needed-for" links** to the Post
  concepts that require it; the critic verifies necessity, grade boundary,
  non-duplication, and zero current-chapter leakage.
* The Pre-Learning Master File carries generated questions per pre-concept —
  **an adaptive target of about 5 per concept, tier split left to the
  model, under the diagnostic posture: the minimum coverage that genuinely
  verifies the prerequisite** (Q4, resolved per D3; calibration re-set by
  owner steer 2026-08-20 to ~10 and 2026-08-21 to ~5 — Q20): neither
  mandatory quota nor maximum; the model authors a concept-specific
  coverage plan; variance carries an authored, critic-flagged rationale;
  an explicit blueprint may override; a thin pre-concept is never padded.
  The Pre Master contains *generated* questions only — current-chapter
  source questions never appear in it.
* **Decided (Q3):** there is no separate pre-learning upload and no
  "derive from existing chapters" flow — one **Build Concepts** action
  produces Post and Pre together. Both existing flows and the ported quota
  engine are removed.

### Phase 04 — Allotment of Types, Cases & Misconceptions

With the polished inventory in hand, and only in this order (Rule 5):

1. **4.1 Classify.** Every question is classified into a **Type**, and a
   **Case** where one applies — each with a proper written definition (the
   Type defines the reusable method; the Case the bounded variation).
   Questions sit as **Examples** under their Cases. Classification precedes
   placement.
2. **4.2 Embed.** Types and Cases are embedded into the concept-detailing
   column per their allotment, in the required rendering order (Rule D).
3. **4.3 Allot misconceptions.** Each inventory item from Phase 2.4 is
   allotted to the concept it belongs to.

Allotment law, from the Aegis document: *not every concept needs a question,
Type, Case or misconception. But every Type, Case and misconception is
allotted uniquely, without repeating.* Every QID has exactly one final
Type/Case assignment (Rule C). **Decided (Q2):** "uniquely" is read at
**Case/Example granularity** — each Case and each Example lands on exactly
one concept, while a Type identity may render under several concepts when its
Cases have different owners, exactly as Rule B and the
Napoleon/Mussolini/Hitler example record.

### Phase 4.5 — Groups

At the end of Phase 04, questions sit under their concepts. Then, per
concept: (1) questions are classified into **levels** — Basic, Intermediate,
Advanced; (2) within a level, **similar questions with minimum variation**
are clubbed into the same group; further grouping follows how the questions
are built (the SOP is the reference).

Group identity remains the machine key `(<ConceptID>) BG01` internally —
BG/IG/AG for the level, the two-digit suffix numbering the variant families.
**Decided (Q12, naming half superseded by Q16):** the friendly-title
projection is retired — per the SOP Bulk-Import guide, `group_name` and
`group_display_name` both carry the group ID itself (the `group_key`
value). Level calls and variant clustering are model verdicts with
independent verification whose dissent flags (Q10); unresolved clusterings
ship as flagged singletons, never guessed.

**Group-description quality bar:** a group's description states the exact
capability the questions assess and how they are constructed — never a count,
a label list, or placeholder text.

### Phase 05 — Writing the outputs

All four outputs follow the same Bulk-Import format — **decided (Q5): the
SOP/MES reference-school family** (the layout carrying `answer_restriction`,
keywords and related_concepts, field-for-field the gold workbooks the
reference school accepted). Older canonical-layout workbooks stay readable
through the reader's auto-detection.

| Output | Contents |
|---|---|
| **01 · Post-Learning Concept Review** | Everything up to the Concepts column: Chapter, Topic and Concept bands filled, one row per concept, full concept detailing. Opens as a rendered, editable page (§7). |
| **02 · Post-Learning Master File** | All columns filled, including assessments: every source question, polished, on its answer-style sheet (Objective / Subjective / Descriptive), with Groups, master records, categories, cognitive skills, difficulty, `answer_restriction`, marks and marking. |
| **03 · Pre-Learning Concept Review** | As Output 01, for the Pre-Learning map. |
| **04 · Pre-Learning Master File** | All columns filled: the generated pre-learning questions (complete diagnostic coverage of each Pre Concept's Mastery, no quota — contract §8, Q26), grouped and marked the same way. |

Two model passes finish every question row:

* **Open / Specific** — the model reads the question together with the
  Open/Specific policy registry and gives the `answer_restriction` verdict;
  the independent verifier's dissent flags (Q10 — no adjudicator). The
  registry is evidence the model reasons over — never a lookup table. The
  verdict's required payload: the complete question, source context, image or
  table, expected answer, rubric, response modality, subject, and grade.
  **Decided (Q11): the corrected v2.0 registry governs** — its answer-space
  definitions, per-item judgment, and removal of every default rule —
  **except that the Math/Physics method-equivalence families keep their v1
  Open stance** (word problems, variable assignment, multi-formula, numerical
  technique, own-words definitions classify Open), so a learner solving by a
  valid alternate method is always safe in grading; revisit only when
  Clarius' Specific grading provably honours recorded equivalents. The
  no-local-fallback invariant holds: an unclassifiable item ships with the
  author's best verdict and a review flag; `answer_restriction` never
  receives an invented third value and the row is never withheld.
* **Marks & marking scheme** — the model reads the question with the
  Question-Paper Blueprint and produces the mark decomposition: step marks,
  diagram marks, sub-question marks (each sub-question enumerated to match
  the stem), keyword weightages summing exactly to the total, no marks for
  redundant steps. The arithmetic identities are checked mechanically and
  **fail closed** — a weightage-sum or decomposition mismatch is a
  mechanical defect, not a judgment call: it is never accepted with a flag,
  and the run does not proceed on corrupt marking. The model owns the
  decomposition; the check only re-asks it (through the same checker, and
  through The Fixer on exhaustion per Q13, which is likewise re-validated by
  that checker) and refuses to ship arithmetic that does not balance —
  nothing rewrites the model's marking. **The recorded blueprint cell is
  canonical for a question's total marks; the decomposition of that total is
  the model's per-item verdict.** Decided 17 Aug 2026: the API authors the
  breakdown — there is no external marking-rubric document and none is
  required (the *Question Paper Blueprint & Analysis* is not adopted as
  runtime evidence; it stays only in this document's §11 provenance). The
  registry's worked mark-scheme examples are calibration evidence for
  Open/Specific only.

Three release invariants sharpen the existing Rules E–G:

* **One immutable snapshot, four projections.** All four files are projected
  from one accepted release snapshot — the Concept and Master pair for each
  phase carry byte-identical chapter/topic/concept content with matching
  identity hashes. The semantic pipeline is never re-run per file. (The
  append-only Bulk-Import workbook remains the published database of record;
  the snapshot is the release-level truth it is published from — the two
  compose, as the MES lane already proves.)
* **Named release states:** *Ready* / *Ready with flags* (semantic
  uncertainties visible — downloads and explicit publication both available;
  flags never block per Rule E) / *Diagnostic release* (structural/import
  integrity failed — evidence still ships; database upload blocked). Semantic
  doubt flags; structural corruption blocks.
* **Publication hardening:** the explicit upload action verifies the chosen
  release's artifact hashes, schema, source-owned assets and placement
  identities before the transactional write, and records a durable
  publication receipt. Idempotent, model-free, never drops a highlighted row.

## §5 · The concept-detailing house format

One rendered value per concept in `concept_details`, assembled by the
renderer from model-authored, critic-flagged parts — the model authors every
sentence; the renderer only assembles what was certified:

```
Description: <refined, grade-level description of the concept>
Achieving Mastery: <what it takes to master this concept>
 // Activity/Info Hub: <placed activities & info hubs, info + [img src="…"] embeds>
 // Types: Type 01: <title> — <reusable method definition>
          Case 01: <bounded variation definition>
          Example: <full polished question, with its figure>
          Type 02: …
 // Misconception/ Error Analysis: Misconceptions: <genuine incorrect belief>;
          Error Analysis: <genuine process error>
```

* Maths renders as `[Katex]…[/Katex]`; images as `[img src="…" alt="…"]` —
  wrappers in body text, raw values in typed answer cells (SOP §4.3).
* Examples never float above their Case and never lose their QID (Rule D). A
  Type or Case without a usable definition releases with a specific audit
  error — it is never invented.
* The Misconception/Error Analysis section appears only on concepts that
  received an allotment from the Phase 2.4 inventory (Q1). Description and
  Achieving Mastery appear on every concept.

## §6 · Data model, naming and the workbook

The five-level relational import from the SOP is the data model, unchanged:
**Chapter → Topic → Concept → Group → Question**, joined by exact text
labels, master records defined once, questions on the sheet matching their
answer style, children referenced by copy-pasted labels.

| Record | ID pattern | Example |
|---|---|---|
| Chapter | `<Grade>_<Subject>_<Board>_<Publication>` | `10_Chemistry_ICSE_SELINA` |
| Topic | `<Class><Board><Subj>_<Chapter>_PL\|PrL` | `10ICCH_Number_System_PL` |
| Concept | `<TopicID>_T##_<ConceptName>` | `10ICCH_Number_System_PL_T01_Real_Number` |
| Group | `(<ConceptID>) BG\|IG\|AG##` | `(10ICCH_Number_System_PL_T01_Real_Number) BG01` |
| Question | `<Class><Board><Subj>_<Chapter>_PL\|PrL_T##_<Concept>_Q##` | `10ICCH_Crcls_PL_T01_Thrm_Q01` |

* Titles carry *name + (machine ID)*; display names carry the name only; the
  pair must match (SOP §3.3). Labels are unique and stable forever.
* **Known gap the restructure closes:** the current writer stamps one
  chapter-level code on all topics; the restructured writer mints per-topic
  and per-concept IDs natively.
* Fixed system values per the SOP: `question_source = UpSchool DB`,
  `question_appears_in = Pre/Post-Worksheet/Test`, `group_status = Active`,
  `math_keyboard = No` unless the learner must type maths.
* **`question` and `question_text` both carry the same complete assessment
  wording** (D4 — adopted on evidence: every populated row in the accepted
  reference gold workbooks has the two fields byte-identical, and the current
  materialization writes them that way). The SOP's "question = stem only"
  line is superseded on this point.
* Every published image carries **neutral, non-answer-leaking alt text**.
* The pre-upload QC checklist (SOP §7) becomes the release audit's mechanical
  checklist — failures flag rows and name labels; nothing silently fixes.

## §7 · The review & edit surface

* **One action: "Build Concepts."** The Post/Pre chooser, the separate
  Pre-Learning upload flow, and "use existing Post Learning" are removed. One
  run produces all four outputs (Q3).
* **Outputs 01 & 03 open as pages, not downloads.** A rendered view — topics,
  concepts, full detailing with maths and images — where the reviewer
  **edits manually in place**. A manual edit is a human decision; Aegis
  applies it verbatim and records it. Nothing re-runs.
* **The instruction box.** Plain-language changes, listed together, then
  **Apply changes** — one bounded model pass interprets and applies the list
  to the staged release; the Aegis pipeline itself is never re-entered. Every
  applied round produces a **new immutable release version** with the
  instruction, operations and diff preserved.
* **The reviewer's word is final on the page.** Reaffirmed against the GPT
  blueprint, which would route manual edits through API review: per the soul,
  the human is the last word on their own correction; Aegis may at most
  attach a non-blocking advisory note. An applied instruction never re-runs
  pipeline stages.
* **Publication unchanged in spirit (Rule G):** "Upload to database" remains
  a separate, explicit, authenticated act.
* Master Files (Outputs 02 & 04) remain downloadable artifacts alongside the
  reviews, with the diagnostics archive.

The instruction box has the capacity to edit **anything** in the output —
worked examples from the real RNE review feedback:

* *"Describe the cause of the Silesian weavers' uprising… → Economic
  Hardship and the Revolts of 1845–1848
  (10CBSS_The_Rise_of_Nationalism_in_Europe_PL_The_Age_of_Revolutions_1830_1848)"*
  — re-tag a question to the named concept.
* *"Find out more about nationalist symbols outside Europe… → Culmination –
  Nationalism and Imperialism"* — re-tag to a culmination concept.
* *"Two concepts 'German Liberal Hopes Represented in Sorrieu's Print' and
  'Sorrieu's Vision of Democratic Nation-states' can be combined into one."*
  — merge concepts, with detailing, Types and questions re-homed onto the
  merged row.

Move, merge, split, remove, rename, re-tag, reword, re-level, re-group — any
field, any row, any placement.

Build Assessments (blueprint sessions, upload identification, MES releases)
is untouched by this restructure except that it continues to consume what
Build Concepts publishes. The Assessment Tagging Apps Script — the primitive
predecessor — is formally retired.

## §8 · The three agents

Three named agentic roles sit over the phase pipeline. None is a second
pipeline — they are how the pipeline *configures itself*, *unblocks itself*,
and *finishes its output*. All three are model agents bound by the same
constitution (§3): decide once, record everything, flag what was judged, no
deterministic shortcuts.

### 8.1 · The Architect — builds the run's instructions

At run start, once conversion has identified the board, class, subject,
publication and chapter, the Architect assembles the **complete
phase-by-phase instruction set** — Phase 01 through Phase 05 — for this
specific chapter.

* **Frozen core, variable slots.** The base scaffolding is constant and is
  not the Architect's to rewrite: the working rules, placement rules, house
  formats, output contracts, naming conventions, and the no-local-fallback
  invariant. The Architect authors only the variable slots —
  subject-specific topology guidance (the thirteen subject adapters are its
  base material), grade-band vocabulary calibration, language-mode selection
  (poem vs prose), board/publication conventions, and chapter-specific
  cautions it reads from the source itself.
* **Why slots, not free authorship:** the founder's framing — the runtime API
  does not have the intelligence to build the prompting wholesale, so it must
  never be asked to. It slots specifics into a proven scaffold; most of the
  prompting stays the same on every run.
* **Governance:** the assembled instruction set is versioned and stamped into
  the pinned run context — its hash joins every decision key, so a changed
  instruction set can never silently reuse old verdicts. An independent
  critic reviews the assembly (advisory, flags per Q10). The set ships in the
  diagnostics for replay; the admin prompt registry remains the human
  override for the frozen core.

### 8.2 · The Fixer — unblocks the run

**Decided (Q13, amending Q7):** wherever the run hits a block — a failed
gate, a structural defect, a semantic non-decision, an integrity contract
refusing to proceed — The Fixer is invoked with the full context: the failing
check, the code path's contract, the active prompts, the produced output, and
the source evidence. It takes **the most suitable decision at that point and
passes the run through. Always.** Runs always reach a complete release.

* **One decision per block** (decide-once): the Fixer's verdict is made once,
  cached content-addressed, and replayed free on resume — never re-litigated.
* **Never silent:** every Fixer decision is recorded on the affected rows as
  a review flag stating what was blocked, what was decided, and why — the
  reviewer sees every Fixer intervention before publication.
* **Never destructive:** exact-once accounting (R4) still binds — a Fixer
  decision places or flags content; it never drops a question, block, figure
  or concept.
* **Reads code, never edits it:** the Fixer reads the code context to
  understand the block; it never modifies code, the frozen scaffold, or
  contracts at runtime.
* With the Fixer in place, a run can fail only on genuine impossibility —
  source unreadable, provider down, quota exhausted. The halt paths, the
  dormant mid-run human-pause machinery, and the legacy semantic-recovery
  code are all superseded by this one role.
* **What it does not replace:** the pre-spend source-integrity gates (source
  review, source-topic recovery, Type granularity). Those fire before
  generation begins, for a broken source only a human can replace — they are
  not mid-run blocks.

### 8.3 · The Refiner — finishes the output

After assembly and before staging, the Refiner reads the four rendered
outputs — the actual Excel contents — and refines them to expectation:
wording polish, grade-level consistency, formatting hygiene, description and
group-description quality, coherence between the Concept and Master
projections. **The output, not the process** — it never re-runs a phase and
never revisits a decision.

* **Identities are untouchable:** QIDs, labels, placements, groups, coverage
  — the Refiner polishes content within them and can never move, add or
  remove a row.
* Every refinement is recorded as a diff on the release; the arithmetic
  identities (marks, weightages) are re-checked mechanically after it runs;
  an independent critic flags (advisory).

### 8.4 · And then, the reviewer's word

After the Refiner: download and review, then the instruction box (§7) — type
the corrections, apply, and anything in the output changes as expected. The
three agents get the output as close as the model can; the last mile is the
reviewer's, applied without ever re-entering the pipeline.

## §9 · Keep · Rebuild · Retire

| Area | Verdict | Notes |
|---|---|---|
| GPT PDF-to-ACSD reader (conversion + independent verification + outline + crops) | Keep | The only converter. Caches per PDF hash. |
| ACSD canonical source layer (Phases 1–2.2.1) & artifact bundle | Keep | Keyword-cue gates in 2.1 become model verdicts (§3 purge list). |
| Chapter Reading (Pass 1) block classification | Keep | Feeds the three containers directly. |
| Semantic source graph (hierarchy author + critic, opaque IDs) | Keep | Pre-81% boundary unchanged. |
| Question/Task Inventory (QINV) + Question Polishing (Pass 4) | Keep | Phase 2.3 as specified; never splits. |
| Phase-3 rewrite engine (envelope · kernel · Settle · Host · Polish · Assemble · Release) | Keep | Becomes the *only* post-81% path; flag removed, legacy lane deleted. |
| Legacy post-81% lane (phase 3.1–3.11 contract modules, semantic recovery, human-pause machinery) | Retire | Rewrite spec PR-4 completion; superseded by The Fixer (Q13). |
| 36-module monkeypatch contract stack over `generation.py` | Rebuild | Collapse into the phased module tree. |
| Release staging + diagnostics + explicit publication (Rules E–G) | Rebuild | From one workbook to the four named outputs; the two release systems converge. |
| Pre-Learning flows (upload / from-existing) + ported quota engine | Retire | Replaced by Phase-03 capture inside the single run (Q3); quotas purged. |
| Every-concept learner-analysis contract (author + flag on every concept) | Retire | Q1: chapter inventory + unique allotment replaces it; Achieving Mastery stays per-concept. |
| Pre-Learning question generation (coverage-planned master file; the adaptive-40 figure is struck per Q26) | New | Blueprint- and registry-guided (Q4 per D3, Q11). |
| Groups for Build Concepts master files | New | Reuses the MES grouping engine (level verdict + variant clustering); its critic becomes advisory per Q10. |
| Critic-gated retry loops in the assessment lane (MAX_ATTEMPTS acceptance gates) | Rebuild | Q10: decide-once everywhere — one verdict plus one correction; critic dissent flags, never retries. |
| Open/Specific (`answer_restriction`) classification pass | New | Policy Registry v2.0 as model evidence, Math/Physics families Open (Q11). |
| Marking-scheme pass (blueprint rubrics, sub-question marks) | New | Arithmetic identities checked mechanically and fail closed; a mismatch is a mechanical defect, never accepted with a flag. |
| Language mode (poem/prose topology, Detailed Analysis topic, grammar threading) | New | Subject adapter within Phase 2.1; in scope per Q9. |
| The Architect (per-run instruction assembly: frozen core + subject/grade slots) | New | §8.1; subject adapters and the prompt registry are its base material. |
| The Fixer (block resolution: one recorded, flagged decision, always passes through) | New | §8.2, ruling Q13; supersedes all halt paths, dormant pause machinery, and legacy semantic recovery. |
| The Refiner (pre-stage output refinement over the four rendered files) | New | §8.3; identities untouchable, every change a recorded diff. |
| Rendered review/edit pages + instruction-box apply API | New | §7; revision engine is the foundation; edits anything in the output (§8.4). |
| Image hosting | Keep | Q8: Fly app-data hosting for now, durable links + volume backup; UpSchool migration designed for later. |
| Per-topic / per-concept ID minting in the writer | Rebuild | Closes the tag-addressability gap found in CH01 authoring. |
| Assessment Tagging Apps Script (Drive/Sheets) | Retire | "A primitive tool I had built before" — superseded by the release pipeline. |
| Legacy CLI scripts (`mmd_to_concepts_excel`, `excel_to_concepts_prelearning`, `concept_mapping_to_prelearning`, `bulk_upload_ultimate`, `extract_pdfs`) + pasted-code notes | Retire | All superseded in `backend/app`; `openai_policy` alone survives as the shared model policy. |
| Create Workbooks (revision-PDF generator) | Keep | Separate product, out of this restructure's scope. |
| Build Assessments module (blueprint sessions, MES releases) | Keep | Consumes Build Concepts' published outputs. |
| Auth, checkpoints, Drive mirror, prompt registry, usage accounting, append-only writer + outbox | Keep | Mechanics; untouched. |

## §10 · Build sequence

No code moves until the register below is signed off (it now is).

1. **Finish the phase-3 rewrite migration** — flag on everywhere, legacy
   post-81% lane deleted, golden gates green.
2. **Purge the deterministic residues** (§3 list) — each replaced by a model
   verdict with critic, each with a regression pinning the new behaviour.
3. **Stand up the three agents** (§8) — The Fixer first (it replaces every
   halt path, so each later step inherits always-complete runs), then The
   Architect's instruction-assembly layer over the prompt registry, then The
   Refiner at the release boundary.
4. **Containers & Phase 2.2** — the info + image embedded Activity/Info Hub
   rendering.
5. **Phase 04 completion under the decided rules** — chapter misconception
   inventory & unique allotment (Q1); Case-granular uniqueness audit (Q2);
   retire the every-concept learner-analysis contract.
6. **Groups + Master File passes** — level verdicts, variant clustering,
   Open/Specific, marking schemes; Output 02 ships.
7. **Phase 03 pre-learning capture** — capture during all phases, pre-map
   build, coverage-planned generation (no quota, Q26); Outputs 03–04 ship;
   old pre-learning flows removed.
8. **Four-output release on the SOP/MES schema (Q5)** — including
   per-topic/per-concept ID minting and the QC-checklist audit.
9. **The review/edit surface** — rendered pages, inline edit, instruction-box
   apply API; frontend simplification to one Build Concepts action.
10. **Image durability per Q8** — non-expiring public links for published
    assets and Fly-volume backup; the UpSchool-environment migration stays a
    designed later step.
11. **Language mode** — the poem/prose adapter, validated on a real chapter
    (e.g. *The Elevator*).
12. **Staging acceptance corpus** — before production: a corpus spanning
    grades, subjects, boards, text and scanned sources, image-dependent
    tasks, maths, English poetry and prose, plus fault-injection cases: API
    dissent, quota failure, asset failure, cache alteration, interrupted
    release, interrupted publication.

## §11 · The GPT blueprint — alignment verdict

The GPT-authored *Aegis Restructuring Architecture v1.0* and the corrected
*Open/Specific API Policy Registry v2.0* were compared line by line against
the soul, the decided rulings, the repo's binding doctrine, and this spec —
with an independent second-pass verification that checked its claims against
actual code and the accepted gold workbooks.

**Where the vision aligns — strongly.** The core is the same product: API
authority for every semantic judgment with independent review; no keyword,
threshold, count, similarity or default shortcuts anywhere; four outputs from
one Build Concepts run; generation separate from explicit idempotent
publication; flagged visibility over silent substitution; the English
topology, the Sorrieu boundary, Achieving Mastery on every concept,
Case-level Type routing, and chapter-inventory misconceptions. Two of its
factual claims were verified true in the repo: `question = question_text` in
every accepted gold row, and the `(ConceptID) BG01` machine identity in the
grouping code.

**Adopted into this spec:** the no-local-fallback invariant by name,
run-context pinning, the semantic-authority matrix as a maintained
drift-guard (§3); containers as non-destructive projections (§4 Phase 1.2);
the three wording layers, the bidirectional-equivalence checklist, and
recorded source roles (§4 Phase 2.3); needed-for prerequisite links and the
D3 coverage-plan formulation, its numeric target since struck (§4 Phase 03,
Q26); the group-description quality bar
(§4 Phase 4.5); the single-snapshot invariant, named release states, and
publication hardening (§4 Phase 05); the Open/Specific decision-input
contract and Blueprint canonicity for marks (§4 Phase 05);
`question=question_text` and neutral alt text (§6); immutable release
versioning for revisions (§7); and the fault-injection acceptance corpus
(§10).

**Rejected — the soul or a recorded ruling overrides:**

* **Universal critic-gated acceptance, fresh-author retries, and the
  adjudicator API** (its P3, §4.3, §12.4/D6) — rejected by ruling Q10:
  decide-once everywhere; the critic is an auditor whose dissent flags and
  never gates, retries, or escalates.
* **API review over manual edits** (its §3.3) — rejected; the reviewer's
  edit on the review page is final (§7).
* **Revisions re-running pipeline stages** (its §15.2) — rejected; the soul
  says changes apply "without the intervention of the Aegis pipeline".
* **An eligibility filter that can keep a source question out of the Post
  Master** (its §8.5/12.1) — rejected; the soul says *all* questions, in any
  form. Source-role rationale is kept as metadata only.
* **Splitting OR-alternative questions into separate linked entities** (its
  §8.4) — rejected; "never split" is doctrine and the splitting pass was
  formally retired.
* **Current-chapter source questions in the Pre Master** (its §12.1) —
  rejected; the Pre Master is generated questions only, and its own §10.3
  forbids the leakage.
* **"Does not invent a default group"** read literally (its §14.3) — the
  approved template requires BG01/IG01/AG01 shells so questionless concepts
  survive into the Master; the shells stay.
* **Dropping the three pre-spend human pauses** (its §3.2 absolutism) — the
  source-review, source-topic and Type-granularity pauses fire before money
  is spent, for problems only a human can fix; they stay. Unattended means
  unattended *generation*.

**Its decision ledger, audited.** The doc declares "D1–D7 resolved; no item
remains open." Audit result: D1 = the Q3 ruling, D2 = the soul's four
outputs — already settled here. D4 (`question=question_text`) is adopted on
repo evidence. D3 (adaptive 40) matched this spec's recommendation and is
recorded as Q4's resolution, provisionally. D5, D6 and D7 were *not* in the
decided register and each changed something real — they appeared as Decisions
Q10–Q12 and have since been ruled on, alongside Q7, which the GPT doc had
silently resolved as "never stop". Notably, the later Q13 ruling (The Fixer)
landed on that same direction — the difference being everything: an explicit,
recorded, flagged deciding agent instead of silence. A ledger that closes
decisions its owner has not made is itself the kind of silent resolution this
project forbids.

## §12 · Decision register

Every place where the Aegis document, a decision recorded earlier, the built
system, and/or the GPT blueprint disagree. Nothing here was resolved
silently. Every point is decided: Q1–Q3, Q5–Q13 by Mohith's word on
16 Aug 2026 (Q7 amended by Q13; Q8 decided for now), and Q4 provisionally per
D3. Since Q26 (4 Sep 2026) the Master Governing Contract v2.0 governs where
an older entry disagrees; Q26 lists what it superseded and what stands.

### Q1 · Decided — Misconceptions: the chapter inventory is the only mechanism

The Aegis document builds a chapter-level list of distinct
misconceptions/error analyses with IDs and allots each uniquely — not every
concept needs one. The built system (from the 154-page review rounds)
authored one on every concept and flagged any concept missing it.
**Ruling: Aegis document only.** Phase 2.4 builds the IDed chapter inventory;
Phase 4.3 allots each item uniquely; the every-concept authoring requirement
and its missing-analysis flag are retired. Achieving Mastery remains on every
concept.

### Q2 · Decided — "Allotted uniquely" is read at Case/Example granularity

Each Case and each Example lands on exactly one concept; no QID ever appears
twice (Rule C); the Type identity may render under several concepts when its
Cases have different owners. Rule B stands.

### Q3 · Decided — Pre-Learning is captured inside the one Build Concepts run

A single Build Concepts action captures prerequisites throughout all phases
and produces Outputs 03–04 alongside 01–02. Both separate flows and the
4–6-topic / 5–7-concept quota engine are removed.

### Q4 · Decided per D3, provisional — the coverage default is an adaptive target

The recorded target is neither mandatory quota nor maximum. The model authors
a concept-specific coverage plan; any variance in total or split carries an
authored, critic-flagged rationale; an explicit blueprint may override; a
thin pre-concept is never padded to reach the target. *(Originally 40,
normally 20+20. Calibration re-set by owner steer 2026-08-20 to ~10 —
recorded at the time only in the residue ledger — and again 2026-08-21 to
about 5 per concept with the tier split left to the model, plus the
diagnostic posture: see Q20, which is the current ruling.)*

### Q5 · Decided — all four outputs use the SOP/MES reference-school schema family

The layout carrying `answer_restriction`, keywords and related_concepts,
field-for-field the gold workbooks the reference school accepted. Older
canonical-layout workbooks remain readable through auto-detection. Schema
constants, writer and acceptance tests migrate accordingly.

### Q6 · Decided — an ungroundable concept is atomised, never retired

The concept is narrowed to exactly what its evidence supports — teaching
content is never deleted. If even the atomised form cannot ground, The Fixer
(Q13) takes its one recorded, flagged decision and the run completes. The 25%
retirement cap disappears with retirement itself; whether a chapter's map is
trustworthy is a model-judged verdict in the release audit, never a
percentage.

### Q7 · Decided, then amended by Q13 — stop vs ship

Original ruling: the run halts at the undecidable point — nothing beyond it
is guessed — and everything durably decided before it ships as a flagged
release with the failure attached. **Amended later the same day by Q13:** the
halt is retired. The Fixer takes one recorded, flagged best-judgment decision
at the block and the run always completes. What survives: nothing is ever
guessed *silently*, nothing is lost, finished work always ships. `CLAUDE.md`
Rule 1's "stop the run" clause is to be formally amended in the repo when
implementation begins — with this register as the provenance.

### Q8 · Decided, for now — images stay in Aegis app data on Fly

Exactly what is already built: crops under the app's data volume, served from
the app's public URL. Two guardrails: (1) links embedded in *published*
content must be durable — non-expiring public URLs; (2) the Fly volume is now
learner-facing infrastructure, so its backup matters. The migration to
UpSchool-environment hosting stays a designed later step: every asset carries
a content hash and manifest entry, so a publication-time URL rewrite can move
the corpus cleanly.

### Q9 · Decided — the English pipeline ships inside this restructure

Sequenced near the end of §10 and validated on one real chapter (e.g. *The
Elevator*) before the restructure counts as done. The architecture is
identical for every subject; only the subject adapter differs.

### Q10 · Decided — decide-once everywhere; the critic never gates; no adjudicator

Every decision — placement, topology, hosting, and freshly authored content
alike — gets one verdict plus one bounded correction. The independent
critic's dissent becomes a review flag on the shipped item; it never blocks,
never retries, never escalates. The assessment lane's existing critic-gated
loops are restructured to this form. There is no adjudicator anywhere.

### Q11 · Decided — Rubrics: v2.0 governs, with the Math/Physics families kept Open

Adopt v2.0's structure, definitions, per-item judgment and no-default rules;
the Math/Physics method-equivalence families (word problems, variable
assignment, multi-formula, numerical technique, own-words definitions) keep
their v1 Open classification. Revisit only when Clarius' Specific grading
provably honours recorded equivalents. The registry is versioned evidence for
the model — never executable classification.

### Q12 · Decided — group naming follows the accepted gold convention (naming half superseded by Q16)

`group_name` and `group_display_name` both carried the friendly "Concept
name — Tier" title, exactly as the reference school accepted; the machine
identity stays internal in `group_key`. GPT's D7 split is set aside. The
naming half is superseded by Q16 (2026-08-21): both visible names now
carry the group ID itself, per the SOP Bulk-Import guide.

### Q13 · Decided — The Fixer always passes the run through; the Q7 halt is retired

The Fixer reads the full context of the block — the failing check, the code
path's contract, the active prompts, the output, the source evidence — and
takes the most suitable decision, once, recorded and flagged. Runs always
reach a complete release. Guardrails that stand: one decision per block
(decide-once, Q10), never silent, never destructive (R4 exact-once
accounting), the Fixer never edits code or contracts at runtime, and every
Fixer intervention is visible to the reviewer before publication. A run can
fail only on genuine impossibility: source unreadable, provider down, quota
exhausted.

---

### Q14 · Decided — one concept owns each Type; Rule B's multi-concept rendering is retired

Owner ruling, 21 Aug 2026 (workbook reviews of jobs 61 and 65): "Each type
should be unique and consistently mapped to the appropriate concept." A Type
identity renders under exactly ONE concept. When the Host pass's per-Case
verdicts resolve one Type's Cases onto different concepts, a dedicated
ownership verdict — model-decided, critic-advised, Fixer-backed, one per
split Type — chooses the owning concept from among the Cases' certified
hosts, and every Case and QID of that Type moves with it: for a Type's
member QIDs, Type ownership outranks per-question routing. Rule C's
exactly-once accounting is unchanged and re-checked after consolidation.

Two boundaries the ruling sets explicitly. First, the identity-splitting
alternative (fresh Type numbers per destination) is REJECTED — it is the
"same type repeated with different type numbering" symptom the same review
named as a defect. Second, **not every concept needs a Type**: choosing one
owner may leave other concepts with no Types at all, and that is a
legitimate outcome — the ownership verdict must never spread Types to cover
concepts, and no gate may demand a minimum Type count per concept.

`docs/concept-release-and-type-case-routing-rules.md` Rule B is amended in
place; ownership stays certified at Case/QID granularity BELOW the Type
(which Cases exist, which QIDs they carry), while the Type's rendering home
is single. Q2's register row stands for everything except the sentence
"the Type identity may render under several concepts", which this entry
supersedes.

### Q15 · Decided — duplicate GENERATED questions are removed by a recorded verdict

Owner ruling, 21 Aug 2026 (job-65 Master review: T01_C01 Q02 and Q03 were
the same question re-worded): when the model judges two GENERATED
pre-learning questions to be the same question — a paraphrase, a number or
a name swapped, the same ask with a different opener — one survivor ships
and the others are REMOVED from the Master. The removal is a model verdict
(one per pre-learning concept group, critic-advised, Fixer-backed,
content-addressed), never string similarity; the mechanical checker only
refuses impossible citations (an id outside the group, a survivor also
removed, a question ruled twice, a removal without a reason). It runs
BEFORE the per-question cell verdicts, so a removed question costs nothing
downstream.

This amends the flag-only doctrine (Q10) for exactly this case and nothing
else: removal is allowed because the survivor IS the removed question —
the learner loses no ask. Every removal rides the release payload under
``duplicates_removed`` with the full removed question, its survivor, and
the reason; the release shows *Ready with flags* so it is reviewable,
never silent (R4 stands: recorded exclusion, not loss). Source questions
are untouched — their exactly-once accounting is Rule C's, and a generated
question that duplicates a SOURCE question remains the generation critic's
flag-only territory. Removal has no quota and is never a goal: a group
with no duplicates removes nothing.

The same review's oral-activity complaint (T01_C08 Q01) stays flag-only —
the owner selected duplicate removal alone.

### Q16 · Decided — group names follow the SOP Bulk-Import guide; Q12's friendly names are retired

Owner ruling, 21 Aug 2026, answering the job-65 nomenclature flag with
"It's there in the SOP Bulk-Import file": SOP §3.2 defines the Group ID
`(<ConceptID>) <BG|IG|AG>##`, and §6.1 says `group_display_name` carries
"the group ID" and `group_name` is "same as group_display_name". Both
visible names therefore carry the group ID itself — the exact value
`group_key` already holds — and Q12's friendly "*Concept name — Tier*"
projection is retired everywhere it was composed: the release group
records, the release-freeze validator (which now checks both names equal
the group ID), the required-shell completer, the CMS export writer (which
composes the ID for legacy rows whose stored names predate this ruling),
tagging, the legacy Build-Assessments append lane, post-generation
synchronisation, and the deposit shells (named once the concept's machine
identity settles — never by front-running the publication lane's carried
ids or position-anchored minting).

One composer owns the format: `identity.compose_group_key` (with the
`GROUP_TIER_CODES` alphabet beside the rest of the id grammar), which
`assessment_grouping.group_key_for` — every lane's entry point — now
delegates to. Q12's second half (level calls and variant clustering as
model verdicts, Q10 dissent flags, flagged singletons) stands untouched.

### Q17 · Decided — a picture-bank question ships ONE stitched labelled figure; MCQ options keep their own images

Owner ruling, 21 Aug 2026 ("Build it", after the difference was explained
on the job-61 example: a classification question rendering eight separate
`[img]` links — Drum, Table surface, Reed pipe, Carrom board, Harmonium,
Road, Playground, Spinning Top). A question whose body carries two or
more canonical image tags ships ONE deterministically stitched, labelled
grid image — tile order = tag order, tile label = each tag's own alt
text, near-square layout, content-addressed JPEG pinned to the durable
source-asset store and served from the app's signed `/source-assets`
route. Objective/MCQ items are exempt: each option must render its own
image, and the exemption is keyed on RECORDED kind (`sheet_kind` in the
assessment lane; the inventory item's recorded `options` upstream),
never on what any picture shows (Rule 1: this pass is pure mechanics).

Failure semantics: a stitch that cannot complete (a download fails, the
public base URL is unconfigured) leaves the text EXACTLY as it was and
rides the candidate as `assessment_image_grid_review` with the error
named in the `_aegis_image_consolidation` audit — never a blocked
Master, never a half-stitched bank.

Build stages. Stage 1 (built): `question_image_grid` + the assessment
lane — both Masters stitch at the run, after materialization and before
the learner-text freeze, so every later stage sees the final
single-figure body. Stage 2 (designed, next): the concept workbook's
Example lines — the ruling's original surface — must stitch at the
INVENTORY item (a `_combined_image_url` stamp consumed by
`_inventory_task_text`, the one composer every coverage key, boundary,
and Example flows through), with the coverage ledger's figure accounting
taught that a source URL inside a consolidation record whose combined
URL is placed counts as placed, and a staleness guard so an inventory
refresh that changes `image_urls` silently retires the stamp. Stitching
any later than the inventory breaks exact-coverage identity; any
earlier, the refresh passes undo it — which is why stage 2 is its own
careful change rather than a rider on stage 1.

### Q18 · Decided — prerequisite-recap material is pre-learning's territory; its source questions leave the Post Master by a recorded claim

Owner ruling, 21 Aug 2026 (option b, chosen explicitly over keeping recap
exercises in the Post Master). Some textbooks open with
prerequisite-recap material — revision of earlier-class learning the
chapter re-activates before its own teaching. The classification is a
MODEL verdict, never position: the owner's own counterexample is the RNE
chapter's Frederic Sorrieu opener, which sits first and is genuine
chapter teaching — it must stay Post. "First blocks" is exactly the
shape-matching Rule 1 forbids.

Stage 1 (built): `assessment_prelearning_claim` — one recorded
per-chapter verdict over the complete source-atom set (critic-advised,
Fixer-backed, decide-once, `assessment-pre-claim-1`), run in the Post
Master's source lane BEFORE any cell verdict is paid for. Claimed
questions leave the Post Master and ride the release payload under
`pre_learning_claimed` with their reasons — a recorded, reviewable
disposition that names the release *Ready with flags*, never loss (R4).
An empty claim is a legitimate verdict; there is no quota. Two standing
rules are explicitly unchanged: the 17-Aug steer (no source question is
ever lifted into any Pre artefact — Output 04 stays generated-only and
its leak barrier still refuses every source qid, claimed or not), and
Rule C's exactly-once accounting, which this amends for exactly this
case the way Q15 amended flag-only: exclusion with a full record.

Stage 2 (designed, lands with the parallel-tracks build — Q19): the
verdict moves to inventory time so the CONCEPT side follows too —
recap sections author no Post concepts, their content feeds the Pre
lane's capture as evidence, the coverage ledger records the claim as a
disposition (never "missing"), the source-topic recovery and
deposit-boundary guards treat a claimed heading as accounted, and Type
mining exempts claimed items. That half threads the human-pause and
exact-coverage machinery, which is why it is staged rather than rushed.

### Q19 · Decided — the run restructures into parallel tracks; the Type-granularity pause halts everything

Owner rulings, 21 Aug 2026, in the cost/time review (a full run measured
~2 hours; the account's provider limits support far more concurrency
than the pipeline can generate).

**Track A — BUILT (2026-08-21).** The question-inventory extraction
(per-chunk itemization, completeness verdicts, anchors, figures, qid
numbering) reads nothing but the sections, so it forks on a side thread
BEFORE the skeleton and runs beside the skeleton/description passes
(`_EarlyInventoryTrack`; the extraction function is split at its
`records` seam into a pre-join half and a finish half whose composition
is byte-identical to the sequential build). The join is the
`question_inventory` checkpoint: topic assignment, then adjudication, in
the exact sequential order, so every decision payload is unchanged. The
track keeps NO checkpoint of its own — a crash or pause before the join
loses only wall-clock, exactly as a pre-checkpoint crash always has, and
resume semantics are untouched. Workers=1 restores the strictly
sequential path.

**Track 3 — corrected, NOT built.** The initial sketch ("fork the Pre
chain after Settle/Host") is WRONG against the code and is withdrawn:
the prerequisite merge requires all four stage captures including
Place's and Analyse's (decided only when those lanes finish), and the
premap's needed-for links read the ASSEMBLED, POLISHED Post rows — so
the Pre chain's tail position is load-bearing recorded-evidence design,
and moving it would re-key every Pre decision onto weaker evidence. The
chain is also already internally parallel (per-concept question
authoring, premap batches, preanalyse batches all fan out under the
worker pool), so there is no idle time to reclaim inside it. The Pre
MASTER'S build does overlap the Post's — the concurrent Masters below.

**Concurrent Masters — BUILT (2026-08-21):** per-lane database sessions,
per-lane `assessment-<lane>/` audit snapshots (the unscoped filenames
silently overwrote the sibling lane's snapshots even sequentially), and
a lock-serialized failure recorder preserving the Q13 one-lane-fault
guarantee.

The pause ruling: when a human gate fires (the Type-granularity
pre-spend pause, or any raise between fork and join), a parallel run
HALTS BOTH tracks — the early track stops before its next chunk;
in-flight provider calls finish and nothing new spends. Recorded
alongside: the owner will later add a Batch-API overnight lane, and
directs that PDF reading converge on ONE reading pass — the GPT
PDF-to-ACSD path — rather than an external Mathpix MMD conversion.

### Q20 · Decided — pre-learning coverage calibrates to ~5 per concept under a diagnostic posture (amends Q4)

Owner ruling, 21 Aug 2026, in the Master cost review (the first chapter
run at the ~10 calibration planned 69 generated questions for 10
pre-concepts, and every planned question rides the full per-question
Master pipeline — the Pre lane was 3.35× the Post lane's spend).

The recorded calibration is now **about 5 questions per pre-concept,
with the split across the tiers left to the model's judgment of the
prerequisite**, superseding the ~10 (5 Basic + 5 Intermediate) steer of
2026-08-20. Alongside it the owner records a **diagnostic posture**:
plan the minimum coverage that genuinely verifies the prerequisite —
prefer fewer, more diagnostic questions, each earning its place by
testing something the chapter's learning actually depends on, over
breadth or drill.

Q4's frame is unchanged and this entry inherits all of it: the figure is
a recorded target, not an observed practice of any school, board or
grade; neither mandatory quota nor maximum; every plan carries an
authored rationale unconditionally; a thin pre-concept is never padded
and a rich one is never capped; a plan of zero stays legitimate; an
explicit blueprint may override. The norm and the posture live in
exactly one place — the labelled CALIBRATION/POSTURE prose inside the
plan payload's rules — in no constant, checker, threshold, default or
comparison, with the norm-location regressions re-pinned to the new
wording. The plan critic's ANCHORING dimension keeps watching that plans
stay evidence-led rather than drifting to the stated figure.

### Q21 · Decided — answer cells use one declared medium; options are lowercase; KaTeX tables are retired

Owner ruling, 22 Aug 2026, from the generated Master review. A typed answer
or rubric cell is rendered by the CMS from its declared `answer_type`, so the
whole cell uses exactly one medium:

- `Equation` carries full raw LaTeX with **no** `[Katex]` wrapper. Any words
  needed inside that equation are LaTeX text atoms such as `\text{...}`;
  loose prose plus a wrapped/raw fragment is invalid.
- `Phrases` carries wholly plain text with no LaTeX, `[Katex]`, math
  delimiter, image tag, or link markup. A rubric may choose Equation or
  Phrases block by block, but one block never mixes the two.
- A four-mark Descriptive answer has at least two rubric blocks. One rubric
  block carrying all four marks is invalid.

Objective options render in the question paper as lowercase `a)`, `b)`,
`c)`, `d)` (continuing alphabetically when the recorded answer list is
longer). Option order and correct-answer identity remain unchanged.

*Table clause superseded in place (P10 in Q23, confirmed by contract §34 per
Q26): a table ships as one complete canonical KaTeX `array` or one tight
complete crop; the coordinate transcription below is a transport encoding
only, never a shippable rendering.* Original text: KaTeX `tabular`/`array`
environments and Markdown pipe tables are unsupported and must not ship.
When the source already associates an image with that table, the semantic
author preserves that source image. Otherwise the fallback is a mechanical,
lossless row/column transcription (`Table row N, column N: value`) preserving
every cell in order. Local code may label those coordinates and unwrap
literal table-cell text, but it never infers headings, fills a missing cell,
or reconstructs meaning.

This ruling supersedes only the conflicting formatting examples in the older
accepted-workbook fixtures and SOP-derived rich-body interpretation. It does
not change the rich-text contract for untyped `question`, `display_answer`,
or `answer_explanation`, which continue to use `[Katex]...[/Katex]` around
math. It is a serialization/shape contract: medium choice and rubric content
remain model-authored; code enforces only declared type, lexical format,
cardinality, and exact marks mechanically.

### Q22 · Decided — every registered Luna purpose requests xhigh

Owner ruling, 23 Aug 2026: keep Luna at `xhigh` throughout the pipeline. All
14 values in `OpenAIPurpose` therefore request `xhigh`, including extraction,
transcription, metadata, author, critic, Pre-Learning, and workbook work.
Purpose labels remain mandatory for routing, transport coverage, usage audit,
and future policy decisions; they no longer select different effort tiers.

`xhigh` is the preferred normal request, not a command to remove reliability
recovery. If an endpoint rejects it as unsupported, the shared capability
negotiator may step down `xhigh` → `high` → `medium` → `low` → `none` and
finally omit the parameter, caching the accepted ceiling process-wide. A
structured response that truncates may likewise retry with more output room
and one rung less reasoning. On ordinary GPT-5.6 Luna calls neither exception
should apply, so the request remains `xhigh`.

Reasoning effort is transport policy, not prompt, schema, payload, or durable
decision-key identity. Existing decide-once records therefore keep their keys
and replay without model spend; only fresh calls and true cache misses use the
new preferred effort. No cost or quality improvement is claimed from this
ruling alone: purposes formerly at `max` move down one rung while purposes
formerly at `high`, `medium`, or `low` move up. The same-PDF live acceptance
must measure the net effect before any production claim.

### Q23 · Decided — the 2026-08-29 audit-round owner decisions (D1–D4, P10, the Question Duration Matrix)

Owner rulings, 29 Aug 2026, from the Concept Mapping Audit review round:

* **D1 — KaTeX support list.** The CMS platform renders `\hspace`, `\phantom`,
  `\boxed`, and dimension row spacing (`\\[0.12 cm]`); only `\mathrm` remains
  unsupported (rewritten to `\text{...}` where the body is plain, refused
  otherwise). A NON-dimension row-break bracket argument stays a defect. The
  shared KaTeX rules prompt re-registered under `content.katex_rules.v2` so a
  stale Admin override cannot resurrect the old bans.
* **P10 — table house style.** The owner-corrected canonical array shape —
  one `[Katex]` wrapper per cell, `\text{...}` prose, `\\[0.12 cm]`
  prose/array spacing, pipe columns (`{|c|c|c|}`), `\hline` around every row,
  `\phantom` placeholders sized like a plausible real entry — is mandatory on
  every prompt surface; coordinate-labelled table prose is a transport
  encoding, never a shippable rendering.
* **D2 — durations.** The owner's uploaded Question Duration Matrix is the
  authority (it matches the earlier written table; the corrector's
  duration-equals-marks practice on SA-3/LA rows is overruled). Scope ruling:
  the matrix binds MH Board (MSBSHSE) at EVERY grade, per subject
  (Math+Physics, English, Social Science policies); grade-scoped policies
  layer on top, so Class 6 keeps its audited closed sets — the ban on Case
  Based / Assertion & Reasons included — and Mathematics keeps Match the
  Following / True or False / Fill in the blanks at 1 minute per sub-point.
* **D3 — `is_update_*` polarity.** "No" on populated bands of every output;
  the Mathematics corrector's "Yes" is rejected.
* **D4 — Pre `related_concepts` ships EMPTY** (supersedes OD3's fill half and
  T3.3/T3.3b's populated column; lane- and marker-keyed so legacy payloads
  publish empty too). Needed-for links are still resolved at staging and an
  unresolvable link stays a recorded review flag. Post rows remain blank
  until a relations pass exists. `keywords` still ships filled per OD3.
* **D8 — a Culmination concept exists only for a topic with two or more
  concepts.** A single-concept topic never carries one — one concept has
  nothing to consolidate. Enforced at AUTHORING time (the culmination
  prompts and the merge pass never mint a single-concept culmination) and
  flagged by the validator (`culmination_single_concept`, warning). A
  legacy row from a pre-D8 checkpoint whose grounding certificate already
  attested it ships flagged, never dropped — dropping an attested row
  breaks certificate lineage and refuses finished work (R4).

### Q24 · Decided — a rich-text source pending whose only settlement is the measured dead end fails the run fast, with the remedy named

Owner ruling, 29 Aug 2026 ("b2 fail", answering the PR #267 review finding
that the "keep the pause" guard delivered nothing on the unattended path).
When an unattended run reaches a `phase3_source_graph_review` pending of
type `semantic_source_rich_text` and the only recordable settlement is
`carry_forward` — the [measured] dead end the semantic-graph integrity gate
refuses downstream (job 'Electricity', Class 10 Ch 5) — the run ends NOW
with the named remedy: convert the PDF again as a new upload (sources
converted before the `\mathrm` ingestion canonicalization of PR #267 are
cured by reconversion; a pause that returns on a fresh conversion means the
block genuinely needs a corrected source). Enforced at the ONE choke point
every automated settlement passes through — the decision recorder
(`_record_human_semantic_decision_locked`) refuses an agent-recorded
`carry_forward` for this pending shape — with earlier guards at the doors
for better failure locality and to avoid wasted spend: the last-resort safe
continuation, the loop-detection/safety-cap forced-safe branches, and the
Fixer's choice set (which loses `carry_forward` for this shape, is told so
in its contract text, and fails fast without spending the Fixer call when
nothing else is applicable). A HUMAN's explicit `carry_forward` on the
sanctioned source-review pause remains that person's call.

This narrowly amends Q13's always-complete posture for exactly this
settlement — completing was an illusion here: the run either failed later
at the integrity gate after spending, or looped through resumes. Fresh
conversions cannot reach this state; the ruling is a legacy-source
condition with a legitimate cure, in the spirit of Q13's "genuine
impossibility" (the source, as converted, is unusable).

### Q25 · Decided — the 2026-08-29 restructure approvals: A (terminal verdict at staging), B (one-shot upload + full text), C as a phased plan

Owner approvals, 29 Aug 2026 ("A - Yes, B - Yes, C - Yes"), after the owner
directed a restructure of the Master-blocking check rather than another spot
fix, plus the upload-UX and MMD-visibility complaints:

* **A — the terminal verdict is decided ONCE, at staging** (shipped, PR
  #272). `stage_release`/`stage_pre_release` record an explicit
  `terminal_generation_complete` on the staged payload and its summary; the
  Pre sibling carries the Post run's verdict. Master eligibility and the
  database-publication act READ the recorded fact and never re-derive it
  from live checkpoint state. Legacy payloads are backfilled once from
  durable evidence, accepting the LIVE `final_content_ready` checkpoint
  where the payload's echoed stage lags it — the measured 'Patterns'
  staging-order race — so completed pre-A runs unblock retroactively. The
  field sits outside the Master seal's key allowlist by design.
  `concept_checkpoint_terminal_diagnosis` remains a troubleshooting helper
  only.
* **B — every run parameter is chosen before upload** (shipped, PR #272).
  Chapter target, model provider, and source book sit in one view; with a
  chapter picked, one action runs upload → convert → generate (explicit
  steps remain the fallback). The 800-character MMD preview is replaced by
  a full converted-text viewer (expand, whole-text search with counts and
  highlighting, `.mmd` download) — the stored text was always complete.
* **C — block JSON becomes the single source authority; MMD demoted to a
  generated view.** Approved as a PHASED plan, recorded in
  `docs/aegis-restructure-c-plan.md`: C1 compile-from-blocks (shadow mode
  first — the owner sees the shadow diff before cutover; shadow
  implementation began with PR #273), C2 page identity end-to-end, C3
  block-sequence chunking, C4 non-PDF parity (needs its own go/no-go with
  cost). Each phase validates against the audit corpus.

### Q26 · Decided — the Master Governing Contract v2.0 is the output specification; the run cost policy

Owner ruling, 4 Sep 2026: the owner's **Master Governing Contract v2.0**
(document AEGIS-MGC-2.0-20260904, checked in verbatim as
`docs/aegis-master-governing-contract-v2.md`) is the specification every
output, validator, exporter, prompt and review surface is built to. Its §2
precedence rule binds: where this register, a SOP, a prompt, a validator or
a sample disagrees with it, the contract wins, and the contract itself is
board-, publication- and grade-agnostic — its Grade 6 MSBSHSE calibration is
a calibration artefact, never a default. The owner's second ruling of the
same day: a chapter costing USD 8–10 to run is not what the pipeline needs;
remove what is not necessary. What this entry decides, and what it
supersedes:

* **Workbook contract, universal.** Every lane and every board ships the
  update-aware schema (72/380/149 as first frozen here; 72/440/149 since
  Q27 accepted the CMS template — `update-aware-master-2`; formerly named
  after MSBSHSE grade 6) with all five `is_update_*` fields `No` on EVERY
  authored row — questionless concept tails included (amends Q23 D3's
  "populated bands" wording); three sheets (Objective, Descriptive,
  Subjective) in every output, unused ones header-only; multi-value cells
  use the exact ` | ` separator (comma/semicolon joins retired at every
  writer, reader and read-back; legacy comma cells still import); in-cell
  line breaks are `<br>` (never a raw newline); True/False is a Subjective
  format everywhere (the closed one-key option set alone is Objective);
  `question_appears_in` is the literal set; `question_source` is the run's
  publication and nothing else (the filename and "UpSchool DB" fallbacks
  are gone — a blank publication is a read-back blocker, never guessed);
  `concept_source` likewise. Outputs 01/03 (the Concept File) render on the
  same update-aware layout as the Master, and the layout every release row
  records (`release_core.layout_id()`) is that one; the reference workbook
  `sop-mes-1` stays registered as a read-side (import) layout only. The
  one-time re-delimiting of stored legacy lists (db backfill) splits
  rosters of identified titles — pre/post topics, related topics and
  concepts — by the §15 identity grammar (every item closes with its
  `(id)` tag), so a comma inside a title survives the migration; plain
  lists (keywords, digicards, sources) split on the legacy comma. Board
  profiles are explicit layers over the universal default, never the
  default itself.
* **Answer and rubric semantics.** An Objective explanation BEGINS with the
  exact correct-option text and never names a letter or a number; a
  Descriptive `display_answer` equals `answer_explanation`; a Subjective
  True/False projects as a placeholder-bound answer; every rubric criterion
  carries exactly 0.5 or 1 mark (the marking checker refuses any other
  quantum, the reader stays lenient on legacy imports); rubric tags come
  from the English registry only (`[content] [evidence] [reasoning]
  [organisation] [language] [creativity] [accuracy]`; `[creative]` is
  invalid) and appear only in English Descriptive criterion fields — a tag
  anywhere else, or in any non-English rubric, is a materialization defect.
  `question` carries the learner text with `$x$` blanks projected as
  `____` for Subjective rows and the multipart stem plus sub-questions for
  Descriptive rows (this restores stem-only `question` and retires the §6 D4
  "both carry the full wording" reading). Answer-type cells carry the lane
  literal (§22–§24): `Words` on Objective options and Subjective answers,
  `Phrases` on Descriptive criteria and keywords, `Equation`/`Image`
  unchanged; inside the pipeline the one canonical textual medium stays
  `Phrases`, the reader accepts both spellings, and the writers project the
  literal at the cell (the parked D6 "Words → Phrases fold" is thereby
  registered and retired for export). Numbers display as `0.##` (§42.10,
  supersedes the A11 "1.0" display).
* **Other register text superseded in place.** Q21's table clause (already
  overruled by P10 — contract §34) is annotated where it stands; the
  "adaptive-40" wording of §4 Phase 03 is struck; Q11's "v2.0" means the
  Open/Specific *registry* v2.0 (`docs/open-specific-registry-v2.md`, model
  evidence, never a lookup), not this contract — and under §31 no subject
  family holds a standing Open stance: a bounded target is Specific with
  recorded accepted equivalents, the registry text stays evidence.
* **Frozen run variables.** `chapter_duration` comes from the accepted
  registry (exact match) or the explicit upload variable
  (`chapter_duration_minutes` on the upload form and API) and from nowhere
  else: the model-estimated duration is retired, the cell ships blank when
  neither exists, and `chapter_duration_unregistered` blocks the database
  write until one is supplied (§32.1). The publication is a run variable
  the same way (§18).
* **Pre coverage (amends Q20, keeps Q4 and the 2026-08-21 posture).** The
  plan prose no longer quotes any numeric target: contract §8 is "complete
  diagnostic coverage of the Mastery, at least one routed question, no
  quotas". The unconditional rationale, the anchoring critic dimension and
  the never-padded rule stand.
* **Cost policy (amends Q22).** Reasoning effort is chosen by a named
  profile (`AEGIS_OPENAI_REASONING_PROFILE`, default `tiered`): semantic
  authoring and adjudication request `high`, the independent advisory
  critic — now its own purpose, `advisory_critic`, on every live critic
  adapter — requests `medium`, transcription/outline/refinement request
  `medium`, metadata requests `low`; `uniform-xhigh` restores Q22 for A/B
  measurement on the same source. Reasoning effort remains transport
  policy: no decide-once key changes, and Q22's step-down negotiation
  stands. In the Master lane the per-decision critics on cells,
  materialization, answer restriction, marking, level, cluster and
  description are OFF by default and replaced by ONE joint per-item review
  after marking (contract §27 step 6: question, answer space, model answer,
  criteria, accepted equivalents, arithmetic — `assessment_item_review`,
  advisory, never a gate, Q10); the route, dedup and pre-claim critics stay
  on (`AEGIS_MASTER_CRITICS` = `all` | `none` | a list); the Master Refiner
  and the touched-group QA are opt-in (`AEGIS_MASTER_REFINER`,
  `AEGIS_MASTER_GROUP_QA`); the concept-row Refiner (§8.3) refines only
  rows carrying a recorded review flag (`AEGIS_CONCEPT_REFINER` =
  `flagged` | `all` | `off`, contract §38 stage 10: re-review of what a
  verdict touched). A pure `[confidence]` shortfall ships flagged after ONE
  attempt instead of three — the prompts forbid inflating a score to pass a
  threshold, so a re-ask on the same evidence could only buy an inflated
  number. Nothing here moves a semantic verdict into code (Rule 1); only
  the number of second passes and the effort they request change, and each
  former behaviour is one environment variable away for measurement.
* **Recorded, not yet done** (the six-area cost map of 4 Sep 2026, for a
  follow-up entry): the legacy skeleton lane authors full descriptions that
  Settle re-authors; Chapter Reading rewrites the chapter verbatim as
  output; the PDF lane sends each page's text layer twice per call; chapter
  metadata is authored up to four times per chapter; Host re-sends every
  concept's full details per batch; Phase 3 critics run per batch rather
  than per stage. Each is a transport or duplication cut compatible with
  Rule 1; none is taken here because each re-keys stored decisions or
  touches the legacy lane's prompts, and the owner should measure the
  effect of this entry's cuts on the same source first. Also recorded: the
  contract's §7.2 "repeated source occurrences remain distinct" reads
  against the Post source-duplicate verdict (P3) and the pre-learning claim
  (Q18); both stay on until the owner rules, since the 2026-08-29 audit
  measured real double-shipped extraction artefacts. Also recorded as owed
  and not done here: the §10 concept house format (`<br>` between sections,
  numbered `Types:`/`Cases:`/`Examples:` lists with cross-references and a
  `Question label:` line under each Example — the ` // ` house format of §5
  still ships and its validators still read it); §26.1's ban on empty
  BG01/IG01/AG01 group shells for questionless concepts (the shell
  completer of Q16 still runs); the release half of §43–§44 (a critic
  dissent the critic itself marks critical blocks publication — today every
  dissent is advisory, Q10 unchanged); and §19.2's model parentage verdict
  for independent enumerations (Phase 2.3's "never split" still governs).
  Each is a contract requirement that touches the authoring prompts, the
  concept validators and the refiner whitelist together, and is the next
  slice.

**Stands:** Q1, Q2/Rule C, Q3, Q5 (as the base layout the update-aware
profile extends), Q6, Q8, Q9, Q13 (read as above: complete every stage, stage
every artefact, publish nothing with a blocker), Q14/Rule B, Q15, Q16's
naming, Q17 (the asset route is anonymous; "signed" was wording), Q18, Q19,
Q21's non-table clauses, Q23 D1/P10/D2 (D2 as the explicit MSBSHSE profile
layer; True/False durations read per row), D4, Q24, Q25, D5, D7.

### Q27 · Decided — the physical CMS template of 4 Sep 2026 is the accepted fingerprint; one source on every output; Post questions verbatim

Owner rulings, 4 Sep 2026 (later the same day as Q26), three of them:

* **The physical template supersedes the quoted widths.** The owner
  supplied the CMS's current bulk-upload template
  (`Bulk_Upload_New_Format.xlsx`, SHA-256 `a199ffe3…cfcc972`, fingerprint
  transcribed byte-exactly in
  `backend/app/bulk_import/templates/bulk_upload_new_format_2026-09-04.json`)
  "so you are aligned with the same". Contract §14 lets a later physical
  template supersede its widths once the full fingerprint is explicitly
  accepted for the run; this entry is that acceptance. Its geometry is the
  update-aware schema with **30 Descriptive answer blocks on every subject
  and lane — 72 / 440 / 149** — so that is now the one universal output
  layout (`update-aware-master-2`) for all four outputs on every board; the
  380-column `update-aware-master-1` stays registered on the read side
  only, so an already-published workbook still imports, and the
  English-Post-only 440 variant of §14 is retired as a variant because it is
  simply the universal shape (the `english-post-master-expanded-1` contract
  id is gone; the MSBSHSE grade-6 board layer keeps only its aggregate rules,
  re-frozen as `msbshse-grade-6-master-2026-09-04`). Recorded, not
  reproduced, because §14 and B.3 forbid them in a write: the template
  carries `chapter_display_name` twice on Subjective and Descriptive,
  seventeen copy-pasted answer-block header cells between Descriptive
  blocks 9–13, a duplicated trailing `sq15_keyword_6`, three
  `is_update_*` headers with a trailing newline, and its sheets in the
  order Objective, Subjective, Descriptive where §12 says Objective,
  Descriptive, Subjective. The generated outputs carry every header once,
  trimmed, in the contract order; the template repair and the sheet-order
  question are the CMS side's, and `test_bulk_upload_template_fingerprint`
  proves column for column that the template's intended geometry and the
  registered layout are the same thing. A repaired template changes that
  pin deliberately.
* **One source, the upload page's.** `concept_source` on Outputs 01/03 and
  `concept_source`/`question_source` on Outputs 02/04 all carry the run's
  publication — the Source book typed on the Build Concepts page and
  frozen on the job (§18) — never a concept's accumulated provenance list
  (`concept.sources`, which stays the database's record of every book a
  concept was built from and still exports on the run-less accumulator
  workbook), never a filename. The chapter's human tag names the same
  publication.
* **Post questions verbatim (narrows §7.3).** "Post learning questions
  should be the questions exactly present in the source, no creation by
  its own." The Post lane already generates nothing (§7.2, the generated
  lane is Pre only); what changes is the polishing latitude: a
  source-owned question is the source task's wording verbatim, and exactly
  four mechanical changes remain — page apparatus dropped, an outside
  pointer replaced by the referenced content copied verbatim, notation
  and blanks projected into the workbook's forms, an activity carried as
  stated. §7.3's "correcting grammar … an obvious wording defect" column is
  withdrawn: a grammar slip in the source ships as printed and is noted in
  the rationale. The materialization author (`assessment-materialize-14`),
  its advisory critic and the joint item review
  (`assessment-item-review-2`) all hold the rule; per Rule 1 the check that
  a question IS the source's wording is the reviewer's verdict, never a
  string comparison. The legacy "Build Assessments → from Concept Mapping"
  generator is untouched by this entry: it is a blueprint tool outside the
  four outputs, and whether it stays is an open question for the owner.

**Stands:** everything Q26 lists, with §14's widths and §7.3's permitted
column read as amended above.

### Q28 · Decided — the technicality layer the corrected outputs demonstrate

Owner input, 6 Sep 2026: the corrected English and Mathematics outputs for
eleven Grade 6 MSBSHSE chapters, each with its source chapter and, for
seven of them, an errors-and-corrections log. The instruction was to follow
the **technicalities** of the corrected outputs against the source files and
write only that layer. This entry records what that layer is, and — per §2
— what in those files the contract overrules rather than blends.

**Implemented (mechanics only; every one is a cell shape, never a meaning):**

* **All five `is_update_*` cells read `No` on the Concept File too.**
  §14.1's "even when the corresponding later entity band is otherwise
  blank", restated by Q26. `writer._concept_to_row` padded everything past
  the Concept band with `""`, so Outputs 01/03 shipped `is_update_group`
  and `is_update_question` empty while the Master stamped all five and
  refused anything else at read-back. The tail is now composed BY FIELD
  NAME (`_concept_row_tail`), and the Concept File read-back gained the
  Master's §42 gate-2 assertion. Evidence both ways: the contract, and the
  owner's corrected Post Concept file for Radha's Letter to Mowgli, whose
  Group and Question bands are empty beside an `is_update_*` pair of `No`.
* **The Concept File read-back addresses the layout it wrote.**
  `write_concepts_workbook` writes on `update-aware-master-2` (72 columns)
  and called its validator without the layout, so the gate fell back to the
  67-column `sop-mes-1` and compared shifted columns — `topic_title` 6
  against 7, `concept_title` 12 against 14. Both call sites now pass it.
* **Numeric storage, §32.** A weight, mark or duration the marking author
  returned as JSON text reached the cell as text: the verdict is
  deep-copied verbatim and nothing coerced it, so `answer_weightage_1`
  shipped as the string `"1"` with General format while `marks` on the same
  row — cast to float upstream — carried a number and `0.##`. One coercion
  at the shared cell seam (`_numeric_cell`, in `_row_values`) converts a
  value that ALREADY is an exact number written as text and leaves anything
  else untouched for the existing gates to name. The written text decides
  int or float, so `"1.0"` stays `1.0`. `chapter_duration` joins the
  `0.##` display set — the A11-era exclusion note it carried described a
  calibration Q26 superseded — and the Concept File applies that format
  too (`writer.apply_numeric_formats`), which it never did.
* **Parent lists resolve inside the file** (§13, §42 gate 4, ID-001).
  `_dangling_reference_errors` resolves every `concept_question_labels`,
  `group_question_labels` and BG/IG/AG token against the identities the
  workbook itself carries. The existing aggregate checks compare a rollup
  with the SNAPSHOT and so cannot see a token whose row is absent — the
  defect the Love for One's Motherland log records, where a concept kept
  listing `… Q01` after that question was gone and "the workbook now points
  to a question label that no longer exists anywhere in the file".

**Recorded, NOT implemented — the corrected files disagree with the
contract, and §2 forbids blending:**

* **Pre-lane `chapter_duration`.** The Mindfulness log clears it on the
  Pre file ("360 minutes is the teaching time allotted to the Post-learning
  chapter"), while §32.1 says the chapter duration is "frozen once per
  chapter and repeated identically across all four outputs". **Ruled by
  the owner, 6 Sep 2026: "let the chapter duration be as it is, as in the
  existing code."** §32.1 stands unchanged; the value repeats on all four
  outputs, and the Mindfulness clearing is a calibration defect under §45,
  not a rule. Closed.
* **Comma-delimited lists** in the older corrected Concept files
  (`keywords`, `topic_concept_labels`, `post_topics`). §16 fixes the
  delimiter as exact ` | `, the corrected *Masters* already use it, and
  §45 makes an observed defect in a calibration a defect still.
* **Rubric criteria above the 0.5/1 quantum** (a 6.0 weight in the
  corrected Radha's Letter Master) and **untagged English Descriptive
  criteria** beside tagged ones in the same cell block: §27.5 and §28
  govern, and both remain enforced.
* **Task-specific `question_category` literals** the corrected Masters
  introduce — `Rearrange the following words`, `Identifying the following`.
  §25 requires the profile to freeze exact legal category strings before
  authoring, so adding them is a profile amendment the owner makes, not a
  mechanical fix; the cell checker refuses them today.

**Stands:** everything Q26 and Q27 list. Nothing here moves a semantic
verdict into code (Rule 1): every change is a cell's type, marker or
resolution, and no content decision changed hands.

### Q29 · Decided — the Pre lane is bound to the run it was authored for

Owner input, 6–7 Sep 2026: "the pre learning built wasn't so great either"
— audit the Pre lane against the previous PRs for what the handoffs
dropped. The audit ran over the same eleven-chapter corpus as Q28 and over
the code at HEAD; the three findings below were confirmed in both places
(the corpus reproduces them, and the code trace names the seam). Each is a
mechanic: an identity comparison, a delimiter, a count against one. No
content decision changed hands (Rule 1).

**Implemented:**

* **The Pre authority records the run it belongs to, and staging refuses
  another run's.** Corpus: the School Bell Rings Again run shipped Self
  Help Is the Only Way's Pre Master (`aegis_master_REL-8b37df…_v1.xlsx`,
  every row carrying `06MSEN_SelfHelpIsth_…_PrL` identities) beside its
  own, correct, Pre Concept file — and all eight School Bell Pre concepts
  shipped with zero questions. Code: the Pre bundle carried no chapter,
  the sidecar restore read a process-scoped ContextVar's directory rather
  than the job's, staging preferred whatever arrived first, and Output
  02's manifest entry never compared the frozen Master's lineage with the
  staged payload's. Now `premap.build` stamps `run_identity` (the
  envelope's frozen `chapter_id`/`chapter_code`, `source_contract_hash`,
  `envelope_sha256`) on every map, including a refused one; the release
  bundle lifts it; `stage_pre_release_from_run` compares every authority
  in its chain against the chapter being staged
  (`generation.pre_release_identity_defect`), refuses a mismatch, records
  it under the payload's own `pre_authority_defects` key with the issue
  `pre_learning_authority_not_this_run`, and falls through to the next
  authority — a refusal that leaves no rows blocks the database write
  (Diagnostic); one that a later authority of this run repaired is a
  warning. `restored_pre_release` takes the job's directory explicitly
  (the four-output deposit handoff passes the audited job's; the Phase 3
  session is only the fallback for a caller with no job) and refuses a
  map whose recorded source contract is not the envelope's beside it.
  `master_entry` keeps the entry present and disabled with
  `MASTER_STALE_FOR_RUN` when the live Master's frozen
  `staged_release_uid` is not the staged payload's, and the publication
  gate's silent `continue` on that lineage mismatch is a note on the
  receipt (`identity_review_flags`). Every comparison is dormant, and
  says so, for an artefact recorded before the field existed — the same
  posture the Master lane's seal gate took for pre-seal rows.
* **`keywords` on Pre rows is a `" | "` list.** Corpus: 100 % of Pre rows
  in five chapters read `['connected verse', 'reading fluency', …]` while
  the same run's Post rows read the clean form. Code:
  `premap.py` `str()`-ed a JSON-array answer into the cell, PREMAP_SYSTEM
  named `keywords` without a format, and the writer's `_list_cell` kept
  the repr as one token. Now `premap.keywords_cell` joins a list on the
  exact delimiter (§16), PREMAP_SYSTEM says the shape in the Post
  prompt's words, `bi.list_token_defects` names a bracketed, quoted
  literal beside the DEL-001 pipe check (so the Master read-back sees it
  through the call it already makes), and the Concept File read-back
  checks the same `MULTI_VALUE_FIELDS` with the same function, recording
  a `bulk_import_readback_list_cell_defect` decision.
* **A Pre concept with no routed question is a blocking QC finding
  (§8.6).** Corpus: every School Bell Pre concept, and the hand-built Pre
  Masters the owner had to write. Code: the plan prompt licensed a zero
  plan ("plan zero only when that is true"), and nothing compared any
  concept's routed count to one — the row rendered as a questionless tail
  and the Pre lane's QC accounting skipped items entirely. Now
  `release_qc.PRE_CONCEPT_UNASSESSED` blocks the database write (every
  download ships) for each Pre row whose `_aegis_pre_generated_questions`
  is empty, transcribing WHY from the lane's own records — the plan's zero
  total, which the prompt now names as the model's recorded request to
  DROP the concept (a prerequisite with no Mastery worth verifying should
  not have become a Pre concept), with its rationale; or the recorded
  authoring block. Nothing drops the concept: the run records the
  request, the reviewer removes the concept or re-runs. The prior pin
  "a lane that authored no question is a flag-free Ready release" is
  retired by this entry; the R4 half it protected — a readable
  "authored none" is not an unreadable snapshot — stands and is still
  pinned.

**Also recorded here (PR #291, 7 Sep 2026, jesc112.pdf):** the four
Figure-citation gate codes (`phase2_unresolved_figure_reference`,
`phase2_ambiguous_figure_reference` and their legacy spellings) joined
`_PHASE2_ADVISORY_GATE_CODES`. `figure_citations_ship_for_review()` had
emitted them as warnings since the rewritten pipeline, but the
advisory/fatal split never listed them, so a verified 13-page extraction
was refused over two unmatched "Fig." citations. §7.1.5 asks for every
unresolved reference to be resolved before RELEASE; the review flag
carries it there. Error severity still fails closed.

**Stands:** everything Q26–Q28 list. The Pre lane's authoring — how many
prerequisites, which concepts, how many questions each — remains the
model's; this entry only makes the run unable to ship another run's
answer, a list as a repr, or a concept it never assessed, without saying
so.

### Q30 · Decided — the owner's Pre coverage rule: five Basic and five Intermediate questions per Pre concept (amends §8 for the Pre lane)

Owner ruling, 7 Sep 2026: *"I would like 5 basic and 5 intermediate level
questions per concept of Pre Learning."*

**What it changes, stated rather than blended (Rule 0).** Contract v2.0 §8
says "No fixed count such as five questions … is permitted" and Q26
restated it as "no quotas"; Q20 (21 Aug) had set "about 5 per concept,
split left to the model" and the 21 Aug diagnostic posture; the 20 Aug
steer before it was ~10 as 5 Basic + 5 Intermediate. The owner, as the
contract's author, now fixes the Pre lane's coverage at **exactly five
Basic and five Intermediate generated questions for every Pre concept, and
no Advanced ones**. §8's "no fixed count" is amended for the Pre lane by
this ruling; the contract text itself awaits the owner's v2.1 issue and is
not rewritten here. §8.6 (at least one routed question; Q29's blocking
finding) stands beneath it. Q29's one exception stands too: a plan of
zero is the model's recorded request to DROP the concept, never a shipped
concept with fewer questions.

**How it is held (`phase3/pre_coverage.py`, the one place the numbers
live):**

* **A frozen run variable.** The rule (`version`
  `pre-coverage-owner-2026-09-07`, `per_tier` `{Basic: 5, Intermediate:
  5}`) is stamped on the Phase 3 envelope's metadata where a production
  envelope is built (`concept_topology_contract._run_rewritten_phase3`),
  so it is inside the seal and inside every decision key. A reused sealed
  envelope keeps the rule, or the absence, it was sealed with — decide-
  once. `prequestions.build` reads it back and logs which posture is in
  force. An envelope that records no rule (sealed before this ruling; the
  RNE golden fixtures) runs under Q26's posture in full, which is why the
  golden replay chain is unchanged and why re-recording it under Q30
  needs a live run.
* **The plan.** Under the rule the plan checker adds one comparison
  against the ONE external number the owner fixed: every plan states
  total 10 and the split 5 Basic / 5 Intermediate exactly, or zero with
  no split. The plan's judgment is its RATIONALE — which capabilities of
  the Mastery each tier's questions verify. The critic's ANCHORING
  dimension is redirected to the rationale's coverage, never the number.
* **Authoring.** Each question is written AT its tier and carries a
  `tier` field; the authoring checker counts each tier against the
  plan's split (the model's own plan against its own questions, tier by
  tier). Basic verifies that the learner holds the fundamental as the
  concept states it; Intermediate that the learner can apply it in a
  situation the needing Post concepts depend on — stated to the model as
  evidence, never branched on in code.
* **The Master.** `assessment_release_run` transports the authored tier
  into the level stage for a generated question that carries one — the
  level row records `mechanical_basis: authored_tier` and no model
  verdict is asked for, because a second verdict could only break the
  split the owner fixed. Every other candidate (the source lane, and
  generated questions authored before the rule) keeps the independent
  verdict exactly as before. Clustering into BG/IG families within a tier
  stays the model's.
* **Release QC.** `release_qc.PRE_CONCEPT_COVERAGE_OFF_RULE` blocks the
  database write (every download ships) for a staged Pre concept whose
  questions are tiered otherwise than the rule the payload records;
  dormant where no rule rode the payload or the questions carry no tier.

**Rule 1, plainly.** A fixed count the owner set is a product rule, not a
judgment about the source: the code compares recorded numbers and
transports a recorded model decision. Which capabilities the ten
questions verify, and every question's text, remain the model's. The
Q4/Q26 pins over `prequestions.py` (no numeric literal, no norm the model
is anchored on) stand: the module holds no count; it reads the owner's.

**Stands:** everything Q26–Q29 list except §8's "no fixed count" for the
Pre lane, amended above.

### Q31 · Decided — writing quality before spend: the Q26 cost cuts are reversed by default

Owner report, 7 Sep 2026: *"since the restructure the outputs have been
completely off … the outputs are coming out but the way they are written
is completely off"*, narrowed by the owner to *"since the time V2.0
contract was introduced in this PR line"* (PR 288, 4 Sep). Owner ruling
on the causal analysis that followed: *"Can we inculcate the changes? And
improve the prompting and intelligence as well."*

**What the analysis found.** Every corrected workbook in the owner's
correction corpus and every pre-4-Sep output on record was written under
Q22's uniform `xhigh`, a concept Refiner over every released row, the
Master Refiner and the touched-group QA on, a critic on every Master
decision and three bounded corrections for a confidence shortfall. PR 288
changed all six in one day, together with the contract's wording rules,
and no output produced since has been measured against one produced
before on the same source. The register recorded each cut as "one
environment variable away for measurement"; the measurement was never
made, and the owner's reading of the outputs is the measurement now.

**What this entry decides (amends Q26's cost policy, keeps Q22 as the
default it restores).** The writing-quality settings are the code defaults
again, stated in `fly.toml` so a deploy cannot drift from them silently:

* `AEGIS_OPENAI_REASONING_PROFILE` defaults to `uniform-xhigh` (Q22): every
  purpose — chapter and topic descriptions, Polish, the Refiner, the
  critics, transcription and outline — requests `xhigh`. The `tiered`
  profile stays selectable as the cost profile; its table remains the
  registry's stated values so the A/B the owner never ran can still be run.
* `AEGIS_CONCEPT_REFINER` defaults to `all` (§8.3 as written before Q26:
  every released row is refined; `flagged` and `off` stay selectable).
* `AEGIS_MASTER_REFINER` and `AEGIS_MASTER_GROUP_QA` default on; `0`
  switches either off.
* `AEGIS_MASTER_CRITICS` defaults to every stage. The joint per-item review
  after marking (contract §27 step 6, `assessment_item_review`) stays on
  beside them — it is a contract stage, not a replacement for the critics.
* A pure `[confidence]` shortfall goes back through the bounded
  corrections (three attempts) like any other defect. Q26's argument — a
  re-ask on the same evidence could only buy an inflated number — assumed
  the feedback is contentless; it is not: the correction names the weak
  grounding and the model may cite better evidence or rewrite the claim
  it could not ground. The prompts still forbid inflating a score, and a
  shortfall that survives the attempts ships flagged, as before Q26.

The Q26 cost profile is one variable away in every case and is written
beside the defaults in `fly.toml`; nothing here moves a verdict into code
or changes a decide-once key (Rule 1; reasoning effort remains transport
policy).

**Recorded, still open for the owner.** Two v2.0 wording rules the
analysis names as the other half of "the way they are written" are
contract text and stand until the owner rules: Q27's verbatim Post
questions (the source's wording ships unedited, so a source's awkward
sentence is the learner's sentence) and §22.5's label-free Objective
explanations. Neither is changed here. The prompt and intelligence
improvements the owner asked for in the same breath are the next slice,
authored against the contract and this register, never against Rule 1.

**Stands:** everything Q26–Q30 list except Q26's cost-policy defaults,
reversed above; Q26's "Recorded, not yet done" list stands unchanged.

### Q32 · Decided — the 8 September owner column rules and prompt refinement

**Historical implementation record:** Q33 below supersedes this entry's
English/Mathematics-only formatting scope, `creative` spelling and fixed
English keyboard rule for new runs. The original workbook evidence and
v1 behavior remain recorded here; frozen v1 releases retain that behavior.

Owner instruction, 8 Sep 2026: work on Project Aegis using the uploaded
Excel specifications for how output columns are written, refine prompting
for every API output, structure it properly and make suggestions. Evidence:
`English_Aegis_column_spec_fill_in.xlsx` and
`Math_Aegis_column_spec_fill_in.xlsx`. Exact cell references, file hashes,
implementation scope, prompt-family audit and unresolved examples are in
[the column-spec review](column-spec-review-2026-09-08.md).

**What changes, stated rather than blended (Rule 0).** Completed owner
rules in column F amend the following fields for new policy-bound runs:

* English keywords use comma-space and 3–6 short terms actually taught, in
  textual order. Relationship rosters remain space-pipe-space. The model
  chooses terms; code projects the already-authored list.
* English Objective explanations start with the correct lowercase option
  label and exact answer, then the supported rationale. This resolves
  Q31's open question about label-free explanations **for English only**.
* English textual rubric criteria use only `content`, `language`,
  `creative`, `evidence` in exact `[tag]: criterion` syntax. The former
  seven-tag registry remains a legacy policy. Tags remain forbidden in
  non-English rubrics, typed Equation/Image criteria and learner answers.
* English and Mathematics Descriptive criterion weights permit positive
  multiples of 0.5 rather than only 0.5 or 1. Exact parent/child sums,
  real numeric storage, blank unused slots and the separate four-mark
  criterion-count safeguard remain.
* Generated question source is exact `UpSchool DB`; source-drawn question
  source remains the run publication. This amends Q27's one-source rule
  **only for authored questions**. Concept source and chapter publication
  do not change.
* Objective and valid Subjective rows are `Specific`. Descriptive
  restriction remains a model decision from the real task. English
  `math_keyboard` is exact `No`; Mathematics remains demand-dependent.

Subject adapters are chosen from run metadata, never the subject of an
example in either workbook. The shared policy is
`owner-column-spec-2026-09-08`, carried by newly resolved profiles. A
persisted resolved profile without it keeps the legacy contract; a replay
does not silently acquire this amendment.

**Writing scope.** Prompts distinguish teaching description from mastery,
learner explanation from evaluator criteria, and source evidence from
instructions. Chapter/topic descriptions use original connected prose;
English specifies 3–5 chapter sentences and 2–4 topic sentences. Optional
learner analysis is omitted when irrelevant rather than fabricated. Group
descriptions name the exact assessed capability in one evaluator-facing
sentence. Changed author and critic prompts retain their exact response
schemas and protected fields. No prose-quality judgment moves into code.

**Explicitly held open.** English topic-title rules omit the topic suffix
used by their concept examples. Math topic title/display rules and concept
ID grammar disagree with their examples; its placeholder example includes
an entire question; its 0/1 Objective weights conflict with exact totals
above one mark; its `post_topics` instruction says pre topics. Current
canonical identities, display names, bare placeholder letters, marks sums
and Post roster behavior stand pending the focused rulings in the review.
Examples do not authorize copying unrelated chapter facts or stale assets.

The update cells distinguish fresh content (`No`) from intentional changes
to existing content (`Yes`). Fresh generation still emits `No`; this
amendment does not invent an update workflow or infer existing entity IDs.
Raw template duplicate-header counts do not change Q27's registered
72/440/149 output schema.

**Q32 export correction:** Contract §24 and both supplied sheets' Answer
blocks F18/F23/F25 require equivalent, non-additive parent and child rubric
views. Existing code incorrectly removed the parent view. Current column
policy restores it by mechanically projecting the ordered child criteria
into the main rubric columns and validating exact equivalence. Internal
scoring remains child-owned; no semantic judgment or second award is added.
Legacy frozen profiles retain their recorded export behavior.

**Stands:** Q27's verbatim Post source questions, Q29's run binding, Q30's
five Basic and five Intermediate Pre questions per concept, Q31's restored
quality defaults, all other Q26–Q31 rulings, model author/independent critic
ownership and the release/artefact distinction. This records an
implementation amendment, not a claim of measured live writing quality or
authorization to merge or deploy.

### Q33 · Decided — universal column rules and owner review before step removal

Owner clarification, 8 Sep 2026: the two supplied subjects are examples of
how Aegis outputs must be written, not a restriction to those subjects.
Only English rubric tags such as `[content]` and `[creativity]` belong in
rubrics; other subjects must not carry them. The owner also requires
end-to-end attention to source-topic absorption, concept decomposition,
teaching detail, KaTeX, hosted images and usable evaluation rubrics, and
supplied varied source PDFs for that review. The owner explicitly requires
being consulted **before any unnecessary run step is removed**.

**Universal format, with one explicit subject exception.** New profiles
carry `owner-column-spec-2026-09-08-v2`. Every subject uses the same output
column rules: comma-space keyword cells, correct lowercase option label
and exact answer at the start of Objective explanations, positive
multiples of 0.5 for Descriptive criterion weights, exact totals and
non-additive multipart parent/child rubric views. Relationship lists keep
their pipe delimiter. Teaching descriptions use original connected prose;
the 3–5 chapter sentence, 2–4 topic sentence and 3–6 relevant keyword
guidance applies across subjects. These are model writing instructions,
not semantic count thresholds.

The English-only registry is `content`, `language`, `creativity`,
`evidence`, in exact `[tag]: criterion` syntax, restricted to textual
English Descriptive criteria. The owner's latest `creativity` spelling
supersedes v1's `creative`. No functional rubric tag belongs in other
subjects, learner-facing fields, or typed Equation/Image criteria. The
actual subject, grade, source and response demand still determine the
pedagogy through API authoring and independent review; shared formatting
does not mean identical content across subjects.

Keyboard mode is response-dependent in every subject, authored by the API
and checked by its critic. Objective's keyboard cell remains blank;
Subjective/Descriptive use exact `Yes` or `No`. Q32's blanket English `No`
is removed from new policy because the clarified subject exception is
rubric tags, not response mechanics. This changes a column policy and
does not remove a pipeline stage. Existing explicit category/marks/duration
profiles continue to apply; a subject-specific example does not select or
invent one.

Each criterion must name observable evidence relevant to the actual task,
allow valid alternatives under its adopted answer contract, and avoid
double-credit or unasked requirements. Independent demands must not be
bundled into an omnibus criterion. A coherent criterion may exceed one
mark under the half-step policy; the separate existing minimum of two
criteria for a four-mark single-part Descriptive answer stands.

**Replay and evidence.** Persisted v1 snapshots keep their recorded
delimiters, explanation prefixes, tag registry and keyboard policy.
Persisted profiles with no column-policy snapshot keep the legacy contract.
The author, critic, release checks and workbook projection read the same
carried policy. Original uploaded workbook cells are retained as evidence;
Q33 corrects their initial interpretation rather than rewriting that
evidence. The six identity/placeholder/weight representation conflicts in
the review remain open where this clarification does not resolve them.

**Removal requires a concrete proposal and the owner's prior approval.**
Review the whole run and identify a proposed removal's purpose, replacement
coverage, expected effect and evidence first. Do not disable, bypass or
delete a stage on the basis of this request alone. Existing author/critic,
refiner and evidence-preservation stages remain in place until the owner
approves a specific proposal. Semantic judgments remain API-driven.

**Validation limit.** Cross-subject dry regressions cover author payloads,
mechanical validation, XLSX read-back, tag containment and frozen policy
replay. They do not establish live chapter quality, successful Fly image
hosting or a complete live end-to-end run. Source-format diversity and
image/KaTeX correctness must be assessed with the supplied chapter evidence
and explicit live results before claiming those outcomes. No merge,
deployment or production database write is authorized by this entry.

### Q34 · Decided — API-owned learner analysis and mastery quality

Owner approval, 8 Sep 2026: **“Yes, please.”**, answering the concrete
proposal to replace the remaining concept learner-analysis word-list and
80% word-overlap checks with model author/critic review while preserving
formatting, identity and scoring checks. The proposal also identified the
mastery-text substance thresholds. This is the specific prior approval
required by Q33, not authorization for other stage removals.

**Approved removal, in both learning lanes and every subject.** Retire the
generic-misconception/error vocabularies, required belief/action framing,
80% analysis token overlap, and the four-word/twelve-character mastery
substance test. Delete the corresponding predicates and semantic finding
codes rather than leaving them disabled. The deeper trace found mirrored
behavior in `concept_refiner.normalize_analysis_sections`: it filtered,
reclassified and deduplicated authored insights by their wording. Remove
that behavior too. Preserve unclassified content for review, preserve
explicitly selected kinds, and retain every authored mastery statement
instead of choosing the last one as “more substantive.”

**Replacement coverage.** Strengthen the existing Settle, Analyse,
Premap/Preanalyse, Polish and final Refiner author/critic instructions.
Review misconception versus faulty application by meaning, duplication by
the claim and consequence, usefulness against supplied evidence, and
mastery as an observable supported capability. A concise statement can be
complete. An empty inventory can be justified. Neither preferred verbs nor
shared vocabulary determines the verdict. Source-backed authors/critics
see their existing chapter or prerequisite evidence; the final Refiner
reviews the assembled teaching row and must not pretend it saw source
material absent from its request. No extra routine concept-quality pass
is added; conditional Polish remains conditional and Q31's all-row Refiner
remains in place.

**Content and review preservation.** Empty-inventory dissent and reviewer
unavailability must survive at inventory scope and on the affected map's
rows, even when there is no item ID to carry a flag. Existing historical
semantic flags remain visible. An explicit model-driven instruction round
now receives one independent advisory critic with the original and
proposed records, instruction, and lane-appropriate source evidence.
Its dissent/unavailability is recorded without rejecting an applicable
author proposal. Exact manual edits remain user-owned and API-free.

**Mechanical checks remain.** Keep required fields, exact section shape,
nonempty explicitly present analysis components, mastery marker presence,
location and count, identity/allotment accounting, protected fields,
KaTeX/image format, rubric tags and score arithmetic. Missing or duplicate
mastery markers can still nominate an API repair; the validator cannot
decide that a nonempty statement is too short to mean enough. Stored
decisions remain immutable; changed author/critic instructions use distinct
decision identities rather than masquerading as previously reviewed work.

**Bounded scope and evidence.** Other inherited semantic heuristics,
including Description length/placeholder/copied-source checks, Type/Case
checks and Settle's identical mastery-string check across concepts, are
outside this approved removal. Do not claim all deterministic semantic
heuristics have been purged. Their removal needs a separate concrete
proposal under Q33. The associated review records dry regression results;
there is no measured live quality result, production provider run, Fly
upload, merge or deployment in this amendment. Q33's universal column
policy v2 and English-only rubric-tag exception stand unchanged.


### Q35 — DECIDED — Complete evidence, efficient execution and exact output vocabulary

**Owner ruling (8 September 2026).** The owner approved all recommendations in
`Aegis_End_to_End_Review_and_Optimization_Proposal.md`, then clarified that the
reported deterministic category/group regression concerns the **field values**
(such as `Fill in the blanks`), not how a question is judged to belong to a
category. This amendment follows merged PR #293 and its verified baseline
`feed425adc9c8a096c7940ecec5aefba7e0d6fad`.

**Approved implementation.** Supply complete specifically referenced source,
task, prerequisite and visual evidence to existing authors and critics; preserve
cross-page and table/figure relationships and complete crops; separate printed
caption provenance from authored public alt text; independently review direct-MMD
normalization without repeating the verified-PDF review. Preserve malformed
learner-facing content and its before/after repair evidence. Replace the audited
remaining Description/copied-prose/Type/Case meaning heuristics with the existing
API semantic review and repair owners. Exact syntax, identity, inventory,
arithmetic, image and KaTeX checks remain mechanical.

Forward adopted answer-space contracts and accepted equivalents to rubric review;
make exported scoring sufficient for equivalent and partial-credit responses;
protect complete source tasks including Objective options and figures. Reconcile
stale Open/Specific instructions with Q26; existing accepted source/language plans
govern concept skeletons. Detect aggregate multipart capacity conflicts early,
preserve all scoring evidence and never silently truncate or split a source task.

Implement repeated-prefix caching, bounded independent parallelism, strict
transport schemas where the complete response contract supports them, interrupted
author/critic resume, and attributable attempt/wait/cost records. Incremental
Master validation must preserve per-unit rollback, complete topology checks and a
final full workbook readback; it must demonstrate parity before replacing a full
per-unit check. Savings are measured, never inferred from a passing mock test.

**Exact output vocabulary.** Restore the historically declared presentation
labels through one versioned, deterministic serializer/allowed-value contract.
Only explicit spelling/casing/plural aliases may map to a canonical label; no
question text, command words, marks or meaning heuristics assign a category or
tier. Category/difficulty judgments retain their existing API owners and carried
blueprint decisions. `Basic`, `Intermediate`, `Advanced` are exact output labels.
Distinct category/mark profiles retain their separate identity. Persist the
vocabulary with new profiles and preserve the labels of already-frozen runs.
The historical evidence and alias table are recorded separately in
`docs/category-group-history-2026-09-08.md`.

**Ratified representation interpretations.** Topics use unique
`ChapterBaseID_TNN`, concepts use `TopicID_CNN`, display names remain plain and
rosters reuse exact decorated titles. Subjective placeholders store `a`, stems
store `$$a$$`, and learner text displays `____`. Correct Objective option weight
equals the accepted item marks; distractors have zero weight. `post_topics`
contains Post topics. The governing Pre prerequisite boundary remains earlier
grade/year; generic assessment calibration must be explicit and must not borrow
an unrelated institution's profile. Chapter duration keeps its separate rule.

**Preserved scope.** Luna, uniform xhigh, all configured critics, all-row Concept
refinement, Master refinement, group QA, and five Basic plus five Intermediate
questions per retained Pre concept remain in effect. No reduction of these stages
or coverage, interactive Batch substitution, production restart/deployment or
unmeasured quality claim is authorized by this amendment. Implementation and dry
acceptance can proceed autonomously; live provider/Fly acceptance depends on the
required configured access and a separately recorded run scope. The original
master-contract document remains a verbatim historical authority; this register
records amendments rather than silently rewriting it.

### Q36 — DECIDED — Durable question numbering and current workflow tests

**Owner ruling (9 September 2026).** After the audit showed six obsolete
workflow assertions and one genuine highest-number reuse bug among the seven
expected failures, the owner approved implementing the proposed fixes and
rerunning CI with zero expected failures.

Persist atomic per-family highwater counters independently of questions,
concepts and releases. Continue existing label families without renumbering.
Record supplied/imported labels and staged Master labels; recover the highest
retained live or historical release number on startup. The same accepted Master
run replays a durable, content-bound reservation, preserving labels and cached
Refiner decisions. Changed accepted work obtains a fresh reservation. Do not
hold a database write lock across provider calls or commit unrelated caller edits.
Previously deleted labels absent every retained record cannot be reconstructed.

Migrate the six old tests to the current staged-generation, explicit-publication
and downloadable-diagnostic contracts, preserving their formatting, topology,
source-merge, checkpoint-recovery and conversion-failure assertions. Remove their
expected-failure exemptions and the numbering exemption only once the actual
behaviors pass. This authorizes no semantic classifier changes, stage removal,
model/effort reduction, production merge or deployment.

### Q37 — DECIDED — Run scheduling, live accounting and complete Pre-learning capture

**Owner ruling (9 September 2026).** After reviewing the active Statistics and
Madam Rides the Bus logs, the owner said "Let's fix other things before thinking
about gemini" and explicitly added "make sure not to deploy yet. files are
runnning still." This authorizes implementation, dry verification and a new PR;
it does not authorize a main merge, deployment, restart or live provider switch.

**Execution.** The automatic two-lane Master wrapper must preserve bounded
parallel question work inside each lane. Orchestration nesting is distinct from
decision-pool nesting: retain the protection against unbounded nested Settle
pools, ordered application, independent lane failure handling, downward
cancellation and saved successful paid work. Independent source hierarchy
batches can run concurrently within each author or critic pass; criticism still
waits for the complete author pass. Source text, prompts, cache identities and
result order are preserved by this scheduling change.

**Accounting and diagnostics.** Show recorded, priced usage even while calls are
pending or a completed request has missing usage. Distinguish pending requests,
unresolved usage and missing pricing; never turn unknown charges into zero or
present a partial subtotal as the complete bill. Keep historical costs frozen
and portable usage schemas backward compatible. Provide a downloadable current
console snapshot containing the retained log and usage during an active run.
This is not the full source/decision archive: that ZIP retains its consistency
lock and is offered after the active operation stops.

**Pre-learning.** Give existing prerequisite authors and critics complete
relevant task/context evidence and actual figures. Resolve all allowed evidence
identities and forward earlier omission findings. Allow a model to split a
compound capture into separately identified fundamentals while preserving its
parent provenance and exact accounting of the atomic parts. Let the final
authority recover an omitted prerequisite from supplied source evidence and
record how source demands are covered, or why no prerequisite is needed, within
the existing author/critic call. Group independently teachable and diagnosable
capabilities rather than merging them merely because one lesson teaches them
together. Earlier-grade eligibility, explicit dispositions and the prohibition
on padding remain. No target concept count or deterministic meaning judgment is
introduced. Newly sealed Phase 3 envelopes freeze this policy; reused envelopes
keep their existing policy and accepted decisions.

**Preserved quality.** Luna, uniform xhigh, all configured author/critic/refiner
stages, English-only functional rubric tags, complete source task protection,
KaTeX/image validation and five Basic plus five Intermediate questions per
retained Pre concept remain. A provider comparison or effort experiment is a
later decision. Mock concurrency tests establish scheduling behavior, not a
measured live completion time or pedagogical quality improvement.

### Q38 — DECIDED — Question-owned tables, visible images and readable Excel

**Owner ruling (9 September 2026).** The owner reports missing tables/images
and clustered Excel cells, then clarifies: "Tabular columns are supposed to
come under the question, if it is part of the question" and requests an image
or a table using the existing KaTeX rules. This authorizes correction of the
lossy transport and formatting rules identified in review. The deployment hold
remains: update the draft PR only, without changing the running app.

**Tables and question ownership.** Keep a question's complete table in its
learner-facing context, including shared context for dependent multipart
children. A complete syntactically understood text/math table becomes one
canonical KaTeX array preserving headings, ordered rows, cells, blanks and
mathematics. Blank-cell phantom spacing uses an existing rendered size
reference from the same source column; no answer is invented. Tables requiring
visuals use one complete faithful image through
the existing source/API crop decision. Do not replace tables with coordinate
prose, detach them into sidecars, or drop nested child tables/figures before
authoring. Malformed, spanning or image-bearing markup that cannot be
transported faithfully remains intact with a named defect for the existing
repair process. No local semantic reconstruction is authorized.

The existing source crop step now also renders a complete table when the API
has declared its visual cells and page bounds. Validate exact cell-figure
identities and containment before creating the image. Preserve original rows,
cell references and bounds as evidence; replace contained cell-only images
with the complete table in the owning question's display. The existing source
critic receives the whole table crop and original page. New approvals record
an evidence-bound crop review receipt; reused historical transcription without
that receipt carries an explicit unreviewed-crop flag.

**Images.** Preserve accepted image references in Description and learner
analysis during refinement and cleanup. Remove stale instructions declaring
those images categorically forbidden. The model still decides relevance; URL,
source-identity and rendering checks still apply. Declared image fields must
remain visible in delivery reports even when their URLs cannot be verified.
Image grids may read verified pinned bytes locally; their cache includes all
inputs that affect the pixels, including labels. Public delivery requirements
remain independent of local availability.

**Excel display.** Preserve the `<br>` import marker and `<br><br>` paragraph
meaning required by §17. In the final XLSX cell presentation, pair each marker
with a native Excel line feed so a wrapped cell displays the separation too.
Apply that projection only outside complete KaTeX spans, preserving TeX
newlines and comments exactly. Declared Equation cells remain raw LaTeX through
export, import, readback and cell-capacity checks.
Import and canonical comparisons treat each pair as one logical break;
repeated export must not multiply breaks. This supplements the HTML transport
instead of relying on native line feeds for import behavior. Apply sensible
widths to the exact registered fields and top-aligned wrapping; preserve row-2
headers, sheet names, data-row topology, numeric types and formula protection.
Measure the final display string against Excel's cell limit, preserving the
existing complete overflow evidence and release flags.

**Limits.** Excel does not itself render Aegis image/KaTeX markup as pictures
or mathematical tables. These fields remain importable rich text for the
application renderer. Previously flattened prose cannot safely be reconstructed
without its original source; previously generated workbooks are not silently
rewritten by this code change. Actual missing assets in a current run require
that run's export or evidence, separate from mocked transport verification.
Fresh source builds use the new table renderer; existing compiled source and
sealed decisions retain their recorded content. Do not invalidate accepted
historical runs by globally bumping downstream compiler versions.


### Q39 — DECIDED — Meaningful topics, complete source coverage and faithful task polishing

**Owner ruling (9 September 2026).** Summary, Introduction and Exercises must
not become generic topic containers. Preserve their entire substantive content
and every task under meaningful topics and concepts. The Frédéric Sorrieu
opening in RNE is substantive current-chapter teaching despite appearing to be
an introduction. Short lower-grade chapters require meaningful header-aware,
capability-level atomisation, never volume-derived counts or thin-content
merging. The owner supplied 18 Classes 1–3 English, EVS and Mathematics PDFs
and an English analysis screenshot. Findings are recorded in
`docs/source-topic-review-2026-09-09.md`.

**Content and question ownership.** API authors and independent critics judge
source roles and names from meaning. Retain source heading text, every source
block/task identity, embedded question, sidebar, table, visual, word bank,
example and dependency. A generic heading can supply context without becoming
a topic. A substantive opening receives an appropriate teaching title. Recaps
reinforce existing concepts; their content is not deleted. Each source question
belongs to the concept it assesses, regardless of exercise-section position.
Preserve a whole multipart task and its source provenance. An adjacent-chapter
page or unavailable referenced script remains represented with a source-boundary
or missing-dependency finding; neither invent the missing source nor discard
the existing content.

**English final analysis.** Keep the final topic `Detailed Analysis of '<Name>'`.
Its concepts are distinct whole-work dimensions appropriate to the supplied
prose/poem and grade: Theme/Central Idea, Plot/Development of Ideas,
Characterisation/Speaker, Setting & Atmosphere, Language & Literary Devices,
and Culmination where supported. These are analytical functions, not invented
content quotas. Factual prose can use main idea, supporting evidence and
informative language rather than fictitious characters/settings. A question
about characterisation across all episodes belongs to Characterisation, not
automatically Culmination. Apply that dimension-first placement to all lenses;
Culmination integrates genuinely different capabilities. Preserve explicit
grammar/phonics instruction and independent reading passages without forcing
them into an unrelated story event. Use only the uploaded work, including an
abridgement's actual ending.

**Post source only, with bounded format polishing.** No new Post questions,
variants or gap-filling generation are permitted. A source-given creative,
personal-response, drawing, shading, speaking or observation task remains a
source task. This ruling expressly narrows Q27's verbatim restriction to permit
faithful task-format adaptation in the existing upstream question-polishing
pass: tick/cross, number-in-box, picture classification, grids, word banks and
other primary-grade layouts become clear, self-contained Aegis questions.
Preserve every original demand, value, option, response cardinality, part,
order, source stimulus, visual dependency and givens. Do not add explanations,
convert multi-select into single-select, substitute picture answers for the
pictures, or change drawing/ordering into recognition. Required tables/images
stay under their question under Q38's KaTeX/image rules.

The immutable raw/normalised source remains evidence. The independent polishing
critic compares the proposed form with that evidence. New items carry a
versioned polishing receipt; their accepted wording is frozen before Type/Case
clustering and carried to Master author/reviewer. The Master Refiner still
cannot alter `question` or `question_text`. Historical unmarked items retain
Q27's raw-source authority. Review flags and incomplete-source findings remain
visible; the existing release/publication distinction stands.

**Pre prior knowledge only.** Retain every necessary, independently teachable
prior-grade/year foundation, with the actual source demand, necessity and
prior-learning basis. Being useful, appearing early, sharing a subject or
being familiar to an adult is not evidence of prior learning. New concepts,
newly taught vocabulary, story events and future extensions cannot be promoted
to Pre. Genuine earlier-learning recaps may support eligibility, judged by
content. Unsupported, current-taught and unnecessary candidates are explicitly
disposed by the existing API authority with their full provenance preserved.
Map descriptions, mastery, learner analysis and generated Pre questions stay
inside retained prerequisite scope; fixed 5 Basic + 5 Intermediate coverage
does not license new teaching. Do not manufacture prerequisites for a first-year
learner. Q37's complete evidence and omission review remain.

**Compatibility and operations.** Apply the new topic and Pre policies only to
freshly sealed Phase 3 envelopes; absent-key historical envelopes retain their
recorded suffixes and decisions. New source/planning/polishing decisions carry
the revised policy identity. No stages, reviewers or refiners are removed;
Luna/uniform-xhigh, Q30 coverage, exact fields, source identities and rendering
gates remain. Keep PR #297 a draft; no merge, deploy, restart, provider change
or paid generation while the owner's files are running. Offline regressions
verify transport, invariants and replay; they do not establish the pedagogical
quality of a newly generated live output.

---

*Prepared from Aegis.docx (the soul), the SOP Bulk-Import Fill Guide, the
Open/Specific rubrics (v1 and the corrected v2.0 registry), the
Question-Paper Blueprint, the GPT Restructuring Architecture v1.0
(alignment-audited), the repo's decision documents, and a six-area map of the
ProjectAegis codebase.*


### Q40 — DECIDED — Stage model routing, adaptive Pre coverage and INR cost logs

**Owner ruling (9 September 2026).** Use Gemini 3.8 Flash only for building Pre
questions, concept-detail writing on Luna at xhigh, and GPT-5.4 mini/Luna for
other stages according to role and complete-context capacity. Complete the
Gemini API workflow. Omit the fixed 5 Basic + 5 Intermediate rule: author only
as much diagnostic coverage as the actual prerequisite warrants. No padding,
forced tiers or scope expansion. All prior-only boundaries, source-only Post
questions and review/refiner stages remain. This expressly supersedes Q30's
fixed quota and Q31/Q37's uniform model restriction for new work.

**Frozen routing and coverage.** New runs freeze the exact stage profile before
spend. Context-local request routes choose credentials, endpoint, model,
reasoning, output capacity, schema and accounting together. Only the explicit
Pre author stage can use Gemini; its planner/critic/Fixer remain on OpenAI.
Concept detail authors and final Concept refiners stay Luna xhigh. Narrow
passes use mini with complete-input capacity checks; exceeding mini capacity
moves the whole request to Luna instead of trimming evidence. No selector
changes a process-wide model during concurrent work. Fresh cache identities
carry the profile; historical unprofiled/5+5 envelopes replay their own policy.
Adaptive totals and tier splits are semantic API decisions, independently
reviewed, then mechanically reconciled to their recorded accepted plans.

**Names, images and tables.** Keep source-specific topics and the final English
`Detailed Analysis of '<actual work>'`, never generic Analysis or a placeholder.
Image captures retain their source-asset URL tags with their owning concept or
question, through refinement and both workbooks. A bracket inside a quoted alt
caption must not truncate the tag. The supplied Council of Ministers/Cabinet
comparison must render as the complete centered bordered KaTeX array with all
rows/cells and `\text{}` text, carried intact inside `[Katex]...[/Katex]`.

**Logs and currency.** The owner confirms API credentials are already in
production and offers read-only Fly access to verify it. Per-request and
cumulative estimated charges must be visible in rupees. Retain actual provider
and model, cached input, output/thinking usage, retries and missing-usage states.
USD remains the provider ledger; INR estimates retain the conversion quote and
date. Cumulative/resumed totals sum recorded amounts rather than revaluing old
requests at a new rate. Unknown bills are not zero. The conversion reference
and pricing date are visible for reconciliation.

**Deployment boundary.** Prepare and verify locally. No merge, deploy, restart
or running-job mutation. The earlier GitHub push was rejected by automatic
approval review; this ruling does not explicitly authorize a new push attempt.
Implementation/evidence: `docs/model-routing-review-2026-09-09.md`.


### Q41 — DECIDED — Complete question membership, response-mechanism classification and Concept review before Masters

**Owner ruling (9 September 2026).** Exercise questions are being extracted,
but in-text questions are inconsistently missing. Extract every actual
source-set learner task from the complete source, including activities and info
hubs. A task in a box or between explanatory paragraphs has the same inventory
standing as an end-of-chapter exercise. Preserve its necessary context and
media, then polish it into a standalone Aegis question without adding a new
assessed demand. Informational prose and dialogue questions in a story/poem
are not automatically learner tasks; the API decides from the source's role.
Every source block receives an accountable semantic disposition. Inventory
coverage must not be limited to the candidates a parser already found.

**Independent and multipart questions.** A heading or instruction such as
“1. Answer the following questions: (a) Question 01 (b) Question 02” is an
administrative wrapper when the questions stand independently. Assign each
independent task its own identity. Only meaningful necessary shared context
with dependent questions forms one multipart question. Numbering, letters,
indentation, proximity and membership in the same exercise cannot decide the
boundary. Preserve genuine multipart tasks whole with every child in source
order. Existing author and independent advisory review stages make these
judgments; deterministic code checks identities and exact coverage.

**Question classification SOP.** The supplied three-page
`SOP_ Classification of Questions.pdf` has SHA-256
`1f6818d1a13595c1025fb43f25d258d09ee13147f2f1603bd9d236c3357d6e52`.
It governs new work as summarized in `docs/question-classification-sop.md`:
Objective selects explicit supplied choices, including multiple selection;
Subjective supplies a short fixed answer; Descriptive constructs an
explanation, calculation, reasoning, drawing, map response or extended answer.
True/False explicitly remains Subjective. A numerical final answer does not
make a calculation Subjective. Classify by required response mechanism,
never marks, words, or final-answer length. Record this amendment where older
single-choice-only Objective examples conflict, preserving sealed historical
policies and the verbatim master governing contract.

**Concept-first review.** New runs generate complete Pre/Post Concept files,
including their question examples, source context and media, then pause for
the owner to review. The owner can reupload corrected Concept inputs and
continue to Master generation from the accepted input set, or continue with
the generated files unchanged. No Master authoring is spent before that
explicit continuation. The review upload stages corrections; it does not
publish to the CMS. Validate the input set before replacing accepted state,
retain immutable source and revisions, and ensure Master caches bind the
accepted corrected snapshot. Corrections to question wording must reach the
Master. The owner's follow-up explicitly permits the reviewer to omit, add
and move questions in the Concept file's Types/Cases. That reviewed question
set is authoritative for Post Master generation: materialize exactly those
questions, honor their reviewed placement, and never automatically restore
removed questions or generate extra Post questions. Record manual additions
as reviewer-authored, and retain original source questions and all
omitted/changed/added/moved dispositions in the audit trail. Initial source
extraction must still be complete; intentional reviewer omissions are a later,
explicitly authorized decision. Pre questions continue to generate
automatically under Q40 and are not constrained to the reviewed Post question
bank. Replace the old Review & Edit section with the file download, optional
corrected upload and Master continuation flow.
Both Pre and Post have separate Excel upload controls for their original
downloaded Concept workbook after local edits; a separate correction format
is not required. Edited Pre concepts are the accepted prerequisite input to
automatic Pre question generation. The owner specifically requires the UI/UX
changes and preservation of every earlier request during this restructuring.
This intentionally authorized boundary supersedes older no-human-review and
automatic-Master wording. It removes no generation or model review stage.

**One cumulative run.** Preserve the same run identity, chronological logs,
per-request/cumulative INR receipts, retry costs, processing time, and monotonic
progress across Concept completion, review, reupload, restart and Master
continuation. Concept review is a paused state, not 100% completion. Pause the
active-processing clock during human review; retain review waiting and total
wall time distinctly. A page refresh or restored checkpoint must recover those
values without resetting or charging prior work twice.

**Boundaries.** Q39 source-only automatic Post extraction (amended above for
explicit manual reviewer additions/omissions), bounded Pre, Q40 stage model routing,
all review stages and immutable historical replay remain. The owner chose to
skip the live Fly image check; that does not establish a new live verification.
The owner's subsequent instruction explicitly authorizes pushing and merging
after the complete implementation and validation. This supersedes the previous
push-approval block and merge hold; root completes the normal repository gates
and merges the reviewed changes. No separate manual Fly deployment or restart
is requested.
