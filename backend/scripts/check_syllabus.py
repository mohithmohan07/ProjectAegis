#!/usr/bin/env python3
"""Verify expected syllabus workbooks are present."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.services.syllabus_import import SYLLABUS_FILES, _discover_workbooks, _missing_expected_files  # noqa: E402


def main() -> int:
    found = _discover_workbooks()
    missing = _missing_expected_files()

    print("Syllabus workbook directories:")
    for d in config.syllabus_workbook_dirs():
        print(f"  {d}")
    print(f"\nFound {len(found)} workbook(s):")
    for p in found:
        print(f"  ✓ {p.name}")

    if missing:
        print(f"\nMissing {len(missing)} expected file(s):")
        for name in missing:
            print(f"  ✗ {name}")
        print(f"\nCopy your Excel files into:\n  {config.BUNDLED_SYLLABUS_DIR}")
        print("\nExpected files:")
        for name in SYLLABUS_FILES.values():
            print(f"  - {name}")
        return 1

    print("\nAll expected syllabus workbooks are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
