# Aegis — working rules

**Latest owner amendment: Q49 (reviewed-file handoff and visible progress).**
Treat generation as two steps: source to Concept files, then reviewed files to
Master files. Reviewed uploads may change layout, row counts, titles and IDs,
and may remove, add, combine or reorder content. Do not require them to match
the previous staged Concept set. Upload stores the reviewed input; Step 2 uses
an independent API author, critic and existing Fixer to extract its concepts
and questions. Previous source text, inventories, routes and cached semantic
decisions are history, not Step 2 inputs. Preserve the selected chapter and
publication metadata. Pre questions absent from the reviewed file are generated
from its accepted concepts only. Fresh runs defer Pre question generation to
Step 2; historical sealed runs retain their recorded workflow. All new work uses
GPT-5.4 mini and the closed CMS vocabulary. Show saved logs after refresh, live
Step 2 progress, upload receipts/errors and accurate running/waiting/failure
states. Continue the authorized checks, merge and deployment workflow.

**Latest owner amendment: Q48 (11 September 2026, job 129).**
Repair missing Pre questions and weak concepts using the attached diagnostics.
The API may infer supported foundations from grade, subject and source task
assumptions without requiring a separate prior-curriculum record. Teach concrete
knowledge and transferable skills, not awareness that a topic was studied.
Accepted Pre concepts are the assessment scope: coverage planning must not
reopen eligibility or assign zero to drop them. Counts and tiers remain adaptive.
Current corrected prose takes authority over superseded prerequisite metadata;
archive stale derived scope instead of carrying it into new questions. Explicit
Continue/Pre retry must recover empty or partial banks with a separate mini
revision, preserving source/Post history and never recording false success.
Keep dependent subquestions together. Corrected Post grouping is an explicit
API decision with exact original identity accounting and preserved dependencies;
retain only essential learner context and keep removed excerpts in audit.
Preserve historical replay, Q44 boundaries, closed CMS lists and all independent
review/Fixer stages. Verify offline and continue the authorized CI/deploy flow.

**Latest owner amendment: Q47 (11 September 2026).**
Refine question classification using the complete required response: descriptive
work must not become Objective or Subjective simply because its final answer is
fixed, and tables of data are not answer options. Capture all supported Pre
fundamentals from the complete book evidence and carry each retained capability
through Concept and Master generation, preserving Q44's prior-learning boundary.
Resolve in-text referents before question wording is frozen; retain only the
necessary learner context in the question and keep full raw extracts in audit.
Do not restore discarded exposition or copy it into concept descriptions.
Review the whole Post chapter for duplicate capabilities and coherent Concept,
Type, Case and question teaching order, with independent API review and Fixer.
Keep complete statistical tables, headers, units, zeroes, blanks and mathematics
faithful through both workbook exports and KaTeX rendering. All semantic choices
remain API-owned. Version new run policies and preserve historical decisions;
keep the closed CMS lists, mini routing, review stages and publication contract.
Continue the already authorized push, CI, merge and deployment workflow.

**Latest owner amendment: Q46 (11 September 2026).**
The owner instructs: “Change all models to 5.4 mini.” All stages in new runs
use OpenAI `gpt-5.4-mini`, including source/visual reading, concept writing,
Pre/Post questions, independent critics, refiners, metadata and the Fixer.
Preserve existing stage reasoning efforts and every review/output contract.
New profiles cannot fall back to Luna or Gemini, and require only OpenAI
credentials. Use mini's documented 400,000-token context and 128,000-token
output maximum; never trim complete evidence to force a request to fit.
Version the model policy; preserve already sealed profiles, receipts and
replay identities. This supersedes Q40's model assignments for new work.
Continue the authorized push, CI, merge and deployment workflow. No paid
generation is required for verification.

**Q45 publishing authorization (11 September 2026).**
The owner explicitly instructs: “Please go ahead push it till deploy.” Push
the approved-vocabulary implementation to `mohithmohan07/ProjectAegis`, open
the PR, resolve any CI failures, then merge after the normal checks pass and
verify the existing main-branch Fly deployment. This supersedes the preceding
automatic push-approval block for this change. Do not bypass checks or access
controls and do not run paid generation to validate deployment.

**Latest owner amendment: Q45 (11 September 2026).**
The owner's attached `CMS clean-up: Finalised Question Categories_ Sources_
Cognitive Skills` is the closed vocabulary for newly generated question fields:
exactly 26 categories, 9 sources and 6 cognitive skills. Use only those labels,
with no abbreviations, synonyms, case changes or additional items. See
`docs/cms-approved-question-vocabulary.md` for the complete lists and provenance.
Freeze this vocabulary on new runs and give authors, reviewers and the Fixer
the same contract. Category and response-mechanism fit remain API judgments;
strict membership is a schema check, never a local classifier. Preserve source
provenance: generated questions use `UpSchool DB`, extracted questions use their
recorded approved publication. Never guess `NON - NCERT` for an unknown source.
Disallowed values cannot populate normal question fields or pass publication:
keep unresolved fields blank with visible errors and retain the original values
in evidence so downloadable artifacts remain available. Check parent and child
questions and read the rendered workbook back. Historical sealed policies and
already released artifacts retain their recorded contract. This amendment
changes none of the other output columns, model routes or review stages.

**CI follow-up (10 September 2026).**
The owner reports that CI did not pass after PR303. Resolve the failing checks,
run the affected offline tests, and wait for all normal PR checks to succeed
before merging this repair. This supersedes the earlier no-testing preference
for the CI repair. Preserve the strict current provider schema and readable HTTP
error contract; update stale test fixtures/expectations without removing their
question-identity, usage, history or retry assertions. Do not use paid generation
as a substitute for offline regression coverage.

**Flexible corrected-input follow-up (10 September 2026).**
The owner reports repeated Post upload failures for supporting context, including
unchanged workbook rows. Keep question wording quoted from the edited cell or
ordered exact spans; do not impose that single-cell quote rule on supporting
context. The v4 author explicitly inherits context, replaces it using cited
edited rows/source questions, or removes it. Context relevance and faithful
assembly remain API judgments with the existing independent advisory critic.
Validate actual evidence addresses and retain the full original/resolved receipt.
Preserve options, answers, media, tables and multipart children independently;
only explicit per-dependency removals clear them. Synchronize effective fields
with nested source_context before the Master snapshot. Preserve span proofs and
the exact target-cell digest through row rebinding and catalog synchronization.
Identical tagged Concept projections repeated across different workbook sheets
may coalesce mechanically with their locations retained; conflicting identities
remain a readable refusal. Retain the original chapter/source selection and
accepted Pre input. No test suite or paid generation is requested; the existing
merge/deployment authorization continues. See the Q44 follow-up below.

**Corrected-workbook follow-up (10 September 2026).**
The owner's job 124 attachments ending `(1)` are the edited Concept files;
the other two files are original outputs. Accept explicitly added Pre concepts
even when the staged Pre capture was empty, with fresh internal identities and
recorded reviewer provenance. Preserve edited Post question wording from its
Concept Details/Types/Cases rather than requiring separate question columns.
Semantic question membership and grouping remain model decisions; quote
transport may only reconstruct exact source spans or reversible display
representations. Keep original releases and evidence immutable. An explicit
new Pre correction adopts Q44 for its new Pre question/Master revision;
historical sealed source and Post decisions are unchanged. Preserve pending
corrections through unchanged-file acceptance and identical reuploads. See the
Q44 corrected-workbook follow-up in docs/aegis-restructure.md.
The subsequent Post-upload HTTP 500 repair keeps the quote-span field required
on the strict provider schema (an empty list represents a contiguous quote),
surfaces request failures without raw provider details, and preserves accepted
upload acknowledgements when a later status refresh fails. It changes no
question membership, model routing or review stages.

**Latest owner amendment: Q44 (10 September 2026).**
Grade 01 Pre-Learning may include a small set of relevant, familiar literacy
foundations such as initial capitals, basic punctuation and everyday vocabulary.
These are candidates for API judgment, never compulsory topics or a generic
bank. Keep learner-facing concepts, mastery, questions, answers and rubrics
minimal and within supported prior learning. Prefer short, single-demand
recognition, choosing the correct alternative or a tiny familiar response;
do not inflate the task with formal grammar, long explanations, multi-step
work or higher-grade knowledge. Difficulty tiers and question counts must not
force extra complexity or coverage. The independent reviewers apply the same
boundary through Concept and Master generation, including corrected Excel
inputs. Empty-capture review must distinguish no eligible teachable prerequisite
from an actual missed eligible fundamental; generic literacy or following a
worksheet direction alone does not establish a missing Pre concept. New chapter
teaching stays in Post. Freeze this additive policy on new envelopes and carry
it into staged Pre releases and Master metadata; historical sealed decisions
retain their recorded policy. Q40 routing and Q41 review/resume/accounting stay
in effect. See Q44 in docs/aegis-restructure.md.

**Previous owner amendment: Q43 (9 September 2026).**
The final follow-up requires Copy to remain visible beside the console title,
and corrects the supplied Grade 01 NCF Maths pairs: unit Time and Measurement
contains chapter Measurement; unit Playing with Numbers contains chapter
Numbers Beyond 20. Apply these explicit corrections to catalogue imports and
existing rows without losing their attached content. The owner explicitly
requests no further testing and immediate PR update/merge for this follow-up;
that supersedes the local testing gate below without bypassing repository
access controls.
The owner requests a collapsible usage box in the log column so the logs have
room, plus separate GPT, Gemini and total estimated charges in rupees. Keep
the compact cost breakdown visible when the detailed metrics are folded.
Use recorded provider charges and request-specific exchange rates; preserve
pending, missing-usage and unavailable-conversion states. Provider subtotals
remain cumulative through Concept review, reupload, retries and Master resume,
including compact live events. Historical unattributed charges stay explicit.
This changes presentation and accounting attribution, not model routing or
content policies. The existing authorized verification and merge workflow
continues; no separate manual Fly action is requested.

**Previous owner amendment: Q42 (9 September 2026).**
The owner explicitly requests fixing and merging the missing NCF chapter
catalogue and frontend code visibility. Bundle the supplied 18-chapter Grades
1–3 workbook, expose Board `NCF` and publication `Seed to Plant`, and preserve
the existing catalogue. Display readable chapter/unit/checkpoint labels while
retaining machine identities for matching, APIs and workbook contracts.
The publication selected before a run remains Concept Source and extracted
Post-learning Question Source; generated Pre-learning Question Source is
`UpSchool DB`. This change does not alter pipeline/model policies or existing
run provenance. Implement, verify and merge; the existing main-branch workflow
handles deployment without a separate manual Fly action.

**Previous owner amendment: Q41 (9 September 2026).**
[Q41](docs/aegis-restructure.md#q41--decided--complete-question-membership-response-mechanism-classification-and-concept-review-before-masters)
requires every source-set task, including in-text and activity/info-hub prompts,
to enter the semantic question inventory with its necessary source context and
media. Numbering and an umbrella instruction do not make independent questions
multipart. Only a meaningful shared context with dependent questions forms a
single multipart unit; models decide those boundaries, with advisory review.
Apply the owner's attached classification SOP by response mechanism: Objective
selects explicit answer options; Subjective supplies a short fixed response;
Descriptive constructs reasoning, calculation, explanation, drawing, mapping,
or an extended response. True/False remains Subjective. See
`docs/question-classification-sop.md`. New runs produce complete Pre/Post
Concept files first, pause for optional corrected-workbook uploads, and build
Masters only when the reviewer continues. This authorized review boundary
supersedes older unattended-through-Master language; it removes no model stage.
The owner's follow-up explicitly permits the reviewer to omit, add or move
Post questions in the Concept file's Types/Cases. The reviewed question set
is authoritative for the Post Master: preserve exactly that set, including
manual additions and omissions with revision provenance; the API must not
invent additional Post questions or restore deliberately removed ones. Pre
questions continue to be generated automatically under Q40. Replace the old
Review & Edit section with the download/corrected-file/continue workflow.
Both Pre and Post accept their same downloaded Concept Excel after local edits;
neither requires a separate correction template. Edited Pre concepts govern
the prerequisite bank generated under Q40.
Corrections are staged inputs, not CMS publication. Preserve the same run's
logs, all attempts/cost receipts, monotonic progress, and cumulative active
processing time across review, reupload and resume; distinguish review waiting
time. Keep immutable source evidence, exact-once reviewed-question provenance, historical
sealed replay and all Q40 model policies. The owner's latest follow-up
explicitly authorizes pushing and merging after implementation and validation,
superseding the prior push-approval block and merge hold. No separate manual
Fly deployment or restart is requested.

**Previous owner amendment: Q40 (9 September 2026).**
[Q40](docs/aegis-restructure.md#q40--decided--stage-model-routing-adaptive-pre-coverage-and-inr-cost-logs)
requires Gemini `gemini-3.8-flash` only for Pre question authoring, Luna
`gpt-5.6-luna` at `xhigh` for concept writing, and GPT-5.4 mini/Luna according
to stage and complete-context capacity elsewhere. Preserve every review stage.
The fixed 5 Basic + 5 Intermediate rule is superseded for new envelopes by an
API-authored contextual coverage plan; no padding, tier balancing or extra
prerequisite teaching. Freeze model/coverage policies for new work, retain
historical sealed decisions and recorded policies. Topic names identify their
actual content/work. Preserve concept/question-owned image URL tags and the
complete bordered KaTeX table form. Logs show individual and cumulative
estimated charges in INR, including Gemini, retries and resumed history, with
recorded exchange-rate provenance and honest missing-usage states. Production
API credentials already exist according to the owner; read-only Fly access is
offered for verification. No merge, deployment or restart is authorized. A
prior automatic approval review rejected the GitHub push; do not retry without
explicit authorization. See `docs/model-routing-review-2026-09-09.md`.

**Previous owner amendment: Q39 (9 September 2026).**
[Q39](docs/aegis-restructure.md#q39--decided--meaningful-topics-complete-source-coverage-and-faithful-task-polishing)
requires meaningful, source-backed topic/concept ownership without generic
Introduction/Summary/Exercises buckets or content loss. Short lower-grade
sections retain independent capabilities. English ends with source-supported
whole-work Detailed Analysis, with questions allocated to their actual lens.
Post questions are source-only; the existing upstream polish may faithfully
adapt boxes/ticks/grids while preserving every demand and visual. Preserve raw
source and freeze the accepted polished wording before clustering; the Master
Refiner's question freeze remains. Pre contains only necessary, supported prior
learning; unsupported candidates have explicit dispositions, not extra teaching.
New policies are versioned for fresh envelopes/items, preserving historical
replay. Keep PR #297 a draft with the existing deployment hold. See
`docs/source-topic-review-2026-09-09.md` for the supplied-source findings.

**Previous owner amendment: Q38 (9 September 2026).**
[Q38](docs/aegis-restructure.md#q38--decided--question-owned-tables-visible-images-and-readable-excel)
requires question-owned tables to appear with their question as a complete
KaTeX array under the existing rules, or one complete faithful image where
needed. Preserve structured source and multipart-child evidence through the
author payload, and preserve accepted images through Concept refinement and
cleanup. Do not flatten tables into coordinate prose or silently strip images.
Make Excel cells readable with wrapping, field widths and native display line
breaks paired with the required `<br>` import markers outside math. Preserve
complete KaTeX spans and declared Equation cells byte-for-byte. A paired prose
break imports as one logical break; all schema fields remain.
Keep PR #297 a draft: no merge, deployment, restart or provider change while
the owner's files are running.

**Previous owner amendment: Q37 (9 September 2026).**
[Q37](docs/aegis-restructure.md#q37--decided--run-scheduling-live-accounting-and-complete-pre-learning-capture)
approves fixing the observed Master serialization, independent source batching,
live cost visibility and Pre-learning evidence/splitting defects before any
Gemini or reasoning-effort experiment. Preserve Luna/uniform-xhigh, every review
stage, the 5+5 Pre question rule and API-owned semantic judgments. New sealed
Phase 3 envelopes carry the improved Pre policy; reused envelopes retain their
recorded policy and decisions. The owner explicitly forbids deployment while
the current files run: prepare and verify a PR only; do not merge to main,
deploy, restart the app or change its provider settings.

**Previous owner amendment: Q36 (9 September 2026).**
[Q36](docs/aegis-restructure.md#q36--decided--durable-question-numbering-and-current-workflow-tests)
approves fixing question-number reuse with durable atomic reservations and
migrating the six stale expected-failure tests to staged generation and explicit
publication. Preserve useful regression assertions and identical-run replay;
target zero expected failures. This is identifier bookkeeping, separate from
Q35's exact category/group field values and API-owned semantic judgments.

**Previous owner amendment: Q35 (8 September 2026).**
[Q35](docs/aegis-restructure.md#q35--decided--complete-evidence-efficient-execution-and-exact-output-vocabulary)
approves the end-to-end audit recommendations: complete source/visual evidence,
lossless formatting and API-owned semantic repair, consistent grading contracts,
and execution/cache/schema/resume/accounting improvements. The additional
Description/copied-prose/Type/Case meaning-heuristic removals identified in that
audit are now authorized. Keep exact schema, identity, arithmetic and rendering
gates, all existing review stages, Luna/xhigh and the 5+5 Pre coverage.
The owner's clarification about deterministic category/group columns concerns
their **exact output labels**, such as `Fill in the blanks` and
`Basic`/`Intermediate`/`Advanced`. It does not authorize changing how a question's
category or difficulty is judged. Carry a versioned canonical output vocabulary
for new runs; preserve the vocabulary and policies frozen into existing runs.
Other stage removals, model/effort reductions and production deployment are not
implied by this implementation amendment. Live benchmark claims require the
configured provider/Fly access and recorded matching runs.

**Previous amendment: Q34 (8 September 2026).**
[Q34](docs/aegis-restructure.md#q34--decided--api-owned-learner-analysis-and-mastery-quality)
records the owner's approval to remove learner-analysis wording/overlap
heuristics and mastery-substance length thresholds, including mirrored
normalizer behavior. Existing API authors and independent advisory critics
own those judgments for every subject and both learning lanes. Preserve
authored content, kind and review evidence through mechanical formatting;
retain exact schema, identity, rich-text and scoring checks. This specific
removal is authorized; other proposed removals still need owner approval.

Read Rule 0 with
[Q33](docs/aegis-restructure.md#q33--decided--universal-column-rules-and-owner-review-before-step-removal)
and the [column-spec review](docs/column-spec-review-2026-09-08.md).
The two example subjects define the shared output format for **all
subjects**. For new policy-bound runs, keyword cells use comma-space,
Objective explanations include the correct lowercase option label and
exact answer, and criterion weights permit positive multiples of 0.5.
Only functional rubric tags are English-specific: the registry is
`content/language/creativity/evidence`, permitted solely in textual English
Descriptive criteria. Every other subject writes untagged criteria. Keyboard
need is an API decision from the requested response in every subject;
Objective retains its blank keyboard cell. Generated question source is
`UpSchool DB`, and valid Objective/Subjective answers are `Specific`. Multipart
workbooks show the parent rubric as the ordered, non-additive copy of child
criteria required by contract §24. Fresh
generation keeps update flags `No`; an existing-entity update is a separate
explicit workflow. The current policy is `owner-column-spec-2026-09-08-v2`;
persisted v1 and legacy profiles keep their carried policy. Q32's initial
subject limits, `creative` spelling and English keyboard default are
superseded for new runs. **Run every proposed pipeline-step removal by the
owner before removing it.** Audit and propose concrete changes first; this
instruction does not authorize deleting a stage. The
review named contradictory identity/placeholder/weight examples; Q35 now
ratifies their documented interpretations. These amendments take precedence over the older field
defaults listed below; Rule 1 and the verbatim master-contract file stand.

## Rule 0: The Master Governing Contract v2.0 is the output specification

`docs/aegis-master-governing-contract-v2.md` (document ID
AEGIS-MGC-2.0-20260904, adopted 2026-09-04, register entry Q26 in
docs/aegis-restructure.md) is the binding specification for what every run
produces: the four outputs, their schema (update-aware 72/440/149 columns
per register Q27, the owner's CMS template of 2026-09-04,
every `is_update_*` cell exact `No`, ` | ` list delimiter, `<br>` line
breaks), lane routing (True/False on Subjective, label-free Objective
explanations, identical Descriptive model answers), rubric rules (0.5 or 1
per criterion; English-only bracket tags), identity grammar, KaTeX/asset
rules and the release gates. Where an older register entry, SOP, prompt,
validator or sample disagrees, the contract governs and the conflict is
recorded in the register — never blended, never silently defaulted. The
contract is board-, publication- and grade-agnostic: a board-specific
category/marks/duration profile is an explicit, versioned layer on top of it,
never a precondition for any contract behaviour.

The contract's semantic boundary (§37) and Rule 1 below are the same rule.
Its fail-closed release stance applies to the RELEASE (the database write and
the "done" verdict): a run still completes and stages every artefact with its
blockers named (Q13); what it may not do is publish or call itself done while
a blocker stands.

## Rule 1: No deterministic judgment. Ever.

**Every decision that requires judgment goes through the model. Not a rule, not
a regex, not a threshold, not a keyword list.**

This is not a preference to weigh against others. It is the constraint the
codebase is built on, and it is not negotiable when writing new code here.

Concretely, do NOT introduce:

* regexes or keyword vocabularies that classify content
  (`_HEADING_ONLY_RE`, "is this a cue?", "is this filler?")
* numeric thresholds that decide meaning
  (min chars, min concepts per topic, coverage ratios, "too short to be real")
* volume-derived structure — topic counts, concept counts, or question counts
  scaled from character/token length, chunk count, or page count
* shape-matching heuristics standing in for "what does the book mean here"

Instead: give the model the source evidence and the question, take its verdict,
and — following this codebase's existing pattern — have an **independent second
pass verify** before anything is dropped, merged, or rewritten (its dissent is
an advisory review flag, never a gate — Q10). When the model does not
positively decide mid-run, the block goes to **The Fixer** (§8.2 of
docs/aegis-restructure.md, decided by Q13 amending Q7 in the §12 register):
one recorded, flagged, content-addressed best-judgment decision with the full
context of the block, and the run completes. Nothing is ever guessed
*silently*, nothing is lost, finished work always ships. Only the pre-spend
source-integrity pauses (source review, source-topic recovery, Type
granularity) and genuine impossibility — source unreadable, provider down,
quota exhausted, a decision that cannot be made mechanically applicable — may
stop a run. One recorded extension of that list (Q24, §12 register,
2026-08-29): an unattended run that could settle a rich-text source pending
only with the measured dead-end `carry_forward` fails fast with the named
remedy (reconvert the PDF) instead of spending into a downstream refusal —
the source, as converted, is unusable, which is the impossibility clause
applied honestly. Silently losing a learner's question is never recoverable.

### Why

Textbooks vary too much across boards, subjects, and grades for shape-matching
to read them. Every deterministic shortcut that has gone into this pipeline has
eventually mis-read a real book:

* a bold-vs-heading rule hid every question from generation (24 → 0, PR 208/211)
* `topic_count * MIN_PER_TOPIC` pressured thin lower-grade chapters into
  inventing concepts to satisfy arithmetic
* re-deriving topics from headings loses topics the outline already decided
* "too short to be a real task" cannot tell a mangled question from a
  section banner — only the source can

Lower grades are where this bites hardest: less content per topic, so anything
keyed to volume systematically under-serves exactly the students the chapter is
written for.

### What is still allowed to be deterministic

Mechanics, not meaning: parsing, ID assignment, caching, ordering, atomic
writes, schema validation, and **gates that refuse to accept a broken
artifact**. A strict check that detects a defect is fine — it makes no
judgment about the content, it just declines to guess. But mid-run a detected
defect routes to The Fixer's recorded decision rather than halting (Q13);
a gate may only stop the run outright at the pre-spend pauses or on genuine
impossibility. What must not be deterministic is the decision about what the
source *means*.
