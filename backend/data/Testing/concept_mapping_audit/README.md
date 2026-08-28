# 2026-08-27 Concept-mapping audit corpus

These sixteen workbooks are verbatim copies of the reviewer-supplied
"Original Outputs" and "Corrected Outputs" for:

- Grade 6 MSBSHSE English, Chapter 2, *Self Help is the Only Way*; and
- Grade 6 MSBSHSE Mathematics, Chapter 3, *In the World of Numbers*.

They are evidence fixtures, not literal format authorities.  The supplied
files contain mutually incompatible schemas and a small number of corrections
that contradict the source PDF or the accompanying error log.  Regression
tests therefore preserve the files byte-for-byte and compare Aegis with the
following **corrected-intent** normalization.

## Authority order

1. The original chapter PDF and its correction/error log.
2. A reviewer correction that is consistent across the supplied files.
3. The committed MES workbook format for mechanics the audit does not change.

## Normalized output contract

- Concept files keep the clean MES schema and sheet order:
  `Objective` (67 columns), `Descriptive` (374), `Subjective` (144).
- Master files use the same `Objective`, `Descriptive`, `Subjective` order and
  add the five entity update columns plus Descriptive `concept_source`:
  72, 380, and 149 columns respectively.
- English Post Master expands Descriptive from 10 to 30 main-rubric slots,
  making that sheet 440 columns.
- Every populated Chapter, Topic, and Concept band carries `No` in its update
  column.  Group and Question update cells carry `No` only on rows where that
  band is populated; concept-only rows leave those two cells blank.
- Descriptive `concept_source` is `Balbharati` when the concept has that source.
- Chapter duration and description are one shared value across the four files
  for a lane pair.  For the Mathematics audit, the logged authority is 362
  minutes; the isolated 480-minute cell is not copied.
- Mathematics category spelling and duration use the exact log taxonomy and
  matrix.  English uses the corrected English taxonomy.
- Question-label aggregates and BG/IG/AG rollups are rebuilt from surviving
  questions and occupied groups.  Deleted questions and empty group shells do
  not remain in aggregates.
- Functional rubric tags require the colon (`[content]:`, `[language]:`, and
  so on), and answer restrictions use the canonical `Open`/`Specific` wire.
- Topic/concept/question identities remain unique and source-aligned.  The
  corrected English `Best_Help` substitution and the five-way Post topic-ID
  collision are not treated as intended output.
- Header newlines, duplicate `chapter_display_name`/`sq15_keyword_6` columns,
  missing `chapter_duration`, nonstandard sheet order, and styled empty tail
  rows/columns are fixture defects and are removed by normalization.

The acceptance harness traverses the union of every original/corrected pair's
logical rows and columns, then checks every logical data row and every field in
the normalized schema.  It also separately pins all raw file hashes so an
evidence fixture cannot change unnoticed.

## SHA-256

| Fixture | SHA-256 |
|---|---|
| `english_post_concept_original.xlsx` | `b2b268648d18d4544d97c610a5f72869254ba4b721f421fba4e2160b629b959c` |
| `english_post_concept.xlsx` | `9b2eba7458927851698b4b4fcdde4b2490b38643aa9db996934d8e1d87e10a05` |
| `english_post_master_original.xlsx` | `9afd438ec1ee24a5ed586e6357066a63dcd5f15e33424fcc5f72df575dd0c1e5` |
| `english_post_master.xlsx` | `7c96d40927c05ac025b7ac9e57848d200610387ad7ff2cda9dcdd0b18cdfb6db` |
| `english_pre_concept_original.xlsx` | `ad19e404ce9b61cb8c5f74d036019ec944ae9bf11509f243d0d5900c477d3755` |
| `english_pre_concept.xlsx` | `a50c5dc2b0393da58f6ee8fc94cb03b85d0fdbdcc0813d42f57512f81058b6a8` |
| `english_pre_master_original.xlsx` | `44e8b7033a6b647c00c086d7cd60369fa70449088346dac19999658d2e4c92ba` |
| `english_pre_master.xlsx` | `a9f7deddacaea53293bfeb10a9662377019c46efb635beef21593a46cf1e014b` |
| `math_post_concept_original.xlsx` | `adcd5b05a47dd60fbe8e1540273fa8bbdede0c6bbfef28ba2969ac42a31a07d8` |
| `math_post_concept.xlsx` | `e940d16bfd972e5bd842e43542e8c211feeaa1a39bf8fdd907734fb9e5b006ae` |
| `math_post_master_original.xlsx` | `252c31be2ae8b794e38d8355782ccc6de5726eeb7374e23a7877c4559832f1da` |
| `math_post_master.xlsx` | `045257aad750cc8e80b3d1401238d2102f1d825ce11e53b6a433918b6021d198` |
| `math_pre_concept_original.xlsx` | `421c3ebc72f65241efa30cd98fc39b1eec28d266e74cf98b9445e100eaa56388` |
| `math_pre_concept.xlsx` | `81e409b8b4a5b81ae8fcd1e1e72d6fd88a9eb3eac6b5db8cec30f9560bc074c3` |
| `math_pre_master_original.xlsx` | `5475c56db37addabe9c8ed79f6a34f2dd12f8926f68f0dd9b9c11bc8348f2262` |
| `math_pre_master.xlsx` | `93f3311bc6b41ddac51b00fe8bde73f4956a73bfc5314b18c7ece1825165caa1` |
