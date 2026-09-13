# Structured Concept Details in Excel — measured options for the owner

**Status: proposal. Nothing here is implemented.** Two of these three changes
alter stored text, the reviewer console and a release gate, so they are the
owner's call under the standing rule in CLAUDE.md.

The owner: *"Also I want structured outputs in the excel, like when I open the
sheets, the detailing should be structured."*

## Where the wall actually is

Measured on `bulk_import_job_139_fixed_v2.xlsx` (37 Aegis-written concept rows)
against `Corrected_Triangles_Concept_File.xlsx`, the file the reviewer
restructured **by hand** — which is the target shape:

| | breaks per row | longest single line |
| --- | --- | --- |
| Aegis output today (job 139) | 2.4 | **4,729** |
| …if `" // "` alone became a break | 3.4 | 4,134 |
| Reviewer's hand-restructured file | **19.3** | **808** |

So the section separator is roughly **an eighth of the gap**. Character totals
and the longest line, by section, over all 37 rows:

| section | total chars | longest single line |
| --- | --- | --- |
| Description | 52,973 | 1,968 |
| Types | 15,688 | 812 |
| **Activity/Info Hub** | 10,945 | **4,134** |
| Misconception/ Error Analysis | 2,355 | 341 |

The 4,729-character line is `Achieving Mastery: …` + `" // "` +
`Activity/Info Hub: …`, and that hub section holds **five `Figure —` notes**
with no break between them.

## Three distinct causes, ranked by what they actually cost

### 1. `append_activity_hub` joins hub notes with a SPACE — the real wall

`concept_refiner.append_activity_hub` (concept_refiner.py:1085):

```python
merged = f"{existing} {text}".strip() if existing else text
```

Every additional Activity or Figure note is space-joined onto the previous one.
Five notes become one 4,134-character line. This single join is the dominant
cause, and the Types body proves the fix works: `assemble.render_types_section`
already emits `"\n"` before every Case and Example, which is why Types' longest
line is 812 and not four thousand.

### 2. `" // "` between top-level sections — real, but small, and load-bearing

`concept_refiner._SECTION_SEP` (concept_refiner.py:45) joins every top-level
section on one line. Contract v2.0 §10 gives the canonical serialization with
each section on its own line and says **"Use `<br>` between sections in workbook
cells"** — `" // "` appears nowhere in the contract, so this is conformance that
was never implemented (Rule 0).

The Excel seam is already correct and waiting: `presentation.to_display_rich_text`
maps a stored newline to `<br>` + a native break, is idempotent, preserves KaTeX
and leaves URLs alone (verified), and `presentation.py:136` deliberately leaves
data rows without a fixed height so the viewer auto-fits.

**But the separator is load-bearing in three places outside the writer:**

* `build_concepts_release.py:2642` literally tests `"// Types:" in details`.
  A naive change makes `has_types` False for every record and that release
  accounting gate fails closed.
* `frontend/src/lib/richText.tsx:31` — `SECTION_SEPARATOR = " // "`, and
  `splitSections` renders each section as a labelled block. Change the stored
  text without the frontend and the Excel looks right while the reviewer
  console becomes the wall.
* `concept_refiner.split_sections` splits on that 4-character literal only, and
  does not raise on a newline-separated cell — it returns ONE tuple,
  `('Description', <whole cell>)`. That silent collapse reaches
  `release_refiner._identity_violations` (the Master Refiner question freeze),
  `polish.source_examples`, `generation._restore_source_owned_type_sections`,
  `assemble.audit_case_uniqueness` and `concept_cleanup._clean_details`.

Roughly eleven parsers bypass the `split_sections` chokepoint and would each
need to accept both forms.

### 3. Long Description paragraphs — NOT ours to break

The Description's longest line is 1,968 characters of genuine authored prose.
Breaking it mechanically would be deciding where a paragraph ends, which is
exactly the judgment Rule 1 forbids. If the owner wants shorter Descriptions
that is an authoring instruction to the model, not a rendering change.

## The options

**Option A — the hub join only.** Change one line so hub notes are separated by
a break instead of a space. Biggest single win: it is where the 4,134-character
line comes from. Touches stored text (so new runs only, versioned; recorded runs
replay as stored), no parser, no frontend, no release gate.

**Option B — A plus the section separator.** Adds contract §10 conformance.
Requires: widening `split_sections` and the ~11 bypassing parsers to accept both
forms, widening the `"// Types:"` gate at `build_concepts_release.py:2642`, and
updating `frontend/src/lib/richText.tsx`. Gains ~1 break per row on top of A.

**Not recommended — the seam-only codec.** Rendering `" // "` as a distinct
paired break at the workbook seam only, leaving every stored byte alone. It is
lossless, idempotent, re-keys nothing and needs no parser or frontend change —
proven on all 37 real cells — but measured, it closes about **12%** of the gap
and leaves 23 of 37 rows with a line over 1,000 characters, because the residual
wall is *inside* sections, not between them.

**Not recommended for now — full §10 conformance.** The contract also specifies
`Types:` / `Cases:` / `Examples:` group headers, `Case 01 (Type 01):` and
`Example 01 (Case 01):` back-references, a `Question label:` line, and spells it
`Misconception/Error Analysis` without the space the code emits. Every one of
those changes the text that the reviewed-file extraction quotes against and that
a dozen parsers match on. It should be a separate, deliberate piece of work.

## What must be true whichever option is taken

* Versioned for new runs; recorded runs replay exactly as stored (the
  Q35/Q39/Q51 pattern).
* `concept_details` sits verbatim inside content-addressed decision payloads and
  inside `envelope_sha256` and `source_release_sha256`, so changing stored bytes
  re-keys those decisions. Only new envelopes may carry the new form.
* The Q38 paired form is preserved: one logical break is `<br>` plus the native
  break it renders as, and Q62 is what makes a model's transcription of it
  resolve back to the cell's own bytes.
