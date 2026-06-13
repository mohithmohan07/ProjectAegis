"""Python refinement for the new Topic-based schema.

GPT is the source of truth — the refiner only:
  • clips text to A4-safe limits (limits are intentionally generous so the
    notes feel like real exam revision, not a one-line summary),
  • normalises block shapes,
  • drops blocks that don't fit a subject's voice,
  • renumbers topics / activity steps to be tidy.
"""
from __future__ import annotations

import re
from typing import Any

from schema import (
    Activity,
    Block,
    Chapter,
    EventRevisionItem,
    GlossaryItem,
    Topic,
)

LIMITS = {
    "summary": 720,
    "overview": 520,
    "topic_title": 140,
    "topic_range": 110,
    "paragraph_text": 2400,
    "bullet_item": 360,
    "table_cell_header": 60,
    "table_cell": 320,
    "flow_label": 70,
    "flow_detail": 220,
    "definition_term": 60,
    "definition_text": 320,
    "callout_text": 520,
    "block_title": 120,
    "glossary_term": 60,
    "glossary_def": 280,
    "activity_title": 110,
    "activity_aim": 260,
    "activity_cell": 320,
    "activity_why": 360,
    "activity_obs": 320,
    "activity_inf": 320,
    "recap_item": 260,
    "excerpt_text": 1400,
    "excerpt_ref": 110,
    "excerpt_expl": 360,
    "problem_statement": 520,
    "problem_step": 320,
    "problem_answer": 180,
    "problem_approach": 360,
    "qa_question": 320,
    "qa_answer": 700,
    "part_label": 120,
    "revision_title": 100,
    "revision_period": 40,
    "revision_event": 900,
    "revision_causes": 650,
    "revision_effects": 650,
}

# Block types that count as "structured" (vs prose).
_STRUCTURED = {
    "table", "flowchart", "definitions", "worked_example",
    "bullets", "callout", "excerpt", "problem_set",
    "venn", "pyramid", "timeline", "cycle", "tree",
}

# Diagram block types (richer visuals).
_DIAGRAMS = {"flowchart", "cycle", "timeline", "pyramid", "venn", "tree"}

# Reflective NCERT prompts we never reproduce as content.
_REFLECTIVE = re.compile(
    r"(?i)\b(think\s+(it\s+over|about\s+it)|let\s+us\s+(discuss|explore)|"
    r"pause\s+and\s+ponder|discuss\s+in\s+class|find\s+out|the\s+quest\s+continues)\b"
)

# A bullet/cell that is really just a figure caption pointer, e.g.
# "Dudhsagar waterfall and railway bridge, Goa: Fig. 1.1".
_FIGURE_CAPTION = re.compile(r"(?i)[:\-–—]\s*\(?fig(?:ure)?s?\.?\s*\d")
_FIGURE_ONLY = re.compile(r"(?i)^\(?\s*(see\s+)?fig(?:ure)?s?\.?\s*\d+(\.\d+)?[a-z]?\)?\s*$")

_TITLE_SMALL = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "of", "on", "or", "over", "the", "to", "vs", "via", "with",
}


def _strip_enumerator(text: str) -> str:
    """Remove a leading textbook enumerator like 'Q4.', 'Example 5.',
    'Exercise 1.2', '(iv)' so it doesn't collide with our own 'Example N.'."""
    return re.sub(
        r"^\s*(?:Q\.?\s*\d+|Example\s*\d+|Ex(?:ercise)?\.?\s*[\d.]+|\([ivxlc]+\))[\.\):]?\s*",
        "", str(text or ""), flags=re.IGNORECASE,
    )


def title_case(text: str) -> str:
    """Headline-style title case that preserves acronyms, numbers (2-D, 3D),
    and small joining words in the middle."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return text
    words = text.split(" ")
    n = len(words)
    out: list[str] = []
    for i, w in enumerate(words):
        low = w.lower()
        if i not in (0, n - 1) and low in _TITLE_SMALL:
            out.append(low)
        elif any(ch.isdigit() for ch in w) or (w.isupper() and len(w) > 1):
            out.append(w)  # keep 2-D, 3D, NCERT, x-axis-style tokens with digits
        else:
            parts = w.split("-")
            new_parts = []
            for k, pp in enumerate(parts):
                if not pp:
                    new_parts.append(pp)
                elif pp.lower() in _TITLE_SMALL and not (i == 0 and k == 0):
                    new_parts.append(pp.lower())
                else:
                    new_parts.append(pp[:1].upper() + pp[1:])
            out.append("-".join(new_parts))
    return " ".join(out)


class Refiner:
    NO_ACTIVITIES_SUBJECTS = {"Mathematics", "English"}
    FLOW_RESERVED_SUBJECTS = {"Mathematics", "English", "Social Science"}
    _GENERIC_ACTIVITY = re.compile(
        r"^(?:let\s+us\s+(?:investigate|experiment|try|do|observe|find out|learn|study|"
        r"enhance|practice|estimate|collect|prepare|perform|record|compare|identify|"
        r"demonstrate|explore|examine|measure|test|find|discover|discuss|analyse|analyze)"
        r"|activity\s*\d+|do it yourself|hands[- ]on|practical\s*\d*|experiment\s*\d*)$",
        re.I,
    )

    def refine(self, chapter: Chapter) -> Chapter:
        chapter.summary = self._clip_inline(chapter.summary, LIMITS["summary"])
        chapter.study_strategy = [
            self._clip_inline(s, 240) for s in chapter.study_strategy[:6] if s.strip()
        ]
        chapter.glossary = self._refine_glossary(chapter.glossary)

        topics_out: list[Topic] = []
        for idx, topic in enumerate(chapter.topics, start=1):
            self._refine_topic(topic, chapter.subject, idx)
            if topic.title and (topic.blocks or topic.overview):
                topics_out.append(topic)
        chapter.topics = topics_out

        chapter.quick_recap = [
            self._clip_inline(r, LIMITS["recap_item"]) for r in chapter.quick_recap[:14]
        ]
        if not chapter.quick_recap:
            chapter.quick_recap = self._fallback_recap(chapter)
        if chapter.subject == "Social Science":
            chapter.event_revision = self._refine_event_revision(chapter.event_revision)
            if not chapter.event_revision:
                chapter.event_revision = self._synthesize_event_revision(chapter)
        chapter.chapter_mindmap = None
        return chapter

    # ---- topics --------------------------------------------------------

    def _normalise_part(self, part: str) -> str:
        """Keep only valid 'Prose — <title>' / 'Poem — <title>' part labels.

        The model occasionally emits non-conforming labels (section names,
        'Writing Task', etc.) which would otherwise render as spurious divider
        banners. Such values are dropped (returned as '')."""
        p = self._clip_inline(part, LIMITS["part_label"])
        m = re.match(r"^\s*(Prose|Poem)\s*[—–-]\s*(.+)$", p, flags=re.IGNORECASE)
        if not m:
            return ""
        kind = m.group(1).capitalize()
        title = m.group(2).strip()
        return f"{kind} — {title}" if title else ""

    def _refine_topic(self, topic: Topic, subject: str, idx: int) -> None:
        topic.number = f"{idx:02d}"
        raw_title = self._clip_inline(topic.title, LIMITS["topic_title"])
        # Topics should be named by concept, not by "Exercise Set 1.1".
        stripped = re.sub(
            r"^Exercise\s+(?:Set\s+)?[\d.]+\s*(?:and|:|[-–—])?\s*",
            "", raw_title, flags=re.IGNORECASE,
        ).strip()
        topic.title = title_case(stripped or raw_title)
        topic.range = self._clip_inline(topic.range, LIMITS["topic_range"])
        # `part` (Prose/Poem section label) is an English-only device that drives
        # the part-divider banners. Only accept well-formed "Prose — <title>" /
        # "Poem — <title>" labels; any other value (a stray "Topic 01", or junk
        # like "Vocabulary and Structures in Context", "Writing Task") is cleared
        # so it can never be rendered as a divider banner above a topic header.
        topic.part = (
            self._normalise_part(getattr(topic, "part", ""))
            if subject == "English" else ""
        )
        topic.overview = self._clip_inline(topic.overview, LIMITS["overview"])

        kept: list[Block] = []
        for blk in topic.blocks:
            norm = self._normalise_block(blk, subject)
            if norm is None:
                continue
            if not self._block_belongs(norm, subject):
                continue
            kept.append(norm)

        # problem_set already carries its own approach flowchart — drop redundant ones.
        if subject in {"Mathematics", "Science"} and any(b.type == "problem_set" for b in kept):
            kept = [b for b in kept if b.type != "flowchart"]

        # Minimal safety net only: if the topic ended up with NOTHING renderable
        # but we do have an overview, seed a single paragraph so the topic isn't
        # blank. We deliberately do NOT force a "Key points" list or a paragraph
        # when structured blocks already exist — forcing template sections is the
        # exact pattern-stuffing we're trying to avoid.
        if not kept and topic.overview:
            kept.append(Block("paragraph", "", {"text": topic.overview}))

        topic.blocks = kept[:14]

        # Activities live inside the topic now. Drop them for math/english,
        # otherwise normalise.
        if subject in self.NO_ACTIVITIES_SUBJECTS:
            topic.activities = []
        else:
            cleaned: list[Activity] = []
            for act in topic.activities:
                if self._refine_activity(act):
                    cleaned.append(act)
            topic.activities = cleaned[:3]

    # ---- block normalisation ------------------------------------------

    def _normalise_block(self, block: Block, subject: str) -> Block | None:
        bt = block.type
        title = self._clip_inline(block.title or "", LIMITS["block_title"])
        d = block.data or {}

        if bt == "paragraph":
            text = self._clip_paragraph(d.get("text", ""), LIMITS["paragraph_text"])
            return Block(bt, title, {"text": text}) if text else None

        if bt == "bullets":
            raw_items = [str(i).strip() for i in d.get("items", []) if str(i).strip()]
            items = []
            for it in raw_items:
                # Drop pure figure-caption pointers — the reader has no figures.
                if _FIGURE_ONLY.match(it) or _FIGURE_CAPTION.search(it):
                    continue
                if _REFLECTIVE.search(it):
                    continue
                items.append(self._clip_inline(it, LIMITS["bullet_item"]))
            # A "dates/numbers to remember" style list with no real dates/numbers
            # is filler — drop the whole block.
            if re.search(r"(?i)date|number", title) and items:
                if sum(1 for it in items if re.search(r"\d", it)) < max(1, len(items) // 2):
                    return None
            return Block(bt, title, {"items": items[:10]}) if len(items) >= 2 else None

        if bt == "table":
            cols = [
                self._clip_inline(c, LIMITS["table_cell_header"])
                for c in d.get("columns", [])
                if str(c).strip()
            ]
            rows = []
            for row in (d.get("rows") or [])[:12]:
                if isinstance(row, list) and len(row) >= 2:
                    rows.append([
                        self._clip_inline(c, LIMITS["table_cell"])
                        for c in row[: len(cols) or 5]
                    ])
            if cols and rows:
                return Block(bt, title, {"columns": cols[:5], "rows": rows})
            return None

        if bt == "flowchart":
            steps = self._clip_steps(d.get("steps") or [], max_steps=6)
            if len(steps) < 2:
                return None
            orient = d.get("orientation", "vertical")
            if orient not in ("horizontal", "vertical"):
                orient = "horizontal" if len(steps) <= 4 else "vertical"
            return Block(bt, title, {"steps": steps, "orientation": orient})

        if bt == "definitions":
            items = []
            for it in (d.get("items") or [])[:10]:
                if isinstance(it, dict) and it.get("term") and it.get("definition"):
                    items.append({
                        "term": self._clip_inline(it["term"], LIMITS["definition_term"]),
                        "definition": self._clip_inline(it["definition"], LIMITS["definition_text"]),
                    })
            return Block(bt, title, {"items": items}) if len(items) >= 2 else None

        if bt == "callout":
            text = self._clip_inline(d.get("text", ""), LIMITS["callout_text"])
            # Drop reproduced reflective prompts and bare questions with no info.
            if _REFLECTIVE.search(title) or _REFLECTIVE.search(text):
                return None
            if text.endswith("?") and len(text) < 90:
                return None
            tone = d.get("tone") or d.get("style") or "note"
            if tone not in {"note", "warning", "tip", "formula", "example", "exam_alert"}:
                tone = "note"
            return Block(bt, title, {"text": text, "tone": tone}) if text else None

        if bt == "worked_example":
            statement = self._clip_paragraph(d.get("statement", "") or d.get("given", ""),
                                             LIMITS["problem_statement"])
            if subject == "Science" and not self._looks_numerical(statement):
                return None
            steps_raw = d.get("steps") or d.get("solution") or []
            steps = [self._clip_inline(s, LIMITS["problem_step"]) for s in steps_raw if str(s).strip()]
            answer = self._clip_inline(d.get("answer", ""), LIMITS["problem_answer"])
            if statement and (steps or answer):
                return Block(bt, title, {"statement": statement, "steps": steps[:10], "answer": answer})
            return None

        if bt == "problem_set":
            if subject not in {"Mathematics", "Science"}:
                return None
            problems = []
            for prob in (d.get("problems") or [])[:8]:
                if not isinstance(prob, dict):
                    continue
                statement = _strip_enumerator(
                    self._clip_paragraph(prob.get("statement", ""), LIMITS["problem_statement"]))
                if subject == "Science" and not self._looks_numerical(statement):
                    continue
                steps = [
                    self._clip_inline(s, LIMITS["problem_step"])
                    for s in (prob.get("steps") or []) if str(s).strip()
                ]
                answer = self._clip_inline(prob.get("answer", ""), LIMITS["problem_answer"])
                if statement and (steps or answer):
                    problems.append({"statement": statement, "steps": steps[:8], "answer": answer})
            type_name = title_case(self._clip_inline(d.get("type_name", title), LIMITS["block_title"]))
            approach_steps: list[dict[str, str]] = []
            raw_steps = d.get("approach_steps")
            if not raw_steps and d.get("approach"):
                raw_steps = re.split(r"(?<=[.;])\s+", str(d.get("approach")))
            for s in (raw_steps or [])[:6]:
                if isinstance(s, dict):
                    lab = self._clip_inline(s.get("label", ""), 70)
                    det = self._clip_inline(s.get("detail", ""), 90)
                    if lab or det:
                        approach_steps.append({"label": lab, "detail": det})
                elif str(s).strip():
                    approach_steps.append({"label": self._clip_inline(s, 70), "detail": ""})
            if problems:
                return Block(
                    bt,
                    type_name,
                    {
                        "type_name": type_name,
                        "approach_steps": approach_steps,
                        "problems": problems,
                    },
                )
            return None

        if bt == "excerpt":
            text = self._clip_excerpt(d.get("text", ""), LIMITS["excerpt_text"])
            reference = self._clip_inline(d.get("reference", ""), LIMITS["excerpt_ref"])
            kind = d.get("kind", "prose")
            if kind not in {"verse", "prose", "quote"}:
                kind = "prose"
            explanation = self._clip_inline(d.get("explanation", ""), LIMITS["excerpt_expl"])
            if text:
                return Block(bt, title, {"text": text, "reference": reference,
                                          "kind": kind, "explanation": explanation})
            return None

        if bt == "qa":
            items = []
            raw_items = d.get("items") or d.get("questions") or []
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                q = self._clip_inline(it.get("question", "") or it.get("q", ""),
                                      LIMITS["qa_question"])
                a = self._clip_paragraph(it.get("answer", "") or it.get("a", ""),
                                         LIMITS["qa_answer"])
                if not q:
                    continue
                kind = str(it.get("kind", "")).strip().lower()
                if kind not in {"critical", "analytical", "creative"}:
                    kind = ""
                items.append({"question": q, "answer": a, "kind": kind})
            return Block(bt, title or "Think and Respond", {"items": items[:5]}) if items else None

        if bt in ("flowchart_steps_placeholder",):  # never used; keeps mypy calm
            return None

        if bt == "cycle":
            steps = self._clip_steps(d.get("steps") or [], max_steps=6)
            return Block(bt, title, {"steps": steps}) if len(steps) >= 2 else None

        if bt == "timeline":
            # Builder sometimes mislabels a flowchart as a timeline (steps, not events).
            if not d.get("events") and d.get("steps"):
                steps = self._clip_steps(d.get("steps") or [], max_steps=8)
                if len(steps) >= 2:
                    return Block("flowchart", title, {"steps": steps, "orientation": "vertical"})
            events = []
            for ev in (d.get("events") or [])[:8]:
                if not isinstance(ev, dict):
                    continue
                title_t = self._clip_inline(ev.get("title", ""), 120)
                if not title_t:
                    continue
                events.append({
                    "date": self._clip_inline(ev.get("date", ""), 26),
                    "title": title_t,
                    "detail": self._clip_inline(ev.get("detail", ""), 200),
                })
            return Block(bt, title, {"events": events}) if len(events) >= 2 else None

        if bt == "pyramid":
            levels = []
            for lv in (d.get("levels") or [])[:6]:
                if not isinstance(lv, dict):
                    continue
                lab = self._clip_inline(lv.get("label", ""), 60)
                if not lab:
                    continue
                levels.append({"label": lab, "detail": self._clip_inline(lv.get("detail", ""), 80)})
            direction = "down" if str(d.get("direction", "up")).lower() == "down" else "up"
            return Block(bt, title, {"levels": levels, "direction": direction}) if len(levels) >= 2 else None

        if bt == "venn":
            # Left/right lobes are wide; the central overlap is narrow, so its
            # items must stay short or they truncate awkwardly.
            left = [self._clip_inline(x, 46) for x in (d.get("left") or []) if str(x).strip()][:6]
            both = [self._clip_inline(x, 30) for x in (d.get("both") or []) if str(x).strip()][:5]
            right = [self._clip_inline(x, 46) for x in (d.get("right") or []) if str(x).strip()][:6]
            if left and right:
                return Block(bt, title, {
                    "left_title": self._clip_inline(d.get("left_title", "A"), 40),
                    "right_title": self._clip_inline(d.get("right_title", "B"), 40),
                    "left": left, "both": both, "right": right,
                })
            return None

        if bt == "tree":
            root = d.get("root")
            if not isinstance(root, dict):
                # tolerate a flat {nodes:[...]} or {children:[...]} shape
                children = d.get("children") or d.get("nodes")
                if isinstance(children, list) and children:
                    root = {"label": title or "Overview", "children": children}
                else:
                    return None
            cleaned = self._clip_tree(root, depth=0, counter=[0], max_depth=3, max_nodes=12)
            if cleaned and (cleaned.get("children") or cleaned.get("label")):
                return Block(bt, title, {"root": cleaned})
            return None

        return None

    def _block_belongs(self, block: Block, subject: str) -> bool:
        # English: never allow per-topic definitions tables — they duplicate the
        # main glossary and read as filler ("the I'm-not-glossary glossary").
        if block.type == "definitions" and subject == "English":
            return False
        # Mathematics: Venn and pyramid almost never fit and read as forced.
        # Math compares with tables, sequences with flowcharts, and classifies
        # with trees.
        if block.type in ("venn", "pyramid") and subject == "Mathematics":
            return False
        if block.type == "pyramid" and subject == "English":
            return False
        # Question-and-answer blocks are an English close-reading device only.
        if block.type == "qa":
            return subject == "English"
        if block.type == "flowchart" and subject in self.FLOW_RESERVED_SUBJECTS:
            return self._looks_process_like(block, subject)
        if block.type == "worked_example" and subject not in {"Mathematics", "Science"}:
            return False
        if block.type == "worked_example" and subject == "Science":
            stmt = block.data.get("statement", "") or block.data.get("given", "")
            return self._looks_numerical(str(stmt))
        if block.type == "problem_set" and subject not in {"Mathematics", "Science"}:
            return False
        if block.type == "problem_set" and subject == "Science":
            probs = block.data.get("problems") or []
            return any(self._looks_numerical(str(p.get("statement", ""))) for p in probs if isinstance(p, dict))
        if block.type == "excerpt" and subject not in {"English"}:
            # Excerpts also work for Social Science (primary sources).
            return subject == "Social Science"
        return True

    def _looks_process_like(self, block: Block, subject: str) -> bool:
        title = (block.title or "").lower()
        steps = block.data.get("steps") or []
        labels = " ".join(
            str(s.get("label", "") if isinstance(s, dict) else s).lower() for s in steps
        )
        text = f"{title} {labels}"
        if subject == "Mathematics":
            keys = ("how to", "procedure", "steps to", "plot", "construct", "find",
                    "solve", "calculate", "draw", "algorithm")
            return any(k in text for k in keys)
        if subject == "English":
            keys = ("plot", "story arc", "narrative", "sequence")
            return any(k in text for k in keys)
        if subject == "Social Science":
            keys = ("cycle", "process", "formation", "flow", "stages", "sequence",
                    "how", "steps", "chain", "evolution")
            return any(k in text for k in keys)
        if subject == "Science":
            keys = ("pathway", "process", "mechanism", "steps", "flow", "cycle",
                    "transport", "filtration", "digestion", "secretion", "formation")
            return any(k in text for k in keys) or len(steps) >= 2
        return True

    def _looks_numerical(self, text: str) -> bool:
        """True when text looks like a calculation problem, not a label/describe task."""
        t = str(text or "").lower()
        if not t.strip():
            return False
        non_numerical = (
            "label", "name the", "identify the", "draw a", "draw the",
            "explain why", "explain how", "describe", "state whether",
            "give reason", "give two", "list the", "match the",
        )
        has_digit = bool(re.search(r"\d", t))
        # Spelled-out quantities ("twenty-five cells", "two metres") still count.
        spelled = bool(re.search(
            r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
            r"twenty|thirty|forty|fifty|hundred|thousand|half|double|twice)\b", t))
        calc_hint = bool(re.search(
            r"(calculate|compute|find the|work out|determine|estimate|how much|how many|"
            r"convert|magnification|density|mass|volume|speed|velocity|distance|force|"
            r"work done|energy|power|pressure|frequency|wavelength|time taken|"
            r"percentage|ratio|formula|=|×|÷|√|\^|"
            r"\d+\s*(?:cm|mm|nm|µm|μm|m|km|g|kg|mg|ml|l|s|hz|khz|j|kj|w|kw|n|pa|%|°))",
            t,
        ))
        if any(p in t for p in non_numerical) and not calc_hint:
            return False
        if (has_digit or spelled) and calc_hint:
            return True
        # An explicit equation/relation with numbers is numerical even without keywords.
        if has_digit and re.search(r"[=×÷√^]|\d\s*/\s*\d", t):
            return True
        return bool(re.search(
            r"(calculate|compute|how much|how many|work out|"
            r"find the (?:mass|volume|density|speed|velocity|distance|force|energy|"
            r"power|pressure|frequency|wavelength|magnification|time))",
            t,
        ))

    def _refine_mindmap(self, raw: dict | None, chapter: Chapter | None = None) -> dict | None:
        if chapter and chapter.subject == "Science":
            built = self._build_chapter_mindmap(chapter, raw)
            if built:
                return built
        if not isinstance(raw, dict):
            return None
        root = raw.get("root") if isinstance(raw.get("root"), dict) else raw
        if not isinstance(root, dict):
            return None
        cleaned = self._clip_tree(root, depth=0, counter=[0], max_depth=4, max_nodes=40)
        return {"root": cleaned} if cleaned else None

    def _build_chapter_mindmap(self, chapter: Chapter, raw: dict | None = None) -> dict | None:
        """Assemble a deep revision tree from built topics, blocks, and activities."""
        existing = raw.get("root") if isinstance(raw, dict) and isinstance(raw.get("root"), dict) else {}
        root_label = self._clip_inline(existing.get("label") or chapter.chapter_title, 90)
        root_detail = self._clip_inline(
            existing.get("detail") or (chapter.summary.split(".")[0] if chapter.summary else ""),
            60,
        )
        children: list[dict] = []
        for topic in chapter.topics:
            topic_node: dict = {
                "label": topic.title,
                "detail": self._clip_inline((topic.overview or "").split(".")[0], 55),
                "children": [],
            }
            sub: list[dict] = []
            for block in topic.blocks:
                bt = block.type
                title = (block.title or "").strip()
                if title and bt in {
                    "table", "flowchart", "cycle", "tree", "venn", "pyramid",
                    "callout", "worked_example", "problem_set", "bullets", "definitions",
                }:
                    detail = bt.replace("_", " ").title()
                    if bt == "callout":
                        detail = str(block.data.get("tone", "note")).replace("_", " ").title()
                    sub.append({"label": title, "detail": self._clip_inline(detail, 40)})
                elif bt == "definitions":
                    for it in (block.data.get("items") or [])[:4]:
                        term = str(it.get("term", "")).strip()
                        if term:
                            sub.append({
                                "label": self._clip_inline(term, 50),
                                "detail": "Definition",
                            })
            for act in topic.activities:
                if act.title:
                    sub.append({"label": act.title, "detail": "Activity"})
            topic_node["children"] = sub[:8]
            children.append(topic_node)
        if chapter.glossary:
            children.append({
                "label": "Key Vocabulary",
                "children": [
                    {
                        "label": g.term,
                        "detail": self._clip_inline(g.definition, 40),
                    }
                    for g in chapter.glossary[:12]
                ],
            })
        if chapter.quick_recap:
            children.append({
                "label": "Exam Essentials",
                "children": [
                    {"label": self._clip_inline(r, 70), "detail": ""}
                    for r in chapter.quick_recap[:8]
                ],
            })
        root = {"label": root_label, "children": children}
        if root_detail:
            root["detail"] = root_detail
        cleaned = self._clip_tree(root, depth=0, counter=[0], max_depth=4, max_nodes=40)
        return {"root": cleaned} if cleaned else None

    def _is_generic_activity_title(self, title: str) -> bool:
        t = re.sub(r"\s+", " ", str(title or "")).strip()
        if not t:
            return True
        if re.match(r"let\s+us\b", t, re.I):
            return True
        return bool(self._GENERIC_ACTIVITY.match(t))

    def _derive_activity_title(self, activity: Activity) -> str:
        for source in (activity.aim, activity.inference, activity.observation):
            text = re.sub(r"\s+", " ", str(source or "")).strip()
            if not text:
                continue
            text = re.sub(r"^To\s+", "", text, flags=re.I)
            sentence = re.split(r"[.!?]", text)[0].strip()
            if len(sentence) >= 12:
                return title_case(self._clip_inline(sentence, LIMITS["activity_title"]))
        return ""

    # ---- activities ----------------------------------------------------

    def _refine_activity(self, activity: Activity) -> bool:
        activity.title = self._clip_inline(activity.title, LIMITS["activity_title"])
        if self._is_generic_activity_title(activity.title):
            derived = self._derive_activity_title(activity)
            if derived:
                activity.title = derived
        activity.aim = self._clip_inline(activity.aim, LIMITS["activity_aim"])
        activity.materials = [
            self._clip_inline(m, 70) for m in activity.materials[:10] if str(m).strip()
        ]
        proc = []
        for i, row in enumerate(activity.procedure[:10], start=1):
            detail = row.get("detail") if isinstance(row, dict) else str(row)
            if not detail:
                continue
            step_row: dict[str, str] = {
                "step": str(i),
                "detail": self._clip_inline(detail, LIMITS["activity_cell"]),
            }
            if isinstance(row, dict):
                why = str(row.get("why", "")).strip()
                why = re.sub(r"^\*\*Why this step:\*\*\s*", "", why, flags=re.I)
                why = re.sub(r"^Why this step:\s*", "", why, flags=re.I)
                if why:
                    step_row["why"] = self._clip_inline(why, LIMITS["activity_why"])
            proc.append(step_row)
        activity.procedure = proc
        activity.observation = self._clip_inline(activity.observation, LIMITS["activity_obs"])
        activity.inference = self._clip_inline(activity.inference, LIMITS["activity_inf"])
        return bool(activity.title and (proc or activity.aim))

    # ---- glossary ------------------------------------------------------

    def _refine_glossary(self, items: list[GlossaryItem]) -> list[GlossaryItem]:
        seen: set[str] = set()
        out: list[GlossaryItem] = []
        for it in items:
            term = self._clip_inline(it.term, LIMITS["glossary_term"])
            key = term.lower()
            if not term or key in seen:
                continue
            seen.add(key)
            out.append(GlossaryItem(
                term=term,
                definition=self._clip_inline(it.definition, LIMITS["glossary_def"]),
            ))
            if len(out) >= 32:
                break
        return out

    # ---- Social Science event revision ---------------------------------

    def _refine_event_revision(self, items: list[EventRevisionItem]) -> list[EventRevisionItem]:
        out: list[EventRevisionItem] = []
        seen: set[str] = set()
        for it in items:
            title = self._clip_inline(it.title, LIMITS["revision_title"])
            event = self._clip_paragraph(it.event, LIMITS["revision_event"])
            if not title or not event:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(EventRevisionItem(
                title=title_case(title),
                period=self._clip_inline(it.period, LIMITS["revision_period"]),
                event=event,
                causes=self._clip_paragraph(it.causes, LIMITS["revision_causes"]),
                effects=self._clip_paragraph(it.effects, LIMITS["revision_effects"]),
            ))
            if len(out) >= 12:
                break
        return out

    def _synthesize_event_revision(self, chapter: Chapter) -> list[EventRevisionItem]:
        title_low = chapter.chapter_title.lower()
        if "maratha" in title_low:
            return self._marathas_event_revision()
        return self._collect_events_from_topics(chapter)

    def _collect_events_from_topics(self, chapter: Chapter) -> list[EventRevisionItem]:
        """One revision block per topic — grouped, not one row per incident."""
        items: list[EventRevisionItem] = []
        for topic in chapter.topics:
            event_bits: list[str] = []
            effect_bits: list[str] = []
            for block in topic.blocks:
                if block.type == "timeline":
                    for ev in (block.data.get("events") or [])[:8]:
                        date = str(ev.get("date", "")).strip()
                        title = str(ev.get("title", "")).strip()
                        detail = str(ev.get("detail", "")).strip()
                        if title:
                            line = f"{date}: {title}" if date else title
                            if detail:
                                line += f" — {detail}"
                            event_bits.append(line)
                elif block.type == "flowchart":
                    for step in (block.data.get("steps") or [])[:8]:
                        lab = str(step.get("label", "")).strip()
                        det = str(step.get("detail", "")).strip()
                        if lab:
                            event_bits.append(f"{lab}: {det}" if det else lab)
            if not event_bits and not topic.overview:
                continue
            event_text = " ".join(event_bits) if event_bits else topic.overview
            items.append(EventRevisionItem(
                title=topic.title,
                event=event_text,
                causes=topic.overview.split(".")[0] + "." if topic.overview else "",
                effects=" ".join(effect_bits),
            ))
        return self._refine_event_revision(items)

    def _marathas_event_revision(self) -> list[EventRevisionItem]:
        blocks = [
            (
                "Maratha Identity and the Bhakti Foundation",
                "7th–17th century",
                "The Marathas emerged in the Deccan with Marathi as their identity marker. A literary tradition continued from the 12th century under the Yadavas of Devagiri. After the Khilji conquest in the 14th century, regional identity persisted. The bhakti movement flourished for centuries — saints such as Dnyaneshwar, Namdev, Tukaram, and Ramdas composed in the language of the masses, translated the Upanishads and Bhagavad Gītā into Marathi, and spread devotion beyond empty ritual.",
                "The Deccan plateau gave the Marathas a distinct homeland. Long literary continuity and the bhakti emphasis on personal devotion in the common tongue brought ordinary people into a shared cultural world.",
                "A shared moral and cultural language developed across Maharashtra. Social organisation and political awareness grew long before military power appeared, laying the foundation for later political unity.",
            ),
            (
                "Shivaji's Rise and Military Innovation",
                "1630–1660",
                "Shivaji was born in 1630 into the Bhonsle clan and was shaped by his mother Jijabai while his father Shahji served distant sultanates. From age 16 he began capturing neglected forts around Pune and strengthening their defences. He built a navy to secure the west coast, adopted guerrilla warfare using hills, forests, and forts, killed Afzal Khan at Pratapgad in 1659, and carried out a daring night raid on Shaista Khan's camp during Aurangzeb's three-year invasion.",
                "The Pune jāgīr was troubled by infighting among Deccan sultanates. Weak control over unoccupied forts gave Shivaji an opening. Bijapur sent Afzal Khan to crush the Marathas, and later the Mughals under Shaista Khan launched a sustained invasion. The geography of the Deccan favoured mobile, surprise attacks over pitched battles.",
                "Swarajya took shape as a sovereign vision, not merely territorial gain. The Marathas proved that smaller, fast-moving forces could defeat much larger armies. Military reputation and morale rose sharply across the region.",
            ),
            (
                "Mughal Confrontation and Formal Sovereignty",
                "1665–1674",
                "Shivaji was defeated at Purandar and signed a treaty with Jai Singh, losing territory and sending Sambhaji into Mughal service. He sacked the wealthy port of Surat, was insulted and placed under house arrest at the Agra court, escaped hidden in gift baskets with Sambhaji, was crowned at Raigad in 1674 as Shri Raja Shiva Chhatrapati with full Vedic rites, launched the dakshina-digvijaya into south India, and opposed Dutch slave trading on the west coast.",
                "Jai Singh's siege forced a tactical compromise. Shivaji needed wealth and was drawn to Agra by Mughal persuasion, only to face deliberate humiliation under Aurangzeb. Escape required patience and planning rather than open battle. Formal coronation was needed to declare sovereign status before the world.",
                "Temporary setbacks did not destroy Swarajya. The Agra episode exposed Mughal arrogance and Shivaji's refusal to accept permanent subordination. Coronation gave him legitimate kingship; the southern campaign created strategic depth against future Mughal pressure from the north.",
            ),
            (
                "Government, Revenue, and Military Organisation",
                "Shivaji's reign",
                "Shivaji abolished hereditary official posts and paid salaries from the state treasury, with periodic transfers and pensions for soldiers' families. The aṣṭa pradhāna maṇḍala — a council of eight ministers — ran key departments. Chauth (25%) and sardeshmukhi (10%) were collected from neighbouring lands in return for protection. The army combined infantry, cavalry, and navy; forts were the core of defence. Kanhoji Angre won naval victories and the Marathas issued cartaz trade passes to challenge European sea control.",
                "Hereditary land assignments had bred local oppression. A central treasury was needed to fund armies and administration. European powers dominated west-coast trade and demanded cartaz passes from others — the Marathas reversed that pressure.",
                "Officials served the state rather than personal estates. Steady revenue from chauth and sardeshmukhi funded expansion. Naval strength protected the coastline and contested European dominance.",
            ),
            (
                "Justice, Trade, and Everyday Rule",
                "Shivaji's reign",
                "Local panchayats delivered justice with moderation in capital punishment; dissatisfied parties could appeal to a Maratha chief. Kotwals policed towns. Roads, ferries, and bridges were maintained. Maratha ships reached Mocha, Muscat, and Malacca carrying gold, textiles, and other cargo.",
                "A growing state needed everyday order to sustain commerce and loyalty. Safe routes and local justice made rule felt beyond the battlefield.",
                "Ordinary administration gave the Maratha kingdom legitimacy. Trade wealth supported military and political ambitions.",
            ),
            (
                "After Shivaji: Crisis, Expansion, and Panipat",
                "1680–1761",
                "After Shivaji's death in 1680, Sambhaji became Chhatrapati but was captured, tortured, and executed during Aurangzeb's long Deccan campaign; Raigad fell. Rajaram fled to Gingee and Tarabai organised counterattacks deep into Mughal territory. Power decentralised under regional chiefs and the Peshwa rose to dominate. Bajirao I and Nanasaheb Peshwa drove expansion across much of India. At Panipat in 1761 the Marathas suffered a disastrous defeat that checked their northern advance.",
                "Aurangzeb was determined to destroy the Maratha state after Shivaji. Succession crises and Mughal pressure forced resistance to shift south. Regional chiefs grew powerful as the centre weakened. Overextension in the north set the stage for Panipat.",
                "The state survived its gravest crisis and eventually reached much of India under the Peshwas. Panipat permanently checked northern expansion and marked a turning point in Maratha fortunes.",
            ),
            (
                "Decline and British Conquest",
                "1771–1818",
                "Mahadji Shinde recaptured Delhi in 1771, briefly restoring Maratha prestige in the north. Three Anglo-Maratha Wars between 1775 and 1818 ended with British victory and the end of Maratha political power.",
                "Recovery under Peshwa Madhavrao I allowed a northern resurgence, but internal disunity among Maratha chiefs persisted. The British combined military organisation, diplomacy, and exploitation of divisions.",
                "Delhi returned to Maratha control for a time, but repeated wars with the British ended the confederacy. British rule replaced Maratha power across the former empire.",
            ),
            (
                "Cultural Revival and Women Leaders",
                "17th–18th century",
                "Shivaji used Sanskrit on his seal, promoted Marathi through works such as the Rajya-Vyavahara-Koshha, reduced Persian loanwords, and rebuilt temples while respecting other faiths. Tarabai led resistance and sent armies northward after Rajaram's death. Ahilyabai Holkar governed wisely, restored temples including Kashi Vishwanath and Somnath, and supported roads, wells, and the Maheshwar weaving tradition. Ekoji established Maratha rule in Thanjavur; Serfoji II patronised medicine, printing, music, dance, and inscription work in a multilingual, syncretic court.",
                "Political sovereignty needed cultural assertion alongside military power. Leadership vacuums after Rajaram's death were filled by capable queens and regional rulers who saw patronage as a duty of kingship.",
                "Maratha rule left a lasting mark on language, literature, art, and regional identity. Women leaders and southern courts showed that Maratha influence extended well beyond the battlefield into culture and everyday life.",
            ),
        ]
        return [
            EventRevisionItem(title=t, period=p, event=e, causes=c, effects=f)
            for t, p, e, c, f in blocks
        ]

    # ---- helpers -------------------------------------------------------

    def _fallback_recap(self, chapter: Chapter) -> list[str]:
        out: list[str] = []
        for t in chapter.topics:
            if t.overview:
                out.append(self._clip_inline(f"{t.title}: {t.overview}", LIMITS["recap_item"]))
            if len(out) >= 10:
                break
        return out

    def _split_into_bullets(self, text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        return [self._clip_inline(p, LIMITS["bullet_item"]) for p in parts if len(p) > 10][:6]

    def _clip_steps(self, steps_raw: list, max_steps: int = 6) -> list[dict[str, str]]:
        steps: list[dict[str, str]] = []
        for s in steps_raw[:max_steps + 1]:
            if isinstance(s, dict):
                label = str(s.get("label", "")).strip()
                detail = str(s.get("detail", "")).strip()
                if re.fullmatch(r"(?i)step\s*\d+", label):
                    label, detail = (detail, "") if detail else ("", "")
                if not (label or detail):
                    continue
                steps.append({
                    "label": self._clip_inline(label, LIMITS["flow_label"]),
                    "detail": self._clip_inline(detail, LIMITS["flow_detail"]),
                })
            elif isinstance(s, str) and s.strip():
                steps.append({"label": self._clip_inline(s, LIMITS["flow_label"]), "detail": ""})
        return steps[:max_steps]

    def _clip_tree(self, node: dict, depth: int, counter: list[int], *,
                     max_depth: int = 3, max_nodes: int = 12) -> dict | None:
        """Recursively clip a tree node; depth and node count are configurable."""
        if not isinstance(node, dict) or depth > max_depth or counter[0] >= max_nodes:
            return None
        label = self._clip_inline(node.get("label", ""), 90)
        if not label:
            return None
        counter[0] += 1
        clean = {"label": label}
        detail = self._clip_inline(node.get("detail", ""), 110)
        if detail:
            clean["detail"] = detail
        branch = self._clip_inline(node.get("branch", ""), 60)
        if branch:
            clean["branch"] = branch
        children = []
        for ch in (node.get("children") or []):
            if counter[0] >= max_nodes:
                break
            cc = self._clip_tree(ch, depth + 1, counter, max_depth=max_depth, max_nodes=max_nodes)
            if cc:
                children.append(cc)
        if children:
            clean["children"] = children
        return clean

    def _clip_inline(self, text: Any, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        return self._smart_truncate(text, limit)

    def _clip_paragraph(self, text: Any, limit: int) -> str:
        # Collapse newlines inside paragraphs to single spaces but preserve
        # blank-line separators between paragraphs (rendered as gaps).
        cleaned = re.sub(r"[ \t]+", " ", str(text or "")).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"(?<!\n)\n(?!\n)", " ", cleaned)
        return self._smart_truncate(cleaned, limit)

    def _clip_excerpt(self, text: Any, limit: int) -> str:
        # Preserve line breaks (vital for verse). Trim whitespace per line.
        lines = [ln.strip() for ln in str(text or "").splitlines()]
        cleaned = "\n".join(ln for ln in lines if ln)
        return self._smart_truncate(cleaned, limit, preserve_newlines=True)

    def _smart_truncate(self, text: str, limit: int, *, preserve_newlines: bool = False) -> str:
        if len(text) <= limit:
            return text
        truncated = text[: limit - 1]
        if preserve_newlines:
            cut = truncated.rfind("\n")
            if cut > limit * 0.5:
                return truncated[: cut].rstrip() + " …"
        cut = max(truncated.rfind(". "), truncated.rfind("? "), truncated.rfind("! "))
        if cut > limit * 0.55:
            return truncated[: cut + 1]
        return truncated.rsplit(" ", 1)[0] + " …"
