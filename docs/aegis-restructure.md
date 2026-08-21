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
  **an adaptive target of 40: normally 20 Basic and 20 Intermediate** (Q4,
  resolved per D3): neither mandatory quota nor maximum; the model authors a
  concept-specific coverage plan; variance carries an authored,
  critic-flagged rationale; an explicit blueprint may override; a thin
  pre-concept is never padded. The Pre Master contains *generated* questions
  only — current-chapter source questions never appear in it.
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
**Decided (Q12):** in the workbook, `group_name` and `group_display_name`
both carry the friendly title — "*Concept name — Tier*" — exactly as the
accepted gold workbooks do; the machine identity stays internal
(`group_key`). Level calls and variant clustering are model verdicts with
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
| **04 · Pre-Learning Master File** | All columns filled: the generated pre-learning questions (adaptive target 40 per concept), grouped and marked the same way. |

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
| Pre-Learning question generation (adaptive-40 master file) | New | Blueprint- and registry-guided (Q4 per D3, Q11). |
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
   build, adaptive-40 generation; Outputs 03–04 ship; old pre-learning flows
   removed.
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
D3 adaptive-40 formulation (§4 Phase 03); the group-description quality bar
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
D3.

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

### Q4 · Decided per D3, provisional — the 40-questions default is an adaptive target

40 (normally 20+20) is neither mandatory quota nor maximum. The model authors
a concept-specific coverage plan; any variance in total or split carries an
authored, critic-flagged rationale; an explicit blueprint may override; a
thin pre-concept is never padded to reach 40.

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

### Q12 · Decided — group naming follows the accepted gold convention

`group_name` and `group_display_name` both carry the friendly "Concept name —
Tier" title, exactly as the reference school accepted; the machine identity
stays internal in `group_key`. GPT's D7 split is set aside.

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

---

*Prepared from Aegis.docx (the soul), the SOP Bulk-Import Fill Guide, the
Open/Specific rubrics (v1 and the corrected v2.0 registry), the
Question-Paper Blueprint, the GPT Restructuring Architecture v1.0
(alignment-audited), the repo's decision documents, and a six-area map of the
ProjectAegis codebase.*
