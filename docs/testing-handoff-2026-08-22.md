# Aegis Testing Handoff — 2026-08-22

Current build under test: local integration branch
**`assistant/master-storage-recovery`**, based on the integrated `xhigh` and
owner-feedback mainline. It includes cache ordering/telemetry, Q21 workbook
contracts, durable Pre-Learning recovery, the Concept visible-route gate, and
the narrow ENOSPC/Master-only recovery round. Neither the storage change nor
Q22 is deployed or live-tested.

The **2,791 passed** figure below belongs to historical PR #240. Use the newest
final-gate row in `docs/residue-ledger.md` for the current branch.
What automated checks CANNOT exercise is live behavior — real provider
latency, a real textbook, the phone console — which is exactly what this
plan covers. Anything marked **FAIL-IF** is a bug: report it with the run
log and I will fix it.

---

## 0. Deploy sequencing — decide once, then follow in order

You have a killed run parked at the Masters stage (CH02 Measurement /
"The School Bell Rings Again..."). The old Option A/B replay-free advice is
superseded. Follow one order only:

1. Confirm no worker is active.
2. Merge the current integration branch, then deploy between chapters.
3. Run a **fresh same-PDF acceptance** for matched-scope cost evidence.
4. Resume the parked job only if its remaining spend is still useful.

The checkpoint remains durable, but changed materialize/restriction/marking/
routing policy versions intentionally re-key those Master decisions. Unchanged
decisions replay; changed ones may re-author. Never describe the resume as
free, and never deploy while the worker is active.

Deploy commands (only after the owner approves and merges the current branch):

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
| 7 | **Q22 uniform Luna effort** | Every one of the 14 registered purposes requests `xhigh` on a normal Luna call. Purpose labels remain mandatory. Only provider rejection or structured-output truncation recovery may lower a retry; durable decisions retain their existing identity. |
| 8 | **Master storage recovery** | Refuse a Master batch before provider spend when server bytes/inodes are insufficient, expose retryable storage evidence, and rebuild only a missing Pre or Post Master from its surviving Concept release after capacity is restored. |

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
- The real `Building Master files (Outputs 02/04)` stage shows both `cached`
  and `cache write` chips at aggregate and lane level when those counters are
  non-zero; Master usage must not appear under concept extraction.
- Parallel lanes (Inventory early track, Place ∥ Analyse ∥ Polish, the two
  Masters) render as separate colored rails with their own totals.
- After an automatic retry, the headline remains cumulative but stage/lane
  rows belong only to the newest server attempt; repeated same-title cards do
  not each claim the newest attempt's cost.
- **No** "capacity is busy / slot acquired after 0s" pairs during the
  Masters stage — at most an occasional wait longer than ~5s, reported once
  with its true duration.

**FAIL-IF** 0-second slot messages still flood the log; a stage card's cost
column stays empty while tokens climb (pricing gap); lanes collapse into one;
Master cache tokens are charged to the preceding concept stage; or an older
same-title retry card duplicates the current attempt's cost.

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
- Record the Pre Master cost separately. The lower Q20 volume should reduce
  its candidate-driven work, but no percentage reduction is an acceptance
  fact until a matched live run measures it.

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
Run the **same PDF** as the $4.9718 screenshot, then send:
1. The stage table (screenshot is fine) — per-stage time/tokens/cost.
2. Total run wall-clock and total cost.
3. Pre-learning plan numbers (N concepts, M questions).
4. Per-lane cache reads, cache writes, ordinary input, output tokens, request
   count, and source-question count.
5. Confirmation that the selected provider/model is OpenAI GPT-5.6 Luna, plus
   every `reasoning effort negotiated` log line if one appears. A lower effort
   is valid only when the accompanying logs tie it to provider rejection or
   truncation recovery.

The $4.9718 screenshot is **not** a four-output baseline: Pre Concept and Pre
Master were absent. Report the new four-output total separately. Compare the
new Post Master with the old Post lane where its journal/stage evidence is
available; otherwise report normalized Post cost per source question and the
cache-read/write ratios. Treat **$3.48** only as an aspirational absolute
affordability target, not as proof of a causal 30% saving. Cache ordering is
accepted only when repeated shared input moves materially from cache writes to
reads; no offline estimate is a pass.

### T9 — Owner workbook-format acceptance

Download both Master workbooks and verify representative rows plus a whole-file
search:

- typed `Equation` answer/rubric cells contain full raw LaTeX and no `[Katex]`;
- typed `Phrases` cells contain wholly plain text; each rubric block may choose
  its own medium, but no block mixes media;
- objective options in `question_text` are `a)`, `b)`, `c)`, `d)`;
- no `tabular`, `array`, or Markdown pipe-table syntax remains; mechanical
  fallback text names every row/column cell unless the source table image is
  used;
- every four-mark Descriptive row has at least two rubric blocks.

**FAIL-IF** any forbidden syntax survives, or manually changing one of these
back to the invalid shape passes Master read-back/import instead of refusing
before database mutation.

### T10 — Concepts, Pre output, and source-question accounting

- All four downloads (01–04) are present, or a missing paid authority appears
  as a downloadable Diagnostic sibling with the reason recorded.
- Every source QID finishes placed or explicitly flagged; blocked/unaccounted
  source questions must be zero for owner acceptance.
- The Concept File contains visible Types with Case/Example question detail
  somewhere when the source has questions. Individual description-only
  concepts remain valid, matching the two standards workbooks; a whole-file
  descriptions-only escape must be Diagnostic and refuse database publication.
- Clicking release on an existing Post-only historical job restores durable
  Pre authority without model/refiner spend, is idempotent, and returns 409 if
  the job is active.

### T11 — ENOSPC recovery: Master-only retry (operator + browser)

**Preconditions**

1. Confirm no generation/rebuild worker is active. Inspect the live filesystem
   using the ENOSPC runbook in `README.md`; extend the existing volume if
   needed, then deploy between runs.
2. Read `/health`. The HTTP response stays 200 for liveness; require
   `storage.status: "ok"` and `storage.two_lane_batch.ready: true`; record the
   available bytes/inodes plus both the one-lane retry and two-lane batch
   requirements before retrying.
3. Open the failed job whose Pre and Post Concept Files are downloadable and
   whose Master cards are unavailable. Download both Concept files and record
   their filenames and parsed Chapter/Topic/Concept cell projections before
   any rebuild. A raw XLSX SHA-256 is not the content comparison: XLSX package
   metadata contains openpyxl wall-clock timestamps even when every data cell
   is unchanged.

**Browser steps**

1. A hard refresh must automatically reopen the same saved job and reconstruct
   all four output cards. If that lookup meets a temporary deploy/network
   failure, confirm the browser keeps the saved-run pointer and offers
   **Retry saved run** instead of discarding the paid run.
2. On one unavailable Master card, click **Rebuild Master File** once. Confirm
   its button becomes busy and the sibling rebuild/upload actions remain
   disabled until the request finishes. Do not upload the PDF or press Resume.
3. Refresh or reopen the job. Confirm that lane's Master now downloads and its
   same-lane Concept download is still enabled. Download the Concept again and
   compare its parsed Chapter/Topic/Concept projection with the pre-rebuild
   copy. If another tab owns the rebuild, this card must poll until the durable
   result appears and the remaining action unlocks.
4. If the other Master is also unavailable, repeat the same one-lane action
   only after the first finishes. Refresh again and download all four outputs.

**Expect**

- The Pre button uses `POST /build-assessments/releases/from-job/{job_id}/pre`;
  the Post button uses `POST /build-assessments/releases/from-job/{job_id}`.
- The before/after Concept projections and staged
  `source_concept_release_sha256` match. Every rebuilt Master row carries the
  same shared authored Chapter/Topic/Concept values as its same-lane Concept
  projection. Presentation/linkage fields that the format deliberately owns
  separately are not content drift: the Concept topic-title prefix, a
  profile-forced blank such as `chapter_duration`, populated Master-only group
  and question label aggregates, and a column absent from a target sheet.
  The existing frozen job, staged Concept release, source inventory and durable
  decisions remain the authority; only the requested Master lane is rebuilt.
- The run/activity log contains Master preflight/rebuild/publication work only:
  no PDF conversion, canonical-source reconstruction, concept extraction,
  Phase 3 Concept authoring, or new full-run start appears. Record any Master
  provider usage separately; a retry may finish a decision that ENOSPC did not
  persist, so "Master-only" does not mean "zero model cost".
- A failed 507 keeps both Concept downloads available, shows actionable server
  storage guidance, and permits the same button to be used after capacity is
  restored. A concurrent original run or second rebuild returns/behaves as a
  409 conflict rather than overlapping the job.
- After success, all four outputs download and no `vN.staging` debris remains
  under the affected assessment release.

**FAIL-IF** either Concept artifact disappears or any shared authored
Chapter/Topic/Concept value changes; clicking Rebuild
starts source conversion or the Concept pipeline; both lane rebuilds overlap;
a lost response leaves a successfully published Master shown as unavailable
after refresh; storage exhaustion returns an opaque 500; a partial Master is
served; or retry requires a full-file generation run.

---

## 3. Known limitations — so nothing here surprises you

- Old parked-run decisions whose policy identity changed may re-author; durable
  does not mean replay-compatible after an intentional policy bump.
- Journals from runs started on builds before Console v2 have no per-stage
  usage events — their stage cards show structure but not cost.
- The Example-ownership verdict records and flags; it does not rewrite or
  delete content. Fixing a genuinely unowned Example is your call via the
  ordinary revision loop ("Review and correct the output").
- The Architect's occasional clumsy sentence in RUN INSTRUCTIONS (like the
  "British-style spellings" one) is model prose already flagged by its
  critic — recorded, advisory, queued for a prompt-side improvement, not a
  code bug.
- Payload cache-ordering and Q22's uniform `xhigh` policy are built but
  live-unproven. Remaining cost/time levers are critics off the latency path,
  parallel grouping/QA tails, and the later Batch API lane. Because Q22 raises
  formerly low/medium/high purposes while lowering formerly `max` purposes,
  only the same-PDF run can establish the net cost and quality effect.

## 4. If something fails

Send: the chapter name, the run log around the failure (the stage cards
view is enough for timing issues; Raw for content ones), and for release
issues the `release.json` / diagnostics zip. Every recorded decision in
this build carries its policy version and rides either the release
`issues` ledger or a payload ledger, so a failure is diagnosable from the
artifacts alone — nothing depends on catching it live.
