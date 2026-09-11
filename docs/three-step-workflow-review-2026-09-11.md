# Three-step generation workflow — review and proposal, 11 September 2026

**Owner instruction (11 September 2026).** Run the whole pipeline in three
explicit parts without changing any existing detail:

| Step | Owner's words | What the team does |
|---|---|---|
| 01 · Generate Concept Files | "extracts all the questions as it is under types, cases" | Nothing until the two Concept files exist. |
| 02 · Generate Master Files | "team reviews it, inculcates the changes … and then upload the same files for generation of master files for Post (with assessments). And for pre, generation of new assessments will happen instead of extraction." | Reviews and edits the Concept files (remove/add concepts, topics, questions), uploads them, presses Generate Master Files. |
| 03 · Publish | "the team then reviews, edits, make changes for master files and then upload the files to cms and also to the data base." | Reviews and edits the Master files, uploads them; Aegis publishes them to the CMS workbook and to the database. |

Additional rulings in the same instruction:

* Work that only serves later steps ("the polishing of question, or writing
  output files") moves out of Step 01 into Step 02.
* After the team uploads reviewed files, Step 01's source artifacts are
  "completely invalid" and are "nowhere connected to the second step".
* Nothing repetitive may run: no stage may spend time or money twice for the
  same purpose.
* Every existing detail is conserved. Only ordering changes, plus "a few extra
  deletions and additions", and the owner wants to be consulted on those.

CLAUDE.md Rule 0/Rule 1 and Q33 still bind: every pipeline-step removal is
proposed to the owner before it is removed, and no deterministic semantic
judgment is introduced. This document is that proposal, with the evidence for
each disposition. The register entry that freezes the decided parts is
[Q51](aegis-restructure.md#q51--decided--three-step-workflow-as-is-concept-questions-step-2-polishing-and-reviewed-master-publication).
Line numbers below refer to the tree at merge commit `1e19eac` (PR #310),
before this change.

## 1. Where the pipeline stood before this change

Q41 (9 September) introduced the Concept-review pause and Q49 (11 September)
made the reviewed files independent inputs. The run already had two steps:

* **Step 1** — source upload → conversion → concept extraction → Phase 3 →
  Concept Refiner → staged Concept releases for both lanes → pause at 70%
  ("Concept files ready for review"). Pre question generation was deferred for
  new runs (`reviewed_file_workflow_policy` v1). The source-question polishing
  pass (Pass 4) still ran inside Step 1 at the inventory join.
* **Step 2** — `POST /uploads/{job}/concept-review/master` → read each
  reviewed file with an independent API author, critic and Fixer → generate
  missing Pre questions from the accepted Pre concepts → build both Masters →
  four outputs ready. Reviewed question wording was frozen verbatim; no
  polishing pass existed in Step 2.
* **Publication** — separate explicit acts per output: Concept rows to the
  database plus the shared Bulk Import output workbook, Master rows to the
  database only. An edited **Concept** workbook could be uploaded and published
  in one act; there was no path for an edited **Master** workbook, and the
  Build Concepts page had no Master publication control at all.

## 2. Stage inventory (verified audit)

Six independent readers audited Step 1, Step 2, publication, the frontend, the
tests/policy pattern and provider spend; the load-bearing claims below were
re-read in the code before being recorded. Model calls are author + independent
critic (+ Fixer) unless stated.

### 2.1 Step 1 — source to Concept files (new run)

| # | Stage (progress label) | Entry point | Model calls | Needed for the Concept files? |
|---|---|---|---|---|
| 1 | Conversion: GPT PDF-to-ACSD reader, page verification, outline, visual assets | `canonical_source_phase221_fallback.reconstruct_pdf_to_acsd` | per 3-page batch (cached), outline once (cached) | yes — the only source reading |
| 2 | Phase 1/2/3 source shadows: canonical source, ACSD task ledger, semantic graph (+ critic) | `canonical_source_contract`, `canonical_source_phase2_contract`, `canonical_source_phase3_contract` | hierarchy classify + critic per 10 sections (cached); anomaly adjudication + critic | yes — task-membership and topology authority |
| 3 | Generation entry: routing record mint, Architect instruction set, language plan, checkpoint identity | `build_concepts.generate_post_learning:4632` | Architect author + critic once; language plan for poem/prose | yes |
| 4 | Chapter Reading (skipped when the PDF reader already ruled) | `chapter_reading_contract:40-79` | 2 per chunk (cached) | yes for non-PDF sources |
| 5 | Phase 3 graph verification + source-review pause | `canonical_source_phase3.prepare_generation_graph` | reuse of the cached verified graph; anomaly select + critic | yes |
| 6–11 | Concept extraction: parse structure (0.01), early inventory track, skeleton (0.24), canonicalize (0.27), align topics (0.35), descriptions (0.55) | `generation.py:21058-21224` | skeleton author + verdict per chunk; single-shot repairs | yes |
| 12 | Question/Task Inventory join (0.58→0.70) | `generation._finish_inventory_with_topics:8029` | inventory author + completeness verdict per chunk; chapter-wide topics once | yes — every Types/Cases Example |
| **13** | **Question polishing (0.705)** | `question_polishing_contract.install` → `question_polishing.polish_inventory:713` | author + critic per 12 items (file-cached) | **partly** — only what the reviewer sees in Example cells; Step 2 never reads it (`reviewed_file_input.py:388-397`) |
| 14 | Type mining, consolidation, fragmentation verdict (0.72→0.76) | `generation.py:21266-21310` | ~7 single-shot calls + verdict/critic | yes — Types/Cases carrier |
| 15 | Type granularity pause (pre-spend) | `generation.py:21318-21590` | only on a consolidate directive | yes |
| 16 | Sufficiency concepts + culminations (0.79→0.81) | `generation.py:21592-21630` | once each | yes |
| 17 | Topology prep (0.812→0.815) | `concept_topology_contract._strip_source_owned_allocations` | none | yes |
| 18 | Phase 3 envelope seal/reuse | `concept_topology_contract:298-489`, `phase3/envelope.build` | none | yes |
| 19 | Phase 3 Settle (0.815) + settle capture | `phase3/settle.py`, `phase3/prelearn.capture_stage` | per concept / per batch | yes |
| 20 | Phase 3 Host, Type ownership, coherence (0.86) + host capture | `phase3/host.py`, `phase3/coherence.py` | per Type/Case unit; per split Type; once | yes |
| 21 | Phase 3 Place → Analyse → Polish (0.88) + captures | `phase3/place.py`, `analyse.py`, `polish.py` | per pooled item / batch; Polish only when findings exist | yes |
| 22 | Prerequisites merge (0.885), Assemble (0.89, deterministic) | `phase3/prelearn.merge`, `phase3/assemble.py` | merge once | yes |
| 23 | Pre map, needed-for links, Pre analysis (0.89→0.915); **Pre questions deferred** | `phase3/premap.py`, `preanalyse.py`, `runner.py:608-620` | map once, links per batch, analysis once + per batch; **no** Pre question calls | yes — Pre rows, `related_concepts` (`build_concepts_release.py:4316-4380`) and the Misconception/Error Analysis section |
| 24 | post_type_assignment checkpoint, artifact hand-off | `generation.py:22499-22566` | none | yes |
| 25 | Final validation and repair (0.93) | `generation.py:22647-22855` | rich-text repair once; Fixer per blocking row | yes — strict artifact gate |
| 26 | Deposit capture, inventory persistence (0.94) | `build_concepts_release_contract._capture_deposit` | none | yes |
| 27 | Concept Refiner, Post rows (0.945) | `release_refiner.refine_release` | author + critic per row | yes — final teaching prose (Q31) |
| 28 | stage_release: Type/Case audit, release QC, asset probe, KaTeX render, example-ownership judge, payload | `build_concepts_release.stage_release:3204-3546` | example ownership once (kernel) | yes — the Concept file's source of truth |
| 29 | Pre sibling staging + Pre Concept Refiner | `build_concepts_release.stage_pre_release_from_run`, `_refine_pre_records` | author + critic per Pre row | yes |
| 30 | Concept review pause (0.70) | `release.initialize_concept_review`, `uploads.pause_run_for_review` | none | yes — the hand-off |
| 31 | Download-time projections: Concept File (bulk import xlsx), release.xlsx, diagnostics.zip, release.json, inventory.csv | `build_concepts_release_files.py` | none (lazy) | Concept File yes; the rest are audit conveniences built on download |

What the reviewer sees: questions have no sheet of their own. Assemble embeds
them in `concept_details` as `Type NN: … / Case NN: … / Example: <prompt>`
(`phase3/assemble.py:55-155`); the prompt is `example_prompt`, backfilled from
`generation._inventory_task_text` (raw wording plus shared context when the
task requires it, plus `[img]` tags; `generation.py:8952-9050`). Under v1 the
polishing wrapper substituted the polished wording there.

### 2.2 Step 2 — reviewed files to Master files

| # | Stage | Entry point | Model calls |
|---|---|---|---|
| 0 | Upload reviewed file (transport only) | `reviewed_file_input.queue` | none |
| 1 | Lifecycle gate, acceptance of unchanged lanes | `build_review_masters:1284-1337` | none |
| 2 | Reviewed Post file extraction | `reviewed_file_input.prepare` | 1 author + 1 critic (+ Fixer) per lane per distinct file; unchanged lanes are re-rendered from the staged payload and extracted the same way |
| 3 | Reviewed Pre file extraction | same | same; file-supplied Pre questions become `generated_questions` |
| 4 | New Pre questions for concepts lacking them | `reviewed_file_input.ensure_pre_questions` → `phase3/prequestions.build` | plan once + author per missing concept, each with critic + Fixer; evidence = reviewed Pre concepts only |
| 5 | Master orchestration (both lanes concurrently) | `_build_master_siblings` → `rebuild_lane_master` | second `prepare()` and Pre regeneration are cache hits (sha / missing-id short-circuits) |
| 6 | Pre-flight: snapshot, leak barrier, decision context | `assessment_release_run.run_release_for_job:1570-1849` | none; envelope sha = reviewed payload sha, store `reviewed-master-decisions` |
| 7 | Post inventory freeze: regex governing-instruction fold, teaching-order projection, **Q18 pre-learning claim**, compound fold, **P3 source dedup** | `assessment_release_run.py:2056-2192` | 2 chapter-wide decisions (author + critic + Fixer) |
| 8–11 | Cells, materialize (frozen wording, no re-polish), answer restriction, marking | `assessment_cells`, `assessment_materialization`, `assessment_answer_restriction`, `assessment_marking` | author + critic + Fixer per item |
| 12 | Joint item review | `assessment_item_review` | 1 auditor per item |
| 13–17 | Routing (mechanical for reviewed targets), levels, clustering, describe, group QA | `assessment_routing`, `assessment_grouping`, `assessment_quality` | per candidate / per group |
| 18 | Question label reservation | `assessment_release_run.py:3138-3166` | none |
| 19 | Master Refiner (candidate wave, group wave) | `assessment_master_refiner` | author + critic per unit |
| 20 | Freeze and publish to disk: `create_release`, `publish_release` (render, readback, KaTeX, assets, readiness) | `assessment_release_service.py:357-467, 616-777` | none |
| 21 | Finalize marker `master_ready`, job status `generated` | `build_review_masters:1417-1493` | none |

### 2.3 Publication acts before this change

| Output | Act | Route | What it writes |
|---|---|---|---|
| 01/03 Concept | publish staged rows | `POST /uploads/{job}/upload-release?lane=` | Topic/Concept upsert + `append_concepts` into the shared CMS output workbook `bulk_import_output.xlsx` (transactional outbox) |
| 01/03 Concept | edited workbook then publish | `POST /uploads/{job}/upload-edited-workbook?lane=` | one manual-edit round (five concept fields; unmatched rows refused) + the same publication; mints a new staged uid, which **stales the Step 2 Master** (`build_concepts_release_files.py:340-350`) |
| 02/04 Master | database upload | `POST /build-assessments/releases/{id}/upload-to-database` | Group/Question rows from the immutable snapshot after hash verification; **no** CMS workbook append; **no** edited file accepted |
| legacy | force release, reviewer revisions, release-review manual edit / instruction | `POST /uploads/{job}/release`, `/revisions`, `/release-review/*` | historical surfaces; `apply-instruction` re-reads `job.mmd_text` |

## 3. Disposition table

| Stage | Disposition | Why |
|---|---|---|
| Step 1 stages 1–12, 14–31 | **Keep in Step 01, unchanged** | Each produces content the reviewer sees in the Concept files (topics, concepts, details, mastery, error analysis, Types/Cases/Examples, Pre map, related concepts) or is a mechanical gate. None is consumed by Step 2. |
| Step 1 stage 13 — question polishing | **Move to Step 02** (this change, v2 runs) | Owner instruction. Under Q49 the Step 1 polish decisions were never read by Step 2; they only changed the Example wording the reviewer saw. Step 1 now renders the source wording as is; polishing runs once, in Step 2, over the reviewed Post questions. |
| Step 1 "writing output files" | **Already Step 02** | The only Master output writing (render, read-back, KaTeX/asset validation, release directory) happens in Step 2 stage 20. Step 1 writes only the Concept payload; every other Step 1 file is a download-time projection with no model cost. |
| Pre question generation | **Already Step 02** (Q49) | Generated from the reviewed Pre concepts only. |
| Step 2 stages 0–6, 8–21 | **Keep in Step 02** | Reviewed-file extraction, new Pre questions, Master authoring with every Q31 review stage. |
| Step 2 stage 7 — Q18 pre-learning claim and P3 source dedup on a reviewed Post set | **Proposed deletion on reviewed inputs — owner approval required (D4)** | Two chapter-wide decisions per run that re-judge membership the reviewer fixed in Step 02 and may drop reviewer-kept questions (`assessment_release_run.py:2084-2119, 2155-2192`). Not changed here. |
| Step 2 stage 7 — regex governing-instruction fold on reviewed items | **Proposed change — owner approval required (D5)** | Deterministic wording classification (`assessment_source_inventory.py:90-99, 130-190`) on items whose placement the reviewed-file author already decided. Not changed here. |
| Step 2 — teaching-order receipt on reviewed rows | **Fixed (mechanical)** | Reviewed records carried no `_aegis_release_qids`, so every reviewed question was flagged "no accepted Concept question-order receipt". `prepare()` now records each concept's reviewed question order. |
| Step 2 — unchanged-file re-extraction | **Kept; owner decision recorded (D3)** | Q49 decided that an unchanged lane consumes exactly the generated workbook. Cost: one author + critic per unchanged lane per run. |
| Step 2 — critic stack (per-decision critics, joint item review, group QA, refiner critics) | **Kept (Q31)** | Owner's 8 September table already lists these as "keep until a measured alternative is approved"; switches exist (`AEGIS_MASTER_*`). |
| Step 2 — Concept Refiner per row, Pre needed-for links, Pre analysis | **Kept in Step 01** | They serve the Concept files (verified: `related_concepts` and the Misconception/Error Analysis section). |
| Explicit per-lane Master rebuild routes | **Fixed (accounting)** | They ran outside the usage/journal wrapper, so retry spend and logs were not persisted on the job (Q41/Q43 contract). |
| Legacy revisions / release-review / force-release routes | **Proposed gating for review-workflow jobs — owner approval required (D7)** | `apply-instruction` supplies `job.mmd_text` after Step 2; the UI already hides these for review jobs. Not changed here. |
| Step 03 — reviewed Master upload and publication | **Added** | See §6. |
| Frontend | **Re-presented as three steps** | Step 02 component retained; Step 03 component added; false "uploaded to database" badge corrected; legacy jobs unchanged. |

## 4. Step 02 independence from Step 01 source artifacts

Verified reads of Step 1 material during Step 2 when the reviewed-file path is
active (`reviewed_file_input.active(payload)`):

| Read | Location | Disposition |
|---|---|---|
| Five metadata keys copied from the previous staged payload: `version`, `target_chapter_id`, `source_book`, `directory_metadata`, `target_identity` | `reviewed_file_input.py:349-350` | legitimate — chapter and publication selection (Q49) |
| Chapter/Topic/Concept rows from the database for machine identity and directory metadata | `build_concepts_release_files.transient_release_hierarchy:639, 736-747` | legitimate — identity only |
| The job artifact directory as parent path for the new decision stores and snapshots | `assessment_release_run.py:272-280, 1808-1827` | path only |
| Pinned source-asset pixels reachable through `[img]` URLs quoted inside the reviewed text | `assessment_visual_evidence.py:51-97` | the reviewer's file carries those URLs; the pixels are the file's own evidence |
| The generated Concept workbook, re-rendered for a lane without an upload | `reviewed_file_input.py:317-330` | the file the reviewer downloaded (Q49); see D3 |
| `original_source_question_ids` frozen in the review marker | `build_concepts_release.py:600-606` | provenance only; no Step 2 consumer (grep verified) |

Bypassed for reviewed payloads: `job.mmd_text`, `job.generation_checkpoint`,
canonical source artifacts, chapter reading, containers, `mined_types`, the
grounding certificate, the Step 1 Phase 3 envelope and decision stores, the
`_generated_lane_source_qids` barrier read, and the legacy corrected-workbook
Pre regeneration that reads `source.phase3-envelope.json`
(`build_concepts_release_contract.py:1043-1046, 1061-1085`). Historical jobs on
the legacy path keep their recorded behaviour. This change also records the
run's workflow version on every Step 2 payload so Step 3 can read it.

## 5. Repetition register

| Candidate | Cost driver | Why it exists | Disposition |
|---|---|---|---|
| Question polishing in Step 1 AND verbatim freeze in Step 2 (wording decided twice, in two places) | author + critic per 12 items | Q39/Q41 polished before Type/Case mining | **Resolved**: v2 runs polish once, in Step 2 (D1) |
| Unchanged-lane re-extraction by API | 1 author + 1 critic per unchanged lane per run | Q49: the generated workbook is the Step 2 input, never the private Step 1 inventories | **Owner decision D3** (default: keep) |
| Q18 pre-learning claim and P3 source dedup on reviewed Post sets | 2 decisions per run; may remove reviewer-kept questions | Q18 / P3 owner approvals for source-extracted banks | **Owner approval D4** (proposal: skip when the Post inventory is a reviewed file) |
| Second `prepare()` and Pre regeneration inside the lane rebuild | none | control-flow duplication | free (sha / missing-id short-circuits); no change |
| Pre coverage re-plan on a retry after a partial failure | 1 plan decision per retry over the still-missing subset | recovery design | recorded; no change |
| Joint item review + group QA + refiner critics on top of per-decision critics | up to 8 review calls per Post item, 2×4 per group | Q31 restored all critics deliberately | **Kept**; measurement switches exist (D6) |
| Concept Refiner per row (batch 1) both lanes | 2 calls per row | Q31 default `all` | **Kept**; re-batching would be a versioned policy change (D6) |
| Teaching-order spurious flag on every reviewed question | no spend; noise in every release | reviewed records lacked the receipt | **Fixed** |
| Explicit lane rebuild spend not persisted | accounting gap | routes predate the usage wrapper | **Fixed** |

No same-evidence, same-purpose double judgment was found elsewhere: per-chunk
retries are bounded corrections after an independent verdict; verified GPT
sources already skip Chapter Reading and QX re-adjudication; the early
inventory track and inline extraction are mutually exclusive.

## 6. Step 03 design — reviewed Master files to CMS and database

"Publish to the CMS" in this codebase means the append into the shared Bulk
Import output workbook that the UpSchool CMS ingests, alongside the database
write; the Concept acts already do both. Masters did only the database half
and accepted no edited file. Step 03 adds, per lane:

1. **Upload the reviewed Master file** — `POST /uploads/{job}/master-review/submit?lane=`.
   The reviewer's edited Master workbook (same layout as the download) is
   parsed with the existing read-back parser. Rows are matched to the
   release's frozen candidates by `question_label`; every changed cell of the
   question band is applied **verbatim** as the reviewer's decision (§7 of the
   register: the human is the last word on their own correction). Rows absent
   from the file are omissions. A row without a known label is refused with a
   readable message: a Master row carries no source provenance (source atom,
   blueprint cell, restriction reason) and the Post Master is source-only
   (Q39/Q41), so new questions are added in Step 02's Concept file and the
   Master is rebuilt from it. The result is a **new immutable release
   version** (contract §44) rendered, read back and validated exactly like the
   Step 2 version; a value outside the closed CMS vocabulary or any mechanical
   defect makes readiness *blocked* with the issue named, never a silent
   rewrite (Q45). No model is called. Identical re-uploads record no new
   version. The round (file hash, filename, edits before/after, omissions,
   additions, flags) is kept in the review marker and on the release.
2. **Publish the Concept file** — the existing `upload-release` act (database
   upsert + CMS workbook append), required before the Master because Master
   rows resolve against published concept identities.
3. **Publish the Master file** — `POST /uploads/{job}/master-review/publish?lane=`:
   the existing `upload_master_to_database` (hash-verified, idempotent) and
   then the CMS half that was missing: the newly published questions are
   appended to the shared output workbook under the same lock and outbox
   discipline the Concept act uses. The receipt (groups/questions created,
   labels reissued, rows appended) is stored per lane; when every available
   lane's Master is published the review marker reads `published`.

Concept editing is deliberately **not** reopened in Step 03: an edited
Concept workbook at publication time mints a new staged release identity that
stales the Step 2 Master and forces a paid rebuild. Concept corrections belong
to Step 02.

## 7. Decisions requested from the owner

Each item names the default this change implements unless the owner rules
otherwise. Items marked *approval required* are **not** implemented.

| # | Decision | Default implemented / recommendation |
|---|---|---|
| D1 | Question polishing moves from Step 01 to Step 02 for new runs. | Implemented (workflow policy v2). Step 01 Examples show the source wording as is; Step 02 polishes the reviewed Post questions once, with the reviewed file as the only evidence. |
| D2 | "As is" in Step 01: the Example cell shows the source task with its necessary shared context and images; the mechanical renderer still omits printed worked solutions and leading task labels, exactly as before. | Implemented as described; say if the printed solution text should also appear. |
| D3 | Unchanged lane without an upload: keep Q49 (re-render the generated workbook and extract it by API; one author + critic per lane) or adopt the staged Step 1 rows mechanically (zero calls, but Step 1 rows re-enter Step 2). | Default: keep Q49 (one authority path, no reconnection to Step 1). |
| D4 | Skip the Q18 pre-learning claim and the P3 source-duplicate verdict when the Post inventory is a reviewed file (2 decisions per run; they can drop reviewer-kept questions). | *Approval required.* Recommendation: skip on reviewed inputs; the reviewer's set is authoritative (Q41). |
| D5 | Trust the reviewed-file author's placement of governing instructions instead of the regex fold on reviewed items. | *Approval required.* Recommendation: yes (Rule 1). |
| D6 | Master critic stack (joint item review, group QA, refiner critics) and per-row Concept Refiner: keep Q31 defaults. | Kept. Any reduction would be measured first with the existing switches. |
| D7 | Gate the legacy force-release, revisions and release-review routes off for review-workflow jobs (one of them re-reads `job.mmd_text` after Step 2). | *Approval required.* Recommendation: yes; historical jobs keep them. |
| D8 | File-supplied Pre questions pass through the mechanical display renderer (heading/solution stripping, image tags) before authoring. | Unchanged (Q49 behaviour); say if they should be frozen byte-for-byte instead. |
| D9 | Step 03 Master edits: every question-band cell applied verbatim (question, question text, options/answers/explanations/placeholders, sub-question blocks, marks, duration, keyboard, category, cognitive skill, difficulty, appears-in, answer restriction, group description); omissions honoured; rows with an unknown or blank label refused with guidance to add questions in Step 02; chapter/topic/concept and group-identity cells and placement moves recorded as flags rather than applied. | Implemented as described. Say if Step 03 should also accept brand-new Master rows (they would need source provenance the Post Master contract requires). |
| D10 | "Upload to CMS" for Masters = append the published questions to the shared Bulk Import output workbook, mirroring the Concept act. | Implemented. |
| D11 | Explicit per-lane Master retry spend and logs are persisted on the job. | Implemented (accounting fix). |
| D12 | Teaching-order receipt recorded on reviewed concepts. | Implemented (mechanical fix). |
| D13 | Orphaned frontend surfaces (ReleaseReview page, ConceptReviewPanel) and their unused client methods are dead code; their backend routes remain. | Left in place; removal is dead-code cleanup the owner may approve with D7. |
