"""Golden gate 4: the full runner, envelope to release-ready output.

One call runs the complete rewritten Phase 3 on job 23's envelope with
replay providers, and the output must be directly consumable by the
existing publication chain (stage_release records → clean_concept_record
→ the bulk-import writer).
"""
from __future__ import annotations

import json
import re

import pytest

from app.services import concept_cleanup
from app.services.phase3 import kernel, runner
from tests import test_phase3_host_golden as host_golden
from tests import test_phase3_settle_golden as settle_golden


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@pytest.fixture(scope="module")
def golden_envelope():
    return settle_golden.envelope_mod.load(
        settle_golden.GOLDEN / "rne_envelope.json"
    )


@pytest.fixture(scope="module")
def replay_providers(golden_envelope):
    golden_rows = json.loads(
        (settle_golden.GOLDEN / "rne_settled_rows.json").read_text(
            encoding="utf-8"
        )
    )["records"]
    golden_hosts = json.loads(
        (settle_golden.GOLDEN / "rne_host_maps.json").read_text(
            encoding="utf-8"
        )
    )
    mapping = settle_golden._replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(mapping)

    class _HostProvider:
        """Host replay needs the settled rows the run itself produces."""

        def __init__(self) -> None:
            self.rows = golden_rows

        def __call__(self, request: dict) -> dict:
            return host_golden._replay_provider(
                golden_hosts, self.rows
            )(request)

    return {
        "topology": topology,
        "grounding": grounding,
        "analysis": analysis,
        "host": _HostProvider(),
        "critic": critic,
    }


def test_runner_produces_publication_ready_output(
    golden_envelope, replay_providers, tmp_path_factory,
):
    store_dir = tmp_path_factory.mktemp("store")
    result = runner.run(
        golden_envelope,
        store_dir=store_dir,
        providers=replay_providers,
    )

    summary = result["summary"]
    assert summary["row_count"] == 53
    # The old run certified 19 QID hosts before dying; the new pass
    # decides every unit, so every case-covered QID reaches a host.
    assert summary["routed_qids"] == 31
    assert summary["unrouted_items"] == 0
    assert summary["flagged_row_count"] == 0
    assert summary["envelope_sha256"] == golden_envelope["envelope_sha256"]

    # Every row survives the actual publication cleaner used by
    # stage_release upload and the bulk-import writer chain.
    for row in result["records"]:
        cleaned = concept_cleanup.clean_concept_record(dict(row))
        assert _normal(cleaned["topic"])
        assert _normal(cleaned["concept_title"])
        assert cleaned["concept_details"].startswith("Description:")

    # Hosted rows carry the house Types section.
    hosted = [
        row for row in result["records"]
        if row.get("_aegis_release_type_case_routes")
    ]
    assert hosted
    assert all("// Types:" in row["concept_details"] for row in hosted)

    # A second run against the same store replays every decision for free.
    calls = {"n": 0}
    original = replay_providers["topology"]

    def counted(request: dict) -> dict:
        calls["n"] += 1
        return original(request)

    again = runner.run(
        golden_envelope,
        store_dir=store_dir,
        providers={**replay_providers, "topology": counted},
    )
    assert calls["n"] == 0
    assert again == result


def test_the_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("AEGIS_PHASE3_REWRITE", raising=False)
    assert runner.rewrite_enabled() is False
    monkeypatch.setenv("AEGIS_PHASE3_REWRITE", "1")
    assert runner.rewrite_enabled() is True
