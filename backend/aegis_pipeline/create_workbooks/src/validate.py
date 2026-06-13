"""Lightweight post-build validation.

Checks the produced PDF is A4, has the expected sections, contains no
forbidden markers, and that key topic content (definitions, flowcharts,
exam-relevant terms) is present.
"""
from __future__ import annotations

from pathlib import Path

import fitz

from schema import Chapter

FORBIDDEN = (
    "Detailed Notes",
    "PENCIL YOUR THOUGHTS",
    "EXAM CORNER",
    "Playbook",
)
A4_W = 595.276
A4_H = 841.89


class WorkbookValidator:
    def validate(self, pdf_path: str, chapter: Chapter) -> tuple[bool, list[str]]:
        issues: list[str] = []
        path = Path(pdf_path)
        if not path.exists():
            return False, [f"PDF not produced: {path}"]
        doc = fitz.open(str(path))
        text = "\n".join(page.get_text("text") for page in doc)

        if doc.page_count < 6:
            issues.append(f"PDF too short ({doc.page_count} pages); detailed notes need more.")
        if "Glossary" not in text:
            issues.append("Glossary missing.")
        # Social Science uses a "Chapter Map" in place of the plain Contents list.
        if "Contents" not in text and "Chapter Map" not in text:
            issues.append("Contents missing.")
        if "TOPIC 01" not in text:
            issues.append("Topic 01 header missing.")
        for marker in FORBIDDEN:
            if marker.lower() in text.lower():
                issues.append(f"Forbidden marker present: {marker}")

        if len(chapter.topics) < 5:
            issues.append(f"Too few topics: {len(chapter.topics)}")
        if not any(any(b.type == "paragraph" for b in t.blocks) for t in chapter.topics):
            issues.append("No paragraph blocks found in any topic.")
        structured = {"table", "flowchart", "definitions", "worked_example",
                      "problem_set", "excerpt", "venn", "pyramid", "timeline",
                      "cycle", "tree"}
        if not any(any(b.type in structured for b in t.blocks) for t in chapter.topics):
            issues.append("No structured blocks found anywhere.")
        # Encourage visual variety: at least a couple of genuine diagrams across
        # the chapter (not a hard fail for English, which leans on excerpts).
        diagrams = {"flowchart", "cycle", "timeline", "pyramid", "venn", "tree"}
        diagram_count = sum(
            1 for t in chapter.topics for b in t.blocks if b.type in diagrams
        )
        if chapter.subject != "English" and diagram_count == 0:
            issues.append("No diagrams (flowchart/cycle/timeline/pyramid/venn/tree) anywhere.")
        # Topic range coverage — strong requirement for English.
        if chapter.subject == "English":
            missing = [t.number for t in chapter.topics if not t.range]
            if missing:
                issues.append(f"English topics missing source range: {','.join(missing)}")

        for page_num, page in enumerate(doc, start=1):
            rect = page.rect
            if abs(rect.width - A4_W) > 2 or abs(rect.height - A4_H) > 2:
                issues.append(f"Page {page_num} not A4 portrait.")

        return not issues, issues

    def write_log(self, log_path: str, messages: list[str], issues: list[str]) -> None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        status = "PASSED" if not issues else "ISSUES"
        out = ["Workbook build log", f"Status: {status}", ""] + messages
        if issues:
            out.extend(["", "Issues:"] + [f"- {i}" for i in issues])
        path.write_text("\n".join(out), encoding="utf-8")
