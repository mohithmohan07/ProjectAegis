# Restructure C — block JSON as the single authority (phased plan)

Status: **C1 shadow mode in progress** (began with PR #273 — the block-first
compiler runs beside the render-then-reparse path and records divergences;
cutover awaits the owner's review of the shadow diff). C2–C4 not started.
Owner directive (2026-08-29): discuss first, then implement phase by phase,
without losing any recorded decision. Restructures A (terminal verdict decided
once at staging, PR #272) and B (one-shot upload panel + full converted-text
viewer, PR #272) shipped first, per the agreed order A → B → C.

This document exists so a fresh session can pick C up with zero context loss.
Read it together with `docs/aegis-restructure.md` (§12 decision register) and
CLAUDE.md Rule 1.

## 1. The problem, measured

The GPT PDF reader (Phase 2.2.1) produces a verified page/block JSON bundle —
`source.gpt-page-acsd.json` — then the pipeline flattens it to MMD and
**re-parses the MMD with regexes** to build the canonical document everything
downstream consumes. The reader's structure is thrown away and partially
reconstructed:

1. **Render-then-reparse.** `reconstruct_pdf_to_acsd`
   (`canonical_source_phase221_fallback.py:3658`) calls
   `render_page_acsd_to_mmd` (`:2591`) and then
   `phase2.compile_phase2_source(mmd_text, …)` (`:3677`), which re-partitions
   the flat text via `canonical_source._raw_blocks` (`canonical_source.py:257`)
   and mints `BLK-xxxxx` ids from the re-parse (`canonical_source.py:320`) —
   not from the reader.
2. **Text-match re-join.** Block/task identity is re-attached by normalized
   string equality/containment (`apply_page_acsd_relationships`,
   `canonical_source_phase221_fallback.py:2863`, matcher at `:2762-2799`);
   a task whose text finds no canonical block raises (`:3045-3055`). This
   failure class exists only because identity was discarded one step earlier.
3. **Pages are lost, then guessed.** The renderer deliberately emits no page
   markers (`:2623-2625`). Downstream, two stages reconstruct pages by
   character-offset ratio: `canonical_source_phase3.py:2339-2349`
   (anomaly packets) and `canonical_source_phase22.py:414-418`. The
   `page_hint` field consumers expect (`assessment_source_inventory.py:290`,
   `semantic_recovery.py:432`, `build_concepts.py:2115` CSV column) is
   permanently empty because nothing ever sets it
   (`canonical_source_phase2.py:893` reads it; the canonical task never
   carries it — only `gpt_pdf_acsd_relationship.page_id`, set at
   `canonical_source_phase221_fallback.py:3337-3344`).
4. **An empty slot already waiting.** The sealed Phase 3 envelope accepts
   `acsd_ledger` (`phase3/envelope.py:72`, stored at `:109`) and its only
   production caller (`concept_topology_contract.py:350-357`) never passes it.
5. **Two lanes, two authorities.** PDF jobs skip Chapter Reading and QX
   because the reader already ruled (`chapter_reading_contract.py:101-108`,
   `canonical_source_phase212_contract.py:57-62`); `.mmd`/`.txt` uploads pay
   for both to reconstruct the same facts.

What the flat MMD loses (all recorded in the block JSON): page_id/number,
reading_order, bbox, per-block confidence and review_flags, native heading
levels (demoted to bold under an active outline, `:2651-2658`), the verbatim
task cue (replaced by a structural cue, `:2677-2682`), figure ownership
(`linked_visual_orders`), and table structure (`table_rows` flattened to
`tabular` text, `:2429`).

Rule 1 alignment: everything C touches is **mechanics** — IDs, joins,
transport, chunk boundaries. The model rulings from the reader (task
membership, source_kind, chapter outline, figure ownership) are already the
authority; C removes deterministic regex re-derivations standing between
those rulings and their consumers. No new deterministic content judgment
enters; several existing regex classifiers exit.

## 2. The phases

Each phase is its own PR: full backend suite + reconversion of both audit
chapters (Maths + English source PDFs from the Concept Mapping Audit corpus)
with a machine diff of the canonical inventory and all four outputs against
the corrected files. No content regression tolerated.

### C1 — compile the canonical document from blocks (core; first; riskiest)

- Mint durable block ids from the reader output (page_id + reading order);
  build canonical blocks directly from page blocks, carrying page number,
  reading order, heading level, source_kind, table structure, figure links,
  and confidence natively.
- MMD becomes a pure **projection** of the canonical document (same renderer,
  unchanged output) — kept for the viewer, downloads, and the non-PDF lane.
- `apply_page_acsd_relationships` becomes unnecessary for new runs (identity
  is native); keep it untouched as the rehydration path for existing jobs
  and old artifacts (`rehydrate_verified_fallback`, `:3569`).
- **Shadow first, then cut over** (the codebase's contract-install pattern):
  one release where both compilers run and any divergence in the canonical
  task inventory is recorded as a defect flag; only then the switch.
- Watch for: caches/seals keyed on MMD hashes (`derived_mmd_sha256` in the
  reconstruction manifest, `:3502`), the Phase 3.6 turnover path that
  rebuilds the canonical from the PDF, and QX/Chapter-Reading exemption
  predicates (must keep firing identically).

### C2 — carry page identity end-to-end (small; high value; independent)

- Fill `page_hint` on inventory items from native block pages (fixes the
  empty page column in CSV exports and assessment atoms).
- Populate the Phase 3 envelope's `acsd_ledger` from the block ledger.
- Delete both character-offset page-guessers; anomaly packets and vision
  heading evidence (`canonical_source_phase3.py:575`) read native pages and
  heading levels.
- C2 can be pulled ahead of C1 if desired — it only *reads* the existing
  block JSON artifact.

### C3 — chunk generation from block sequences (moderate)

- PDF lane: generation chunkers consume ordered block runs grouped by the
  reader's chapter outline, replacing regex re-splitting of flat text
  (`_split_mmd_into_chunks` `generation.py:3213`, `parse_mmd_sections`
  `:3368`, `normalize_mmd_headings` `:3304` — which first *undoes* the
  renderer's own heading flattening, and can slice mid-paragraph `:3256`).
- Non-PDF lane keeps the existing MMD chunkers untouched.

### C4 — non-PDF parity (optional; last; needs its own go/no-go with cost)

- Mint a synthetic block document for `.mmd`/`.txt` uploads from the model
  pass that already classifies their blocks (Chapter Reading,
  `chapter_reading.py`), collapsing the two-authority split. Separate owner
  approval with cost numbers before starting.

## 3. What does not change

Prompts and semantics; the release/Master/staging contracts (Restructure A
included); the §12 decision register; all pre-spend pauses; every model
ruling. Old jobs keep working through the existing rehydration path — no
destructive migration. Rollback at every phase is reverting one contract
install.

## 4. Key artifacts and seams (from the 2026-08-29 survey)

- Reader dispatch: `canonical_source_phase221_contract.py:149-159`; wrapper
  install order `app/services/__init__.py:121-142`.
- Block schema: `_block_schema` `canonical_source_phase221_fallback.py:305`;
  kinds `:89-99`; bundle envelope `:2172-2184`
  (`PAGE_ACSD_SCHEMA_VERSION = "1.1.0"`).
- Artifacts on disk (`UPLOAD_DIR/{job_id}/source-shadow/`): `source.raw.mmd`,
  `source.aegis-source.json`, `source.aegis.mmd`, `source.source-report.json`,
  `source.gpt-page-acsd.json` (`:3438-3448`); figure crops content-addressed
  in `DATA_DIR/source-asset-store`. The block JSON is never stored in the DB —
  only `mmd_text`/`question_inventory` are (`models.py:322,332`).
- Inventory built from ACSD tasks, not MMD: `inventory_from_canonical`
  (`canonical_source_phase2.py:762`), item fields `:883-951`
  (`source_kind`, `_acsd_*`, `page_hint` — dead).
- Assessments lane is pure-MMD (`identify_questions_from_mmd`,
  `generation.py:553`) — out of C's scope except via C4.
- Non-PDF marker: the MMD header stamp
  (`mmd_is_gpt_reconstructed`, `:731`) and
  `source_contract.source_reader`; uploaded `.mmd` must never be judged
  stale (`:741-745`).
