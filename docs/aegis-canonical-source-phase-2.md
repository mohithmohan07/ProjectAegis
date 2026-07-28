# Aegis Canonical Source Document: Phase 2

## Status

Phase 2 is a **partial production cutover** for Build Concepts. It does not move
semantic concept generation away from the immutable raw MMD.

The ACSD is authoritative for the source-critical ledger:

- Question / Task Inventory rows;
- stable source-ordered `QINV-xxxx` identifiers;
- task identity and exact-once coverage keys;
- source order and section ownership;
- Figure relationships, image URLs, and captions;
- canonical teacher-facing task display with `[Katex]` and `[img]` wrappers.

The raw MMD remains authoritative for:

- semantic concept extraction;
- concept canonicalisation and granularity;
- description generation;
- learner analysis;
- Type classification and semantic concept-host reasoning.

Build Assessments continues to use the Phase 1 shadow only.

## Why this slice comes first

Repeated runs over the same chapter previously produced different model-derived
inventory sizes and Type counts before failing late on source presentation. The
Phase 2 ledger removes the model from task discovery and gives every source task
one deterministic identity before Type mining begins.

The compiler does not sort by title, model response order, or task category.
Tasks are ordered by immutable source position and only then receive QIDs.

## Generation path

```text
PDF
  -> Mathpix raw MMD
  -> ACSD compiler and validation
  -> source-critical gate
  -> deterministic Question / Task Inventory
  -> constrained chapter-wide topic placement, when required
  -> Type mining and the existing semantic pipeline
```

The inventory extraction model pass is not called for Build Concepts in Phase 2.
A chapter-wide review task can still require the existing constrained topic
placement call because assigning that task to a concept-map topic is semantic
classification, not source extraction.

## Source-anchor enrichment

Phase 1 already creates tasks from the deterministic production anchor parser.
Phase 2 reuses that parser only to enrich those same source tasks with fields that
were not persisted in the Phase 1 schema, such as shared context, subpart labels,
answer text, and source-owned image captions.

An enrichment row must match by exact or contained task text. A source label may
help only inside the same source section; a repeated generic label cannot move a
task to another section. ACSD task order and identity remain authoritative.

## Fail-closed gate

Build Concepts stops before paid inventory or Type calls when ACSD contains any
source-critical defect, including:

- non-sequential or duplicate QIDs;
- duplicate or missing task identities;
- source-order reversal;
- an empty source task;
- malformed task rich text;
- unresolved or ambiguous explicit Figure references;
- a required visual without a source-owned image;
- Phase 1 source reconstruction or image-preservation errors.

The error remains downloadable in `source.source-report.json`. The immutable raw
MMD is never edited to make the gate pass.

## Checkpoint migration

A Phase 1 or older checkpoint can contain model-derived inventory rows whose QIDs
are incompatible with ACSD. Phase 2 does not delete the deepest checkpoint before
the established recovery selector has inspected it.

- During resume, the active inventory adapter reconstructs source tasks from ACSD.
- The existing terminal validator then checks exact coverage, Figure ownership,
  Type placement, and host certification against that source-critical ledger.
- A valid checkpoint can remain API-free.
- A checkpoint that no longer satisfies the refreshed ledger is rejected by the
  normal terminal gate and falls back to the preceding compatible stage.
- A checkpoint whose inventory declares
  `source_contract.mode = acsd-phase2-source-critical` resumes with its stable
  task identities normally.

An explicit administrative migration helper remains available for cases where a
pre-inventory stage should be selected in advance, but it is not invoked before
every generation request. This preserves semantic work while keeping ACSD source
validation authoritative.

## Artifact metadata

For Build Concepts, the four canonical artifacts now declare:

```json
{
  "phase": "phase-2-source-critical",
  "shadow_mode": false,
  "used_for_generation": true,
  "generation_usage": {
    "mode": "source-critical"
  }
}
```

This does **not** mean the Aegis MMD replaces the raw source for all generation.
The artifact explicitly lists which components use ACSD and which continue to
use raw MMD.

## Phase 2 acceptance gate

Before the next cutover slice:

1. The same raw MMD must produce byte-identical ACSD tasks and inventory.
2. RNE must consistently produce the same 26 source-ordered QIDs.
3. The RNE source-critical report must contain no blocking Phase 2 issue.
4. Image URLs and Figure captions must remain source-exact.
5. Mathematical task display must remain valid Aegis rich text.
6. Legacy checkpoints must retain the established API-free validation and
   fallback behavior after their inventory is refreshed from ACSD.
7. Build Assessments must remain unchanged.
8. The full backend, frontend build, and frontend test suites must pass.

The regression suite enforces the RNE task count, QID order, byte-identical
compilation, and `phase2_inventory_ready` status before this slice is mergeable.
Representative AP and Electricity sources also enforce their existing deterministic
65-task and 60-task ledgers.

The first deployed Phase 2 chapters must retain their validation reports so the
stable ACSD inventory can be compared with the final deposited Type examples.

The next slice may move topic-scoped semantic concept extraction to ACSD blocks,
but only after this source-critical ledger has been exercised in production and
its deterministic inventory has been compared with the deposited Types.
