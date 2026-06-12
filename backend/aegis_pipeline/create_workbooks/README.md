# Workbook Generator

Detailed NCERT chapter revision workbooks built by combining
**GPT-5.4 mini** content authoring with **Python + ReportLab** layout.

## Design philosophy

Every chapter reads like premium hand-written study notes — not a
collection of cards, not a wall of plain tables. Each section opens
with a short overview, then mixes the most appropriate of these
content blocks (chosen by the model based on what suits the topic):

| Block | When it's used |
|-------|----------------|
| **paragraph** | Concept explanations, context, reasoning. |
| **bullets** | Lists of properties, exceptions, subtypes, etc. |
| **table** | Comparisons, classifications, organelle vs function pairs. |
| **flowchart** | Mechanisms, life cycles, step-by-step processes. Drawn as boxed steps with arrows. |
| **definitions** | Term + definition pairs for terminology-dense topics. |
| **callout** | Exam alerts, common mistakes, mnemonics, examples. |
| **worked\_example** | Numerical / math problems with given / find / solution steps. |

The PDF is strictly A4 portrait, with consistent 18 mm margins,
header rule and footer page numbers. Long sections naturally flow
across pages; tables split with their headers repeated.

## Setup

```powershell
cd workbook
pip install -r requirements.txt
$env:OPENAI_API_KEY = "your-key"
```

## Generate a chapter

```powershell
cd workbook\src
python cli.py --from-path `
  --source-pdf "C:\Users\FCI\OneDrive\Desktop\Books\Class 09\CBSE_NCERT_G09_Science\CBSE_NCERT_G09_CH02_CELL_THE_BUILDING_BLOCK_OF_LIFE.pdf"
```

## Where outputs go

- **Real runs** publish to the library tree, named after the source chapter:
  `C:\Users\FCI\OneDrive\Desktop\Books\Workbooks\Class NN\<Subject>\<source-stem>.pdf`
  (e.g. `…\Workbooks\Class 09\English\CBSE_NCERT_G09_CH04_VITAMIN-M.pdf`).
- **Samples** — add `--sample` to write to `workbook/output/` instead, leaving the
  published library untouched:

  ```powershell
  python cli.py --from-path --sample --source-pdf "…\CBSE_NCERT_G09_CH04_VITAMIN-M.pdf"
  ```

- `--output-pdf <path>` still overrides the destination explicitly.

The published root is `PUBLISH_ROOT` in `src/config.py`.

## Project layout

```
workbook/
├── requirements.txt
└── src/
    ├── cli.py            # argparse entry point
    ├── pipeline.py       # extract → GPT → refine → render → validate
    ├── config.py         # paths, model, margins
    ├── extract.py        # PyMuPDF source-PDF extractor
    ├── metadata.py       # parse subject/grade/chapter from filename
    ├── schema.py         # Chapter / Section / Block dataclasses
    ├── gpt_writer.py     # OpenAI prompt + JSON parsing
    ├── refiner.py        # text clipping, gap filling, A4-safe limits
    ├── styles.py         # palette + ParagraphStyle definitions
    ├── flowchart.py      # custom Flowable: boxed step flowchart
    ├── blocks.py         # block-type renderers
    ├── document.py       # SimpleDocTemplate + page decoration
    └── validate.py       # PyMuPDF post-build checks
```

Default model: `gpt-5.4-mini-2026-03-17`.
