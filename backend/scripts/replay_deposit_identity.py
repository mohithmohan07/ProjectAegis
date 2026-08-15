"""Pass-by-pass identity replay of the deposit-only cleanup chain (#218).

The deposit gate's second ``verify_final_certificate`` (the one that fired
``CONCEPT-GROUND-0002`` on Balbharati Std 6 CH01) exists to catch a sealed
row whose identity drifts inside the deposit-only cleanup between the two
certificate checks in ``_deposit_concepts``. This tool replays that exact
chain over a freshly sealed payload and, after EVERY pass, diffs each
row's ``row_identity_material`` against the certificate's attested
``evidence[].row_identity`` — so the guilty pass is named the moment it
mutates an identity field, not at the end of the chain.

Baseline payload: the golden job-23 rows (CBSE Rise of Nationalism),
re-sealed by replaying Settle → Host → Assemble from ``tests/golden``.
The drift is content-conditional, so the interesting runs are the
divergence levers that mirror how a live deposit differs from seal time:

  --chapter-title / --subject / --board   deposit uses DB chapter fields,
                                          the seal used env metadata
  --strip-release-qids                    forces the non-rewrite hub
                                          binding branch
  --json-roundtrip                        rows/inventory/mined_types via
                                          json round-trip (persisted-
                                          artifact provenance)

Run offline (no API calls, ~2-3 minutes):

    python3 scripts/replay_deposit_identity.py
    python3 scripts/replay_deposit_identity.py \
        --chapter-title "The Rise of Nationalism in Europe (10_History_CBSE_NCERT)" \
        --subject "Social Science" --json-roundtrip
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def replay_golden_sealed_rows():
    """Settle → Host → Assemble over tests/golden, exactly as the golden
    gates replay it, returning (records, inventory, mined_types)."""
    from tests import test_phase3_host_golden as host_golden
    from tests import test_phase3_settle_golden as settle_golden
    from app.services.phase3 import assemble as assemble_mod
    from app.services.phase3 import host as host_mod
    from app.services.phase3 import kernel, settle

    golden = settle_golden.GOLDEN
    envelope = settle_golden.envelope_mod.load(golden / "rne_envelope.json")
    golden_rows = json.loads(
        (golden / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]
    golden_hosts = json.loads(
        (golden / "rne_host_maps.json").read_text(encoding="utf-8"))
    mapping = settle_golden._replay_map(envelope, golden_rows)
    topology, grounding, analysis, critic = settle_golden._providers(mapping)
    settled = settle.settle(
        envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )
    hosts = host_mod.host(
        envelope,
        settled,
        provider=host_golden._replay_provider(golden_hosts, settled),
        critic=host_golden._verified_critic,
        store=kernel.DecisionStore(),
    )
    result = assemble_mod.assemble(envelope, settled, hosts)
    records = result["rows"]
    inventory = copy.deepcopy(envelope.get("inventory") or {})
    mined_types = copy.deepcopy(envelope.get("mined_types") or {})
    return records, inventory, mined_types


def identity_diffs(records, attested_by_concept_id) -> list[str]:
    """Per-row identity diff against the certificate's attested material."""
    from app.services import grounding_certificate as gc

    diffs: list[str] = []
    seen: set[str] = set()
    for record in records:
        concept_id = str(record.get(gc.CONCEPT_ID_FIELD) or "")
        if not concept_id:
            continue
        seen.add(concept_id)
        attested = attested_by_concept_id.get(concept_id)
        if attested is None:
            diffs.append(f"NEW row {concept_id} (not in the certificate)")
            continue
        current = gc.row_identity_material(record)
        for field in sorted(set(attested) | set(current)):
            if attested.get(field) != current.get(field):
                diffs.append(
                    f"{concept_id}.{field}: attested "
                    f"{attested.get(field)!r} != current "
                    f"{current.get(field)!r}")
    for concept_id in sorted(set(attested_by_concept_id) - seen):
        diffs.append(f"LOST row {concept_id}")
    return diffs


def deposit_chain(chapter_title: str, subject: str, board: str):
    """The deposit-only cleanup passes, in _deposit_concepts order
    (backend/app/services/build_concepts.py between the two
    verify_final_certificate calls). Each step mirrors its anchor line."""
    from app.services import concept_cleanup, concept_refiner
    from app.services import concept_validator as cv
    from app.services import generation

    def clean(records, _ctx):  # build_concepts.py:975
        return [concept_cleanup.clean_concept_record(dict(r))
                for r in records]

    def review_filter(records, _ctx):  # :976-978
        return concept_cleanup.filter_review_violations(
            records, subject=subject, board=board,
            chapter_title=chapter_title)

    def dedupe(records, _ctx):  # :979
        return concept_cleanup.dedupe_similar_titles_chapter_wide(records)

    def refine(records, _ctx):  # :980
        return concept_refiner.refine_chapter(records)

    def learner_analysis(records, _ctx):  # :985
        return cv.ensure_valid_learner_analysis(records)

    def mastery(records, _ctx):  # :987
        return generation._ensure_mastery_lines_via_api(
            records, meta=_ctx["meta"], use_api=False)

    def culmination(records, _ctx):  # :989
        return generation._ensure_terminal_culmination_contract(records)

    def rich_text(records, _ctx):  # :990
        return generation._canonicalize_concept_rich_text(records)

    def hub_normalize(records, ctx):  # :1006
        return generation._normalize_activity_hubs_from_inventory(
            records, ctx["inventory"], ctx["mined_types"])

    def coverage_1(records, ctx):  # :1008
        return generation._enforce_rendered_inventory_coverage(
            records, ctx["inventory"], ctx["mined_types"])

    def rich_text_2(records, _ctx):  # :1015
        return generation._canonicalize_concept_rich_text(records)

    def coverage_2(records, ctx):  # :1016
        return generation._enforce_rendered_inventory_coverage(
            records, ctx["inventory"], ctx["mined_types"])

    def rich_text_3(records, _ctx):  # :1018
        return generation._canonicalize_concept_rich_text(records)

    def renumber(records, _ctx):  # :1021
        return concept_refiner.renumber_types_continuously(records)

    def learner_analysis_2(records, _ctx):  # :1022
        return cv.ensure_valid_learner_analysis(records)

    return [
        ("clean_concept_record", clean),
        ("filter_review_violations", review_filter),
        ("dedupe_similar_titles_chapter_wide", dedupe),
        ("refine_chapter", refine),
        ("ensure_valid_learner_analysis", learner_analysis),
        ("_ensure_mastery_lines(use_api=False)", mastery),
        ("_ensure_terminal_culmination_contract", culmination),
        ("_canonicalize_concept_rich_text", rich_text),
        ("_normalize_activity_hubs_from_inventory", hub_normalize),
        ("_enforce_rendered_inventory_coverage #1", coverage_1),
        ("_canonicalize_concept_rich_text #2", rich_text_2),
        ("_enforce_rendered_inventory_coverage #2", coverage_2),
        ("_canonicalize_concept_rich_text #3", rich_text_3),
        ("renumber_types_continuously", renumber),
        ("ensure_valid_learner_analysis #2", learner_analysis_2),
    ]


def replay(records, inventory, mined_types, certificate, *,
           chapter_title: str, subject: str, board: str,
           json_roundtrip: bool = False,
           strip_release_qids: bool = False) -> int:
    from app.services import grounding_certificate as gc

    if json_roundtrip:
        records = json.loads(json.dumps(records))
        inventory = json.loads(json.dumps(inventory))
        mined_types = json.loads(json.dumps(mined_types))
    if strip_release_qids:
        for record in records:
            record.pop("_aegis_release_qids", None)

    attested = {
        str(entry.get("concept_id") or ""): dict(
            entry.get("row_identity") or {})
        for entry in certificate.get("evidence") or []
    }
    context = {
        "inventory": inventory,
        "mined_types": mined_types,
        "meta": {"subject": subject, "board": board,
                 "chapter_title": chapter_title},
    }
    drifted = 0
    for name, step in deposit_chain(chapter_title, subject, board):
        try:
            records = step(records, context)
        except Exception as exc:  # noqa: BLE001 — name the pass, keep going
            print(f"[STOP] {name}: {type(exc).__name__}: {exc}")
            return 2
        diffs = identity_diffs(records, attested)
        if diffs:
            drifted += 1
            print(f"[DRIFT] after {name}:")
            for diff in diffs[:20]:
                print(f"    {diff}")
        else:
            print(f"[ok]    {name}")

    try:
        gc.verify_final_certificate(
            records, certificate,
            require_semantic_graph=False,
            require_placement_contracts=False,
        )
        print("[ok]    final verify_final_certificate")
    except Exception as exc:  # noqa: BLE001
        print(f"[DRIFT] final verify_final_certificate: {exc}")
        drifted += 1
    return 1 if drifted else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--chapter-title", default="The Rise of Nationalism in Europe")
    parser.add_argument("--subject", default="History")
    parser.add_argument("--board", default="CBSE")
    parser.add_argument("--json-roundtrip", action="store_true")
    parser.add_argument("--strip-release-qids", action="store_true")
    args = parser.parse_args()

    from app.services import grounding_certificate as gc

    print("Replaying Settle -> Host -> Assemble over tests/golden ...")
    records, inventory, mined_types = replay_golden_sealed_rows()
    print(f"sealed rows: {len(records)}")
    certificate = gc.build_final_certificate(
        records, require_placement_contracts=False)
    print("certificate minted; replaying the deposit-only cleanup chain\n")
    code = replay(
        records, inventory, mined_types, certificate,
        chapter_title=args.chapter_title,
        subject=args.subject,
        board=args.board,
        json_roundtrip=args.json_roundtrip,
        strip_release_qids=args.strip_release_qids,
    )
    print("\nresult:", "IDENTITY CLEAN" if code == 0 else
          "DRIFT OR STOP — see the named pass above")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
