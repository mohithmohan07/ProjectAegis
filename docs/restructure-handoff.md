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
