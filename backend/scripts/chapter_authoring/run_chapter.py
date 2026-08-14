#!/usr/bin/env python3
"""Drive one chapter PDF through convert -> generate -> release workbook.

Calls the services directly rather than the HTTP API so there is no auth or
SSE plumbing in the way. The Phase 2.2.1 contract installs itself over
``uploads.convert_job`` on ``app.services`` import, so the PDF goes through the
GPT PDF-to-ACSD reader exactly as production does.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
import traceback
from pathlib import Path

SCRATCH = Path(__file__).resolve().parent
BACKEND = SCRATCH.parents[1]

# Credential comes from the environment and is never written into the tree.
if not os.environ.get("OPENAI_API_KEY"):
    raise SystemExit("OPENAI_API_KEY is not set")
# _public_base_url() requires an https:// origin before it will publish source
# assets. Nothing fetches these URLs in a local run, but they are stamped into
# the canonical source, so it has to be a well-formed https origin.
os.environ.setdefault("AEGIS_PUBLIC_BASE_URL", "https://aegis.local")
os.environ.setdefault("AEGIS_SOURCE_ASSET_SECRET", "local-run-secret")

# Production's pipeline behaviour comes from fly.toml's [env]. Read it rather
# than hand-copying: missing AEGIS_PHASE3_REWRITE silently ran the legacy
# 3.1/3.2/3.3 lane for a full hour, which fly.toml's own comment warns about
# ("without this flag the deleted-era legacy path runs and the webpage output
# diverges from everything validated in staging").
# Deployment-specific keys are deliberately skipped: they point at the Fly
# volume and Google auth, which would break or bypass the local run.
_DEPLOY_ONLY = {
    "AEGIS_DATA_DIR", "AEGIS_DB_URL", "AEGIS_AUTH_MODE",
    "AEGIS_ALLOWED_GOOGLE_DOMAIN", "AEGIS_PUBLIC_BASE_URL", "PORT",
}
_applied = []
_fly = BACKEND.parent / "fly.toml"
if _fly.is_file():
    _in_env = False
    for _line in _fly.read_text().splitlines():
        _stripped = _line.strip()
        if _stripped.startswith("["):
            _in_env = _stripped == "[env]"
            continue
        if not _in_env or _stripped.startswith("#") or "=" not in _stripped:
            continue
        _k, _, _v = _stripped.partition("=")
        _k = _k.strip()
        _v = _v.strip().strip("'").strip('"')
        if _k and _k not in _DEPLOY_ONLY:
            os.environ.setdefault(_k, _v)
            _applied.append(f"{_k}={_v}")
print("== production env from fly.toml: " + ", ".join(_applied), flush=True)

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402
from app.services import progress  # noqa: E402
from app.services import build_concepts as svc  # noqa: E402
from app.services import uploads  # noqa: E402
from app.services import build_concepts_release_files as release_files  # noqa: E402

_START = time.time()


def _sink(event: dict) -> None:
    kind = str(event.get("type") or "")
    stamp = f"[{time.time() - _START:7.1f}s]"
    if kind == "log":
        print(f"{stamp} {str(event.get('level') or 'info')[:4]:>5} | "
              f"{event.get('message')}", flush=True)
    elif kind == "step":
        print(f"{stamp}  STEP | {event.get('label')}", flush=True)
    elif kind == "progress":
        print(f"{stamp}  ---- | {float(event.get('value') or 0) * 100:5.1f}% "
              f"{event.get('label') or ''}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--chapter-id", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    progress._sink.set(_sink)
    init_db()
    db = SessionLocal()
    pdf = Path(args.pdf)
    label = args.label or pdf.stem

    try:
        chapter = db.get(models.Chapter, args.chapter_id)
        if chapter is None:
            print(f"!! chapter {args.chapter_id} not found", flush=True)
            return 2
        print(f"== {label}", flush=True)
        print(f"== target: [{chapter.id}] {chapter.board} / {chapter.grade} / "
              f"{chapter.subject} / {chapter.chapter_title}", flush=True)

        job = svc.create_post_learning_job(
            db,
            filename=pdf.name,
            raw_bytes=pdf.read_bytes(),
            source_book="Balbharati Std 6 Mathematics",
            owner_sub=None,
        )
        job_id = int(job.id)
        print(f"== job {job_id} staged ({pdf.stat().st_size:,} bytes)", flush=True)

        print("== CONVERT (GPT PDF-to-ACSD) ...", flush=True)
        converted = uploads.convert_job(
            db, job_id, owner_sub=None, module="build_concepts",
        )
        print(f"== converted via {converted.get('conversion_source')}: "
              f"{converted.get('mmd_chars', 0):,} MMD chars", flush=True)

        print("== GENERATE ...", flush=True)
        # Through the same wrapper the API endpoint uses. It is what
        # activates the compiled canonical (phase2.activate), which is how
        # chapter reading sees the outline. Calling the service directly
        # silently skips that and makes the harness unfaithful.
        result = uploads.run_with_openai_usage(
            db, job_id,
            lambda: svc.generate_post_learning(
                db, job_id, args.chapter_id, owner_sub=None,
            ),
            owner_sub=None,
        )
        if isinstance(result, dict) and result.get("status") == "paused":
            print(f"!! generation PAUSED for a human decision: "
                  f"{str(result)[:800]}", flush=True)

        db.expire_all()
        job = uploads.get_job(db, job_id, owner_sub=None,
                              module="build_concepts")
        print(f"== job status: {job.status}", flush=True)

        content = release_files.build_release_workbook(job)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        print(f"== WROTE {out} ({len(content):,} bytes)", flush=True)

        # Summary counts straight off the staged release.
        from app.services.build_concepts_release import release_payload
        payload = release_payload(job) or {}
        records = [r for r in payload.get("records") or [] if isinstance(r, dict)]
        topics = sorted({str(r.get("topic") or "").strip() for r in records} - {""})
        print(f"== concepts: {len(records)}", flush=True)
        print(f"== release topics ({len(topics)}):", flush=True)
        for name in topics:
            print(f"     - {name}", flush=True)

        # The three count points handoff item 1 needs: what the outline
        # decided, what the concepts named, and what reached the release.
        import json as _json
        art = uploads.source_artifact_directory(job_id) / "source.gpt-page-acsd.json"
        outline_titles: list[str] = []
        if art.exists():
            page_acsd = _json.loads(art.read_text(encoding="utf-8"))
            outline = page_acsd.get("chapter_outline") or {}
            outline_titles = [
                str(t.get("title") or "")
                for t in outline.get("topics") or []
                if isinstance(t, dict)
            ]
        record_topics = sorted(
            {str(r.get("topic") or "").strip() for r in records} - {""}
        )
        print("", flush=True)
        print("== TOPIC ATTRITION (outline -> concepts -> release)", flush=True)
        print(f"   outline decided : {len(outline_titles)}", flush=True)
        print(f"   named by concepts: {len(record_topics)}", flush=True)
        print(f"   reached release : {len(topics)}", flush=True)
        lost = [t for t in outline_titles if t not in record_topics]
        if lost:
            print(f"   LOST ({len(lost)}):", flush=True)
            for name in lost:
                print(f"     - {name!r}", flush=True)
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
