# Concept review and complete question capture — 9 September 2026

> **Superseded in part by Q51 (11 September 2026).** New runs freeze workflow
> policy v2: Step 01 extracts questions as is (no polishing before the Concept
> files), Step 02 polishes the reviewed Post questions and generates Pre
> questions, and Step 03 publishes reviewed Master files. The "Gemini-only
> authoring" sentence below records the Q40 era; Q50 routes every stage to
> GPT-5.6 Luna. See `docs/three-step-workflow-review-2026-09-11.md`.

Q41 builds on the Q39 source-preservation and Q40 model/accounting changes.
The owner's follow-ups authorize reviewed Post question additions, omissions
and moves; uploads of the same edited Pre/Post Concept Excel files; removal
of the old Review & Edit interface; and merge after validation.

## Acceptance scope

| Requirement | Required behavior |
|---|---|
| Initial source capture | Account for every actual learner task across exercises, text, activities and info hubs. Preserve context, media and original wording. Do not turn informational prose or literary dialogue into invented tasks. |
| Independent questions | An umbrella instruction and lettered items do not establish multipart identity. Give independent questions separate identities. |
| Shared-context questions | Preserve a necessary passage, scenario, table or visual and all dependent children as a whole task. |
| Classification | Apply the supplied SOP by response mechanism, including Subjective True/False, Descriptive calculation, and Objective multiple selection. |
| Concept stage | Produce complete Pre/Post Concept workbooks before Master authoring and pause for review. |
| Edited inputs | Accept the same downloaded Excel file after edits, separately for both Pre and Post. Retain generated inputs when no correction is needed. |
| Reviewed Post bank | The accepted Types/Cases questions define the exact Master bank, including deliberate reviewer additions, omissions and moves. Preserve original evidence and revision dispositions. Do not generate extra Post questions or restore removed ones. |
| Pre bank | Generate prerequisite questions automatically from accepted Pre concepts under Gemini-only authoring and adaptive coverage. Do not reuse a bank tied to superseded concepts. |
| Interface | Replace the old Review & Edit section with clear downloads, lane-specific Excel uploads, accepted-input state and Master continuation. |
| One run | Preserve logs, request receipts, cumulative INR costs, processing time and progress through review and restart. Show review waiting separately and avoid premature 100%. |
| Existing requirements | Preserve meaningful topics, detailed English work naming, lower-grade atomisation, context-bounded Pre, concept/question image URLs, complete KaTeX tables, and configured stage models. |

## Evidence boundaries

The attached classification SOP was read locally, including a rendered check
of its decision table and exceptions. Its fingerprint and interpretation are
recorded in Q41 and `question-classification-sop.md`.

The earlier source review covers the supplied eighteen lower-grade PDFs;
`source-topic-review-2026-09-09.md` records concrete embedded questions,
activity formats, visual dependencies and substantive introductory material.
The Q40 report records prior model routing, currency and image/table transport
verification. No live image-delivery verification is claimed: the owner chose
to skip Fly access.

## Implemented handoff

New upload generation stages both Concept files and pauses at 70% of the same
run. The interface provides a download and corrected Excel upload for each
lane. Selecting Generate Master Files explicitly accepts unchanged inputs as
well as uploaded corrections. The old Review & Edit page is removed from the
frontend; historical releases retain their compatible publication path.

For the canonical three-sheet Post Concept workbook, a model reads the full
edited Concept Details and original question bank. It determines question
boundaries, retained identities, additions, omissions and Concept/Type/Case
placement. An independent critic records advisory findings. Mechanical checks
require exact quotations from the uploaded cells, valid original identities,
and a disposition for every original question and edited row. Neither row
position nor a regex decides which source question an edited example represents.
The author/critic receipt is retained with the immutable review revision,
including a deliberate empty question set. Exact unchanged cells do not incur
this reconciliation call. Pre Concept edits feed automatic prerequisite
question authoring instead of this Post question-bank reconciliation.

The same durable run retains its journal sequence, provider receipts and
individual/cumulative INR estimates. Processing, reviewer waiting and total
wall time are separate. Master processing occupies the remaining progress
allocation, and completion reaches 100% only when all four outputs are ready.

## Validation

The integration checks exercise canonical edited-cell identity, explicit
omission, reviewer additions, moved questions, exact KaTeX/image/multipart
content, the independent critic receipt and unchanged-file behavior. Broader
checks cover the supplied SOP, Objective multiple selection through workbook
read-back and marking, embedded question evidence, legacy sealed replay,
cumulative run state and the frontend handoff. Final suite results are recorded
with the merge evidence once the integrated checks finish.

Provider behavior is verified through controlled API responses and existing
production transport contracts. No new paid production generation or live Fly
image check is claimed by these tests.
