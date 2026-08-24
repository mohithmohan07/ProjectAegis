# Aegis — assistant handoff, 2026-08-22

Written for the next AI assistant (or human) continuing this project with
no prior context. Everything here was true at `main` commit `5abb703`
("Merge PR #242"). The owner is Mohith (mohithmohan07 on GitHub); he
often works from a phone, rules by short messages ("merge it", "b) it
is"), and expects you to bring him decisions, not options-essays.

**Continuation update, later 2026-08-22.** The working branch
`assistant/e2e-owner-feedback` now integrates four post-snapshot rounds:
Master prompt-cache ordering/telemetry, Q21 workbook formatting, durable
Pre-Learning release recovery, and the Concept Type/Case visible-route gate.
It is an offline release candidate only: it has not been pushed, merged,
deployed, or live-tested. This update supersedes the stale cost/roadmap and
resume claims below where explicitly noted; `docs/residue-ledger.md` carries
the detailed evidence and `docs/testing-handoff-2026-08-22.md` carries the
current live acceptance addendum.

**Continuation update, 2026-08-23.** The owner ruled that all Luna work stays
at `xhigh`. Branch `assistant/all-xhigh` records this as Q22 and changes the
central transport policy so all 14 registered purposes request `xhigh`.
Purpose labels remain mandatory; provider-capability and structured-output
recovery may lower a retry. This transport-policy change does not re-key
durable decisions. Cost and semantic effect remain live-unproven until the
same-PDF acceptance in the testing handoff.

**Continuation update, 2026-08-24.** Storage recovery is live and `/health`
reports both one-lane retry and two-lane batch capacity ready. The first
explicit Pre Master rebuild then exposed a separate `assessment.marking`
protocol failure: policy v6 required Luna and the generic Fixer to repeat the
entire immutable question/answer/rubric tree byte-for-byte while adding marks.
Branch `assistant/marking-response-projection` changes only that transport.
Policy v7 asks Luna for ordered weights, duration, keyboard mode and rationale;
the server mechanically binds those decisions onto the untouched staged
candidate. Exact valid v6 decisions are reconstructed by their original key,
revalidated, and replayed for free, so only true misses use v7. Concepts and
their staged content seal are not regenerated or modified. Live acceptance
still requires deploying the branch and clicking the affected Master rebuild
once; do not repeatedly retry on the v6 deployment because failed responses
are not stored and therefore spend again.

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
   decision register §12 (Q1–Q22)**. Every owner ruling gets a register
   entry. Q20 calibrates pre-learning coverage to ~5 questions per concept;
   Q21 defines raw Equation cells, plain Phrases cells, lowercase options,
   multi-block four-mark rubrics, and the table fallback contract; Q22 makes
   `xhigh` the preferred Luna effort for all 14 registered purposes while
   preserving bounded recovery fallbacks.
3. **`docs/residue-ledger.md`** — the chronological round-by-round state
   ledger. Its real convention: each round of work is a
   `## <round name> (<date>: …)` SECTION holding an
   `| Item | Disposition |` table, inserted near the TOP (after the
   leading residue table and the E2E-audit section) so the file reads
   newest-first; follow-up rounds within the same effort are added as
   ROWS inside that effort's existing section (the "Master cost round"
   section holds five review rounds). **Record every round of work you
   do this way** — it is how continuity survives session changes.
4. **`docs/testing-handoff-2026-08-22.md`** — the owner's live test plan
   for the current build (objectives, steps, FAIL-IF criteria).
5. `docs/concept-release-and-type-case-routing-rules.md`, the SOP
   Bulk-Import guide (`docs/SOP_Bulk_Import_Fill_Guide.docx` — a binary
   .docx; the machine-readable column orders live in
   `backend/app/bulk_import/__init__.py`) and the Open/Specific registry
   (`docs/open-specific-registry-v2.md`) — the output contracts. The
   gold reference workbooks (frozen acceptance fixtures) are under
   `backend/data/Testing/reference_bulk_import/`.

**Supersession:** this document supersedes
`docs/handoff-2026-08-21.md` wherever they disagree — above all its
"push directly to main, no pull requests" directive, which the owner
replaced with the draft-PR + "merge it" flow described in §4.
`docs/restructure-handoff.md` is historical design context, not process.

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

**Integrated locally, not yet merged:** stable-prefix explicit caching for
all eight high-volume Master author/critic paths; cache read/write tokens in
the real Master stage and lane cards; Q21 generation/read-back/import guards;
Pre sibling recovery from durable authority (including historical Post-only
jobs, without interactive model spend); and a publication gate that refuses a
whole source-question Concept File with no visible Type/Case question route
while preserving Q14's legitimate description-only individual concepts.

**NOT yet done (owner-side):** merge/deploy of the current integration branch,
one same-PDF live acceptance, and any resumption of
the **parked run**: chapter "The School Bell Rings Again..." (Grade 6
English poem, MSBSHSE) was killed mid-Masters on 2026-08-21 (~$1.47
spent in the Post Master lane; the Pre lane had just started
materializing 67 candidates). Its checkpoint and old decisions remain
durable, but the new materialize/restriction/marking/routing policy versions
intentionally re-key those changed passes. Do **not** promise a free resume:
unchanged decisions replay, changed Master decisions may re-author. Follow the
testing handoff's current addendum and never deploy while a worker is active.

The old snapshot gate was **2,801 passed (+7 xfailed)**; it is historical.
Use the newest final-gate row in `docs/residue-ledger.md` for this branch.

## 3b. Running it — local dev, modes, and where state lives

Start from **`README.md`** ("Run locally", "Dry vs live mode",
"Hosted access for UpSchool") and **`.env.example`**; the essentials:

- Backend: `pip install -r requirements.txt` then
  `uvicorn app.main:app --reload --port 8000` from `backend/`.
  Frontend: `npm run dev` from `frontend/` (port 5173; vite proxies API
  calls to 127.0.0.1:8000). `docker compose up --build` is the
  one-command alternative.
- **Dry vs live:** live model calls are ON whenever `OPENAI_API_KEY` is
  set. `AEGIS_USE_LIVE=0` forces dry; dry/stub generation also needs
  `AEGIS_ALLOW_DRY=1` (disabled in production). The test suite sets
  both (`backend/tests/conftest.py`) — **a key in your local env plus a
  careless manual run = real paid calls.** `AEGIS_OPENAI_MODEL` is the
  single source of truth for the model slug.
- **State locations:** local DB defaults to `backend/aegis.db`
  (`AEGIS_DB_URL`); the data dir defaults to `backend/data`
  (`AEGIS_DATA_DIR`; `/data` on Fly). Per-job durable state lives under
  `<data>/uploads/<job_id>/`: the run journal is `run-events.ndjson`,
  and the canonical source + decide-once decision stores live under
  `source-shadow/`. When this document says "the job's artifact
  directory", that is the place.
- **Identity & auth:** jobs are `UploadJob` rows keyed by integer id
  and scoped by `owner_sub` (Google `sub` when hosted; `local:default`
  offline) — ledger references like "job 65" are these ids. The hosted
  app is Google-sign-in locked to @up.school (`AEGIS_AUTH_MODE=google`
  in fly.toml; `local` for dev); an admin password gates prompt editing
  and destructive actions. **Any new API route must scope by
  `owner_sub`** or it is a cross-user data leak.
- `fly.staging.toml` (app `projectaegis-staging`) exists but is stale —
  it still sets a retired flag (`AEGIS_PHASE3_REWRITE`). Ask the owner
  before treating staging as live.

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

**Purposes/efforts** (`backend/aegis_pipeline/openai_policy.py`): every
model call declares a `purpose=` from the 14-value Literal. Enforcement
is a runtime `ValueError` from `reasoning_effort_for` plus module-scoped
static sweeps (`test_openai_policy.py` covers generation/gpt_writer;
`test_concept_example_ownership.py` covers its own module) — there is
NO single repo-wide sweep, so never invent a purpose string. Under Q22 every
registered purpose requests `xhigh`; purpose labels still identify and audit
the work rather than selecting a tier. A provider rejection or structured-
output truncation may lower a recovery request. Provider-max completion
headroom (128k) is ON by default.

**Concurrency:** global gate `AEGIS_OPENAI_MAX_CONCURRENCY` (fly.toml
sets 48), per-lane workers `AEGIS_PHASE3_DECISION_WORKERS` (16),
`AEGIS_SOURCE_CHUNK_WORKERS` (10). Slot waits under
`AEGIS_OPENAI_SLOT_WAIT_QUIET_SECONDS` (5) are silent. The two Master lanes
can expose 32 contenders for one run; additional creators enter bounded
queues behind the 48-slot ceiling and can still hit the 900-second timeout.

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
per-(stage,lane) usage, including cache reads and cache writes; **the `stages`
table rides ONLY `console_summary()` — persisted summaries must stay
byte-stable**). Frontend
`RunConsole.tsx` (`run`, `watch`), `RunConsolePanel` stage cards,
`BuildConcepts.tsx` resume/watch/landing.

## 6. Cost & time — the standing diagnosis (measured 2026-08-21)

Killed poem run: ~66 min to the Masters; Post Master lane 5.2M tokens /
$1.47 through routing (~200 calls, ~24–26k tok/call, blended $0.283/M —
input-mass dominated). Full-chapter Master estimate ≈1,100–1,400 calls.
Original root causes, in order (the effort mix below describes the historical
2026-08-21 measurement; Q22 now requests `xhigh` for both roles):
1. ~7 recorded decisions per question × (author max + critic high), historically.
2. **Cache-hostile payload ordering — fixed offline, live effect unproven.**
   Materialization, answer restriction, marking and routing now put their
   complete stable evidence first, candidate evidence last, and use explicit
   deterministic four-shard cache keys on author and critic calls. The console
   now exposes stage/lane cache reads and writes. Only a same-PDF live run can
   prove savings.
3. Pre-question volume (was 69; Q20 should reduce candidate-driven work, but
   the size of the saving is a live-test hypothesis, not a proved percentage).
4. Serial author→critic inside each decide; stage barriers; the
   grouping/QA/master-refiner tail is sequential per candidate.
Pricing table: `backend/app/services/openai_usage.py` (~line 42).
Effort map: `backend/aegis_pipeline/openai_policy.py` (~line 84) —
note the two files live in DIFFERENT packages.

## 7. Roadmap — queued and owner-acknowledged, in order

1. **[owner, next] Review/merge/deploy the integrated branch between
   chapters, then run the same-PDF acceptance addendum.** Do not resume an
   active/parked job under a replay-free assumption: the four changed Master
   pass identities intentionally re-key. The live run must prove all four
   outputs, exact source-question accounting, and cache-read/write behavior.
2. **Lever 2 — payload cache-ordering: BUILT offline, acceptance pending.**
   Do not claim the estimated 30–50% saving until matched-scope live telemetry
   confirms it. Report the four-output total separately from normalized Post
   Master cost because the $4.9718 screenshot omitted the Pre outputs.
3. **Lever 3 — critics off the latency path**: Q10 critics never gate,
   so the critic call need not serialize inside `kernel.decide`; design
   an async attach (record updated when the critic returns).
4. **Lever 4 — parallelize the sequential tails**: cluster/describe/QA
   loops in `assessment_release_run.py` are plain for-loops; the
   assessment master refiner is propose-serial by design — consider
   propose-parallel/apply-serial.
5. **Lever 5 — author effort tiering: SUPERSEDED by Q22.** The owner selected
   uniform `xhigh`, so there is no stage-specific author/critic effort tiering.
   Measure the resulting same-PDF cost; do not infer a saving from effort alone.
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
  14 declared purposes exist (runtime ValueError; static sweeps are
  module-scoped, not repo-wide, so the compiler will not save you).
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
- Q21 typed cells: `Equation` `answer_content` and `sqN_keyword` values are
  full raw LaTeX with no `[Katex]`; `Phrases` values are wholly plain text.
  Untyped rich fields such as `question`, `display_answer`, and
  `answer_explanation` retain the ordinary `[Katex]...[/Katex]` contract.
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
- Run/serve the app locally, dry vs live, where state lives → §3b and
  `README.md`.
- See what the owner saw → the run console (Stages view) and the
  release page; the durable records are the run journal
  (`<data>/uploads/<job_id>/run-events.ndjson`) and the release
  artifacts served by the API
  (`/build-concepts/uploads/{job_id}/release.json`,
  `…/release.xlsx`, `…/diagnostics.zip` — routes in
  `backend/app/api/build_concepts.py`).
