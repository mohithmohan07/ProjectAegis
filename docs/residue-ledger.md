# Residue ledger — build-sprint (running, per owner guardrail 29)

One line per unresolved residue. Severity: does downstream work build on a
false contract if this stays open? None below is foundation-false; the
sprint continues past all of them.

| # | Residue | Location | Consequence | Downstream safe? | Owner / fix point |
|---|---|---|---|---|---|
| R-QX1 | Legacy API extractor's post-spend semantic raises not re-polarized (`generation.py:6935-6939,7458-7466,7516-7535`) | legacy `_extract_question_task_inventory_via_api` body | Production-unreachable (Phase 2 rebind); the offline sample script and no-canonical paths can still halt on semantic doubt | Yes | QX follow-up slice |
| R-QX2 | QX-created tasks (missed asks) skip the Phase 2.1 visual-ownership pass — recorded in the ledger's `recorded_limits` | `canonical_source_phase212.py` reconciliation | An image-bearing ask recovered by the author ships without its visual until re-compiled with visuals rerun | Yes (flagged, recorded) | QX follow-up; re-order visuals after adjudication |
| R-QX3 | Live acceptance (QX4) not run — no real-book validation of author/critic/Fixer prompts | docs/spec-qx.md §6 | Semantic quality on real chapters unproven; scripted tests prove mechanics only | Yes for build; NO for production sign-off | Owner: live key + acceptance corpus run |
| R-QX4 | QX-created tasks default `source_kind="checkpoint_question"` with `not_model_ruled_flagged` (membership model-ruled, KIND not) | `_created_task` | Kind-sensitive routing treats recovered asks as checkpoint questions until a kind authority rules | Yes (flagged) | Kind-ruling step (outline-equivalent for text) |
| R-QX5 | `_refresh_inventory_from_source_anchors` and `_extract_question_task_inventory_via_api` names are stale (they render from the adjudicated ledger) | `canonical_source_phase2_contract.py`, `generation.py` | Misleading names only; behavior truthful, logs updated | Yes | Naming-truth cleanup |
| R-QX6 | Suite-wide echo author (conftest autouse) is a test-harness membership authority; any future test asserting the ABSENCE of QX fields will fight it | `tests/conftest.py` | Test-harness convention, not production; recorded so nobody mistakes echo verdicts for model output | Yes | Test harness; revisit at final debug pass |
| R-QX7 | Direct `_source_task_anchors` pins (corpus counts, ~30 anchor tests) not yet re-commented as candidate-surface pins | `tests/test_review_corpus_contracts.py` et al. | Comment debt only; the tests are mechanically correct as candidate pins | Yes | Final debug/cleanup pass |
| R-QX8 | Oversized single blocks are not windowed; the whole block text goes to the author in one payload | `canonical_source_phase212._author_prompt` | A pathologically large block could overrun the request; correction/Fixer path catches the failure loudly | Yes | QX follow-up (lossless windowing per spec §4) |
| R-S11a | Plan semantic roles are recorded in the plan artifact/envelope but NOT yet transported onto individual skeleton records; culmination consumers (`concept_refiner.is_culmination`, coverage-ledger exemptions, writer/QC checks) still read English title shape | `language_topology.py`, `concept_refiner.py` | A localized/renamed culmination title changes downstream behavior until role transport lands | Yes for expository; literary lanes ship flagged plans | Step-11 follow-up (GPT spec §8.3) |
| R-S11b | Output-02/04 identity composition (bands_record identity trio, `identity.titled` sharing, set-based Master validation, importer re-mint) unrepaired — same-titled cross-topic concepts survive authoring/validation/publication but their workbook round-trip identity is unproven | `assessment_workbook.py`, `assessment_release_snapshot.py`, `bulk_import/reader.py` | The "Courage in two stanzas" end-to-end contract is not yet provable through re-import | Yes (pre-existing surface; flagged) | Step-11 follow-up (GPT spec §8.2) |
| R-S11c | The language plan author sends the whole chapter in one call (no block batching); grammar-threading exact-once is schema-checked but multi-placement verdicts are not yet modeled | `language_topology.py` | Very large chapters could overrun one request; multi-home components need an explicit verdict field | Yes | Step-11 follow-up |
| R-S11d | Live acceptance for step 11 (The Elevator, refrain poem, repeated-name poem, Devanagari edition) not run — scripted tests prove mechanics only | docs/spec (GPT) §10.2 | Semantic topology quality unproven on real literature | Yes for build; NO for production sign-off | Owner: live key + corpus |
| R-S12a | Phase-2.2 adjudication-cache corruption injection (spec D5 seam D, sub-case d) not implemented; the batch/bundle cache injections are | `tests/test_fault_injection.py` | The phase22 corrupt-cache re-bill property is unpinned | Yes | Step-12 follow-up |
| R-S12b | The asset-failure companion (phase3 autonomous downgrade-to-flags twin, map §6) not separately pinned; only the halt+deterministic-retry residue is | `tests/test_fault_injection.py` | The correct-twin behavior is untested | Yes | Step-12 follow-up |
| R-S12c | Corpus sources are synthetic (copyright: real pages are the owner's call); live-API acceptance over real textbooks remains the operator residue the spec records | `backend/tests/acceptance_corpus/` | Mechanics proven; real-book semantic quality is the live run's business | Yes for build; NO for production sign-off | Owner/operator |
| R-S12d | The step-12 branch carries the step-8 lane by merge (guardrail 27: corpus must exercise the real final path); its PR must land AFTER #229 or retarget onto it | branch `claude/step-12-acceptance-corpus` | Merging step-12 to main before #229 would ship step-8 code unreviewed | Yes (ordering note) | Owner at merge time |

## E2E audit outcome (GPT audit of 865915b — docs/audit-e2e-main.md)

| Audit finding | Disposition |
|---|---|
| F1 seal mismatch on count-changing QX | FIXED (contract resyncs Phase-2.1 seals; create/reject reload regression) |
| F2 language plan cannot realize topics | CONTAINED: literary modes block pre-spend, named (build_concepts wiring); full topology materialization is the step-11 follow-up. R-S11a superseded by this row |
| F3 QX context/visual loss | FIXED (task_context attaches to owner tasks' shared_context + anchoring required; created tasks resolve figures mechanically; unresolved refs recorded, not cleared) |
| F4 title-shape identity | FIXED (topic-scoped addition screens; unique-title-only fallbacks; unambiguous-only re-homing; lossless same-identity dedupe — distinct content survives flagged). The direct-deposit title join (build_concepts.py:552-567) remains a recorded residue |
| F5 unverified asset serving | FIXED (sha256 verification on every serve, verified-only pinning, digest-checked boot sweep, named integrity-loss 404) |
| F6 evidence-starved QX review | FIXED (critic gets blocks+candidates; Fixer gets neighbours + nearest-anchored candidates; per-item flags surface as release issues via release_qc qx_item_review_flag; orphan Fixer decisions recorded on the ledger) |
| F7 unbounded correction | FIXED (correction bounded to defective blocks + their candidates; paid verdicts retained) |
| F8 stale plan replay | FIXED (decision key binds work name/blocks/tasks/contract versions; sealed hash re-verified; replay re-validated via plan_defects) |
| F9 unsealed blocking authority | FIXED by the release-gate pass (see commit) |
| F10 degenerate-crop retry poison | FIXED (per-figure flag-and-continue; pinned test flipped) |
| F11 sealed-bundle tamper trust | FIXED (canonical pages digest verified on reuse; pinned test flipped) |
| F12 row-count topology preference | FIXED (allotment-identity superset comparison; ambiguity keeps current rows, recorded) |
| F13 PDF lane double-spends QX | FIXED (page-ledger authority visible before compile / reader-identity exemption) |
| F14 false plan receipts | FIXED (Fixer reason = actual defects sent; critic outage recorded as unavailable) |
| F15 QX cache identity gaps | FIXED (author-context sha joins the sealed key) |
| F16 repeated-evidence collision | FIXED (occurrence-aware evidence location + validation) |
| F17 step-12 test truthfulness | FIXED (exact six-ask pins, partial-batch quota resume, task→figure link, honest spec wording, honest quota docstring) |
| F18 lossy repeated-question normalizer | FIXED by the release-gate pass (lossless normalization only) |
| F19 persisted volume-derived durations/descriptions win over fresh authoring | OPEN — owner decision (migration policy); recorded in GPT's docs/owner-decisions-open.md. Downstream safe: yes (legacy-data policy, not new loss) |
| F20 docs/ignore/comment drift | FIXED (.env.example providers+reasoning, .gitignore caches, QC-transport comments) |

Ledger corrections per the audit: R-QX2/R-QX4/R-S11a "downstream safe" columns were overstated — superseded by the F3/F6/F2 rows above. R-QX8's consequence note is superseded by F7's fix. R-S11b is CLOSED (Output-02/04 identity composition verified repaired at main). R-S12d is CLOSED (merge order completed).

## Post-audit follow-up round (finishing sprint)

| Item | Disposition |
|---|---|
| Audit F2 / R-S11a topology half | RESOLVED: the language plan's topics are MATERIALIZED as semantic-graph topics (compile_semantic_graph builds them from the plan's evidence blocks; the phase-3 contract threads the plan slot into graph metadata; the hierarchy model call is skipped as double spend). The pre-spend literary block is retired. Remaining R-S11a half: per-record semantic_role transport onto skeleton rows (culmination consumers still read titles) — still open, flagged plans carry roles for review |
| Step-12 interrupted-path injections (spec residue) | RESOLVED: `test_fault_injection_interrupted.py` pins both contracts — staging is atomic-at-the-end (a crash in the last assembly helper leaves the slot byte-untouched; retry re-mints cleanly), publication is one transaction with full rollback (zero partial rows, latch not set; retry publishes idempotently, no duplicate rows). No real defect found |
| Step 9 — review/edit surface | BUILT: `release_review.py` (view projection, verbatim manual edits, one bounded instruction pass), `models.ConceptReleaseVersion` (append-only §7 version rows incl. recorded failed rounds), three routes under `/build-concepts/uploads/{id}/release-review`, frontend page at `/build-concepts/review/:jobId` with house rich-text rendering. Recorded step-9 residues: R-S9a/b/c below |

## Performance round (2026-08-20)

| Item | Disposition |
|---|---|
| Parallel defaults ON | Gate 3→8, phase-3 decision workers 1→6, PDF page batches 1→4 (config defaults; env-reversible). Prerequisite hardenings landed: usage-accumulator mutation lock (exact cost reports under threads), atomic + locked DecisionStore directory writes |
| Pool given to the skipped loops | Release Refiner rows, Polish repairs, Settle topology/grounding/authoring batches (nested under the topic pool), QX author batches and Fixer decisions — all via `kernel.parallel_map_in_order`, decisions fan out, application stays in input order (byte-identical outputs). Worker log lines carry `[unit]` labels; each fan-out announces its width |
| Chapter Reading exempted on the PDF lane | Mirrors the QX reader-identity exemption (`adjudication_exempt`, one authority): block kinds on GPT-read sources are already model-decided and page-verified, so the chapter is not re-read or re-billed; the skip is logged. Text-MMD uploads keep the pass |
| Page-bundle reuse | Phase 3 adopts the convert-time `source.gpt-page-acsd.json` when its own copy is absent (validated by the same seals) and no longer writes a duplicate bundle |
| Truth fixes | Convert stream title no longer claims "Converting document to MMD"; the Phase 2.2.1 doc's Mathpix claims corrected (Mathpix is deleted; the GPT reader IS the converter) |
| Deferred (next round) | Per-call OpenAI client reuse at high concurrency; phase36 turnover double-prepare; kernel-level deferred-critic primitive (see round 2 note below) |

## Performance round 2 — stage overlap (2026-08-20)

| Item | Disposition |
|---|---|
| Place ∥ Analyse ∥ Polish | The three passes read the same frozen post-Host input and write disjoint artifacts meeting only in deterministic Assemble, so the runner runs them as parallel lanes (each on its own deep copy of the rows; `min(3, AEGIS_PHASE3_DECISION_WORKERS)`; 1 = sequential). Lane logs carry `[Place]`/`[Analyse]`/`[Polish]`, and label scopes now COMPOSE (`[Place · batch 2/4]`) so nested pools stay attributable |
| Capture riders (critic overlap, highest-value form) | The settle/host stage-boundary prerequisite captures ride on a side thread over deep-copied boundary evidence while the next stage runs; place/analyse captures ride inside their lanes. The merge consumes the capture list in canonical stage order whatever finished first. Removes four full author+critic round trips from the critical path |
| Phase 2 chunk fan-outs | Question identification chunks, Question/Task Inventory chunks (incl. per-chunk completeness verdict + retry), skeleton chunks, and Phase 2.2 evidence packets decide in parallel (`AEGIS_SOURCE_CHUNK_WORKERS`, default 4) and APPLY in input order — cross-chunk dedup, QINV numbering, canonical repairs, and the adjudication marker are byte-identical to sequential. Skeleton durable checkpoints stream via the new ordered `on_result` hook, so every checkpoint still carries a clean contiguous chunk prefix and resume semantics are unchanged |
| Kernel `on_result` | `parallel_map_in_order` gained an ordered side-effect hook (caller's thread, strict input order) for prefix checkpoints and monotone progress; pinned in tests |
| Residual critic overlap NOT taken | A kernel-level deferred-review primitive (store the decision only after an async critic lands) would compress the remaining single-decision seams (merge, premap chain, prequestions plan). Pooled stages already overlap author-of-N with critic-of-M across units, so the marginal gain is small next to its surgery on `decide()`'s store-put contract — recorded, not built |

## UI-makeover residues (2026-08-20)

| # | Residue | Location | Consequence | Downstream safe? | Owner / fix point |
|---|---|---|---|---|---|
| R-UI1 | RESOLVED: owner picked the Constellation Shield (gallery #3) with the Clarius Blue accent (B); wired through Logo.tsx, the favicon, and the accent tokens | `frontend/src/components/Logo.tsx`, `index.html`, `styles.css` | — | Yes | Done |
| R-UI2 | UpSchool's exact brand palette unfetchable (up.school and examin8.com egress-blocked); owner chose the Clarius Blue direction, exact parent-brand match still unverified | `styles.css` `--accent*` token block | Hue may sit near but not exactly on the parent brand until provided | Yes (5-line swap by design) | Owner shares brand colors or a screenshot |
| R-UI3 | Untested pages (Home, Database, Tagging, Workbooks, Admin, BuildAssessments) were restructured with no behavioral test net; tsc + App smoke only | `frontend/src/pages/*` | A missed regression on those pages would not be caught by CI | Yes (presentation-only changes; API calls untouched) | Follow-up: page-level tests |
| R-UI4 | `vite preview`/production serving assumed SPA-fallback for deep links; dev proxy now bypasses page URLs explicitly | `frontend/vite.config.ts` | A production server without HTML fallback would 404 deep links (pre-existing) | Yes | Deploy-time check |

## Step-9 residues

| # | Residue | Location | Consequence | Downstream safe? | Owner / fix point |
|---|---|---|---|---|---|
| R-S9a | §7 merge / split / remove operations are not in the instruction schema (changes + additions only) | `release_review._instruction_schema` | A reviewer asking to merge or delete rows gets a failed round (recorded), not a silent partial apply | Yes | Step-9 follow-up: extend the operation vocabulary |
| R-S9b | A reviewer's edit leaves the row's source-grounding seal describing the PRE-edit provenance; the edit's own record is the `_aegis_manual_edit_rounds` trail + review flag | `release_review.apply_manual_edits` | Grounding audits on an edited row must read the trail to see the text is the reviewer's, not the source's | Yes (flagged, recorded) | Step-9 follow-up: re-seal or annotate grounding on edit |
| R-S9c | Real KaTeX typesetting is not done on the review page — raw LaTeX shows in a styled inline chip | `frontend/src/lib/richText.tsx` | Formulas legible but unrendered in review; workbooks unaffected | Yes | Frontend follow-up (KaTeX dependency is the owner's call) |
