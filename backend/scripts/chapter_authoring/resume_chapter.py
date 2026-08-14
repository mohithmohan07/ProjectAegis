#!/usr/bin/env python3
"""Resume generation for an already-converted job from its saved checkpoint.

The inventory stage refuses to persist a checkpoint when it produces invalid
source rows, so the last good checkpoint survives the failure. Re-entering
generate_post_learning picks up from there instead of re-running conversion,
chapter reading, and description refinement.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

SCRATCH = Path(__file__).resolve().parent
BACKEND = SCRATCH.parents[1]

if not os.environ.get("OPENAI_API_KEY"):
    raise SystemExit("OPENAI_API_KEY is not set")
os.environ.setdefault("AEGIS_PUBLIC_BASE_URL", "https://aegis.local")
os.environ.setdefault("AEGIS_SOURCE_ASSET_SECRET", "local-run-secret")

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.db import SessionLocal, init_db  # noqa: E402
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
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--chapter-id", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    progress._sink.set(_sink)
    init_db()
    db = SessionLocal()
    try:
        job = uploads.get_job(db, args.job_id, owner_sub=None,
                              module="build_concepts")
        cp = job.generation_checkpoint or {}
        print(f"== resuming job {args.job_id} from "
              f"{cp.get('stage_label')!r} ({cp.get('stage')})", flush=True)

        result = uploads.run_with_openai_usage(
            db, args.job_id,
            lambda: svc.generate_post_learning(
                db, args.job_id, args.chapter_id, owner_sub=None,
            ),
            owner_sub=None,
        )
        if isinstance(result, dict) and result.get("status") == "paused":
            print(f"!! PAUSED for a human decision: {str(result)[:800]}",
                  flush=True)

        db.expire_all()
        job = uploads.get_job(db, args.job_id, owner_sub=None,
                              module="build_concepts")
        print(f"== job status: {job.status}", flush=True)

        content = release_files.build_release_workbook(job)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        print(f"== WROTE {out} ({len(content):,} bytes)", flush=True)

        import json as _json
        from app.services.build_concepts_release import release_payload
        payload = release_payload(job) or {}
        records = [r for r in payload.get("records") or [] if isinstance(r, dict)]
        release_topics = sorted(
            {str(r.get("topic") or "").strip() for r in records} - {""}
        )
        print(f"== concepts: {len(records)}", flush=True)
        print(f"== release topics ({len(release_topics)}):", flush=True)
        for name in release_topics:
            print(f"     - {name}", flush=True)

        art = uploads.source_artifact_directory(args.job_id) / "source.gpt-page-acsd.json"
        outline_titles: list[str] = []
        if art.exists():
            page_acsd = _json.loads(art.read_text(encoding="utf-8"))
            outline = page_acsd.get("chapter_outline") or {}
            outline_titles = [
                str(t.get("title") or "")
                for t in outline.get("topics") or []
                if isinstance(t, dict)
            ]
        print("", flush=True)
        print("== TOPIC ATTRITION (outline -> release)", flush=True)
        print(f"   outline decided : {len(outline_titles)}", flush=True)
        print(f"   reached release : {len(release_topics)}", flush=True)
        lost = [t for t in outline_titles if t not in release_topics]
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
