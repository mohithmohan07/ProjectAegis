# Restructure handoff — method, conventions, and briefs for §10 steps 6–12

Steps 1–5 of `docs/aegis-restructure.md` §10 are complete on branch
`claude/phase-3-rewrite-migration-8wg2ax` (PR #227, commits `19479be..ef9160a`).
This document hands the remaining steps to the next implementer. It is not a
spec — the spec is `docs/aegis-restructure.md` plus the §12 decision register.
It records **how the first five steps were built**, so steps 6–12 stay
consistent in method, code shape, and discipline.

## 0. The two documents that outrank this one

1. `/CLAUDE.md` — Rule 1 as amended per Q13 (16 Aug 2026). Read it before
   every step. The short form: no deterministic judgment about content
   meaning, ever; model verdicts with independent **advisory** critics
   (dissent flags, never gates — Q10); mid-run blocks go to **The Fixer**
   for one recorded, flagged, content-addressed decision (Q13); only the
   three pre-spend pauses and genuine impossibility stop a run; nothing a
   learner would see is ever lost silently (R4).
2. `docs/aegis-restructure.md` — §4 for each phase's target behavior, §5 for
   the house format, §8 for the three agents, §10 for the build sequence,
   §12 for the decided rulings. When the doc and the code disagree, the doc
   governs; when two doc passages disagree, the §12 register governs.

## 1. The working method (used for every step so far)

Each step ran the same four-stage loop. Keep it.

1. **Map first, read-only.** Before writing anything, produce an exhaustive
   inventory of the machinery the step touches: file:line for every author,
   consumer, validator, cache key, test, and golden fixture at risk. End the
   map with a ranked list of gaps/design tensions. (The maps for steps 1–5
   lived in a scratchpad; their durable conclusions are in the PR #227 body
   and the commit messages.)
2. **Decide the design before implementing.** Every tension the map surfaces
   gets an explicit resolution written into a spec *before* code is written
   — including which golden fixtures are allowed to change and why. Do not
   let the implementation discover the design.
3. **Implement with regressions.** Every behavior change is pinned by a new
   test. Deletions get "the machinery is gone" pins (e.g. assert a template
   string appears nowhere under `app/`). Replacements pin the new behavior,
   not the absence of the old.
4. **Verify independently, then ship.** Re-run the FULL suite from a clean
   isolation env yourself — never trust a reported green. Then one commit
   per coherent unit, push, and extend the PR body with an honest account
   (including defects found and golden diffs, each explained).

## 2. Build/verify mechanics

- Work from `backend/`. The full suite:

  ```
  cd backend
  AEGIS_DB_URL="sqlite:////tmp/<unique>/step.db" \
  AEGIS_DATA_DIR="/tmp/<unique>/step-data" \
  python3 -m pytest tests/ -q -p no:cacheprovider
  ```

  Always use fresh, per-run `AEGIS_DB_URL`/`AEGIS_DATA_DIR` — the
  chapter-reading and assessment-materialization disk caches leak across
  runs that share a data dir and produce phantom failures. Wipe both dirs
  before a final verification run. `AEGIS_ALLOW_DRY=1 AEGIS_USE_LIVE=0`
  come from conftest for offline runs.
- **Golden gates** (must stay green on every commit):
  `tests/test_phase3_{settle,host,assemble,runner}_golden.py`,
  `test_phase3_kernel.py`, `test_phase3_flip_seam.py`, `test_phase3_polish.py`,
  plus the newer `test_phase3_place.py` / `test_phase3_analyse.py` /
  `test_phase3_case_uniqueness.py` golden replays.
- `pyflakes` every touched file; compare against the base commit (git stash)
  so only *new* findings count. Known pre-existing findings are left alone.
- Corpus fixtures live in `backend/data/Testing/` — note the filename
  `"jemh105 (1).mmd"` contains a space.
- Suite size at step-5 close: **1891 passed, 7 xfailed** (~60s). If your
  final count is lower than the count you started from, you deleted
  coverage — account for every removed test by name.

## 3. Code conventions that keep the pipeline consistent

**Model verdicts.** Every new judgment goes through
`phase3/kernel.py::decide()`:
- `kind` namespaced by pass (`"place.container02"`, `"analyse.inventory"`,
  `"fixer.<block>"`), `unit_id` the judged unit, `envelope_sha256` from the
  sealed envelope, an explicit `policy_version` string that changes whenever
  the pass's rules change (that is what re-keys stored decisions — see
  `polish.py`'s `"content-codes:…"` and step 5's `"-q1"` suffix precedents).
- Checkers are **mechanics only**: schema shape, ids resolve, exactly-once
  accounting. Never a content-meaning vocabulary in a checker.
- `critic=` is advisory; its dissent lands via `kernel.advisory_flags`.
- `fixer=` wired on every live call site (Q13); dry/test paths may inject a
  fake or None.
- Decide-once: same inputs must replay with zero provider calls — pin it.

**Pass shape.** New post-81% passes follow `place.py`/`analyse.py`: pool →
batched `decide` → checker/critic/fixer → result maps returned through
`runner.py` → **assemble stamps row-private `_aegis_*` markers before the
deterministic deposit pipeline**, and the pipeline must stay a fixpoint over
whatever you stamp (regression-pin idempotence). Snapshots go to
`<artifact_dir>/source.phase3-<pass>.json` beside the decision store, which
makes them ship in diagnostics for free.

**Audit fields.** Row-private markers named `_aegis_*`, registered in
`build_concepts_release.py::_RELEASE_AUDIT_FIELDS` (ships in the release
payload, stripped before DB upload). Internal ids (TYPE/CASE/LA/QINV) never
render in visible workbook text; the §5 rendered shapes are parsed strictly
— do not add visible slots to them.

**Evidence discipline.** Placement/allotment payloads carry content evidence
only. Printer position (`source_start`, page numbers) is deliberately
excluded — pin its absence with a regression, as steps 4–5 did. Concept
evidence is the Description text, never Types/Examples/analysis (circular
evidence).

**Flags, not gates.** Anything sub-floor, dissenting, or Fixer-decided ships
with a `review_flags` entry stating what was blocked / decided / why. The
only fail-closed stops: the three pre-spend pauses, certificate/identity
mechanics, protocol impossibility, provider/quota failure.

**Golden fixtures are recorded verdicts.** Regenerating one is legitimate
only when the schema or authorship moved by design; author new fixtures the
way `rne_place.json`/`rne_analysis.json` were authored (derived from
recorded evidence, documented), and never hand-fudge a hash to make a gate
pass. The envelope fixture (`rne_envelope.json`) changing is a red flag —
steps 4 and 5 both kept it byte-identical deliberately.

**Prompts.** Registered prompts (`app/services/prompts.py`) for
generation-side scaffolds (admin-overridable); `phase3/prompts.py` module
constants for phase-3 systems. No numeric floors, counts, or quotas in any
prompt — say "state your honest confidence; a low-confidence decision ships
flagged for review" and "there is no quota".

**Commits and PR.** Develop only on
`claude/phase-3-rewrite-migration-8wg2ax` unless the owner says otherwise.
One commit per verified unit; the body explains what moved and why, names
suite counts and golden diffs, and never contains tool/model identifiers in
code comments or artifacts. Extend the PR body per step with the same
honesty (defects found included).

**This branch is shared and unmerged.** Steps 1–5 live on it and PR #227 is
open against it. Therefore: `git pull origin claude/phase-3-rewrite-migration-8wg2ax`
before starting work *and* before every push; **never force-push, never
rebase published history, never `reset --hard`** on this branch. A force-push
here silently erases completed steps — it is the single worst failure mode of
this setup, and no situation on this branch requires one. If histories have
diverged, merge; if that looks wrong, stop and ask the owner. Do not open a
new PR per step while the branch is unmerged — extend #227's body with a
section per step instead.

## 4. Known residue to pick up in later steps (flagged during steps 1–5)

- `" — {group_type}"` group-name minting sites in `post_generation.py` and
  the BG/IG/AG display-name conventions → **step 6** (Q12 decides naming).
- `_is_answer_key_source_section`, `_CHAPTER_WIDE_TASK_HEADING_RE`,
  `_CHECKPOINT_CONTAINER_HEADING_RE`, `_EXERCISE_RE`, and the fallback's
  worked_example/exercise label mapping (`canonical_source_phase221_fallback`
  ~:2900 area) — deterministic label mechanics left on the neutral path;
  revisit whenever a step touches that lane.
- Pre-Learning lane: `_ensure_misconceptions_via_api` (per-pre-concept
  analysis) and the whole separate pre-learning flow → **step 7** (Q3
  removes the separate flows; capture moves inside the one run).
- Fixer seams deliberately left raising (enumerated in
  `phase3/fixer.py`'s docstring): writer/publication seams (F43–F45) →
  naturally revisited at **steps 6/8**; F27–F34 bounded-retry adjacents.
- Signed asset URLs (`api/source_assets.py`) are not yet "durable public
  links" → **step 10** (Q8).
- MES/assessment-lane refinement and group-description quality were scoped
  out of the Refiner → **step 6** wires that lane through
  `release_refiner.py`'s `output_kind` parameter.

## 5. Per-step briefs (6–12)

Read the doc sections named in each brief before mapping. Estimated scale is
relative to step 5 (≈1 map + 1 build agent-day).

### Step 6 — Groups + Master File passes (Output 02 ships)
Doc: §4 Phase 4.5, §5, §6, §9 rows on grouping/MES, Q11 (rubrics v2.0,
Math/Physics families stay Open), Q12 (group naming).
- Level verdicts (Basic/Intermediate/Advanced) and variant clustering are
  **model verdicts with advisory critic** — the existing MES grouping engine
  (`assessment_grouping.py`, `assessment_quality.py`) is the code to
  restructure to kernel-decide form, not to delete.
- Open/Specific classification per Q11: the rubric registry is versioned
  evidence for the model, **never executable classification** — grep for any
  code that branches on rubric text and purge it like a §3 residue.
- Q12 naming: `group_name` = `group_display_name` = "Concept name — Tier";
  machine identity stays in `group_key`. The `" — {group_type}"` minting
  sites are the entry point.
- Marking schemes: arithmetic identities (marks/weightage) are mechanics —
  keep the read-back validators (`assessment_workbook.py:505+`) intact.
- Output 02 = the Master File on the SOP/MES schema; check Q5 before
  touching schema constants (full migration is step 8; do not half-migrate).
- Wire the MES lane into the Refiner via `output_kind`.

#### Step 6 — resolved questions and binding constraints

These were raised by the step-6 map and are **decided**; treat them as part of
the brief above.

**Slice 3 carry-forward (retired in Slice 4; preserve these guarantees).**
Slices 1–3 landed and were independently audited clean (grouping, then routing
+ cells + materialization; both slice-2 carryovers retired, concepts pipeline
untouched). Slice 4 completed all three follow-ups; they remain regression
constraints for later assessment work:

1. **`_FIXER_UNACCEPTABLE_CODES` does not exist** — an earlier version of this
   handoff referenced it as the fail-closed pattern. It was never needed: the
   kernel re-validates the Fixer's output against the *same* checker and raises
   `ContractError` on any surviving structural defect, so every marking /
   identity / arithmetic defect is unacceptable-with-flag by construction. That
   is stronger than a code list. This is now recorded in
   `docs/assessment-decision-registry.md`; do not add the named set.
2. **Two schemas share `kind="assessment.cell"`** — the legacy
   `build_assessments._recorded_cell_marks` and the MES `assessment_cells`
   `decide_cells` use the same decision kind with different response schemas and
   checkers. Replay is safe today because their `policy_version` strings differ
   (`assessment-legacy-cell-contract-1` vs `assessment-cell-1`), but two schemas
   under one kind was an audit-registry smell. Slice 4 renamed the legacy kind
   to `assessment.legacy_cell_contract` and bumped its policy to
   `assessment-legacy-cell-contract-2`, deliberately recording a re-decision;
   preserve that separation.
3. **`cell_id` uniqueness is implicit** — `assessment_cells.decide_cells`
   derives `cell_id = "CELL-" + decision_key[:16]` (64-bit) with no explicit
   output-uniqueness assertion; a collision is caught only downstream by the
   materialization duplicate-`candidate_id` raise. Slice 4 added an explicit
   post-decision uniqueness assertion and prefix-collision regression; preserve
   both.

The implemented decision identities, audit fields, replay behavior, and the
kernel's same-checker Fixer guarantee are recorded in
`docs/assessment-decision-registry.md`.

**The Open/Specific registry — now in the repo (AUTHORITATIVE).** The owner
supplied the corrected v2.0 workbook; it is committed as
`docs/open-specific-registry-v2.xlsx` (the binary source of truth), with a
complete faithful transcription in `docs/open-specific-registry-v2.md`
(`status: AUTHORITATIVE`, `registry_id: registry-v2.0`). Pass the `.md`'s
complete text into the `assessment.answer_restriction` payload and hash the
file into that pass's `policy_version`. **No code parses it, indexes it, or
branches on subject, question type, Policy ID (e.g. `CHEM-A01`), keyword, or
family name** — the workbook states this of itself ("never an executable
lookup table"; "never from command words or local matching") and its own
legend carries the no-local-fallback invariant verbatim. Note two things the
registry makes explicit: the Objective policy is *verify-then-classify* (after
the API and critic verify a closed response contract and exactly one correct
option → Specific), not a blind Objective→Specific default (which it records
as removed); and the Math/Physics families carry paired Open/Specific policies
(`MATH-B0n-O`/`-S`) so the carve-out lives in the evidence, not in code. The
`.md` also keeps a small **supplementary** calibration set (the accepted
reference-workbook verdicts) as a second corroborating source. If a further
correction of the workbook ever arrives, swap the files: the policy hash turns
it into an automatic re-decide, no migration.

**Slice 4 marking-evidence contract.** The full source named *Question-Paper
Blueprint & Analysis* is not tracked in this repository. Until that owner
artifact is supplied, the recorded assessment blueprint cell is canonical for
the total marks and the adopted Phase-05 rules in `docs/aegis-restructure.md`
are the marking-decomposition contract. The legacy, admin-overridable
`assessment.rubric` prompt and the Open/Specific registry's worked mark
examples are not substitutes for the missing artifact. Every
`assessment.marking` audit records this evidence status alongside its explicit
cell id and marks. A future owner artifact must be added as whole, versioned
model evidence and hashed into a bumped marking policy; it is never recreated,
parsed into executable rules, or silently claimed to have been present.

**The Refiner dispatch is step-6 code.** There is no file-level freeze on
steps 1–5. The real constraints are: golden fixtures are recorded verdicts and
are never hand-edited; `rne_envelope.json` drifting is a red flag; the three
pre-spend pause suites pass unchanged; and the `concepts_release` branch of
`release_refiner.py` stays byte-behavior identical. An additive
`assessment_master` dispatch at the existing `output_kind` seam, with the
assessment logic in its own module, satisfies all of these.

**The Master Refiner must not alter `question` or `question_text`.** Variant
clustering groups questions *by their wording*, and §8.3 forbids the Refiner
from revisiting a decision — rewording a clustered question would invalidate
the verdict that placed it with its siblings. The question text is already
polished at Phase 2.3 and is the learner-facing item. The Refiner polishes
around it: rubric wording, answer prose, group descriptions. This also makes
the §6 `question = question_text` identity a trivial unchanged-assertion
rather than a moved-together one.

**Marking arithmetic stays fail-closed.** A Fixer decision may never
accept-with-flag a weightage-sum mismatch, a mark-decomposition error, or a
duplicate `group_key`. That is data corruption, not judgment. No
`_FIXER_UNACCEPTABLE_CODES` set exists or is needed: the kernel re-validates a
Fixer response with the same checker and raises on any surviving defect.

**Do not merely route around `post_generation.py`.** It is still consumed at
`build_assessments.py:304` and `:619` (the legacy Build Assessments lane).
Dropping it from the Output-02 path leaves its `" — {group_type}"` naming and
deterministic cognitive-skill clustering live there, giving the codebase two
naming conventions. Resolve it against §9's verdict for that surface — fix the
naming per Q12, or retire the path — rather than leaving an unowned doctrine
violation. Not a blocker for Output 02; do not drop it either.

**Audit fields go in the existing list.** Steps 4 and 5 added
`_aegis_hub_placements` and `_aegis_analysis_allotments` directly to
`build_concepts_release._RELEASE_AUDIT_FIELDS`. Extend the same list; a
separate assessment-local registry is indirection for no gain.

**Land step 6 in verified slices.** Ten new decision kinds plus a Master
Refiner is the largest step in the sequence. Each slice gets its own commit
with the full suite green: (1) Q12 naming + read-back provenance ledger;
(2) level verdict + variant clustering + group descriptions to
`kernel.decide`; (3) routing + cells + materialization; (4) Open/Specific +
marking as distinct passes with the registry evidence; (5) the Master Refiner
dispatch.

**Confirmed findings from the step-6 map** (act on all of them): the
Objective→Specific executable policy at `assessment_materialization.py:64-68,
145-148` is a §3-style residue that Q11 retires; the difficulty→tier mapping
at `assessment_grouping.py:24-27, 100-106` is a Rule 1 violation replaced by
an independent level verdict; the 2,000-character semantic truncations and
`sub_question_count`-as-evidence are volume proxies to remove (counts stay
mechanics); the Q12 read-back conflation — validating groups by visible name
when two families share one friendly name — is real, and a manifest-only
`(sheet,row) → group_key` provenance ledger is the fix; and Output 02 must
route against the **staged Output-01 release payload**, never re-reading
concept meaning from mutable database rows.

### Step 7 — Phase 03 pre-learning capture (Outputs 03–04 ship)
Doc: §4 Phase 03, Q3, Q4/D3 (adaptive-40).
- Capture prerequisites **during** the one Build Concepts run (the model
  keeps a running capture across phases — likely a per-stage capture channel
  merged by a settle-style pass), then build the Pre map to the same
  detailing standard, with needed-for links critic-verified.
- Remove both legacy pre-learning flows (upload flow + derive-from-existing)
  and the offline `aegis_pipeline/concept_mapping_to_prelearning.py` tool's
  role; Q1 applies to the Pre lane here (retire
  `_ensure_misconceptions_via_api` in favor of the analyse pass).
- Adaptive-40 per Q4: target, not quota — variance carries an authored,
  critic-flagged rationale. No `40` literal in any validator.
- The Pre Master contains generated questions only.

### Step 8 — Four-output release on the SOP/MES schema (Q5)
Doc: §6, §7, Q5.
- Migrate schema constants, writer, and acceptance tests to the
  reference-school layout (`answer_restriction`, keywords,
  related_concepts); auto-detect keeps older canonical workbooks readable.
- Per-topic/per-concept ID minting and the QC-checklist audit join the
  release audit. The two release systems (build_concepts_release vs
  assessment_release) converge here — map both before choosing the survivor.

### Step 9 — Review/edit surface
Doc: §7. Rendered pages, inline edit, instruction-box apply API
(`concept_revisions.py` is the existing diff machinery), frontend
simplification to one Build Concepts action. First step with real frontend
work — `frontend/src/` has pinned tests.

### Step 10 — Image durability (Q8)
Non-expiring public URLs for published assets (today's are HMAC-signed —
see `api/source_assets.py`), Fly-volume backup. Every asset already carries
a content hash; the manifest-driven URL rewrite is designed, not built.

### Step 11 — Language mode
Doc: §4 Phase 2.1 language mode, Q9. Poem/prose topology as a subject
adapter inside Phase 2.1 (the Architect already selects and records the
mode — this step implements what the selection drives). Validate on a real
chapter (e.g. *The Elevator*) — needs live API.

### Step 12 — Staging acceptance corpus
Doc: §10 item 12. Grades × subjects × boards × text/scanned × images ×
maths × English, plus fault injection (API dissent, quota failure, asset
failure, cache alteration, interrupted release/publication). Live-API
acceptance runs; budget human review time for the flagged output.

## 6. What "done" means for any step

- The doc section's behavior exists and is pinned by regressions.
- No new deterministic judgment about content meaning anywhere (reviewers
  should grep your diff for regexes/thresholds/counts near content).
- Full suite + golden gates green from a clean isolation env, verified by
  the orchestrator (you), not just reported by a sub-task.
- Every golden diff explained; envelope fixture untouched unless the step's
  design explicitly moves it.
- Committed in coherent units with honest messages; PR body extended.
- The three pre-spend pause suites pass unchanged — they are the canary
  that the Fixer's carve-out is intact.
