# Unattended Release and Case-Granular Type Routing

This document is an authoritative extension to
`docs/concept-placement-rules.md`. It records additional product decisions made
by Mohith Mohan (Founder, UpSchool) for Project Aegis. Where an implementation,
prompt, UI or older operational note disagrees with these rules, the
implementation is defective.

## Rule A - no semantic selection during generation

Build Concepts is unattended from source conversion through released output.
It must not show a user a move, refine, split, host, Type-granularity, source
repair or placement choice at 81%, 89% or any other point in generation.

The autonomous provider/critic and bounded recovery ladder may decide, retry,
split a batch, narrow a question to an individual QID, move, refine or split a
concept, or record the item as unresolved. The user is not part of that ladder.

When autonomous resolution is exhausted, the newest durable output is released
with its unresolved decision, evidence and errors attached. The job never parks
in an `awaiting_decision` state for Build Concepts.

## Rule B - Type identity does not force one topic

A reusable Type is a method taxonomy, not a single-topic container. The same
Type identity may be rendered under more than one concept or topic when its
Cases require different teaching owners.

For example:

```text
Type 01: <reusable method definition>

Topic A / Concept A
  Type 01
    Case 01: <case definition owned by Topic A>
      Example: QINV-0004 ...

Topic B / Concept B
  Type 01
    Case 02: <case definition owned by Topic B>
      Example: QINV-0011 ...
```

`Type 01` remains the same reusable Type. Case 01 and Case 02 have independent
semantic ownership. Each Case belongs to the latest topic whose knowledge,
method or framework is genuinely necessary to attempt its QID, subject to the
retrospective-reference exception in Rule 4 of the main placement rules.

Ownership is therefore certified at Case/QID granularity. A Type must not be
forced wholesale into the topic of its first Case, its most common Case, or the
place where the Type was first mined.

## Rule C - closed QID allocation

Every source QID must have exactly one final Type/Case assignment.

- A QID may not appear in two Cases.
- A QID may not appear under two Types.
- A QID may not be duplicated merely because its Type is rendered under more
  than one concept or topic.
- Every QID in the Question/Task Inventory must be accounted for.
- A rendered QID absent from the source inventory is an error.

The release audit must identify duplicate, missing and unknown QID assignments
without deleting the affected question or preventing output.

## Rule D - required rendering order

The public Type/Case structure is always rendered in this order:

1. Type number and Type title.
2. A proper Type definition describing the reusable method.
3. Case number and a proper Case definition describing the bounded variation.
4. The source-grounded example or examples immediately below that Case.

Examples must not float above their Case, collect in a detached block, or lose
their source QID. A Type or Case without a usable definition is released with a
specific audit error.

## Rule E - output survives every post-map error

Once any durable concept rows exist, Build Concepts must release an output even
when a later semantic, grounding, routing, rendering or publication-preparation
step fails.

- Rows with unresolved issues remain in the output.
- Problem rows are highlighted.
- A separate error/status column names the problem.
- The error must also appear in a release-issues sheet with its phase, unit ID,
  topic, QIDs, BLKs and full details where available.
- If no concept row was materialized, a diagnostic release is still produced
  with the source, logs, checkpoint and failure context.

Releasing an output does not assert that every highlighted row is ready for
publication. It preserves the completed work and puts the defect beside the
context needed to repair it.

## Rule F - complete diagnostic export

Every released Build Concepts job offers one portable diagnostic-context
archive containing, where available:

- the released concept workbook;
- the complete generation log;
- the durable generation checkpoint;
- the original uploaded source;
- converted MMD;
- canonical source artifacts and source-evidence packets;
- the source artifact manifest;
- all discovered BLK records;
- the Question/Task Inventory and QIDs;
- mined Types, Cases, examples and final routing;
- pending autonomous semantic-decision context;
- row-level and release-level issues;
- final grounding and placement audit material.

The purpose of the archive is to let an engineering or content issue travel
with its evidence rather than as a context-free error message.

## Rule G - generation and database publication are separate

Generation never inserts or updates concepts in the database and never writes
the released concepts into the shared Bulk Import workbook automatically.

Generation first stages a released output. The user may download and inspect
that output and its diagnostic archive. Database publication occurs only after
a separate explicit authenticated **Upload released output to database**
action.

That publication action:

- starts no semantic model request;
- does not silently remove highlighted rows;
- is idempotent for an already uploaded release;
- uses the normal database/workbook publication transaction and outbox;
- keeps the release audit after publication.

## Implementation map

| Product rule | Primary implementation |
|---|---|
| No manual semantic pause | `build_concepts_release_contract` and the Build Concepts decision API guard |
| Release newest durable output | `build_concepts_release.stage_release` |
| Case-granular Type routing audit | `build_concepts_release.audit_type_cases` |
| Row highlighting and errors | `build_concepts_release_files.build_release_workbook` |
| Full context export | `build_concepts_release_files.build_diagnostics_zip` |
| Separate explicit publication | `build_concepts_release_publication.upload_release_to_database` |
| Release/download/upload controls | Build Concepts API and `DocumentUpload` |
