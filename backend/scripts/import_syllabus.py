#!/usr/bin/env python3
"""Import unit/chapter syllabus workbooks into the database."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services import syllabus_import as svc  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(
        description="Import unit/chapter syllabus Excel files into Aegis.",
    )
    p.add_argument(
        "files",
        nargs="*",
        help="Optional .xlsx paths (default: load all files from data/syllabus/)",
    )
    p.add_argument(
        "--board",
        default="",
        help="Default board when importing a single file (e.g. CBSE)",
    )
    p.add_argument(
        "--subject",
        default="",
        help="Default subject when importing a single file",
    )
    p.add_argument(
        "--universal",
        action="store_true",
        help="Replicate rows across all boards (for English Language)",
    )
    args = p.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if not args.files:
            result = svc.load_all_syllabus_files(db)
        else:
            all_rows = []
            for fp in args.files:
                path = Path(fp)
                rows = svc.parse_workbook(
                    path,
                    default_board=args.board,
                    default_subject=args.subject,
                    universal_boards=svc.ALL_SYLLABUS_BOARDS if args.universal else None,
                )
                all_rows.extend(rows)
            result = svc.upsert_chapters(db, all_rows)
            result["loaded_files"] = [str(f) for f in args.files]

        print("Syllabus import complete:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        if result.get("missing_files"):
            print(f"\nPlace missing files in: {config.SYLLABUS_DIR}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
