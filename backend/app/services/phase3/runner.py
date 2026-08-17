"""The one entry point: envelope in, release-ready output out.

``run`` executes the complete rewritten Phase 3 — Settle, Host, Assemble
— against a sealed envelope, with every decision cached in the store.
It returns the release material the existing publication chain consumes
(``stage_release(records=...)`` and the bulk-import writer): rows with
Types embedded in the house format, QIDs routed, coverage accounted,
and review flags carried through.

A run may fail (ContractError, fail-closed); it can never pause. On
failure the decision store still holds every decision already made, so
the caller's failure release ships them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import analyse as analyse_mod
from . import assemble as assemble_mod
from . import envelope as envelope_mod
from . import host as host_mod
from . import kernel
from . import place as place_mod
from . import polish as polish_mod
from . import settle as settle_mod


def _snapshot_settled_rows(
    env: Mapping[str, Any],
    settled: list[dict[str, Any]],
    store_dir: str | Path | None,
) -> None:
    """Persist the settled rows so a later failure release can ship them.

    Written beside the decision store in the job's durable artifact
    directory, in the same shape as the legacy validated-topology cache so
    the release upgrade path verifies it identically.
    """

    if not store_dir:
        return
    import json

    from .. import canonical_source_phase3 as phase3_core

    try:
        path = Path(store_dir).parent / "source.phase3-settled-rows.json"
        path.write_text(
            json.dumps(
                {
                    "records": settled,
                    "records_sha256": phase3_core._sha256_json(settled),
                    "source_contract_hash": str(
                        env.get("source_contract_hash") or ""
                    ),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # snapshotting is best-effort; the store already has decisions


def _snapshot_place(
    placements: Mapping[str, Any],
    store_dir: str | Path | None,
) -> None:
    """Persist the Phase 2.2 placement pass's recorded output.

    Written beside the decision store so the coverage ledger and the
    diagnostics export can account every pooled item — placements and
    recorded figure dispositions alike (R4) — long after the run.
    """

    if not store_dir:
        return
    import json

    try:
        path = Path(store_dir).parent / "source.phase3-place.json"
        path.write_text(
            json.dumps(dict(placements), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass  # snapshotting is best-effort; the store already has decisions


def _snapshot_analysis(
    analysis: Mapping[str, Any],
    store_dir: str | Path | None,
) -> None:
    """Persist the Phase 2.4/4.3 chapter analysis inventory (Q1).

    Written beside the decision store so the coverage ledger and the
    diagnostics export can account every LA-item — allotted to exactly
    one concept (R4) — long after the run.
    """

    if not store_dir:
        return
    import json

    try:
        path = Path(store_dir).parent / "source.phase3-analysis.json"
        path.write_text(
            json.dumps(dict(analysis), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass  # snapshotting is best-effort; the store already has decisions


def run(
    env: Mapping[str, Any],
    *,
    store_dir: str | Path | None = None,
    providers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Settle → Host → Place → Analyse → Polish → Assemble.

    ``providers`` is test-only injection ({"topology", "grounding",
    "analysis", "host", "place", "analyse", "analyse_allot", "critic",
    "fixer"} — "analysis" is Settle's content-authoring provider,
    "analyse" the Q1 chapter-inventory pass); production omits it and
    the passes use their live API adapters — including the live Fixer
    (Q13) — failing closed if no API is live.
    """

    from .. import progress

    env = envelope_mod.validate(env)
    store = kernel.DecisionStore(store_dir)
    injected = dict(providers or {})

    progress.step(
        "Phase 3 — Settle: topology, grounding, and content authoring",
        value=0.82,
    )
    settled = settle_mod.settle(
        env,
        topology_provider=injected.get("topology"),
        grounding_provider=injected.get("grounding"),
        analysis_provider=injected.get("analysis"),
        critic=injected.get("critic"),
        store=store,
        fixer=injected.get("fixer"),
    )
    _snapshot_settled_rows(env, settled, store_dir)
    progress.step(
        "Phase 3 — Host: certifying Type/Case and QID hosts", value=0.91
    )
    hosts = host_mod.host(
        env,
        settled,
        provider=injected.get("host"),
        critic=injected.get("critic"),
        store=store,
        fixer=injected.get("fixer"),
    )
    # Phase 2.2 (doc §4): pool Container-02 chapter-wide and let the model
    # place every activity, info hub, and unclaimed figure BEFORE Polish
    # converges content — the placements ride into Assemble, which stamps
    # them on the rows the deposit pipeline renders. An empty pool skips
    # the pass without a decision.
    progress.step(
        "Phase 3 — Place: pooling Container 02 for model placement",
        value=0.93,
    )
    placements = place_mod.place(
        env,
        [*settled, *(hosts.get("new_concepts") or [])],
        provider=injected.get("place"),
        critic=injected.get("critic"),
        store=store,
        fixer=injected.get("fixer"),
    )
    _snapshot_place(placements, store_dir)
    # Phase 2.4 + 4.3 (Q1): the chapter's misconception/error-analysis
    # inventory is built over chapter-wide evidence and each item is
    # allotted to exactly one concept. Assemble stamps the allotments;
    # the rendered section exists only where an item landed.
    progress.step(
        "Phase 3 — Analyse: chapter misconception/error-analysis "
        "inventory",
        value=0.935,
    )
    analysis = analyse_mod.analyse(
        env,
        [*settled, *(hosts.get("new_concepts") or [])],
        provider=injected.get("analyse"),
        allot_provider=injected.get("analyse_allot"),
        critic=injected.get("critic"),
        store=store,
        fixer=injected.get("fixer"),
    )
    _snapshot_analysis(analysis, store_dir)
    # Terminal content quality (generic analysis, verbatim Descriptions)
    # is converged BEFORE Assemble seals anything, on settled and
    # host-created rows alike; only failing rows cost a model call.
    progress.step(
        "Phase 3 — Polish: converging content on the terminal quality "
        "gate",
        value=0.94,
    )
    new_concepts = hosts.get("new_concepts") or []
    polished = polish_mod.polish(
        env,
        [*settled, *new_concepts],
        provider=injected.get("polish"),
        store=store,
        fixer=injected.get("fixer"),
    )
    settled = polished[:len(settled)]
    hosts = {**hosts, "new_concepts": polished[len(settled):]}
    _snapshot_settled_rows(env, settled, store_dir)
    progress.step(
        "Phase 3 — Assemble: embedding Types and routing QIDs "
        "(deterministic)",
        value=0.96,
    )
    assembled = assemble_mod.assemble(
        env, settled, hosts, placements, analysis
    )

    rows = assembled["rows"]
    flagged = sum(1 for row in rows if row.get("review_flags"))
    return {
        "records": rows,
        "host_map": hosts["host_map"],
        "qid_map": hosts["qid_map"],
        "new_concepts": hosts["new_concepts"],
        "analysis": analysis,
        "coverage": assembled["coverage"],
        "summary": {
            "row_count": len(rows),
            "flagged_row_count": flagged,
            "routed_qids": len(assembled["coverage"]["routed_qids"]),
            "unrouted_items": len(assembled["coverage"]["unrouted"]),
            "envelope_sha256": str(env.get("envelope_sha256") or ""),
        },
    }
