"""Restore the durable Phase-3 -> four-output release handoff.

Job 80 exposed two integration gaps after the Job-79 literary formation repair:

* the Post-learning runner materialized the authoritative literary envelope
  *inside* ``runner.run`` after the legacy 81% seam had already persisted the
  pre-materialization envelope.  The Concept File could finish in memory while
  Output 04 later rejected the stale/missing durable envelope.
* Phase 03 Pre map/questions were returned by the runner and snapshotted, but
  the unchanged deposit call did not pass ``phase3_pre_release`` into the
  release interceptor.  The sibling Pre release therefore looked unstaged even
  though the run had already authored it.

This contract changes no educational judgment.  It only makes the two durable
handoffs agree with the model-authored artifacts that already exist.
"""
from __future__ import annotations

import copy
import importlib
import json
from functools import wraps
from pathlib import Path
from typing import Any, Mapping


CONTRACT_VERSION = 1


def _persist_effective_envelope(env: Mapping[str, Any]) -> None:
    """Persist exactly the envelope the wrapped Phase-3 runner will consume."""
    phase3_core = importlib.import_module(
        "app.services.canonical_source_phase3"
    )
    session = phase3_core.active_session() or {}
    artifact_dir = session.get("artifact_dir") if isinstance(session, dict) else None
    if not artifact_dir:
        return
    target = Path(artifact_dir) / "source.phase3-envelope.json"
    skeleton_sha = phase3_core._sha256_json(list(env.get("skeleton_rows") or []))
    phase3_core._atomic_write(
        target,
        json.dumps(
            {
                "boundary_skeleton_sha256": skeleton_sha,
                "envelope": dict(env),
            },
            ensure_ascii=False,
            indent=1,
        ),
    )


def _install_runner_handoff() -> None:
    runner = importlib.import_module("app.services.phase3.runner")
    formation = importlib.import_module(
        "app.services.postlearning_formation_contract"
    )
    current = runner.run
    if getattr(current, "_aegis_four_output_envelope_handoff", False):
        return

    @wraps(current)
    def run_with_durable_envelope(env, *args, **kwargs):
        # Materialize first, then persist, then execute that SAME envelope.
        # The inner Job-79 wrapper sees its marker and is a no-op.
        effective = formation.materialize_envelope(env)
        _persist_effective_envelope(effective)
        return current(effective, *args, **kwargs)

    run_with_durable_envelope._aegis_four_output_envelope_handoff = True
    run_with_durable_envelope._aegis_four_output_original = current
    runner.run = run_with_durable_envelope


def _install_pre_release_handoff() -> None:
    release_contract = importlib.import_module(
        "app.services.build_concepts_release_contract"
    )
    topology = importlib.import_module("app.services.concept_topology_contract")
    current = release_contract._capture_deposit
    if getattr(current, "_aegis_four_output_pre_handoff", False):
        return

    @wraps(current)
    def capture_with_pre_release(original, args, kwargs):
        result = current(original, args, kwargs)
        captured = release_contract._RELEASE_CAPTURE.get()
        if not isinstance(captured, dict):
            return result
        if isinstance(captured.get("phase3_pre_release"), Mapping):
            return result

        # The runner already wrote these sidecars atomically.  Reading them is
        # recovery/transport only: no prerequisite or question is inferred
        # here, and absence is not interpreted as "no Pre-Learning".
        restored, defects = topology.restored_pre_release()
        if isinstance(restored, Mapping):
            patched = copy.deepcopy(captured)
            patched["phase3_pre_release"] = copy.deepcopy(dict(restored))
            patched["phase3_pre_release_handoff"] = {
                "version": "four-output-release-handoff-1",
                "source": "phase3_sidecars",
                "defects": [],
            }
            release_contract._RELEASE_CAPTURE.set(patched)
        elif defects:
            patched = copy.deepcopy(captured)
            patched["phase3_pre_release_handoff"] = {
                "version": "four-output-release-handoff-1",
                "source": "phase3_sidecars",
                "defects": [str(value) for value in defects],
            }
            release_contract._RELEASE_CAPTURE.set(patched)
        return result

    capture_with_pre_release._aegis_four_output_pre_handoff = True
    capture_with_pre_release._aegis_four_output_original = current
    release_contract._capture_deposit = capture_with_pre_release


def install() -> None:
    """Install both mechanical handoffs idempotently."""
    _install_runner_handoff()
    _install_pre_release_handoff()
