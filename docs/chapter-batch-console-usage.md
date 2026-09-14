# Chapter batch console — how to use it

*The team's step-by-step guide to running many chapters through Aegis
unattended. Page: **Chapters** in the navigation (`/chapters`). The frozen
contract is `docs/chapter-batch-console-contract.md`; this document tells a
person what to click, what each state means and what to do when a row stops.
Register entries Q53, Q64, Q65 and Q66 in `docs/aegis-restructure.md` record
why it is built this way.*

The owner calls this the **batch API**: one page that lists every chapter in
the catalogue, takes a source PDF per row, pushes many rows at once, runs them
unattended through Step 01, waits for the reviewed Concept files, builds the
Master files in Step 02, waits for the reviewed Master files, and publishes
them to the database and the shared CMS output workbook. Everything the page
does goes through `POST /chapter-batches/…`, which scripts may call too
(section 8).

**What it is not.** It does not use OpenAI's *Batch API* (the asynchronous
24-hour endpoint). A chapter runs 36 recorded decision kinds in sequence, so a
Batch-API lane would be 70–100 sequential waits against a 24-hour-only
guarantee for no fewer requests; that lane is deliberately held
(`docs/chapter-batch-console-audit-2026-09-13.md`, last section). The console
runs the existing synchronous engine, many chapters at a time, from a durable
queue.

---

## 1. The three steps, in one picture

| step | you do | Aegis does | it ends at |
| --- | --- | --- | --- |
| **Step 01** | stage the source PDF, push *Run Step 01* | reads the whole source, writes the Post Concept file (questions embedded under Types/Cases, with every picture uploaded to this server and linked) and the Pre Concept file (every supported prerequisite the source evidences) | **Concept files ready for review** — this is success, not a stop |
| **Step 02** | download both Concept files, correct them, upload the reviewed files, push *Run Step 02* | extracts concepts and questions **from the reviewed file alone** (no link to Step 01), polishes the reviewed Post questions once, generates the Pre questions from the accepted Pre concepts, builds both Master files | **Master files ready for review** |
| **Step 03** | download the Master files, correct them, upload the reviewed Masters, push *Publish* | applies your edits verbatim into a new immutable release version, writes the database, appends the CMS output workbook (Concept file first, then Master) | **Published** |

Nothing in Step 02 reads Step 01's inventories, routes or cached decisions
(Q49/Q51). The reviewed file you upload is the only evidence.

## 2. Before the first push — is the deployment able to run the queue?

The worker starts with the app and reports itself in the summary card at the
top of the page: **worker alive** (green) or **worker stopped** (red). If it is
red, nothing you push will start; the server log says why.

The queue refuses to start when the provider gate cannot pay for its most
expensive step. Step 02 runs the Post and Pre Master lanes at once, so it
presents **two** fan-outs; a Step 01 presents one. The arithmetic:

    needed = AEGIS_QUEUE_PROVIDER_RESERVE + AEGIS_PHASE3_DECISION_WORKERS × 2
    the queue starts only when AEGIS_OPENAI_MAX_CONCURRENCY >= needed

With the production values (`fly.toml`: gate 48, workers 16, reserve 16) the
queue needs exactly 48, so it starts. With the code defaults (gate 8, workers 6,
reserve 16) it needs 28 and refuses, logging:

> AEGIS_OPENAI_MAX_CONCURRENCY=8 cannot admit a step costing 2 fan-out(s): with
> AEGIS_QUEUE_PROVIDER_RESERVE=16 and 6 decision worker(s) the queue needs a
> gate of at least 28. Raise the gate, lower the reserve, or set
> AEGIS_QUEUE_WORKER=0 to run without the batch console.

The knobs, all environment variables read at start:

| variable | default | what it does |
| --- | --- | --- |
| `AEGIS_QUEUE_WORKER` | `1` | `0`/`off` runs the app without the queue (the page still lists chapters; pushes are refused) |
| `AEGIS_OPENAI_MAX_CONCURRENCY` | `8` (prod `48`) | the provider gate every request in the process shares |
| `AEGIS_PHASE3_DECISION_WORKERS` | `6` (prod `16`) | one step's fan-out of parallel decisions |
| `AEGIS_QUEUE_PROVIDER_RESERVE` | `16` | slots the queue never takes, so a person can still run a job on Build Concepts while the batch runs |
| `AEGIS_QUEUE_MAX_CONCURRENT_RUNS` | `2` | Step 01 + Step 02 tasks running at once |
| `AEGIS_QUEUE_MAX_CONCURRENT_MASTERS` | `1` | Step 02 tasks running at once (each holds two Master lanes and their volume reservation) |
| `AEGIS_QUEUE_POLL_SECONDS` | `5` | how often the worker looks at the queue |
| `AEGIS_QUEUE_COLLISION_BACKOFF_SECONDS` | `30` | how long a task waits after another route held its job's lock |

The honest throughput on the production machine is therefore **two chapters in
flight, one of them a Master build**, with 16 slots held back for the Build
Concepts page. A queue of forty chapters is fine; it is worked two at a time.

The worker runs on **one** machine. Its database lease survives a restart of
that machine; it cannot make two Fly machines coherent (each has its own volume
and SQLite file). Never scale the app to two machines with the queue on.

## 3. Finding the chapters

* **Board / Grade / Subject** cascade from the whole catalogue, so narrowing one
  never empties the next. Social Science is one subject under CBSE and
  Karnataka (the KSTATE workbook), exactly as the directory shows it.
* **Search** matches the chapter title and code. **State** filters by the row
  state (section 5).
* Filters live in the URL, so a link to the page is a shareable worklist.
* Pages hold 25 rows. **A selection survives paging**: tick rows on page 1,
  move to page 2, tick more, and the action bar counts and sends all of them.
  *Clear* forgets every page's selection.

## 4. Running a chapter

### 4.1 Stage the source PDF (one row)

1. In the row, *Upload source PDF* → choose the file.
2. The row asks for **Source (publication)** and **Chapter duration
   (minutes)**, then *Stage source*. The publication becomes the run's
   Concept Source and the extracted Post-Learning Question Source (Q42/Q45);
   generated Pre-Learning questions are always `UpSchool DB`. A blank
   publication blocks the database upload later, so name it now.
3. The row reads **Source staged**. A wrong file can be replaced from the
   drawer (*Choose a replacement file*) until a step actually holds the
   chapter.

### 4.2 Push Step 01 (many rows)

Tick the rows, then *Run Step 01 (n of m)* in the action bar. `n` is how many
of the selected rows may take that step right now; the others are left alone,
never refused as a batch. Each pushed row shows **Step 01 queued**, then **Step
01 running** with a progress bar and a live stage line, and ends at **Concept
files ready for review**.

The queue works rows in the order they were pushed, within the capacity above.
An older Step 02 that cannot be admitted yet holds its cost back from the Step
01s behind it, so a steady supply of cheap steps can never starve it.

### 4.3 Watch a row

Click the row to open its drawer:

* the **log tail** of the running or finished step (the same journal the Build
  Concepts page shows, so nothing is lost on refresh);
* the **charges** so far for this job, in rupees, with the provider split;
* **Download Concept file** / **Download Master file** per lane, once they
  exist;
* the **decision card** when a run has paused on a question only a person can
  answer (section 6), with a link to the Build Concepts page where it is
  answered;
* the reviewed-file uploads per lane (4.4, 4.6) and the source replacement.

### 4.4 Review the Concept files, upload the reviewed files

Download both Concept files from the drawer. Edit them locally under the rules
in `docs/aegis-master-governing-contract-v2.md`: you may add, delete, replace
and reorder concepts and questions; Post questions live under their
Types/Cases and the reviewed set is authoritative — Aegis will neither invent a
Post question nor restore one you removed (Q41). Edited Pre concepts govern the
Pre question bank Step 02 generates. Pictures are referenced by the links Step
01 wrote; keep them where they are.

Upload each reviewed file to its lane:

* a run with **one** available lane (Post only) shows *Upload reviewed Concept
  file* in the row itself;
* a run with **two** lanes opens the drawer, where each lane is named — the
  page never guesses a lane, because recording a correction against the other
  lane would be a silent wrong write.

The row reads **Reviewed — ready for Step 02** once every required lane has a
reviewed file. You may skip the upload for an unchanged lane: pushing Step 02
records the unchanged file as accepted (Q41).

A reviewed Concept file is accepted while the row is at *Concept files ready
for review* or *Reviewed*. Once the Masters exist the route refuses it
("Master authoring has started or completed for this upload; start a new run
to submit different Concept inputs"), and the page no longer offers it there.
Whether a second Concept round after the Masters exist should be allowed is an
engine-policy question recorded for the owner under Q66.

### 4.5 Push Step 02

Tick the rows, *Run Step 02 (n of m)*. Step 02 is admitted only when the gate
has two fan-outs free **and** the volume can hold a Pre+Post Master batch; a
row that cannot be admitted yet stays *Step 02 queued* with the reason in the
server log rather than starting, failing and charging an attempt. It ends at
**Master files ready for review**, or at **Master build failed** naming the
lane and the recorded reason (a refused lane never hides the finished one:
Q13, Q56).

### 4.6 Review the Master files, upload the reviewed Masters

Download each Master file from the drawer, correct it, upload it to its lane
(*Upload reviewed Master file*, one lane in the row, two in the drawer). Edits
are applied verbatim into a new immutable release version (Q51/Q52); a
brand-new row under an existing group is minted a durable label and recorded
as reviewer-authored. A Master upload is accepted at *Master files ready for
review* and again after publication — re-publishing edited content rewrites
that row in the CMS output workbook (Q52).

### 4.7 Publish

Tick the rows, *Publish (n of m)*. The dialog names, per row, exactly the lanes
that will be written — this run's available lanes not yet published, never a
hardcoded pre+post pair — and you confirm. Per lane the Concept release is
published first, then the Master, to the database and the shared CMS output
workbook. The row ends **Published**.

* **Partly published** with *cms_workbook_queued*: the workbook append is
  queued behind another writer. Publish again; it converges.
* **Blocked — publication_order**: the Master was pushed before its Concept
  release. Publish the Concept lane first.

## 5. Every state, and what it means

| state | label on the page | meaning |
| --- | --- | --- |
| `no_source` | No source | nothing staged yet |
| `source_staged` | Source staged | PDF staged, Step 01 not pushed |
| `step01_queued` / `step01_running` | Step 01 queued / running | in the queue / on the worker |
| `recovering` | Interrupted — recovering | the worker stopped mid-step; the lease is being reclaimed — **never** rendered as running |
| `concept_review` | Concept files ready for review | Step 01 succeeded; download and review |
| `reviewed` | Reviewed — ready for Step 02 | every required lane has a reviewed file |
| `step02_queued` / `step02_running` | Step 02 queued / running | |
| `master_failed` | Master build failed | a lane was refused; the reason is on the row; push Step 02 again after fixing the cause |
| `master_review` | Master files ready for review | Step 02 succeeded |
| `publish_queued` / `publish_running` | Publish queued / Publishing | |
| `partly_published` | Partly published | at least one lane is published or queued; never green |
| `published` | Published | every available lane is published |
| `blocked` | Blocked — needs a person | section 6 |
| `failed` | Failed | the step ended with an error; *Retry* is offered |
| `dead` | Dead — attempts exhausted | a do-not-resume verdict; only a new source upload recovers it |
| `cancelled` | Cancelled | a person cancelled the queued task |
| `legacy` | Historical run | a run made before the console; read-only |

Progress: **0–70 %** is Step 01, **70–98 %** is the Master build. **99 %**
means the run *finished* with fewer than four outputs ready (the reason is on
the row); **100 %** means all four outputs exist. A bar that reads 99 % is
therefore never "still building" (Q65).

## 6. When a row stops — what the queue did, and what you do

The queue never answers a pause, never skips one, never clears one by itself,
and never retries a do-not-resume verdict. A blocked row burns no further
attempts and returns to the queue only when a person presses *Retry*.

| the row says | what happened | what you do |
| --- | --- | --- |
| **Blocked · source_review** | the source, as converted, needs a person to pick the verified evidence block or replace the file | open the drawer's decision card, answer it on Build Concepts, then *Retry* |
| **Blocked · source_topic_recovery** | the concept topology dropped a numbered source topic; Aegis will not silently absorb it | same |
| **Blocked · type_granularity** | the mined Type taxonomy may be too fragmented to reuse | same |
| **Blocked · human_decision** | a mid-run semantic decision (a critic conflict, for one) awaits a person | same |
| **Blocked · storage_capacity** | the volume cannot hold the Master batch | free space on the volume, then *Retry* |
| **Blocked · publication_order** | the Master was published before its Concept | publish the Concept lane, then publish again |
| **Blocked · cms_workbook_queued** | the CMS append is queued behind another writer | publish again |
| **Blocked · interrupted_master** | a Step 02 started from another surface and did not finish | push Step 02 again |
| **Blocked · no_review_marker** | Step 01 finished without recording its review marker (runs made before Q64; the startup sweep now recovers them) | restart the app once; if the row is still stuck, upload the source again |
| **Failed · attempts_exhausted** | the step raised on every attempt (`max_attempts`, default 2) | read the error in the drawer, fix the cause, *Retry* |
| **Failed · run_incomplete** | Step 01 stopped mid-way on every attempt (provider outage, for one); the checkpoint is saved | *Retry* resumes from the saved checkpoint, replaying finished work from the decision store — nothing paid for is spent again |
| **Failed · master_lane_unavailable** | a Master lane was refused; the lane and reason are on the row | fix the named cause, push Step 02 again |
| **Failed · non_resumable** / **Dead** | the run recorded a do-not-resume verdict (the source as converted is unusable — Q24) | upload the source again (a new conversion) |
| **Failed · job_missing** | the staged upload is gone | upload the source again |

A lock collision (another route held this job while the queue claimed it) is
not an attempt: the attempt is refunded, the task waits one backoff interval
and goes back in line by itself.

**Retry** works on any blocked or failed row except a dead one, in bulk from
the action bar or per row. **Cancel** works on a queued task only; a running
step cannot be interrupted (stopping a run mid-flight would strand paid work).

## 7. What a push answers

`push`, `cancel` and `retry` always answer **200** with a verdict per row; one
ineligible row never fails the batch:

    verdict      queued | already_queued | already_running | refused
    reason_code  no_source | not_ready | already_live | blocked | dead
                 | wrong_state | no_lanes | unknown_chapter | at_capacity

The receipt under the action bar lists them. A row that reads *refused ·
blocked* is waiting for a person (section 6); *refused · dead* needs a new
source; *refused · not_ready* is a step pushed out of order.

## 8. The HTTP surface, for scripts

Router prefix `/chapter-batches`; the same sign-in the page uses. Every route
the page calls:

    GET  /chapter-batches?board=&grade=&subject=&q=&state=&page=1&page_size=25
    GET  /chapter-batches/{chapter_id}
    GET  /chapter-batches/{chapter_id}/events?after=N
    POST /chapter-batches/{chapter_id}/source            multipart file; query source_book, chapter_duration_minutes
    POST /chapter-batches/push                           {"step": "step01"|"step02"|"publish", "rows": [{"chapter_id": 12, "lanes": ["post","pre"]}]}
    POST /chapter-batches/cancel                         {"chapter_ids": [12, 13]}
    POST /chapter-batches/retry                          {"chapter_ids": [12, 13]}
    POST /chapter-batches/{chapter_id}/concept-review?lane=post   multipart file
    POST /chapter-batches/{chapter_id}/master-review?lane=post    multipart file

Examples (`$AEGIS` is the app's base URL; add the credentials the page sends):

    # every CBSE grade 10 Social Science chapter waiting for Step 02
    curl "$AEGIS/chapter-batches?board=CBSE&grade=10&subject=Social%20Science&state=reviewed"

    # stage a source
    curl -F "file=@ch07.pdf" \
         "$AEGIS/chapter-batches/12/source?source_book=NCERT&chapter_duration_minutes=200"

    # push Step 01 for three chapters
    curl -H "Content-Type: application/json" -d '{"step":"step01","rows":[{"chapter_id":12},{"chapter_id":13},{"chapter_id":14}]}' \
         "$AEGIS/chapter-batches/push"

    # upload the reviewed Post Concept file, then push Step 02
    curl -F "file=@ch07-post-concepts-reviewed.xlsx" "$AEGIS/chapter-batches/12/concept-review?lane=post"
    curl -H "Content-Type: application/json" -d '{"step":"step02","rows":[{"chapter_id":12}]}' "$AEGIS/chapter-batches/push"

    # publish both lanes — a publish MUST name its lanes
    curl -H "Content-Type: application/json" -d '{"step":"publish","rows":[{"chapter_id":12,"lanes":["post","pre"]}]}' \
         "$AEGIS/chapter-batches/push"

    # return blocked/failed rows to the queue after fixing the cause
    curl -H "Content-Type: application/json" -d '{"chapter_ids":[12,13]}' "$AEGIS/chapter-batches/retry"

The list row is deliberately light (no source text, inventories or release
payloads); `GET /chapter-batches/{chapter_id}` carries the full job for the
drawer.

## 9. What the console guarantees, and what it does not

* Every state is derived from the engine's own durable markers; the queue
  stores no workflow state of its own. A crashed process reads *recovering*,
  never *running*; a queued CMS append reads *partly published*, never green.
* An attempt is charged at claim and reconciled against the job before any
  spend: a completed step is never charged twice, and a do-not-resume verdict
  is never retried.
* Any signed-in teammate may act on a row that is on the board; the job's
  owner is never rewritten, and who acted is recorded on the row.
* Nothing here changes what a run produces. Every column, rule and prompt of
  the Concept and Master files is the engine's (Rule 0, Rule 1); the console
  only orders the work.
* It cannot stop a running step, and it does not cap a queued task's wait.
  Both are recorded as open in the audit.

## Running a cohort at the batch price

The console's ordinary push runs chapters one after another at the
synchronous price. A **cohort** runs a group of chapters together so that
every stage's model calls leave in one batch, which the provider bills at
half the synchronous rate (register Q73).

**How to push one.** Tick the chapters, turn on **Run together at the batch
price**, choose a slot (the next half hours are offered; `now` means no
gate), then press the step you want. Every chapter in the group becomes
claimable on the same tick, so their first stage arrives in one wave rather
than trickling in push order.

    POST /chapter-batches/push
    {"step": "step01", "cohort": true, "start_at": "2026-09-14T12:30:00Z",
     "rows": [{"chapter_id": 12}, {"chapter_id": 13}, {"chapter_id": 14}]}

**What happens then.** Each chapter runs the same seventy to a hundred
sequential seams it always ran. At each seam the broker collects every
request the cohort produced, submits them as one batch, and answers each
caller from the result. A wave closes when the cohort has been quiet for
`AEGIS_BATCH_QUIET_SECONDS` (20s), or when it has been open for
`AEGIS_BATCH_MAX_WAIT_SECONDS` (180s), or at `AEGIS_BATCH_MAX_LINES` (400).

**What protects the money.**

| Risk | What the lane does |
| --- | --- |
| The process dies mid-wave | The wave record is written before the request leaves and the batch carries its wave id; the next boot re-attaches, harvests the answers and stores them. Nothing already bought is bought again. |
| The provider is slow | Every waiter gives up after `AEGIS_BATCH_DEADLINE_SECONDS` (90 min) and makes the ordinary synchronous call. The batch is cancelled; anything it had already produced is still banked. |
| The same request twice | Responses are stored content-addressed by the sha256 of the request body, so an identical ask is answered free, across runs and across processes. |

**Capacity.** A cohort is bounded by `AEGIS_QUEUE_COHORT_CONCURRENCY`
(default 6) rather than by the synchronous fan-out budget, because its
requests queue at the provider rather than on this machine. Narrow the
cohort and the waves narrow with it, which is the whole saving — so raise
this knob only with machine headroom to match. Publish is never a cohort
step: it spends nothing and serializes on one workbook.

**Cost.** A batched receipt is priced at half the synchronous rate and a
synchronous fallback inside the same run is priced at the full rate, so the
drawer's *Cumulative model usage* is what the run actually cost.

**Not yet proven live.** Whether a batched request is eligible for the
prompt-cache discount, whether `prompt_cache_options` and `service_tier` are
accepted inside a batch body, and the real wave latency all need one real
cohort to settle. The fallback absorbs each of those without stranding a run.
