# Aegis end-to-end step review — 8 September 2026

This is a source-code audit against `95a1c6e8e4f7c2ecc82b80a3256c3e8651d6664b`,
with the owner's latest clarification: the two column spreadsheets express the
general output contract; subject-specific rubric tags apply to English only.
It records the existing execution path and possible simplifications. **No step
has been removed or disabled. Any proposed removal must be presented to the
owner and approved before implementation.**

Code and prompt inspection establishes intended behavior, not successful live
execution. This review did not call production providers, upload assets to Fly,
or run a complete chapter. The new source corpus must supply that evidence.
Findings below are baseline observations; implementation changes in the same
review should name which findings they close.

## What actually runs

The displayed phase names and service-file version numbers are not a sequence
of thirty separate model calls. Some files install compatibility wrappers or
mechanical contracts. The service installation order is in
[`backend/app/services/__init__.py`](../backend/app/services/__init__.py).
The following table describes actual responsibilities and conditional passes.

| Stage | Input → output | API judgment and evidence | Why it exists |
|---|---|---|---|
| Upload and conversion, Phase 01 | Original PDF → page/block ACSD, canonical source, source MMD, visual crops, diagnostic artifacts | The GPT PDF reader transcribes original page images; a separate verifier compares the extraction with those same pages. Bounded corrections preserve source words and record omitted page furniture. | Recover text, tables, formulas, task boundaries and images before generating teaching content. |
| Chapter outline | Verified page blocks → chapter title, ordered source topics, assessment collections, whole tasks and independent task partitions | `derive_chapter_outline` judges teaching divisions and question independence from a structural digest. The source's own small topics must survive. | Keep textbook topic identity distinct from exercise banners and formatting. |
| Source graph and task accounting, Phase 02 | Canonical source → semantic graph, source topic/subtopic evidence, closed question inventory, two content containers | Source-semantic services and inventory adjudication record content roles and ownership. The QX author and critic account for every eligible source block; current verified GPT-source provenance exempts already-decided membership. | Avoid lost questions, accidental duplicate task rows, and invented topics. |
| Skeleton, topic alignment and reusable Types | Source graph and inventory → concept proposals, reusable Types/Cases, culmination proposals | The pre-81% portion of `generation.py` performs semantic extraction/alignment/mining; Phase 3 later settles and hosts these proposals. | Separate a textbook section from the teachable concepts and question methods it contains. |
| Source question polishing | Eligible inventory questions → derived `polished_task`, notes and retained original text | Existing author rewrites Concept-example wording in batches. The baseline has no independent polishing critic. | Make examples usable away from the source page, while preserving source evidence. The baseline's permissions are too broad; see G2. |
| Settle | Sealed envelope and proposals → finalized concept topology, evidence grounding and original teaching content | Three purposes: topology, grounding, and authoring. Each uses the decision kernel, independent advisory criticism and a Fixer seam. | Decide the concept breakdown, demonstrate where it comes from, and write it fully. |
| Host and Type ownership | Settled concepts plus Types/Cases/QIDs → certified hosts and any justified new concepts | Host author/critic; split Type ownership receives a recorded consolidation verdict only where needed. Literary topology plans remain authoritative through installed contracts. | Ensure every Type has one owner and each question has a defensible home. |
| Place | Frozen post-Host rows plus Container 02 → owned activities, information hubs and source figures | Placement author/critic; structural projection keeps Type-owned hubs with the recorded Type owner. | Keep supporting material and images with the concept that uses them, without copying them everywhere. |
| Analyse and allot | Chapter evidence plus frozen concept rows → misconception/error-analysis inventory and concept allotments | Inventory author, allotment author and advisory critics. | Make analysis specific to learning difficulties rather than decorative generic sentences. |
| Polish | Rows with named content findings → corrected concept details | Conditional repair only: no call when there are no findings. The baseline payload still contains semantic sentence/verb/overlap rules; see G3. | Resolve concrete content defects before assembly. |
| Running prerequisite capture and merge | Evidence at Settle, Host, Place and Analyse → one accounted prerequisite set | Four stage captures and a merge, with author/critic pairs. Captures see different evidence and are not four identical prompts. | Build Pre-Learning from prerequisite evidence rather than merely shortening Post conclusions. |
| Assemble | Final rows, hosts, placements and analysis → complete Post Concept rows | Mechanical projection of recorded decisions, identity and coverage checks; no new semantic judgment. | Produce consistent cells while preserving all source and decision identities. |
| Pre map, needed-for links, Pre analysis and Pre questions | Captured prerequisite set plus read-only finished Post rows → run-bound Pre Concept map and generated questions | Distinct map/link/analysis/question authoring decisions with critics. Current policy requires five Basic and five Intermediate questions per Pre concept. | Complete the separate Pre outputs and make their dependencies inspectable. |
| Concept release refinement and QC | Completed Pre/Post Concept rows → refined immutable release snapshots, findings and workbooks | Current Q31 default refines all rows with an independent critic; protected IDs, topology and relationships cannot be rewritten. | Improve final teaching quality while retaining an auditable before/after result. |
| Assessment source binding and classification | Source atoms or generated Pre questions plus output profile → lane/category/cell obligations | Semantic deduplication and cell decisions are recorded; the generated lane also verifies its Pre-only claim. | Decide response format from the actual required response and preserve source/generated provenance. |
| Answer/rubric materialization, restriction and marking | Cell, source evidence and curriculum context → complete item, model answer, criteria, answer space, weights, duration and keyboard | Separate authors and advisory critics. The joint reviewer sees the finished item across these decisions. | Write a correct answer and a rubric that can score it consistently; allocate marks only after defining the credit-bearing demands. |
| Assessment route, level, cluster, description and group QA | Finished items plus concepts → one-home routing, levels and groups with meaningful descriptions | Separate recorded semantic purposes; current Q31 retains group QA and all critics. | Keep pedagogical placement, demand, true variation and group description distinct. |
| Master Refiner and final release | Frozen items and groups → refined answers/rubrics/group descriptions, four outputs and release evidence | Author/critic can polish permitted fields; `question` and `question_text` remain protected. Mechanical workbook readback and arithmetic checks detect malformed files. | Deliver the agreed output format and preserve the question decisions that produced each group. |
| Database publication | Staged artifacts and release findings → explicit publication receipt | No new model judgment. Release blockers control database acceptance; the export remains available with findings. | Separate completed files from database acceptance. |

Primary execution references:

- Conversion and outline:
  [`canonical_source_phase221_contract.py`](../backend/app/services/canonical_source_phase221_contract.py),
  [`canonical_source_phase221_fallback.py`](../backend/app/services/canonical_source_phase221_fallback.py).
- Source graph and inventory:
  [`canonical_source_phase3_contract.py`](../backend/app/services/canonical_source_phase3_contract.py),
  [`canonical_source_phase212.py`](../backend/app/services/canonical_source_phase212.py),
  [`canonical_source_phase212_contract.py`](../backend/app/services/canonical_source_phase212_contract.py).
- Generation and detailed Phase 3 sequence:
  [`generation.py`](../backend/app/services/generation.py),
  [`phase3/runner.py`](../backend/app/services/phase3/runner.py),
  [`phase3/settle.py`](../backend/app/services/phase3/settle.py),
  [`phase3/kernel.py`](../backend/app/services/phase3/kernel.py).
- Release and assessments:
  [`release_refiner.py`](../backend/app/services/release_refiner.py),
  [`assessment_release_run.py`](../backend/app/services/assessment_release_run.py),
  [`build_concepts_release_files.py`](../backend/app/services/build_concepts_release_files.py).

## KaTeX, images and Fly: supported behavior and limits

### KaTeX

The PDF extraction prompt requires canonical `[Katex] ... [/Katex]` for
inline mathematical content and exact LaTeX for math blocks. Its independent
page verifier is explicitly told to accept those wrappers and compare the
mathematics with the page. This applies to every subject, including formulas
inside Science or other chapters.

[`katex_rules.py`](../backend/app/services/katex_rules.py) distinguishes:

- Rich teaching/question/model-answer text: wrapped mathematical expressions.
- Equation-typed answer or rubric cells: one raw LaTeX medium, without wrappers.
- Phrases: plain text; Image: the required image source.
- House-style arrays, `<br>` workbook line breaks, and complete image tags.

`assessment_materialization`, `assessment_release` and workbook readback use
these format checks. They establish wire-format correctness, not the truth of
the mathematics. The author and critic must still judge signs, values, units,
equivalent forms and meaning against the source.

**Baseline limit G4:** the review UI's
[`frontend/src/lib/richText.tsx`](../frontend/src/lib/richText.tsx) displays a
KaTeX body as a `<code className="katex-inline">` element. It does not invoke
the KaTeX renderer. Backend syntax checks also do not constitute rendering
every formula with a KaTeX engine. A successful syntax test should therefore
not be described as visual verification of the learner-facing formulas.

### Images and Fly

The original PDF page is the crop authority. The model supplies normalized
figure bounding boxes and visual ownership links; `materialize_visual_assets`
clips and renders those exact areas with PyMuPDF, writes JPEG bytes, hashes
them and records the source block's URL. It does not generate replacement
illustrations or infer the image from nearby words.

The public URL uses `AEGIS_PUBLIC_BASE_URL` plus
`/source-assets/{job_id}/{sha256}.jpg`. The crop is also pinned to
`DATA_DIR/source-asset-store`, outside the disposable upload directory, with
a content-hash manifest. [`source_asset_store.py`](../backend/app/services/source_asset_store.py)
implements that durable copy. [`source_assets.py`](../backend/app/api/source_assets.py)
serves only bytes matching the requested hash, can use the durable copy after
a job reset, and treats the legacy signature as advisory so key rotation
does not break already-published images.

[`fly.toml`](../fly.toml) configures the production HTTPS origin and persistent
`/data` volume. **Writing a crop while running on that Fly service is the
publication mechanism.** Setting a local process's base URL to Fly does not
transfer its local bytes to Fly. A local-only rehearsal must not claim it
has published usable production image links.

**Baseline limit G5:** release checks require image URL/alt syntax and source
ownership, but this review found no general release-time public HTTP image
availability/content-type probe. A URL can be well formed while its origin
or stored bytes are unavailable. Crop failures and failed durable pins are
logged/flagged rather than silently invented; those findings must remain
visible in the exported evidence and release state. A Fly acceptance run
needs actual GET checks on its exported image URLs and visual inspection of
the crops, including captions and diagrams in tables.

Existing local durability tests cover reset, legacy backfill, rotated
signatures, corrupt bytes and missing assets in
[`test_data_reset_durable_assets.py`](../backend/tests/test_data_reset_durable_assets.py).
Their presence is useful regression coverage; this read-only audit did not
execute them or establish current deployed-volume health.

## Prompt and evaluation findings

The assessment prompt family now has useful explicit distinctions: solve the
item; write the complete learner-facing answer; define separately observable
credit; score each required demand once; do not score unasked material;
record accepted alternatives; preserve the same Descriptive model answer in
both required fields; and restrict English tags to English textual rubric
criteria. The independent joint item reviewer checks across question,
answer, restriction and rubric. These are stronger instructions, but reliable
evaluation still needs adversarial answer examples, not just schema tests.

For each representative item, the evaluation review should include a full
correct answer, a valid equivalent, a partly correct answer, a response with
the right result but missing a required method/reason, a plausible wrong
answer, an irrelevant fluent answer, and an answer that contradicts itself.
An independent API evaluator should apply the exported rubric without hidden
author notes; disagreements should cite the exact criterion and source
evidence. Do not invent numerical quality thresholds or use keyword overlap
to decide conceptual correctness.

| Finding | Evidence at the audit baseline | Corrective direction without removing a step |
|---|---|---|
| G1 — outline criticism is missing | `derive_chapter_outline` calls the outline author, normalizes references and caches the result with `status="verified"`. The independent page verifier ran before these topic and partition decisions existed. | Add independent evidence-bound outline criticism with durable findings. Distinguish page transcription verification from outline review in provenance. Preserve the full task ledger and Fixer behavior. |
| G2 — source-example rewriting lacks an independent check | `question_polishing._decisions_via_api` makes one author call per batch. Its prompt permits changing read-aloud/pronunciation demands into written questions. Raw source fields survive, but changed demand is still a semantic risk. | Preserve the source's assessed skill and allowed mechanical contextualization; review every proposed rewrite independently against raw text, options and assets. Keep dissent visible. Do not silently turn an oral/performance task into a different written test. |
| G3 — residual semantic heuristics steer Polish | `phase3/polish.py` still asks for exact belief/error sentence forms, forbidden verbs and reduced word overlap because an overlap filter can delete output. The Polish kernel call has no independent critic argument. | Replace semantic shape decisions with API author/critic judgment; retain structural checks for identities and field shape. Audit the corresponding validator, not only the prompt string, so a strengthened prompt cannot be undone downstream. |
| G4 — KaTeX format is not rendered preview | `richText.tsx` renders the raw formula as a code element. | Add actual KaTeX preview and meaningful render fixtures, while preserving the exported wire format. Distinguish rendered preview from semantic mathematical review. |
| G5 — URL syntax is not image delivery | Source assets are durable locally and verified when requested; no general public-delivery probe was found at release. | Record a bounded asset availability check where runtime access permits, and verify live Fly image delivery during acceptance. Never invent a successful receipt for untransferred local bytes. |
| G6 — supported uploads are narrower than the UI's generic source language may imply | `mmd.py` states that Mathpix is retired. The Build Concepts PDF route uses GPT PDF-to-ACSD; raw image uploads have no OCR converter and other PDF upload routes may reject them. | Present an accurate supported-format matrix and route PDFs through the actual reader. Test image-only PDFs separately from standalone image files. Add unsupported formats deliberately rather than promising they already work. |

The Master materializer explicitly requires source wording with only the
permitted apparatus/context/notation projection. Its source atom contains
both `raw_text` and `normalized_public_text`, and the latter may carry polished
wording (`assessment_source_inventory._atom_of`). Regression checks should
therefore prove the final source-owned item follows the raw source authority,
not merely assume the stronger system prompt always wins.

## Steps considered for simplification — approval required

These are proposals to measure, not changes already made. An optimization
must preserve the owner-required API author/independent critic boundary,
content coverage, output files and visible failure evidence.

| Candidate | Why it may duplicate work | Required replacement/evidence | Quality risk and recommendation |
|---|---|---|---|
| **Primary owner-approval proposal: retire semantic wording and overlap heuristics in `concept_validator.py`** | Active belief/action vocabularies, an 80% token-overlap rule and mastery-text heuristics judge meaning from wording. They can reject a sound explanation or steer prompts toward formulaic sentences. | Replace these semantic decisions with recorded API author and independent critic judgments using the complete concept and source evidence. Retain mechanical schema, identity, field ownership and rich-text checks. Compare accepted/rejected examples across the supplied subjects. | **Await owner approval before removing any of these active checks.** This proposes a change in who judges meaning; it does not remove the independent review or formatting gates. No such check is removed by this audit. |
| Full Concept Refiner after conditional Polish | Both can edit teaching prose, although Polish handles named defects and Refiner audits final quality. | Compare the same source, same initial rows and same provider settings with current all-row refinement versus a reviewed subset selected by an API quality decision. Retain independent review and before/after evidence. | Subtle teaching weaknesses may have no mechanical flag. **Keep current Q31 default; do not remove on inspection alone.** |
| Per-decision assessment critics plus the joint item review | The complete-item reviewer revisits answer/rubric consistency already reviewed in fragments. | Prove the joint reviewer receives every source, decision rationale, protected identity and criterion, and independently covers each removed review responsibility. Measure the same Math/English/Science/Social items. | A late review can identify an error after dependent decisions are made. Q31 explicitly restored both. **Keep until the owner approves a measured alternative.** |
| Four prerequisite captures plus merge | Some prerequisites are repeated across stage captures. | Preserve all four stages' evidence in any proposed single inventory; compare missed/duplicated prerequisites and resulting Pre questions. | Host/Place/Analyse reveal prerequisites not present at Settle. **No removal recommendation without corpus evidence.** |
| Compatibility-wrapper stack | Installation indirection makes execution hard to reason about, and old names suggest obsolete converters or extra phases. | A later refactor can expose one explicit orchestration path while preserving cache identities, replay, old artifacts and every recorded decision. | This is code organization, not evidence that model steps are unnecessary. **Document first; approve behavior changes separately.** |
| Re-reading verified GPT source and re-adjudicating its task roles | These would repeat the page author's already-verified judgments. | Existing provenance-aware exemptions in `chapter_reading_contract._reader_already_ruled` and `canonical_source_phase212_contract.adjudication_exempt` already reuse that authority. | **No new removal is needed.** Retain the independent page verifier and source-identity checks. |

Topic outline, concept topology, Type ownership, figure placement, answer-space
restriction and rubric weighting should not be merged merely because each
reads the chapter. They answer different questions and have different output
authorities. Mechanical assembly, hashes, schema/readback checks and mark
arithmetic are also not removable semantic “extra steps”; they make model
decisions safely representable in the promised files.

## Evidence still needed from the supplied source corpus

Run fresh, policy-bound sources through their actual supported entry point.
For each source, retain the original-document hash, page/block extraction,
topic/partition ledger, concept evidence, Type/Case/QID coverage, image
manifest, raw and polished question text, all API decisions and critiques,
four final workbooks, readback findings and provider usage.

The acceptance review must trace at least: a short lower-grade topic, a
multi-page topic, a literary stanza/episode, a historical source box, a
hands-on activity, independent lettered questions, dependent multipart
questions, a proof/derivation, a circuit or geometry figure, a visual table,
an open response and a valid equivalent answer. Compare those against the
actual uploaded page evidence. A clean dry test suite is necessary regression
evidence; it is not proof that a new source's topics, concepts, diagrams or
rubrics have been understood correctly.

## Implemented closure for G5: delivery evidence, not a Fly claim

`source_asset_publication.py` now records local pin status separately from a
bounded public GET verification. It fetches only the explicitly configured
HTTPS origin's exact content-hash source-asset route, strips query parameters,
refuses redirects and arbitrary external targets, limits time/bytes/pixels and
checks HTTP status, JPEG MIME, the expected hash and actual JPEG decoding.
Identical hashes are probed once per inspection. Malformed URLs, missing
configuration, provider-independent network faults and exhausted budgets
produce named unverified/failed reports; they cannot discard finished files.

Both Concept lanes record a staging report. Master publication reads the
actual serialized Concept and Master workbook cells, including Image-typed
answer and subquestion-criterion cells, and records final reports against
the exact workbook hashes. Reports and findings appear in the existing
diagnostic/manifest channels. A new release with missing, stale or unsuccessful
required evidence cannot claim database readiness; its artifacts remain
available. Historical frozen releases retain their original behavior through
an explicit new-output validation version marker. Readiness and downloads do
not rerun network probes or the KaTeX engine.

The local run/resume scripts no longer invent an `aegis.local` image origin or
stamp every source as Mathematics. They require an actual configured serving
origin, take an optional actual publication label, and report public image
verification separately from file creation. Those scripts export a Post
Concept workbook; use the normal four-output run for full acceptance.

This closes the missing verification mechanism. It does **not** establish
that the newly supplied sources have run on Fly or that their image URLs are
currently available there; live execution and visual crop review remain
unperformed in this code-only change.

## Other review closures in this change

- **G1:** the chapter outline now receives independent criticism against the
  verified source blocks. Topic boundaries, whole-task/partition accounting
  and source references are reviewed separately from page transcription.
  The critique and its unavailable/dissent states remain recorded.
- **G2:** question polishing now has an independent batch critic and retains
  its before/after evidence. Oral pronunciation, listening, practical and
  collaborative demands must retain their response modality; source grammar,
  mathematical expressions, options and images remain protected. This is the
  derived Concept Example pass, not permission to rewrite source-owned Master
  questions.
- **G3, partial closure:** the Polish prompt and independent review have been
  strengthened. Active semantic vocabulary/overlap/mastery checks in
  `concept_validator.py` remain in place pending the explicit owner approval
  proposal above; this is a remaining conflict, not a completed removal.
- **G4:** the target KaTeX engine now produces a bounded validation report for
  staged concept fields and for actual serialized Concept/Master workbook
  cells. Unsupported syntax or an unavailable engine prevents new outputs
  claiming database readiness, while the files still ship. The review UI now
  renders mathematics. Engine validation establishes renderability, not the
  mathematical correctness of the expression.
- **Master wording and evaluation:** the old literary assumption that the
  learner has the chapter open has been removed from the materialization
  prompt. Contract §§19, 20 and 35 require the actual necessary source
  passage/context and owned visuals to travel with the question. Raw source
  text is explicitly authoritative; a derived polished field cannot override
  it. Printed errors and caption conflicts are flagged rather than silently
  repaired in source questions. The joint reviewer now tests the rubric
  against a valid equivalent, a partly correct response and a plausible wrong
  or irrelevant response, identifying the affected criterion without
  inventing a new scoring scale.

These closures describe implemented code and prompts. They do not substitute
for the planned fresh four-output runs on the supplied source corpus.

## Verification at the Q33 draft update

- Final targeted backend integration: **759 passed**, zero failures/errors,
  with a recorded JUnit result. Covers column policies, rubric arithmetic and
  readback, source authority, outline/polishing review, release lifecycle,
  image/KaTeX reports, and representative Phase 3 runner/cache behavior.
- The final image/render suite separately passed **28 tests**, including a
  serialized invalid formula that blocks database upload while both workbook
  files remain available. These counts overlap; they are not additive.
- Earlier combined Phase 3, prompt-registry and durable-image suite:
  **368 passed**. The exact-engine validator separately passed **19 tests**.
- Frontend: production build succeeded; **127 tests passed**. Fractions,
  arrays, scientific units, invalid input, canonical line breaks and safe
  handling of untrusted math commands are exercised with KaTeX **0.18.7**.
  The renderer uses the documented [KaTeX API](https://katex.org/docs/api.html)
  and [trust/resource options](https://katex.org/docs/options.html).
- Initial integration exposed seven stale labelled-explanation fixtures and
  one intentional payload-key addition; fixtures were updated to the current
  policy without weakening production gates, and the final run passed.
- `git diff --check` passed. No full-backend-suite result is claimed.

Provider credentials and Fly deployment access are not configured in this
workspace. No fresh source completed a live API run, no new source image was
verified on Fly, and no evaluator-quality improvement has been measured.
Docker is unavailable here, so the two updated runtime images were not built.
The changes remain a draft, with no merge, deployment or step removal.
