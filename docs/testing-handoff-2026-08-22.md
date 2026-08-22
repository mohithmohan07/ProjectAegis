# Aegis Testing Handoff — 2026-08-22

Build under test: **PR #240** (4 commits, branch `claude/project-aegis-review-6a2357`)
on top of `main`, which already carries **PR #239** (Watch live + auto-landing)
and the Console v2 / parallel-tracks / Q14–Q18 rounds.

Verification already done on this build: full backend suite **2,791 passed**,
frontend suite 87 passed + `tsc` + production build, and two adversarial
review rounds (9 findings then 4; every substantive one fixed in-branch).
What automated checks CANNOT exercise is live behavior — real provider
latency, a real textbook, the phone console — which is exactly what this
plan covers. Anything marked **FAIL-IF** is a bug: report it with the run
log and I will fix it.

---

## 0. Deploy sequencing — decide once, then follow in order

You have a killed run parked at the Masters stage (CH02 Measurement /
"The School Bell Rings Again..."). Two valid orders:

**Option A — safest for the wallet:** Resume the parked run on the CURRENT
deployed build, let it finish (~$5–9, ~30–60 min), THEN merge #240 and deploy.
The parked chapter will NOT carry the new Example-ownership record (old code
staged it); everything else tests on your next chapter.

**Option B — best for testing (recommended):** Merge #240 → deploy → resume
the parked run on the NEW build. Everything already decided replays free from
the durable decide-once store; staging re-runs with the new code, so even the
parked poem chapter gets the Example-ownership verdict (it is the chapter
known to produce 7 findings — the perfect test case). Bounded risk: if the
final checkpoint is rejected at deposit and the run falls back to an earlier
stage, the Q20 prompt change re-keys the pre-learning plan and re-authors the
pre questions at the new ~5 calibration (extra spend, different Pre outputs).
That fallback is rare; if it happens, let it finish — it is still a valid run.

Deploy commands (after "merge it" on #240):

```
git checkout main && git pull
fly deploy --app projectaegis --ha=false
```

Deploy-sanity: open the app, start nothing, and confirm the home page loads
and the resume dialog for the parked run appears with **Resume** enabled
(worker not running).

---

## 1. Objectives — what this build is supposed to do

| # | Change | Objective |
|---|--------|-----------|
| 1 | **Q20 calibration** (register Q4→Q20) | Pre-learning plans target **~5 questions per pre-concept** (split model-judged) under a "minimum coverage that genuinely verifies the prerequisite" posture — roughly halving the Pre Master lane's cost and tail time. Still a target, never a quota: thin is never padded, rich is never capped, zero is legal, every plan carries a rationale. |
| 2 | **Example-ownership recorded verdict** | A public Type Example whose wording has no exact owner in the source Question/Task Inventory can no longer ship silently. At staging, one recorded model decision classifies each such Example (re-worded source task naming its qid / parser fragment / genuinely unowned) and the verdicts land on the release as a reviewer-visible issue. If the judge is off or fails, the finding is still recorded, marked unadjudicated. |
| 3 | **Truthful staging summary** | Release-first runs no longer end with the false "Created 0 post-learning concepts (0 merged)" + phantom workbook path. They say "Captured N concept row(s) for the staged release…" and the job detail matches. |
| 4 | **Quiet slot handoffs** | Sub-5-second waits for an OpenAI slot are silent; only real waits log. Your console stops drowning in busy/acquired pairs at full concurrency. |
| 5 | **Watch live + auto-landing** (merged, #239) | Any device can attach read-only to a running job and, when it completes, lands automatically on the download-and-review page. Watching never restarts a run. |
| 6 | **Console v2** (merged earlier) | Stage cards with per-stage time, tokens, and cost; parallel lanes as separate rails; mobile-friendly. |

---

## 2. Test plan — copy this section into your notes and tick as you go

### T1 — Resume & watch the parked run (phone + laptop)
**Steps**
1. Open the app on your phone. The dialog for the parked job should offer
   **Watch live** only if it is running (it is not) — expect **Resume**.
2. Tap Resume, confirm the setup restores (filename + target chapter), then
   resume from the checkpoint.
3. While it runs, open the app on a second device/tab → the dialog now says
   "Generation is already running" → tap **Watch live**.

**Expect**
- The watcher shows the same stage cards, with the run's REAL timestamps.
- When the run completes, the watching tab lands on the outputs/review page
  by itself — Run outputs card + "Review and correct the output" panel.

**FAIL-IF** the watcher restarts or re-bills the run; the dialog dead-ends
with a disabled button; completion leaves you stranded on the dialog.

### T2 — Console v2 stage table (during T1's run)
**Steps** Watch the Stages view during the run; toggle Raw once.

**Expect**
- One card per stage with live elapsed time, token and cost chips.
- Parallel lanes (Inventory early track, Place ∥ Analyse ∥ Polish, the two
  Masters) render as separate colored rails with their own totals.
- **No** "capacity is busy / slot acquired after 0s" pairs during the
  Masters stage — at most an occasional wait longer than ~5s, reported once
  with its true duration.

**FAIL-IF** 0-second slot messages still flood the log; a stage card's cost
column stays empty while tokens climb (pricing gap); lanes collapse into one.

### T3 — Q20 volume calibration (needs your NEXT fresh chapter, or the
parked run only if it fell back and re-planned)
**Steps** Run a fresh chapter end-to-end. In the log find:
`Pre-Learning questions: coverage planned for N pre-concept(s)` and
`M generated question(s)`.

**Expect**
- M lands in the neighbourhood of **5 × N** (the poem chapter's 10 concepts
  produced 69 at the old ~10 anchor; expect roughly 30–45 now). NOT a hard
  number: a genuinely rich prerequisite may exceed it, a thin one may plan 2
  or 0 — each with an authored rationale in the plan.
- The Pre Master stage card's cost is roughly **half** the poem run's.

**FAIL-IF** every concept plans exactly 5 (that is quota behaviour, the
opposite of the ruling); or totals stay ~10/concept with rationales that
just restate the target.

### T4 — Example-ownership record (the poem chapter fires this)
**Steps** After the run releases, open the release/diagnostics for the Post
lane (release.json or the diagnostics zip) and search `issues` for code
`unowned_rendered_examples`. Also find the console line
`Example ownership: N public Example(s) without an exact inventory owner…`.

**Expect**
- One warning-severity issue, `details.adjudicated: true` on a live run,
  with one verdict per Example: `source_variant` + the owning qid (a trimmed
  poem quotation is the typical legitimate case), `parser_fragment`, or
  `unowned` — each with a reason. `details.owner_qids` lists the claimed
  owners.
- The issue is chapter-level: it does NOT mark individual rows as errored.
- The old line "closed-world validation remains blocked" no longer appears;
  its replacement says the release stage adjudicates and records.
- A chapter with no such Examples: no issue, no model call, nothing to see —
  that is the correct silent case.

**FAIL-IF** the warning line appears mid-run but no issue exists on the
release (that is the exact defect this build fixes); `adjudicated: false` on
a live run WITHOUT a named failure reason; a verdict names a qid that is an
Activity/Info-Hub item.

### T5 — Truthful end-of-run summary
**Steps** Read the last lines of the run log after "Final concept count: N."

**Expect**
`Captured N concept row(s) for the staged release; nothing enters the
database until the release is published from the review page.` The job's
detail line matches. No "Created 0 post-learning concepts (0 merged)". No
"Output workbook path:" line (release-first runs write no shared workbook at
that point — the released workbook renders on download).

**FAIL-IF** any "Created 0" or workbook-path line on a release-first run;
or a captured count that differs from "Final concept count".

### T6 — Interactive release stays instant
**Steps** On a job with a saved checkpoint, use "Release newest output"
(the manual release button/route) once.

**Expect** It returns in seconds, exactly as before. If the released rows
carry unowned Examples, the issue appears with `adjudicated: false` and
reason "recorded without live adjudication (interactive release route)" —
recorded, not judged, never blocking the click.

**FAIL-IF** the button hangs for minutes (that would mean live model calls
leaked into the interactive route).

### T7 — The four outputs against the SOP (regression sweep of the merged
rounds; use the poem or your next chapter)
- **Group names** are SOP group IDs — `(<concept machine id>) B01/I01/…` —
  everywhere (workbook, Masters), never "Concept — Tier" friendly names (Q16).
- **Poem questions** quote only the needed lines, never the full poem (Q9 ruling).
- **Picture-bank questions** show ONE stitched labelled grid image, not a
  strip of separate URLs (Q17); grid presence is auditable in the row.
- **Post Master** carries no prerequisite-recap source questions; each
  claimed one is recorded under `pre_learning_claimed` with its reason (Q18),
  and removed generated duplicates under `duplicates_removed` (Q15) — check
  the release payload for both ledgers; their presence flips the release to
  READY_WITH_FLAGS, which is correct, not an error.
- **Durations** are model-judged minutes per question — no uniform value
  repeated across every question.
- **Types**: each Type owned by exactly one concept (Q14); consolidation
  moves are flagged for review, not silent.

### T8 — Cost & time acceptance (the numbers to send back)
After your next full chapter on this build, send me:
1. The stage table (screenshot is fine) — per-stage time/tokens/cost.
2. Total run wall-clock and total cost.
3. The pre-learning plan numbers (N concepts, M questions).

Baselines to beat: the poem run was ~66 min to the kill with the Post
Master lane at $1.47 / 5.2M tokens through routing, heading to an estimated
$10–12 total. With Q20 alone, expect the Pre Master lane near half; the
remaining big levers (payload cache-ordering, async critics, parallel
grouping tails) are queued for after your numbers confirm where the spend
sits now.

---

## 3. Known limitations — so nothing here surprises you

- The parked run resumed under **Option A** finishes on old staging code:
  its release will NOT carry the Example-ownership issue. Option B does.
- Journals from runs started on builds before Console v2 have no per-stage
  usage events — their stage cards show structure but not cost.
- The Example-ownership verdict records and flags; it does not rewrite or
  delete content. Fixing a genuinely unowned Example is your call via the
  ordinary revision loop ("Review and correct the output").
- The Architect's occasional clumsy sentence in RUN INSTRUCTIONS (like the
  "British-style spellings" one) is model prose already flagged by its
  critic — recorded, advisory, queued for a prompt-side improvement, not a
  code bug.
- The register's queued next rounds: payload cache-ordering (30–50% off
  Master input cost; deploys only between chapters, policy bump), critics
  off the latency path, parallel grouping/QA tails, author effort tiering,
  Batch API overnight lane.

## 4. If something fails

Send: the chapter name, the run log around the failure (the stage cards
view is enough for timing issues; Raw for content ones), and for release
issues the `release.json` / diagnostics zip. Every recorded decision in
this build carries its policy version and rides either the release
`issues` ledger or a payload ledger, so a failure is diagnosable from the
artifacts alone — nothing depends on catching it live.
