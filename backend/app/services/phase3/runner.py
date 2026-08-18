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
from . import preanalyse as preanalyse_mod
from . import prelearn as prelearn_mod
from . import premap as premap_mod
from . import prequestions as prequestions_mod
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


def _snapshot_prelearn(
    prerequisites: Mapping[str, Any],
    store_dir: str | Path | None,
) -> None:
    """Persist the Phase 03 running pre-requisite capture (doc §4, Q3).

    Written beside the decision store so the per-stage captures and the
    merged prerequisite set — every capture consolidated exactly once
    (R4) — survive the run for the Pre lane and the diagnostics export.
    """

    if not store_dir:
        return
    import json

    try:
        path = Path(store_dir).parent / "source.phase3-prelearn-capture.json"
        path.write_text(
            json.dumps(dict(prerequisites), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass  # snapshotting is best-effort; the store already has decisions


def _snapshot_premap(
    pre_map: Mapping[str, Any],
    store_dir: str | Path | None,
) -> None:
    """Persist the Phase 03 Pre-Learning concept map (doc §4, Q3).

    Written beside the decision store so the built Pre rows, their
    needed-for links, and their review flags survive the run for the
    later deposit/release slice and the diagnostics export.
    """

    if not store_dir:
        return
    import json

    try:
        path = Path(store_dir).parent / "source.phase3-prelearn-map.json"
        path.write_text(
            json.dumps(dict(pre_map), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass  # snapshotting is best-effort; the store already has decisions


def _snapshot_prequestions(
    pre_questions: Mapping[str, Any],
    store_dir: str | Path | None,
) -> None:
    """Persist the Phase 03 coverage plan and its generated questions (Q4).

    Written beside the decision store so each pre-concept's authored
    plan — its total, its split and the rationale EVERY plan carries —
    and the questions generated to it survive the run for the later
    assessment-lane slice and the diagnostics export.
    """

    if not store_dir:
        return
    import json

    try:
        path = Path(store_dir).parent / "source.phase3-prelearn-questions.json"
        path.write_text(
            json.dumps(dict(pre_questions), ensure_ascii=False, indent=1),
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
    "analysis", "host", "place", "analyse", "analyse_allot", "prelearn",
    "prelearn_merge", "premap", "premap_links", "preanalyse",
    "preanalyse_allot", "prequestions", "prequestions_author", "critic",
    "fixer"} — "analysis" is Settle's
    content-authoring provider, "analyse" the Q1 chapter-inventory pass,
    "prelearn" the Phase 03 per-stage pre-requisite capture, "premap" the
    Pre-Learning map build, "preanalyse" the Pre lane's own Q1
    inventory, "prequestions" the Q4 coverage plan and
    "prequestions_author" the generated questions authored to it);
    production omits it and the passes use their live API adapters —
    including the live Fixer (Q13) — failing closed if no API is live.

    The Phase 03 capture (doc §4, Q3) runs alongside the passes: one
    decision at each stage boundary over THAT stage's own evidence, then
    one merge. It rides out on the ``prerequisites`` key, the Pre map
    built from it on ``pre_map``, and the Q4 coverage plan with the
    questions generated to it on ``pre_questions``; ``records`` is
    untouched by all three. A Pre map that fails the no-extraction guard
    is refused and recorded on ``pre_map["refused"]`` — the finished Post
    map still ships, because a Pre-lane fault must not discard work that
    is already done and paid for (see the call site); the questions carry
    the same property one level down, on ``pre_questions["refused"]``.
    """

    from .. import progress

    env = envelope_mod.validate(env)
    store = kernel.DecisionStore(store_dir)
    injected = dict(providers or {})
    # Phase 03 (doc §4, Q3): the running pre-requisite capture. One
    # decision per stage boundary, each over the evidence THAT stage had
    # in hand — which is why it lives here and not inside any pass: the
    # runner is the only place that controls each payload, and no
    # existing pass's payload, checker, or policy_version is touched.
    captures: list[dict[str, Any]] = []

    def _capture(stage: str, **evidence: Any) -> None:
        captures.append(prelearn_mod.capture_stage(
            env,
            stage,
            provider=injected.get("prelearn"),
            critic=injected.get("critic"),
            store=store,
            fixer=injected.get("fixer"),
            **evidence,
        ))

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
    _capture("settle", settled=settled)
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
    _capture("host")
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
    _capture("place")
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
    _capture("analyse", analysis=analysis)
    # Phase 03 (Q3): the running capture is consolidated into one
    # prerequisite set. The model decides which captures are the same
    # prerequisite seen from two stages; the checker only refuses a
    # response that loses one (R4). This call is deliberately unwrapped:
    # it can only fail after the bounded corrections AND The Fixer both
    # failed, which is protocol impossibility, and swallowing it would
    # drop a learner's prerequisite silently (prelearn.py's docstring
    # records the choice).
    progress.step(
        "Phase 3 — Prerequisites: consolidating the running Phase 03 "
        "capture",
        value=0.937,
    )
    prerequisites = prelearn_mod.merge(
        env,
        captures,
        provider=injected.get("prelearn_merge") or injected.get("prelearn"),
        critic=injected.get("critic"),
        store=store,
        fixer=injected.get("fixer"),
    )
    _snapshot_prelearn(prerequisites, store_dir)
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
    # Phase 03 (doc §4): the consolidated capture becomes the complete
    # Pre-Learning concept map. It runs LAST because its needed-for links
    # read the finished Post Descriptions to link TO them — the map
    # itself is built from the capture and its source evidence, never
    # from the Post conclusions (the retired derive-from-existing flow,
    # Q3). The Post rows are handed over read-only.
    progress.step(
        "Phase 3 — Pre-Learning: building the Phase 03 concept map",
        value=0.97,
    )
    try:
        pre_map = premap_mod.build(
            env,
            prerequisites,
            rows,
            provider=injected.get("premap"),
            links_provider=injected.get("premap_links"),
            analysis_provider=injected.get("preanalyse"),
            analysis_allot_provider=injected.get("preanalyse_allot"),
            critic=injected.get("critic"),
            store=store,
            fixer=injected.get("fixer"),
        )
    except (
        premap_mod.PreExtractionError,
        preanalyse_mod.PreAnalysisError,
    ) as error:
        # Recorded as a deliberate decision, not left as an omission.
        #
        # The no-extraction guard fails closed on the ARTEFACT — a Pre
        # row carrying a source question's identity must never ship, and
        # ``premap.build`` refuses it. But by this line the Post map is
        # finished and sealed: Settle, Host, Place, Analyse, Polish and
        # Assemble have all completed and been paid for. Letting the
        # refusal propagate would discard every one of those rows, and
        # discard them PERMANENTLY — the offending decision is stored
        # content-addressed, so the replay reproduces the refusal at zero
        # spend with no route out. CLAUDE.md is explicit in the other
        # direction: "finished work always ships", and a run may be
        # stopped only at the pre-spend pauses or on genuine
        # impossibility, neither of which this is.
        #
        # So the Pre map is refused and the refusal is recorded — on the
        # returned map, in the snapshot, and in the run log — while the
        # finished Post map ships. Refusing loudly is the fail-closed
        # behaviour the steer asks for; taking the chapter down with it
        # is not. A provider that dies here still raises, exactly as it
        # does in every other pass (CLAUDE.md's "provider down" carve-out
        # covers that, and nothing is finished-and-discarded by it
        # that would not also be lost by a Post-lane provider failure).
        pre_map = {
            "rows": [],
            "topics": [],
            "needed_for": {},
            "analysis": {
                "inventory": [],
                "allotments": {},
                "rationales": {},
                "review_flags": {},
            },
            "review_flags": {},
            "decision_flags": {},
            "validation": [],
            "refused": str(error),
        }
        progress.log(
            "Pre-Learning map REFUSED and not shipped: " + str(error)
            + " The chapter's finished Post-Learning map is unaffected "
            "and ships; the Pre map must be rebuilt once the leak is "
            "corrected.",
            level="error",
        )
    _snapshot_premap(pre_map, store_dir)
    # Phase 03 (doc §4, Q4 per D3): the adaptive coverage plan and the
    # GENERATED questions authored to it. Its own try, deliberately not
    # the map's: a leak in the questions must not discard a finished Pre
    # map any more than a leak in the Pre map discards a finished Post
    # one. The map ships without questions, loudly, and the run
    # completes.
    progress.step(
        "Phase 3 — Pre-Learning: coverage plan and generated questions",
        value=0.975,
    )
    try:
        pre_questions = prequestions_mod.build(
            env,
            pre_map,
            provider=injected.get("prequestions"),
            author_provider=injected.get("prequestions_author"),
            critic=injected.get("critic"),
            store=store,
            fixer=injected.get("fixer"),
        )
    except (
        premap_mod.PreExtractionError,
        prequestions_mod.PreQuestionError,
    ) as error:
        pre_questions = {
            "plans": {},
            "questions": {},
            "blocked": {},
            "review_flags": {},
            "decision_flags": {},
            "refused": str(error),
        }
        progress.log(
            "Pre-Learning questions REFUSED and not shipped: " + str(error)
            + " The chapter's finished Post-Learning and Pre-Learning maps "
            "are unaffected and ship; the questions must be regenerated "
            "once the leak is corrected.",
            level="error",
        )
    _snapshot_prequestions(pre_questions, store_dir)
    flagged = sum(1 for row in rows if row.get("review_flags"))
    return {
        "records": rows,
        "host_map": hosts["host_map"],
        "qid_map": hosts["qid_map"],
        "new_concepts": hosts["new_concepts"],
        "analysis": analysis,
        "prerequisites": prerequisites,
        "pre_map": pre_map,
        "pre_questions": pre_questions,
        "coverage": assembled["coverage"],
        "summary": {
            "row_count": len(rows),
            "flagged_row_count": flagged,
            "routed_qids": len(assembled["coverage"]["routed_qids"]),
            "unrouted_items": len(assembled["coverage"]["unrouted"]),
            "envelope_sha256": str(env.get("envelope_sha256") or ""),
        },
    }
