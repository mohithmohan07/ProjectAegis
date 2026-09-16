#!/usr/bin/env python3
"""Export only the validated public incident schema to stdout.

--backfill-existing is an explicit maintenance operation: read failed jobs and
save private historical reports once. It never modifies jobs or starts work.
Normal pagination is read-only and needs no database session.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--backfill-existing", action="store_true")
    args = parser.parse_args()
    try:
        from app.services import failure_reports
        if args.backfill_existing:
            from app.db import SessionLocal
            with SessionLocal() as db:
                outcome = failure_reports.backfill_existing(db)
            if outcome["unavailable"]:
                raise ValueError("Some saved failures could not be captured")
            if set(outcome) != {"captured", "already_captured", "unavailable"} or any(type(value) is not int or value < 0 for value in outcome.values()):
                raise ValueError("Invalid backfill result")
            print(json.dumps(outcome, sort_keys=True))
            return 0
        envelope = failure_reports.export_public_page(cursor=args.cursor, limit=args.limit)
    except Exception:
        # Errors may mention a private path or record. Never echo them to an
        # SSH collector whose stdout/stderr could enter public Actions logs.
        print("Failure report export unavailable; inspect private server diagnostics", file=sys.stderr)
        return 1
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
