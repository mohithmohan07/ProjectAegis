# Open/Specific policy registry — evidence for the `answer_restriction` verdict

    registry_id: registry-v2.0-summary
    status: PLACEHOLDER — the summary standing in for the full corrected v2.0 workbook

## What this file is, and how it is used

This file is **evidence the model reasons over** when it decides a question's
`answer_restriction`. Per **Q11** it is *"versioned evidence for the model —
never executable classification."* Two rules bind every consumer:

1. **Pass it whole.** The complete text of this file goes into the
   `assessment.answer_restriction` decision payload. No code may parse it,
   index it, extract rules from it, or branch on subject, question type,
   keyword, or family name. There is no lookup table anywhere.
2. **Hash it into the policy version.** The pass's `policy_version` includes
   the sha256 of this file's contents. Replacing this file therefore re-keys
   every stored `answer_restriction` decision, which re-decides them against
   the new evidence — no stale verdict can survive a registry change. This is
   the same mechanism the Architect uses for the instruction-set hash
   (`docs/restructure-handoff.md` §3).

## Status: this is a placeholder, and replacing it is expected

`docs/aegis-restructure.md` names *"the corrected Open/Specific API Policy
Registry v2.0"* (`:13`, `:41-42`, `:922-923`) as a **source workbook used to
prepare the restructure document**. That workbook is not in this repository.
What follows is the authoritative *ruling* about the registry as recorded in
the restructure document — the answer-space doctrine and the decided
carve-out — which stands as the registry evidence until the full workbook is
committed here.

**When the full v2.0 workbook is available:** replace this file's body with
its complete content, set `registry_id` to `registry-v2.0` and `status` to
`AUTHORITATIVE`, and change nothing else. The policy-version hash does the
rest — every previously recorded verdict re-decides automatically. Do not
merge the workbook into code, and do not summarize it: the model reads it
whole.

---

## The registry (current evidence)

### The verdict and its inputs

The model reads the question together with this registry and gives the
`answer_restriction` verdict. An independent verifier reviews it; **the
verifier's dissent flags and never gates** (Q10 — there is no adjudicator).

The verdict's required payload is the complete evidence for the item:

* the complete question,
* its source context,
* any image or table the question depends on,
* the expected answer,
* the rubric,
* the response modality,
* the subject,
* and the grade.

Judgment is **per item**. There is no default rule, no fallback classification,
and no rule that assigns a verdict from an item's type, format, or subject
without reading it. In particular, being an objective/MCQ item does not by
itself make a question Specific — that inference was an executable default and
is retired.

### The governing definitions (v2.0)

v2.0's structure, its **answer-space definitions**, its **per-item judgment**
requirement, and its **removal of every default rule** govern the verdict.

The verdict turns on the **answer space** the question actually admits:

* A question whose valid answers form a bounded, enumerable space that a
  grader can match against a recorded expected answer is **Specific**.
* A question that admits materially different valid responses — different
  wordings, different valid methods, different legitimate selections of
  evidence — is **Open**, because a Specific grading of it would mark a
  correct learner wrong.

When the evidence does not settle the answer space, the author ships its best
verdict **with a review flag**. The no-local-fallback invariant holds:
`answer_restriction` never receives an invented third value, and the row is
never withheld from the output.

### The decided carve-out: Math/Physics method-equivalence families (Q11)

The **Math/Physics method-equivalence families keep their v1 Open stance**.
These classify **Open**:

* word problems,
* variable assignment,
* multi-formula items,
* numerical technique items,
* own-words definitions.

The reason is grading safety: a learner who solves by a valid alternate method
must always be safe. This carve-out is revisited **only when Clarius' Specific
grading provably honours recorded equivalents** — not before.

Note that these are named families of *reasoning shape*, not a keyword list.
The model judges whether the item in front of it is one of these; no code may
match a question against these words.

### Scope boundary: marks are not this registry's business

The **Question-Paper Blueprint is canonical for mark decomposition.** The
registry's worked mark-scheme examples, where they exist, are calibration
evidence for the Open/Specific judgment **only** — they never govern marks,
step marks, sub-question marks, or keyword weightages. Marking is a separate
pass whose arithmetic identities are checked mechanically at write time.

---

## Calibration evidence — accepted reference classifications

The paragraphs above are the *ruling*. The rows below are **recorded ground
truth**: the actual `answer_restriction` verdicts on real questions in the
three workbooks the reference school accepted
(`backend/data/Testing/reference_bulk_import/grade6_{english,mathematics,science}.xlsx`).
They are evidence the model calibrates against — **not a lookup table, not a
rule, and not the absent v2.0 workbook**. No code reads them. They are here
because the restructure document names the registry's worked examples as
"calibration evidence for Open/Specific" (§4 Phase 05), and the accepted gold
is the most authoritative calibration this repository holds.

**How to read this set (the doctrinal guardrails):**

* It is **small** (25 accepted rows) and illustrative, not exhaustive. It
  supplements the ruling; it does not replace the full corrected v2.0
  workbook, which is still owed (see the placeholder note at the top).
* Objective/MCQ items appear here as **Specific**, but this is an
  *observation about answer space*, never a licence for the retired
  Objective→Specific default (Q11 removed that). An objective item **can** be
  Open; the model reads every item on its own answer space.
* The Mathematics Descriptive split (determinate single-answer items Specific;
  multi-step / "explain why" / method items Open) and the Science Descriptive
  set (uniformly Open) are the **method-equivalence carve-out visible in
  accepted data** — a learner reaching a valid answer by a different method or
  wording is graded safe.

### English

* **Specific** — *Choose the correct meaning of "buckle up" in the poem.* (Objective)
* **Specific** — *Write True or False: The mother lark decided to leave the nest…* (Descriptive)
* **Specific** — *Do as directed: Fill in the correct article. _____ sun rises in the east.* (Descriptive)
* **Open** — *Answer in 2–3 sentences: What is the poem "The School Bell Rings Again…" about…* (Descriptive)
* **Open** — *Language Study: use "keep your eyes and ears open" in a meaningful sentence…* (Descriptive)
* **Open** — *Write a short composition of about 60–80 words on "A time when self-help helped me…"* (Descriptive)
* **Open** — *Imagine the farmer had decided to reap the corn himself… write a short alternative ending.* (Descriptive)

### Mathematics

* **Specific** — *Choose the correct option: Which of the following shapes is not three-dimensional?* (Objective)
* **Specific** — *Choose the correct option: A complete angle contains ______ right angles.* (Objective)
* **Specific** — *Write the smallest whole number.* (Descriptive)
* **Specific** — *A solid has 5 faces, 5 vertices and 8 edges… (a) Name the solid. (b) How many triangular faces…* (Descriptive)
* **Specific** — *At exactly 3:00 p.m., find the smaller angle between the hour and minute hands. Write its measure and type.* (Descriptive)
* **Open** — *Write two differences between a line and a ray.* (Descriptive)
* **Open** — *A lift starts at floor 0, goes to -3, rises 7, goes down 2… (a) which floor now? (b)…* (Descriptive, multi-step)
* **Open** — *A prism and a pyramid each have a 4-sided base… find the edges in each and explain why they differ.* (Descriptive, method)

### Science

* **Specific** — *Choose the correct option: Which pair describes the main characteristics of living organisms?* (Objective)
* **Specific** — *Choose the correct option: The SI unit of length is:* (Objective)
* **Open** — *Two students measure the same desk using hand spans and get different answers. Why?* (Descriptive)
* **Open** — *Name the most suitable instrument for measuring (a) the girth of a tree trunk; (b) the thickness of an eraser.* (Descriptive)
* **Open** — *Differentiate between breathing and respiration in two points.* (Descriptive)
* **Open** — *A potted plant bends towards sunlight. Identify the stimulus and response, and name the characteristic shown.* (Descriptive)
* **Open** — *A lab thermometer has 10 °C between two big marks and 10 divisions between them. (a) What does each small division read…* (Descriptive)
* **Open** — *During a severe drought… explain three effects on animals using the characteristics of living organisms.* (Descriptive)

---

*Provenance: the ruling text above is drawn from `docs/aegis-restructure.md`
§4 Phase 05 (the Open/Specific and Marks passes) and §12 Q11. The calibration
rows are read verbatim from the accepted reference workbooks in
`backend/data/Testing/reference_bulk_import/`. Both are faithful to what this
repository already holds — not an invention, and not a reconstruction of the
absent workbook.*
