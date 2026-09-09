# Source corpus review: 8 September 2026

The 14 uploaded PDFs contain **209 PDF pages** across Mathematics, Science,
English, History and Social Science. They demonstrate why Aegis needs a common
output contract with subject-sensitive teaching and evaluation. English rubric
tags belong only in English; an explanation, diagram, proof or historical argument
still needs an appropriate rubric in every other subject.

This review inspected the text layer of every file and rendered representative
pages from every file. All 17 pages of the image-only Social Science scan were
visually inspected. Page references below are **1-based PDF page numbers**, not
printed book page numbers. This is source review and a proposed acceptance corpus,
not evidence that these uploads have completed a live Aegis run or that their
images have reached Fly.

The [manifest](../backend/tests/fixtures/aegis_source_corpus/manifest.json) records
the exact filename, SHA-256, page count, metadata evidence, reviewed visual pages,
observations and acceptance obligations. Source PDFs and extracted textbook text
are not copied into Git. No topic count, concept count, task count or semantic
classification rule is inferred from page counts or typography.

## Findings that change the review plan

1. **A filename is not a reliable chapter boundary or identity.** `English Ch1.pdf`
   contains *How I Taught My Grandmother to Read* with associated work on pages
   1-22 and *Bharat Our Land* with associated work on pages 23-32. The uploaded
   Electricity filename says Chapter 5; its first page prints Chapter 11.
   `Social Ch1.pdf` identifies *Understanding Social Science*, Chapter 1, and its
   footers explicitly show Class 9. Other grades are left unconfirmed where the
   reviewed source pages do not establish them.
2. **An empty text layer can accompany a readable source.** All 17 pages of
   `Social Ch1.pdf` are scans without selectable text. Its illustrations,
   headings, questions and answers are visually readable. This source needs the
   configured PDF conversion/vision route, not an empty-content conclusion.
3. **Some tasks need material outside the upload.** The listening tasks in
   `English Ch1.pdf` pages 17 and 30 refer to teacher transcripts on printed
   pages 259 and 260. Those pages are absent. Exact heard words and speaker
   assignments cannot be truthfully authored from this upload alone. The task
   and missing dependency must remain visible in the evidence and output review.
4. **Source evidence can contain inconsistencies.** In *In the World of Numbers*,
   page 5 says that zero has no opposite number. That conflicts with zero being
   its own additive inverse. In *Let's Separate the Components*, page 5
   distinguishes sedimentation from decantation, while the summary on page 7
   combines their names for settling. Preserve the source record and have an
   independent subject review decide how to explain or flag the discrepancy;
   do not silently reproduce an error as new teaching or silently edit the source.
5. **Figure labels alone cannot resolve ownership.** In `RNE.pdf` page 22, an
   activity references Fig. 17 alongside the caption for Fig. 18. Context, the
   depicted subject and the adjacent evidence must inform the model's decision.
   A figure-number mismatch is a source finding, not permission to discard the task.
6. **Printed answers must remain evidence, not leak into stems.** The Social
   Science scan places answer keys between exercise sections, then repeats a
   separate competency practice set and worksheet. The converter and inventory
   must preserve their associations without treating answer lines as learner
   questions or joining separate sets just because numbering restarts.

## Coverage across the 14 uploads

| Source | Pages | Observed structure and acceptance focus |
| --- | ---: | --- |
| `RNE.pdf` | 26 | *The Rise of Nationalism in Europe*: topic/subtopic hierarchy, source quotations, historical maps, allegorical images, captions, sideboxes and activities. Review pages 4, 22 and 26 for visual ownership and evidence-based historical rubrics. |
| `jemh105 (1).pdf` | 24 | *Arithmetic Progressions*: definitions, worked examples, nth term, sums and optional exercises. Pages 10 and 22 test subscripts, derivations and cross-page diagram context for semicircles, stacked logs and a potato race. |
| `English Ch1.pdf` | 32 | Two literary units, embedded comprehension, grammar, vocabulary, formal letter, poetry devices and listening/speaking tasks. Pages 9, 24 and 31 were rendered; pages 17 and 30 identify missing transcripts. |
| `Social Ch1.pdf` | 17 | *Understanding Social Science*, Class 9: scan-only pages, numbered teaching sections, glossary, answer keys, assertion/reason, passage and picture questions, projects and a separate worksheet. All pages were rendered and inspected. |
| `Class 10 Chapter 5 Electricity.pdf` | 24 | Printed Chapter 11: circuit diagrams, symbols, graph, resistivity, series/parallel circuits, heating and power. Pages 6 and 23 test equations, units, graph axes and an exercise data table. Some repeated text-layer headings do not appear repeated on the rendered pages. |
| `Chapter_06_Triangles.pdf` | 26 | Similarity, theorems, constructions, proofs, worked examples and diagrams. Pages 8 and 23 test proof dependencies, correspondence order and several subfigures sharing a figure number. |
| `CH01_Characteristics_of_Living_Organisms_06_MSBSHSE(2).pdf` | 9 | Narrative introduction, observations, life processes, germination, movement and life cycle. Pages 4 and 8 show embedded photographs, a germination sequence and an excretion diagram. The final exercise includes sequence ordering and explanations. |
| `CH02_Measurement_06_MSBSHSE(2).pdf` | 11 | Everyday measurement, rulers, thermometers, units and procedures. Pages 3 and 10 test scale readability, instrument selection and mixed exercise formats. |
| `CH03_Lets_Separate_the_Components_06_MSBSHSE(1).pdf` | 9 | Investigation, separation methods, apparatus and procedural photographs. Pages 4 and 9 test image sequences, word-search positions and a triangular puzzle with rotated labels. |
| `CH01_Three_Dimensional_Shapes_06_MSBSHSE(3).pdf` | 8 | Object classification, properties and construction activities. Pages 4 and 7 test labelled faces/edges/vertices, missing-answer cells and the prism/pyramid comparison table. |
| `CH02_Lines_and_Angles_06_MSBSHSE(3).pdf` | 9 | Lines, rays, points and angles, naming tables, fan/clock activities and shared diagrams. Pages 5 and 9 test arrowheads, vertex labels and multi-part figure questions. |
| `CH03_In_the_World_of_Numbers_06_MSBSHSE(3).pdf` | 9 | Number systems, Indian place values, signed counters, number lines, stairs and a partially filled magic square. Pages 5 and 8 test visual models and the noted source-content discrepancy. |
| `CH01_The_School_Bell_Rings_Again_06_MSBSHSE(3).pdf` | 2 | Short Grade Six poem, vocabulary, page-spanning MCQs, personal response, rhyming words and alliteration. Both pages test appropriate granularity without padding the concept count. |
| `CH02_Self_Help_is_the_Only_Way_06_MSBSHSE(4).pdf` | 3 | Fable, dialogue, sequencing, character interpretation, alternative ending, articles and poster writing. Pages 2 and 3 test the boundary between literary comprehension, grammar and creative tasks. |

## What to verify through the run

| Stage | Evidence required from this corpus |
| --- | --- |
| Phase 01: ingestion and conversion | The actual uploaded file hash and complete page inventory; source-derived metadata; readable conversion for the Social scan; intact tables, diagrams, glyphs and text order; explicit unavailable dependencies. A successful text extraction alone is insufficient. |
| Phase 02: source structure and task inventory | An evidence-backed disposition for teaching text, headings, examples, activities, questions, printed answers, sideboxes and facilitator material. Preserve repeated numbering scopes, parent/child links, passage context and image ownership. API author and independent critic judge meaning. |
| Topic absorption | Follow the actual book's hierarchy and the chosen source units. Do not turn every activity banner into a topic, import headings from another edition, or let the first literary title consume the later poem. Keep cross-topic evidence without duplicating question identities. |
| Concept breakdown | Explain a teachable capability and its prerequisites. Distinguish proof from application, grammar from literary interpretation, mechanism from method selection, and observation from recall where justified by the source. Short poems and long chapters should both remain proportionate, without fixed concept quotas. |
| Concept detailing | Write original, connected teaching with definitions, why/how, conditions, worked or textual examples, and meaningful mastery evidence. Keep poem/quotation context, mathematical correspondence and scientific units intact. An image must support the explanation and be cited from the correct evidence. |
| Assets and KaTeX | Demonstrate source crop identity, retained labels, upload success, stable application-owned Fly URL, and successful retrieval from the exported workbook. Render the actual exported text to check fractions, subscripts, Greek letters, units, angle marks and deliberate line breaks. Verify map legends, apparatus scales and multi-panel diagrams at a usable resolution. |
| Assessment and rubric authoring | Match the response mode to the task; solve independently; preserve source question wording under the governing contract. Keep learner model answer and evaluator criteria distinct. Give each criterion observable evidence, acceptable variation, explicit partial-credit meaning and its weight. |
| Final workbook and release | Read all four outputs back: identities, topics/concepts, source labels, image URLs, KaTeX, question/answer projections, rubric totals and parent/child representation. Export the evidence and visible findings even when a separate database-release gate cannot pass. |

These are acceptance observations for a model-reviewed corpus, not new production
heuristics. Schema, hashes, page bounds, identity, arithmetic and URL retrieval can
be checked mechanically; the meaning of a source, a concept boundary or a valid
answer requires the API author and independent critic.

## Rubric checks that matter for evaluation

- **Mathematics:** award the demonstrated method, relationships and justified
  calculation. For geometry, the correct correspondence and reason for a theorem
  application matter; a final claim repeated as a second criterion must not earn
  duplicate credit. A valid alternative proof or calculation should remain eligible.
- **Science:** specify the observation, principle, mechanism, calculation or unit
  needed by the task. Instrument choice and measurement method are different
  assessable demands. A description of a diagram must preserve the relevant
  topology, scale or process order.
- **History and Social Science:** accept defensible wording while requiring the
  relevant cause, comparison, example, source interpretation or relationship.
  A long answer should earn credit for distinct supported points rather than
  similarity to one paragraph or use of stock phrases.
- **English:** use the active English-only rubric registry according to demand.
  Comprehension needs accurate or supported interpretation; grammar needs the
  relevant language evidence; alternative endings and letters need task fulfilment,
  coherence and appropriate expression. Creative writing may have several valid
  answers. No English bracket tags should appear in other subjects.

For each pilot, include a small evaluator trial with a fully correct answer, a
valid paraphrase or alternative method, a partly correct answer and an answer that
sounds plausible but misses the demand. API review should explain the awarded
criteria. Passing workbook arithmetic does not establish evaluation quality.

## First live pilots and remaining coverage

Run these four first, with complete phase evidence and all four exported files:

1. `Social Ch1.pdf`: scan ingestion, printed answers, source questions and visual
   ownership in a subject outside the two original column examples.
2. `English Ch1.pdf`: two literary units, grammar and poetry, English rubric
   dimensions, rich text and an honest missing-transcript outcome.
3. `Chapter_06_Triangles.pdf`: mathematical source fidelity, KaTeX, complex figure
   ownership, proof teaching and evaluable criteria.
4. `Class 10 Chapter 5 Electricity.pdf`: scientific equations and units, circuits,
   data tables, graph interpretation and the metadata mismatch.

Then prioritize `RNE.pdf` for historical maps and allegories, Arithmetic
Progressions for cross-page diagram applications, and the short Grade Six sources
for proportional concept detail and embedded activities. The first four pilots
do not establish coverage for maps, rotated puzzle labels or every lower-grade
exercise style.

No Aegis step was removed for this review. Before proposing a removal, collect
the stage's actual purpose, evidence consumed/produced, overlap with another stage,
failure examples from these sources, and measured run cost. Present the proposed
change and replacement coverage to the owner before making it.

## Verification status

Completed: file identity/page census, all-file text-layer inspection, named visual
page review, source findings and the checked-in manifest. The scan-only source was
reviewed visually in full. Grades absent from reviewed page evidence remain
unconfirmed.

Not established by this work: complete semantic extraction of every task, live
Phase 01-through-release success, Fly upload or URL durability, all-output visual
rendering, or evaluator scoring quality. These require the proposed live pilots;
the offline manifest deliberately records `not_run_in_this_review` for each PDF.
