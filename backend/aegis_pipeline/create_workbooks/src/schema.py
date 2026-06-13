"""Workbook data schema.

Renamed Section → Topic with an explicit `range` field. Each topic owns its
activities so they stay in context (instead of being clumped at the end of
the chapter). Added two block types: `excerpt` (verbatim text/verse with a
reference) and `problem_set` (a typed bundle of solved numerical problems).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


BlockType = Literal[
    "paragraph",
    "bullets",
    "table",
    "flowchart",
    "definitions",
    "callout",
    "worked_example",
    "excerpt",
    "qa",
    "problem_set",
    # richer diagrams — chosen by the model when they genuinely fit the topic
    "venn",
    "pyramid",
    "timeline",
    "cycle",
    "tree",
]


@dataclass
class Block:
    type: BlockType
    title: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Activity:
    title: str
    aim: str = ""
    materials: list[str] = field(default_factory=list)
    procedure: list[dict[str, str]] = field(default_factory=list)
    observation: str = ""
    inference: str = ""


@dataclass
class Topic:
    number: str
    title: str
    range: str = ""           # e.g. "p. 4–6 · Paragraphs 5–18" or "Lines 1–22"
    overview: str = ""
    blocks: list[Block] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    # English only: which part of a Unit this episode belongs to, e.g.
    # "Prose — The Tiger and the Deer" or "Poem — The Road Not Taken". Used to
    # divide a Grade-8 Unit (prose + poem) into clearly separated sections.
    part: str = ""


@dataclass
class GlossaryItem:
    term: str
    definition: str


@dataclass
class EventRevisionItem:
    """Social Science revision block: grouped related incidents."""
    title: str
    event: str
    causes: str = ""
    effects: str = ""
    period: str = ""


@dataclass
class Chapter:
    chapter_number: str
    chapter_title: str
    subject: str
    grade: str
    summary: str = ""
    study_strategy: list[str] = field(default_factory=list)
    glossary: list[GlossaryItem] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    quick_recap: list[str] = field(default_factory=list)
    event_revision: list[EventRevisionItem] = field(default_factory=list)
    chapter_mindmap: dict[str, Any] | None = None
    discipline: str = ""  # Biology | Physics | Chemistry | "" (Science sub-discipline)
