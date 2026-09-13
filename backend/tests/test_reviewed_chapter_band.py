"""Step 02 carries the chapter band: the frozen duration and the file's cells (Q67).

The reviewer's Bholi Masters shipped a blank chapter_duration (the registry lists
Bholi at 126 minutes), a blank chapter_description and every topic_description
as "Covers X, Y, Z." — hand-corrected on all six topics of both sheets. One
mechanism: the reviewed candidate copied none of Step 01's ``chapter_meta``, so
the transient hierarchy the Master snapshot builds on had no duration to freeze,
no description, and fell back to the code-composed name list that contract §9.1
declares invalid. Release QC then flagged ``chapter_duration_unregistered`` —
falsely naming the registry as empty — and would have refused Step 03's write.
"""
from __future__ import annotations

import copy

from app import models
from app.services import build_concepts, release_qc
from app.services import reviewed_file_input as reviewed
from app.services.phase3 import kernel
from tests.test_independent_reviewed_files import critic, document, result, setup_job


def _band_result(topic_description: str, chapter_description: str) -> dict:
    payload = copy.deepcopy(result())
    payload["concepts"][0]["topic_description"] = topic_description
    payload["chapter_description"] = chapter_description
    return payload


def test_the_reviewed_candidate_carries_the_duration_and_the_band_cells(db, tmp_path, monkeypatch):
    from app.services import chapter_durations

    from app.services import build_concepts_release as release

    job = setup_job(db)
    # Step 01 froze the run variable onto its payload; Step 02 carries that
    # same value (§32.1: frozen once, repeated identically on all four
    # outputs) — the registry and the explicit upload variable are only the
    # fallbacks for a payload that froze none.
    frozen = int(release.release_payload(job, lane="post")["chapter_meta"]["chapter_duration_minutes"])
    assert frozen and frozen != 126
    monkeypatch.setattr(chapter_durations, "lookup_duration_minutes", lambda **kwargs: None)
    job.chapter_duration_minutes = 126
    db.commit()
    text = ("Definition and uses\nWhat is DNA, and what are its uses?\n"
            "This chapter teaches how DNA is read.\nReorganized covers reading DNA.")
    path = document(tmp_path, text)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")

    current = reviewed.prepare(
        db, job, lane="post", owner_sub="local:default",
        provider=lambda _: _band_result("Reorganized covers reading DNA.", "This chapter teaches how DNA is read."),
        critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"),
    )
    meta = current["chapter_meta"]
    assert meta["chapter_duration_minutes"] == frozen
    # A payload that froze none falls back to the explicit upload variable.
    bare = {k: v for k, v in release.release_payload(job, lane="post").items() if k != "chapter_meta"}
    fallback = reviewed._chapter_meta_from_reviewed(db, job, bare, result())
    assert fallback["chapter_duration_minutes"] == 126
    assert meta["chapter_description"] == "This chapter teaches how DNA is read."
    assert meta["topic_descriptions"] == {"reorganized": "Reorganized covers reading DNA."}
    # The band cell is not a concept-row field.
    assert "topic_description" not in current["records"][0]
    # And release QC no longer blames the registry for a value the payload carries.
    issues, blocking = release_qc._chapter_duration_findings(current)
    assert issues == [] and blocking == []


def test_the_registry_row_outranks_the_explicit_upload_variable(db):
    from app.services import build_concepts_release as release, chapter_durations

    job = setup_job(db)
    job.chapter_duration_minutes = 126
    previous = release.release_payload(job, lane="post")
    identity = reviewed.metadata(db, previous)
    registered = chapter_durations.lookup_duration_minutes(
        board=identity["board"], grade=identity["grade"], subject=identity["subject"],
        chapter_title=identity["chapter_title"])
    assert registered and registered != 126   # the setup chapter is a registry row
    meta = reviewed._chapter_meta_from_reviewed(db, job, previous, result())
    assert meta == {"chapter_duration_minutes": registered}


def test_a_band_cell_not_quoted_from_the_file_is_refused():
    doc = {"blocks": [{"ref": "B1", "text": "Definition and uses\nWhat is DNA, and what are its uses?"}],
           "images": [], "filename": "r.txt", "sha256": "0" * 64}
    check = reviewed._checker(doc)
    invented = _band_result("A description nobody wrote.", "An invented chapter summary.")
    defects = check(invented)
    assert any("chapter_description must be quoted" in d for d in defects)
    assert any("topic_description must be quoted" in d for d in defects)
    # Quoted (or empty) band cells pass; the paired-break view counts as quoted.
    assert check(_band_result("", "")) == []
    assert check(_band_result("Definition and uses", "What is DNA, and what are its uses?")) == []


def test_release_qc_names_what_it_measured_not_the_registry():
    # A pure function of the payload (it wrote the recorded key at staging):
    # it never consults the registry, so it no longer claims the registry
    # has no row — Bholi is registered at 126 minutes and still fired it.
    payload = {
        "directory_metadata": {"board": "CBSE", "grade": "10", "subject": "English",
                               "chapter_title": "Bholi", "chapter_duration": ""},
        "chapter_meta": {},
        "records": [],
    }
    issues, blocking = release_qc._chapter_duration_findings(payload)
    assert len(issues) == 1 and blocking
    assert "was frozen onto it" in issues[0]["message"]
    assert "registry has no row" not in issues[0]["message"]


def test_the_value_step_01_froze_outranks_a_fresh_lookup(db):
    from app.services import build_concepts_release as release

    job = setup_job(db)
    previous = dict(release.release_payload(job, lane="post"))
    previous["chapter_meta"] = {"chapter_duration_minutes": 126, "chapter_description": "Step 01 prose"}
    meta = reviewed._chapter_meta_from_reviewed(db, job, previous, result())
    # The frozen run variable rides; Step 01's prose does not (Q51).
    assert meta == {"chapter_duration_minutes": 126}


def test_release_qc_names_an_unauthored_topic_description_without_blocking():
    payload = {
        "chapter_meta": {"topic_descriptions": {"reading dna": "How DNA is read, base by base."}},
        "records": [{"topic": "Reading DNA"}, {"topic": "Copying DNA"}, {"topic": "Copying DNA"}],
    }
    issues, blocking = release_qc._band_description_findings(payload)
    assert blocking == []
    codes = {issue["code"] for issue in issues}
    assert codes == {release_qc.TOPIC_DESCRIPTION_UNAUTHORED, release_qc.CHAPTER_DESCRIPTION_UNAUTHORED}
    topic_issue = next(i for i in issues if i["code"] == release_qc.TOPIC_DESCRIPTION_UNAUTHORED)
    assert "Copying DNA" in topic_issue["message"] and "Reading DNA" not in topic_issue["message"]
    assert topic_issue["severity"] == "warning"


def test_the_topic_summary_never_composes_a_name_list():
    chapter = models.Chapter(chapter_code="10CBEN_BHOLI", board="CBSE", grade="10",
                             subject="English", unit="Prose", chapter_title="Bholi",
                             chapter_display_name="Bholi")
    topic = models.Topic(topic_title="Reading DNA", pre_post_learning="Post", source_order=1,
                         topic_description="Covers stale, names.")
    topic.concepts = [models.Concept(concept_title="Base pairs", source_order=1),
                      models.Concept(concept_title="Helix", source_order=2)]
    chapter.topics = [topic]

    build_concepts._sync_chapter_topic_summary(chapter, {})
    assert topic.topic_description == ""          # blank, never "Covers Base pairs, Helix."

    build_concepts._sync_chapter_topic_summary(
        chapter, {"topic_descriptions": {"reading dna": "How DNA is read, base by base."},
                  "chapter_description": "Bholi's story.", "chapter_duration_minutes": 126})
    assert topic.topic_description == "How DNA is read, base by base."
    assert chapter.chapter_description == "Bholi's story."
    assert chapter.chapter_duration == "126 minutes"
