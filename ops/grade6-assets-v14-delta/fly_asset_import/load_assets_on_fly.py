#!/usr/bin/env python3
"""Validate or apply a prepared source-asset bundle on a Fly Machine.

Dry-run is the default.  ``--apply`` is deliberately required before this
script calls Project Aegis's existing ``source_asset_store.pin_asset()``.
Run it from an environment where the backend ``app`` package and the mounted
``DATA_DIR`` (normally /data) are available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


class LoaderError(RuntimeError):
    """Raised when the staging bundle cannot be trusted or applied."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / "migration-manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LoaderError(f"cannot read valid manifest: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise LoaderError("unsupported migration manifest schema")
    return value


def _validated_assets(bundle: Path, manifest: dict[str, Any]) -> list[tuple[str, bytes]]:
    records = manifest.get("assets")
    if not isinstance(records, list) or not records:
        raise LoaderError("migration manifest has no assets")
    unique: dict[str, bytes] = {}
    for record in records:
        if not isinstance(record, dict):
            raise LoaderError("migration manifest contains an invalid asset record")
        filename = str(record.get("jpeg_filename") or "")
        digest = str(record.get("jpeg_sha256") or "")
        if filename != f"{digest}.jpg" or len(digest) != 64:
            raise LoaderError(f"invalid content-addressed filename: {filename!r}")
        if any(character not in "0123456789abcdef" for character in digest):
            raise LoaderError(f"invalid sha256 in filename: {filename!r}")
        path = bundle / "assets" / filename
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise LoaderError(f"missing staged JPEG: {path}") from exc
        if _sha256(data) != digest:
            raise LoaderError(f"staged JPEG hash mismatch: {filename}")
        if not (data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")):
            raise LoaderError(f"staged file is not JPEG-framed: {filename}")
        expected_size = int(record.get("jpeg_size_bytes") or -1)
        if len(data) != expected_size:
            raise LoaderError(f"staged JPEG size mismatch: {filename}")
        previous = unique.setdefault(filename, data)
        if previous != data:
            raise LoaderError(f"content hash collision: {filename}")
    return sorted(unique.items())


def load_bundle(bundle: Path, *, apply: bool = False) -> dict[str, int | str]:
    bundle = bundle.resolve()
    manifest = _read_manifest(bundle)
    assets = _validated_assets(bundle, manifest)
    target = manifest.get("target")
    if not isinstance(target, dict):
        raise LoaderError("manifest has no target configuration")
    job_id = int(target.get("job_id", -1))
    base_url = str(target.get("public_base_url") or "").rstrip("/")
    if job_id < 0 or not base_url.startswith("https://"):
        raise LoaderError("manifest target is invalid")

    if not apply:
        return {"mode": "dry-run", "validated": len(assets), "pinned": 0}

    try:
        from app.services import source_asset_store
    except ImportError as exc:
        raise LoaderError(
            "Project Aegis backend app is not importable; run with backend on PYTHONPATH"
        ) from exc

    pinned = 0
    for filename, data in assets:
        asset_url = f"{base_url}/source-assets/{job_id}/{filename}"
        returned = source_asset_store.pin_asset(
            data,
            job_id=job_id,
            asset_url=asset_url,
            public_base_url=base_url,
        )
        if returned != filename:
            raise LoaderError(
                f"pin_asset returned {returned!r}, expected {filename!r}"
            )
        stored = source_asset_store.stored_asset_path(filename)
        try:
            stored_data = stored.read_bytes()
        except OSError as exc:
            raise LoaderError(f"pinned asset cannot be read back: {filename}") from exc
        if _sha256(stored_data) != filename.removesuffix(".jpg"):
            raise LoaderError(f"pinned asset failed read-back hash: {filename}")
        pinned += 1
    return {"mode": "apply", "validated": len(assets), "pinned": pinned}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="directory containing migration-manifest.json and assets/",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually call source_asset_store.pin_asset(); default is dry-run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = load_bundle(arguments.bundle, apply=arguments.apply)
    except LoaderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
