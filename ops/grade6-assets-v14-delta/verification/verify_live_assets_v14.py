#!/usr/bin/env python3
"""Anonymously verify every JPEG in the final Grade 6 v1.4 live set."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SCHEMA_VERSION = "aegis.asset-live-verification.v1.4"
MANIFEST_SCHEMA_VERSION = "aegis.manual-assets.v1.4"
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "f5c08bfd69720021d9317c936a3fbc6e788752e6e11def18bee45d4f00ea8f8d"
)
ALLOWED_ORIGIN = "https://projectaegis.fly.dev"
EXPECTED_PUBLIC_BASE = f"{ALLOWED_ORIGIN}/source-assets/0"
EXPECTED_ASSET_COUNT = 29
EXPECTED_RELEASE_USED_ASSET_COUNT = 27
EXPECTED_PUBLIC_CONTENT_OCCURRENCES = 69
EXPECTED_INTERNAL_SOURCE_EVIDENCE_OCCURRENCES = 3
EXPECTED_ALL_NORMALIZED_OCCURRENCES = 72
EXPECTED_UNUSED_IDS = {
    "WD-AST-P03-Q10-A",
    "WD-AST-P03-Q10-B",
}
EXPECTED_DELTA = {
    "FR-ASSET-001": "d8fc15e13e1601d15045489b92b6661ddba41265d16f725318f7ee164b31526d",
    "WD-PRE-AST-LIBRARY-ACTION": "385d4a1b3cfdb770a4a8b52fb47d063d5197c7b98d7e90b05a0f6fa19bb9a16d",
}
EXPECTED_BRANCH = "ops/grade6-assets-v14-20260902"
EXPECTED_MACHINE_ID = "8d4066aed23998"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class VerificationError(RuntimeError):
    """Raised when verification inputs cannot represent the reviewed release."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_loader_report(raw: str, *, apply: bool) -> dict[str, Any]:
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerificationError("loader report is not valid JSON") from exc
    expected = {
        "mode": "apply" if apply else "dry-run",
        "validated": 2,
        "pinned": 2 if apply else 0,
    }
    if report != expected:
        raise VerificationError(
            f"unexpected {'apply' if apply else 'dry-run'} report: {report!r}"
        )
    return report


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read valid manifest: {path}") from exc
    manifest_sha256 = _sha256(raw)
    if manifest_sha256 != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise VerificationError(
            "source Asset_Manifest_v1.4 SHA-256 is not the reviewed value"
        )
    if not isinstance(manifest, dict):
        raise VerificationError("manifest root is not an object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise VerificationError("unexpected live-set manifest schema")
    if not str(manifest.get("status") or "").startswith("PASS"):
        raise VerificationError("source asset manifest status does not start with PASS")
    if manifest.get("public_base") != EXPECTED_PUBLIC_BASE:
        raise VerificationError("unexpected public base")
    if manifest.get("asset_count") != EXPECTED_ASSET_COUNT:
        raise VerificationError("manifest asset_count is not 29")
    if manifest.get("release_used_asset_count") != EXPECTED_RELEASE_USED_ASSET_COUNT:
        raise VerificationError("manifest release_used_asset_count is not 27")
    if manifest.get("public_content_occurrences") != EXPECTED_PUBLIC_CONTENT_OCCURRENCES:
        raise VerificationError("manifest public content occurrence total is not 69")
    if (
        manifest.get("internal_source_evidence_occurrences")
        != EXPECTED_INTERNAL_SOURCE_EVIDENCE_OCCURRENCES
    ):
        raise VerificationError("manifest internal source-evidence total is not 3")
    if manifest.get("all_normalized_occurrences") != EXPECTED_ALL_NORMALIZED_OCCURRENCES:
        raise VerificationError("manifest all-normalized occurrence total is not 72")
    records = manifest.get("assets")
    if not isinstance(records, list) or len(records) != EXPECTED_ASSET_COUNT:
        raise VerificationError("manifest must contain exactly 29 asset records")

    ids: set[str] = set()
    digests: set[str] = set()
    urls: set[str] = set()
    release_used_ids: set[str] = set()
    unused_ids: set[str] = set()
    public_occurrences = 0
    internal_occurrences = 0
    all_occurrences = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise VerificationError(f"asset record {index} is not an object")
        asset_id = str(record.get("asset_id") or "")
        digest = str(record.get("jpeg_sha256") or "")
        filename = str(record.get("jpeg_filename") or "")
        url = str(record.get("public_url") or "")
        try:
            size = int(record.get("jpeg_size_bytes"))
            width = int(record.get("width"))
            height = int(record.get("height"))
        except (TypeError, ValueError) as exc:
            raise VerificationError(f"{asset_id or index}: invalid numeric metadata") from exc
        if not asset_id or asset_id in ids:
            raise VerificationError(f"missing or duplicate asset_id: {asset_id!r}")
        if not SHA256_RE.fullmatch(digest) or digest in digests:
            raise VerificationError(f"{asset_id}: invalid or duplicate JPEG SHA-256")
        if filename != f"{digest}.jpg":
            raise VerificationError(f"{asset_id}: JPEG filename is not content-addressed")
        if record.get("mime_type") != "image/jpeg":
            raise VerificationError(f"{asset_id}: manifest MIME type is not image/jpeg")
        expected_url = f"{EXPECTED_PUBLIC_BASE}/{digest}.jpg"
        if url != expected_url or url in urls:
            raise VerificationError(f"{asset_id}: public URL is not canonical")
        parsed = urlparse(url)
        if f"{parsed.scheme}://{parsed.netloc}" != ALLOWED_ORIGIN:
            raise VerificationError(f"{asset_id}: public URL origin is not approved")
        if parsed.query or parsed.fragment:
            raise VerificationError(f"{asset_id}: public URL must be immutable and unsigned")
        if size <= 0 or width <= 0 or height <= 0:
            raise VerificationError(f"{asset_id}: dimensions and byte size must be positive")
        release_used = record.get("release_used")
        if not isinstance(release_used, bool):
            raise VerificationError(f"{asset_id}: release_used must be a boolean")
        occurrence_fields = (
            "public_content_occurrences",
            "internal_source_evidence_occurrences",
            "all_normalized_occurrences",
        )
        if any(
            not isinstance(record.get(field), int) or record[field] < 0
            for field in occurrence_fields
        ):
            raise VerificationError(f"{asset_id}: invalid occurrence counts")
        record_public = record["public_content_occurrences"]
        record_internal = record["internal_source_evidence_occurrences"]
        record_all = record["all_normalized_occurrences"]
        if record_all != record_public + record_internal:
            raise VerificationError(f"{asset_id}: public + internal does not equal all")
        if release_used and record_public < 1:
            raise VerificationError(f"{asset_id}: release-used asset has no public occurrence")
        if not release_used and record_public != 0:
            raise VerificationError(f"{asset_id}: unused asset has a public occurrence")
        if release_used:
            release_used_ids.add(asset_id)
        else:
            unused_ids.add(asset_id)
        public_occurrences += record_public
        internal_occurrences += record_internal
        all_occurrences += record_all
        ids.add(asset_id)
        digests.add(digest)
        urls.add(url)

    if len(release_used_ids) != EXPECTED_RELEASE_USED_ASSET_COUNT:
        raise VerificationError("per-record release-used count is not 27")
    if unused_ids != EXPECTED_UNUSED_IDS:
        raise VerificationError(
            f"unexpected unused asset IDs: {sorted(unused_ids)!r}"
        )
    if public_occurrences != EXPECTED_PUBLIC_CONTENT_OCCURRENCES:
        raise VerificationError("per-record public occurrences do not sum to 69")
    if internal_occurrences != EXPECTED_INTERNAL_SOURCE_EVIDENCE_OCCURRENCES:
        raise VerificationError("per-record internal occurrences do not sum to 3")
    if all_occurrences != EXPECTED_ALL_NORMALIZED_OCCURRENCES:
        raise VerificationError("per-record all occurrences do not sum to 72")

    for asset_id, digest in EXPECTED_DELTA.items():
        matches = [item for item in records if item["asset_id"] == asset_id]
        if len(matches) != 1 or matches[0]["jpeg_sha256"] != digest:
            raise VerificationError(f"required v1.4 delta is missing: {asset_id}")
    if "MEAS-A002" in ids:
        raise VerificationError("superseded MEAS-A002 must not be in the v1.4 live set")
    if "d774b64f94364b3101c25f4fec8a5c0120c6447dcebe09f175d394578967810c" in digests:
        raise VerificationError("stale fixed-arrow FR-ASSET-001 crop is in the v1.4 live set")
    expected_pending = set(EXPECTED_DELTA)
    if set(manifest.get("pending_live_asset_ids") or []) != expected_pending:
        raise VerificationError("pending-live asset IDs do not equal the reviewed delta")
    if set(manifest.get("content_replaced_asset_ids") or []) != {"FR-ASSET-001"}:
        raise VerificationError("content-replaced asset IDs are not exact")
    if set(manifest.get("added_asset_ids") or []) != {"WD-PRE-AST-LIBRARY-ACTION"}:
        raise VerificationError("added asset IDs are not exact")
    if set(manifest.get("retired_asset_ids") or []) != {"MEAS-A002"}:
        raise VerificationError("retired asset IDs are not exact")
    if set(manifest.get("reused_asset_ids") or []) != ids - expected_pending:
        raise VerificationError("reused asset ID set is not exact")
    if set(manifest.get("reused_live_asset_ids") or []) != (
        release_used_ids - expected_pending
    ):
        raise VerificationError("reused live asset ID set is not exact")
    return manifest, manifest_sha256


def _verify_asset(asset: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = str(asset["public_url"])
    expected = {
        "final_url": url,
        "http_status": 200,
        "content_type": "image/jpeg",
        "size_bytes": int(asset["jpeg_size_bytes"]),
        "sha256": str(asset["jpeg_sha256"]),
    }
    result: dict[str, Any] = {
        "asset_id": asset["asset_id"],
        "public_url": url,
        "release_used": asset["release_used"],
        "expected": expected,
    }
    errors: list[str] = []
    actual: dict[str, Any] = {}
    try:
        request = Request(
            url,
            headers={
                "Accept": "image/jpeg",
                "User-Agent": "Project-Aegis-Release-Verification/1.4",
            },
            method="GET",
        )
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            actual = {
                "final_url": response.geturl(),
                "http_status": int(response.status),
                "content_type": response.headers.get_content_type().lower(),
                "size_bytes": len(body),
                "sha256": _sha256(body),
                "cache_control": str(response.headers.get("Cache-Control") or ""),
            }
    except HTTPError as exc:
        body = exc.read()
        actual = {
            "final_url": exc.geturl(),
            "http_status": int(exc.code),
            "content_type": exc.headers.get_content_type().lower(),
            "size_bytes": len(body),
            "sha256": _sha256(body),
        }
        errors.append(f"HTTP {exc.code}: {exc.reason}")
    except URLError as exc:
        errors.append(f"URL error: {exc.reason}")
    except Exception as exc:  # pragma: no cover - defensive release evidence
        errors.append(f"{type(exc).__name__}: {exc}")

    result["actual"] = actual
    for field, expected_value in expected.items():
        if actual.get(field) != expected_value:
            errors.append(
                f"{field}: expected {expected_value!r}, got {actual.get(field)!r}"
            )
    result["status"] = "PASS" if not errors else "FAIL"
    result["errors"] = errors
    return result


def _write_step_summary(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "## Grade 6 v1.4 live-asset verification",
        "",
        f"- Status: **{summary['status']}**",
        f"- Checked: **{summary['checked']} / {summary['expected']}**",
        f"- Passed: **{summary['passed']}**",
        f"- Failed: **{summary['failed']}**",
        f"- Evidence: `{report['source_manifest']['sha256']}` (full-29 manifest SHA-256)",
        "",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--loader-dry-run-report", required=True)
    parser.add_argument("--loader-apply-report", required=True)
    parser.add_argument("--github-step-summary", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fatal_errors: list[str] = []
    manifest: dict[str, Any] = {"assets": []}
    manifest_sha256 = ""
    dry_run_report: dict[str, Any] | None = None
    apply_report: dict[str, Any] | None = None
    try:
        manifest, manifest_sha256 = _load_manifest(args.manifest)
        dry_run_report = _parse_loader_report(args.loader_dry_run_report, apply=False)
        apply_report = _parse_loader_report(args.loader_apply_report, apply=True)
        if args.branch != EXPECTED_BRANCH:
            raise VerificationError(f"unexpected branch: {args.branch!r}")
        if args.machine_id != EXPECTED_MACHINE_ID:
            raise VerificationError(f"unexpected Fly machine: {args.machine_id!r}")
        if not COMMIT_RE.fullmatch(args.commit):
            raise VerificationError("commit is not a full lowercase Git SHA")
        if not SHA256_RE.fullmatch(args.archive_sha256):
            raise VerificationError("archive SHA-256 is malformed")
        if not args.workflow_run_id.isdigit():
            raise VerificationError("workflow run id is not numeric")
        expected_run_url = (
            f"https://github.com/{args.repository}/actions/runs/{args.workflow_run_id}"
        )
        if args.workflow_run_url != expected_run_url:
            raise VerificationError("workflow run URL does not match repository and run id")
    except VerificationError as exc:
        fatal_errors.append(str(exc))

    results: list[dict[str, Any]] = []
    if not fatal_errors:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(_verify_asset, asset, args.timeout): asset
                for asset in manifest["assets"]
            }
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: (str(item["asset_id"]), item["public_url"]))

    passed = sum(item["status"] == "PASS" for item in results)
    failed = len(results) - passed
    verified_release_used = sum(
        item["status"] == "PASS" and item["release_used"] is True
        for item in results
    )
    verified_unused = sum(
        item["status"] == "PASS" and item["release_used"] is False
        for item in results
    )
    status = (
        "PASS"
        if not fatal_errors
        and len(results) == EXPECTED_ASSET_COUNT
        and passed == EXPECTED_ASSET_COUNT
        else "FAIL"
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "public_base": EXPECTED_PUBLIC_BASE,
        "scope": "all_29_final_v1.4_assets_including_2_unused_source-panel_assets",
        "source_manifest_sha256": manifest_sha256,
        "source_manifest": {
            "filename": args.manifest.name,
            "sha256": manifest_sha256,
            "expected_sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
            "asset_count": manifest.get("asset_count"),
            "release_used_asset_count": manifest.get("release_used_asset_count"),
            "unused_asset_count": (
                EXPECTED_ASSET_COUNT - EXPECTED_RELEASE_USED_ASSET_COUNT
            ),
            "public_content_occurrences": manifest.get("public_content_occurrences"),
            "internal_source_evidence_occurrences": manifest.get(
                "internal_source_evidence_occurrences"
            ),
            "all_normalized_occurrences": manifest.get("all_normalized_occurrences"),
        },
        "import_execution": {
            "workflow": args.workflow_name,
            "run_id": int(args.workflow_run_id) if args.workflow_run_id.isdigit() else args.workflow_run_id,
            "run_url": args.workflow_run_url,
            "repository": args.repository,
            "branch": args.branch,
            "commit": args.commit,
            "archive_sha256": args.archive_sha256,
            "machine_id": args.machine_id,
            "delta_asset_count": 2,
            "loader_dry_run": dry_run_report,
            "loader_apply": apply_report,
        },
        "required_checks": [
            "canonical unsigned Project Aegis URL",
            "no redirect",
            "HTTP 200",
            "Content-Type image/jpeg",
            "exact byte length",
            "exact SHA-256",
        ],
        "fatal_errors": fatal_errors,
        "counts": {
            "verified_pinned_assets": passed,
            "verified_release_used_assets": verified_release_used,
            "verified_unused_assets": verified_unused,
            "errors": failed + len(fatal_errors),
        },
        "summary": {
            "expected": EXPECTED_ASSET_COUNT,
            "checked": len(results),
            "passed": passed,
            "failed": failed,
            "status": status,
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.github_step_summary:
        _write_step_summary(args.github_step_summary, report)
    print(json.dumps(report["summary"], sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
