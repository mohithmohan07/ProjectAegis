# Runtime optimisation and accounting — approved implementation

This slice changes scheduling, paid-author recovery and measurement. It does not
reduce model effort, review coverage or generated-question coverage. Prompt cache
helpers retain their existing signatures and concatenate the complete stable
prefix and variable suffix without clipping evidence.

## Bounded scheduling

`phase3.kernel.parallel_map_in_order` limits submitted-but-unapplied work to its
configured worker count. Completion is observed independently of input order, so
a later failure is noticed while an earlier unit is still running. Application
and checkpoint callbacks remain ordered. Queued work is cancelled; started work
finishes and can save its paid decisions. Nested decision pools execute inline
within their outer decision worker. An explicit orchestration pool does not
consume this decision-nesting budget. Linked cancellation scopes let an outer
failure stop its descendants while a failure caught inside one Master lane does
not cancel its healthy sibling.

Q37 corrects the overly broad Q35 nesting guard: the automatic Pre/Post Master
wrapper uses two orchestration workers, and each can now use its configured
decision pool, commonly 16. Deeper Settle decision pools remain bounded instead
of growing 16 × 16. All calls still share the provider gate, commonly 48.
Independent source hierarchy batches use `AEGIS_SOURCE_CHUNK_WORKERS` within
each pass; the complete author pass precedes criticism. Ordered application and
per-success cache persistence remain intact. Concurrency regressions exercise
the actual automatic Master wrapper and source batch boundaries with mock
transports; they are not live runtime benchmarks.

## Pending author receipts

A mechanically valid author or Fixer response is saved atomically in a separate
`pending_author/` store before the critic starts. Its immutable identity includes
the existing full-payload/source/policy decision key and conservative callback
implementation fingerprints. Effective prompt/schema versions must remain bound
in the caller's payload/policy. The response has a content digest and is checked
again against the active mechanical contract on resume.

An interrupted critic resumes that paid draft. Pending receipts are never
returned by decision `peek`, never presented as reviewed, and never mutate a
completed decision. Critic dissent remains advisory; ordinary critic errors still
complete the decision with an unaudited flag, matching existing behaviour.

## Attempt ledger and cost semantics

All three physical provider-call adapters are instrumented: the shared JSON
adapter, legacy source multimodal adapter and active strict-schema source adapter.
Each scheduled attempt records queue/service/backoff intervals, outcome, request
and response identifiers, purpose/stage/lane, requested model/effort/tier and
provider-reported values. Unreported actual values remain null. A transport start
records an attempted dispatch; it does not prove the provider billed it.

- `request_count` keeps its existing meaning: responses with usable token usage.
- `attempt_count` includes scheduled attempts, including a queue timeout.
- `provider_request_count` counts transport starts, including failures without a response.
- `pending_request_count` counts started calls still awaiting a receipt;
  `unresolved_usage_request_count` counts completed calls whose usage is absent.
  Pending calls do not mark already recorded usage as lost. A persisted pending
  attempt from an interrupted segment becomes unresolved on resume.
- Missing or incomplete completed usage is unknown cost. An unsupported model or service
  tier is not priced at a convenient standard rate. The total estimate is null
  when incomplete; `known_usage_estimated_cost_usd` retains the known subtotal
  globally and per stage. The console labels that subtotal as recorded and
  explains pending requests, missing receipts and unpriced usage separately.
- The stage/lane/model cost matrix preserves historical estimates when segments
  merge. `stage_timings` reports each stage once. Existing lane rows explicitly
  identify their elapsed value as a shared stage window: do not sum those lanes.
  `active_request_seconds` uses the union of overlapping request intervals.
- Replay and mechanical workbook work consume elapsed time even with zero API calls.
- Named mechanical spans measure Master validation, production concept/Master
  projection, workbook serialization and read-back parsing. They record wall
  time and current-thread CPU even on a zero-API replay. Nested same-thread CPU
  is counted once, and mechanical wall time is the union of overlapping spans.
  Thread CPU explicitly excludes child Node/renderer processes; it is not a
  complete process-tree or Fly-machine CPU measurement. Named spans describe
  only the instrumented operations, not every mechanical operation in the run.
- Request starts and missing-receipt/failure events update the console too.
  Lightweight console updates retain the totals without repeatedly streaming the
  growing attempt ledger. Durable and terminal summaries contain the full details.

The Console's Download snapshot action exports its current state, usage and
retained log while generation continues. It is explicitly a console snapshot,
not the complete persisted source/decision archive. The full diagnostic ZIP
retains its job lock; its links show the active-run limitation instead of
offering a predictably rejected request.

Details use the existing job-summary checkpoint and final-save path. Ordinary
runtime failures are saved by that path. There is no separate per-attempt fsync
journal: a hard process kill before the next job save can lose recent usage
accounting even when the paid author receipt already reached disk. This limit is
explicit; an independent journal/reconciliation change remains outside this slice.
These estimates cover provider tokens, not total Fly compute/storage/egress cost.

## Pre-learning evidence and workload

Q37 keeps the existing capture, merge, authority and map stages. New sealed
Phase 3 envelopes use complete source evidence and bound figures. The authority
can split compound captures and recover source-supported omissions while
recording atomic provenance, dispositions and source-demand coverage. Explicit
source relationships remain available across stages and into the teaching map.
Existing sealed runs retain their original policy and decisions.

This improves the information available to the models without reducing review
effort. It is not a promise of lower token cost: complete evidence can add input
tokens, and each additional retained Pre concept still receives its required
five Basic and five Intermediate questions. Scheduling removes avoidable serial
waiting; provider latency and the amount of valid content still determine runtime.

## Verification

Dry tests cover interruption and disk resume without re-authoring, source/prompt/
schema identity changes, nested worker limits, early failure observation, ordered
application, invalid-output billing, source-adapter attempts, missing usage,
unknown service tiers, compact console totals, historical cost preservation,
parallel stage timing and zero-API replay time. All tests use a separate database
and mocked providers. No live provider benchmark or deployment was performed.

Historical Q35 verification: 89 targeted tests passed across runtime accounting, kernel,
parallel infrastructure, console stages, Phase 34 turnover, Phase 34.1 schema
completeness and Phase 35 provider capacity. After the final same-identity receipt
race fix, all 28 kernel/resume tests passed, including the new race regression.

New portable checkpoint telemetry declares `usage_schema_version: 3`, while
version 2 and legacy records remain accepted. The runtime
extension, request/backoff records, mechanical spans, stage extensions and cost
matrix have closed typed schemas with bounded values; unknown keys and malformed
numbers/booleans are rejected. Legacy usage records remain accepted by their
existing schema. Validation does not populate defaults, strip fields, or reprice
historical costs. Round-trip and hostile nested-record tests cover this boundary.
