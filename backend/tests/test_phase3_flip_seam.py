"""The 81% seam: prepare_final always routes through runner.run.

The seam is the installed ``prepare_final`` wrapper — the single point
where the pipeline crosses the 81% boundary. It seals the envelope from
exactly the session state it holds and hands the run to the rewritten
Phase 3; since PR 4 there is no other path.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services import canonical_source_phase3 as phase3
from app.services import four_output_release_contract
from app.services import generation
from app.services import postlearning_formation_contract as formation
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


def test_prepare_final_routes_through_the_rewrite(
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

        # A refresh that CHANGES THE QID SET is not equivalent: the sealed
        # Host certifications can never cover it, and reusing the seal
        # trapped the run in a permanent non-terminal loop ([measured]
        # job "Patterns", owner report 2026-08-29). The envelope must
        # re-key so Phase 3 certifies the current set.
    with phase3.activate_session(session), phase3.activate(
        _graph_from(fixture_env)
    ):
        qid_drifted = copy.deepcopy(fixture_env["inventory"])
        items = qid_drifted.get("items") or []
        assert items, "fixture inventory must carry items"
        items[0] = {**items[0], "qid": "QINV-QID-DRIFTED"}
        generation._prepare_final_concept_content(
            copy.deepcopy(fixture_env["skeleton_rows"]),
            question_task_inventory=qid_drifted,
            **common,
        )

    assert len(sealed) == 3
    assert sealed[2] != sealed[0]


def test_materialized_envelope_keeps_raw_resume_boundary(
    monkeypatch, tmp_path, fixture_env,
):
    """A literary materialization may change rows, not the 81% replay key."""

    sealed: list[str] = []
    build_calls = 0
    original_build = envelope_mod.build

    def counted_build(**kwargs):
        nonlocal build_calls
        build_calls += 1
        return original_build(**kwargs)

    def materialize(env):
        original = envelope_mod.validate(env)
        marker = (original.get("metadata") or {}).get(
            "post_language_plan_materialization"
        )
        if marker:
            return original
        effective = copy.deepcopy(original)
        effective["skeleton_rows"][0]["concept_title"] += " · materialized"
        effective["metadata"]["post_language_plan_materialization"] = {
            "version": 1,
            "prior_skeleton_row_count": len(original["skeleton_rows"]),
        }
        effective["envelope_sha256"] = envelope_mod.seal_sha256(effective)
        return envelope_mod.validate(effective)

    def fake_run(env, *, store_dir=None, providers=None):
        sealed.append(env["envelope_sha256"])
        return {
            "records": [{"concept_title": "Stub Row"}],
            "host_map": {}, "qid_map": {}, "new_concepts": [],
            "coverage": {"items": 0, "routed_qids": [], "unrouted": []},
            "summary": {
                "row_count": 1, "flagged_row_count": 0,
                "routed_qids": 0, "unrouted_items": 0,
                "envelope_sha256": env["envelope_sha256"],
            },
        }

    monkeypatch.setattr(envelope_mod, "build", counted_build)
    monkeypatch.setattr(formation, "materialize_envelope", materialize)
    monkeypatch.setattr(runner, "run", fake_run)
    four_output_release_contract._install_runner_handoff()

    raw_rows = copy.deepcopy(fixture_env["skeleton_rows"])
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
            copy.deepcopy(raw_rows),
            question_task_inventory=copy.deepcopy(fixture_env["inventory"]),
            **common,
        )
        drifted = copy.deepcopy(fixture_env["inventory"])
        drifted["_resume_refresh_marker"] = "different-bytes"
        generation._prepare_final_concept_content(
            copy.deepcopy(raw_rows),
            question_task_inventory=drifted,
            **common,
        )

    wrapper = json.loads(
        (tmp_path / "source.phase3-envelope.json").read_text(encoding="utf-8")
    )
    effective = envelope_mod.validate(wrapper["envelope"])
    raw_boundary_sha = phase3._sha256_json(raw_rows)
    effective_rows_sha = phase3._sha256_json(effective["skeleton_rows"])

    assert build_calls == 1
    assert sealed == [effective["envelope_sha256"]] * 2
    assert wrapper["boundary_skeleton_sha256"] == raw_boundary_sha
    assert raw_boundary_sha != effective_rows_sha
