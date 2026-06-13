"""Command-line entry point for the workbook generator."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import DEFAULT_CONFIG, default_output_path
from metadata import infer_metadata
from pipeline import run


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a detailed NCERT chapter revision workbook.")
    p.add_argument("--source-pdf", required=True, help="Path to NCERT chapter PDF.")
    p.add_argument("--subject", choices=["Science", "Mathematics", "Social Science", "English"])
    p.add_argument("--grade")
    p.add_argument("--chapter-number")
    p.add_argument("--chapter-title")
    p.add_argument("--output-pdf")
    p.add_argument("--from-path", action="store_true",
                   help="Auto-fill subject/grade/chapter from filename.")
    p.add_argument("--sample", action="store_true",
                   help="Write to workbook/output instead of the published library "
                        "(Books\\Workbooks\\Class NN\\Subject).")
    p.add_argument("--model", default=DEFAULT_CONFIG["openai_model"], help="OpenAI model slug.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg: dict = {}
    if args.from_path:
        cfg.update(infer_metadata(args.source_pdf))
    cfg["source_pdf"] = args.source_pdf
    if args.subject:
        cfg["subject"] = args.subject
    if args.grade:
        cfg["grade"] = args.grade
    if args.chapter_number:
        cfg["chapter_number"] = args.chapter_number
    if args.chapter_title:
        cfg["chapter_title"] = args.chapter_title
    cfg["openai_model"] = args.model

    if "subject" not in cfg or "chapter_title" not in cfg or "chapter_number" not in cfg:
        print("Error: missing chapter metadata. Use --from-path or pass --subject/--grade/--chapter-number/--chapter-title.")
        return 1

    if args.output_pdf:
        out = Path(args.output_pdf)
    else:
        out = default_output_path(cfg, sample=args.sample)
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg["output_pdf"] = str(out)
    cfg["build_log"] = str(out.with_suffix(".build_log.txt"))

    result = run(cfg)
    print(result["output_pdf"])
    print(result["build_log"])
    if not result["valid"]:
        print("\nValidation issues:")
        for issue in result["issues"]:
            print(f"  - {issue}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
