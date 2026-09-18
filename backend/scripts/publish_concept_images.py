"""Publish reviewed JPEG crops only; never call generation or write a database.

The content-addressed store and /source-assets/0 URL use the existing Aegis
delivery contract. Zero denotes offline authoring, not an invented upload job.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

BASE = "https://projectaegis.fly.dev"
NAME = re.compile(r"[0-9a-f]{64}\.jpg")
RESERVE = 1024 ** 3


def validated(z):
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["schema"] == 1 and manifest["job_id"] == 0
    entries = manifest["assets"]
    assert entries and len(z.namelist()) == len(entries) + 1
    assert len({a["filename"] for a in entries}) == len(entries)
    assert set(z.namelist()) == {"manifest.json", *(a["filename"] for a in entries)}
    result = []
    for a in entries:
        name = a["filename"]
        assert NAME.fullmatch(name), "Invalid content-addressed filename"
        data = z.read(name)
        assert len(data) == a["bytes"]
        assert hashlib.sha256(data).hexdigest() == a["sha256"] == name[:-4]
        assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
        assert a["url"] == f"{BASE}/source-assets/0/{name}"
        result.append((a, data))
    return result


def write_new(path, data):
    """Atomic create-if-absent: never replace any existing file."""
    fd, temporary = tempfile.mkstemp(prefix=".concept-image-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("Existing destination differs; refusing overwrite")
    finally:
        os.unlink(temporary)


def install(archive, store, reserve=RESERVE):
    store = Path(store)
    if store.is_symlink():
        raise ValueError("Store must not be a symlink")
    with zipfile.ZipFile(archive) as z:
        entries = validated(z)
    assert store.parent.is_dir(), "Missing data-volume parent"
    assert shutil.disk_usage(store.parent).free >= reserve + sum(len(d) for _, d in entries)
    # Validate every existing destination before adding any new bytes.
    for a, data in entries:
        path = store / a["filename"]
        assert not path.is_symlink() and not path.with_suffix(".json").is_symlink()
        if path.exists():
            assert path.read_bytes() == data, "Existing asset is corrupt; no overwrite allowed"
    store.mkdir(exist_ok=True)
    added = 0
    for a, data in entries:
        path = store / a["filename"]
        added += not path.exists()
        write_new(path, data)
        sidecar = path.with_suffix(".json")
        if not sidecar.exists():
            entry = {"sha256": a["sha256"], "bytes": len(data), "media_type": "image/jpeg",
                     "job_id": 0, "asset_url": a["url"], "public_base_url": BASE,
                     "created_at": time.time(), "origin": "offline-reviewed-concept-images"}
            write_new(sidecar, json.dumps(entry, sort_keys=True).encode())
    print(json.dumps({"verified": len(entries), "added": added, "database_touched": False}))


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Aegis-image-verifier/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get_content_type(), r.read()


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    report = []
    for a in manifest["assets"]:
        status, media, data = get(a["url"])
        assert status == 200 and media == "image/jpeg"
        assert len(data) == a["bytes"] and hashlib.sha256(data).hexdigest() == a["sha256"]
        report.append({"url": a["url"], "status": status, "sha256": a["sha256"], "bytes": len(data)})
    Path("image-url-verification.json").write_text(json.dumps(report, indent=2))
    print(f"Verified {len(report)} public image URLs anonymously.")


def publish(directory):
    # Use the repository-configured token only through flyctl's normal env.
    if not os.environ.get("FLY_API_TOKEN"):
        raise SystemExit("Missing configured FLY_API_TOKEN; no publication attempted")
    machine_data = subprocess.check_output(["flyctl", "machines", "list", "-a", "projectaegis", "--json"])
    machines = json.loads(machine_data)
    eligible = [m for m in machines if m.get("state") == "started" and
                any(x.get("path") == "/data" for x in m.get("config", {}).get("mounts", []))]
    if len(eligible) != 1:
        raise SystemExit("Expected one running /data-volume machine; inspect topology before publication")
    machine = eligible[0]["id"]
    before = json.loads(get(BASE + "/health")[2])
    run = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    assert run.isdigit() and attempt.isdigit()
    remote = f"/tmp/aegis-concept-images-{run}-{attempt}.zip"
    with tempfile.TemporaryDirectory(prefix="aegis-image-publish-") as temporary:
        archive = Path(temporary) / "assets.zip"
        manifest = json.loads((directory / "manifest.json").read_text())
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as z:
            z.write(directory / "manifest.json", "manifest.json")
            for a in manifest["assets"]:
                assert NAME.fullmatch(a["filename"])
                z.write(directory / a["filename"], a["filename"])
        with zipfile.ZipFile(archive) as z:
            validated(z)
        subprocess.run(["flyctl", "ssh", "sftp", "put", "-a", "projectaegis", "--machine", machine,
                        "--mode", "0600", str(archive), remote], check=True)
        payload = base64.b64encode(Path(__file__).read_bytes()).decode()
        code = "import base64,sys;sys.argv=['image-install','--install'," + repr(remote) + ", '--store','/data/source-asset-store'];exec(base64.b64decode(" + repr(payload) + "))"
        subprocess.run(["flyctl", "ssh", "console", "-a", "projectaegis", "--machine", machine,
                        "-C", "python -c " + shlex.quote(code)], check=True)
    verify(directory)
    after = json.loads(get(BASE + "/health")[2])
    for key in ("release_sha", "release"):
        if key in before:
            assert before[key] == after[key], "Release changed during image publication"


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install", type=Path)
    p.add_argument("--store", type=Path, default=Path("/data/source-asset-store"))
    p.add_argument("--assets", type=Path, default=Path("ops/concept-image-assets/2026-09-18"))
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args()
    if args.install:
        install(args.install, args.store)
        # Remove only this exact, verified transfer archive after success.
        if re.fullmatch(r"/tmp/aegis-concept-images-\d+-\d+\.zip", str(args.install)):
            args.install.unlink()
    elif args.verify_only:
        verify(args.assets)
    else:
        publish(args.assets)
