# Phase 3: API-First Universal Semantic Graph

## Purpose

Phase 3 replaces the split source architecture in which ACSD controlled task
identity and rich-media mechanics while raw Mathpix MMD still controlled
semantic concept extraction, topic alignment, learner analysis, and Type
classification. The new contract is:

> OpenAI interprets textbook meaning inside bounded source identities;
> deterministic code preserves evidence, order, exact wording, rendering, and
> final coverage.

The original PDF remains the ultimate audit authority. Mathpix MMD, the PDF text
layer, page images, and GPT page extraction are evidence streams. None of them is
allowed to become an unaudited replacement source by itself.

## Source evidence channels

Phase 3 can combine four channels:

| Evidence channel | Primary use |
| --- | --- |
| Original PDF rendering | Layout, visual relationships, page order, and final verification |
| PDF text layer | Searchable visible text and page-local retrieval |
| Mathpix MMD | Mathematics, tables, dense transcription, and source image URLs |
| GPT page ACSD | Semantic block roles, hierarchy, task boundaries, and bounded recovery |

The canonical graph records the evidence used for each accepted source object.
A disagreement does not trigger a free-form rewrite. It creates a bounded source
fusion packet and remains `review_required` until the selected original-PDF
block is independently verified.

## Universal graph identities

The graph uses stable opaque identities rather than visible headings as keys:

- `TOPIC-####`
- `TOPIC-####-SUB-###`
- `BLK-#####`
- `TASK-#####`
- `QINV-####`
- `FIG-#####`
- `IMG-#####`
- `MATH-#####`

Visible labels remain display data. Therefore publisher variations such as
`2.1`, `II-A`, `Lesson 2`, or an unnumbered heading do not change the academic
identity once the semantic hierarchy has been verified.

## API and deterministic boundaries

### OpenAI is used for

- classifying chapter headings, main topics, subtopics, activities, exercises,
  source extracts, glossaries, boxes, and editorial labels;
- independently criticising the proposed hierarchy;
- selecting one already-verified PDF block for a bounded converter anomaly;
- independently verifying that selection;
- grounding concepts to valid source block IDs;
- reconciling a missing source-grounded concept host for a Type assignment unit;
- semantic Type-to-concept selection among supplied legal IDs.

API responses use strict JSON Schema. Models may return only supplied opaque IDs
or fields explicitly allowed by the schema. They cannot reorder pages, create
QIDs, invent Figure URLs, write final rich-text wrappers, or bypass validation.

### Deterministic code is used for

- page, block, task, and topic order;
- source spans and hashes;
- stable ID construction;
- QID inventory and exact task wording;
- Figure ownership and source-owned assets;
- KaTeX and image rendering;
- duplicate and missing-Example detection;
- checkpoint compatibility;
- graph referential integrity;
- final workbook rendering and deposit gates.

Regex is retained for syntax detection and candidate discovery only. It may
identify a possible heading number, URL, LaTeX command, image tag, or list
marker. It does not decide pedagogical hierarchy, concept granularity, task
ownership, or Type scope.

## Topic and subtopic graph

Every semantic topic retains physical source order. A subtopic points to its
canonical main-topic ancestor. Activities, `Discuss` labels, source boxes,
glossaries, projects, and review sections retain their own roles instead of
being promoted into teaching topics.

For the RNE source, this restores the omitted main topic:

```text
TOPIC-0002  The Making of Nationalism in Europe
  TOPIC-0002-SUB-001  The Aristocracy and the New Middle Class
  TOPIC-0002-SUB-002  What did Liberal Nationalism Stand for?
  TOPIC-0002-SUB-003  A New Conservatism after 1815
  TOPIC-0002-SUB-004  The Revolutionaries
```

The graph no longer relies on an exact match between a generated
`topic_match_hint` and a concept's visible topic string.

## Source-grounded concept generation

Concept generation consumes topic packets derived from the graph rather than
arbitrary flat MMD chunks. Each concept retains:

- canonical topic ID;
- applicable subtopic IDs;
- supporting source block IDs;
- graph contract hash;
- pedagogical title, description, mastery statement, and later learner analysis.

A concept without valid source evidence cannot pass the final graph gate.
Generator, critic, and reconciler passes may improve pedagogical granularity,
but deterministic validation checks identity and evidence after every pass.

## QID-derived Type scope

A Type's structural scope is computed through:

```text
Type or Case
  -> source QIDs
  -> canonical tasks
  -> source blocks
  -> subtopics
  -> canonical main-topic ancestors
```

`topic_match_hint` is retained only as human-readable metadata.

Cases keep their own source scope. A Type containing Cases from different topics
is not flattened onto the first topic. A genuinely multi-topic Case is marked
`cross_topic_synthesis` and can be placed only on an eligible culmination that
represents the source topics.

## Missing-host preflight and fail-soft allocation

Before learner analysis, Phase 3 checks whether every assignment unit has a
legal host. If a topic has source-grounded Type units but no normal concept, a
bounded API call may create the smallest necessary concept using only supplied
source block IDs and assignment-unit IDs. An independent critic verifies it.

Allocation is fail-soft:

1. resolvable units are assigned and checkpointed;
2. unresolved units enter bounded reconciliation;
3. only the affected topic and dependent stages are invalidated;
4. final deposit remains fail-closed until every required unit is resolved.

One unresolved Type therefore cannot discard unrelated verified work, but it
also cannot be silently placed under a semantically convenient wrong topic.

## Structured content and rendering

Concepts, Types, Cases, Examples, Activities, Figures, and mathematics remain
structured objects until the final renderer. The model does not author the
final `concept_details` blob.

The deterministic renderer alone creates:

- `[Katex] ... [/Katex]` wrappers;
- `[img ...]` tags;
- Type and Case numbering;
- exact Example text retrieved by QID;
- Activity/Info Hub sections.

This prevents raw `\\captionsetup`, copied Mathpix figure environments,
paraphrased questions, duplicated Examples, and mismatched image captions from
surviving into deposit.

## Source fusion and visual table cells

Converter-specific semantic markup such as `<smiles>`, `<chem>`, `<reaction>`,
`<structure>`, or `<mathml>` outside a valid subject context is treated as an
anomaly, not accepted text. The model may select only an existing verified PDF
page block. It cannot write replacement content.

Verified page tables may use `[[VISUAL:n]]` placeholders linked to a precise
PDF crop. Aegis materialises the crop as a source-owned asset and replaces the
placeholder deterministically with the canonical image tag while preserving the
table structure.

## Subject adapters

The graph schema is universal, while subject adapters guide semantic review:

- Mathematics
- Physics and Chemistry
- Life Science
- Environmental Science
- Social Science
- Language and Literature
- Computer Science and Artificial Intelligence
- Electronics
- Commerce
- Health and Physical Education
- Arts
- General Science
- General

Specialised subjects are resolved before generic `Science`, preventing names
such as `Computer Science`, `Social Science`, and `Life Science` from being
misclassified as physical science.

## Checkpoint migration and resumption

A checkpoint is reusable only when its immutable source identity remains valid.
Phase 3 annotates reusable inventory and mined Types with graph IDs, then
invalidates only stages that depended on the old topic-string topology.

New generation requires an API-classified and independently verified graph. A
matching cached verified graph is reused without another model call only when
both the complete source contract and the selected board, grade, subject, unit,
chapter, and learning-kind context match.

Legacy checkpoint recovery preserves completed concept work, the exact QID
inventory, and mined Types, but it never waives source verification. If no
matching API-classified and independently verified graph is cached, Phase 3 runs
one bounded hierarchy verification before resuming the checkpoint. An ambiguous
legacy source is blocked rather than being resumed on a guessed topology. Source
anomalies always remain fail-closed and require original-document evidence.

For the RNE 81% checkpoint, the intended boundary is:

- preserve the canonical concept snapshot;
- preserve the exact QID inventory;
- preserve mined reusable Types;
- rebuild Type scope and placement from graph identities;
- regenerate dependent learner analysis only when its host topology changes.

## Artifacts

Phase 3 publishes:

| Artifact | Purpose |
| --- | --- |
| `source.semantic-graph.json` | Machine-readable universal source graph |
| `source.semantic-graph-report.json` | Identity, evidence, ordering, and fusion audit |
| `source.semantic.mmd` | Deterministic graph-controlled semantic source |
| `source.phase3-page-acsd.json` | Verified page/block evidence ledger when available |

The immutable raw MMD and existing ACSD artifacts remain available for audit.

## Validation and quality gates

A graph is rejected for any of the following:

- unknown topic, subtopic, block, task, QID, or Figure reference;
- topic, block, or task order drift;
- duplicated source ownership;
- stale graph contract after ACSD or verified-overlay changes;
- invalid source-override hashes;
- unresolved converter anomalies;
- unsupported concepts or missing concept evidence;
- Types without canonical source scope or a legal host;
- missing or duplicate Example inventory;
- malformed KaTeX or image tags;
- raw layout LaTeX in rendered output.

Release acceptance spans History, Mathematics, Physics, Chemistry, Biology,
Language, Computer Science, non-Latin sources, headingless chapters, and
publisher-variant structures.

## Rollout and rollback

Phase 3 artifacts can be compiled alongside earlier ACSD artifacts. Generation
switches to the semantic graph only when the graph is `ready`, its contract hash
matches the current canonical source, and all source-critical gates pass.

Rollback does not mutate the PDF or raw MMD. Disabling Phase 3 returns generation
to the previous source path while retaining the graph artifacts for diagnosis.
No Phase 3 recovery workflow, embedded payload, or temporary branch machinery is
part of the production runtime.

## Release verification

The final merge gate must run on a head that contains the materialised Phase 3
source files and no `.phase3` payloads, temporary materialisation workflows, or
modified CI transport jobs. Release evidence consists of the complete backend
test suite, the frontend production build, the complete frontend test suite, and
a final PR-diff audit on that same clean head. A transport or extraction run is
not release evidence.
