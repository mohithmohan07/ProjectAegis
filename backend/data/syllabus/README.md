# Syllabus structure workbooks

Place the five Excel files in this folder **and commit them to git** so they
ship with the app and preload automatically on every deploy.

| File | Board |
|------|-------|
| `Unit-Chapter List_ CBSE.xlsx` | CBSE |
| `Unit-Chapter List_ ICSE.xlsx` | ICSE |
| `Maharashtra Board Chapter List.xlsx` | Maharashtra |
| `Kstate Syllabus Grade 6-10.xlsx` | Karnataka |
| `English Language Units and Chapters.xlsx` | All boards (universal) |

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
