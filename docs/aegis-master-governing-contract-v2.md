<!-- Repository copy of the owner's Master Governing Contract v2.0 (AEGIS-MGC-2.0-20260904), checked in verbatim on 2026-09-04; only table row spacing was normalized for rendering. Register entry Q26 in docs/aegis-restructure.md records its adoption and what it supersedes. -->

> **Binding final English rubric decision:** Controlled bracket tags such as `[content]` and `[creativity]` are required only in English Descriptive rubric criterion fields. They are prohibited in Answer Display, Answer Explanation, model answers, questions, options, accepted-answer cells, descriptions, metadata, and every non-English rubric.

| Control | Binding value |
| --- | --- |
| Document ID | AEGIS-MGC-2.0-20260904 |
| Authority | The user's latest explicit instruction, captured and operationalized in this contract |
| Applies to | Manual generation, deployed Aegis, APIs, workers, workbook exporters, validators, review UI, release tools and recovery bundles |
| Accepted calibration | Grade 6 MSBSHSE, including the A Water Drop v1.4 output structure; calibration artifacts never override the source or a later user correction |
| Current run source values | `concept_source = Balbharati`; `question_source = Balbharati` |
| Release rule | Fail closed. No release with a semantic, scoring, schema, source-accounting, asset, relational or recovery blocker |
| Supersedes | Project Aegis Uniform Manual Authoring Contract v1.2; Aegis Four-Output Golden Contract v1; conflicting guide, SOP, prompt, validator and sample behavior |

*This is an implementation and authoring contract, not a legal services agreement. Its normative words are MUST, MUST NOT, SHOULD, MAY and BLOCK.*

# PROJECT AEGIS - MASTER GOVERNING CONTRACT

**Contract v2.0 - Effective 4 September 2026**

---

## Contents and operating map

| Division | Coverage |
| --- | --- |
| Part I | Governance, authority, evidence and final decision capture |
| Part II | Canonical curriculum and source-accounting contract |
| Part III | Four-output workbook, schema, field and identity contract |
| Part IV | Question, assessment lane, multipart, answer and grouping contract |
| Part V | Rubric, English tag-containment and answer-space contract |
| Part VI | Rich text, KaTeX, tables, images and rendering contract |
| Part VII | Aegis architecture, review, every-click and recovery contract |
| Part VIII | Quality gates, release evidence, amendments and adoption |
| Appendix A | Reconstructed decision capture register |
| Appendix B | Field, delimiter and update matrix |
| Appendix C | English rubric tag registry and examples |
| Appendix D | Blocking defect codebook |
| Appendix E | Evidence and calibration register |
| Appendix F | Copy-paste execution directive for an independent Aegis run |

> **How to use this contract:** Feed the complete document as the controlling specification. A system is not compliant because it can summarize these rules; it is compliant only when every authored record, UI path, persisted release and generated workbook passes the required receipts and zero-blocker gates.

### Normative language

| Word | Meaning |
| --- | --- |
| MUST / MUST NOT | Absolute requirement or prohibition. Violation blocks the affected stage and release. |
| SHOULD / SHOULD NOT | Default requirement. A deviation needs an explicit evidence-backed waiver recorded in the run manifest. |
| MAY | Permitted option that cannot weaken any higher requirement. |
| BLOCK | Stop publication or apply. Preserve evidence, emit a diagnostic release, and require adjudication or repair. |
| Receipt | Persisted evidence showing the model decision, inputs, source references, profile, prompt/model hash, critic verdict and validator result. |

---

# PART I: Governance, authority and reconstructed decisions

The contract controls conflicts, records evidence, and prevents a context-window reset from silently changing the project.

## 1. Governing purpose and outcome

Project Aegis MUST convert a complete textbook chapter into one coherent, source-accounted, grade-aware curriculum and assessment release. The release MUST contain complete concept teaching, correct Pre and Post boundaries, source-owned assessments, valid model answers and rubrics, exact hierarchy links, import-ready workbook projections, render-safe content, public assets, reviewer evidence and restartable state.

The product is not successful merely because an XLSX opens, a job reaches 100%, or a model produced plausible prose. Success means that the source, curriculum, assessments, scoring, schema, UI state and release evidence agree end to end and survive readback.

> **Non-negotiable outcome:** No source obligation is lost, no extra Post task is invented, no current-chapter learning leaks into Pre, no semantic decision is made by a regex or positional fallback, and no artifact is declared complete until the actual files are persisted and deliverable.

## 2. Authority, precedence and conflict handling

| Priority | Authority | Binding use |
| --- | --- | --- |
| 1 | Latest explicit user instruction or amendment | Controls immediately. Compatible completed work remains; conflicting behavior is superseded. |
| 2 | This Master Governing Contract and its signed amendment ledger | Canonical semantic, authoring, workbook, architecture and release specification. |
| 3 | The complete uploaded source chapter | Ground truth for Post membership, content, order, context, visual dependency and answer evidence. |
| 4 | Explicit error/correction documents and accepted reviewer findings | Primary calibration of known defects and repaired behavior. |
| 5 | Accepted output artifacts and QA receipts | Positive calibration only where internally consistent and not contradicted by a higher authority. |
| 6 | Reviewer guides, Open/Specific sheets, SOPs, blueprint references and templates | Applied only where consistent with priorities 1-5. |
| 7 | Existing prompts, code, validators, defaults and deployed behavior | Implementation evidence only. Existing behavior cannot overrule the contract. |

- A conflict MUST be recorded as an explicit supersession; the system MUST NOT blend incompatible rules or silently choose the easiest default.

- A sample file is never a blind template. Known defects in a corrected or approved file remain defects when a later instruction or the source contradicts them.

- When the source and subject accuracy appear irreconcilable, the item MUST be held for adjudication; neither silent copying nor silent correction is allowed.

- An unrecognized profile, category, field, board code, duration or asset environment MUST block rather than borrow a nearby value.

### 2.1 Final English rubric-tag amendment

> **Final state after the earlier reversals:** The historical global tag requirement was removed, and that blanket removal is now itself narrowed. The final rule is: English Descriptive rubric criteria carry controlled bracket tags; every other field and every non-English rubric is tag-free. `[creative]` is superseded by `[creativity]`.

This amendment is deliberately field-scoped. It does not authorize tags in an English model answer, Answer Display, Answer Explanation, question text, accepted-answer slot, option, concept description or metadata. It also does not authorize English tags in Mathematics, Science, Social Science or any other subject.

## 3. Reconstruction method, evidence status and honesty boundary

The Project Library exposed the named source files, governing documents, generated bundles, QA evidence and approved chapter outputs. It did not expose a single verbatim text export of the four named chat transcripts. This contract therefore performs a decision-level reconstruction from the current user instruction, the latest governing documents, indexed attachment content, accepted output behavior and release evidence. It does not pretend to quote or reproduce unseen chat text.

| Evidence class | Meaning | How it is used |
| --- | --- | --- |
| U - current instruction | The user's present correction and requested outcome | Highest authority and direct amendment source. |
| D - direct artifact review | Raw or materialized contract, workbook, Markdown or QA artifact inspected in this reconstruction | Used for exact observed structure and contradictions. |
| I - indexed attachment review | Attachment content retrieved through the Project Library index when raw materialization was unavailable | Used with file name and retrieved content, without claiming raw-byte inspection. |
| Q - derivative QA evidence | Chapter audit, readback report, source disposition or release summary produced from underlying files | Used as evidence, never as a substitute for source-grounded future adjudication. |

Every future handoff MUST preserve the same distinction. A system may state what it inspected, what it inherited through a validated receipt, and what remains unavailable. It MUST NOT upgrade indexed or inferred evidence into a false claim of direct inspection.

## 4. Supersession decisions that MUST not regress

| Area | Earlier state | Final binding state |
| --- | --- | --- |
| Rubric tags | Global tags required -> blanket tag removal | Controlled tags required only in English Descriptive rubric criteria; forbidden everywhere else. |
| English tag spelling | `[creative]` appeared historically | Use exact lowercase `[creativity]`; `[creative]` is invalid. |
| List delimiter | Comma-separated lists and comma-free topic names | Use exact ` \| ` delimiter. Commas are allowed inside names and prose. |
| Objective explanation | Correct option label/number plus text | Begin with exact correct answer text, then rationale. No option letter, number or phrase such as "option b". |
| True/False routing | Objective in older profiles | Subjective with one deterministic placeholder-bound answer. |
| Workbook line breaks | Literal `\n` or renderer-dependent line feeds | Export canonical HTML `<br>`; use `<br><br>` for paragraph breaks. |
| Multipart display | Shared stem only or child-only projection | Complete `question_text` contains shared context plus every labelled child; structured child fields are also populated. |
| Post generation | Generated variants, quota fill or semantic de-duplication | Only source-present learner tasks; preserve occurrence ownership and repeated occurrences. |
| Pre generation | Small count targets or mirrored chapter questions | Complete prerequisite closure with fresh diagnostics and zero current-chapter leakage. |
| Update flags | No only on populated entity bands | All five `is_update_*` fields are exact `No` on every authored data row. |
| Semantic implementation | Regex, heading, position, keyword or fallback authority | Model/API semantic adjudication with evidence and critic receipts; deterministic code only validates mechanics. |
| Concept coverage | Short descriptions, missing Types, fewer Concepts or bare shells | Complete Description, Mastery and all applicable Hubs, Types, Cases, Examples, misconceptions and culminations. |

---

# PART II: Canonical curriculum and source-accounting contract

One semantic model governs the source, concepts, prerequisite map and assessment inventory before any workbook is written.

## 5. Canonical entities and binding definitions

| Term | Binding definition |
| --- | --- |
| Run snapshot | One immutable chapter build with source hashes, board/grade/subject/publication, profile, model/prompt hashes, decisions, outputs and release receipts. |
| Source atom | One traceable source unit: heading, paragraph, example, activity, figure, table, question, teacher note, furniture or pointer. |
| Chapter | The complete uploaded textbook chapter and its evidence boundary, not selected pages or only an exercise section. |
| Topic | A coherent teaching division based on meaning, grade and instructional progression; not a page, banner, equal chunk or question bank. |
| Concept | The smallest independently teachable mastery unit with a substantive Description, distinct observable Mastery and unambiguous ownership. |
| Learning element | The smallest proposition, skill, convention, vocabulary item, representation or operation accepted/rejected during semantic inventory; not automatically an exported Concept. |
| Pre Learning | Necessary prior knowledge or skill learned before the current chapter; source-informed, freshly assessed and free of current-chapter teaching/questions. |
| Post Learning | The current chapter's new teaching plus exactly its source-present learner assessment obligations. |
| Source task | A source occurrence that asks the learner to produce an assessable response, whether interrogative or imperative. |
| Type | A reusable task or assessment method stated operationally as what is given, what the learner does and what is produced. |
| Case | A bounded variation of a Type based on givens, ask, representation or constraint. |
| Example | A complete evaluation-ready question instantiating a Case and retaining its internal/source identity and required context/media. |
| Group | A question family within one lane, concept and tier sharing capability, givens, response space, solution/rubric shape and media role. |
| Activity/Info Hub | Whole supporting pedagogy, vocabulary, worked visual or enrichment allocated to the Concept it supports; it is not automatically a Concept or Post question. |
| Culmination | A source-grounded synthesis Concept that integrates the meaning or capability of a Topic or whole literary work; never a bare recap shell. |
| True multipart | One shared stimulus/instruction with dependent labelled child tasks intentionally evaluated together. |
| Independent enumeration | A common exercise instruction followed by individually meaningful, scoreable items; typography alone does not create a parent. |
| Polishing | A minimal source-faithful repair for clarity, grammar, self-containment and evaluation readiness without changing demand, scope, answer space, modality or media dependency. |

## 6. Uniform contract and subject-aware adaptation

Uniformity means every subject uses the same evidence standard, hierarchy, source accounting, concept-completeness test, Pre/Post logic, field semantics, question routing, rubric quality, numeric rules, identity rules, validation, review and release gates. Uniformity does not mean equal counts, identical topic shapes, identical question categories or Physics-style content in English.

- Grade changes abstraction, vocabulary, assumed prerequisites, representation, example complexity and response burden. Grade MUST affect topology, not merely wording.

- No numeric Topic, Concept, Group or question quota may drive semantic authoring. Counts emerge from the source and mastery boundaries.

- Every subject receives the complete pipeline and independent critics. No subject may fall to a shorter fallback because it lacks a dedicated legacy profile.

- Subject adapters MUST be explicit, versioned and included in the run manifest. File names and keyword matches cannot select an adapter.

## 7. Source inventory, task ownership and disposition

### 7.1 Inventory before authoring

1. Render and parse every source page; preserve page order, block order, tables, figures, labels, captions and task boundaries.

2. Mint a stable `source_atom_id` for every atom and a stable ordered `source_qid` for every assessable task occurrence.

3. Assign every atom exactly one disposition: `task`, `context`, `support`, `not_task`, `pointer_alias` or `unresolved_reference`.

4. For each task, preserve original wording, page/block anchor, parent/child topology, shared context, required media, answer evidence and occurrence order.

5. Resolve every `unresolved_reference` before release. A flag without adjudication is not a pass.

### 7.2 What enters Post Master

> **Closed Post set:** Post Master contains only explicit source-present learner tasks, including source tasks embedded inside an Activity, Info Hub, table, image discussion, warm-up or other body text. Polishing may make the task usable; it cannot create a new task.

- Rhetorical questions, fictional questions spoken inside a poem/letter/story, procedural headings and teacher-only directions are not learner tasks unless the source separately instructs the learner to answer them.

- Deliberate repeated source occurrences remain distinct. Semantic similarity does not authorize de-duplication.

- One source occurrence is published once, represented by one true multipart parent, or explicitly dispositioned. Parent and decomposed duplicates are forbidden.

- No generated enrichment, concept-derived variant, quota filler, inferred practice item or imported question-bank item may enter Post.

- Every Post Concept Example that carries an assessment MUST resolve to one source QID and one Post Master row. Every Post Master row MUST resolve back to one source QID and Concept Example route.

### 7.3 Source-faithful polishing

| Permitted | Prohibited |
| --- | --- |
| Correcting grammar, singular/plural mismatch or an obvious wording defect while preserving the task | Adding a new requirement, example count, constraint, subquestion or scoring demand |
| Supplying the source passage, table, diagram or prior instruction needed for self-containment | Detaching essential context or replacing a visual with a description that changes the task |
| Replacing ambiguous source apparatus with clear labels while retaining source order and meaning | Narrowing an Open task or broadening a Specific task |
| Making implicit evaluation mechanics explicit when the source clearly requires them | Changing response mode, intended answer, evidence burden or creative freedom |

Every polished item MUST carry a semantic equivalence receipt comparing original and final demand, response mode, answer space, context and media dependency. Any uncertainty that could affect scoring blocks release.

## 8. Pre-Learning prerequisite closure

> **Pre boundary:** Pre Learning is earlier-grade or earlier-year prerequisite knowledge needed to access the chapter. It is not current-chapter teaching, a chapter summary, a copied source exercise or an arbitrary diagnostic count.

1. For every Post capability, reverse-inventory the vocabulary, notation, representation, convention, fact, operation, comprehension skill or method that must already be known.

2. Accept a prerequisite only when it passes three tests: necessity for a named Post capability, plausible prior-grade boundary, and non-leakage from current-chapter new teaching.

3. When an idea has an earlier base and a current extension, record separate `prior_scope` and `new_scope`; lexical overlap is acceptable, mastery overlap is not.

4. Assign each prerequisite atom once to one Pre Concept. One Pre Concept may support several Post Concepts through explicit needed-for links.

5. Author fresh grade-appropriate diagnostics. Do not copy, lightly paraphrase, numerically mutate or structurally mirror a current-chapter source task.

6. Every shipped Pre Concept MUST have complete diagnostic coverage of its Mastery and at least one routed question. Generated Pre QIDs MUST be fed back into Pre Concept Types/Cases/Examples before release.

- No fixed count such as five questions or one Concept per heading is permitted.

- Pre Topics need not mirror Post Topics. They are organized by coherent prerequisite capabilities.

- A genuinely empty Pre lane requires a chapter-wide evidence verdict and critic approval, never a blank default.

- The release MUST contain a forward-and-reverse closure ledger: Post capability -> prerequisite atom -> Pre Concept -> diagnostic QID, and every Pre item must point back to a real Post need.

## 9. Topic, Concept and description quality

### 9.1 Topic boundary

- A Topic has one coherent instructional purpose and a meaningful progression of Concepts.

- Source headings and order are evidence, not automatic boundaries. Pages, activities, exercise banners, equal text chunks and question categories are not Topics by default.

- Topic count and density MUST be grade-aware. A lower grade generally uses fewer, broader, concrete units than a higher grade when the source supports that treatment.

- `topic_description` MUST explain what is learned, how the Concepts fit, and how the Topic advances the Chapter. A name list is invalid.

### 9.2 Concept boundary and completeness

- Split when the material contains independently teachable capabilities, mechanisms, interpretations or methods with distinct Mastery. Merge fragments that are merely terms, facts, steps, examples or aspects unable to stand alone.

- Every Concept MUST have a substantive grade-appropriate Description and a distinct observable Achieving Mastery. Generic text such as "covers the topic" or a one-line glossary is invalid.

- No two Concepts may carry duplicate mastery under different wording. Every question has one unambiguous owning Concept.

- Concept descriptions MUST use the source and the accepted final topology, not a generic template summary.

## 10. Canonical Concept details

Every Concept MUST serialize its content in the following semantic order. Optional sections appear only when genuine source or pedagogical evidence exists; their absence is permitted, but Description and Achieving Mastery are never optional.

```text

Description: <substantive grade-appropriate explanation>
Achieving Mastery: <distinct observable learner capability>
Activity/Info Hub: <whole allotted source support, if applicable>
Types:
Type 01: <stable title> - Given <inputs>, the learner <action> and produces <output>.
Cases:
Case 01 (Type 01): <bounded variation and constraint>.
Examples:
Example 01 (Case 01): <complete evaluation-ready question>.
Question label: <stable question label>
Misconception/Error Analysis: <only genuine, nonduplicated entries>

```

- Use `<br>` between sections in workbook cells. Do not use literal `\n`.

- Type definitions MUST be operational: given -> action -> product. A category name such as "MCQ Type" is not a Type.

- Cases MUST be real variations rather than restating the Type title.

- Examples MUST retain complete context, options/subquestions and required media. A fragment or answer key is not an Example.

- Activity/Info Hub material remains whole. It is not fragmented into thin Concepts or assessment rows unless an explicit learner task is present.

- Misconceptions and Error Analyses are allotted without duplication. They are not filler and are not mandatory for every Concept.

## 11. English literary and language topology

| Mode | Binding topology |
| --- | --- |
| Poetry | Meaning-bearing stanza or movement Topics; line-pair/idea Concepts where independently teachable; source-grounded Culmination at the close of each literary Topic; final Detailed Analysis Topic. |
| Prose / fable / story | Substantial narrative movements or episodes as Topics; meaningful episodes, decisions, themes and language capabilities as Concepts; Topic culminations and whole-text Detailed Analysis. |
| Play | Substantial scene or dramatic-development Topics; action, conflict, character, dialogue and performance capabilities as Concepts; scene culminations and whole-text Detailed Analysis. |
| Expository / functional language | Coherent informational or language-skill movements. Grammar, vocabulary, dictionary, phonics, listening and composition are threaded to source purpose unless explicitly standalone. |

### 11.1 Culminations

- Every English literary Topic MUST end in a source-grounded Culmination unless an explicit semantic receipt proves that the Topic is itself atomic and a separate synthesis would be artificial.

- The whole-text Detailed Analysis Topic MUST end in a final Culmination integrating meaning, development and responsibility/theme as supported by the source.

- A Culmination MUST contain a real Description and Mastery. It may own source questions or generated Pre diagnostics only when those questions genuinely assess the synthesis; it MUST NOT create an extra Post question.

### 11.2 Detailed Analysis

The Detailed Analysis Topic SHOULD include only supported dimensions from: Theme and Central Idea; Plot/Development or Progression; Character/Speaker/Point of View; Setting and Atmosphere; Language and Literary Devices; and a final Culmination. The dimensions are not a quota. They must be source-grounded, distinct and grade-appropriate.

### 11.3 Printed English blocks

| Printed block | Default treatment |
| --- | --- |
| Poem/story/play content | Literary Topics and Concepts according to meaning and development. |
| Warm-up / Let us talk | Activity/Info Hub unless it contains an explicit learner task, which enters the source task inventory. |
| Word Basket / vocabulary box | Pre support when truly earlier knowledge; otherwise a Post Hub tied to the relevant Concept. |
| Poetic-device box / worked example | Retain whole in the relevant Hub; worked examples do not become duplicate questions. |
| Grammar / phonics / dictionary | Thread to the literary or functional context; independently scoreable exercise items remain independent. |
| Facilitator / teacher note | `not_task` unless it explicitly addresses the learner and requires a response. |

---

# PART III: Four-output workbook, schema and identity contract

The accepted semantic release is projected into four exact, linked, update-aware workbooks without losing meaning.

## 12. The four required output files

| Output | Purpose | Question origin and placement |
| --- | --- | --- |
| 01 Pre-Learning Concept | Prerequisite Chapter/Topic/Concept map | No current-chapter source questions. Hierarchy-only rows. |
| 02 Pre-Learning Master | Fresh prerequisite diagnostic bank | Generated Pre questions routed to Objective, Descriptive or Subjective by mechanics. |
| 03 Post-Learning Concept | Current-chapter teaching map and source-task Examples | Post Concepts and source QID routes; no unowned generated Post question. |
| 04 Post-Learning Master | Polished source assessment bank | Exactly the accepted source task inventory, with every occurrence owned or explicitly dispositioned. |

- All four files are projections of one accepted release snapshot. Shared Chapter, Topic and Concept identity, text and source values MUST be byte-consistent wherever repeated.

- Filenames MUST state purpose explicitly; ambiguous numbering alone is not enough.

- Every file contains sheets in exact order: `Objective`, `Descriptive`, `Subjective`.

- Unused sheets remain header-only. They MUST NOT contain placeholder rows, stale data or hidden questions.

- A Concept output uses the accepted profile's hierarchy carrier sheet. For the current update-aware Grade 6 profile, hierarchy rows are carried on `Objective`; the other two sheets are header-only.

- Master outputs carry the complete hierarchy and group bands on every question row and route the question band to the correct response sheet.

## 13. Row projection and relational model

> **Approved row-carried projection:** For this four-output contract, a data row is a complete projection, not a detached shell. Concept rows carry Chapter + Topic + Concept bands. Master rows carry Chapter + Topic + Concept + Group + Question bands. Old instructions that required separate staircase shell rows do not control this profile.

- One row represents one Concept in a Concept file or one Question in a Master file. Do not split a record across rows.

- All applicable parent fields are repeated exactly on the row so the import is self-resolving and readback-verifiable.

- No merged cells are permitted in the data region. Row 1 bands and row 2 field headers are template structure, not editable data.

- Records remain in accepted Topic/Concept order; Post questions preserve source occurrence order within the semantic routing rules.

- Every parent list resolves byte-for-byte to real child identities. No dangling, duplicate, cross-lane or cross-phase references may ship.

## 14. Update-aware schema and template fingerprint

| Sheet/profile | Accepted width | Notes |
| --- | --- | --- |
| Objective - standard update-aware | 72 columns | Five update fields added to the accepted Objective base. |
| Descriptive - standard update-aware | 380 columns | Main rubric plus structured multipart capacity and restored source field. |
| Subjective - standard update-aware | 149 columns | Five update fields and placeholder-bound accepted-answer slots. |
| English Post Descriptive | 440 columns | English profile variant with expanded parent rubric capacity. Only an explicitly frozen profile may use it. |

- Generation MUST freeze the exact template filename, SHA-256, sheet order, row-2 header sequence, row-1 bands, column positions and slot capacity before authoring.

- All writes and validations locate fields by unique row-2 header name. Excel letters and old coordinates are explanatory only and never authority.

- Duplicate headers, trailing-newline headers, whitespace-tainted headers, missing required fields, unknown fields, styled trailing columns or partial update-column insertion block before data write.

- A later physical template may supersede these widths only after its full fingerprint is explicitly accepted for the run. Missing columns are never guessed.

### 14.1 Mandatory update fields

| Field | Insertion point | Value on every authored data row |
| --- | --- | --- |
| `is_update_chapter` | Immediately after `chapter_title` | `No` |
| `is_update_topic` | Immediately after `topic_title` | `No` |
| `is_update_concept` | Immediately after `concept_title` | `No` |
| `is_update_group` | Immediately after the canonical group-name field in the resolved sheet | `No` |
| `is_update_question` | Immediately after `question_label` | `No` |

> **Exact update rule:** Every one of the five fields MUST contain exact case-sensitive text `No` on every authored row, even when the corresponding later entity band is otherwise blank. `Yes`, blank, `FALSE`, `0`, formulas and inherited values are invalid.

## 15. Canonical IDs, titles and labels

| Entity | Canonical pattern | Example |
| --- | --- | --- |
| Chapter title | `<Display Name> (<Grade>_<Subject>_<Board>_<Publication>)` | `A Water Drop! (06_English_MSBSHSE_Balbharati)` |
| Topic title | `Topic NN: <Display Name> (<Base>_<PrL\|PL>_TNN)` | `Topic 05: Detailed Analysis of A Water Drop! (06MSEN_AWaterDrop_PL_T05)` |
| Concept title | `<Display Name> (<TopicID>_CNN)` | `Language and Literary Devices (06MSEN_AWaterDrop_PL_T05_C05)` |
| Group name/display | `(<ConceptID>) <BG\|IG\|AG>NN` | `(06MSEN_AWaterDrop_PL_T05_C05) IG01` |
| Question label | `<ConceptID> QNN` | `06MSEN_AWaterDrop_PL_T05_C05 Q07` |
| Source occurrence QID | Stable chapter-local ordered source identity kept in evidence ledger | `WD-SQ-007` |

- Board, grade, subject, chapter slug, phase and numbering grammar are frozen once per run. Semantic title edits MUST NOT silently remint unrelated IDs.

- Display-name pairs MUST match the human title. Group name and group display name byte-match the canonical group ID; semantic family meaning belongs in `group_description`.

- Question, group, concept and topic labels MUST be unique, stable, source/resume-safe and copied exactly into every parent rollup.

- One ID maps to one title, parent, source and description across all four outputs. Collisions or aliases without a documented mapping block release.

## 16. Pipe delimiter and multi-value field contract

> **Delimiter override:** All exported multi-value cells use exact separator ` | ` (one space, pipe, one space). Comma is ordinary content and may appear inside a title, phrase, question or description.

| Band | Pipe-delimited fields |
| --- | --- |
| Chapter | `pre_topics`, `post_topics` |
| Topic | `topic_concept_labels`, `related_topics` |
| Concept | `keywords`, `digicards`, `related_concepts`, `basic_groups`, `intermediate_groups`, `advanced_groups`, `concept_question_labels` |
| Group | `group_question_labels`, `related_digicards` |
| Other/profile fields | Any field declared as a multi-value list, including multi-source or pre/post relationship fields |

- Trim every token. No leading/trailing delimiter, empty token, duplicated token or accidental double pipe is allowed.

- Preserve the accepted semantic/source order. Do not alphabetize unless the profile explicitly requires it.

- The parser MUST split only the list delimiter, never commas. Topic names therefore may contain commas.

- A literal pipe inside a single value is forbidden in v2.0. The run MUST block or use a future explicitly versioned escape rule; it MUST NOT guess.

- `question_source` and `concept_source` remain scalar per-run fields. A separate source list, if present, follows the pipe rule.

## 17. Rich cell line-break contract

- Workbook rich/display cells use `<br>` for a line break and `<br><br>` for a paragraph break.

- Literal backslash-n (`\n`) is forbidden. Raw line-feed behavior cannot be relied on in the imported workbook.

- Objective `question_text` uses `<br>a) ...<br>b) ...` and so on. Multipart `question_text` uses `<br>` between labelled children.

- Concept details use `<br>` between Description, Mastery, Hubs, Types, Cases, Examples and analysis sections.

- The canonical internal model may store structured paragraphs and lines; the exporter is responsible for one deterministic HTML projection.

- Only the accepted rich-text tokens are emitted. Arbitrary HTML, scripts, styles and unapproved tags are prohibited.

## 18. Controlled scalar fields and exact values

| Field | Binding rule |
| --- | --- |
| `concept_source` | Mandatory per run on every populated Concept band. Current Grade 6 runs use exact `Balbharati`. |
| `question_source` | Mandatory on every question. Current Grade 6 runs use exact `Balbharati`; source QID remains separate evidence. |
| `question_appears_in` | Exact `Pre/Post-Worksheet/Test` unless a separately accepted profile defines another literal. |
| `group_status` | Exact `Active`. |
| `cognitive_skills` | One exact controlled value: `Remember`, `Understand`, `Apply`, `Analyse`, `Evaluate` or `Create`. |
| `level_of_difficulty` | One exact controlled value: `Less`, `Moderate` or `High`; must agree with real task demand and group tier. |
| `group_type` | `Basic`, `Intermediate` or `Advanced`, matching `BG`, `IG` or `AG`. |
| `answer_restriction` | Exactly `Open` or `Specific`; no blank, lowercase variant or third state. |
| `question_disclaimer` | Blank unless the resolved profile explicitly requires content. |
| `math_keyboard` | `Yes` only when the learner must enter mathematics; otherwise the exact profile-approved negative/blank behavior. |

---

# PART IV: Assessment, question and grouping contract

Response mechanics, source atomicity and scoring determine the row, not typography or a legacy category default.

## 19. Question atomicity and parentage

### 19.1 True multipart

- Keep one row when a shared passage, scenario, table, diagram, dataset, instruction or visual is required by dependent children evaluated together.

- The parent `question` carries only the shared context/instruction. Complete `question_text` carries that shared content followed by every labelled child in order.

- Every child is also projected once into contiguous `sub_question_N` fields with the identical enumeration and wording used in `question_text`.

- Parent and child model/rubric views are equivalent and non-additive. The evaluator MUST never sum both projections.

### 19.2 Independent enumeration

- Items under a common grammar, vocabulary, rhyme, True/False, sentence-rearrangement or end-exercise instruction become separate rows when each remains meaningful and scoreable without its siblings.

- Labels such as a), b), c), i), ii) or iii) are typography only. They do not determine parentage.

- Each independent row repeats the minimum complete shared passage/instruction needed for standalone evaluation.

### 19.3 Integrated structures and constraints

- A matching table, classification grid or single bank-governed task remains one integrated question when its entries are one scoring structure rather than child prompts.

- Hints, formatting labels, composition cues, response boxes and layout furniture are constraints/scaffolds unless they independently ask for responses.

- Never emit both a multipart parent and standalone duplicates of its children elsewhere.

## 20. `question` and `question_text`

| Field | Binding content |
| --- | --- |
| `question` | The authoring stem or shared instruction only. Objective options are excluded. For true multipart, dependent child wording is excluded because it lives in structured child fields. |
| `question_text` | The complete learner/evaluator rendering: full required stimulus/context plus all options or every labelled subquestion. It MUST stand alone. |

- Passage-based items include the correct passage or excerpt, attribution where needed, the shared instruction and every dependent child in one `question_text`.

- Source apparatus such as page number, exercise number or an unresolved phrase such as "as above" is removed or replaced with the actual referenced content.

- Question polishing MUST NOT leak an answer, remove a required source detail, change answer space, detach a visual or alter task difficulty merely to fit a category.

## 21. Assessment lanes and routing

| Lane | Use when | Required behavior |
| --- | --- | --- |
| Objective | A closed, source/author-supplied option set has exactly one correct option | Stem-only `question`; full option list in `question_text`; contiguous option fields; one `Yes`; distractors `No`; Specific. |
| Subjective | One or more deterministic placeholder-bound accepted answers, with no option list | `$$a$$`, `$$b$$`... in `question`; matching visible blanks in `question_text`; contiguous accepted-answer slots; Specific. |
| Descriptive | Learner constructs an explanation, method, proof, interpretation, composition, diagram or true multipart response | Complete model answer; main rubric; child blocks when multipart; may be Open or Specific. |

> **True/False override:** Every True or False item is routed to `Subjective`, not `Objective`. It uses one placeholder, one accepted answer (`True` or `False`), `answer_display_1 = Yes`, full weight, and `answer_restriction = Specific`.

- Response mechanics are adjudicated before category. A legal category string on the wrong sheet is still a defect.

- An unoptioned deterministic Fill in the Blank belongs in Subjective. A source-supplied choice set may be Objective only if the accepted profile and option model support it.

- A Match-the-Following structure may be Objective only when the platform supports a closed keyed option projection without distortion; otherwise it is an integrated Descriptive table.

- An Open response MUST NOT be forced into Subjective exact-key form.

- The same semantic question may appear on only one lane and one sheet in a release.

## 22. Objective contract

1. `question` contains the stem only. `question_text` contains the stem plus every option, each separated by `<br>` and labelled exact lowercase `a)`, `b)`, `c)` and so on.

2. Occupied option slots are contiguous. Each `answer_content_N` contains exact unlabelled option content. Text uses `Words`; `Equation` or `Image` is used only when the whole option requires that medium.

3. Exactly one `correct_answer_N` is `Yes`; every distractor is `No`. The correct option weight equals question marks; every distractor weight is numeric zero.

4. Options are parallel, plausible, nonduplicated and do not reveal the key through grammar, length, formatting or copied wording.

5. `answer_explanation` begins with the exact correct answer text and then explains why it is correct. It MUST NOT include the option letter/number, a leading `a)`/`b)`, or wording such as "Option 2".

| Invalid explanation | Valid explanation |
| --- | --- |
| `b) sleepy. The clue shows...` | `Sleepy. The clue "curled up and slept" shows that drowsy means sleepy.` |
| `Option a is correct because...` | `An integer. Negative five belongs to the set of integers.` |

## 23. Subjective contract

- `question` contains contiguous machine placeholders `$$a$$`, `$$b$$`, ... exactly once each and in order. It contains no option list.

- `question_text` shows matching learner-visible blanks or the profile-approved True/False answer position without losing one-to-one mapping.

- Each used slot contains `answer_type_N`, accepted `answer_N`, literal `answer_display_N = Yes`, numeric positive `weightage_N`, and `placeholder_N` without dollar signs.

- Text answers normally use `Words`. Use `Equation` or `Image` only when the answer medium genuinely requires it.

- Every blank has a bounded accepted-answer set. A broad, explanatory, creative or defensible multi-answer response is rewritten only if source-equivalent; otherwise it is routed to Descriptive.

- Slot weights sum exactly to question marks. Unused slots are completely blank.

### 23.1 True/False projection

```text

question: <complete statement and context><br>Answer: $$a$$
question_text: <complete statement and context><br>Answer: ____
answer_type_1: Words
answer_1: True  // or False
answer_display_1: Yes
weightage_1: <full item marks>
placeholder_1: a
answer_restriction: Specific
answer_explanation: True. <source-grounded reason>

```

## 24. Descriptive and multipart scoring contract

- `display_answer` and `answer_explanation` are byte-equivalent after canonical whitespace/HTML normalization and contain one complete learner-facing model answer.

- Model answers contain the answer, not rubric narration, criterion tags, step labels with marks, evaluator instructions or hidden implementation text.

- Main rubric fields are populated for every Descriptive item, including true multipart items.

- Textual criteria use `Phrases`; `Equation` and `Image` cells use their true typed medium and raw value conventions.

- For true multipart, every child block contains the identical labelled `sub_question_N`, child marks, and complete contiguous `sqN_*` criteria. Parent criteria are the ordered union of all child-scored content.

- Parent marks equal the sum of child marks. Parent rubric weights equal parent marks. Each child criterion sum equals that child's marks. Parent and child views are equivalent and non-additive.

- A profile capacity limit MUST NOT cause content to be omitted, merged or weakened. Resolve an expanded accepted profile or block.

## 25. Categories, cognitive skill, difficulty, marks and duration

- The board-grade-subject profile MUST freeze exact legal category strings, marks, duration behavior, lane compatibility, slot capacity and controlled values before authoring.

- Category follows source-faithful response mechanics. The system MUST NOT change a task to fit a convenient category.

- Cognitive skill describes the highest actual demand required for full credit, not the command verb alone.

- Difficulty reflects the source/context, number of operations, abstraction, inference, independence and response burden. It is not assigned by marks alone.

- Marks come from the source/accepted blueprint and the actual credit-bearing demands. If demands cannot support the assigned marks, block or obtain an explicit blueprint correction; do not invent filler criteria.

### 25.1 Accepted Grade 6 MSBSHSE category behavior

| Subject | Objective | Subjective | Descriptive |
| --- | --- | --- | --- |
| English | Multiple Choice Question | Fill in the Blanks; True or False | Very Short Answer Questions; Short Answer Type (2 Marks); Short Answer Type (3 Marks); Long Answer Type (4 Marks); Composition Writing |
| Mathematics | Multiple Choice Question; source-faithful closed matching/choice structures only | Deterministic Fill in the Blanks; True or False | Constructed methods, comparisons, diagrams, proofs, long answers and integrated structures under exact profile categories |
| Science | Multiple Choice Question; source-supplied choice structures with one key | Deterministic Fill in the Blanks; True or False | Constructed explanations, observations, comparisons, classifications, diagrams, procedures and multipart tasks under exact profile categories |

*The profile must store exact CMS literals. The descriptive summary above does not authorize an unregistered synonym or category.*

## 26. Group formation, reuse and relational tagging

### 26.1 Group fingerprint

A group is formed only after comparing the complete question fingerprint: owning Concept; lane/sheet; tier; atomic capability; questioning intent; givens and their role; response shape; solution or rubric signature; essential media; and source/provenance constraints.

- Surface wording alone does not split a family. For example, "What is a circle?" and "Define a circle" belong together when answer scope and scoring are the same.

- Different answer spaces, scoring signatures, media roles, atomicity or cognitive methods require separate groups even when the topic is similar.

- Every question has exactly one primary group in one lane, one Concept and one tier. A question label may not be listed in two groups.

- Every group has exact `group_name = group_display_name`, meaningful `group_description`, `group_status = Active`, matching `group_type`, and a pipe-delimited list of real question labels.

- Concept `basic_groups`, `intermediate_groups` and `advanced_groups` contain only existing matching-tier groups. Empty group shells and generic placeholders are forbidden.

### 26.2 Existing-group reuse

When appending to an existing Master, Aegis MUST freeze the existing group snapshot including members, answers, rubrics, media and provenance. The semantic authority returns exactly one decision: `reuse_existing` with the recorded group key and snapshot version, or `create_new` with the next valid key and rationale. Local code MUST NOT choose a group by first match, tier count or order, and MUST NOT overwrite occupied group metadata as a side effect.

---

# PART V: Rubric and answer-space contract

Scoring must be complete, atomic, question-specific and cleanly separated from the learner-facing answer.

## 27. General rubric quality

1. Build a demand-to-criterion map from the complete source-faithful question before assigning weights.

2. Decide the semantic answer space (`Open` or `Specific`) from the complete item, context, media, model response and unweighted rubric.

3. Write the complete model answer and acceptance envelope.

4. Convert each independently creditable demand into one observable, question-specific criterion.

5. Assign only `0.5` or `1` mark to each criterion under this contract. Split larger awards into discrete non-overlapping criteria.

6. Run an independent critic that jointly verifies question, answer space, model answer, criteria, accepted equivalents and arithmetic.

- Every explicit demand and expected full-credit element is scored once. Nothing unasked is credited; nothing required is unscored; nothing is double-counted.

- A criterion states the actual content, relation, step, evidence, feature, conclusion or communication performance that earns credit. Generic text such as "correct content", "good explanation" or "uses language well" is invalid.

- A single undivided four-mark rubric criterion is invalid. Marks cannot be manufactured through vague language/style filler.

- Every criterion appears in the model answer or acceptance envelope, and every required model-answer component is scored. This bidirectional check is blocking.

- Open rubrics define common relevance, correctness, defensibility, evidence, reasoning, task-fulfilment and communication constraints without making one exemplar's chosen content exclusive.

- Specific rubrics enumerate the bounded required elements, steps, accepted equivalents and precision needed for full credit.

## 28. English rubric-only tag containment

> **Exact scope:** Every populated English Descriptive textual rubric criterion MUST begin with exactly one approved tag. The tag is permitted only in parent `answer_content_N` and child `sqM_keyword_N` rubric cells (and the equivalent structured rubric field before projection). It is forbidden everywhere else.

### 28.1 Approved English tag registry

| Exact tag | Use |
| --- | --- |
| `[content]` | Required answer substance: idea, fact, meaning, detail, response component or task fulfilment. |
| `[evidence]` | A relevant textual/source detail, action, word, quotation or observation supporting a claim. |
| `[reasoning]` | Explanation, inference, interpretation or explicit link from evidence to claim/conclusion. |
| `[organisation]` | Required sequence, cohesion, paragraph/letter/poster structure, layout or logical progression. |
| `[language]` | Grammar, vocabulary, sentence control, expression and mechanics when the source/profile genuinely assesses them. |
| `[creativity]` | Original, imaginative or personally developed content that remains relevant and within source/task constraints. |
| `[accuracy]` | Exact form, spelling, punctuation, transformation, recitation fidelity or bounded language correctness. |

### 28.2 Syntax

- Exact syntax is `[tag]: <observable credit-bearing criterion>` with lowercase tag, closing bracket, colon and one space.

- Use exactly one tag per criterion. If two independently creditable dimensions exist, split them into two criteria and weights.

- The tag classifies the criterion and earns no mark by itself. Text after the tag must be specific enough to score without seeing the tag.

- `[creative]`, `[Content]`, `[organisation and language]`, multiple adjacent tags and custom tags are invalid.

- A structured internal field `criterion_class` SHOULD store the enum separately. The workbook compatibility projection prefixes the English rubric cell; UI may render the class as a badge. No renderer may move it into the model answer.

### 28.3 Allowed and forbidden locations

| Location | English tag rule |
| --- | --- |
| English Descriptive parent `answer_content_N` | Required on every populated textual rubric criterion. |
| English Descriptive child `sqM_keyword_N` | Required on every populated textual child criterion. |
| English Objective `answer_content_N` | Forbidden: these are options, not rubric criteria. |
| English Subjective `answer_N` | Forbidden: these are accepted answers, not rubric criteria. |
| `display_answer` and `answer_explanation` | Forbidden without exception. |
| `question`, `question_text`, `sub_question_N` | Forbidden. |
| Concept details, Examples, descriptions, titles, labels, source fields, group descriptions and metadata | Forbidden. |
| All Mathematics, Science, Social Science and other non-English rubric cells | Forbidden; write the actual criterion directly, tag-free. |

### 28.4 Valid and invalid examples

| Status | Criterion |
| --- | --- |
| Valid | `[content]: Names the rainy season as the preferred season.` |
| Valid | `[evidence]: Refers to the line in which the drop falls as rain.` |
| Valid | `[reasoning]: Explains how the cited action shows the speaker's wonder.` |
| Valid | `[organisation]: Uses a greeting, connected body and closing in the reply letter.` |
| Valid | `[language]: Uses clear complete sentences with correct basic punctuation.` |
| Valid | `[creativity]: Develops an original but source-consistent alternative ending.` |
| Invalid | `[content]: Correct content.` |
| Invalid | `[creativity]: Creative answer.` |
| Invalid | `[content][evidence]: Gives the answer and proof.` |
| Invalid | `[creative]: Writes an original response.` |
| Invalid | A model answer containing `[content]:` or any rubric label. |

## 29. English rubric substance

- Comprehension and long-answer criteria separately identify the direct answer/claim, exact source fact or acceptable paraphrase, evidence-to-claim explanation, and required inference/theme/character change where demanded.

- A character-quality question based on words/actions credits both the quality and supporting action/word. Naming a trait alone is insufficient unless the prompt asks only for the trait.

- Organisation, expression, grammar or language receives marks only when the source task, category, profile and marks genuinely assess it.

- Creative/composition tasks may assess task fulfilment, relevant development, organisation/cohesion, language/mechanics and creativity, each only to the extent required by the task.

- Grammar/transformation tasks use bounded criteria for form, meaning preservation, tense, agreement, spelling or punctuation and are normally Specific.

- No reusable generic English long-answer rubric may be pasted across questions. Every criterion is question-specific.

## 30. Mathematics and Science rubrics

### 30.1 Mathematics

- Credit only necessary assumptions, extracted givens, valid formula/method, distinct calculation steps, diagram/table transformation where required, and final answer/conclusion with units.

- Do not award repeated algebra, redundant rewriting or the same step twice.

- Equivalent valid methods and notation are accepted under the Specific answer-space contract when they reach the same bounded mathematical target.

- Equations in rubric cells use typed `Equation` with raw LaTeX when the criterion itself is an equation; prose criteria remain tag-free `Phrases`.

### 30.2 Science

- Criteria distinguish observation, identification, mechanism/reason, evidence, comparison dimension, labelled diagram, procedure and conclusion as separately creditable demands.

- Do not invent generic accuracy or language marks to balance an under-specified source task.

- Alternative scientifically valid phrasing is accepted when it preserves the required mechanism and grade scope.

- Visual/experimental evidence must be present in the learner context when a criterion depends on it.

## 31. Open and Specific answer restriction

| Value | Binding meaning |
| --- | --- |
| Specific | Every full-credit response satisfies one bounded semantic target or one complete closed set of required elements. Synonyms, paraphrases, equivalent order/notation, units, spelling variants or working remain accepted when they do not change the target. |
| Open | Materially different full-credit content, evidence choices, assumptions, methods, representations or justified conclusions are intentionally possible, while all responses still satisfy explicit relevance, correctness, evidence, reasoning and task constraints. |

- No command word, question type, subject, sheet, marks, response length, difficulty, option count, regex or default may independently decide the restriction.

- Objective is Specific only after verifying one unambiguous correct option. Every valid Subjective row is Specific because each slot has bounded accepted values.

- Descriptive may be Open or Specific. A composition is not automatically Open; a comprehension inference is not automatically Specific.

- A true multipart parent is Open when any child intentionally permits materially different full-credit answers; otherwise it is Specific.

- Classification requires a persisted evidence receipt from the complete stimulus, question_text, children, source context, media, model answer and unweighted rubric.

## 32. Numeric and arithmetic contract

- Every populated `chapter_duration`, `question_duration`, `marks`, answer/rubric weight, sub-question mark and sub-question weight is a real numeric cell, never numeric text or unit-bearing text.

- Use numeric half-step granularity where applicable. Rubric criteria under this contract use only `0.5` or `1`. Numeric zero is used only for zero-weight distractors or an explicitly required field.

- Display with number format `0.##`, preserving `0.5`, `1`, `1.5`, `2` without converting storage to text.

- Empty numeric fields remain blank; they do not become zero.

- Every option/slot/parent/child sum reconciles exactly to item marks. Floating tolerance is not used to excuse a wrong workbook value.

### 32.1 Duration

- Chapter duration is frozen once per chapter and repeated identically across all four outputs. It is not the sum of assessment durations.

- Use an accepted board-grade-subject-chapter registry value, explicit source periods multiplied by accepted period length, or an explicit upload variable. Means, nearest-title matches, cross-board defaults and silent fallbacks are forbidden.

- Question duration follows the exact accepted category/difficulty profile or an evidence-backed semantic duration rule. It MUST NOT be guessed from text length alone.

- Historical values such as 160 minutes for Radha's Letter or 200 minutes for A Water Drop are chapter-specific calibration receipts, not global defaults.

---

# PART VI: Rich text, mathematics, tables, images and rendering

The workbook must remain semantically complete after importer parsing and frontend rendering.

## 33. KaTeX and mathematical content

| Context | Binding syntax |
| --- | --- |
| Body/display fields | Wrap each genuine mathematical expression in one `[Katex]...[/Katex]` block. |
| Typed `Equation` answer/rubric cell | Store raw supported LaTeX only, without `[Katex]` wrappers. |
| Blank placeholder | `$$a$$`, `$$b$$`... only for Subjective placeholder tokens; never as a math delimiter. |
| Fractions | Use semantic stacked form such as `\frac{a}{b}`; do not flatten a required fraction into ambiguous inline text. |
| Units and exponents | Keep units with the mathematical quantity/expression, use semantic spacing, and brace exponents such as `s^{-2}`. |

- No raw `$...$`, math `$$...$$`, `\(...\)` or `\[...\]` delimiters in body text.

- No nested or empty wrappers, broken braces, unsupported commands, spurious minus signs or fragmented expressions.

- Use only the accepted frontend KaTeX allowlist. Historical unsupported commands such as `\mathrm` and `\eq` are prohibited unless a later profile explicitly proves parser support.

- Ordinary prose numerals, dates and page numbers remain plain. A visible formula, equation, fraction, algebraic relation or formulaic quantity with unit is rendered as mathematics.

- Every unique expression is parsed and rendered through the actual target KaTeX engine before release.

## 34. Tables and structured matching

- Text/math tables and matching structures default to one complete canonical KaTeX `array` preserving headings, row labels, cells, order, relations and blanks.

- Do not use Markdown tables, `tabular`, coordinate prose or separate screenshots for rows/cells.

- If a table contains an essential non-textual visual that cannot be faithfully represented, use one tight complete crop and retain every required label and cell.

- Reconstruct the table content into an inventory and compare it with the source before release. Missing cells, reordered labels or answer-revealing reconstruction are blockers.

## 35. Images, crops and public assets

- An assessment that depends on an image, diagram, map, graph, table, photograph or passage MUST include it in the question context. A sidecar asset alone is insufficient.

- Body syntax is exact `[img src="https://..." alt="..."]`. A typed `Image` answer cell contains the raw URL only.

- Use exact-context tight crops. Include every required label/feature and exclude unrelated page furniture, neighboring answers, branding and accidental text.

- Alt text is meaningful and neutral. It describes the stimulus without revealing the answer or adding editorial claims.

- Assets MUST use immutable, content-addressed, anonymously accessible HTTPS URLs on the accepted environment. Account-bound Google Drive, expiring testing links and unapproved external Mathpix links are prohibited.

- Release verifies anonymous HTTP 200, correct MIME, nonzero bytes, exact byte length, SHA-256, crop semantics and workbook reference parity.

- The asset importer is idempotent. Re-running it validates and reuses identical content rather than producing duplicate or drifting URLs.

## 36. Rendering, readback and learner-facing parity

1. Generate the workbook from the canonical accepted model.

2. Read the written XLSX back through an independent parser and compare every populated field, type, number, formula, list token and identity.

3. Render every data-bearing sheet/panel through the target or parity renderer.

4. Visually and semantically inspect wrapping, HTML breaks, KaTeX, tables, images, options, placeholders, model answers and multipart display.

5. Compare rendered learner-facing content with the canonical question and answer contracts. No clipping, hidden child, duplicate option, broken glyph or missing stimulus may remain.

> **Answer cleanliness gate:** English rubric tags, evaluator commentary and marks must never appear in Answer Display or Answer Explanation. This gate is tested both on workbook cells and on the rendered frontend output.

---

# PART VII: Aegis architecture, review, every-click and recovery contract

The software must enforce the same authority from upload through release and survive interruption without relying on chat memory.

## 37. Semantic intelligence boundary

| Semantic decisions - model/API authority required | Deterministic mechanics - permitted |
| --- | --- |
| Source task membership; Pre/Post; parentage; Topic/Concept boundaries; grade granularity; descriptions; Type/Case/Example meaning; grouping/reuse; polishing equivalence; lane/category; rubric semantics; Open/Specific; crop relevance | Schema/type/enum validation; exact ID existence; hashes/seals; uniqueness; model-declared order; marks arithmetic; transactions; authorization; path containment; syntax parsing; HTML/KaTeX/image fetch; byte/readback comparison |

- Forbidden semantic authorities include regex or keyword vocabularies, filename conventions, heading depth, numbering, source position/proximity, text length, word counts, substring similarity, first/only group, nearest title and local default fallbacks.

- Deterministic extraction may identify candidate spans, but a semantic model grounded in original page/block evidence MUST adjudicate their meaning and ownership.

- If a required semantic model is unavailable, invalid or still dissenting after bounded repair, Aegis emits a blocked diagnostic release. It never publishes a locally inferred result.

## 38. One staged, reviewable pipeline

| Stage | Name | Required output |
| --- | --- | --- |
| 0 | Freeze run | Source files/hashes, board, grade, subject, publication, chapter, adapter, profile, template, source variables, model/prompt hashes. |
| 1 | Ingest and reconstruct | Page/block source document, reading order, tables, figures, math and asset inventory. |
| 2 | Adjudicate source | Source atoms, source tasks, QIDs, parentage, context/media ownership and dispositions. |
| 3 | Build Post curriculum | Grade-aware Topics, Concepts, descriptions, mastery, hubs, Types/Cases/Examples and culminations. |
| 4 | Build Pre closure | Prerequisite atoms, needed-for graph, Pre Concepts and fresh diagnostic blueprint. |
| 5 | Author assessments | Source-only Post items and generated Pre diagnostics with complete context. |
| 6 | Classify and score | Lane, category, cognitive skill, difficulty, marks, duration, Open/Specific, model answer and rubric. |
| 7 | Group and link | Group fingerprint, reuse/create decision, identity, rollups and referential integrity. |
| 8 | Resolve media | Crops, URLs, alt text, hashes and anonymous fetch receipts. |
| 9 | Project workbooks | Four files, exact headers, update flags, pipe lists, HTML/KaTeX and numeric types. |
| 10 | Critic and repair | Independent semantic critics, bounded repairs and re-review of changed dependents. |
| 11 | Readback and render | Independent XLSX readback, target rendering, parity and visual/semantic checks. |
| 12 | Human review and apply | Review accepted release only; atomic import/apply; immutable final package and receipts. |

- No normal build path may write live Concepts, Groups or Questions before explicit review and atomic apply.

- A repair invalidates and re-runs every dependent stage and seal; stale downstream approvals cannot survive a semantic edit.

- Retries are idempotent and resume from the latest valid checkpoint. They MUST NOT duplicate records or restart completed expensive stages without reason.

- All model requests, prompts, provider settings, cache behavior, token usage, cost, time, retries and worker state are recorded accurately. Completion MUST NOT show 100% while work or cost accounting is still moving.

## 39. Canonical persisted objects

```text

RunManifest {
  contract_id, contract_hash, amendment_ids[],
  source_files[{name, sha256, pages}], board, grade, subject, publication,
  chapter_id, chapter_mode, adapter_id, profile_id, template_hash,
  concept_source, question_source, chapter_duration,
  model_provider, model_id, prompt_hashes[], exporter_version
}

SourceTask {
  source_qid, source_atom_ids[], page_anchor, original_text, normalized_text,
  disposition, parentage, shared_context, required_assets[], answer_evidence[]
}

StructuredConcept {
  id, title, description, achieving_mastery, needed_for[],
  activity_info_hub[], types[], cases[], examples[],
  misconceptions[], error_analyses[], source_refs[], critic_receipt
}

RubricCriterion {
  criterion_class,  // English only; null for non-English
  criterion_text, marks, accepted_equivalents[], evidence_refs[]
}

AssessmentCandidate {
  question_label, source_qid, phase, concept_id, group_id, lane, category,
  question, question_text, children[], model_answer, rubric[],
  answer_restriction, cognitive_skill, difficulty, marks, duration, assets[]
}

ReleaseReceipt {
  semantic_hash, workbook_hashes[], asset_manifest_hash,
  critic_results[], validator_results[], readback_hash, render_manifest_hash,
  reviewer_decision, applied_at
}

```

## 40. Review authority and every-click contract

| Visible action | Binding behavior |
| --- | --- |
| Upload source/syllabus | Validate in staging, preserve original bytes/hash, reject poison/partial files before any live replacement. |
| Build Concepts | Creates a staged curriculum release from the canonical source inventory; does not mutate live content. |
| Concept Review | Displays only the staged release, all descriptions, source evidence, changes and critic findings; no competing legacy editor. |
| Build Assessments | Uses the accepted curriculum/source snapshot and complete existing-group evidence; produces staged candidates only. |
| Blueprint create/edit/compile | Versioned cells, counts, marks, durations and constraints with preview before generation. No hidden default blueprint. |
| Question/Rubric Review | Shows question, complete context/media, model answer, answer space, parent/child rubrics, source QID and group decision together. |
| Apply/Import | One authorized atomic transaction; idempotent; exact release ID; rollback/undo receipt; no partial append. |
| Assets/Downloads | Persisted artifacts and public assets are available immediately after successful release. Status prose is not a substitute for files. |
| Run Console / Resume | Shows all concurrent workers, stage, retry, elapsed time, token/cost totals and checkpoint; reconnect resumes the same run. |
| Admin model/prompt/profile | Changes are versioned and apply only to new runs unless an explicit rebuild is requested. Effective hashes appear in each run. |

Every frontend click MUST trace to a real API state transition, authorization rule, persisted object and user-visible result. Dead controls, hidden legacy routes, competing review authorities and disconnected release paths are blockers.

## 41. Recovery and context-window handoff

> **Recovery principle:** No new chat, worker, machine or session may resume Project Aegis from conversational memory alone. It resumes from a sealed checkpoint bundle whose hashes and status are verified first.

### 41.1 Required handoff bundle

| Bundle item | Required content |
| --- | --- |
| `CONTRACT.pdf` / contract hash | Exact governing contract and amendments. |
| `RUN_MANIFEST.json` | Frozen run identity, profile, source values, model/prompt/template/exporter hashes. |
| `DECISION_LEDGER.md` | Accepted, rejected, superseded and unresolved decisions with authority and rationale. |
| `SOURCE_REGISTER.json` | File names, hashes, page counts and access/review status. |
| `SOURCE_INVENTORY.json` | All atoms, source QIDs, parentage, context/media and dispositions. |
| `CURRICULUM.json` | Accepted Pre/Post topology, descriptions, mastery, Types/Cases/Examples and links. |
| `ASSESSMENTS.json` | All Pre/Post questions, groups, model answers, rubrics and source ownership. |
| `ASSET_MANIFEST.json` | Crops, URLs, hashes, alt text and verification receipts. |
| `VALIDATION_REPORT.json` | Every semantic and mechanical gate with blocker status. |
| `STATUS.md` | Completed stages, artifacts ready, current blocker, remaining work and exact next action. |
| Partial/final outputs | Persisted generated files with hashes; never only a promise that they exist. |

### 41.2 Resume rules

1. Verify contract, amendment, source, profile, prompt/model, template and latest checkpoint hashes.

2. Read `STATUS.md` and the decision ledger before generating or editing anything.

3. Reuse completed sealed stages. Re-run only the invalidated stage and its dependents.

4. Do not reinterpret an already accepted decision unless a higher-authority amendment or new source evidence explicitly invalidates it.

5. When a context limit, interruption or transfer is foreseeable, emit and persist the handoff bundle before stopping. A silent partial state is a failure.

6. When the user asks for completed outputs, return the persisted ready artifacts immediately and clearly distinguish any blocked remainder. Never replace delivery with repeated status messages.

---

# PART VIII: Quality gates, release, amendment and adoption

Aegis releases only after complete semantic, workbook, visual and recovery evidence passes.

## 42. Mechanical release gates

1. Exact sheet set/order, header fingerprint, geometry, capacity and field uniqueness.

2. All five update fields exact `No` on every authored row.

3. Pipe-delimited lists parse to the expected ordered identities with no empty/dangling token.

4. Unique, stable and byte-resolving Chapter/Topic/Concept/Group/Question identities.

5. Correct lane, exact controlled values and category/profile compatibility.

6. Objective option/key/weight/HTML projection and label-free explanation prefix.

7. Subjective placeholder/answer/display/weight parity; True/False on Subjective.

8. Descriptive display/explanation equality; complete parent and child blocks.

9. English tags present in every English textual rubric criterion and absent from every prohibited field/non-English rubric.

10. Numeric storage, `0.##` display, 0.5/1 rubric increments and exact arithmetic.

11. Valid HTML breaks, KaTeX, arrays, Equation/Image typed-cell syntax and frontend rendering.

12. Anonymous image fetch, MIME, bytes, hash, crop and alt-text validation.

13. Independent XLSX readback and render comparison with zero material discrepancy.

## 43. Semantic release gates

| Gate | PASS condition |
| --- | --- |
| Source accounting | Every source atom disposed; every source task has one QID/owner; Post row set equals accepted source occurrences; zero invented Post rows. |
| Pre boundary | Every Pre atom is necessary/prior/non-leaking; every Pre Concept has mastery and diagnostics; no source-task paraphrase. |
| Topology | Grade-aware, source-grounded Topics/Concepts; complete descriptions; no duplicate mastery; required English culminations and Detailed Analysis. |
| Atomicity | Every container adjudicated; no split true multipart, over-bundled independent items or duplicated parent/children. |
| Question fidelity | Complete context/media; polishing equivalent; intended answer and response mode preserved. |
| Grouping | One coherent primary group; correct reuse/create decision; exact rollups; no empty group shell. |
| Rubric and answer space | Bidirectional demand/model/rubric coverage; no filler; correct Open/Specific; English tag semantics accurate. |
| Subject accuracy | Answers, examples, formulas, units, scientific claims and literary interpretation are correct at the accepted grade scope. |
| Review | All critical critic findings resolved; no semantic warning remains if it could affect content, scoring, import, display or provenance. |

## 44. Release package and immutable evidence

- A final release contains the four workbooks, run manifest, contract/amendments, source register, source disposition inventory, semantic QA, workbook readback QA, render manifest/contact sheets, asset manifest/live verification and a concise release summary.

- Every artifact has a SHA-256 and exact version. The release records how many files, sheets, questions and assets were checked, but those counts are evidence for that run, not future quotas.

- The package is immutable after acceptance. Any content change creates a new release version and invalidates dependent hashes and receipts.

- Apply/import is transactional and idempotent. A partial live state, missing file link or output known only to a worker is not a release.

- Release requires zero blockers. Advisory findings may remain only when they cannot affect semantics, scoring, import, rendering, security, provenance or recovery.

## 45. Regression corpus and accepted calibrations

The following chapters and artifacts form a regression corpus because they expose different topology, question, table, media, grammar and source-accounting demands. Their exact counts are fixture assertions only when the fixture source/version is unchanged.

| Fixture | Primary regression purpose |
| --- | --- |
| A Water Drop! - English poetry; v1.4 structural output | Pipe delimiter, HTML breaks, update flags, Subjective True/False, expanded culminations, Detailed Analysis, multipart/photo task, English tag-only amendment and label-free Objective explanation correction. |
| Radha's Letter to Mowgli - English functional/expository language | Independent grammar enumerations, one bank-governed integrated task, composition, dictionary groups, Pre boundary and source-only inventory. |
| Love for One's Motherland / The School Bell Rings Again | English literary topology, grade-aware descriptions and reviewer-guide behavior. |
| Fractions / Three-Dimensional Shapes | Mathematical Types/Cases/Examples, equation rendering, tables, working and exact rubric arithmetic. |
| Measurement / Properties of Substances / Disaster Management / Characteristics of Living Organisms | Science source tasks, procedures, visuals, comparisons, descriptions, classification and subject-profile coverage. |
| Six-chapter 24-workbook release + QA evidence | Cross-file identity, 72-sheet readback/render pipeline, assets and release receipts. |

> **Calibration is not authority:** Observed defects in any calibration remain defects. In particular, tag-free English rubric criteria and Objective explanations that still begin with an option label are superseded by this v2.0 contract.

## 46. Amendment, non-waiver and adoption

- A contract change requires an amendment ID, effective date, exact old rule, exact new rule, authority, affected stages/fields, migration impact and regression tests.

- A later amendment supersedes only the conflicting rule. Compatible prior decisions and accepted evidence remain in force.

- A successful past import, manual approval or passing old validator does not waive a current requirement.

- No prompt, code deploy, template edit or admin setting may silently alter the effective contract. The run manifest must reference the controlling contract and amendments by hash.

- This v2.0 document is adopted as the final governing baseline for subsequent Project Aegis reconstruction, manual generation and implementation until the user explicitly amends it.

| Adoption field | Value |
| --- | --- |
| Contract | Project Aegis Master Governing Contract v2.0 |
| Document ID | AEGIS-MGC-2.0-20260904 |
| Effective date | 4 September 2026 |
| Final special amendment | English rubric tags only; never in Answer Display or Answer Explanation |
| Release stance | Zero blockers / fail closed |
| Status | Final governing baseline |

---

# PART APPENDIX A: Reconstructed decision capture register

A compact audit trail of the user inputs recovered across the Project Aegis workstream.

## A.1 Binding decision register

| ID | Area | Binding decision | Status |
| --- | --- | --- | --- |
| A-001 | Authority | Latest user correction supersedes prior request, sample, SOP, prompt, validator and code. | ACTIVE |
| A-002 | Source | Current Concept and Question source values are exact `Balbharati`. | ACTIVE |
| A-003 | Four outputs | Generate Pre Concept, Pre Master, Post Concept and Post Master for every chapter. | ACTIVE |
| A-004 | All sheets | Sheet order is Objective, Descriptive, Subjective; unused sheets are header-only. | ACTIVE |
| A-005 | Post membership | Only source-present learner tasks enter Post; embedded asks are retained and refined. | ACTIVE |
| A-006 | Post exclusions | No generated, inferred, quota-filling, enrichment or concept-derived Post questions. | ACTIVE |
| A-007 | Pre boundary | Only necessary earlier-grade/year fundamentals; no current chapter content or task paraphrase. | ACTIVE |
| A-008 | Pre closure | Every Pre Concept has Mastery, needed-for evidence and diagnostic coverage. | ACTIVE |
| A-009 | Topic formation | Follow source meaning and grade-aware granularity, not context length or heading count. | ACTIVE |
| A-010 | Descriptions | Chapter, Topic, Concept and Group descriptions are substantive and well written. | ACTIVE |
| A-011 | Concept detail | Include Description, Mastery and all applicable Hubs, Types, Cases, Examples, misconceptions/error analysis. | ACTIVE |
| A-012 | Culminations | English literary Topics and Detailed Analysis require source-grounded culmination behavior. | ACTIVE |
| A-013 | English topology | Poem/prose/play/language adapters and Detailed Analysis dimensions are explicit. | ACTIVE |
| A-014 | Grouping | Group by shared capability, givens, response and rubric signature; similar definitions stay together. | ACTIVE |
| A-015 | Labels | Question, Group, Concept and Topic labels use frozen embedded codes and resolve byte-for-byte. | ACTIVE |
| A-016 | Delimiter | All multi-value fields use exact pipe separator ` \| `. | ACTIVE |
| A-017 | Comma | Commas are allowed inside names/prose and are not list separators. | ACTIVE |
| A-018 | HTML | Use `<br>` line breaks inside workbook rich-text cells; no literal `\n`. | ACTIVE |
| A-019 | KaTeX | Body math wrapped; Equation cells raw; stacked fractions/tables/units render correctly. | ACTIVE |
| A-020 | Update schema | Five `is_update_*` columns present; every data row uses exact `No` in all five. | ACTIVE |
| A-021 | Numbers | Durations, marks and weights are numeric; half-step capable; number format `0.##`. | ACTIVE |
| A-022 | True/False | Route to Subjective with one deterministic placeholder-bound answer. | ACTIVE |
| A-023 | Objective display | `question_text` includes all lowercase-labelled options on `<br>` lines. | ACTIVE |
| A-024 | Objective explanation | No correct option number/letter; begin with exact correct answer text and rationale. | ACTIVE |
| A-025 | Subjective | Placeholder tokens/answers/display/weights are complete and deterministic. | ACTIVE |
| A-026 | Descriptive | Answer Display and Answer Explanation are identical complete model answers. | ACTIVE |
| A-027 | Multipart | Complete `question_text` includes all children; parent and child scoring fields both populated. | ACTIVE |
| A-028 | Enumeration | End-exercise a/b/c or i/ii/iii items are separate when independently answerable. | ACTIVE |
| A-029 | Passage task | Shared passage/context stays with dependent child questions as one true multipart item. | ACTIVE |
| A-030 | Rubric atoms | Question-specific, non-overlapping criteria; weights only 0.5 or 1; no single vague 4-mark criterion. | ACTIVE |
| A-031 | English tags | Approved tags required only in English Descriptive rubric criteria. | ACTIVE |
| A-032 | Tag exclusion | No tag in Answer Display, Answer Explanation, any model answer or non-rubric field. | ACTIVE |
| A-033 | Tag exclusion by subject | No English bracket tags in non-English rubrics. | ACTIVE |
| A-034 | Open/Specific | Whole-item semantic verdict, no command-word/regex default, no third value. | ACTIVE |
| A-035 | Assets | Essential visuals embedded, tight, public, anonymous, content-addressed and hash verified. | ACTIVE |
| A-036 | Tables | Complete KaTeX array or one faithful full crop; no fragmented/Markdown table. | ACTIVE |
| A-037 | Semantic AI | API/model intelligence decides all educational meaning; deterministic code validates mechanics only. | ACTIVE |
| A-038 | No regex semantics | No regex/keyword/position/filename/count fallback may publish a semantic result. | ACTIVE |
| A-039 | Review | Critical QA findings block; staged review precedes atomic apply. | ACTIVE |
| A-040 | Existing groups | Reuse/create decision uses complete evidence and may not be discarded by local code. | ACTIVE |
| A-041 | Blueprint | Versioned create/edit/compile/preview/generate/review/apply flow; no hidden default. | ACTIVE |
| A-042 | Readback | Independent workbook readback, render and visual inspection before delivery. | ACTIVE |
| A-043 | Delivery | Persist and send completed artifacts; status messages do not replace output files. | ACTIVE |
| A-044 | Recovery | Every handoff uses sealed manifests, ledgers, inventories, hashes, status and partial/final artifacts. | ACTIVE |
| A-045 | Resume | Never resume from chat memory alone; verify checkpoint and reuse sealed completed stages. | ACTIVE |
| A-046 | Accounting | Tokens, cost, elapsed time, retries and worker completion are captured accurately. | ACTIVE |
| A-047 | No quota | No fixed Topic/Concept/question target controls semantic output. | ACTIVE |
| A-048 | Zero blockers | No release with unresolved source, semantic, scoring, schema, asset, relational or recovery blocker. | ACTIVE |

---

# PART APPENDIX B: Field, delimiter and update matrix

Exact workbook projections that validators must enforce.

## B.1 Multi-value fields

| Field | Separator | Validation |
| --- | --- | --- |
| `pre_topics` / `post_topics` | ` \| ` | Every listed Topic exists once in the matching phase. |
| `topic_concept_labels` / `related_topics` | ` \| ` | Every Concept/Topic identity resolves byte-for-byte; no phase drift. |
| `keywords` / `digicards` / `related_concepts` | ` \| ` | Trimmed, ordered, no empty or duplicate tokens. |
| `basic_groups` / `intermediate_groups` / `advanced_groups` | ` \| ` | Every Group exists exactly once and tier matches BG/IG/AG. |
| `concept_question_labels` / `group_question_labels` | ` \| ` | Every Question exists in the correct Concept/Group and only once. |
| `related_digicards` / profile multi-source lists | ` \| ` | Exact list semantics; scalar source fields remain scalar. |

## B.2 Question-field behavior

| Field family | Objective | Subjective | Descriptive |
| --- | --- | --- | --- |
| `question` | Stem only | Stem with `$$a$$` etc. | Stem/shared context only |
| `question_text` | Stem + all lowercase options with `<br>` | Visible blank/answer rendering | Complete item; all labelled children |
| Answer cells | Option content + Yes/No + weights | Accepted answers + display Yes + placeholders + weights | Model answer + parent rubric; child blocks when multipart |
| Explanation | Exact correct answer text + rationale; no label | Answer/rationale as appropriate | Byte-equivalent to Display Answer |
| Restriction | Specific | Specific | Open or Specific by semantic verdict |
| English rubric tag | Forbidden | Forbidden | Required in textual rubric criteria only |

## B.3 Header and update assertions

- Every row-2 header is unique and trimmed. Row 1 bands are unchanged.

- Five update fields exist in every sheet profile and contain exact `No` on every data row.

- Unused answer, child and entity cells are blank; no partial answer block is permitted.

- Concept and source fields required by the resolved profile are populated on every applicable row.

- The workbook stores the profile/template ID and full header fingerprint in release evidence.

---

# PART APPENDIX C: English rubric tag registry and containment tests

The final special correction, expressed as deterministic validator rules.

## C.1 Canonical validator

```text

For each populated workbook cell:

IF subject == "English" AND sheet == "Descriptive"
   AND field matches answer_content_[1-30] OR sq[1-15]_keyword_[1-6]
   AND answer_type for that criterion is textual:
       REQUIRE value matches exactly:
       ^\[(content|evidence|reasoning|organisation|language|creativity|accuracy)\]:\s+\S.*$
       REQUIRE exactly one opening tag
       REQUIRE criterion text after tag is specific and credit-bearing
ELSE:
       FORBID any leading or embedded English rubric tag

ALWAYS FORBID tags in:
question, question_text, sub_question_N,
display_answer, answer_explanation,
Objective answer_content_N, Subjective answer_N,
all descriptions, titles, labels, source fields and metadata.

ALWAYS FORBID deprecated [creative].

```

## C.2 Semantic tag selection

| Question demand | Primary criterion tag |
| --- | --- |
| Names, facts, meanings, required points, task fulfilment | `[content]` |
| Text/source detail supporting a response | `[evidence]` |
| Inference, interpretation, explanation, claim-evidence link | `[reasoning]` |
| Sequence, cohesion, format, layout, paragraph/letter/poster organization | `[organisation]` |
| Grammar, expression, vocabulary, sentence mechanics | `[language]` |
| Original development within constraints | `[creativity]` |
| Exact bounded form, punctuation, spelling, transformation or recitation fidelity | `[accuracy]` |

## C.3 Model-answer sanitation tests

- Zero bracket-tag occurrences in `display_answer` and `answer_explanation` for every English row.

- Byte-equivalent Descriptive model answers after HTML normalization.

- No evaluator language such as "award one mark", "criterion", "student identifies" or parenthesized mark labels in model answers.

- Every tagged criterion has a corresponding model-answer/acceptance element, but the tag itself is never copied to the model answer.

---

# PART APPENDIX D: Blocking defect codebook

Stable machine-readable reasons for fail-closed behavior.
| Code | Blocking condition |
| --- | --- |
| GOV-001 | Contract or amendment hash missing/mismatched |
| GOV-002 | Unrecorded conflict or silent rule rollback |
| RUN-001 | Run manifest/profile/template/model/prompt identity incomplete |
| SRC-001 | Source atom lacks disposition |
| SRC-002 | Source task dropped, duplicated or unowned |
| SRC-003 | Generated or inferred Post task |
| SRC-004 | Polishing changes demand/answer space/context/media |
| PRE-001 | Current-chapter teaching/question leaked into Pre |
| PRE-002 | Prerequisite has no named Post need or diagnostic coverage |
| TOP-001 | Wrong Topic/Concept boundary or grade granularity |
| CON-001 | Missing/weak Description or Achieving Mastery |
| CON-002 | Duplicate mastery or orphan Concept |
| CUL-001 | Required English Culmination/Detailed Analysis missing or bare |
| QST-001 | Wrong assessment lane/category/profile |
| QST-002 | Incomplete/non-standalone question_text |
| QST-003 | Objective explanation contains option label/number |
| QST-004 | True/False outside Subjective or malformed slot |
| MPT-001 | True multipart split, independent enumeration over-bundled or child fields incomplete |
| GRP-001 | Wrong/duplicate group, discarded reuse decision or dangling rollup |
| RUB-001 | Missing, vague, duplicated or non-atomic rubric criterion |
| RUB-002 | Marks/weights do not reconcile or invalid criterion quantum |
| RUB-003 | English rubric tag missing/invalid/deprecated |
| RUB-004 | Rubric tag leaks into model answer or prohibited field |
| ANS-001 | Descriptive Display Answer/Explanation mismatch |
| ANS-002 | Incorrect, incomplete or unsupported model answer |
| OSP-001 | Wrong Open/Specific verdict or unsupported default |
| DEL-001 | Wrong list delimiter, empty token or comma-split behavior |
| UPD-001 | Missing update field or value other than exact No |
| ID-001 | Duplicate, colliding or unresolved identity/reference |
| NUM-001 | Numeric text, bad format, invalid duration or arithmetic |
| SCH-001 | Header/geometry/capacity/template fingerprint mismatch |
| HTML-001 | Literal \n, malformed HTML or unapproved markup |
| KTX-001 | Invalid/unsupported KaTeX or wrong typed Equation syntax |
| TBL-001 | Incomplete/reordered table or broken matching structure |
| IMG-001 | Missing/wrong crop, inaccessible URL, MIME/hash/alt mismatch |
| ARC-001 | Semantic result produced by forbidden deterministic fallback |
| REV-001 | Critical critic finding unresolved or stale approval |
| REL-001 | Non-atomic/partial apply, missing artifact or unverified readback |
| REC-001 | Missing/stale handoff bundle or resume from conversational memory |
| ACC-001 | Cost/time/retry/worker completion accounting inaccurate |

---

# PART APPENDIX E: Evidence and calibration register

Files and artifacts used to reconstruct the governing baseline.

## E.1 Governing and audit documents

| Artifact | Evidence status | Use |
| --- | --- | --- |
| Project_Aegis_Uniform_Manual_Authoring_Contract.pdf (v1.2, version 3) | D | Primary prior contract; source-only Post, prerequisite closure, update-aware schema, profile and release gates. |
| Aegis_Four_Output_Golden_Contract_v1.md | D | Earlier unified authoring baseline and documented contradictions. |
| Project_Aegis_Output_Retrospective_Audit.pdf | D | Observed workbook and implementation failures, including historic rubric-tag behavior. |
| Aegis_End_to_End_Audit_2026-08-20.pdf | I | Architecture, semantic-boundary, review/apply, grouping, UI and operational findings. |

## E.2 Guides and reference attachments

| Artifact | Evidence status | Use |
| --- | --- | --- |
| AegisEnglishReviewerGuideGrade6MSBSHSE(1).pdf | I | English literary topology, levels, answer restriction, marks and reviewer behavior. |
| SOP_Bulk_Import_Fill_Guide.docx | I | Hierarchy, fields, answer blocks, multipart structure and import QC; superseded where later decisions differ. |
| Requried File_ Instructions - Google Docs.pdf | I | Marks/rubric decomposition and subject-specific reference behavior. |
| Rubrics- Open_ Specific.xlsx | I | Dedicated Open/Specific calibration and controlled answer-space evidence. |
| Concept Mapping Audit-20260827T193738Z-1-001(1).zip | I/Q | Corrected output, error and concept-mapping evidence referenced by later audits. |

## E.3 Source chapter corpus

| Source file | Role |
| --- | --- |
| CH01_The_School_Bell_Rings_Again_06_MSBSHSE(1).pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH01_Characteristics_of_Living_Organisms_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH01_Three_Dimensional_Shapes_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH04_Love_for_Ones_Motherland_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH05_Radhas_Letter_to_Mowgli_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH06_A_Water_Drop_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH02_Measurement_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH05_Disaster_Management_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH06_Fractions_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |
| CH06_Properties_of_Substances_06_MSBSHSE.pdf | Source/calibration corpus; each future run re-inventories the exact bytes and does not inherit counts blindly. |

## E.4 Generated output and QA evidence

| Artifact | Evidence status | Use |
| --- | --- | --- |
| Project_Aegis_First_Chapter_Outputs.zip | I/Q | Early four-output baseline and failure evidence. |
| Project_Aegis_Balbharati_Grade6_Three_Chapters_12_XLSX.zip | D | Three-chapter output calibration. |
| Project_Aegis_Grade6_Six_Chapters_24_Workbooks_v1.2.zip | D | Six chapters x four outputs; cross-file schema and output corpus. |
| Project_Aegis_Grade6_QA_Evidence_v1.2.zip | D | Release summary, semantic audits, readback, render, source disposition and asset receipts. |
| Grade6_English_Chapter06_A_Water_Drop_v1.4.zip and four XLSX files | D | Latest approved English structural calibration: pipe lists, HTML breaks, update flags, Subjective True/False and expanded culminations. |
| Project_Aegis_Grade6_Fly_Asset_Import_v1.2.tar.gz | I/Q | Content-addressed asset import and anonymous verification workflow. |

*D = direct artifact inspection in this reconstruction; I = indexed content/metadata review; Q = validated derivative evidence. These labels prevent overclaiming raw access.*

---

# PART APPENDIX F: Copy-paste execution directive

A condensed governing prompt for an independent Project Aegis run. The full contract remains controlling.

## F.1 Independent-run directive

```text

You are executing Project Aegis under Master Governing Contract v2.0 (AEGIS-MGC-2.0-20260904). Do not merely summarize the contract. Enforce it.

AUTHORITY
1. Latest explicit user amendment.
2. Master Governing Contract v2.0 and amendment ledger.
3. Complete uploaded source chapter.
4. Accepted error/correction evidence and reviewer findings.
5. Accepted calibration outputs/QA.
6. Guides, SOPs and profiles.
7. Existing prompts, validators and code.
Record every conflict; never silently fall back.

FREEZE THE RUN
Before semantic work, freeze source file hashes/pages; board, grade, subject, publication, chapter mode and adapter; profile/template/header fingerprint; concept_source and question_source; chapter duration; model/provider/prompt hashes; exporter version; and contract hash.

SEMANTIC BOUNDARY
Educational meaning is decided by API/model intelligence grounded in source page/block evidence. Regex, keywords, filename, heading depth, numbering, proximity, length, counts, first match and local defaults may never publish a semantic decision. Deterministic code validates schema, types, IDs, arithmetic, syntax, hashes, transactions, authorization, rendering and bytes. If semantic adjudication fails, emit a blocked diagnostic release.

SOURCE AND POST
Inventory every source atom and assign task/context/support/not_task/pointer_alias/unresolved_reference. Mint ordered source QIDs. Post Master contains exactly explicit source-present learner tasks, including embedded asks. Preserve repeated occurrences. Do not create generated, inferred, enrichment, quota-filling or concept-derived Post questions. Resolve every source occurrence once. Polishing may repair clarity/self-containment but must preserve demand, scope, response mode, answer space and media.

PRE
Reverse-map essential earlier-grade/year prerequisites for every Post capability. Accept only necessary, prior-boundary, non-leaking atoms. Author fresh diagnostics; never copy or paraphrase current chapter questions. Every Pre Concept has Description, Mastery, needed-for links and complete diagnostic coverage. No quotas.

CURRICULUM
Build grade-aware Topics and independently teachable Concepts from meaning, not pages or counts. Every Concept has substantive Description and distinct Achieving Mastery, plus all applicable whole Hubs, operational Types, bounded Cases, complete Examples, misconceptions/error analyses and source-grounded Culminations. English literature uses stanza/episode/scene Topics, Topic culminations and a final Detailed Analysis Topic with only supported dimensions and a final culmination.

FOUR OUTPUTS
Create 01 Pre Concept, 02 Pre Master, 03 Post Concept, 04 Post Master from one accepted snapshot. Sheet order is Objective, Descriptive, Subjective. Concept files contain hierarchy-only rows on the accepted carrier sheet. Master rows carry Chapter+Topic+Concept+Group+Question. Unused sheets are header-only. Trust unique row-2 headers, not letters.

SCHEMA
Use accepted update-aware profiles (72 Objective, 380 standard Descriptive, 149 Subjective, 440 English Post Descriptive when explicitly resolved). All five is_update fields are exact No on every data row. Store all numbers as real numeric values with 0.## display. Freeze and validate the exact header fingerprint.

IDENTITY AND LISTS
Use stable coded Chapter/Topic/Concept/Group/Question labels and byte-exact references. Group name and display name match the canonical ID; group description states the semantic family. Every multi-value field uses exact delimiter space-pipe-space: ` | `. Commas are allowed inside names. No empty, duplicate or dangling list item.

HTML AND MATH
Use `<br>` and `<br><br>` in workbook rich text; never literal backslash-n. Body mathematics uses `[Katex]...[/Katex]`; typed Equation cells contain raw supported LaTeX. Use stacked fractions, complete arrays, correct units/exponents and target KaTeX validation. Essential visuals use tight complete public content-addressed HTTPS assets with neutral alt text and anonymous HTTP/MIME/bytes/hash/crop verification.

ATOMICITY AND LANES
Typography does not decide parentage. Independent exercise items become separate rows. True multipart stays one Descriptive row: parent question is shared context; complete question_text contains all labelled children; structured child fields also contain each child; parent and child scoring projections are complete, equivalent and non-additive.
Objective: stem-only question; question_text contains lowercase a), b), c) options on `<br>` lines; exactly one key; correct weight=marks; distractors=0. answer_explanation begins with exact correct answer text and rationale, with no option letter/number.
Subjective: deterministic `$$a$$`, `$$b$$` placeholders; accepted answers; display Yes; weights; placeholders; Specific. Every True or False item is Subjective with one slot.
Descriptive: complete model answer; display_answer equals answer_explanation; full main rubric; child blocks when multipart; Open or Specific by whole-item semantics.

RUBRICS
Map every demand to one observable, question-specific, non-overlapping criterion. Use only 0.5 or 1 per criterion. No vague filler, repeated credit or single undivided four-mark criterion. Model answer and rubric coverage are bidirectional.
FINAL ENGLISH TAG RULE: Every populated English Descriptive textual parent answer_content_N and child sqM_keyword_N criterion begins with exactly one of `[content]:`, `[evidence]:`, `[reasoning]:`, `[organisation]:`, `[language]:`, `[creativity]:`, `[accuracy]:`. `[creative]` is invalid. Tags classify criteria and do not earn marks. Tags are forbidden in display_answer, answer_explanation, questions, options, Subjective answers, descriptions, metadata and every non-English rubric.

OPEN/SPECIFIC
Exactly Open or Specific from the full stimulus, question_text, children, model answer and unweighted rubric. No command-word, category, marks, sheet or regex default. Objective and valid Subjective are Specific; Descriptive may be either.

GROUPS
Group by complete capability/response/rubric/media fingerprint. Similar definition prompts share a group. Different answer space, method, rubric or media separates groups. Each question has one primary group. Reuse an existing group only from a frozen evidence snapshot and recorded semantic decision; otherwise create a new key. Local code may not override the decision.

PIPELINE AND REVIEW
Use one staged pipeline: freeze -> ingest -> source inventory -> Post curriculum -> Pre closure -> assessments -> classify/score -> group/link -> assets -> workbooks -> independent critics/repair -> readback/render -> human review -> atomic apply/release. Do not write live state before review. Repairs invalidate dependent seals. Retries are idempotent. Record accurate tokens, cost, time, retries and worker state.

RECOVERY
At every stage boundary and before interruption, persist contract/hash, run manifest, decision ledger, source register/inventory, curriculum, assessments, asset manifest, validation report, STATUS.md and all partial/final outputs with hashes. Resume only after verifying the bundle. Never resume from chat memory alone.

RELEASE
Run mechanical and semantic gates. Read back and render all data-bearing sheets. Zero blockers are required. Deliver persisted artifacts immediately; status text is not an output. Final package includes four workbooks and all source, semantic, readback, render, asset and release receipts.

```

## F.2 Final acceptance sentence

> **Acceptance:** A Project Aegis release is accepted only when the complete source, curriculum, questions, groups, model answers, rubrics, English tag containment, workbook fields, assets, UI state, readback/render evidence and recovery bundle all agree under the same contract hash with zero blockers.

---
