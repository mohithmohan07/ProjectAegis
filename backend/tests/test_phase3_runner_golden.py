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
from tests import test_phase3_analyse as analyse_golden
from tests import test_phase3_host_golden as host_golden
from tests import test_phase3_settle_golden as settle_golden


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@pytest.fixture(scope="module")
def golden_envelope():
    return settle_golden.envelope_mod.load(
        settle_golden.GOLDEN / "rne_envelope.json"
    )


def place_replay_provider(golden_place: dict):
    """Answer each pooled batch with the recorded Phase 2.2 verdicts.

    The recorded fixture names each hub item's destination by
    (topic_id, concept_title); the provider resolves that pair against
    the request's own settled_concepts payload, exactly as a recorded
    concept_id replay would.
    """

    def provider(request: dict) -> dict:
        by_identity = {
            (
                str(row.get("topic_id") or ""),
                _normal(row.get("concept_title")).casefold(),
            ): str(row.get("concept_id") or "")
            for row in request.get("settled_concepts") or []
        }
        placements = []
        for entry in request.get("pool") or []:
            ref = str(entry.get("item_ref") or "")
            recorded = (golden_place.get("hub_placements") or {}).get(ref)
            if recorded is None:
                recorded = (golden_place.get("figure_placements") or {}).get(
                    ref
                )
            assert recorded is not None, f"no recorded placement for {ref}"
            placements.append({
                "item_ref": ref,
                "concept_id": by_identity[(
                    str(recorded.get("topic_id") or ""),
                    _normal(recorded.get("concept_title")).casefold(),
                )],
                "rationale": str(
                    recorded.get("rationale")
                    or "replayed from the golden place fixture"
                ),
            })
        return {"placements": placements}

    return provider


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
    golden_place = json.loads(
        (settle_golden.GOLDEN / "rne_place.json").read_text(
            encoding="utf-8"
        )
    )
    golden_analysis = json.loads(
        (settle_golden.GOLDEN / "rne_analysis.json").read_text(
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
        "place": place_replay_provider(golden_place),
        "analyse": analyse_golden.analyse_replay_provider(golden_analysis),
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
    # Q2/R4 re-baseline: the deposit-parity pipeline's exact-once
    # Example dedupe has ALWAYS removed these rows' duplicate activity
    # Examples and the Case shells the removal emptied — it used to do
    # so silently (the map's gap 4). The removals are now review flags
    # naming where each Example renders, so the three affected rows
    # (the hub-relocated Wolff account plus the two rows whose
    # duplicated figure-activity Cases were deduped) surface as flagged.
    assert summary["flagged_row_count"] == 3
    flagged_rows = [
        row for row in result["records"] if row.get("review_flags")
    ]
    assert sorted(
        _normal(row["concept_title"]) for row in flagged_rows
    ) == [
        "Educated Middle-class Leadership of Liberal-nationalist "
        "Revolutions",
        "Nationalism as Conservative State Power After 1848",
        "Personifying the Nation Through Female Allegories",
    ]
    for row in flagged_rows:
        for flag in row["review_flags"]:
            assert flag.startswith(
                "Q2: removed the example-less Case shell"
            )
            assert "its Example(s) render under:" in flag
    assert summary["envelope_sha256"] == golden_envelope["envelope_sha256"]
    # Q1: the analysis inventory rode through the runner — every LA-item
    # allotted exactly once, and only allotted rows carry the section.
    analysis = result["analysis"]
    assert len(analysis["inventory"]) == 94
    assert set(analysis["allotments"]) == {
        item["item_id"] for item in analysis["inventory"]
    }
    coverage_analysis = result["coverage"]["learner_analysis"]
    assert coverage_analysis["allotments"] == analysis["allotments"]
    for row in result["records"]:
        has_section = "Misconception/ Error Analysis" in str(
            row["concept_details"]
        )
        assert has_section == bool(row.get("_aegis_analysis_allotments"))
    # The Q2 deterministic audit found nothing on the golden replay.
    assert result["coverage"]["case_audit"] == []
    assert result["coverage"]["case_splits"] == []

    # Every row survives the actual publication cleaner used by
    # stage_release upload and the bulk-import writer chain.
    for row in result["records"]:
        cleaned = concept_cleanup.clean_concept_record(dict(row))
        assert _normal(cleaned["topic"])
        assert _normal(cleaned["concept_title"])
        assert cleaned["concept_details"].startswith("Description:")

    # Hosted rows carry the house Types section. One exception is a row
    # whose ONLY Example is an _activity_origin prompt: the Phase 2.2
    # placement pass put that item's hub on the concept its content
    # exercises (the Wolff-account row), and the align pass moves the
    # assessable Example WITH its hub — the dual-role identity travels
    # together, so the origin row keeps its route marker (audit) while
    # the visible Example renders beside its placed hub note.
    hosted = [
        row for row in result["records"]
        if row.get("_aegis_release_type_case_routes")
    ]
    assert hosted
    without_types = [
        _normal(row["concept_title"]) for row in hosted
        if "// Types:" not in row["concept_details"]
    ]
    assert without_types == [
        "Educated Middle-class Leadership of Liberal-nationalist Revolutions"
    ]

    # Phase 2.2: every pooled hub item was placed by the recorded model
    # verdict and its note renders on the placed row — the marker is the
    # release-audit trail of that placement.
    golden_place = json.loads(
        (settle_golden.GOLDEN / "rne_place.json").read_text(
            encoding="utf-8"
        )
    )
    marked: dict[str, str] = {}
    for row in result["records"]:
        for qid in row.get("_aegis_hub_placements") or []:
            marked[qid] = _normal(row["concept_title"])
            assert "Activity/Info Hub:" in row["concept_details"], qid
    assert marked == {
        qid: _normal(entry["concept_title"])
        for qid, entry in golden_place["hub_placements"].items()
    }
    # The coverage accounting names every pooled verdict (R4).
    assert set(result["coverage"]["hub_placements"]) == set(marked)
    assert result["coverage"]["figure_placements"] == {}
    assert result["coverage"]["figure_dispositions"] == {}

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


def test_settled_rows_snapshot_lands_beside_the_store(
    golden_envelope, replay_providers, tmp_path_factory,
):
    """A failure after Settle must leave the release something to ship."""
    import json

    from app.services import canonical_source_phase3 as phase3_core

    artifact_dir = tmp_path_factory.mktemp("artifacts")
    store_dir = artifact_dir / "phase3-decisions"
    runner.run(
        golden_envelope, store_dir=store_dir, providers=replay_providers,
    )

    snapshot = json.loads(
        (artifact_dir / "source.phase3-settled-rows.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(snapshot["records"]) == 53
    assert snapshot["records_sha256"] == phase3_core._sha256_json(
        snapshot["records"]
    )
    assert snapshot["source_contract_hash"] == golden_envelope[
        "source_contract_hash"
    ]


def test_the_migration_flag_is_retired():
    # PR 4: the rewritten Phase 3 is the only post-81% path; the
    # AEGIS_PHASE3_REWRITE flag no longer exists anywhere.
    assert not hasattr(runner, "rewrite_enabled")
