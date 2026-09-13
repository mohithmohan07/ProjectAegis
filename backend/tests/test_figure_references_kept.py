"""Learner prose keeps its figure references under generation-quality v2 (Q67).

``strip_dangling_references`` deleted "Fig. 7.7"/"Figure 7.10" from any prose
section without an image tag, leaving the reviewers' three subjectless
sentences byte for byte. The publication path had already dropped this
cleaner for that reason; staging still ran it. The policy stamp now has two
supported versions: a run keeps the one it recorded, so sealed rows replay
through the cleanup they were sealed with, and only v2 work keeps the figures.
"""
from __future__ import annotations

import pytest

from app.services import concept_cleanup, generation_quality_policy as quality
from app.services.concept_cleanup import clean_concept_record, strip_dangling_references

REVIEWED = (
    "Cut the flower as illustrated in Fig. 7.7. Then compare with Fig. 7.9: the "
    "cotyledon. Figure 7.10 labels the penis, urethra."
)


def test_figure_references_survive_when_kept_and_apparatus_still_goes():
    assert strip_dangling_references(REVIEWED, keep_figures=True) == REVIEWED
    # The legacy behaviour is still what an old row replays through.
    broken = strip_dangling_references(REVIEWED)
    assert "as illustrated in." in broken and "Fig. 7.7" not in broken
    # Example/Exercise apparatus is neutralised either way; tables too.
    assert strip_dangling_references(
        "Use Fig. 3 (see Example 19) to find R.", keep_figures=True
    ) == "Use Fig. 3 to find R."
    assert strip_dangling_references(
        "Read Fig. 3 and Table no. 1 for details", keep_figures=True
    ) == "Read Fig. 3 for details"


def test_a_v2_row_keeps_its_figures_and_an_older_row_does_not():
    details = "Description: Cut the flower as illustrated in Fig. 7.7 (see Example 3)."
    v2 = clean_concept_record({"concept_title": "Flower", "concept_details": details,
                               quality.KEY: quality.V2})
    assert "Fig. 7.7" in v2["concept_details"]
    assert "Example 3" not in v2["concept_details"]
    v1 = clean_concept_record({"concept_title": "Flower", "concept_details": details,
                               quality.KEY: quality.V1})
    assert "Fig. 7.7" not in v1["concept_details"]
    legacy = clean_concept_record({"concept_title": "Flower", "concept_details": details})
    assert "Fig. 7.7" not in legacy["concept_details"]
    # A caller holding the envelope's answer overrides the row's own stamp.
    forced = clean_concept_record({"concept_title": "Flower", "concept_details": details},
                                  keep_figures=True)
    assert "Fig. 7.7" in forced["concept_details"]


def test_the_policy_keeps_every_recorded_version_and_mints_only_the_latest():
    assert quality.VERSION == quality.V4 and quality.SUPPORTED == (quality.V1, quality.V2, quality.V3, quality.V4)
    # v4 is monotone too: it keeps v2 and v3 and adds the declared Pre options.
    assert quality.figure_references_kept({quality.KEY: quality.V4}) is True
    assert quality.host_creations_resolved({quality.KEY: quality.V4}) is True
    assert quality.declared_pre_options({quality.KEY: quality.V3}) is False
    assert quality.declared_pre_options({"metadata": {quality.KEY: quality.V4}}) is True
    assert quality.declared_pre_options({"profile": {quality.KEY: quality.V4}}) is True
    assert quality.declared_pre_options({}) is False
    # v3 is monotone: it keeps v2's figure references and adds the Host resolution.
    assert quality.figure_references_kept({quality.KEY: quality.V3}) is True
    assert quality.host_creations_resolved({quality.KEY: quality.V2}) is False
    assert quality.host_creations_resolved({"metadata": {quality.KEY: quality.V3}}) is True
    assert quality.host_creations_resolved({}) is False
    v1 = {quality.KEY: quality.V1}
    assert quality.is_current(v1) and quality.active(v1)
    assert quality.fields(v1) == {quality.KEY: quality.V1}          # never upgraded
    assert quality.suffix(v1) == ";" + quality.V1                   # decision keys unchanged
    assert quality.figure_references_kept(v1) is False
    assert quality.figure_references_kept({quality.KEY: quality.V2}) is True
    assert quality.figure_references_kept({"metadata": {quality.KEY: quality.V2}}) is True
    assert quality.figure_references_kept({}) is False and quality.fields({}) == {}
    with quality.bind_run(quality.V1):
        assert quality.run_fields() == {quality.KEY: quality.V1}
    with quality.bind_run(quality.V2):
        assert quality.run_fields() == {quality.KEY: quality.V2}
    with quality.bind_run(None):
        assert quality.run_fields() == {}
    with pytest.raises(ValueError):
        with quality.bind_run("owner-generation-quality-2099-01-01-v9"):
            pass


def test_the_deposit_pipeline_reads_the_envelope_not_the_row():
    """The Assemble deposit fixpoint and the Refiner read the envelope's or
    the metadata's recorded stamp, never the row (plain rows carry none)."""
    import inspect

    from app.services import release_refiner
    from app.services.phase3 import assemble

    assert "figure_references_kept(env)" in inspect.getsource(assemble)
    assert "figure_references_kept(metadata)" in inspect.getsource(release_refiner)


@pytest.mark.parametrize("text", [
    "Refer table no. 1 and Figure 1,2 and Example 2 or ex 1 for details",
    "Compare the results; see (Example 19).",
    "Use fig.11.1 to find R.",
    "See Table no. 1 and Fig 2 or Example 3 for the values.",
    REVIEWED,
])
def test_an_old_cleaned_prose_section_is_a_fixpoint_of_the_kept_figures_cleaner(text):
    """The replay argument: a row sealed under the old cleaner is unchanged
    by the new one, gate or no gate — the kept-figure matches are a subset
    of the old matches and a sealed prose section holds none of them."""
    old = strip_dangling_references(text)
    assert strip_dangling_references(old, keep_figures=True) == old
    assert strip_dangling_references(old) == old


def test_the_database_deposit_reads_the_bound_run(db, first_chapter):
    import app.models as models
    from app.services import build_concepts, directory

    assert quality.bound_figure_references_kept() is False
    with quality.bind_run(quality.V1):
        assert quality.bound_figure_references_kept() is False
    with quality.bind_run(quality.V2):
        assert quality.bound_figure_references_kept() is True
    with quality.bind_run(None):
        assert quality.bound_figure_references_kept() is False

    detail = directory.chapter_detail(db, first_chapter["id"])
    topic = db.get(models.Topic, detail["topics"][0]["id"])
    row = {
        "concept_title": "Flower",
        "concept_details": "Description: Cut the flower as illustrated in Fig. 7.7.",
        "keywords": "",
    }
    kept = build_concepts._add_concept(db, topic, dict(row), keep_figures=True)
    legacy = build_concepts._add_concept(db, topic, dict(row))
    db.flush()
    assert "Fig. 7.7" in kept.concept_details
    assert "as illustrated in." in legacy.concept_details
    assert "Fig. 7.7" not in legacy.concept_details


def test_sealed_v1_decision_keys_carry_the_recorded_version():
    """Every site that suffixed or stamped the bare constant now carries the
    RECORDED version, so bumping VERSION re-keys no sealed v1 decision."""
    import inspect
    import json

    from app.services import (
        assessment_item_review, assessment_master_refiner,
        assessment_materialization, assessment_source_inventory,
        assessment_teaching_order, prelearning_authority_v2, question_polishing,
    )
    from app.services.phase3 import coherence, prelearn, premap

    v1_meta = {quality.KEY: quality.V1, "subject": "Science"}
    assert json.loads(question_polishing._batch_payload(v1_meta, []))[quality.KEY] == quality.V1
    assert assessment_item_review._quality_fields({}, {quality.KEY: quality.V1}) == {
        quality.KEY: quality.V1
    }
    assert assessment_master_refiner._quality_fields({}, {quality.KEY: quality.V1}) == {
        quality.KEY: quality.V1
    }
    assert quality.suffix({"metadata": {quality.KEY: quality.V1}}) == ";" + quality.V1
    assert quality.fields({quality.KEY: quality.V1}) == {quality.KEY: quality.V1}
    # An atom's teaching-order audit rides into every cell payload: a v1
    # release keeps its v1 stamp there.
    atoms, receipt = assessment_teaching_order.project_atoms(
        [{"source_qid": "QINV-0001"}],
        {quality.KEY: quality.V1, "records": [{"_aegis_release_qids": ["QINV-0001"]}]},
    )
    assert atoms[0][assessment_teaching_order.AUDIT_FIELD]["policy_version"] == quality.V1
    assert receipt["policy_version"] == quality.V1
    for module in (
        premap, prelearn, coherence, prelearning_authority_v2, question_polishing,
        assessment_source_inventory, assessment_materialization,
        assessment_item_review, assessment_master_refiner, assessment_teaching_order,
    ):
        source = inspect.getsource(module)
        assert "quality.VERSION if quality.active(" not in source, module.__name__
        assert "quality.KEY: quality.VERSION" not in source, module.__name__
    assert 'quality.VERSION + ";coherence:"' not in inspect.getsource(coherence)


def test_stamp_readers_accept_every_supported_version(monkeypatch, tmp_path):
    from app import config
    from app.services import checkpoints, model_routing_run
    from tests.test_generation_quality_boundaries import _job

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    for version in quality.SUPPORTED:
        model_routing_run.save_profile_for_job(_job(), None, quality_version=version)
    with pytest.raises(ValueError):
        model_routing_run.save_profile_for_job(
            _job(), None, quality_version="owner-generation-quality-2099-01-01-v9",
        )
    source = __import__("inspect").getsource(checkpoints._validate_payload)
    assert "generation_quality_policy.SUPPORTED" in source
    assert "!= generation_quality_policy.VERSION" not in source


def test_release_qc_records_a_prose_figure_with_no_image_on_the_row():
    from app.services import release_qc

    row = {
        "topic": "Flowers",
        "concept_title": "Parts of a Flower",
        "concept_details": (
            "Description: Cut the flower as illustrated in Fig. 7.7.\n"
            "Achieving Mastery: Name each part of a flower."
        ),
        "keywords": "flower",
    }
    # The pass itself never blocks; the full audit's other passes block on
    # this synthetic payload for unrelated reasons (no frozen duration).
    pass_issues, pass_blocking = release_qc._figure_reference_findings(
        {"records": [row]}
    )
    assert pass_blocking == []
    assert [issue["code"] for issue in pass_issues] == [
        release_qc.PROSE_FIGURE_WITHOUT_IMAGE
    ]
    issues, blocking = release_qc.audit({"records": [row]})
    figure_issues = [
        issue for issue in issues
        if issue["code"] == release_qc.PROSE_FIGURE_WITHOUT_IMAGE
    ]
    assert len(figure_issues) == 1
    assert figure_issues[0]["severity"] == "warning"
    assert "Fig. 7.7" in figure_issues[0]["message"]
    assert not any("figure" in item for item in blocking)

    with_image = dict(row)
    with_image["concept_details"] += (
        ' // Activity/ Info Hub: [img src="https://x.test/f77.png" '
        'alt="Fig. 7.7 Longitudinal section of a flower"]'
    )
    assert not [
        issue for issue in release_qc.audit({"records": [with_image]})[0]
        if issue["code"] == release_qc.PROSE_FIGURE_WITHOUT_IMAGE
    ]
    legacy = dict(row)
    legacy["concept_details"] = (
        "Description: Cut the flower as illustrated in.\n"
        "Achieving Mastery: Name each part of a flower."
    )
    assert not [
        issue for issue in release_qc.audit({"records": [legacy]})[0]
        if issue["code"] == release_qc.PROSE_FIGURE_WITHOUT_IMAGE
    ]
    in_example = dict(row)
    in_example["concept_details"] = (
        "Description: Cut the flower.\nAchieving Mastery: Name each part. // "
        "Types: Type 01: Naming Case 01: Parts Example 01: Label Fig. 7.7."
    )
    assert not [
        issue for issue in release_qc.audit({"records": [in_example]})[0]
        if issue["code"] == release_qc.PROSE_FIGURE_WITHOUT_IMAGE
    ]


def test_a_pre_v2_run_replays_the_culmination_mastery_skip():
    """Q68: the two deposit-chain mastery formatters replay the culmination
    skip a run sealed before generation-quality v2 was assembled with — its
    final certificate seals concept_details, so an ungated formatter would
    refuse the sealed payload at the deposit recompute."""
    from app.services import concept_refiner as cr
    from app.services import generation as g

    inline = "Description: Together these explain germination. Achieving Mastery: Explain it."

    def culm():
        return {"concept_title": "Culmination - Germination",
                "parent_concept": "Culmination", "concept_details": inline}

    assert cr.refine_chapter([culm()], format_culminations=False)[0]["concept_details"] == inline
    assert g._ensure_mastery_lines_via_api(
        [culm()], meta={}, use_api=False, format_culminations=False,
    )[0]["concept_details"] == inline
    expected = "Description: Together these explain germination.\nAchieving Mastery: Explain it."
    assert cr.refine_chapter([culm()])[0]["concept_details"] == expected
    assert g._ensure_mastery_lines_via_api(
        [culm()], meta={}, use_api=False,
    )[0]["concept_details"] == expected
    # A normal row is formatted either way.
    normal = {"concept_title": "Germination", "parent_concept": "Seeds",
              "concept_details": "Description: A seed sprouts. Achieving Mastery: Describe it."}
    assert "\nAchieving Mastery:" in cr.refine_chapter(
        [dict(normal)], format_culminations=False,
    )[0]["concept_details"]
    assert quality.culmination_mastery_formatted({quality.KEY: quality.V1}) is False
    assert quality.culmination_mastery_formatted({quality.KEY: quality.V2}) is True
    assert quality.culmination_mastery_formatted({"metadata": {quality.KEY: quality.V4}}) is True
    assert quality.culmination_mastery_formatted(None) is False
    assert quality.bound_culmination_mastery_formatted() is False
    with quality.bind_run(quality.V1):
        assert quality.bound_culmination_mastery_formatted() is False
    with quality.bind_run(quality.V3):
        assert quality.bound_culmination_mastery_formatted() is True
    import inspect

    from app.services import build_concepts
    source = inspect.getsource(build_concepts._deposit_concepts)
    assert "format_culminations=format_culminations" in source

