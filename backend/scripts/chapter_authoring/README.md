# Chapter authoring and pipeline-harness scripts

Tooling from the CH01 (Balbharati Std 6 Mathematics, Three-Dimensional
Shapes) delivery. Two script pairs with different jobs:

## Hand-authored bulk import (the CH01 deliverable)

The pipeline could not yet deposit CH01 (grounding-certificate drift at the
deposit boundary), so the chapter content was authored by hand against the
source text and formatted by Aegis's own code. Content lives in
`build_bulk_import.py`; every formatting rule is applied by the pipeline's
modules, never transcribed.

```
python3 build_bulk_import.py --out out/CH01_Three_Dimensional_BULK_IMPORT.xlsx
AEGIS_AUTHORING_OUT=out python3 emit_canonical.py
```

* `build_bulk_import.py` — the authored content: 8 topics (the book's printed
  sections), 10 concepts with canonical `concept_details`
  (Description / Achieving Mastery / Activity–Info Hub / Types–Cases–Examples /
  Misconception–Error Analysis), all 31 source questions. Refuses to build if
  a question is placed twice, is attributed to the wrong concept, or reaches
  neither a Case nor the Activity/Info Hub.
* `emit_canonical.py` — imports the authored workbook into a scratch SQLite
  DB, applies the Clarius chapter-duration lookup and the chapter/topic
  summary rules, emits through `bulk_import.writer` (all tags, labels and
  group columns composed by the pipeline), then appends topic/concept names
  to the tag codes so each is individually addressable for tagging.

The emitted file re-imports through `app/bulk_import/reader.py` with zero
issues; that round-trip is the acceptance check.

## Pipeline harness

Drive one chapter PDF through convert → generate → release against the
services directly (no HTTP/auth in the way). Requires `OPENAI_API_KEY` in the
environment; both scripts refuse to start without it.

```
export OPENAI_API_KEY=sk-...
python3 run_chapter.py --pdf chapter.pdf --chapter-id 42 --out release.xlsx
python3 resume_chapter.py --job-id 7 --chapter-id 42 --out release.xlsx
```

Two details that make the harness faithful to production — both were learned
the hard way:

* generation is invoked through `uploads.run_with_openai_usage`, which
  performs `phase2.activate`; calling the service directly skips it and the
  chapter outline silently never reaches chapter reading.
* pipeline env comes from `fly.toml`'s `[env]` block (deploy-only keys
  skipped); the rewritten Phase 3 is the only post-81% path, so no
  routing flag is needed — concurrency sizing and similar knobs still
  apply.

`resume_chapter.py` re-enters `generate_post_learning` from the newest saved
checkpoint, skipping conversion and chapter reading.
