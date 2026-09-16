"""Read public-safe incident pages from Fly; never mutate application state.

Only the independently validated public schema may reach the diagnostics branch.
Collection completes in a temporary directory before replacing the output; an
unavailable server or bad page cannot be reported as a successful empty export.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


def _validator():
    path = Path(__file__).resolve().parents[1] / "app/services/failure_reports_public.py"
    spec = importlib.util.spec_from_file_location("failure_reports_public", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_public_envelope


def fetch_page(cursor: str | None) -> dict:
    command = ["python", "/app/scripts/export_failure_reports.py", "--limit", "200"]
    if cursor:
        if not re.fullmatch(r"[A-Za-z0-9_=-]{1,1024}", cursor):
            raise ValueError("Invalid collection cursor")
        command += ["--cursor", cursor]
    result = subprocess.run(
        ["flyctl", "ssh", "console", "-a", "projectaegis", "-C", shlex.join(command)],
        capture_output=True, text=True, timeout=120,
    )
    # Neither raw SSH output nor a partially parsed document is public evidence.
    if result.returncode:
        raise RuntimeError("Incident collection failed; inspect hosting connectivity privately")
    if len(result.stdout.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("Incident page exceeds collection size limit")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        raise ValueError("Incident exporter did not return a valid public JSON page") from None


def collect(output: Path, *, fetch=fetch_page, validate=None) -> dict:
    validate = validate or _validator()
    output = output.resolve()
    if output.exists():
        raise ValueError("Collection output must be a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    seen_cursors: set[str] = set()
    seen_reports: dict[str, str] = {}
    pages = 0
    cursor = None
    started_at = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="aegis-public-export-", dir=output.parent) as folder:
        staging = Path(folder)
        (staging / "incidents").mkdir()
        while True:
            envelope = fetch(cursor)
            validate(envelope)
            pages += 1
            for report in envelope["reports"]:
                report_id = report["report_id"]
                if not re.fullmatch(r"[0-9a-f]{32}", report_id):
                    raise ValueError("Invalid incident identity")
                encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
                if report_id in seen_reports and seen_reports[report_id] != encoded:
                    raise ValueError("Incident identity changed during collection")
                seen_reports[report_id] = encoded
                (staging / "incidents" / f"{report_id}.json").write_text(encoded)
            cursor = envelope["next_cursor"]
            if not cursor:
                break
            if cursor in seen_cursors or pages >= 1000:
                raise ValueError("Incident pagination did not finish; export remains incomplete")
            seen_cursors.add(cursor)
        manifest = {
            "schema_version": 1,
            "collection_status": "complete",
            "started_at": started_at,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "report_count": len(seen_reports),
            "page_count": pages,
            "repository_visibility": "public",
            "content_policy": "strict-allowlist-no-source-text",
        }
        (staging / "latest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        (staging / "README.md").write_text(
            "# Aegis failure diagnostics\n\n"
            "These are strict allowlisted incident records from the production volume. "
            "Original logs and source content remain private. `latest.json` records the "
            "last successful collection; stale collection is not evidence of no failures. "
            "Reports are immutable observations, not proof that a run has recovered.\n\n"
            "Maintenance instructions: [nightly-maintenance.md](https://github.com/"
            "mohithmohan07/ProjectAegis/blob/main/docs/nightly-maintenance.md).\n"
        )
        shutil.copytree(staging, output)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = collect(args.output)
    except Exception:
        # Never echo rejected payloads or SSH stderr into a public Actions log.
        raise SystemExit("Public incident collection failed; no diagnostic commit was prepared") from None
    print(json.dumps(summary, sort_keys=True))
