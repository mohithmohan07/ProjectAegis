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
from app.services.phase3 import runner
from tests import test_phase3_analyse as analyse_golden
from tests import test_phase3_host_golden as host_golden
from tests import test_phase3_premap as premap_golden
from tests import test_phase3_prelearn as prelearn_golden
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
    golden_prelearn = json.loads(
        (settle_golden.GOLDEN / "rne_prelearn.json").read_text(
            encoding="utf-8"
        )
    )
    golden_premap = json.loads(
        (settle_golden.GOLDEN / "rne_premap.json").read_text(
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
        "prelearn": prelearn_golden.prelearn_replay_provider(
            golden_prelearn
        ),
        "premap": premap_golden.premap_replay_provider(golden_premap),
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
    # Q2/R4 re-baseline. Two deterministic repairs in the deposit-parity
    # pipeline have ALWAYS acted on this chapter and used to act silently;
    # both now ride the rows they touched.
    #
    #  * exact-once Example dedupe removes duplicate activity Examples and
    #    the Case shells the removal empties (the map's gap 4) — three
    #    rows, unchanged from the previous baseline;
    #  * coverage repair FORCE-PLACES an inventory prompt that no authored
    #    Type/Case rendered, synthesising a Type/Case shell around the raw
    #    source wording and publishing it to a learner. Ten of this
    #    chapter's 31 routed QIDs land that way. That was invisible before
    #    — nothing was dropped, but a deterministic guess reached the page
    #    unrecorded, which Q13 forbids. Flagging it is what makes the
    #    purged word-count nomination safe to remove: a section banner the
    #    extractor mistook for a task now arrives at a reviewer instead of
    #    quietly becoming an Example.
    assert summary["flagged_row_count"] == 6
    flagged_rows = [
        row for row in result["records"] if row.get("review_flags")
    ]
    assert sorted(
        _normal(row["concept_title"]) for row in flagged_rows
    ) == [
        "Autocracy, Censorship, and the Demand for Freedom of the Press",
        "Educated Middle-class Leadership of Liberal-nationalist "
        "Revolutions",
        "Gendered Limits of Liberal Political Rights",
        "Nationalism as Conservative State Power After 1848",
        "Personifying the Nation Through Female Allegories",
        "Utopian Nationalism and Democratic-social Republicanism",
    ]
    forced_qids: list[str] = []
    for row in flagged_rows:
        for flag in row["review_flags"]:
            if flag.startswith("R4: source question "):
                forced_qids.append(flag.split()[3])
                assert "force-placed its inventory wording here" in flag
                continue
            assert flag.startswith(
                "Q2: removed the example-less Case shell"
            )
            assert "its Example wording renders under:" in flag
    # Every force-placement names its QID, so the reviewer can go back to
    # the source row rather than guessing which Example was synthesised.
    assert sorted(forced_qids) == [
        "QINV-0001", "QINV-0005", "QINV-0009", "QINV-0010", "QINV-0011.1",
        "QINV-0011.2", "QINV-0012", "QINV-0013", "QINV-0014", "QINV-0015",
    ]
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
    # Phase 03 (Q3): the running pre-requisite capture rode through the
    # runner alongside every stage — four captures over four different
    # stage payloads, then one merge that consolidates every capture
    # exactly once (R4). It never touches the Post rows.
    prerequisites = result["prerequisites"]
    assert set(prerequisites["captures"]) == {
        "settle", "host", "place", "analyse",
    }
    assert [
        len(prerequisites["captures"][stage])
        for stage in ("settle", "host", "place", "analyse")
    ] == [7, 6, 3, 4]
    assert len(prerequisites["prerequisites"]) == 16
    consolidated = [
        ref
        for row in prerequisites["prerequisites"]
        for ref in row["captures"]
    ]
    assert len(consolidated) == len(set(consolidated)) == 20
    assert prerequisites["review_flags"] == {}
    # No decision drew a dissent on the golden replay, at a row or at a
    # decision — the two channels agree here.
    assert prerequisites["stage_flags"] == {}
    # A prerequisite seen by three stages carries all three, which is
    # what makes the doc's word "running" mean something.
    assert sorted(
        row["stages"] for row in prerequisites["prerequisites"]
        if len(row["stages"]) > 1
    ) == [["settle", "analyse"], ["settle", "host"],
          ["settle", "host", "place"]]
    # Phase 03 (doc §4): the consolidated capture became the complete
    # Pre-Learning concept map, inside the same run. It is built from the
    # capture — the build payload never sees a Post concept row — and
    # every one of the 16 captured prerequisites is mapped exactly once
    # (R4) across 15 pre-concepts in 6 pre-topics. No count anywhere is
    # derived from the size of the capture or of the chapter.
    pre_map = result["pre_map"]
    assert len(pre_map["topics"]) == 6
    assert len(pre_map["rows"]) == 15
    mapped = [
        entry["prerequisite_id"]
        for row in pre_map["rows"]
        for entry in row["_aegis_pre_prerequisites"]
    ]
    assert sorted(mapped) == sorted(
        row["prerequisite_id"] for row in prerequisites["prerequisites"]
    )
    assert len(mapped) == len(set(mapped)) == 16
    # Every Pre row ships Description + Achieving Mastery and nothing
    # else, and NO source question id appears anywhere in the Pre map —
    # the owner's steer, checked mechanically (it fails closed in the
    # pass itself; this is the golden-chapter proof).
    pre_flat = json.dumps(pre_map["rows"], ensure_ascii=False)
    for item in golden_envelope["inventory"]["items"]:
        assert str(item["qid"]) not in pre_flat
    for row in pre_map["rows"]:
        assert row["concept_details"].startswith("Description: ")
        assert "\nAchieving Mastery: " in row["concept_details"]
        assert "// Types:" not in row["concept_details"]
        assert "Misconception" not in row["concept_details"]
        # Grounded on the prerequisite capture, never on this chapter's
        # blocks (spec T4, the Sorrieu passage).
        assert row["_source_block_ids"] == []
        assert row["_source_grounding_contract"] == (
            "derived-from-prerequisite-capture"
        )
        assert row["_source_grounding_record_sha256"]
    # Every needed-for link resolves to a real Post concept of this run.
    from app.services.phase3 import place as place_mod

    post_ids = set(place_mod.mint_concept_ids(result["records"]))
    for row in pre_map["rows"]:
        for link in row["_aegis_needed_for"]:
            assert link["post_concept_id"] in post_ids
            assert link["post_concept_title"]
    # Exactly three advisory flags on the golden chapter, and every one
    # of them is honest:
    #  * PRC-0015 ("Finding Information Beyond the Textbook") is linked to
    #    nothing — no post concept of this chapter genuinely requires
    #    independent research. Necessity is a CRITIC dimension (Q10), so
    #    the concept ships flagged rather than being dropped.
    #  * PRC-0001 and PRC-0013 trip concept_validator's `placeholder`
    #    code, whose PLACEHOLDERS vocabulary contains the word "none" and
    #    fires on the ordinary English "faces none at all" / "almost
    #    none". That is a keyword vocabulary classifying content in the
    #    SHARED validator (Rule 1) — recorded here, not authored around,
    #    and advisory in this lane so it can never gate a row.
    assert sorted(pre_map["review_flags"]) == [
        "PRC-0001", "PRC-0013", "PRC-0015",
    ]
    assert any(
        "its necessity needs review" in flag
        for flag in pre_map["review_flags"]["PRC-0015"]
    )
    assert {finding["code"] for finding in pre_map["validation"]} == {
        "placeholder"
    }
    # No decision drew a critic dissent on the golden replay.
    assert pre_map["decision_flags"] == {}
    # And the Pre map rides its own key: no Pre row leaks into the Post
    # release records the publication chain consumes.
    assert not any(
        "_pre_concept_id" in row for row in result["records"]
    )

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


def test_a_refused_pre_map_still_ships_the_finished_post_map(
    golden_envelope, replay_providers, tmp_path_factory,
):
    """CLAUDE.md: "finished work always ships".

    The no-extraction guard fails closed on the Pre ARTEFACT — a Pre row
    carrying a source question's identity never ships, and
    ``premap.build`` refuses it. But by the time the Pre map is built,
    Settle → Host → Place → Analyse → Polish → Assemble have all
    completed and been paid for. Letting the refusal propagate would
    discard all 53 finished rows, and discard them PERMANENTLY: the
    offending decision is stored content-addressed, so the replay
    reproduces the refusal at zero spend with no route out. That is the
    hard-stuck run Q13 exists to prevent.

    So the Pre map is refused and RECORDED, and the Post map ships.
    """
    import copy

    from app.services.phase3 import premap as premap_mod

    store_dir = tmp_path_factory.mktemp("refused-store")
    clean = runner.run(
        golden_envelope, store_dir=store_dir, providers=replay_providers,
    )

    qid = premap_mod.inventory_qids(golden_envelope)[0]
    original = replay_providers["premap"]

    def leaking(request: dict) -> dict:
        response = copy.deepcopy(original(request))
        if str(request.get("stage") or "") == "premap.map":
            concept = response["topics"][0]["concepts"][0]
            concept["achieving_mastery"] += f" (see {qid})"
        return response

    poisoned_dir = tmp_path_factory.mktemp("refused-store-2")
    result = runner.run(
        golden_envelope,
        store_dir=poisoned_dir,
        providers={**replay_providers, "premap": leaking},
    )

    # The finished Post map is untouched and reachable.
    assert result["records"] == clean["records"]
    assert result["summary"]["row_count"] == 53
    assert result["summary"]["routed_qids"] == 31

    # The Pre map is refused, empty, and says so — nothing lost silently.
    pre_map = result["pre_map"]
    assert pre_map["rows"] == []
    assert qid in pre_map["refused"]
    assert "Pre-Learning concept row" in pre_map["refused"]

    # And the refusal is durable, not just in-process.
    import json as _json

    snapshot = _json.loads(
        (poisoned_dir.parent / "source.phase3-prelearn-map.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["refused"] == pre_map["refused"]
    assert snapshot["rows"] == []


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

    # The Phase 03 capture lands beside the store too, so the Pre lane
    # and the diagnostics export can read it long after the run.
    capture = json.loads(
        (artifact_dir / "source.phase3-prelearn-capture.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(capture) == {
        "captures", "prerequisites", "review_flags", "stage_flags",
    }
    assert len(capture["prerequisites"]) == 16


def test_the_migration_flag_is_retired():
    # PR 4: the rewritten Phase 3 is the only post-81% path; the
    # AEGIS_PHASE3_REWRITE flag no longer exists anywhere.
    assert not hasattr(runner, "rewrite_enabled")
