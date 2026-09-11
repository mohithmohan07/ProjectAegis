# Approved question vocabulary

Owner decision Q45, 11 September 2026. The source is the attached workbook
`CMS clean-up: Finalised Question Categories_ Sources_ Cognitive Skills`.
Its three sheets define the complete permitted values for these three fields.
These are hard output requirements, not illustrative examples.

## Question categories

Source: `Question Categories!A2:A27`.

| # | Exact output value |
| --- | --- |
| 1 | Match the following Questions |
| 2 | Case Based Questions |
| 3 | Composition Writing |
| 4 | Assertion & Reasons Type |
| 5 | Sentence Transformation |
| 6 | Extract based on Map Survey |
| 7 | Numerical/application based |
| 8 | Long Answer Type (5 Marks) |
| 9 | Locating and Plotting on map |
| 10 | Choose the ODD one Out |
| 11 | Passage based questions |
| 12 | Long Answer Type (4 Marks) |
| 13 | Short Answer Type (2 Marks) |
| 14 | True or False |
| 15 | Error correction |
| 16 | Identifying the following |
| 17 | Fill in the Blanks |
| 18 | Rearrange the following words |
| 19 | Extract based question |
| 20 | Long Answer Type (6 Marks) |
| 21 | Short Answer Type (3 Marks) |
| 22 | Multiple Choice Question |
| 23 | Very Short Answer Questions |
| 24 | Name the following |
| 25 | Read the Explanations and give structure |
| 26 | Reading Comprehension |

The source cell A15 has a trailing tab and A26 has a trailing space. Only those
boundary whitespace artifacts are removed. All wording, internal spacing,
capitalization and punctuation above are preserved.

## Question sources

Source: `Question Sources!A2:A10`.

| # | Exact output value |
| --- | --- |
| 1 | UpSchool DB |
| 2 | Selina |
| 3 | NCERT |
| 4 | NON - NCERT |
| 5 | Oswaal |
| 6 | K State (Extra) |
| 7 | Seed to Plant |
| 8 | Balbharati |
| 9 | RS Aggarwal |

## Cognitive skills

Source: `Cognitive Skills!A2:A7`.

| # | Exact output value |
| --- | --- |
| 1 | Remember |
| 2 | Understand |
| 3 | Apply |
| 4 | Analyse |
| 5 | Evaluate |
| 6 | Create |

## Enforcement

1. Bind the same versioned lists to new run profiles, author/reviewer/Fixer
   payloads and final output checks. The lists are closed: no custom value,
   synonym, abbreviation, alternate capitalization or combined label may be
   written to these fields. Existing immutable releases and sealed policies
   preserve their historical contract.
   The Master path binds its vocabulary when resolving the assessment profile,
   before category authoring. This is separate from the earlier Concept source
   envelope. New standalone assessment generation uses the same current list.
2. The API decides which approved category and cognitive skill the actual task
   supports. Q41 still governs response mechanisms and sheet routing; in
   particular, `True or False` remains Subjective. The category list does not
   require generating every category or making a Grade 01 question more complex.
3. Exact spelling transport for old format configuration may preserve a known
   category's marks/duration rules. It must not infer a new category for a
   question. An unqualified old `Long Answer` cannot be mapped to a particular
   mark-bearing category. An explicit mark-bearing approved category retains
   its stated marks; the API chooses a suitable category for the task.
4. Question source remains provenance, not a model guess or fallback. Generated
   questions use `UpSchool DB`; extracted questions use the recorded publication
   only when it belongs to this list. An unsupported source is unresolved, not
   automatically `NON - NCERT`. This list constrains Question Source, not the
   separate Concept Source field or the immutable source evidence.
   If an existing duplicate question is reused, its already-approved source
   remains attached to that identity; incoming source provenance is retained
   separately in the audit. Never join multiple source labels into this field
   or pick one arbitrarily from a historical combined value.
5. Check populated parent and child question fields, rendered workbook fields
   and publication validation. A semantic review or Fixer flag cannot waive
   membership in the lists. Rejected attempts remain in evidence.
6. If resolution fails, stage the downloadable artifact with the disallowed
   field blank, a visible issue and the original value retained in the issue
   evidence. Do not drop the question, invent a valid-looking replacement,
   publish the record or report a successful release while that defect remains.

This decision does not add values to any other output field or change the
existing four-file schema, source-question wording, model routing, review
stages, source ownership, rubric contracts or explicit upload boundary.
