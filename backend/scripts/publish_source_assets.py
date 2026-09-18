"""Publish reviewed source crops, without deployment, database or model calls.

The manifest declares meaning and placement; this utility validates bytes only.
Job 0 is an explicit manual-publication namespace, not an invented upload job.
The existing public asset route falls back to the content-addressed store.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import urllib.request
import uuid

APP = "projectaegis"
ORIGIN = "https://projectaegis.fly.dev"
STORE = Path("/data/source-asset-store")
MAX_BYTES = 50 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_manifest(manifest: dict) -> list[dict]:
    if manifest.get("schema_version") != 1:
        raise ValueError("unknown asset manifest version")
    rows = manifest.get("assets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("asset manifest must contain reviewed crops")
    seen = set()
    total = 0
    for row in rows:
        sha = row.get("sha256", "")
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("invalid SHA-256")
        if sha in seen:
            raise ValueError("duplicate asset identity")
        seen.add(sha)
        if row.get("filename") != sha + ".jpg":
            raise ValueError("filename must be SHA-256.jpg")
        if type(row.get("bytes")) is not int or row["bytes"] <= 0:
            raise ValueError("invalid byte length")
        if row.get("media_type") != "image/jpeg":
            raise ValueError("only JPEG is accepted by the current public route")
        if not isinstance(row.get("provenance"), dict) or not row["provenance"]:
            raise ValueError("missing source provenance")
        total += row["bytes"]
    if total > MAX_BYTES:
        raise ValueError("asset bundle exceeds 50 MiB transport limit")
    return rows


def validate_bytes(row: dict, data: bytes, *, decode: bool = False) -> None:
    if len(data) != row["bytes"] or digest(data) != row["sha256"]:
        raise ValueError("asset bytes do not match the reviewed manifest")
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise ValueError("asset does not have a complete JPEG signature")
    if decode:
        import fitz  # Already part of the deployed Aegis runtime.
        picture = fitz.Pixmap(data)
        if picture.width <= 0 or picture.height <= 0:
            raise ValueError("empty decoded image")


def pack_manifest(path: Path) -> dict:
    root = path.parent.resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = validate_manifest(manifest)
    package = {"schema_version": 1, "assets": []}
    for row in rows:
        source = root / row["filename"]
        if source.is_symlink() or source.resolve().parent != root:
            raise ValueError("asset path escapes its manifest directory")
        data = source.read_bytes()
        validate_bytes(row, data)
        package["assets"].append({**row, "data_base64": base64.b64encode(data).decode("ascii")})
    return package


def create_immutable(path: Path, data: bytes) -> bool:
    """Install complete bytes without replacing any existing path."""
    if path.is_symlink():
        raise ValueError("refusing symbolic link in asset store")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("existing asset conflicts; no overwrite performed")
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".manual-asset-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError("concurrent asset conflict; no overwrite performed")
            return False
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_package(package: dict, store: Path = STORE) -> dict:
    rows = validate_manifest(package)
    prepared = []
    # Validate the complete batch, including existing targets, before a write.
    for row in rows:
        data = base64.b64decode(row.get("data_base64", ""), validate=True)
        validate_bytes(row, data, decode=True)
        target = store / row["filename"]
        sidecar = target.with_suffix(".json")
        if target.is_symlink() or sidecar.is_symlink():
            raise ValueError("refusing symbolic link in asset store")
        if target.exists() and target.read_bytes() != data:
            raise ValueError("existing asset conflicts; batch not written")
        prepared.append((row, data, target, sidecar))
    if store.is_symlink():
        raise ValueError("refusing symbolic-link store")
    store.mkdir(parents=True, exist_ok=True)
    receipts = []
    for row, data, target, sidecar in prepared:
        created = create_immutable(target, data)
        url = f"{ORIGIN}/source-assets/0/{row['filename']}"
        if not sidecar.exists():
            provenance = {
                "sha256": row["sha256"], "bytes": row["bytes"],
                "media_type": "image/jpeg", "job_id": 0,
                "asset_url": url, "public_base_url": ORIGIN,
                "created_at": time.time(), "origin": "manual-chatgpt-authoring",
                "provenance": row["provenance"],
            }
            try:
                create_immutable(sidecar, json.dumps(provenance, sort_keys=True).encode("utf-8"))
            except ValueError:
                # Existing first-mint provenance is authoritative; retain it.
                if not sidecar.is_file() or sidecar.is_symlink():
                    raise
        receipts.append({"sha256": row["sha256"], "bytes": row["bytes"],
                         "media_type": "image/jpeg", "url": url,
                         "status": "created" if created else "reused"})
    return {"schema_version": 1, "assets": receipts}


def verify_public_assets(rows: list[dict]) -> dict:
    receipts = []
    for row in rows:
        url = f"{ORIGIN}/source-assets/0/{row['filename']}"
        # No credential, cookie, private header or authenticated session.
        with urllib.request.urlopen(url, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("public asset returned a redirect or non-200 status")
            if response.headers.get_content_type() != "image/jpeg":
                raise ValueError("public asset MIME mismatch")
            data = response.read(row["bytes"] + 1)
            validate_bytes(row, data)
            if int(response.headers.get("Content-Length", "-1")) != row["bytes"]:
                raise ValueError("public asset Content-Length mismatch")
        receipts.append({"sha256": row["sha256"], "bytes": row["bytes"],
                         "media_type": "image/jpeg", "url": url, "http_status": 200})
    return {"schema_version": 1, "assets": receipts}


def run_fly(*arguments: str, capture: bool = False) -> str:
    result = subprocess.run(["flyctl", *arguments], check=True, text=True,
                            capture_output=capture, timeout=120)
    return result.stdout if capture else ""


def publish(manifest_path: Path, receipt_path: Path) -> None:
    package = pack_manifest(manifest_path)
    machines = json.loads(run_fly("machine", "list", "-a", APP, "--json", capture=True))
    targets = [m for m in machines if m.get("state") == "started" and
               any(v.get("path") == "/data" for v in m.get("config", {}).get("mounts", []))]
    if len(targets) != 1:
        raise ValueError("expected exactly one started /data machine; no machine changed")
    machine = str(targets[0]["id"])
    nonce = uuid.uuid4().hex
    remote_package = f"/tmp/aegis-reviewed-assets-{nonce}.json"
    remote_script = f"/tmp/aegis-reviewed-assets-{nonce}.py"
    with tempfile.TemporaryDirectory(prefix="aegis-reviewed-assets-") as temporary:
        packed = Path(temporary) / "assets.json"
        packed.write_text(json.dumps(package), encoding="utf-8")
        try:
            for local, remote in ((str(packed), remote_package), (str(Path(__file__).resolve()), remote_script)):
                run_fly("ssh", "sftp", "put", local, remote, "-a", APP, "--machine", machine, "--mode", "0600")
            command = shlex.join(["python", remote_script, "--install-package", remote_package])
            run_fly("ssh", "console", "-a", APP, "--machine", machine, "-C", command)
            receipt = verify_public_assets(package["assets"])
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            print(f"Verified {len(receipt['assets'])} public image URLs. No deployment or generation was invoked.")
        finally:
            # Delete only the two exact temporary paths created by this run.
            code = "from pathlib import Path;" + ";".join(
                f"Path({p!r}).unlink(missing_ok=True)" for p in (remote_package, remote_script))
            run_fly("ssh", "console", "-a", APP, "--machine", machine,
                    "-C", shlex.join(["python", "-c", code]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--install-package", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--receipt", type=Path, default=Path("asset-publication-receipt.json"))
    args = parser.parse_args()
    if args.install_package:
        package = json.loads(args.install_package.read_text(encoding="utf-8"))
        print(json.dumps(install_package(package)))
    elif args.validate_only:
        package = pack_manifest(args.manifest)
        print(f"Validated {len(package['assets'])} immutable JPEG assets; nothing published.")
    else:
        publish(args.manifest, args.receipt)


if __name__ == "__main__":
    main()
