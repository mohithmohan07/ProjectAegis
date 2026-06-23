# Syllabus structure workbooks

Place the following Excel files in this folder. On startup, Aegis loads
unit/chapter shells (no concepts or questions) when the database is empty.

| File | Board(s) |
|------|----------|
| `Unit-Chapter List_ CBSE.xlsx` | CBSE |
| `Unit-Chapter List_ ICSE.xlsx` | ICSE |
| `Maharashtra Board Chapter List.xlsx` | Maharashtra |
| `Kstate Syllabus Grade 6-10.xlsx` | Karnataka |
| `English Language Units and Chapters.xlsx` | All boards (universal) |

## Expected columns

The importer auto-detects headers. Typical columns:

- **Grade** / Class / Standard
- **Subject**
- **Unit**
- **Chapter**

English Language is replicated across CBSE, ICSE, Maharashtra, and Karnataka.

## Manual import

```bash
cd backend
python scripts/import_syllabus.py
# or import a single file:
python scripts/import_syllabus.py "/path/to/Unit-Chapter List_ CBSE.xlsx" --board CBSE
```

Names are normalized to Title Case with trimmed spacing on import.
