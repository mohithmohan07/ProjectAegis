"""The production flip: AEGIS_PHASE3_REWRITE routes 81%+ through runner.run.

The seam is the installed ``prepare_final`` wrapper — the single point
where the old pipeline crosses the 81% boundary. With the flag on it
seals the envelope from exactly the session state it holds and hands the
run to the rewritten Phase 3; with the flag off (the default) the old
path is untouched.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import concept_topology_contract as contract
from app.services import generation
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import runner

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture()
def fixture_env() -> dict:
    return envelope_mod.load(GOLDEN / "rne_envelope.json")


def _graph_from(fixture_env: dict) -> dict:
    return {
        "source_contract_hash": fixture_env["source_contract_hash"],
        "metadata": fixture_env["metadata"],
        "topics": fixture_env["graph"]["topics"],
        "subtopics": fixture_env["graph"]["subtopics"],
        "blocks": fixture_env["graph"]["blocks"],
    }


def test_flag_on_routes_prepare_final_through_the_rewrite(
    monkeypatch, tmp_path, fixture_env,
):
    captured: dict = {}

    def fake_run(env, *, store_dir=None, providers=None):
        captured["env"] = env
        captured["store_dir"] = store_dir
        return {
            "records": [{"concept_title": "Stub Row"}],
            "host_map": {},
            "qid_map": {},
            "new_concepts": [],
            "coverage": {"items": 0, "routed_qids": [], "unrouted": []},
            "summary": {
                "row_count": 1,
                "flagged_row_count": 0,
                "routed_qids": 0,
                "unrouted_items": 0,
                "envelope_sha256": env["envelope_sha256"],
            },
        }

    monkeypatch.setenv("AEGIS_PHASE3_REWRITE", "1")
    monkeypatch.setattr(runner, "run", fake_run)

    session = {
        "artifact_dir": tmp_path,
        "canonical": fixture_env["canonical"],
    }
    with phase3.activate_session(session), phase3.activate(
        _graph_from(fixture_env)
    ):
        rows = generation._prepare_final_concept_content(
            copy.deepcopy(fixture_env["skeleton_rows"]),
            subject="History",
            mmd_text="canonical semantic source",
            meta=fixture_env["metadata"],
            source_sections=[],
            source_topic_excerpts=[],
            method_anchors=[],
            question_task_inventory=copy.deepcopy(fixture_env["inventory"]),
            mined_types=copy.deepcopy(fixture_env["mined_types"]),
        )

    assert rows == [{"concept_title": "Stub Row"}]
    # The seam sealed the exact same envelope the golden fixture records.
    assert captured["env"]["envelope_sha256"] == fixture_env[
        "envelope_sha256"
    ]
    assert captured["env"]["skeleton_rows"] == fixture_env["skeleton_rows"]
    # The decision store lives in the job's durable artifact directory.
    assert str(captured["store_dir"]).endswith("phase3-decisions")
    assert str(captured["store_dir"]).startswith(str(tmp_path))


def test_flag_off_keeps_the_old_path(monkeypatch, fixture_env):
    monkeypatch.delenv("AEGIS_PHASE3_REWRITE", raising=False)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("runner.run must not fire with the flag off")

    monkeypatch.setattr(runner, "run", must_not_run)
    sentinel = [{"concept_title": "Old Path Row"}]
    monkeypatch.setattr(
        generation,
        "_TOPOLOGY_CONTRACT_ORIGINAL_PREPARE",
        lambda out, **kwargs: copy.deepcopy(sentinel),
    )
    monkeypatch.setattr(
        contract,
        "_allocate_after_freeze",
        lambda _generation, topology, **kwargs: topology,
    )

    rows = generation._prepare_final_concept_content(
        copy.deepcopy(fixture_env["skeleton_rows"][:2]),
        subject="History",
        mmd_text="canonical semantic source",
        meta=fixture_env["metadata"],
        source_sections=[],
        source_topic_excerpts=[],
        method_anchors=[],
        question_task_inventory={"items": []},
        mined_types={"types": []},
        refresh_chapter_wide_assignments=False,
    )

    # The old path normalizes rows on the way through; the sentinel row
    # surviving proves the original prepare chain ran, not the rewrite.
    assert [row["concept_title"] for row in rows] == ["Old Path Row"]


def test_the_sealed_envelope_is_reused_across_resumes(
    monkeypatch, tmp_path, fixture_env,
):
    """Resume-time inventory refreshes must not re-bill the run: while the
    source contract and boundary skeleton are unchanged, the persisted
    sealed envelope is reused so every stored decision replays."""
    sealed: list[str] = []

    def fake_run(env, *, store_dir=None, providers=None):
        sealed.append(env["envelope_sha256"])
        return {
            "records": [{"concept_title": "Stub Row"}],
            "host_map": {}, "qid_map": {}, "new_concepts": [],
            "coverage": {"items": 0, "routed_qids": [], "unrouted": []},
            "summary": {
                "row_count": 1, "flagged_row_count": 0, "routed_qids": 0,
                "unrouted_items": 0,
                "envelope_sha256": env["envelope_sha256"],
            },
        }

    monkeypatch.setenv("AEGIS_PHASE3_REWRITE", "1")
    monkeypatch.setattr(runner, "run", fake_run)
    session = {
        "artifact_dir": tmp_path,
        "canonical": fixture_env["canonical"],
    }
    common = dict(
        subject="History",
        mmd_text="canonical semantic source",
        meta=fixture_env["metadata"],
        source_sections=[],
        source_topic_excerpts=[],
        method_anchors=[],
        mined_types=copy.deepcopy(fixture_env["mined_types"]),
    )
    with phase3.activate_session(session), phase3.activate(
        _graph_from(fixture_env)
    ):
        generation._prepare_final_concept_content(
            copy.deepcopy(fixture_env["skeleton_rows"]),
            question_task_inventory=copy.deepcopy(fixture_env["inventory"]),
            **common,
        )
        # A refreshed (byte-different, semantically equivalent) inventory
        # on resume must NOT change the envelope.
        drifted = copy.deepcopy(fixture_env["inventory"])
        drifted["_resume_refresh_marker"] = "different-bytes"
        generation._prepare_final_concept_content(
            copy.deepcopy(fixture_env["skeleton_rows"]),
            question_task_inventory=drifted,
            **common,
        )

    assert len(sealed) == 2
    assert sealed[0] == sealed[1]
    assert (tmp_path / "source.phase3-envelope.json").exists()
