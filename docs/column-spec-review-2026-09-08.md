# Owner column specifications and API prompt review — 8 September 2026

**Current rule: Q33, universal policy v2.** The owner clarified that the
English and Mathematics workbooks demonstrate the output format for **all
subjects**. Only functional rubric tags are English-only; the current
registry is `content/language/creativity/evidence`. The initial Q32
interpretation that limited some formats to English or Mathematics, used
`creative`, and fixed English keyboard mode to `No` is superseded for new
runs. Frozen v1 releases preserve their recorded policy. Original file
hashes and cell references below remain the workbook evidence.

The two supplied workbooks amend the output contract on the fields where
their completed **YOUR RULE** cells give a clear instruction. This change
implements those instructions in prompts, versioned run policy, workbook
projection and mechanical validation. It also improves the distinction
between teaching prose, learner answers, evaluator rubrics and review
findings. It does not assert that a live chapter has demonstrated better
writing yet.

The current ruling is recorded as
[Q33](aegis-restructure.md#q33--decided--universal-column-rules-and-owner-review-before-step-removal);
[Q32](aegis-restructure.md#q32--decided--the-8-september-owner-column-rules-and-prompt-refinement)
retains the initial implementation record. The master contract remains
verbatim; the register states the amendments instead of blending conflicting
rules. **Every proposed pipeline-step removal must be reviewed and approved
by the owner before implementation.** No stage removal is authorized here.

The later [Q34 approval](aegis-restructure.md#q34--decided--api-owned-learner-analysis-and-mastery-quality)
authorizes the specific learner-analysis wording/overlap and mastery-length
heuristic removal. It strengthens existing semantic reviews without changing
the universal column policy or the English-only rubric-tag exception.

## Evidence and scope

| Evidence | SHA-256 of the uploaded file |
| --- | --- |
| `English_Aegis_column_spec_fill_in.xlsx` | `39e5546f03bfa65ddc4042d7316dedf137a6e79dd0eb593c39779439a4faefdc` |
| `Math_Aegis_column_spec_fill_in.xlsx` | `b393f1941fbd0abeac4aef9a054e4bbb448d905d5e43070e0e0a9a910bc0c87e` |

References below use the exact sheet name and Excel cell address. Column
F contains the owner's rule; G contains an example. Column E describes
the previous implementation. The demonstration row `1 Chapter-Topic-Concept!2`
is explicitly labelled `EXAMPLE (delete me)` and is excluded from the
owner's decisions. Column H is unfilled on the substantive rule rows; it
does not authorize moving semantic decisions into code.

The current policy supplies common column formats for every subject.
Explicit run metadata selects only the English rubric-tag exception.
Examples in either workbook never change the run's subject or supply its
chapter facts. Existing explicit category/marks/duration profiles still
apply; a format example does not select a different curriculum profile.
Content and keyboard requirements follow the actual task through API
authoring and independent review.

The four deliverables retain their current order: **01 Pre Concept, 02 Pre
Master, 03 Post Concept, 04 Post Master**. Registered output schemas stay
at 72 Objective, 440 Descriptive and 149 Subjective columns. The README
counts of 72/458/150 describe the uploaded raw template's repeated header
cells, already reconciled by Q27; they are not a new schema migration.

## Explicit changes

| Field or writing rule | Owner evidence | Implementation and limit |
| --- | --- | --- |
| Keywords | English `1 Chapter-Topic-Concept!F24`; universal scope clarified by Q33 | In every subject, the model chooses 3–6 short terms actually taught, in textual order. Workbook keyword cells use comma-space. Internal lists and relationship rosters retain pipes. Count and relevance are writing instructions, not semantic length gates. |
| Objective explanation | English `3 Answer blocks!F7:G7`; universal scope clarified by Q33 | Begins with the correct lowercase option label and exact answer content, then the supported rationale. This replaces the label-free rule for every new subject run. |
| English rubric tags | English `3 Answer blocks!F19`, `F26`; Q33's latest spelling | Exactly `content`, `language`, `creativity`, `evidence`, using `[tag]: criterion`. The workbook's original `creative` spelling remains recorded in Q32; Q33 supersedes it for new English runs. Tags remain forbidden outside textual English criteria, including Equation/Image cells and every other subject. |
| Descriptive criterion weights | Both `3 Answer blocks!F18`, `F25`; universal scope clarified by Q33 | Positive multiples of 0.5 are allowed, including 1.5 and 2, with exact parent/child totals in every subject. Numeric cells and blank unused slots remain mandatory. Independently creditable demands cannot be bundled into an omnibus criterion; a coherent criterion may exceed one mark. |
| Authored question source | Both `2 Group-Question!F16` | Generated items use exact `UpSchool DB`; source-drawn items use the run publication. Concept source and chapter publication remain the run publication. This amends Q27 for generated question source only. |
| Answer restriction | Both `2 Group-Question!F20` | Objective and valid Subjective rows are `Specific`; the model still determines whether a Descriptive task is Open or Specific. An unbounded task must be routed correctly, not rewritten to conceal a routing problem. |
| Keyboard | English `2 Group-Question!F25`; Math same cell; Q33 clarification | In every subject, the API author determines actual response need and the critic checks it. Objective remains blank; Subjective/Descriptive use exact `Yes` or `No`. The prior blanket English `No` interpretation does not apply to new runs because the clarified subject exception is rubric tags. |
| Description quality | English `1 Chapter-Topic-Concept!F10`, `F18`, `F23`; Math same cells; Q33 scope | Chapter prose is original and connected: 3–5 chapter sentences and 2–4 topic sentences guide writing in every subject. Concept Description teaches the idea and Achieving Mastery names an observable capability. Optional material is supplied by its owning stage. No invented learner analysis or copied example storyline. |
| Group description | Both `2 Group-Question!F6` | One original evaluator-facing sentence names the exact capability assessed and how the questions assess it. It should explain their assessed similarity, not repeat the concept description. |
| Multipart rubric projection | Contract §24; both `3 Answer blocks!F18`, `F23`, `F25` | Restores the required parent rubric as the ordered union of all child criteria. The internal child-scoring authority remains single; the workbook shows equivalent, non-additive parent/child views. Readback and import verify the equivalence rather than counting both views as marks. Frozen legacy profiles retain their prior projection. A union exceeding the registered 30 parent slots blocks readiness; child content and the complete omitted projection remain reviewable rather than being truncated. |
| Answer and rubric separation | Both `3 Answer blocks!F16:F20`; English `F14` | A model answer explains the solution to the learner. Each rubric criterion describes observable credit-bearing evidence for the evaluator. Descriptive answer/explanation equivalence remains required. Subjective explanations begin with the accepted answer. |
| Response discipline | Owner request to refine every API output; field-specific rows throughout both workbooks | Changed prompt families state exact schema ownership, real JSON number types, evidence boundaries, completeness and uncertainty handling. Critics identify a concrete field, defect and evidence independently. Examples never supply facts or extra response fields. |

`backend/app/services/column_spec.py` owns the versioned rules, labelled
`owner-column-spec-2026-09-08-v2`. Newly resolved assessment profiles carry a
policy snapshot. Persisted v1 profiles retain their exact snapshot,
including their old subject scope, `creative` tag and keyboard policy.
A persisted resolved profile without this policy remains
under its previous contract, so replay does not silently reinterpret an old
release. Changed assessment prompts have new policy versions; Phase 3's
shared prompt text also changes its content-addressed prompt identity.

## Rules retained

- Q27's source Post questions remain verbatim, subject to its permitted
  apparatus, context, notation and blank projections. The Master Refiner
  cannot edit `question` or `question_text`.
- Q29 binds Pre outputs to their source run. Q30 still requires five Basic
  and five Intermediate questions per Pre concept and no Advanced Pre
  questions. A workbook's Advanced group example does not reverse that.
- Q31 quality defaults remain: uniform `xhigh`, all concept refiners,
  Master Refiner, all stage critics, joint item review and three bounded
  correction attempts.
- Source identity, accepted category/marks/duration profiles, exact list
  membership, numeric cells, contiguous slots, question/group ownership
  and exact arithmetic remain enforced. No new category set or duration
  matrix was supplied.
- The author and independent critic make semantic decisions. Code only
  checks formats, existing identities, arithmetic and structural
  invariants. Review flags remain visible; artefacts still ship when a
  release blocker prevents database publication.
- Fresh generation emits `No` update flags. The workbooks explicitly allow
  `Yes` when updating existing entities (both `1 Chapter-Topic-Concept!F5`,
  `F13`, `F21`; `2 Group-Question!F4`, `F13`). This change does not create an
  update-authoring workflow or infer existing entity identities. Such a
  workflow needs explicit target IDs and the intended changed fields.
- The existing minimum of two criteria for a four-mark single-part
  Descriptive answer remains. Allowing larger half-step weights does not
  explicitly remove that separate safeguard.

## Conflicts requiring an owner ruling

These fields remain on the current canonical behavior until their intended
representation is clear. They are not counted as implemented amendments.

| Decision | Exact evidence | Conflict and current behavior | Suggested resolution |
| --- | --- | --- | --- |
| English topic ID | `1 Chapter-Topic-Concept!F12:G12`, `F8:G9`, compared with `F20:G20` and `G16` | Topic rules/examples put the shared ChapterBaseID in every topic title; concept examples still use topic-scoped `_T04_C01`. Current topic titles retain a unique `_TNN` ID. | Retain `_TNN` in the ID inside every topic title and use those exact titles in both rosters. If the chapter base alone is intentional, define a separate unique topic identity first. |
| Math topic title and display | `1 Chapter-Topic-Concept!F12:G12`, `F14:G14` | F12 describes a chapter/phase code, G12 includes `_T01`; F14 includes `Topic 01:`, and G14 additionally includes an ID. Current title contains its unique TopicID; display is the plain topic name. | Keep the same title/display distinction as English F14: decorated title, plain display name. |
| Math concept ID | `1 Chapter-Topic-Concept!F20:G20` | Written grammar is `<TopicID>_T##_<TopicNameInCamelCase>`, while its example ends `_T02_C04`. Current concepts retain `<TopicID>_CNN`. | Confirm the example/current `_CNN` grammar. A changed identity grammar needs an explicit migration. |
| Math placeholder cell | `3 Answer blocks!F13:G13`; compare English `F13:G13` and both `2 Group-Question!F22:F23` | Math describes a `$$…$$` dummy variable and gives an entire question as the placeholder example. English explicitly requires bare `a`. Current placeholder cell is `a`; the stem carries `$$a$$`; learner text carries `____`. | Use the English separation for both subjects. |
| Math Objective weights above one mark | `3 Answer blocks!F6`; `2 Group-Question!F24` | Correct option weight is stated as 1, but every weight must sum exactly to item marks. Both can hold only for a one-mark Objective item. Current correct weight equals marks; distractors are 0. | Confirm either all Math Objective items are one mark or the correct option weight equals the accepted profile's marks. The existing one-mark Math profile already satisfies both. |
| Math post-topic roster wording | `1 Chapter-Topic-Concept!B9`, `F9`, `G9` | The `post_topics` row says “pre topics,” but its example is Post. Current Post roster remains Post. | Correct F9 to say Post topics. |

Example formatting also needs cleanup, without changing the explicit
rules: English `3 Answer blocks!G16/G20` has doubled quotes around a Drive
viewer link; English `2 Group-Question!G23` uses uppercase option labels
despite F23's lowercase rule; Math `1 Chapter-Topic-Concept!G23` includes
old signed asset URLs and mixed section separators. Examples from other
chapters appear in both workbooks. The API receives the intended format
rules and actual run evidence, never those stale URLs or unrelated facts.

## API prompt-family inventory

This inventory distinguishes changes from review. Stage response schemas
and field ownership stay local to each API; a shared writing standard does
not give a refiner permission to alter another stage's fields.

| Family / code surface | Review disposition |
| --- | --- |
| Phase 3 topology, grounding, inventory/allotment and all critics (`services/phase3/prompts.py`) | Shared exact-schema, evidence and uncertainty instructions apply to every system prompt. Semantic verdicts remain model decisions. |
| Pre capture/merge/map/needed-for, empty-Pre review and Pre question planning/authoring/critic (same module) | Shared response discipline; teaching guidance on Pre mapping. Retains run binding and 5 Basic + 5 Intermediate per-concept plan. |
| Hosting, Type ownership, placement, Polish, Fixer and final Refiner/critic (same module) | Shared discipline; substantive teaching guidance for Polish/Refiner. Optional learner analysis is not invented to fill a section; protected field whitelists stand. |
| Chapter/topic metadata (`services/generation.py`) | Original prose, required chapter/topic roles, universal topic 2–4 sentence guidance, no example chapter leakage. Remaining legacy/auxiliary surfaces are covered by the separate full prompt audit below. |
| Routing and Pre-claim review (`assessment_routing.py`, `assessment_prelearning_claim.py`) | Exact role/schema and evidence ownership, independent concrete criticism. |
| Level, variants, group description, dedup and touched-group QA (`assessment_grouping.py`, `assessment_dedup.py`, `assessment_quality.py`) | Shared response and review discipline; precise assessed-capability group prose. |
| Cell definition and answer restriction (`assessment_cells.py`, `assessment_answer_restriction.py`) | Removes impossible instructions to split a one-cell result; preserves generated tier ownership; closed answers use Specific without hiding a misrouted open task. |
| Materialization (`assessment_materialization.py`) | Universal carried policy, complete learner rendering, labelled explanation prefix, typed values, accepted answers, model answer/rubric separation and arithmetic. |
| Marking and joint review (`assessment_marking.py`, `assessment_item_review.py`) | English-only tags, universal positive half-step weights, exact totals and response-dependent keyboard choice; independent whole-item review. |
| Master refinement (`assessment_master_refiner.py`) | Clarifies permitted answer/rubric changes and protected source question fields; uses the same carried column policy. |
| Source normalization, Phase 2 task inventory/adjudication, PDF extraction and chapter outline | Reviewed. Existing strict schemas, verbatim evidence, complete task/visual accounting and independent verification already serve these transcription stages; CMS formatting is applied downstream. |
| Legacy assessment builder (`assessment_prompts.py`, `generation.py`) | Corrected Subjective placeholders and bounded answers, complete rich question text, numeric examples, metadata-derived policy, source labels and non-additive multipart rubric instructions. |
| Create Workbooks (`subject_prompts.py`, `gpt_writer.py`) | All four API calls receive schema/evidence discipline and supplied board/publication. Removed assumed NCERT/General Science context and filler quotas while retaining renderer limits. Plan/resume caches now key the effective prompts, source, metadata, model policy and plan; old content is retained. |

## Suggestions and verification

1. **Resolve the six representation conflicts above.** The proposed
   resolutions preserve stable IDs and make the two workbook examples
   internally consistent.
2. **Review representative chapters across the supplied subjects before deployment.**
   Compare the same source and profile before/after, inspecting all four
   outputs for correctness, teaching prose, model answers, rubric coverage
   and total provider cost. Dry tests prove mechanics; they cannot establish
   improved educational writing.
3. **Provide an update example if updates are part of this workflow.**
   Identify the existing chapter/topic/concept/group/question IDs and which
   cells should change. Generation should not guess targets or flip flags.
4. **Propose a replacement for the revision-workbook partial-output fallback.**
   This separate feature still has a legacy fallback after repeated truncation
   and lacks the assessment pipeline's independent semantic critic. Add a
   concrete proposal for completeness review and visible incomplete status.
   Obtain the owner's approval before removing or bypassing any existing
   step; this remains a suggestion, not an implemented removal.
5. **Review saved Admin prompt overrides.** Refined defaults and shared policy
   apply to new calls, but explicit saved overrides remain user-controlled.
   Inspect any older custom blocks for conflicting field instructions.
6. **Keep one versioned column policy shared by author, critic and export.**
   This change establishes that path for the accepted amendments. Future
   corrections should update the policy, affected prompts, export checks
   and a representative workbook regression together.

Q33 policy verification: **286 tests passed**, including 48 focused
owner-column tests. These cover English, Mathematics, Science, History and
Geography authoring/export paths, additional non-English tag containment,
real XLSX read-back, and frozen v1/legacy replay. The run used dry providers,
not live chapter APIs. `git diff --check` also passed.

Historical Q32 validation: the integrated targeted regression command collected 1,144
tests and completed with exit status 0. It covered owner-column and
multipart regressions, assessment stages, MES release/export, all Phase 3
passes, Pre coverage/run binding, release gates, prompt administration,
workbook serialization/delivery/cache and bulk-import migration. Separate
focused runs also completed: 147 legacy/import/prompt tests and 40
column/arithmetic/projection tests. Compilation and `git diff --check` pass.

A complete backend-suite result is not certified: full-suite attempts in
this workspace ended before a usable pytest summary. No live API chapter
comparison, merge, deployment or production database write was performed.
The new prompts and format mechanics are verified; an educational-quality
improvement still needs the matched chapter review recommended above.
