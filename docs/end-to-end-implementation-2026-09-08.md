# Approved end-to-end audit implementation

This change implements Q35 on the verified `feed425` baseline, following the
owner's approval of the audit recommendations. The follow-up clarification
concerns exact serialized field values such as `Fill in the blanks`; category
selection and difficulty judgments remain with their existing API authorities.

## Change and verification map

| Approved finding | Implemented behavior | Regression evidence |
|---|---|---|
| 1. Image evidence | Existing authors and critics receive pinned image bytes from the relevant recorded source dependencies. Original URLs and hashes bind replay; missing evidence is explicit. | `test_assessment_visual_evidence.py`, `test_concept_evidence_preservation.py` |
| 2. Complete concept evidence | Complete named source blocks, tasks, selected references and learner analysis reach their owning calls and final refinement. Culminations see completed sibling teaching at the existing authoring stage. | `test_concept_evidence_preservation.py`, Phase 3 golden tests |
| 3. Content preservation | Repeated Description sections and malformed Types survive formatting; structural repair retains original and revised evidence. API authors/critics own the approved prose and Type/Case quality judgments. | Concept validator, refiner, Polish and preservation tests |
| 4. Evaluation contract | Joint item review and Master refinement receive adopted required elements and accepted variations separately from prior self-evaluation. Rubric prompts require exported criteria and model answers sufficient for equivalents and partial credit. | Item-review, marking and Master Refiner tests |
| 5. Complete Objective task | Final option content, media and ordering are immutable alongside the stem. Explanation prose remains editable. | Master Refiner and release-run tests |
| 6. Open/Specific precedence | Current Q26 bounded-answer interpretation is explicit; superseded registry material remains historical evidence. | Answer-restriction tests |
| 7. Source relationships | Qualified cross-page dependencies, table-cell figure ownership, source captions and public alt text remain distinct. The existing PDF verification call receives crop pixels. | `test_source_evidence_completeness.py`, PDF/source publication tests |
| 8. Direct MMD | Fresh source normalization receives an independent comparison, preserving originals, proposals and findings. Existing verified PDF and legacy checkpoint paths retain their recorded behavior. | Source evidence and chapter-reading resume tests |
| 9. Instruction authority | Source/language plans govern skeleton structure. Arbitrary opening, numbering and sentence quotas are removed. Pre prerequisites and qualitative tier guidance use consistent boundaries. | Skeleton/source-plan, Pre, profile and grouping tests |
| 10. Multipart capacity | Authors receive the fixed parent projection limit before writing. Oversized child rubrics remain complete and carry the limitation. | 36-criterion materialization fixture |
| B1. Caching | Additional shared evidence/policy prefixes opt into the existing explicit cache adapter. | Runtime and prompt transport tests |
| B2. Independent stages | Group clustering, descriptions and QA execute concurrently within each stage, then apply in stable order behind stage barriers. | Barrier-based end-to-end tests, complete sibling QA and no-call replay |
| B3. Scheduling | Pending work is bounded; nested kernel pools execute within their outer workers. | Nested worker ceiling, failure and ordered-result tests |
| B4. Response structure | Fixed advisory envelopes use a shared strict schema and matching local validation; unsupported wire modes retain compatible JSON transport. | `test_response_schemas.py` and provider wrapper tests |
| B5. Master validation | Baseline and final readback remain complete. Intermediate rendering covers affected rows while whole-release mechanical checks remain active. Failed validation or merge retains original prose and truthful rollback evidence. | Incremental/full parity, multi-member and fault-injection tests |
| B6. Resume | Successful author output is saved before criticism. Interrupted review resumes from the bound receipt after current contract checks. | `test_runtime_resume_and_attempts.py` |
| Field labels | Fresh runs carry an exact vocabulary and full format policy; legacy frozen runs retain their labels. Declared marks-to-duration tables work in both sealing and re-import. | `test_assessment_output_vocabulary.py`, profile/import/release tests |

The universal column contract, English-only functional rubric tags, actual
locked KaTeX checks and source-asset publication gates remain active. Luna,
uniform xhigh, configured review stages and 5 Basic + 5 Intermediate Pre
coverage remain unchanged. See [label history](category-group-history-2026-09-08.md)
for the exact presentation map and historical evidence.

## Q36 follow-up: expected failures and durable identifiers

The previous CI result was 3,955 passed and seven expected failures. Six tests
still assumed automatic publication, invented metadata or error-only failures;
one exposed actual reuse of the highest deleted question number. Q36 approves
fixing that defect and migrating the useful assertions to current behavior.

Two additive tables now retain each label family's issued highwater and the
content-bound reservations used by Master retries. Both generation paths reserve
complete ranges atomically. Imported questions and staged release snapshots
advance history without changing their supplied labels; startup backfills live
questions and every retained release version. Blank and legacy families remain
separate. History outlives deleted questions, concepts and releases. Numbers
absent every retained record before the migration cannot be recovered.

An identical accepted Master replay keeps its labels, frozen payload and cached
Refiner work. New accepted content reserves new numbers. Master reservations
commit in a short independent transaction before external Refiner calls; they
survive interruption without holding a write lock across those calls. Ordinary
assessment batches reserve within their publication transaction and do not
commit unrelated caller edits. Database integer exhaustion is detected before
arithmetic can round or reuse a number; oversized historical labels remain
verbatim and bootable, with further allocation in that family refused.

The six migrated tests now cover staged downloads and explicit publication,
source merging across two jobs, registered or missing duration, conversion
failure diagnostics, and certified checkpoint recovery without provider calls.
All seven expected-failure exemptions have been removed. The new allocator suite
uses independent SQLite sessions and a recreated engine to check concurrency,
rollback, restart, deletion, imports, staged history and interrupted Master
reservation replay. Existing release tests retain their exact-payload and
zero-provider-call replay assertions.

## Operational verification still required

Offline tests establish transport, evidence preservation, arithmetic, identity,
rendering, rollback and replay behavior. They do not establish that a live model
has interpreted every uploaded chapter correctly or that an external evaluator
awards appropriate partial credit from the exported workbook alone.

No provider or Fly credentials are configured in this workspace. A fresh matched
live run over the School Bell, Three-Dimensional Shapes, Electricity and RNE
sources is therefore still required for semantic acceptance, real crop/public
delivery inspection and measured cost/time comparison. Use the same source,
model, effort and coverage settings on both sides. Supplying previously missing
images and complete evidence can increase input cost, so no percentage saving
is claimed from the code changes alone.

Telemetry records request attempts, provider responses and missing usage
explicitly. Cost estimates are incomplete when usage or applicable pricing is
unknown. Detailed usage uses the existing job checkpoints; an abrupt process
kill before the next save can lose the most recent counters, although the
author receipt itself is already durable before criticism. Bounded scheduling
also needs measurement against the configured provider concurrency limit.

This implementation does not merge the production PR or deploy to Fly.
