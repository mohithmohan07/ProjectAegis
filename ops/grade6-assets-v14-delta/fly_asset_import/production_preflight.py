#!/usr/bin/env python3
"""Read-only preflight for the Project Aegis volume-bearing Fly machine."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from app import config
from app.services import source_asset_store


EXPECTED_DATA_DIR = Path("/data")
EXPECTED_STORE = EXPECTED_DATA_DIR / source_asset_store.STORE_DIRNAME
MINIMUM_FREE_BYTES = 1024 * 1024


def _is_mountpoint(path: Path) -> bool:
    expected = str(path.resolve())
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return path.is_mount()
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and fields[4].replace("\\040", " ") == expected:
            return True
    return False


def main() -> int:
    configured_data_dir = Path(config.DATA_DIR).resolve()
    store = source_asset_store.store_root().resolve()
    free_bytes = shutil.disk_usage(EXPECTED_DATA_DIR).free
    checks = {
        "configured_data_dir_is_data": configured_data_dir == EXPECTED_DATA_DIR,
        "data_is_mountpoint": _is_mountpoint(EXPECTED_DATA_DIR),
        "store_path_is_expected": store == EXPECTED_STORE,
        "store_exists": store.is_dir(),
        "store_is_writable": os.access(store, os.W_OK),
        "minimum_free_bytes": free_bytes >= MINIMUM_FREE_BYTES,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    report = {
        "status": "PASS" if not failed else "FAIL",
        "configured_data_dir": str(configured_data_dir),
        "store_path": str(store),
        "free_bytes": free_bytes,
        "checks": checks,
        "failed": failed,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
