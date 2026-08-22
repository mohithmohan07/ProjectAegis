# Aegis — assistant handoff, 2026-08-22

Written for the next AI assistant (or human) continuing this project with
no prior context. Everything here was true at `main` commit `5abb703`
("Merge PR #242"). The owner is Mohith (mohithmohan07 on GitHub); he
often works from a phone, rules by short messages ("merge it", "b) it
is"), and expects you to bring him decisions, not options-essays.

---

## 1. What this project is

**Clarius Aegis** converts school-textbook chapters (PDF) into
structured, tagged, bulk-import-ready learning assets for the Clarius
CMS. One generation run produces FOUR outputs per chapter:

- **Output 01 / 03** — Pre-/Post-Learning **Concept Files**: topics,
  concepts, descriptions, Types/Cases/Examples, learner analysis.
- **Output 02 / 04** — Pre-/Post-Learning **Master Files**: assessment
  questions (Output 02 = generated pre-learning questions; Output 04 =
  the chapter's own source questions), classified, materialized, marked,
  grouped, exported in the SOP Bulk-Import workbook format.

Stack: **FastAPI backend** (`backend/`), SQLite (WAL) at `/data`,
**React/Vite frontend** (`frontend/`), deployed on **Fly.io app
`projectaegis`** (single machine, shared-cpu-2x/2GB, volume at `/data`).
Model provider: OpenAI **gpt-5.6-luna** via JSON-mode chat
(`generation._openai_json`), policy in `backend/aegis_pipeline/openai_policy.py`.

## 2. The constitution — read these before touching anything

1. **`CLAUDE.md`** — Rule 1: *no deterministic judgment about content
   meaning, ever*. No regexes/thresholds/volume-derived counts deciding
   what the book means. Model verdicts + independent advisory critics
   (dissent flags, never gates — Q10). Detected mid-run defects route to
   **The Fixer** (one recorded, flagged, content-addressed best-judgment
   decision — Q13) and the run completes. **R4: nothing is ever lost
   silently.** Only the pre-spend pauses (source review, source-topic
   recovery, Type granularity) and genuine impossibility may stop a run.
2. **`docs/aegis-restructure.md`** — the design "soul" plus the **owner
   decision register §12 (Q1–Q20)**. Every owner ruling gets a register
   entry. Q20 (newest): pre-learning coverage calibrates to ~5 questions
   per concept under a diagnostic posture.
3. **`docs/residue-ledger.md`** — the chronological round-by-round state
   ledger. Newest rounds near the top. **Append a row for every round of
   work you do.** This is how continuity survives session changes.
4. **`docs/testing-handoff-2026-08-22.md`** — the owner's live test plan
   for the current build (objectives, steps, FAIL-IF criteria).
5. `docs/concept-release-and-type-case-routing-rules.md`, the SOP
   Bulk-Import guide and Open/Specific registry
   (`docs/open-specific-registry-v2.md`) — the output contracts.

## 3. Exact current state

**Merged into `main` (all shipped 2026-08-21/22):**
- #238 Console v2 (per-stage time/token/cost cards, parallel lane rails,
  mobile) + fly.toml `[env]` concurrency sizing.
- #239 Watch live (attach-only journal tailing from any device; never
  restarts a run) + auto-landing on the review page at completion.
- #240 **Master cost round**: Q20 calibration (~5/concept), the
  Example-ownership recorded verdict (see §5), truthful staging summary
  ("Captured N…" replaces the false "Created 0 post-learning concepts"),
  slot-wait quiet grace, + review rounds 1–2.
- #241 review rounds 3–4 (stable judge context from the captured
  checkpoint, free interactive replay via `kernel.peek`, reachable
  inventory fallback, truthful timeout edge).
- #242 review round 5 (R4-safe degradation of every pre-branch step,
  lane-key stripping at the source, frozen-tuple pins).

**NOT yet done (owner-side):** `fly deploy` of this build, and resuming
the **parked run**: chapter "The School Bell Rings Again..." (Grade 6
English poem, MSBSHSE) was killed mid-Masters on 2026-08-21 (~$1.47
spent in the Post Master lane; the Pre lane had just started
materializing 67 candidates). Its 98% checkpoint and every decided unit
are durable in the job's decide-once store — **resume replays paid work
free**. Plan of record: deploy first, then resume (testing-handoff §0,
Option B), then work through tests T1–T8.

**Backend suite: 2,801 passed. Frontend: 87 passed + tsc + build.**

## 4. How the owner works — process rules that are non-negotiable

- **"I only want build, no auto testing or reviewing of codes, for
  every little thing."** Targeted tests while developing; ONE full-suite
  gate before push/merge. CI is workflow_dispatch-only.
- Full backend gate (run from `backend/`):
  `S=$(mktemp -d) && mkdir -p $S/data && AEGIS_DB_URL="sqlite:////$S/x.db" AEGIS_DATA_DIR="$S/data" python -m pytest tests/ -q -p no:cacheprovider`
- Frontend gate (from `frontend/`): `npx tsc --noEmit`,
  `npx vitest run`, `npm run build`.
- PRs are opened as **drafts**; the owner merges by saying "merge it"
  (mark ready first — merging a draft 405s). Work on a dedicated
  `claude/...`- or assistant-named branch, reset onto `origin/main`
  after each merge.
- Owner rulings → register entry (Q-number) + prompt/code change + test
  re-pins + ledger row, in the same PR.
- **Never deploy while a run is in flight** (deploy restarts the
  machine and kills the worker; the run then needs an explicit resume).
- Secrets: never in chat or commits. Fly deploy tokens go into the
  assistant environment's secret env vars if direct fly access is wanted
  (`fly tokens create deploy -a projectaegis`). There is currently NO
  fly access from the assistant environment — the owner runs deploys.

## 5. The architecture in one pass

**Generation flow (release-first):** upload PDF → convert →
`build_concepts.generate_post_learning` (wrapped by
`build_concepts_release_contract` at import time) → Architect
instruction set (+ language topology plan for poem/prose; hashes join
every decision key) → Phase 2 canonical source (GPT PDF reader → ACSD;
Mathpix MMD is legacy) → concept extraction with the **early inventory
track** running in parallel (Track A) → Phase 3 kernel passes: Settle →
Host (+ Q14 one-concept-owns-each-Type consolidation) → Place ∥ Analyse
∥ Polish → Prerequisites → Assemble → Pre-Learning chain (premap →
preanalyse → prequestions; Q20 calibration lives in
`phase3/prequestions.py::_plan_rules` ONLY) → final validation → the
deposit is **intercepted**: rows are captured for a **staged release**
(`stage_release`), nothing enters the DB until the reviewer publishes →
Refiner → the two **assessment Master lanes build concurrently**
(Output 02: dedup Q15 → generated cells → materialize → answer-space →
marking → levels → grouping → refiner; Output 04: freeze source
inventory → pre-learning claim Q18 → cells → materialize → … → routing
→ …). Reviewer downloads/reviews via the release page and the revision
loop; publication to the DB tree is a separate explicit act.

**The kernel** (`phase3/kernel.py`): `decide()` = author (≤3 attempts on
checker defects) + Fixer (≤3) + ONE advisory critic; content-addressed
**decide-once** via `DecisionStore` (directory-backed under the job's
artifact dir → replays free on resume); `decision_key`/`peek` are the
shared identity/probe. **Changing any payload/prompt re-keys decisions**
— deploy such changes between chapters and bump the pass's
policy_version.

**Purposes/efforts** (`aegis_pipeline/openai_policy.py`): every model
call declares a `purpose=` from the 14-value Literal (a repo-wide test
pins that every literal is valid). Master authors run
`concept_mapping` (max effort), critics `concept_validation` (high).
Provider-max completion headroom (128k) is ON by default.

**Concurrency:** global gate `AEGIS_OPENAI_MAX_CONCURRENCY` (fly.toml
sets 48), per-run workers `AEGIS_PHASE3_DECISION_WORKERS` (16),
`AEGIS_SOURCE_CHUNK_WORKERS` (10). Slot waits under
`AEGIS_OPENAI_SLOT_WAIT_QUIET_SECONDS` (5) are silent.

**Newest module — `concept_example_ownership.py`:** at staging,
rendered public Examples whose wording has no exact owner in the source
Question/Task Inventory get ONE chapter-wide recorded verdict
(source_variant naming its qid / parser_fragment / unowned), appended to
the release's `issues` ledger (code `unowned_rendered_examples`,
chapter-level, deliberately NO qid anchor). Hub-kind items are never
candidate owners. Judge unavailable/failing → the finding still records,
unadjudicated. Interactive `force_release` replays a stored verdict for
free and never makes live calls. Five review rounds hardened this seam —
read its docstrings before touching it.

**Console/observability:** `progress.py` (contextvars stage/lane,
NDJSON stream + durable run journal) + `openai_usage.py` (per-model and
per-(stage,lane) usage; **the `stages` table rides ONLY the live
console summary — persisted summaries must stay byte-stable**). Frontend
`RunConsole.tsx` (`run`, `watch`), `RunConsolePanel` stage cards,
`BuildConcepts.tsx` resume/watch/landing.

## 6. Cost & time — the standing diagnosis (measured 2026-08-21)

Killed poem run: ~66 min to the Masters; Post Master lane 5.2M tokens /
$1.47 through routing (~200 calls, ~24–26k tok/call, blended $0.283/M —
input-mass dominated). Full-chapter Master estimate ≈1,100–1,400 calls.
Root causes, in order:
1. ~7 recorded decisions per question × (author max + critic high).
2. **Cache-hostile payload ordering**: materialization puts varying
   `candidate_id` as the 3rd JSON key and ships the FULL released
   hierarchy LAST in every request
   (`assessment_materialization.py::_decision_payload`), defeating
   OpenAI prefix caching ($0.20/M vs $0.02/M). Marking and routing have
   the same defect; answer-restriction ships a 13.5k-token registry per
   call (cache-ordered correctly, still heavy).
3. Pre-question volume (was 69; Q20 should roughly halve it).
4. Serial author→critic inside each decide; stage barriers; the
   grouping/QA/master-refiner tail is sequential per candidate.
Pricing table: `openai_usage.py` (~line 42). Effort map:
`openai_policy.py` (~line 84).

## 7. Roadmap — queued and owner-acknowledged, in order

1. **[owner, now] Deploy + resume the parked run + testing handoff
   T1–T8.** The resulting stage table chooses the next lever.
2. **Lever 2 — payload cache-ordering** (no ruling needed, owner aware):
   reorder Master pass payloads stable-prefix-first / candidate-last
   (materialization, marking, routing), consider `prompt_cache_key`.
   Est. 30–50% off Master input cost. Identity-changing: bump the
   policy_version of each touched pass, deploy between chapters.
3. **Lever 3 — critics off the latency path**: Q10 critics never gate,
   so the critic call need not serialize inside `kernel.decide`; design
   an async attach (record updated when the critic returns).
4. **Lever 4 — parallelize the sequential tails**: cluster/describe/QA
   loops in `assessment_release_run.py` are plain for-loops; the
   assessment master refiner is propose-serial by design — consider
   propose-parallel/apply-serial.
5. **Lever 5 — author effort tiering** (needs an owner ruling): drop
   cells/marking/levels authors from max→high.
6. **Batch API overnight lane** (owner: "later"): 50% price, fits the
   barrier structure.
7. **Q17 stage 2 + Q18 stage 2** (designed, not built): stitched-figure
   stamp + recap verdict at the inventory seam.
8. **PDF-direct promotion** (Q19 recorded direction): retire the
   external Mathpix MMD conversion in favor of the GPT PDF-to-ACSD read.
9. **Architect prose polish** (advisory): the conventions line
   occasionally ships clumsy model prose (critic already flags it).

## 8. Traps a fresh assistant will hit (all learned the hard way)

- `purpose="assessment"` and similar guesses **crash live** — only the
  14 declared purposes exist; a repo-wide pin test enforces it.
- Never put new keys into **persisted** usage summaries or checkpoint
  bundles — strict schemas refuse unknown fields and idempotence tests
  will fail. Live-console-only data rides `console_summary()`.
- The **norm-location tests** pin that Q20's "~5" appears ONLY in
  `_plan_rules` labelled prose (AST sweep rejects numeric literals for
  current AND superseded norms).
- **Frozen serializations never change casually**: checkpoint
  fingerprints, `checkpoints._TARGET_FIELDS` (pinned equal to
  `models.CHECKPOINT_TARGET_IDENTITY_FIELDS`), the sealed envelope, gold
  reference workbooks. Changing them breaks resume/acceptance.
- KaTeX: raw answers ONLY in `sqN_keyword` cells; `answer_content`
  stays wrapped (gold workbooks are the schema of record — Q5).
- Recorder helpers (`record_assessment_lane_unavailable`,
  `adjudication_issue`) must NEVER raise, and every internal step must
  degrade toward "record the finding" — R4. Round 5 exists because a
  restructure broke this once.
- Background shell commands reset cwd to the repo root — `cd` into
  `backend/` explicitly every time.
- The release payload's `issues` list is seal-safe (not hashed);
  almost everything else in the payload is sealed — check
  `assessment_release_snapshot` before adding payload keys.
- Two scanners exist for unowned Examples: `generation.py`'s original
  is REPLACED at import by `closed_inventory_contract.install()` — keep
  both in sync (documented duplication).
- Lane release slots (`RELEASE_KEY`/`PRE_RELEASE_KEY`) live inside
  `job.question_inventory` — never let them nest into a new payload's
  inventory (stripped in `stage_release`; keep it that way).
- The five review rounds are logged in `docs/residue-ledger.md` with
  every finding and its resolution — read them before "improving" the
  ownership seam; several obvious-looking simplifications were tried
  and rejected there with reasons.

## 9. Where to answer "how do I…"

- Run the app’s tests → §4. Deploy → §4 (owner does it).
- Understand a past decision → register §12, then the ledger row of its
  round, then the module docstring (they cross-reference each other).
- Add an owner ruling → new Q-entry in §12 + prompt/code + pins +
  ledger row, one PR.
- See what the owner saw → the run console (Stages view) and the
  release page; the run journal and `release.json`/diagnostics zip are
  the durable records.
