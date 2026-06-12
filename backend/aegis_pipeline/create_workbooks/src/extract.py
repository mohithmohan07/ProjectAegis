"""Extract clean text + structural blocks from an NCERT chapter PDF."""
from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

EXCLUDE = re.compile(
    r"(?i)(exam corner|journey beyond|ready to go beyond|discuss in class|"
    r"share your answers|pencil your thoughts|let us discuss)"
)
TOPIC_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?\s+[A-Z]")
ACTIVITY_RE = re.compile(r"(?i)^Activity\s+\d+\.\d+")


@dataclass
class SourceBlock:
    kind: str  # "topic" | "activity"
    title: str
    text: str
    page: int


@dataclass
class ExtractedSource:
    page_count: int
    full_text: str
    blocks: list[SourceBlock]


class PDFExtractor:
    def extract(self, pdf_path: str) -> ExtractedSource:
        doc = fitz.open(pdf_path)
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text("text"))
        full_text = "\n".join(pages)
        blocks = self._split(full_text)
        return ExtractedSource(page_count=len(doc), full_text=full_text, blocks=blocks)

    def _split(self, text: str) -> list[SourceBlock]:
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        blocks: list[SourceBlock] = []
        current_title = "Chapter Introduction"
        buffer: list[str] = []
        page_marker = 1

        def flush() -> None:
            nonlocal buffer
            body = "\n".join(buffer).strip()
            if len(body) > 80 and not EXCLUDE.search(body):
                blocks.append(SourceBlock("topic", current_title, body, page_marker))
            buffer = []

        for line in lines:
            stripped = line.strip()
            if EXCLUDE.search(stripped):
                continue
            if TOPIC_RE.match(stripped):
                flush()
                current_title = re.sub(r"^\d+\.\d+(?:\.\d+)?\s+", "", stripped).strip()
                page_marker += 1
                buffer = [stripped]
                continue
            if ACTIVITY_RE.match(stripped):
                blocks.append(SourceBlock("activity", stripped.rstrip(":"), stripped, page_marker))
                continue
            buffer.append(stripped)
        flush()
        return blocks
