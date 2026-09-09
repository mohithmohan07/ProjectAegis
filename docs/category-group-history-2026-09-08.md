# Assessment field labels: history and restoration

The owner approved the end-to-end audit recommendations, then clarified:
the deterministic part is the **field value**, such as `Fill in the blanks`,
not deciding what category a question belongs to. Category, difficulty,
answer-space and tier meaning remain API judgments. An explicit blueprint
continues to carry its already-decided cell. No difficulty-to-tier lookup
has been restored.

## Evidence from prior PRs

| Evidence | What it establishes |
|---|---|
| [PR #24](https://github.com/mohithmohan07/ProjectAegis/pull/24) | The older generator had a controlled category vocabulary plus a `canonical_category()` helper. It also contained content checks and broad category-family rewriting. Those semantic shortcuts are not restored. |
| [PR #233](https://github.com/mohithmohan07/ProjectAegis/pull/233), [9d148ea](https://github.com/mohithmohan07/ProjectAegis/commit/9d148ea04db1eba197d543e6ca71925d14b02be1) | The owner review found `MCQ`, `Multiple Choice` and `Multiple Choice Question` on sibling rows. The fix supplied exact per-sheet category values to both cell authors and enum-gated their responses. This gate still exists; the audit did not establish that a later commit removed it. |
| [PR #269](https://github.com/mohithmohan07/ProjectAegis/pull/269), [7754fb7](https://github.com/mohithmohan07/ProjectAegis/commit/7754fb7838e8a64e1f8a147651f3a6b274c1aae2) | The Question Duration Matrix added exact board/subject category labels and their marks/minutes contracts. This retained different presentation spellings across profile families: Mathematics `Fill in the blanks`, English/generic `Fill in the Blanks`, and other explicitly named variants below. The underlying local format and time policies are preserved. |
| [PR #289](https://github.com/mohithmohan07/ProjectAegis/pull/289), Q28 | A new task-specific category such as `Rearrange the following words` is not an automatic formatting repair. The profile must explicitly permit it. Exact profile vocabulary is still the boundary. |

The confirmed issue is presentation drift between declared profile vocabularies,
not evidence that question content should be classified by keywords or that
Basic/Intermediate/Advanced should be derived from difficulty. Claims about a
single unspecified PR breaking all label validation would overstate the evidence.

## Fresh-run vocabulary

`assessment_output_vocabulary.py` contains only this exact presentation map:

| Previously declared value | Current serialized value | Basis |
|---|---|---|
| `Fill in the Blanks` | `Fill in the blanks` | Owner's example and existing Mathematics profile literal. |
| `Assertion & Reasons Type` | `Assertion & Reasons` | Existing generic CMS literal documented by PR #233. |
| `True or False` | `True/False` | Existing generic CMS literal documented by PR #233. This does not change its Subjective sheet ownership. |
| `Extract Based Question` | `Extract Based Questions` | Existing generic CMS literal documented by PR #233. |

Every other declared label remains exact. In particular, `Very Short Answer`
and `Very Short Answer Questions` retain their distinct declarations, and
`Short Answer Type (2 Marks)` and `Short Answer Type (3 Marks)` never collapse.
The helper does not strip marks, infer synonyms, casefold arbitrary values,
read question content, assign missing values, or expand a profile's categories.
Unknown strings remain visible for the existing exact per-sheet validator.
Colliding aliases in a configured policy are named configuration errors.

Group labels are exactly **Basic**, **Intermediate**, **Advanced**. An invalid
spelling is a schema defect, not permission to reinterpret the tier. The level
checker now also rejects surrounding whitespace instead of accepting it and
later emitting a malformed label. Qualitative grade-relative anchors are
shared by the level author and critic; they explicitly prohibit a lookup from
marks, Bloom, length, step count or blueprint difficulty. The existing 5 Basic
+ 5 Intermediate Pre coverage policy is unchanged.

At `resolve_for_metadata`, a new run snapshots its presentation vocabulary and
its selected complete format policy, including every marks/duration rule.
Authors, validators and serializers use that same carried authority. A frozen
legacy profile without the new vocabulary retains its earlier spelling. A
same-run resume retains the frozen policy even if default declarations change.
Completing previously unknown metadata may select the newly established profile;
retargeting already-known selectors remains refused.

Strict workbook import accepts the documented old and current presentation
labels without changing the imported category. A new regression also exposed
a separate existing defect: the importer did not implement the declared
`marks_matrix` duration mode; the direct release seal had the same gap.
Both now validate that exact marks × difficulty
table, including English one-/four-mark blanks, and still refuses mismatched
marks or minutes.

Direct blueprint/candidate validation also uses the new run's exact category
set. The release seal compares the same mechanically projected aliases as the
workbook without mutating the input payload; its hashes continue to identify
the original sealed payload. Omitting a payload format policy cannot bypass a
new run profile's category contract. Legacy frozen releases retain their
earlier literals and validation path.

If no exact local profile matches, the policy explicitly identifies generic
API calibration. Marks and question duration in minutes are decided from the
task, learner grade and response demand. No sample school's matrix is borrowed,
and the separate chapter-duration contract remains in force.

## Ratified spreadsheet interpretations

Q35 approves these interpretations of the conflicting example cells. They
are now named in the fresh column policy and authoring discipline:

| Field | Interpretation |
|---|---|
| Topic/concept identities | Unique lane-qualified `ChapterBaseID_TNN` and `TopicID_CNN`; decorated titles, plain display names, exact decorated roster references. Existing persisted IDs are retained. |
| Subjective blank | Placeholder `a`, stored token `$$a$$`, learner rendering `____`; further placeholders follow in order. |
| Objective weights | The correct option carries the accepted item marks; distractors carry zero. A one-mark local profile remains one mark. |
| `post_topics` | Post topic roster. The spreadsheet wording referring to Pre topics is a typo. |
| Pre boundary | Earlier grade/year foundations. Being in an earlier chapter of the same grade alone is insufficient. Capture, consolidation, map and source-recap claim instructions now agree; unsupported provenance is reported rather than invented. |

The Pre Refiner receives each mapped prerequisite's ID and complete captured
text through `prerequisite_evidence`, using already-redacted map records.
Current-chapter source questions, raw MMD and the finished Post map remain
outside that payload. This supplies the relevant evidence without weakening
the existing source-question leakage boundary.

## Grouping evidence and validation

Level, variant-cluster and group-description decisions bind relevant member
stimuli and home-concept visuals before decision identity is computed. Each
author and independent critic receives the bound pixels through the shared
visual adapter. Unavailable figures create explicit review flags; no model
can claim visual verification from a URL alone. The three advisory critic
responses also opt into the shared exact verdict/confidence/issues schema;
its identity rides the decision payload and semantic review remains intact.

Focused regressions cover cross-subject labels with unchanged local rules,
frozen-profile replay, explicit generic calibration, distinct mark-tagged
categories, unknown-label refusal, independent tier decisions, all six image
transport adapters, missing-image flags and the source-neutral Pre Refiner
payload. They do not claim a paid end-to-end model run or downstream live
evaluation measurement.
