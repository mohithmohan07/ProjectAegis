from __future__ import annotations

import json
from pathlib import Path

from app.services import four_output_release_contract as contract


def test_runner_persists_the_effective_materialized_envelope(monkeypatch, tmp_path):
    from app.services import canonical_source_phase3 as phase3_core
    from app.services import postlearning_formation_contract as formation
    from app.services.phase3 import runner

    seen = {}

    def inner(env, *args, **kwargs):
        seen["env"] = env
        return {"records": [], "summary": {}}

    monkeypatch.setattr(runner, "run", inner)
    monkeypatch.setattr(
        formation,
        "materialize_envelope",
        lambda env: {
            **dict(env),
            "skeleton_rows": [{"concept_title": "planned"}],
            "envelope_sha256": "effective-seal",
        },
    )
    monkeypatch.setattr(
        phase3_core, "active_session", lambda: {"artifact_dir": str(tmp_path)}
    )
    monkeypatch.setattr(
        phase3_core,
        "_sha256_json",
        lambda value: "effective-skeleton-sha",
    )

    contract._install_runner_handoff()
    runner.run({"skeleton_rows": [{"concept_title": "old"}]})

    assert seen["env"]["envelope_sha256"] == "effective-seal"
    wrapper = json.loads(
        (tmp_path / "source.phase3-envelope.json").read_text(encoding="utf-8")
    )
    assert wrapper["boundary_skeleton_sha256"] == "effective-skeleton-sha"
    assert wrapper["envelope"]["envelope_sha256"] == "effective-seal"
    assert wrapper["envelope"]["skeleton_rows"] == [
        {"concept_title": "planned"}
    ]


def test_release_capture_recovers_authored_pre_bundle_from_sidecars(monkeypatch):
    from app.services import build_concepts_release_contract as release_contract
    from app.services import concept_topology_contract as topology

    def base_capture(_original, _args, _kwargs):
        release_contract._RELEASE_CAPTURE.set({
            "records": [{"concept_title": "Post"}],
            "phase3_pre_release": None,
        })
        return ([], [], {"written": 1})

    monkeypatch.setattr(release_contract, "_capture_deposit", base_capture)
    monkeypatch.setattr(
        topology,
        "restored_pre_release",
        lambda: ({
            "pre_map": {"rows": [{"concept_title": "Prior knowledge"}]},
            "pre_questions": {"questions": {"PRE-1": []}},
        }, []),
    )

    contract._install_pre_release_handoff()
    result = release_contract._capture_deposit(lambda: None, (), {})

    assert result == ([], [], {"written": 1})
    captured = release_contract._RELEASE_CAPTURE.get()
    assert captured["phase3_pre_release"]["pre_map"]["rows"] == [
        {"concept_title": "Prior knowledge"}
    ]
    assert captured["phase3_pre_release_handoff"]["source"] == "phase3_sidecars"
    # The regression this handoff shipped with: the raw sidecar pair has no
    # schema_version, and storing it verbatim made the staging gate
    # (valid_phase3_pre_release_bundle) reject the recovered, already-paid
    # Pre authority as malformed — the run then staged no Pre release. The
    # handoff must mint the one real bundle shape.
    from app.services import generation

    assert generation.valid_phase3_pre_release_bundle(
        captured["phase3_pre_release"]
    ), "restored sidecar authority must satisfy the staging gate"


def test_missing_pre_sidecars_are_not_misread_as_empty_pre_learning(monkeypatch):
    from app.services import build_concepts_release_contract as release_contract
    from app.services import concept_topology_contract as topology

    def base_capture(_original, _args, _kwargs):
        release_contract._RELEASE_CAPTURE.set({
            "records": [{"concept_title": "Post"}],
            "phase3_pre_release": None,
        })
        return ([], [], {"written": 1})

    monkeypatch.setattr(release_contract, "_capture_deposit", base_capture)
    monkeypatch.setattr(
        topology,
        "restored_pre_release",
        lambda: (None, ["source.phase3-prelearn-map.json is absent"]),
    )

    contract._install_pre_release_handoff()
    release_contract._capture_deposit(lambda: None, (), {})

    captured = release_contract._RELEASE_CAPTURE.get()
    assert captured["phase3_pre_release"] is None
    assert captured["phase3_pre_release_handoff"]["defects"] == [
        "source.phase3-prelearn-map.json is absent"
    ]
