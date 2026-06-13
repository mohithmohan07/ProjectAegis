"""End-to-end pipeline: Mathpix MMD → GPT plan → GPT build → refine → render → validate."""
from __future__ import annotations

import sys
from pathlib import Path

import json

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import DEFAULT_CONFIG, MMD_CACHE_DIR, PLAN_CACHE_DIR, OUTPUT_DIR
from coverage import audit_plan_vs_build
from document import WorkbookDocument
from gpt_writer import GPTWriter, TokenBudgetExceeded
from mathpix import MathpixClient
from refiner import Refiner
from validate import WorkbookValidator


def run(config: dict) -> dict:
    cfg = {**DEFAULT_CONFIG, **config}
    messages: list[str] = []
    messages.append(f"Source: {cfg['source_pdf']}")
    messages.append(f"{cfg['subject']} | {cfg['grade']} | Chapter {cfg['chapter_number']}")
    if cfg.get("discipline"):
        messages.append(f"Discipline: {cfg['discipline']}")
    messages.append(f"Title: {cfg['chapter_title']}")
    messages.append(f"Model: {cfg['openai_model']}")

    # --- Mathpix: PDF → MMD (cached) ----------------------------------
    mathpix = MathpixClient(cache_dir=cfg["mmd_cache_dir"])
    mmd = mathpix.convert_to_mmd(cfg["source_pdf"])
    messages.append(f"MMD: {len(mmd):,} chars (cached at {cfg['mmd_cache_dir']})")

    # --- GPT two-pass ---------------------------------------------------
    writer = GPTWriter(cfg)
    if not writer.enabled:
        raise RuntimeError("Set OPENAI_API_KEY in your environment.")

    plan_path = Path(cfg["plan_cache_dir"]) / f"{Path(cfg['source_pdf']).stem}.plan.json"
    raw_dump_path = Path(cfg["plan_cache_dir"]) / f"{Path(cfg['source_pdf']).stem}.build.raw.json"
    try:
        chapter, gpt_msg = writer.write(
            mmd,
            {
                "chapter_number": cfg["chapter_number"],
                "chapter_title": cfg["chapter_title"],
                "subject": cfg["subject"],
                "grade": cfg["grade"],
            },
            plan_cache_path=plan_path,
            raw_dump_path=raw_dump_path,
        )
    except TokenBudgetExceeded as exc:
        # Surface the issue as a visible warning, then re-raise so the
        # caller can act on it (e.g., split the chapter).
        print(f"\n⚠  Token budget warning: {exc}\n", file=sys.stderr)
        raise
    messages.append(gpt_msg)

    # --- Coverage: plan vs built JSON -----------------------------------
    stem = Path(cfg["source_pdf"]).stem
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    raw_data = None
    if raw_dump_path and raw_dump_path.exists():
        try:
            raw_data = json.loads(raw_dump_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            messages.append("Coverage: build.raw.json is invalid JSON")
    coverage = audit_plan_vs_build(stem, plan, raw_data)
    if coverage.issues:
        for gap in coverage.issues:
            messages.append(f"Coverage gap: {gap}")

    # --- Refine ---------------------------------------------------------
    Refiner().refine(chapter)

    block_count = sum(len(t.blocks) for t in chapter.topics)
    act_count = sum(len(t.activities) for t in chapter.topics)
    messages.append(
        f"Refined: {len(chapter.topics)} topics · {block_count} blocks · "
        f"{act_count} activities · {len(chapter.glossary)} glossary"
    )

    # --- Render ---------------------------------------------------------
    WorkbookDocument(cfg).build(chapter, cfg["output_pdf"])
    messages.append(f"PDF: {cfg['output_pdf']}")

    # --- Validate -------------------------------------------------------
    validator = WorkbookValidator()
    valid, issues = validator.validate(cfg["output_pdf"], chapter)
    if coverage.issues:
        issues = list(issues) + [f"Coverage: {gap}" for gap in coverage.issues]
        valid = False
    validator.write_log(cfg["build_log"], messages, issues)
    return {"output_pdf": cfg["output_pdf"], "build_log": cfg["build_log"],
            "valid": valid, "issues": issues}
