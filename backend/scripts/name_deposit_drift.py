"""Name the guilty deposit-cleanup pass from a live checkpoint export (#218).

Feed this the job's checkpoint bundle (GET
``/build-concepts/uploads/{job_id}/checkpoint``) and the target chapter's
identity as the database stores it. The tool pulls the sealed
``final_content_ready`` records and the final grounding certificate out of
the bundle, replays the exact deposit-only cleanup chain pass by pass
(``scripts/replay_deposit_identity.py`` carries the chain), and reports the
first pass after which any row's ``row_identity_material`` no longer
matches the certificate's attested ``evidence[].row_identity`` — the
drifted field is named with attested and current values.

When the bundle lacks the sealed records (a deposit failure durably
discards the ``final_content_ready`` stage), the tool degrades gracefully:
it prints the attested identity of every certificate row — one-line
comparison against the live gate's error text, which prints the current
identity — and diffs the final certificate against the deposited one when
both are present.

    python3 scripts/name_deposit_drift.py bundle.aegis-checkpoint.json \
        --chapter-title "Three-Dimensional Shapes (06_Mathematics_MSBSHSE_Balbharati)" \
        --subject Mathematics --board MSB
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from replay_deposit_identity import deposit_chain, identity_diffs  # noqa: E402


def _walk(value, key: str):
    """Every dict value stored under ``key`` anywhere in the bundle."""
    if isinstance(value, dict):
        if key in value:
            yield value[key]
        for child in value.values():
            yield from _walk(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)


def _newest_final_stage(bundle) -> dict | None:
    """The newest checkpoint entry sealed at final_content_ready."""
    candidates = [
        entry
        for stage_list in _walk(bundle, "checkpoints")
        for entry in (stage_list if isinstance(stage_list, list) else [])
        if isinstance(entry, dict)
        and entry.get("stage") == "final_content_ready"
        and entry.get("records")
    ]
    if not candidates:
        # A flat bundle may carry one stage dict rather than a history list.
        candidates = [
            entry for entry in _walk(bundle, "generation_checkpoint")
            if isinstance(entry, dict)
            and entry.get("stage") == "final_content_ready"
            and entry.get("records")
        ]
    candidates.sort(key=lambda e: str(e.get("saved_at") or ""))
    return candidates[-1] if candidates else None


def _first(bundle, key: str):
    for value in _walk(bundle, key):
        if value:
            return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--chapter-title", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--board", default="")
    args = parser.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    certificate = _first(bundle, "final_grounding_certificate")
    deposited = _first(bundle, "deposited_grounding_certificate")
    if not isinstance(certificate, dict):
        print("no final_grounding_certificate in the bundle; nothing to "
              "diff against")
        return 2
    attested = {
        str(entry.get("concept_id") or ""): dict(
            entry.get("row_identity") or {})
        for entry in certificate.get("evidence") or []
    }
    print(f"certificate rows: {len(attested)}")

    final_stage = _newest_final_stage(bundle)
    inventory = _first(bundle, "question_task_inventory") or _first(
        bundle, "question_inventory") or {}
    mined_types = _first(bundle, "mined_types") or {}

    if final_stage is None:
        print("\nno sealed final_content_ready records in the bundle "
              "(a deposit failure durably discards that stage).")
        if isinstance(deposited, dict):
            deposited_attested = {
                str(entry.get("concept_id") or ""): dict(
                    entry.get("row_identity") or {})
                for entry in deposited.get("evidence") or []
            }
            drifted = False
            for concept_id in sorted(set(attested) | set(deposited_attested)):
                a = attested.get(concept_id)
                d = deposited_attested.get(concept_id)
                if a == d:
                    continue
                drifted = True
                print(f"\n{concept_id} differs between final and deposited "
                      "certificates:")
                for field in sorted(set(a or {}) | set(d or {})):
                    if (a or {}).get(field) != (d or {}).get(field):
                        print(f"  {field}: final={(a or {}).get(field)!r} "
                              f"deposited={(d or {}).get(field)!r}")
            if not drifted:
                print("final and deposited certificates agree row-for-row.")
        print("\nattested identity per row (compare against the live "
              "gate's printed current identity):")
        for concept_id in sorted(attested):
            print(f"  {concept_id}: "
                  + json.dumps(attested[concept_id], ensure_ascii=False))
        return 0

    records = json.loads(json.dumps(final_stage["records"]))
    print(f"sealed records: {len(records)} "
          f"(stage saved_at {final_stage.get('saved_at')})")
    context = {
        "inventory": inventory,
        "mined_types": mined_types,
        "meta": {"subject": args.subject, "board": args.board,
                 "chapter_title": args.chapter_title},
    }
    for name, step in deposit_chain(
        args.chapter_title, args.subject, args.board,
    ):
        try:
            records = step(records, context)
        except Exception as exc:  # noqa: BLE001 — name the pass
            print(f"[STOP] {name}: {type(exc).__name__}: {exc}")
            return 2
        diffs = identity_diffs(records, attested)
        if diffs:
            print(f"\n[DRIFT] first drift after {name}:")
            for diff in diffs[:40]:
                print(f"    {diff}")
            return 1
        print(f"[ok]    {name}")
    print("\nno identity drift across the whole deposit chain for this "
          "bundle + chapter identity; the live divergence needs different "
          "inputs (check the chapter row's stored fields).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
