# Concept-generation review audit

## Scope and evidence

This is the repository-level traceability record for concept-generation
feedback incorporated over successive versions. The evidence available in this
checkout is:

- the 154-page authoritative review,
  `backend/data/Testing/UpSchool Mail - Feedback_ Aegis Concept Generation.pdf`;
- source MMD files and versioned AP, Electricity, and Rise of Nationalism in
  Europe workbooks under `backend/data/Testing/`;
- the **Reviews 01-06** regressions in
  `backend/tests/test_concept_mapping_reviews.py`;
- the cross-chapter contracts in
  `backend/tests/test_universal_review_contracts.py`;
- terminal validation coverage in
  `backend/tests/test_concept_validator.py` and
  `backend/tests/test_generation_validation_diagnostics.py`; and
- production services in `backend/app/services/` plus the bulk-import boundary
  in `backend/app/bulk_import/`.

On 2026-07-26, the PDF text was extracted locally. The latest surviving review
pages for Rise of Nationalism in Europe V13 (page 145), Arithmetic Progressions
V11 (page 146), and Electricity V9/V11 (page 147) were also rendered and
visually checked against the extraction. That corrects the earlier audit's
stale claim that the PDF was absent.

The statuses below distinguish three different kinds of evidence:

- **Contract present; live unverified**: production enforcement and focused
  regression coverage exist, but no fresh three-chapter model run has yet
  demonstrated the result.
- **Partial; live acceptance required**: deterministic guards cover objective
  failure modes, while the remaining usefulness or completeness judgment must
  be checked in freshly generated workbooks.

Passing tests prove only the encoded contracts. They do not prove that every
probabilistic model output satisfies every PDF comment.

## Latest surviving PDF requirements

The following stable IDs cover the issues still present in the latest reviewed
versions. Page numbers identify the authoritative PDF evidence.

| ID | PDF page | Latest surviving requirement | Enforcement and regression evidence | Current status |
| --- | ---: | --- | --- | --- |
| `RNE-V13-01` | 145 | Remove public container headings such as “Discuss” and “Activity” from checkpoint/example text without deleting a real student-facing imperative | Public source-heading cleanup and activity-hub normalization; heading and checkpoint fixture regressions | Contract present; live unverified |
| `RNE-V13-02` | 145 | Do not copy textbook paragraphs verbatim into concept descriptions | Terminal `verbatim_source_description` validation against source windows; validator regressions distinguish descriptions from quoted question text | Contract present; live unverified |
| `RNE-V13-03` | 145 | Keep a multipart question and its stem together, and assign each checkpoint exactly once | Parent/subpart anchors, authoritative source replacement, duplicate/missing inventory checks, and RNE fixture regressions | Contract present; live unverified |
| `RNE-V13-04` | 145 | Consolidate repetitive or overly narrow Types while keeping genuinely different methods separate | Semantic Type consolidation, topic-safe acceptance, and duplicate-Type validation | Partial; live acceptance required |
| `RNE-V13-05` | 145 | Preserve all checkpoint and exercise questions and place them under concepts that actually teach the needed method | RNE inventory-count fixtures, exact-coverage gate, source-topic constraints, and host-entailment review | Contract present; live unverified |
| `AP-V11-01` | 146 | Place each Type under the concept that entails it (for example, arithmetic mean work must not sit under constructing an AP; introductory material must not receive an nth-term Type) | Concept sufficiency, semantic host review, and terminal high-confidence host validation | Contract present; live unverified |
| `AP-V11-02` | 146 | Recover the complete AP source-question inventory and render every eligible problem exactly once, without repeated questions | AP source fixtures, exact inventory coverage, duplicate assignment detection, and terminal repair checks | Contract present; live unverified |
| `AP-V11-03` | 146 | Preserve the option set belonging to each MCQ; do not combine stale or neighboring options | Parent-question/MCQ anchoring plus canonical replacement from structured source options | Contract present; live unverified |
| `AP-V11-04` | 146 | Use reusable, meaningful Type and Case definitions; remove vague labels such as “Source inventory task,” avoid one-question taxonomy, and keep each Case semantically aligned with its Examples | Generic/raw Case rejection, duplicate-Type checks, semantic consolidation, and conservative Case/Example family checks | Partial; live acceptance required |
| `AP-V11-05` | 146 | Remove textbook section-number leakage, preserve the correct figure with the question, and reject incomplete/truncated Examples | Description reference validation, canonical image/figure checks, and full-example validation | Contract present; live unverified |
| `AP-V11-06` | 146 | Preserve a shared stem with its lettered/numbered subparts instead of creating repeated standalone concepts | Parent/subpart identity and atomic-parent regressions | Contract present; live unverified |
| `AP-V11-07` | 146 | Keep ordinary Types/Cases on normal concepts and reserve Culmination for genuine cross-concept synthesis | Explicit placement scopes, normal-vs-synthesis terminal checks, and Culmination structure regressions | Contract present; live unverified |
| `ELEC-V11-01` | 147 | Use meaningful Case names and keep the Case and Example in the same task family, including series-versus-parallel distinctions | Generic/raw Case rejection and conservative Case/Example semantic mismatch checks | Contract present; live unverified |
| `ELEC-V11-02` | 147 | Give important methods such as Ohm’s law, resistance, resistivity, and resistor combinations dedicated, useful Types when the source supports them | Concept sufficiency, method-anchor recovery, semantic host review, and mined-Type rendering | Partial; live acceptance required |
| `ELEC-V11-03` | 147 | Do not overload Culmination with textbook activities, routine derivations, or ordinary exercises; use it for synthesis | Activity-hub routing, normal-scope placement, chapter-wide relocation, and Culmination validation | Contract present; live unverified |
| `ELEC-V11-04` | 147 | Reach complete, non-duplicate exercise coverage and distribute questions semantically instead of dumping them under the last concept | Electricity inventory-count fixtures, exact-coverage/duplicate gates, semantic host review, and placement repair | Contract present; live unverified |
| `ELEC-V11-05` | 147 | Represent textbook activities in concise activity hubs rather than turning their procedures into generic Cases | Activity-origin classification, compact hub rendering, hub/example alignment, and anti-Culmination regressions | Contract present; live unverified |

## End-to-end traceability

These broader contracts preserve useful coverage from earlier review rounds.

| Review concern | Production enforcement | Regression evidence | Repository status |
| --- | --- | --- | --- |
| Learner analysis distinguishes commonly held incorrect beliefs (`Misconceptions`) from plausible application mistakes (`Error Analysis`); every normal Pre/Post concept has at least one, and both may be retained when distinct | `generation.py`, `concept_refiner.py`, and `concept_validator.py` | Learner-analysis contract, normalization, validator, and review-regression cases | Contract present; live unverified |
| Duplicate, generic, correction-shaped, or misplaced learner analysis and mastery text | `concept_refiner.py` and `concept_validator.py` | Learner-analysis and mastery cases in `test_concept_mapping_reviews.py` | Contract present; live unverified |
| Duplicate concepts, aliases such as BPT, merged descriptions, and invented topics | `concept_cleanup.py` and generation repair passes | Cleanup, similar-title, merge, and topic-safety cases | Contract present; live unverified |
| Overview, summary, and editorial matter must not become topics or leak into adjacent topics | Section parsing in `generation.py` and final filtering in `concept_cleanup.py` | Overview/summary omission and source-topic recovery cases | Contract present; live unverified |
| Type → Case → Example hierarchy retains full source questions | Inventory mining, rendering, salvage, and alignment in `generation.py` | Hierarchy, short-example, raw-task, and exact-coverage cases | Contract present; live unverified |
| Each eligible source question appears exactly once; activities stay in their hubs; parent/subpart identity survives | Inventory anchors, activity placement, exact-inventory acceptance, and terminal coverage repair in `generation.py` | Duplicate/missing assignment, activity, MCQ, parent/subpart, and repair cases | Contract present; live unverified |
| Equivalent Types consolidate without crossing topics or losing Examples | Semantic consolidation and topic-safe acceptance in `generation.py` | Consolidation, repeated-Type, and topic-drift cases in both review modules | Partial; live acceptance required |
| Cases attach only to concepts that entail the method | Concept sufficiency, semantic host review, and terminal placement validation in `generation.py` | Host-entailment, derivation, proof-anchor, and worked-example cases | Contract present; live unverified |
| Chapter-wide exercises are placed semantically, not dumped under the last topic | Chapter-wide placement and retry logic in `generation.py` | Semantic distribution and invalid-topic retry cases | Contract present; live unverified |
| Figures, formulas, and rich text remain canonical, safe, and student-facing | `katex_rules.py`, cleanup, inventory rendering, and `concept_validator.py` | KaTeX, image, Mathpix, figure, equation, and rich-text cases | Contract present; live unverified |
| Metadata, duration, subject codes, book tags, and display names survive persistence | Metadata helpers, directory service, and bulk-import reader/schema | Metadata, duration, subject-code, display-name, and bulk-import tests | Contract present; live unverified |

## Pipeline review

The coverage is end to end rather than prompt-only:

1. **Source parsing and inventory** recover headings, checkpoints, activities,
   exercises, images, MCQs, and parent/subpart anchors.
2. **Model generation** uses registered prompts; deterministic checks reject
   incomplete or structurally unsafe responses.
3. **Semantic repair** consolidates Types, checks concept sufficiency and host
   entailment, and preserves topic boundaries.
4. **Deterministic cleanup** removes review artifacts, duplicate sections,
   editorial topics, invalid formatting, and unsupported fallback text.
5. **Coverage enforcement** detects missing or duplicate assignments and repairs
   them without silently moving activities or synthesis items.
6. **Validation and persistence** enforce rich text and the canonical bulk-import
   schema before generated data is stored or exported.
7. **Durable checkpoints** preserve the source, inventory, generated rows, and
   validation diagnostics so a failed terminal pass can resume without
   regenerating earlier stages.

## Verification result

On 2026-07-26, after the current PDF-driven hardening edits, the complete
backend suite passed locally with **730 tests**. This verifies the encoded
deterministic contracts together; it does not replace the fresh production-model
samples required for semantic acceptance.

The PDF is now available and its latest review pages have been checked, but the
overall audit is **not complete**. Fresh production-model samples for Arithmetic
Progressions, Electricity, and Rise of Nationalism in Europe remain required.
Each sample must start from its checked-in MMD source, retain/resume the same
durable job after a late validation failure, export a workbook, and be inspected
against every chapter-specific ID above.

## Local HTTP and checkpoint evidence

On application commit `6dabb961f1453896022cf7b57e254e7560764e47`, all
three checked-in MMD sources were exercised through the real local HTTP health,
directory lookup, upload, conversion, generation-stream, and job-status routes
with isolated databases. Live providers were forcibly disabled. Arithmetic
Progressions normalized to 40,098 characters, Electricity to 53,529, and Rise
of Nationalism in Europe to 61,351. Every run recorded zero provider requests,
zero tokens, and zero estimated cost.

The deliberately primitive test-only dry generator does not emit production
inventories, Types, or checkpoints. The strict deposit boundary correctly
rejected its full-chapter output, so these runs are route and conversion smoke
evidence only, not semantic acceptance samples.

An API-level regression now covers the late-checkpoint failure itself. Through
the streamed generation endpoint it loads a history containing a valid
`post_type_assignment` checkpoint and an invalid `final_content_ready`
checkpoint, rejects the 98% content, durably removes only that stage, restores
the 91% stage in the same request, and confirms the surviving stage through the
job-status endpoint. An OpenAI-call sentinel proves this recovery is
provider-free.

## Residual risks and completion gate

- Live model quality remains probabilistic; a deterministic contract can reject
  a bad result but cannot prove the next result will be pedagogically useful.
- The V11 workbooks document the reviewed defects; they are not golden files
  that a new workbook should reproduce cell for cell.
- Qualitative concerns such as Type breadth, useful naming, and whether every
  important method deserves its own Type require human review in addition to
  automated validation.
- A successful resume must prove that a 90%+ checkpoint reuses persisted work
  and reaches validated persistence; retrying from scratch is not equivalent.

Do not mark the PDF audit “all covered” until:

1. the exact pushed commit retains the **730-test** result in CI;
2. fresh AP, Electricity, and RNE jobs complete from their repository MMD
   sources, with checkpoint resume exercised for any late failure;
3. the exported workbooks are checked against all stable IDs above, including
   exact source counts and duplicate detection; and
4. every **Partial; live acceptance required** row is either accepted from the
   samples or converted into a narrower executable contract and regression.
