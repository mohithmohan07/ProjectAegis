#!/usr/bin/env python3
"""Clear all Aegis data for a fresh start."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.data_reset import reset_all  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Reset Aegis to an empty database.")
    p.add_argument(
        "--keep-seed",
        action="store_true",
        help="Keep bulk_import_database.xlsx (demo seed will reload on restart).",
    )
    args = p.parse_args()
    result = reset_all(keep_seed=args.keep_seed)
    print("Reset complete:")
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
