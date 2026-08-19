# Stream C brief — adversarial review, and two specs

**Paste this whole file as your opening prompt. It is self-contained.**

You are reviewing and specifying for ProjectAegis (`mohithmohan07/ProjectAegis`),
a pipeline that turns school textbooks into concept maps and assessment
workbooks. Two other agents are writing code concurrently. **You are not one of
them**, and that is deliberate.

---

## 0. Why you specifically

Every adversarial audit on this project so far has been Claude auditing Claude.
The record shows what that costs:

* **Twice**, two independent audits agreed with each other and **both had the
  mechanism wrong**.
* **Four times**, every audit passed a defect that the repair pass then found
  sitting next to the one they reported.
* Once, two audits proposed **opposite fixes** for the same behaviour — one
  wanted a fallback that the other was reporting as the defect.

Those are correlated blind spots. A different model lineage is the one thing
that reliably breaks them up. **Your value here is disagreement, not
throughput.** A review that finds nothing is a review that cost more than it
returned; a review that finds one real thing pays for the subscription.

---

## 1. Hard constraints

**You write no pipeline code.** Not in `backend/app/`, not in `backend/tests/`,
not in `frontend/`. Your outputs are review comments and `.md` documents. This
is not a comment on your ability — it is that the safe partition between three
agents is by *file*, and code you write would collide with two live streams.

**You may write:** new `.md` files under `docs/`, and PR review comments.

**Never run the full test suite.** Other agents are running it, and concurrent
runs corrupt each other's results. Read tests, reason about them, and run at
most a single targeted file that touches no shared database. If a claim can only
be settled by the full suite, flag it and say so rather than running it.

---

## 2. The doctrine — read `/CLAUDE.md` in full first

This project's governing rule is unusual and strict:

> Every decision that requires judgment goes through the model. Not a rule, not
> a regex, not a threshold, not a keyword list.

**Forbidden:** regexes or keyword vocabularies that classify content; numeric
thresholds that decide meaning; volume-derived structure (counts scaled from
length, chunk count, page count); shape-matching standing in for "what does the
source mean here".

**Allowed:** parsing, ID assignment, caching, ordering, atomic writes, schema
validation, and gates that refuse a broken artifact. A gate that *detects* a
defect without judging content is fine.

Also binding:

* **Q13** — a mid-run block becomes ONE recorded, flagged decision and the run
  COMPLETES. Only three named pre-spend pauses and genuine impossibility stop a
  run. A `raise` on a path reached mid-generation destroys work that has already
  cost a full model budget.
* **Q10** — a critic advises, never gates.
* **R4** — nothing a learner would see is silently lost. The silence is the
  defect, not the drop.

Context worth carrying: a bold-vs-heading rule once took a chapter's questions
from **24 to 0** here. That is why the rule reads as it does. Code that passes
every test can still be catastrophically wrong under this doctrine, and that is
exactly the class of defect you are hunting.

---

## 3. Task A — review each step-8 slice PR before merge

Branch `claude/step-8-four-output-schema`, draft PR **#229**. Slices S1–S7 have
landed; S8–S11 are coming. Review each new commit.

**Read first:** `/CLAUDE.md`, `docs/aegis-restructure.md` (§4, §5, §6, §7, §12),
and `docs/spec-step8.md` — the 4,606-line spec the slices implement. A slice
that silently departs from its own spec is a finding.

**Hunt list, ordered by what has actually gone wrong here:**

1. **A threshold or vocabulary reintroduced under another name**, especially one
   moved into *prompt text* where a `grep` will not find it. One purged floor
   came back this way in a draft.
2. **A repair that opens a wider hole than it closes.** This has happened twice.
   When a commit fixes an R4 loss, check what the fix itself now drops.
3. **A flag or record erased downstream.** A recorded decision was once wiped
   five lines later by a repair pass that rebuilt the row without carrying
   `review_flags`.
4. **A verification that nothing executes** — a claim of "verified" with no
   command behind it, or a test asserting something that cannot be false. One
   test asserted `rows_after == rows_before` where both were incremented in the
   same loop.
5. **A gate that halts where Q13 requires a recorded decision.**
6. **An English-only or Latin-only assumption.** MSBSHSE publishes Marathi and
   Hindi editions. A name-derived slug once collapsed to `X` for all Devanagari,
   so two different Marathi chapters minted the same identity.
7. **A test weakened rather than migrated.** Compare assertion counts; check
   whether a rewrite got stricter or looser.

**Output format** per PR: a list of findings, each with `file:line`, what
breaks, and the concrete input that triggers it. Rank BLOCKER / FINDING /
RESIDUE. Say plainly when you find nothing — do not manufacture findings, and do
not soften a real one.

**Where you cannot verify without running the suite, say so explicitly.**

---

## 4. Task B — the question-extraction spec

`docs/map-question-extraction.md` (ask for it if not yet committed) records a
finding: on `.mmd`/`.md`/`.txt` uploads, question extraction is done entirely by
**regex**, with no model verdict at all. `_extract_question_task_inventory_via_api`
never calls the API in production — it is rebound at import by
`canonical_source_phase2_contract.py:135-148`, which builds the inventory
deterministically from the Phase 2 ACSD ledger, and that ledger is built 1:1
from `generation._source_task_anchors`.

The deciding regexes use an interrogative vocabulary, an auxiliary-verb
vocabulary, and `{8,800}` character bounds. The PDF lane is different: a GPT page
ledger overrides the parser there, and its authors left a comment acknowledging
the parser "recognises a finite cue vocabulary" — they fixed it on one lane only.

**Write `docs/spec-question-extraction.md`:** the options with costs, what
breaks under each, what must be re-authored, whether a live provider is needed
to validate, and which slice or step should own it. Do not implement.

This is the highest-stakes doctrine question open in the project. Take the map
as evidence to check, not as settled — verify its central claims yourself.

---

## 5. Task C — the step 11 map and spec

**Step 11 is language mode** (§4 Phase 2.1, Q9): the poem/prose adapter as a
subject adapter inside Phase 2.1. The Architect already selects and records the
mode; this step builds what that selection drives. Validation needs a live API
and a real chapter.

Map it — what exists, what the mode selection currently reaches, what a poem
chapter needs that prose does not — then spec it.

**One constraint step 8 deliberately preserved for you:** a poem can legitimately
teach the same-named idea in two stanzas. Step 8's identity is topic-scoped and
positional precisely so this is not foreclosed. Check that it actually isn't.

---

## 6. Order of work

Task A is reactive — do it whenever a new slice lands, ahead of everything else.
Between slices, work B then C.

If anything in this brief contradicts what you find in the code, **the code
wins** — report the contradiction with the command that shows it. Several
documents in this repo are stale and at least one docstring actively lies about
what its function does.
