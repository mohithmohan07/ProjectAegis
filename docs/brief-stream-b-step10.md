# Stream B brief — Step 10, image durability (Q8)

**Paste this whole file as your opening prompt. It is self-contained.**

You are working on ProjectAegis (`mohithmohan07/ProjectAegis`), a pipeline that
turns school textbooks into concept maps and assessment workbooks. Another
agent is concurrently building §10 step 8 on a different branch. You will not
touch anything it touches.

---

## 0. Hard constraints — read before anything else

**Branch and PR.** Work on `claude/step-10-image-durability`, branched from
`main`. Open your own draft PR. **Never** push to
`claude/step-8-four-output-schema` and never rebase onto it. Rebase on `main`
only.

**Your own clone.** You must have your own working copy. Two agents sharing a
working tree corrupts both.

**Never run the test suite while another agent is running it on the same
clone.** The DB-state suites (`test_directory`, `test_tagging`, `test_sources`,
`test_data_reset`, and four others) produce phantom failures under concurrent
runs. On your own clone you are safe; just never share one.

Run the suite ALONE, from a **fresh** isolation directory you never reuse:

```bash
cd backend && S=$(mktemp -d) && mkdir -p $S/data && \
  AEGIS_DB_URL="sqlite:////$S/x.db" AEGIS_DATA_DIR="$S/data" \
  python3 -m pytest tests/ -q -p no:cacheprovider
```

**Baseline as of `main` at the time of writing: check it yourself and record
it.** Step 8's branch is at 2482 passed / 7 xfailed; `main` will be lower until
step 8 merges. Whatever number you measure on your first run is your baseline,
and a close below it is deleted coverage you must account for test-by-test.

**NO FRONTEND WORK.** The owner is planning a complete frontend/UI/UX makeover.
`git diff --name-only -- frontend/` must be EMPTY in every commit you make.
Anything the UI needs, record as a residue in your PR body.

**What must not move**, in any commit:

* `backend/tests/golden/rne_envelope.json` stays
  `e27cdcf02ed8579b1210c1d55d484cf20d604b2f08cb379c814d3d4ba1e42c79`
* `git diff --name-only -- backend/app/services/phase3/ backend/tests/golden/`
  stays EMPTY
* `backend/tests/test_assessment_reference_acceptance.py` — KEEP VERBATIM
* `backend/data/Testing/` — the owner-supplied fixtures. Untouched.

---

## 1. The doctrine — this is not optional and it is unusual

**Read `/CLAUDE.md` in full before writing a line.** Summary, but read the
original:

> Every decision that requires judgment goes through the model. Not a rule, not
> a regex, not a threshold, not a keyword list.

Concretely forbidden: regexes or keyword vocabularies that classify content;
numeric thresholds that decide meaning; volume-derived structure; shape-matching
standing in for comprehension.

**Explicitly allowed** — and most of your work is here: parsing, ID assignment,
caching, ordering, atomic writes, schema validation, and **gates that refuse a
broken artifact**. Image durability is almost entirely mechanics. Say so where
it is, so a reviewer can tell you thought about it rather than not noticing.

Three register decisions that will bite:

* **Q13** — a mid-run block becomes ONE recorded, flagged decision and the run
  COMPLETES. Only the three pre-spend pauses and genuine impossibility stop a
  run. If you find yourself adding a `raise` on a path reached during a
  generation run, that run has already spent its entire model budget and you are
  destroying finished work.
* **Q10** — a critic advises, never gates.
* **R4** — nothing a learner would see is ever silently lost. A dropped value
  with no record is the defect; the drop alone is not.

This codebase has shipped a Rule 1 disaster before: a bold-vs-heading rule once
took a chapter's questions from **24 to 0**. That is why the doctrine reads the
way it does.

---

## 2. The working method — follow it, it is what keeps this safe

For each slice:

1. **Map** — read the real code, measure the current behaviour, write down what
   you found with `file:line`. Do not trust docstrings; several in this repo are
   stale and one actively lies.
2. **Spec** — decide, record the decision AND the argument against it that lost.
3. **Implement.**
4. **Audit adversarially** — at least two independent passes with *different*
   lenses, each trying to REFUTE the work rather than confirm it. One must be a
   doctrine lens (hunt for Rule 1 violations, mid-run halts, R4 loss). One must
   be an integrity lens (full suite, no weakened tests, frozen paths).
5. **Repair** — verify each finding by RUNNING the code before acting. Auditors
   in this project have repeatedly reported a real failure with the wrong
   mechanism, and on four slices the repair found defects every audit missed.
   Check the mechanism, and look once at the thing NEXT TO what was reported.
6. **Verify yourself** — full suite alone from a fresh dir, hashes, frozen
   paths — then commit.

Every regression you write must FAIL before your change and pass after. Prove it
by neutralising your fix and watching the test go red. Say in the PR which ones
you checked that way.

---

## 3. What you are building — Step 10, Q8

**The goal, from the Aegis document:** published assets get **non-expiring
public URLs**, plus volume backup. Every asset already carries a content hash.
The manifest-driven URL rewrite is *designed, not built*.

**What exists today** (verify all of it yourself before designing):

* `backend/app/api/source_assets.py:15` — serves an asset only after
  `fallback.validate_asset_signature(job_id, filename, sig)`.
* `backend/app/services/canonical_source_phase221_fallback.py:204-221` —
  `asset_signature()` and `validate_asset_signature()`, keyed on
  `AEGIS_SOURCE_ASSET_SECRET` / `config.SOURCE_ASSET_SECRET`.
* Other consumers: `app/main.py`, `app/services/canonical_source_phase3.py`.

**The problem in one sentence:** an HMAC-signed URL is not durable. A published
workbook that a school opens next term must still resolve its images, and a
signature scheme tied to a rotatable secret cannot promise that.

**Your first task is a MAP, not code.** Answer these, with evidence:

1. Where does an asset URL enter a *published* artifact? Trace it into the
   workbook cells and the release payload. Which of the four outputs carry image
   references at all?
2. What exactly does the signature protect against, and who is the attacker? If
   the answer is "nothing that matters for a published asset", say so — that
   changes the design.
3. If `AEGIS_SOURCE_ASSET_SECRET` rotates, what breaks, and how would anyone
   find out? Is there a published workbook in the wild whose images would go
   dark?
4. Every asset carries a content hash — where is it, and is it stable across
   re-runs? (Step 8 learned the hard way that "stable" ids were re-derived from
   a release hash and moved on every content edit. Check, do not assume.)
5. What is the volume-backup story today: what is on the Fly volume, what
   happens on redeploy, and what is the recovery path?
6. What would a **content-addressed, non-expiring** URL look like here, and what
   breaks if you switch to one — old signed links, tests, the manifest,
   anything cached?

**Then write a spec** naming the slices, and only then build. Expect roughly
5–8 hours of work total.

**Explicitly out of scope:** the UpSchool-environment migration is a designed
later step. Do not start it.

---

## 4. What to report

Your PR body must contain:

* What you measured before the change, with `file:line` and the commands.
* Each decision and the argument against it that lost.
* Every regression by name, and which you proved fail-without-fix by
  neutralising.
* The exact suite tail, the fresh dir used, and the baseline you are comparing
  against.
* The frozen-path checks: golden hash, empty phase3/golden diff, empty frontend
  diff.
* Residues: anything real you found and deliberately did not fix, with the step
  that owns it. Anything the UI needs, under a "frontend makeover" heading.

If you find something that contradicts this brief, **say so with the command
that shows it** rather than silently working around it. Three briefs in this
project have turned out to be wrong about a coordinate or a premise, and
catching that is worth more than smooth progress.
