"""Two-pass GPT pipeline.

Pass 1 (planner): MMD → outline + inventories (problems, activities,
                   excerpts, cases) so we can hold the builder accountable.
Pass 2 (builder): MMD + plan → full Chapter JSON.

The same MMD goes into both passes; the plan is purely a contract so the
builder cannot quietly skip material.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from schema import (
    Activity,
    Block,
    Chapter,
    EventRevisionItem,
    GlossaryItem,
    Topic,
)
from subject_prompts import builder_system, chapter_shell_system, planner_system, topic_builder_system


class TokenBudgetExceeded(RuntimeError):
    pass


class GPTTruncationError(RuntimeError):
    """Raised when a single GPT response hit the output-token ceiling."""
    pass


_CONCISE_TOPIC_NOTE = (
    "IMPORTANT — return complete, valid JSON for this episode. Do not truncate "
    "mid-structure; include all required fields even if the chapter is long."
)


class GPTWriter:
    # Input/output budgets — use model maximum so textbook content is not truncated.
    MAX_MMD_CHARS = 500_000          # full Mathpix MMD sent to planner + builder
    _MAX_OUT = int(os.getenv("AEGIS_OPENAI_MAX_OUTPUT_TOKENS", "128000"))
    MAX_PLANNER_OUTPUT_TOKENS = _MAX_OUT
    MAX_BUILDER_OUTPUT_TOKENS = _MAX_OUT
    MAX_TOPIC_OUTPUT_TOKENS = _MAX_OUT
    MAX_SHELL_OUTPUT_TOKENS = _MAX_OUT

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.api_key = os.getenv(cfg.get("openai_api_key_env", "OPENAI_API_KEY"))
        self.model = cfg.get("openai_model", "gpt-5.4-mini-2026-03-17")
        self.base_url = cfg.get("openai_base_url")
        self.enabled = bool(self.api_key)
        self._client = None
        if self.enabled:
            from openai import OpenAI  # local import keeps the module light
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)

    # ---- public ---------------------------------------------------------

    def write(self, mmd: str, meta: dict, plan_cache_path: Path | None = None,
              raw_dump_path: Path | None = None) -> tuple[Chapter, str]:
        if not self.enabled:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        if len(mmd) > self.MAX_MMD_CHARS:
            raise TokenBudgetExceeded(
                f"MMD is {len(mmd):,} chars (> {self.MAX_MMD_CHARS:,}). "
                "Raise MAX_MMD_CHARS or split the source chapter."
            )

        plan = self._plan(mmd, meta, plan_cache_path)
        chapter, msg = self._build(mmd, meta, plan, raw_dump_path=raw_dump_path)
        return chapter, msg

    # ---- planner pass ---------------------------------------------------

    def _plan(self, mmd: str, meta: dict, plan_cache_path: Path | None) -> dict:
        if plan_cache_path and plan_cache_path.exists():
            try:
                return json.loads(plan_cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        user = (
            f"Subject: {meta['subject']}\nGrade: {meta['grade']}\n"
            f"Chapter title hint: {meta['chapter_title']}\n"
            f"Chapter number: {meta['chapter_number']}\n\n"
            "--- MMD START ---\n" + mmd + "\n--- MMD END ---"
        )
        plan_raw = self._chat(planner_system(), user, max_tokens=self.MAX_PLANNER_OUTPUT_TOKENS)
        plan = _parse_json(plan_raw, "planner")
        if plan_cache_path:
            plan_cache_path.parent.mkdir(parents=True, exist_ok=True)
            plan_cache_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan

    # ---- builder pass ---------------------------------------------------

    def _needs_chunked_build(self, meta: dict, plan: dict) -> bool:
        """Monolithic builder truncates large chapters — build topic-by-topic."""
        n_topics = len(plan.get("topics") or [])
        inventory = (
            len(plan.get("problem_inventory") or [])
            + len(plan.get("activity_inventory") or [])
            + len(plan.get("excerpt_inventory") or [])
            + len(plan.get("case_inventory") or [])
        )
        return n_topics >= 6 or inventory >= 20

    def _items_for_topic(self, plan: dict, inventory_key: str, topic_number: str) -> list:
        num = str(topic_number).zfill(2)
        return [
            item for item in plan.get(inventory_key) or []
            if str(item.get("topic_number", "")).zfill(2) == num
        ]

    def _problems_for_topic(self, plan: dict, topic_number: str) -> list:
        return self._items_for_topic(plan, "problem_inventory", topic_number)

    def _build_chapter_shell(self, meta: dict, plan: dict) -> dict:
        discipline = meta.get("discipline", "")
        user = (
            f"Chapter {meta['chapter_number']}: {meta['chapter_title']}\n"
            f"Subject: {meta['subject']} | Grade: {meta['grade']}\n"
            f"Discipline: {discipline or 'General Science'}\n\n"
            f"PLAN SUMMARY:\n{plan.get('summary', '')}\n\n"
            f"STUDY STRATEGY HINTS:\n"
            f"{json.dumps(plan.get('study_strategy') or [], ensure_ascii=False)}\n\n"
            f"GLOSSARY TERMS TO DEFINE:\n"
            f"{json.dumps(plan.get('glossary_terms') or [], ensure_ascii=False)}\n\n"
            f"QUICK RECAP HINTS:\n"
            f"{json.dumps(plan.get('quick_recap') or [], ensure_ascii=False)}"
        )
        raw = self._chat(
            chapter_shell_system(meta["subject"], meta.get("discipline", "")),
            user,
            max_tokens=self.MAX_SHELL_OUTPUT_TOKENS,
        )
        return _parse_json(raw, "chapter_shell")

    def _build_single_topic(
        self,
        meta: dict,
        plan: dict,
        topic_plan: dict,
        mmd: str,
    ) -> dict:
        problems = self._problems_for_topic(plan, topic_plan.get("number", ""))
        activities = self._items_for_topic(plan, "activity_inventory", topic_plan.get("number", ""))
        excerpts = self._items_for_topic(plan, "excerpt_inventory", topic_plan.get("number", ""))
        cases = self._items_for_topic(plan, "case_inventory", topic_plan.get("number", ""))
        numerics = self._items_for_topic(plan, "numerical_inventory", topic_plan.get("number", ""))
        discipline = meta.get("discipline", "")
        user = (
            f"Chapter {meta['chapter_number']}: {meta['chapter_title']}\n"
            f"Subject: {meta['subject']} | Grade: {meta['grade']}\n"
            f"Discipline: {discipline or 'General Science'}\n\n"
            f"TOPIC PLAN:\n{json.dumps(topic_plan, ensure_ascii=False, indent=2)}\n\n"
            f"PROBLEMS FOR THIS TOPIC (Mathematics — cover all in problem_set blocks):\n"
            f"{json.dumps(problems, ensure_ascii=False, indent=2)}\n\n"
            f"NUMERICAL ITEMS FOR THIS TOPIC (Science — include EVERY item below; "
            f"one worked_example or problem_set problem per inventory line, same numbers):\n"
            f"{json.dumps(numerics, ensure_ascii=False, indent=2)}\n\n"
            f"ACTIVITIES FOR THIS TOPIC (science — embed in topic.activities):\n"
            f"{json.dumps(activities, ensure_ascii=False, indent=2)}\n\n"
            f"EXCERPTS FOR THIS TOPIC (english — verbatim excerpt blocks):\n"
            f"{json.dumps(excerpts, ensure_ascii=False, indent=2)}\n\n"
            f"CASE STUDIES FOR THIS TOPIC (social science):\n"
            f"{json.dumps(cases, ensure_ascii=False, indent=2)}\n\n"
            f"MMD SOURCE (full chapter — use for wording and examples):\n{mmd}"
        )
        system = topic_builder_system(meta["subject"], meta.get("discipline", ""))
        try:
            raw = self._chat(system, user, max_tokens=self.MAX_TOPIC_OUTPUT_TOKENS)
        except GPTTruncationError:
            # One topic ran away — retry once, asking for a compact response.
            print("    (topic output truncated; retrying concisely)", flush=True)
            try:
                raw = self._chat(system, user + "\n\n" + _CONCISE_TOPIC_NOTE,
                                 max_tokens=self.MAX_TOPIC_OUTPUT_TOKENS)
            except GPTTruncationError:
                print("    (still too long; using a minimal fallback for this topic)", flush=True)
                return self._fallback_topic(topic_plan, excerpts)
        return _parse_json(raw, f"topic_{topic_plan.get('number', '?')}")

    def _fallback_topic(self, topic_plan: dict, excerpts: list) -> dict:
        """A minimal but valid topic so a single runaway episode can't abort the
        whole chapter build (keeps the verbatim excerpts at least)."""
        blocks: list = []
        for ex in (excerpts or [])[:3]:
            if not isinstance(ex, dict):
                continue
            blocks.append({
                "type": "excerpt", "title": "",
                "data": {
                    "kind": ex.get("kind", "prose"),
                    "text": ex.get("text", ""),
                    "reference": ex.get("reference", ""),
                    "explanation": "",
                },
            })
        summary = str(topic_plan.get("summary", "")).strip()
        if summary:
            blocks.append({"type": "paragraph", "title": "What It Says",
                           "data": {"text": summary}})
        return {
            "number": str(topic_plan.get("number", "")).zfill(2),
            "title": topic_plan.get("title", ""),
            "range": topic_plan.get("range", ""),
            "part": topic_plan.get("part", ""),
            "overview": summary,
            "blocks": blocks,
            "activities": [],
        }

    def _progress_path(self, raw_dump_path: Path | None) -> Path | None:
        if not raw_dump_path:
            return None
        return raw_dump_path.with_suffix(".progress.json")

    def _load_progress(self, progress_path: Path) -> dict | None:
        try:
            return json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_progress(self, progress_path: Path, data: dict) -> None:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _build_chunked(
        self,
        mmd: str,
        meta: dict,
        plan: dict,
        raw_dump_path: Path | None,
    ) -> tuple[Chapter, str]:
        topic_plans = plan.get("topics") or []
        n = len(topic_plans)
        progress_path = self._progress_path(raw_dump_path)
        progress = self._load_progress(progress_path) if progress_path else None

        if progress and len(progress.get("topics") or []) >= n:
            print(f"  Chunked build: using completed progress cache ({n} topics)", flush=True)
            data = progress
        else:
            topics: list[dict] = []
            shell: dict
            done_nums: set[str] = set()
            if progress and progress.get("topics"):
                topics = list(progress["topics"])
                done_nums = {str(t.get("number", "")).zfill(2) for t in topics}
                shell = progress
                print(
                    f"  Resuming chunked build: {len(topics)}/{n} topics already done",
                    flush=True,
                )
            else:
                print(
                    f"  Chunked build: {n} topics (chapter too large for one pass)",
                    flush=True,
                )
                shell = self._build_chapter_shell(meta, plan)

            for i, tp in enumerate(topic_plans, 1):
                num = str(tp.get("number", "")).zfill(2)
                if num in done_nums:
                    continue
                title = tp.get("title", f"Topic {tp.get('number', i)}")
                print(f"  Building topic {i}/{n}: {title}", flush=True)
                built = self._build_single_topic(meta, plan, tp, mmd)
                # Carry the planner's part (Prose/Poem) so the divider is reliable.
                if tp.get("part") and not str(built.get("part", "")).strip():
                    built["part"] = tp.get("part")
                topics.append(built)
                if progress_path:
                    self._save_progress(
                        progress_path,
                        {
                            "chapter_number": meta["chapter_number"],
                            "chapter_title": meta["chapter_title"],
                            "summary": shell.get("summary", plan.get("summary", "")),
                            "study_strategy": shell.get(
                                "study_strategy", plan.get("study_strategy", [])
                            ),
                            "glossary": shell.get("glossary", []),
                            "quick_recap": shell.get(
                                "quick_recap", plan.get("quick_recap", [])
                            ),
                            "chapter_mindmap": shell.get("chapter_mindmap"),
                            "topics": topics,
                        },
                    )

            data = {
                "chapter_number": meta["chapter_number"],
                "chapter_title": meta["chapter_title"],
                "summary": shell.get("summary", plan.get("summary", "")),
                "study_strategy": shell.get("study_strategy", plan.get("study_strategy", [])),
                "glossary": shell.get("glossary", []),
                "quick_recap": shell.get("quick_recap", plan.get("quick_recap", [])),
                "chapter_mindmap": shell.get("chapter_mindmap"),
                "topics": topics,
            }

        if raw_dump_path:
            raw_dump_path.parent.mkdir(parents=True, exist_ok=True)
            raw_dump_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if progress_path and progress_path.exists():
            progress_path.unlink(missing_ok=True)
        chapter = _to_chapter(data, meta)
        msg = (
            f"GPT chunked build · model={self.model} · "
            f"plan-topics={n} · chapter-topics={len(chapter.topics)} · "
            f"glossary={len(chapter.glossary)}"
        )
        return chapter, msg

    def _build(self, mmd: str, meta: dict, plan: dict, raw_dump_path: Path | None = None) -> tuple[Chapter, str]:
        if self._needs_chunked_build(meta, plan):
            return self._build_chunked(mmd, meta, plan, raw_dump_path)

        plan_json = json.dumps(plan, indent=2)
        user = (
            f"Subject: {meta['subject']}\nGrade: {meta['grade']}\n"
            f"Chapter number: {meta['chapter_number']}\n"
            f"Chapter title: {meta['chapter_title']}\n\n"
            "--- PLAN JSON START ---\n" + plan_json + "\n--- PLAN JSON END ---\n\n"
            "--- MMD START ---\n" + mmd + "\n--- MMD END ---"
        )
        raw = self._chat(
            builder_system(meta["subject"], meta.get("discipline", "")),
            user,
            max_tokens=self.MAX_BUILDER_OUTPUT_TOKENS,
        )
        if raw_dump_path:
            raw_dump_path.parent.mkdir(parents=True, exist_ok=True)
            raw_dump_path.write_text(raw, encoding="utf-8")
        data = _parse_json(raw, "builder")
        chapter = _to_chapter(data, meta)
        msg = (
            f"GPT two-pass · model={self.model} · "
            f"plan-topics={len(plan.get('topics', []))} · "
            f"chapter-topics={len(chapter.topics)} · "
            f"glossary={len(chapter.glossary)}"
        )
        return chapter, msg

    # ---- low-level chat -------------------------------------------------

    def _chat(self, system: str, user: str, *, max_tokens: int) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        # gpt-5.x models use max_completion_tokens; older use max_tokens.
        try:
            kwargs_try = dict(kwargs)
            kwargs_try["max_completion_tokens"] = max_tokens
            response = self._client.chat.completions.create(**kwargs_try)
        except Exception:
            kwargs["max_tokens"] = max_tokens
            response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise GPTTruncationError(
                f"GPT output truncated (finish_reason=length, max_tokens={max_tokens}). "
                "Raise token limits or use chunked build."
            )
        return choice.message.content or ""


# ---------- helpers ------------------------------------------------------


def _parse_json(text: str, label: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned invalid JSON: {exc}\n--- raw ---\n{text[:1200]}") from exc


def _as_str_list(value) -> list[str]:
    """Coerce GPT output into a clean list of strings.

    Critically guards against the model returning a single STRING where a list
    is expected: iterating a string yields characters ("M","a","k","e",...),
    which previously produced a Study Strategy table with one letter per row.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        items: list = list(value.values())
    elif isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # Split on explicit list separators (newlines, bullets, numbered
        # markers, semicolons).
        parts = re.split(r"(?:\r?\n+|\s*[•\u2022]\s*|\s*\d+[.)]\s+|;\s+)", text)
        items = [p for p in parts if p and p.strip()]
        # If the model returned one long paragraph (no list separators), fall
        # back to sentence splitting so e.g. a study strategy becomes tidy rows
        # instead of one giant cell (and never one-letter-per-row).
        if len(items) <= 1:
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
            items = sentences if len(sentences) > 1 else (items or [text])
    else:
        items = [value]
    return [str(s).strip() for s in items if str(s).strip()]


def _to_chapter(data: dict, meta: dict) -> Chapter:
    topics_raw = data.get("topics") or data.get("sections") or []
    topics: list[Topic] = []
    for entry in topics_raw:
        if isinstance(entry, dict):
            topics.append(_to_topic(entry))
        # Skip non-dict garbage entries gracefully.
    glossary = []
    glossary_raw = data.get("glossary") or []
    if isinstance(glossary_raw, dict):
        glossary_raw = [
            {"term": str(k).strip(), "definition": str(v).strip()}
            for k, v in glossary_raw.items()
            if str(k).strip() and str(v).strip()
        ]
    for g in glossary_raw:
        if not isinstance(g, dict):
            continue
        term = str(g.get("term", "")).strip()
        definition = str(g.get("definition", "")).strip()
        if term and definition:
            glossary.append(GlossaryItem(term=term, definition=definition))
    mindmap_raw = data.get("chapter_mindmap")
    chapter_mindmap = None
    if isinstance(mindmap_raw, dict):
        if mindmap_raw.get("root"):
            chapter_mindmap = mindmap_raw
        elif mindmap_raw.get("label"):
            chapter_mindmap = {"root": mindmap_raw}

    revision_items: list[EventRevisionItem] = []
    for row in data.get("event_revision") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "")).strip()
        event = str(row.get("event", "")).strip()
        if not title and not event:
            continue
        revision_items.append(EventRevisionItem(
            title=title or event[:80],
            period=str(row.get("period") or row.get("date", "")).strip(),
            event=event,
            causes=str(row.get("causes") or row.get("cause", "")).strip(),
            effects=str(row.get("effects") or row.get("effect", "")).strip(),
        ))

    return Chapter(
        chapter_number=str(data.get("chapter_number", meta["chapter_number"])),
        chapter_title=str(data.get("chapter_title", meta["chapter_title"])),
        subject=meta["subject"],
        grade=meta["grade"],
        discipline=str(meta.get("discipline", "")),
        summary=str(data.get("summary", "")).strip(),
        study_strategy=_as_str_list(data.get("study_strategy")),
        glossary=glossary,
        topics=topics,
        quick_recap=_as_str_list(data.get("quick_recap")),
        event_revision=revision_items,
        chapter_mindmap=chapter_mindmap,
    )


_KNOWN_BLOCK_TYPES = {
    "paragraph", "bullets", "table", "flowchart", "definitions", "callout",
    "worked_example", "excerpt", "qa", "problem_set", "venn", "pyramid", "timeline",
    "cycle", "tree",
}


def _coerce_block(b: dict) -> Block | None:
    """Accept several JSON shapes the model may emit:

    1. {"type": "paragraph", "title": "...", "data": {...}}
    2. {"type": "paragraph", "title": "...", "text": "..."}   (data flattened)
    3. {"paragraph": {"text": "..."}}                         (type as the key)
    """
    if not isinstance(b, dict):
        return None

    btype = b.get("type")
    if btype in _KNOWN_BLOCK_TYPES:
        data = b.get("data")
        if not isinstance(data, dict):
            data = {k: v for k, v in b.items() if k not in ("type", "title", "data")}
        title = str(b.get("title", "") or data.pop("title", "") or "").strip()
        return Block(type=btype, title=title, data=data or {})

    # type-as-key shape: find the single known-type key
    for key, payload in b.items():
        if key in _KNOWN_BLOCK_TYPES:
            if isinstance(payload, dict):
                data = dict(payload)
                title = str(b.get("title", "") or data.pop("title", "") or "").strip()
            elif isinstance(payload, list):
                # e.g. {"bullets": [...]}
                data = {"items": payload}
                title = str(b.get("title", "")).strip()
            else:
                data = {}
                title = str(b.get("title", "")).strip()
            return Block(type=key, title=title, data=data)
    return None


def _to_topic(raw: dict) -> Topic:
    raw_blocks: list[Any] = list(raw.get("blocks") or [])
    # Model sometimes emits problem_set bundles outside `blocks` — hoist them in.
    for ps in raw.get("problem_set") or []:
        if isinstance(ps, dict):
            raw_blocks.append(
                {
                    "type": "problem_set",
                    "title": str(ps.get("type_name", "")).strip(),
                    "data": ps,
                }
            )
    blocks = []
    for b in raw_blocks:
        blk = _coerce_block(b)
        if blk is not None:
            blocks.append(blk)
    activities = []
    for a in raw.get("activities", []) or []:
        if not isinstance(a, dict):
            continue
        activities.append(
            Activity(
                title=str(a.get("title", "")).strip(),
                aim=str(a.get("aim", "")).strip(),
                materials=[str(m).strip() for m in (a.get("materials") or []) if str(m).strip()],
                procedure=[
                    {
                        "step": str(row.get("step", "")),
                        "detail": str(row.get("detail", "")).strip(),
                        **({"why": str(row.get("why", "")).strip()} if row.get("why") else {}),
                    }
                    for row in (a.get("procedure") or [])
                    if isinstance(row, dict)
                ],
                observation=str(a.get("observation", "")).strip(),
                inference=str(a.get("inference", "")).strip(),
            )
        )
    return Topic(
        number=str(raw.get("number", "")).strip() or "01",
        title=str(raw.get("title", "")).strip(),
        range=str(raw.get("range", "")).strip(),
        overview=str(raw.get("overview", "")).strip(),
        blocks=blocks,
        activities=activities,
        part=str(raw.get("part", "")).strip(),
    )
