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

import os
from pathlib import Path
from typing import Any, Mapping

from . import assemble as assemble_mod
from . import envelope as envelope_mod
from . import host as host_mod
from . import kernel
from . import settle as settle_mod


def rewrite_enabled() -> bool:
    """The production flag for routing post-81% work through this package."""

    return os.environ.get(
        "AEGIS_PHASE3_REWRITE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def run(
    env: Mapping[str, Any],
    *,
    store_dir: str | Path | None = None,
    providers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Settle → Host → Assemble. Returns rows, maps, and coverage.

    ``providers`` is test-only injection ({"topology", "grounding",
    "analysis", "host", "critic"}); production omits it and the passes
    use their live API adapters (failing closed if no API is live).
    """

    env = envelope_mod.validate(env)
    store = kernel.DecisionStore(store_dir)
    injected = dict(providers or {})

    settled = settle_mod.settle(
        env,
        topology_provider=injected.get("topology"),
        grounding_provider=injected.get("grounding"),
        analysis_provider=injected.get("analysis"),
        critic=injected.get("critic"),
        store=store,
    )
    hosts = host_mod.host(
        env,
        settled,
        provider=injected.get("host"),
        critic=injected.get("critic"),
        store=store,
    )
    assembled = assemble_mod.assemble(env, settled, hosts)

    rows = assembled["rows"]
    flagged = sum(1 for row in rows if row.get("review_flags"))
    return {
        "records": rows,
        "host_map": hosts["host_map"],
        "qid_map": hosts["qid_map"],
        "new_concepts": hosts["new_concepts"],
        "coverage": assembled["coverage"],
        "summary": {
            "row_count": len(rows),
            "flagged_row_count": flagged,
            "routed_qids": len(assembled["coverage"]["routed_qids"]),
            "unrouted_items": len(assembled["coverage"]["unrouted"]),
            "envelope_sha256": str(env.get("envelope_sha256") or ""),
        },
    }
