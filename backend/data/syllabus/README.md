# Syllabus structure workbooks

Place the six Excel files in this folder **and commit them to git** so they
ship with the app and preload automatically on every deploy.

| File | Board |
|------|-------|
| `UnitChapter_List__CBSE.xlsx` | CBSE |
| `UnitChapter_List__ICSE.xlsx` | ICSE |
| `UnitChapter_List__Maharashtra_Board.xlsx` | Maharashtra |
| `UnitChapter_List__KSTATE.xlsx` | Karnataka |
| `UnitChapter_List__NCF.xlsx` | NCF, Grades 1–3; publication: Seed to Plant |
| `English_Language_Units_and_Chapters.xlsx` | CBSE, ICSE, Maharashtra and Karnataka |

The NCF workbook is the owner's unmodified `Unit-Chapter List_ NCF(1).xlsx`,
containing 18 chapters across English, Mathematics and Environmental Studies.
SHA-256: `2e644fea147a7cfd547e67affed8d5285b5e77cf5d7fc897f073cba9f6055f59`.
Its Board comes from the filename, grades from the sheet names, and unit/chapter
identity suffixes are removed from readable labels. Supplied spellings remain.
NCF does not inherit the unrelated shared Grades 6–10 English Language rows.

`Seed to Plant` is available in the Source (publication) selector. Publication
remains the value selected for each run, rather than a fixed chapter attribute:
it supplies Concept Source and extracted Post-learning Question Source;
generated Pre-learning Question Source is `UpSchool DB`.

## From Windows (OneDrive)

Copy your files from:

```
C:\Users\FCI\OneDrive\Chapters and Units For CBSE, ICSE, Maharastra Board and KSTATE\
```

Into this folder in the project:

```
backend/data/syllabus/
```

Keep the exact filenames above, then commit and push.

## Verify

```bash
cd backend
python scripts/check_syllabus.py
python scripts/import_syllabus.py
```

## Expected columns

The importer auto-detects headers. Typical columns:

- **Grade** / Class / Standard
- **Subject**
- **Unit**
- **Chapter**

Names are normalized on import: Title Case, trimmed spacing, cleaned punctuation.
English Language is replicated across CBSE, ICSE, Maharashtra, and Karnataka.

## Alternative: upload via UI

On **Build Concepts** step 2 or the **Database** tab, use **Upload syllabus Excel
files** if you prefer not to commit the workbooks to git.
