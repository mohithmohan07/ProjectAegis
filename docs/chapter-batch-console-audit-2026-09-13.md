# Chapter Batch Console — audit against the frozen contract

The owner: *"let's finish off the remaining work on batch api."*

Every clause of `docs/chapter-batch-console-contract.md` was read against the
code, and every claimed gap was then adversarially verified by a second reader
who had to reproduce it or refute it. **39 findings survived; 1 was refuted.**
Three are blocking. Two of those are fixed here; the third needs the owner.

## Blocking

### 1. A pushed chapter built its Masters inside Step 01 — FIXED

`chapter_queue_worker._run_step01` called `generate_post_learning` without
`pause_for_concept_review=True`. Contract §5 spells that call out *with* the
kwarg, and the interactive route passes it; the queue was the one production
path that did not. The flag defaults to `False`, so the pause branch was
skipped and control fell through to `_build_master_siblings`.

It is worse than "Masters from unreviewed Concepts". Inside each lane,
`reviewed_file_input.prepare` takes its unchanged-file branch whenever the
staged payload records a workflow version — which every new run does — so
step01 **rendered the job's own staged Concept workbook, recorded it as an
accepted reviewed input, and spent the whole of Step 02 on it**:
`reviewed_file.extract`, the Post polish, the Pre regeneration and both Master
runners. Q49/Q51 exist precisely to keep Step 02 reading only a file a person
reviewed.

`initialize_concept_review` — sole caller, that skipped branch — never ran, so
no marker existed and `concept_review_state` returns `{}` with nothing able to
backfill it. Reproduced end to end: the console derives
`state=blocked, blocked_kind=no_review_marker` with
`step01/step02/publish/cancel/retry/upload_concept/upload_master` all false and
only `upload_source` left. **A full paid run, stranded.**

**Owner decision still needed:** any chapter already pushed through the current
code is in that stranded state and will not self-heal. That is a data question,
not a code one.

### 2. The drawer's Concept and Master downloads never rendered — FIXED

`GET /chapter-batches/{chapter_id}` declared no `response_model`, so FastAPI
encoded the ORM object's loaded **columns**. `source_artifacts` is a property
installed at import time, not a column, so it was dropped from every response.
`ChapterRowDrawer` therefore read `undefined` and rendered "Concept file not
available yet" / "Master file not available yet" in every state, forever —
while the identical `files.find(kind)` code worked on Build Concepts, which
declares `UploadJobOut`. Declaring the model also stops the route disclosing
raw columns no console consumer reads.

### 3. Step 02 is inadmissible under every configuration but one — OWNER

`chapter_queue_worker` computes
`usable = OPENAI_MAX_CONCURRENCY - provider_reserve()`, then
`budget = max(1, usable // workers)`. `cost(step02)` is 2. The general condition
for a step02 ever to be admitted is
`AEGIS_OPENAI_MAX_CONCURRENCY >= 2 x phase3_decision_workers() + 16`:

| configuration | gate / workers | needed | result |
| --- | --- | --- | --- |
| code defaults | 8 / 6 | ≥ 28 | **dead** |
| `fly.staging.toml` | 3 / 1 | ≥ 18 | **dead** |
| `fly.toml` | 48 / 16 | ≥ 48 | alive at *exactly* the threshold, zero margin |

Nothing sets `AEGIS_QUEUE_PROVIDER_RESERVE` anywhere, so a production-sized
reserve of 16 is applied to a default gate of 8 — the reserve exceeds the whole
gate, and `max(1, ...)` papers over it for step01 only.

Worse, the dispatcher `break`s on the first inadmissible task rather than
skipping it, and `claimable` orders by `enqueued_at`. Verified by running the
real dispatcher: queue `[step02, step01, step01]` at code defaults starts
**nothing** — both step01s are individually admissible and are never reached.
Even at 48/16 with one step01 in flight, `[step02, step01]` starts nothing. The
machine idles while admissible work is queued, and neither `admits` nor the
`break` logs anything: the console just says "Queued for Step 02" forever.

The scheduling half — skip instead of break, with an anti-starvation rule so a
steady supply of step01s cannot keep a step02 out forever, plus one log line
naming the denied task, its cost and the budget — is pure ordering mechanics
and needs no approval. **Making a step02 actually admissible at 8/6 does need
the owner**, because every option is a policy choice: raise the floor to
`max(_STEP_COST.values())` (at 8/6 that presents 12 concurrent requests against
a gate of 8), lower the reserve, or declare the queue unsupported below a named
gate size and refuse to start with a readable message.

## Important (18 verified)

Most consequential, in order:

* A lock collision (`JobAlreadyRunningError`) on the **last** attempt drops its
  promised refund and records `failed/attempts_exhausted` for a step that never
  ran — the error text says the opposite of what happened. The same collision
  also spins the dispatcher at ~60–100 cycles/s for the whole duration of the
  conflicting run.
* A resumable `run_incomplete` exit is settled as a clean `done` (step01).
* Step 02 has no pre-claim storage check, so a full volume burns an attempt and
  a partial paid run — which is the exact outcome contract §7 was written to
  prevent.
* No wall-clock cap on a queued task and no way to stop a running one.
* Worker step bodies are never executed by any test, and the injected `sleep`
  seam is dead.
* `PrimaryAction`'s can-ordering hides the reviewed-Concept upload at
  `concept_review`; at `master_review` the row's one action is a Concept upload
  the server then refuses with 409.
* A selection spanning pages is counted in the action bar but never sent.

## Minor (16 verified)

Includes: all three pre-spend pauses writing one `blocked_kind`
(`human_decision`) where §6 requires the pause to name itself; no non-SQLite
fallback for the `json_extract` signal reads; the partial UNIQUE index declared
only on the model; `reason_code at_capacity` declared in §9 but never emitted;
`publish` having no already-complete reconcile entry; the per-row Publish
button being dead code (`can.publish` is a strict subset of `can.upload_master`);
and `retryableFor` being written, tested and never imported, so there is no bulk
Retry.

## The deferred OpenAI Batch-API lane

Scoped separately and **not recommended yet**. The console's own blocking
defects mean the synchronous path has not yet run a chapter end to end
unattended; batching the widest fan-out stages on top of a queue that cannot
admit a step02 would measure nothing. Revisit once the three blocking items are
closed and one chapter has completed overnight.

