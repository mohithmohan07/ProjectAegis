"""The same filename must not resume content from a different API request."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.services import workbooks

workbooks._vendor()
import gpt_writer


META = {
    "subject": "English", "grade": "Grade 6", "chapter_title": "A Story",
    "chapter_number": "01", "board": "Example Board", "publication": "Reader",
}
PLAN = {
    "summary": "A supported overview.",
    "topics": [{"number": "01", "title": "First"}, {"number": "02", "title": "Next"}],
}


def _writer():
    writer = gpt_writer.GPTWriter.__new__(gpt_writer.GPTWriter)
    writer.model = "gpt-5.6-luna"
    writer.base_url = None
    return writer


@pytest.mark.parametrize("change", ["prompt", "source", "metadata"])
def test_plan_cache_reuses_exact_request_and_refreshes_changed_input(tmp_path, monkeypatch, change):
    writer = _writer()
    path = tmp_path / "chapter.plan.json"
    legacy = '{"summary": "old filename-only content"}'
    path.write_text(legacy)
    calls = []

    def chat(system, user, **kwargs):
        calls.append((system, user))
        return json.dumps({"summary": f"fresh plan {len(calls)}", "topics": []})

    writer._chat = chat
    monkeypatch.setattr(gpt_writer, "planner_system", lambda: "Original planner")
    first = writer._plan("Source one", META, path)
    assert len(calls) == 1
    assert any(p.read_text() == legacy for p in (tmp_path / "_prompt_cache").glob("*.json"))
    assert writer._plan("Source one", META, path) == first
    assert len(calls) == 1

    source, meta = "Source one", copy.deepcopy(META)
    if change == "prompt":
        monkeypatch.setattr(gpt_writer, "planner_system", lambda: "Refined planner")
    elif change == "source":
        source = "Revised source"
    else:
        meta["grade"] = "Grade 7"
    second = writer._plan(source, meta, path)
    assert len(calls) == 2
    assert second != first
    assert json.loads(path.read_text()) == second
    assert "Board: Example Board" in calls[-1][1]
    assert "Publication: Reader" in calls[-1][1]


@pytest.mark.parametrize("change", ["same", "prompt", "source", "metadata", "plan"])
def test_chunk_resume_requires_matching_source_plan_and_prompts(tmp_path, monkeypatch, change):
    writer = _writer()
    raw_path = tmp_path / "chapter.build.raw.json"
    legacy_path = writer._progress_path(raw_path)
    legacy = {"summary": "legacy", "topics": [{"number": "01"}, {"number": "02"}]}
    legacy_path.write_text(json.dumps(legacy))
    monkeypatch.setattr(gpt_writer, "chapter_shell_system", lambda *a: "Shell")
    monkeypatch.setattr(gpt_writer, "topic_builder_system", lambda *a: "Original topic")
    monkeypatch.setattr(gpt_writer, "_to_chapter", lambda data, meta: SimpleNamespace(
        topics=data["topics"], glossary=data.get("glossary", []),
    ))
    built, shells = [], []
    interrupted = True

    def shell(meta, plan):
        shells.append(meta["grade"])
        return {"summary": "Current shell", "glossary": []}

    def topic(meta, plan, topic_plan, mmd):
        if topic_plan["number"] == "02" and interrupted:
            raise RuntimeError("temporary provider outage")
        built.append(topic_plan["number"])
        return {"number": topic_plan["number"], "title": topic_plan["title"], "blocks": []}

    writer._build_chapter_shell = shell
    writer._build_single_topic = topic
    with pytest.raises(RuntimeError, match="temporary provider outage"):
        writer._build_chunked("Source one", META, PLAN, raw_path)
    assert built == ["01"]
    assert json.loads(legacy_path.read_text()) == legacy

    source, meta, plan = "Source one", copy.deepcopy(META), copy.deepcopy(PLAN)
    if change == "prompt":
        monkeypatch.setattr(gpt_writer, "topic_builder_system", lambda *a: "Refined topic")
    elif change == "source":
        source = "Revised source"
    elif change == "metadata":
        meta["grade"] = "Grade 7"
    elif change == "plan":
        plan["topics"][0]["title"] = "Updated first topic"
    interrupted = False
    chapter, _ = writer._build_chunked(source, meta, plan, raw_path)
    assert len(chapter.topics) == 2
    assert built == (["01", "02"] if change == "same" else ["01", "01", "02"])
    assert len(shells) == (1 if change == "same" else 2)
    assert json.loads(legacy_path.read_text()) == legacy
