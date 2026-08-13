"""Chapter reading is told which sections the outline ruled question banks.

The outline decides a Practice Set or Exercise is a question collection, not a
teaching topic, and the renderer deliberately leaves its banner un-promoted.
Chapter reading is a separate model pass that never saw that decision, so it
read the bold banner as a section title and rewrote it into a heading. Once it
is a heading, downstream treats it as a source topic that must be covered by
concepts, and the inventory extractor reads it as a task with no prompt.

Live acceptance, Balbharati Std 6 "Three Dimensional Shapes": that promotion
blocked generation twice over — first at the inventory gate, then at the
source-topic review gate.
"""
from __future__ import annotations

from app.services import canonical_source_phase2 as phase2
from app.services import chapter_reading
from app.services import chapter_reading_contract as contract


_OUTLINE = {
    "topics": [
        {"title": "Pyramid", "kind": "content"},
        {"title": "Practice Set 1.1", "kind": "assessment"},
        {"title": "Cone", "kind": "content"},
        {"title": "Exercise 1", "kind": "assessment"},
    ],
}


def test_only_assessment_titles_travel_to_the_reader():
    with phase2.activate({"chapter_outline": _OUTLINE}):
        assert contract._assessment_sections() == (
            "Practice Set 1.1", "Exercise 1",
        )


def test_no_outline_means_the_pass_behaves_exactly_as_before():
    """An uploaded .mmd, or a chapter converted before outlines existed."""
    assert contract._assessment_sections() == ()
    with phase2.activate({}):
        assert contract._assessment_sections() == ()
    with phase2.activate({"chapter_outline": "not-a-dict"}):
        assert contract._assessment_sections() == ()


def test_the_reader_is_told_which_banners_not_to_promote(monkeypatch):
    seen: dict = {}

    def fake_call(system, user, **_kwargs):
        seen["system"] = system
        seen["user"] = user
        return {
            "normalized_mmd": "**Practice Set 1.1**\n\n1) Name the solid.\n",
            "blocks": [{"kind": "question", "label": "", "page": None}],
            "dropped_furniture": [],
        }

    monkeypatch.setattr(
        chapter_reading.config, "use_live_generation", lambda: True)
    reading = chapter_reading.read_chapter(
        "**Practice Set 1.1**\n\n1) Name the solid.\n",
        assessment_sections=("Practice Set 1.1", "Exercise 1"),
        api_call=fake_call,
    )

    assert reading["mode"] == "live"
    assert "assessment_sections" in seen["user"]
    assert "Practice Set 1.1" in seen["user"]
    # The rule itself must reach the model, not just the data.
    assert "assessment_sections" in seen["system"]


def test_a_reading_taken_without_the_outline_is_not_replayed(monkeypatch):
    """The same chapter read with and without its outline is different text.

    Sharing one cache key would replay the promoted-heading reading for every
    later run and make the fix invisible.
    """
    monkeypatch.setattr(
        chapter_reading.config, "use_live_generation", lambda: True)
    raw = "**Practice Set 1.1**\n\n1) Name the solid.\n"

    assert chapter_reading._cache_key(raw) != chapter_reading._cache_key(
        raw, ("Practice Set 1.1",)
    )


def test_an_existing_run_keeps_replaying_its_own_reading(monkeypatch):
    """A run that already read before the outline was passed must not switch.

    Its saved stages were compiled from that text; re-reading mid-run would
    invalidate every one of them.
    """
    monkeypatch.setattr(
        chapter_reading.config, "use_live_generation", lambda: True)
    raw = "**Practice Set 1.1**\n\n1) Name the solid.\n"
    stored = {"mode": "live", "normalized_mmd": "## Practice Set 1.1\n"}

    monkeypatch.setattr(chapter_reading, "_memory_cache", {})
    chapter_reading._store_cached(chapter_reading._cache_key(raw), stored)

    # Looked up WITH sections, but only the pre-outline reading exists.
    hit = chapter_reading.cached_reading(raw, ("Practice Set 1.1",))
    assert hit is not None
    assert hit["normalized_mmd"] == "## Practice Set 1.1\n"


def test_the_banner_rule_never_licenses_dropping_it():
    """Un-promoting is not deleting: its questions still belong to the run."""
    system = chapter_reading.prompts.get_text(
        "chapter_reading.normalize.system")
    assert "never rewrite it into a '#' heading" in system
    assert "ONLY if it would already have been furniture" in system
