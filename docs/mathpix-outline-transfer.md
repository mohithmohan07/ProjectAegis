# Applying the chapter outline to a Mathpix-converted source

> **Historical (superseded by PR #213, "Scrap Mathpix: one converter, no
> switch, no gate").** The two-converter architecture this document
> describes no longer exists: the Mathpix converter, its credentials, and
> the source-quality gate were removed, and the **GPT PDF-to-ACSD reader is
> the only conversion path**, so every conversion now receives the outline
> restructure via `render_page_acsd_to_mmd`. The measurements below are why
> the Mathpix path lost. Kept for the record; nothing here describes
> current behaviour, except that MMD from old Mathpix conversions remains
> readable (legacy-format cleanup in `generation.py`).

## The problem

`job.mmd_text` is written once by conversion and every later phase reads it.
Two converters can produce it:

* **Mathpix** — used whenever its output passes the objective source-quality
  gate (`canonical_source_phase221_contract.py`, `use_fallback` false).
* **GPT PDF-to-ACSD** — used when Mathpix fails, is unusable, or is forced.

The chapter-outline pass decides a chapter's topics and its question
boundaries, but it shapes the MMD only inside `render_page_acsd_to_mmd`,
which belongs to the GPT path. When Mathpix wins, the outline is still
derived — Phase 3 runs the page ACSD as an evidence channel — and then
discarded.

Measured on Balbharati Std 6 "Three Dimensional Shapes", same PDF:

| | Mathpix path | GPT path |
|---|---|---|
| outline topics applied | 0 | 8 content + 2 assessment |
| questions extracted | 4 | 31 |
| image URLs | 75 `cdn.mathpix.com/cropped/…` | 56 `/source-assets/…` |

A deployment with Mathpix credentials therefore never receives the
restructure, which is invisible from the generation log because the outline
is logged as derived.

## Why figures must not be merged

The obvious fix — keep the GPT-rendered MMD and overlay Mathpix's cropped
image URLs onto it — was measured and rejected.

Mathpix page-1 image is 2067x2756 px against a 595x794 pt page, a consistent
3.472 px/pt on both axes, so the two coordinate spaces can be compared
directly. Counting an ACSD figure as covered when a Mathpix crop overlaps at
least half its area:

```
page 1: ACSD figs=10 crops=16 covered= 4      page 5: ACSD figs= 4 crops=15 covered= 0
page 2: ACSD figs= 9 crops= 3 covered= 4      page 6: ACSD figs= 9 crops=10 covered= 0
page 3: ACSD figs=13 crops=12 covered= 1      page 7: ACSD figs= 7 crops= 2 covered= 4
page 4: ACSD figs= 3 crops=14 covered= 0      page 8: ACSD figs= 1 crops= 3 covered= 0

covered: 13/56 (23%)
```

The two readers disagree about what a figure is. 48 of Mathpix's 75 crops
are under 40,000 px — inline glyphs and symbols it could not OCR — while the
ACSD identifies diagrams Mathpix never crops. A geometric merge would give
Mathpix images for roughly one figure in four and Aegis crops for the rest,
which is worse than either source alone and unstable between chapters.

## The design

Leave the Mathpix MMD's content and images untouched. Transfer only the two
structural decisions, both anchored on text rather than geometry:

1. **Topic boundaries.** Each outline topic names a heading the book prints.
   Locate that wording in the Mathpix MMD and give it level-1 heading weight;
   demote any other heading that would otherwise open a topic. Topics are
   derived from level-1 sections, so this is sufficient and reversible.

2. **Question boundaries.** Each partition's parts are required to be
   verbatim, which is what makes them locatable: find the part text in the
   Mathpix MMD and mark the boundaries the outline decided. Task cues render
   at `_TASK_CUE_HEADING_LEVEL` so the deterministic task parser still finds
   the block without minting a topic — the same contract the GPT path uses.

Anything that cannot be located verbatim is left alone and recorded as an
outline review flag, exactly as an ungrounded partition already is. The
source is never rewritten on a guess.

## What this preserves

* every `cdn.mathpix.com/cropped/…` URL, unmoved
* Mathpix's LaTeX and table transcription
* the outline's topics and question splits
* one structural contract across both converters, so staging and a
  Mathpix-configured deployment agree


## Resolution: Mathpix is archived

The transfer above was designed for a deployment that keeps Mathpix in
front. It is not the route taken. Aegis-hosted image URLs
(`/source-assets/{job}/{sha}.jpg?sig=…`, served from `AEGIS_DATA_DIR`) were
accepted as the deliverable, which removes the only thing the GPT reader
cost, so the reader becomes the conversion path and Mathpix is retired.

`AEGIS_GPT_PDF_ACSD_FALLBACK_FORCE` defaulted to `1`, and the Mathpix call
was skipped rather than made, billed, and discarded. Setting the variable to
`0` put Mathpix back in front, which is how the quality-gate branch stayed
exercised in tests.

## Removal: the switch and the gate are gone

Archiving left the code in place behind a default. It has since been
deleted outright — the vendored client, the credentials, the
`source.mathpix.raw.mmd` artifact, the `AEGIS_GPT_PDF_ACSD_FALLBACK_FORCE`
switch, and the `assess_mathpix_quality` gate that chose between two
converters. There is one converter now, so there is nothing to choose.

Two capabilities went with it, deliberately:

* **Image OCR.** `/v3/text` was the only path from an uploaded image to
  MMD. Image uploads to Build Assessments and Build Concepts now fail with
  an error naming the supported formats instead of being OCR'd.
* **PDF upload to Build Assessments.** That lane is not served by the GPT
  reader, which only intercepts Build Concepts, so its PDFs now fail the
  same way. Build Concepts is the route for a PDF.

Live **Create Workbooks** kept working: its PDF → MMD step became the
vendored PyMuPDF text extractor the dry path already used, so maths-heavy
chapters reach the planner as plainer text than MMD gave it.

`transfer_outline_to_mmd` stays on the branch, unwired and covered by tests,
for the day a converter other than the reader has to produce the source.
Its task-cue half is verified (4 -> 25 tasks with every image URL
preserved); its topic half is not.

Artifact directories written before the removal may still hold
`source.mathpix.raw.mmd`. It is listed in `LEGACY_ARTIFACT_NAMES`, so the
next publish for that upload sweeps it inside the same rollback-safe
transaction rather than serving a file no current run produces.
