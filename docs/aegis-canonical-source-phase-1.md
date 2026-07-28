# Aegis Canonical Source Document: Phase 1 Shadow Compiler

## Purpose

Phase 1 adds a deterministic source-compilation layer immediately after document-to-MMD conversion. It is observational only. Current Build Concepts and Build Assessments generation continue to consume the unchanged raw `UploadJob.mmd_text`.

Every successful conversion now creates four private, per-upload artifacts:

| Artifact | Purpose |
| --- | --- |
| `source.raw.mmd` | Exact immutable copy of the converted MMD used by the current pipeline |
| `source.aegis-source.json` | Machine-readable Aegis Canonical Source Document (ACSD) |
| `source.aegis.mmd` | Human-readable rendering derived from the ACSD |
| `source.source-report.json` | Deterministic validation and comparison report |

The artifacts are stored under the upload owner's private job directory and are available through authenticated `/source-artifacts/uploads/{job_id}/...` endpoints.

## Non-cutover guarantee

Phase 1 does not:

- replace or rewrite `UploadJob.mmd_text`;
- alter existing concept-generation prompts;
- alter Type mining, concept extraction, checkpoints, or deposit behavior;
- make a shadow validation warning fatal to conversion;
- use GPT or any other probabilistic step.

The canonical document declares both `shadow_mode: true` and `used_for_generation: false`.

## Ordering contract

Source order is structural data. The compiler records:

- an exact `source_start` and `source_end` for every block;
- monotonically numbered `section_id` and `block_id` values;
- the explicit `section_sequence` array;
- `parent_section_id`, heading level, and depth;
- block order both chapter-wide and within each section.

The concatenation of every block's `raw_text`, in block order, must reproduce the raw MMD byte-for-byte as Unicode text. A mismatch is an ACSD validation error.

Models are not involved, so they cannot alphabetize topics, move the last topic first, or reorder blocks by completion time.

## Source preservation

The ACSD separately records:

- raw block text and its SHA-256 digest;
- canonical display text and the deterministic transformations applied;
- every image URL with its original source span;
- every Figure object, caption, source label, and image relationship;
- every detected mathematical span with raw text, canonical LaTeX, and `[Katex]` display text;
- deterministic source tasks and their visual references when the existing source parser can prove them.

Raw Mathpix figure and table syntax is retained in `source.raw.mmd` and canonical block data. Teacher-facing Aegis MMD renders canonical image tags and KaTeX wrappers. Raw table blocks remain fenced for Phase 1 audit and therefore prevent `ready_for_future_cutover` until a later structured table renderer exists.

## Validation report

The report currently checks:

1. complete and contiguous source-span coverage;
2. exact raw-MMD reconstruction from ordered blocks;
3. unique and monotonic section order;
4. exact image-URL multiset preservation;
5. task-to-Figure referential integrity;
6. unresolved or ambiguous explicit Figure references;
7. canonical rich text in derived public-text blocks;
8. whether Mathpix layout commands leak outside fenced audit blocks.

Statuses are:

- `passed`;
- `passed_with_warnings`;
- `failed`.

A shadow failure does not invalidate the existing MMD conversion. All four audit files are still written, including a failure report, so the compiler itself remains observable.

## Determinism

For the same:

- raw MMD text;
- source filename;
- ACSD schema version;
- compiler version;

Phase 1 must produce byte-identical canonical JSON, derived Aegis MMD, and validation report. The artifacts contain no timestamps or random IDs.

## Storage and regeneration

The artifacts are deterministic derivatives of the raw MMD and do not need a database migration. Replacing an upload deletes the previous shadow directory. Reconversion regenerates the complete artifact set.

Portable generation checkpoints continue to carry the raw MMD. A shadow bundle can therefore be regenerated after restore; it is not yet embedded into checkpoint JSON or used for generation.

## Phase 1 acceptance gate

Phase 1 is complete when:

- conversion remains backward-compatible;
- all four artifacts are generated for both upload modules;
- the raw MMD downloaded from the shadow exactly matches the conversion result;
- topic/section order is deterministic;
- image URLs and KaTeX representations survive compilation;
- artifacts are owner-isolated and privately downloadable;
- complete backend and frontend CI remains green.

A later phase may compare ACSD task, figure, and section ledgers against generation outputs. No production cutover should occur until that corpus-level comparison is accepted.
