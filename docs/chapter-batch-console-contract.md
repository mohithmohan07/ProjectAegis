# Chapter Batch Console — frozen contract (Q53)

Owner request (11 September 2026, verbatim):

> "There should be a page with all chapters names (in rows) loaded (with filters
> of board, subject, grade, etc), where i will be able to upload pdfs and then
> push for batch api. Where it will happen by itself, and show when step 01 is
> completed (generating concept files) and then the team picks up later on
> these, and reuploads the reviewed ones, (in the same row there should be an
> upload option) and then at last, the master files are generated and finalised
> ones downloaded and reviewed. Then upload back to Data base."

Scale: "Tens at a time, overnight is fine." Push model: "select rows and push
them together."

**Scope of this document.** The owner decided the build order: the console and
a durable queue on the existing synchronous engine first; an OpenAI Batch-API
lane is a separate, later piece. Nothing here submits a batch, writes JSONL,
calls `client.batches` or gives `phase3.kernel.decide` a pending state. The
page must not change when the engine later does.

This file is frozen before either side is written, because the design round
found the backend and frontend proposals disagreeing on the router prefix, the
push body, the action vocabulary and the row-state names — every console call
would have 404'd.

## 1. What is queued

A queued unit is **one machine step of one chapter's three-step run**, never a
chapter and never a job: the three steps are separated by unbounded human
review waits and carry independent attempt budgets.

    step01   convert the staged PDF if needed, then generate the Concept files
             and pause for review
    step02   build the Master files from the uploaded reviewed Concept files
    publish  per lane: publish the Concept release, then the reviewed Master,
             to the database and the shared CMS output workbook

Conversion is **inside** `step01`, not a fourth kind. Staging a PDF spends
nothing; the push is the only act that spends money.

**At most one live task per chapter**, enforced by a partial UNIQUE index over
`state IN ('queued','leased','blocked')`. A double push cannot produce two runs
that both append the same chapter to the CMS workbook.

## 2. Persistence — two new tables, zero `ALTER TABLE`

`db.init_db()` runs `Base.metadata.create_all` (mints new tables on existing
databases) then `_ensure_columns()` (additive `ADD COLUMN` only). New tables
need no migration entry; the partial index is created in `db.py`'s gated scan
beside `_ensure_question_label_index`.

### `chapter_batch_rows` — the durable chapter↔job link

`models.UploadJob` has no `chapter_id`: `target_chapter_id` is a per-request
field that only becomes durable *after* Step 01, inside the Concept-review
marker, and `deposit_scope_ids` is written only at `status='generated'`. The
worker needs the chapter *before* the first call, so the link lives here.

    id, chapter_id (FK chapters.id, UNIQUE, NOT NULL),
    job_id (FK upload_jobs.id, NULL), previous_job_ids (JSON list),
    source_filename, source_book, chapter_duration_minutes, source_staged_at,
    created_by_sub, created_by_email,
    last_actor_sub, last_actor_email, last_actor_act, last_actor_at,
    created_at, updated_at

No `upload_jobs.chapter_id` column. No back-populating relationship on
`Chapter`: deleting a chapter must never cascade into generation history.

### `chapter_batch_tasks` — the queue

    id, batch_row_id (FK, NOT NULL), kind ('step01'|'step02'|'publish'),
    lanes (JSON list), state, attempt, max_attempts (default 2),
    lease_owner, leased_at, heartbeat_at, lease_expires_at,
    blocked_kind, failure_code, last_error, last_error_at,
    push_group_id, enqueued_by_sub, enqueued_by_email,
    enqueued_at, started_at, finished_at

    states: queued | leased | blocked | done | failed | cancelled

`push_group_id` (one per push request) plus `enqueued_at` replaces a push-group
table: a group has no lifecycle and every aggregate is a `GROUP BY`.

### No projected state columns

Row state is **derived at query time**, never stored, so the queue can never
become a second divergent copy of workflow state. The list query would
otherwise have to load `upload_jobs.question_inventory` — which also holds both
staged release payloads and is megabytes per row — so the three semantic reads
are pulled with SQLite `json_extract`, which returns only the small object:

    json_extract(question_inventory,   '$._aegis_concept_review')
    json_extract(question_inventory,   '$._aegis_generation_recovery')
    json_extract(generation_checkpoint,'$.human_decisions.pending')

A non-SQLite dialect falls back to reading the column in Python. Nothing about
this reads meaning: it selects a recorded marker by key.

## 3. The lease — what replaces the process-local lock

`uploads.is_job_running` is a module-level dict of `threading.Lock` (and
`UploadJob.generation_running` proxies it), so after a restart it reads False
for a row that was mid-run. **The console never uses `generation_running`.**

    lease_owner = f"{FLY_MACHINE_ID or hostname}:{pid}:{uuid4-boot-nonce}"

minted once at module import. The nonce is what makes a restarted process a
different owner even when machine and pid repeat.

    a LIVE run    == state='leased' AND the lease has not expired
    a DEAD run    == state='leased' AND the lease HAS expired

``lease_owner`` says whose lease it is; expiry says the holder stopped renewing
it. **Both** are required to reclaim. A foreign token alone is not enough: two
processes can share one volume and therefore one queue, and a sweep that
reclaimed foreign leases on sight would hand a colleague's live two-hour run to
a second thread and charge it twice. Requiring expiry costs at most one TTL of
latency after a restart and removes that entirely.

Claim is one compare-and-swap using the repo's existing idiom
(`update().where(...).values(...).execution_options(synchronize_session=False)`
+ `rowcount != 1`), serialized by SQLite's write lock under WAL +
`busy_timeout=30000`. `attempt` increments **at claim**, so a worker that dies
mid-run still burns an attempt and a crash-loop cannot spin.

A **startup sweep** runs in the lifespan before any worker thread starts and
reclaims foreign-token leases. A **periodic sweep** reclaims expired leases in
a live process but never touches a task id in this process's in-flight
registry — a heartbeat merely late behind 48 provider threads on 2 shared vCPUs
must not cause a live two-hour run to be reclaimed and re-spent.

`LEASE_TTL = 300s`, `HEARTBEAT = 60s`, emitted by a timer thread independent of
whether the generation thread is blocked in a 600s provider call. The TTL
bounds the gap between heartbeats, never the length of a run.

`_now()` returns naive `datetime.utcnow()`, matching every existing `DateTime`
column. An aware value compared against a naive stored column raises in Python
and compares as text in SQLite.

## 4. Reconcile before spend — on every claim

Before any provider call, re-read the job:

* `generation_recovery.blocked_recovery(job)` set → settle `failed`,
  `failure_code='non_resumable'`, **never retried** (it is terminal for the job)
* the step's output already exists per `concept_review_state(job)` → settle
  `done` **without calling the engine**

This is "never record false success" run in both directions: never a false
failure, and never a second charge for work that completed just before a crash.

Enqueue is additionally gated on the marker: `step01` only when the marker is
empty; `step02` only at `pending_review`/`reviewed`; `publish` only at
`master_ready`.

## 5. The worker — exactly four service calls, all three-step

    step01   uploads.convert_job(...) when job.status == 'uploaded', then
             release_contract.generate_post_learning(
                 db, job_id, row.chapter_id,
                 owner_sub=job.owner_sub, pause_for_concept_review=True)
    step02   release_contract.build_review_masters(db, job_id,
                 owner_sub=job.owner_sub)
    publish  per lane, Concept first: release_publication.upload_release_to_database(...)
             then master_review.publish_reviewed_master(db, job, lane=,
                 owner_sub=job.owner_sub)

**`owner_sub` is always `job.owner_sub`, never the acting principal's sub.**
Every one of these resolves the job through `uploads.get_job`, which filters on
`owner_sub`; passing a teammate's sub raises `UploadJobNotFound` before any
spend. Who acted is recorded on the batch row and the task
(`enqueued_by_sub`, `last_actor_*`), never by rewriting `UploadJob.owner_sub`.

The worker never calls force-release, revisions or release-review — Q52 closed
those for three-step jobs with HTTP 409. A test greps for those names.

### Journal

`progress.capture_history()` installs only the history contextvar; the durable
NDJSON journal is installed inside `progress.stream()`, which a queue run never
calls. So a new `progress.capture_to_journal(job_id, *, continue_existing=True)`
is **mandatory** for every queue-executed step, installing the sink/track/floor
exactly as `stream()` does and emitting the same terminal `result`/`error`
events. `continue_existing=True` is load-bearing: `RunJournal` opens `mode='w'`
otherwise, and Step 02 would truncate Step 01's saved log.

The console reads the existing unchanged
`GET /build-concepts/uploads/{job_id}/run-events?after=N`. No progress, stage
or log field is ever persisted on a task row.

## 6. Outcomes — every way a step can end

| Outcome | Task state | Retryable | What the operator does |
|---|---|---|---|
| clean return | `done` | — | move to the next step |
| Step 01 review pause | `done` | — | **this is success**; download and edit the Concept files |
| `job.awaiting_decision` | `blocked/human_decision` | after a person answers | answer the decision in the drawer |
| source review, source-topic recovery, Type granularity | `blocked/<pause>` | after a person resolves it | resolve the named pause, then return the row to the queue |
| `StorageCapacityError` | `blocked/storage_capacity` | after space is freed | free volume space |
| publish receipt not `published` | `blocked/cms_workbook_queued` | yes, converges | publish again |
| Concept not published for the lane | `blocked/publication_order` | yes | publish the Concept file first |
| `JobAlreadyRunningError` | back to `queued`, attempt **refunded** | yes | nothing; another route holds the lock |
| non-resumable recovery verdict | `failed/non_resumable` | **never** | follow the recorded recovery action |
| any other exception | `queued` while attempts remain, else `failed` | bounded | read the error, fix the cause, re-push |
| person cancelled | `cancelled` | — | nothing |

A blocked row burns no further attempts, is refused by every push, and returns
to the queue only by an explicit human act. The queue never answers a pause,
never skips one, and never auto-clears one.

`max_attempts` defaults to **2** (initial plus one) and is a per-row column, so
an operator can grant one more without a deploy. At ~2 hours and real provider
spend per attempt, three unattended attempts on a structurally broken chapter
is not a defensible default.

## 7. Admission control and the honest throughput

`backend/app/config.py` and `fly.toml` state the sizing inequality directly:
`runs x overlapping lanes x workers <= AEGIS_OPENAI_MAX_CONCURRENCY`, with
`AEGIS_OPENAI_MAX_CONCURRENCY=48` and `AEGIS_PHASE3_DECISION_WORKERS=16`.
Sustained queueing past `AEGIS_OPENAI_SLOT_WAIT_TIMEOUT_SECONDS=900` **fails a
run after real spend** — over-subscription does not merely slow things down.

    cost(step01)  = phase3_decision_workers()          (16)
    cost(step02)  = 2 x phase3_decision_workers()      (32, two Master lanes)
    publish pool  = 1 (one process-wide output-workbook lock)

    AEGIS_QUEUE_MAX_CONCURRENT_RUNS     default 2
    AEGIS_QUEUE_MAX_CONCURRENT_MASTERS  default 1
    AEGIS_QUEUE_PROVIDER_RESERVE        default 16   (held back for interactive use)

A `step02` also pre-checks the storage reservation (~528 MiB per concurrent
Master build on a 2 GB volume) **before** claiming, so a full volume refuses
admission instead of burning an attempt.

**Stated plainly, because the owner asked for tens at a time:** at ~2 hours per
chapter, 2 concurrent Step 01s or 1 concurrent Step 02, a 12-hour night clears
roughly a dozen Step 01s or about six Step 02s. Thirty chapters through all
three steps is a multi-night cycle on the current machine, not one night. The
levers are a bigger machine or the Batch-API lane; raising the concurrency
knobs is not a lever, it is the slot-wait failure.

## 8. Team access — the narrowest change

Sign-in is already restricted to one Google domain, so "authenticated" already
means "on the team". One new helper:

    uploads.get_shared_job(db, job_id, *, module="", learning_kind="")

It resolves a job **only when that job is referenced by a `chapter_batch_rows`
row**, and raises the byte-identical `UploadJobNotFound("upload job not found")`
otherwise, so membership is never leaked. `uploads.get_job` keeps its exact
signature, filter and message; no existing caller widens.

Widened: every `/chapter-batches/*` route, `GET /uploads/{id}`,
`GET /uploads/{id}/run-events`, and the artifact/Concept/Master download
routes. **Not** widened: `PUT /uploads/{id}/file`, checkpoint delete/import,
`/checkpoints/resumable`, the model-provider routes, every `/build-assessments`
route, `/admin/*`, `/data/reset`.

`UploadJob.owner_sub` is never rewritten; it stays the creator of record and is
displayed as "staged by".

## 9. HTTP surface

Router prefix `/chapter-batches`. Page path `/chapters`. `vite.config.ts` gets
`/chapter-batches` in the proxied list and **nothing** in `SPA_PATHS`
(`/chapters` matches no proxied prefix, so a hard load already falls through).

    GET  /chapter-batches?board=&grade=&subject=&q=&state=&page=1&page_size=25
         -> {items, page, page_size, total, total_pages,
             facets:{boards,grades,subjects,triples}, states:[...],
             queue:{running,queued,blocked,capacity,worker_alive},
             server_time}

    GET  /chapter-batches/{chapter_id}         -> row + job detail + lanes + pending_decision
    POST /chapter-batches/{chapter_id}/source  -> row
         multipart body: file
         query:          source_book, chapter_duration_minutes
         (the shape POST /build-concepts/post-learning/uploads already uses:
          the file is the body, the two scalars are query parameters)
    POST /chapter-batches/push   {step, rows:[{chapter_id, lanes?}]}
    POST /chapter-batches/cancel {chapter_ids:[...]}
    POST /chapter-batches/retry  {chapter_ids:[...]}
    POST /chapter-batches/{chapter_id}/concept-review?lane=  (multipart file) -> row
    POST /chapter-batches/{chapter_id}/master-review?lane=   (multipart file) -> row
    GET  /chapter-batches/{chapter_id}/events?after=N        -> run journal tail

`push`, `cancel` and `retry` **always return 200** with a per-row verdict; one
ineligible row never fails the batch.

    verdict      queued | already_queued | already_running | refused
    reason_code  no_source | not_ready | already_live | blocked | dead
                 | wrong_state | no_lanes | unknown_chapter | at_capacity

Facets come from `SELECT board, grade, subject, COUNT(*) FROM chapters GROUP BY
1,2,3`, **not** from `bulk_import.GRADES`, which lists `01,02,03,06,07,08,09,10`
and would hide every grade 04 and 05 chapter.

The list row never carries `source_artifacts`, `mmd_text`, `question_inventory`,
release payloads or `generation_running`.

## 10. Row states — one closed set, server-owned

Exported in the list response as `states` so the client never hardcodes one:

    no_source, source_staged, step01_queued, step01_running, recovering,
    concept_review, reviewed, step02_queued, step02_running, master_failed,
    master_review, publish_queued, publish_running, partly_published,
    published, blocked, failed, dead, cancelled, legacy

`recovering` exists because a crashed run must never render as running: a task
`leased` with `lease_expires_at < now` is "Interrupted — recovering", not
"Running".

`published` is keyed on the run's **available lanes**, never a hardcoded
pre+post pair — a Post-only run must be able to reach done. A queued CMS append
is `partly_published`, never green.

`step01` reporting `done` with no review marker renders "Step 01 finished but
no review marker" — a visible inconsistency, never a silent green.

## 11. Frontend rules

* The page needs **both** a `NAV` entry and a `<Route>` in `App.tsx`.
  `pages/ReleaseReview.tsx` is the in-repo proof that half of that is invisible;
  `App.test.tsx` asserts both halves.
* `DocumentUpload`, `ConceptReviewWorkflow`, `MasterReviewWorkflow` and
  `useRunConsole()` are **never** rendered or called per row: they share one
  `localStorage` key, one global run console and fixed DOM ids, so the last row
  to render captures the console and detaches the others. The page uses thin
  per-row inputs calling the API directly. Every per-row DOM id is scoped by
  chapter id.
* `stateFor` is exported from `WorkflowStepStrip.tsx` and reused verbatim for
  the step pips, so the table and the single-chapter page cannot disagree.
* One list poll for N rows, never N job polls. Every poll honours
  `isNonTransientStatus` and stops permanently on 401/403/404/410.
* `window.confirm` is replaced by one page-level publish dialog naming every
  chapter and the exact number of database writes and CMS appends.
* The server's `can` map is the authority for which action a row offers; the
  client renders it and keeps only "is this row selected" locally.

## 12. Safety items that must ship with it

* **Syllabus prune guard.** `bootstrap_syllabus` → `refresh_syllabus(prune=True)`
  runs on every boot and deletes any chapter the bundled workbooks no longer
  list when `_chapter_has_content(chapter)` is False — and that predicate is
  only `bool(chapter.topics)`. A chapter with a staged PDF and a queued task has
  no topics yet, so a redeploy would delete it mid-run and orphan the row. It is
  extended to `bool(chapter.topics) or _chapter_has_runs(db, chapter.id)`, an
  EXISTS probe over `chapter_batch_rows`. Row existence is mechanics, not
  judgment, and it matches the function's own stated intent that "a chapter
  carrying authored work is never deleted".
* **Deployment constraint, documented not claimed.** The worker runs on one
  machine. The DB lease closes the restart hole and the multi-thread hole within
  one process and volume; it cannot make two Fly machines coherent, because a
  second machine gets its own volume and therefore its own SQLite file, and the
  job locks, the output-workbook lock and the storage reservations are all
  process-local.

## 13. Rules compliance

* **Rule 1.** Admission order, leases, attempt counters and per-row state are
  scheduling mechanics. Nothing here classifies content: no regex, no keyword
  list, no threshold, no volume-derived structure. The only semantic reads are
  two durable markers the engine itself wrote (`concept_review_state`,
  `blocked_recovery`), selected by key.
* **Rule 1, pre-spend pauses.** Source review, source-topic recovery and Type
  granularity each get a visible `blocked` state requiring a person. The queue
  never answers or skips one.
* **Never record false success.** `done` on a task asserts only that one service
  call returned; the chapter's done-ness is read from the marker. A queued CMS
  append is not a publication.
* **Rule 0.** Nothing changes what a run produces — no output column, model
  route, review stage or release gate is touched. The queue decides only *when*
  an existing service call runs.
* **Q52.** The worker drives only the three-step routes; the legacy
  force-release, revisions and release-review acts stay closed.
* **Q49/Q51.** Step 02 still reads the reviewed file independently; the queue
  introduces no Step 01 → Step 02 semantic path.

## Appendix A — the row payload, byte for byte

Both sides code against this. The backend emits exactly these keys; the
frontend declares exactly these types in `frontend/src/types.ts`.

```ts
export type ChapterBatchState =
  | "no_source" | "source_staged" | "step01_queued" | "step01_running"
  | "recovering" | "concept_review" | "reviewed" | "step02_queued"
  | "step02_running" | "master_failed" | "master_review" | "publish_queued"
  | "publish_running" | "partly_published" | "published" | "blocked"
  | "failed" | "dead" | "cancelled" | "legacy";

export type ChapterBatchStep = "step01" | "step02" | "publish";

export interface ChapterBatchLane {
  lane: string;                       // "post" | "pre"
  available: boolean;
  concept_reviewed: boolean;
  concept_reviewed_filename: string;
  concept: "published" | "available" | "unavailable";
  concept_reason: string;
  master: "published" | "queued" | "ready" | "none";
  master_reason: string;
  master_version: number;
}

export interface ChapterBatchQueue {
  task_id: number | null;
  kind: ChapterBatchStep | null;
  state: "queued" | "leased" | "blocked" | "done" | "failed" | "cancelled" | null;
  position: number | null;            // 1-based place in the queue, null unless queued
  attempt: number;
  max_attempts: number;
  blocked_kind: string;
  failure_code: string;
  last_error: string;
  enqueued_by_email: string;
  enqueued_at: string | null;
  started_at: string | null;
  lease_expired: boolean;             // leased but the lease ran out -> "recovering"
}

export interface ChapterBatchPendingDecision {
  decision_id: string;
  kind: string;
  question: string;
  companions: number;
}

export interface ChapterBatchCan {
  step01: boolean; step02: boolean; publish: boolean;
  cancel: boolean; retry: boolean;
  upload_source: boolean; upload_concept: boolean; upload_master: boolean;
}

export interface ChapterBatchRow {
  chapter_id: number;
  chapter_code: string;
  chapter_title: string;
  chapter_display_name: string;
  board: string; grade: string; subject: string; unit: string;
  job_id: number | null;
  source_filename: string;
  source_book: string;
  staged_by_email: string;
  source_staged_at: string | null;
  state: ChapterBatchState;
  state_label: string;                // server-supplied; the client never invents one
  stage: string;                      // live stage name, "" when idle
  progress: number;                   // 0..1
  workflow_status: string;            // the Concept-review marker status, or ""
  lanes: ChapterBatchLane[];
  blocked_kind: string;
  blocked_reason: string;
  error_message: string;
  pending_decision: ChapterBatchPendingDecision | null;
  can: ChapterBatchCan;               // the server is the authority
  queue: ChapterBatchQueue;
  last_actor_email: string;
  last_actor_act: string;
  last_actor_at: string | null;
  updated_at: string | null;
}

export interface ChapterBatchFacets {
  boards: string[]; grades: string[]; subjects: string[];
  triples: Array<{ board: string; grade: string; subject: string }>;
}

export interface ChapterBatchQueueSummary {
  running: number; queued: number; blocked: number;
  capacity: number;                   // max concurrent generation runs
  worker_alive: boolean;
}

export interface ChapterBatchPage {
  items: ChapterBatchRow[];
  page: number; page_size: number; total: number; total_pages: number;
  facets: ChapterBatchFacets;
  states: Array<{ value: ChapterBatchState; label: string; tone: string }>;
  queue: ChapterBatchQueueSummary;
  server_time: string;
}

export interface ChapterBatchPushOutcome {
  chapter_id: number;
  verdict: "queued" | "already_queued" | "already_running" | "refused";
  reason_code: string;                // "" when queued
  reason: string;                     // human sentence, "" when queued
  task_id: number | null;
  position: number | null;
  row: ChapterBatchRow;               // freshly projected, for an optimistic patch
}

export interface ChapterBatchPushResult {
  step: ChapterBatchStep;
  push_group_id: string;
  results: ChapterBatchPushOutcome[];
}

export interface ChapterBatchDetail {
  row: ChapterBatchRow;
  job: UploadJob | null;              // the full job, drawer only
}
```

`tone` is one of `neutral | accent | green | yellow | red`, and the client maps
it to the existing badge classes.
