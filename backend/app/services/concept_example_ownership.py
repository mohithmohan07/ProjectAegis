"""Unowned rendered Examples are adjudicated by a recorded verdict.

The closed-inventory scanner (``closed_inventory_contract``) detects
public Type Examples whose wording has no exact owner in the source
Question/Task Inventory. Until this module, that finding was only a log
line claiming "closed-world validation remains blocked" while nothing
actually blocked, recorded, or flagged — the rows shipped and the
finding evaporated, violating Q13/R4 (a detected defect becomes a
recorded, flagged decision; nothing is guessed silently).

Now the release stage adjudicates: one chapter-wide recorded decision
classifies each unowned Example — a re-worded rendering of a named
source task, a parser fragment of a source task's own wording, or a
genuinely unowned Example — and the verdicts land on the staged release
as an ``issues`` entry the reviewer sees. What each Example MEANS is the
model's judgment (Rule 1); the scan that found it and the checker that
audits the verdict's shape are mechanics. Nothing is dropped or
rewritten here: the reviewer acts through the ordinary revision loop.

The record rides the release's own ``issues`` ledger: ``stage_release``
calls ``adjudication_issue`` while assembling that ledger, exactly like
the staging-time QC audit — so every exit that stages rows (clean,
captured-failure, and both checkpoint exits) carries the record, and a
judge that fails still leaves the deterministic finding behind, marked
unadjudicated with the failure named. The helper never raises — a
recorder that can raise is the same defect one level up.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .phase3 import kernel

EXAMPLE_OWNERSHIP_POLICY_VERSION = "concept-example-ownership-1"

UNOWNED_EXAMPLES_ISSUE_CODE = "unowned_rendered_examples"

VERDICT_VOCABULARY = ("source_variant", "parser_fragment", "unowned")

EXAMPLE_OWNERSHIP_SYSTEM = (
    "You are the Aegis Example-ownership judge. The released concept "
    "rows carry public Type Examples, and every public Example must be "
    "a source task from the chapter's Question/Task Inventory. You are "
    "given the Examples whose wording has NO exact match in that "
    "inventory, together with every inventory task. For EACH such "
    "Example, judge what it is:\n"
    "- source_variant: a re-worded, trimmed or reformatted rendering of "
    "one inventory task (name that task's qid). Trimming a poem "
    "quotation to only the needed lines is a common legitimate cause.\n"
    "- parser_fragment: a piece of one inventory task's own wording "
    "(the task itself contains the literal word 'Example:'), split off "
    "by the flat format (name the task's qid).\n"
    "- unowned: it corresponds to no inventory task — invented, "
    "mangled, or copied from outside the inventory.\n"
    "Judge from the texts alone; never invent a qid, and choose "
    "'unowned' whenever no inventory task genuinely matches — that is "
    "a legitimate verdict, not a failure, and there is no quota either "
    "way. Respond with a single JSON object and nothing else:\n"
    '{"verdicts":[{"example_index":0,"verdict":"source_variant|'
    'parser_fragment|unowned","owner_qid":"QINV-0000 or empty",'
    '"reason":"evidence-bound reason"}],"confidence":0.0,'
    '"rationale":"overall reasoning"}'
)

EXAMPLE_OWNERSHIP_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis "
    "Example-ownership decision. Audit the proposed verdicts against "
    "the actual texts: is any 'source_variant' claim a stretch (the "
    "cited task asks something else), and is any 'unowned' verdict "
    "actually a plain re-wording of a listed task? Dissent must name "
    "the example_index and the qid at issue. Respond with a single "
    'JSON object: {"verdict":"concur|dissent","confidence":0.0,'
    '"issues":["..."]}'
)


def _live_ownership(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        EXAMPLE_OWNERSHIP_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_ownership_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        EXAMPLE_OWNERSHIP_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_validation",
    )


def _ownership_checker(
    example_count: int, inventory_qids: set[str],
) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        verdicts = response.get("verdicts")
        if not isinstance(verdicts, list):
            return ["verdicts must be an array"]
        defects: list[str] = []
        ruled: set[int] = set()
        for position, entry in enumerate(verdicts, start=1):
            if not isinstance(entry, Mapping):
                defects.append(f"verdict {position} is not an object")
                continue
            index = entry.get("example_index")
            if not isinstance(index, int) or not (
                0 <= index < example_count
            ):
                defects.append(
                    f"verdict {position}: example_index {index!r} is not "
                    f"one of the {example_count} unowned Example(s)"
                )
            elif index in ruled:
                defects.append(
                    f"verdict {position}: example {index} is ruled twice"
                )
            else:
                ruled.add(index)
            verdict = str(entry.get("verdict") or "")
            if verdict not in VERDICT_VOCABULARY:
                defects.append(
                    f"verdict {position}: {verdict!r} is not one of "
                    f"{VERDICT_VOCABULARY}"
                )
            owner = str(entry.get("owner_qid") or "").strip()
            if verdict in ("source_variant", "parser_fragment"):
                if owner not in inventory_qids:
                    defects.append(
                        f"verdict {position}: {verdict} must name an "
                        f"inventory qid; {owner!r} is not one"
                    )
            elif verdict == "unowned" and owner:
                defects.append(
                    f"verdict {position}: unowned must not claim a qid "
                    f"({owner!r})"
                )
            if not str(entry.get("reason") or "").strip():
                defects.append(f"verdict {position}: reason is empty")
        missing = set(range(example_count)) - ruled
        if missing:
            defects.append(
                "every unowned Example must be ruled exactly once; "
                f"missing example_index(es): {sorted(missing)!r}"
            )
        return defects

    return check


def decide_example_ownership(
    findings: list[Mapping[str, Any]],
    *,
    inventory: Mapping[str, Any],
    meta: Mapping[str, Any],
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One recorded chapter-wide verdict over the unowned Examples.

    Returns ``(verdicts, review_flags)`` — one verdict per finding, in
    finding order, each carrying the Example text it judged. An empty
    findings list costs nothing and returns nothing.
    """

    rows = [f for f in findings if isinstance(f, Mapping)]
    if not rows:
        return [], []

    tasks = _candidate_tasks(inventory)
    qids = {task["qid"] for task in tasks}

    if provider is None:
        provider = _live_ownership
        critic = critic if critic is not None else _live_ownership_critic
    store = store or kernel.DecisionStore()

    decision = kernel.decide(
        kind="concepts.example_ownership",
        unit_id="chapter",
        envelope_sha256=envelope_sha256,
        payload=_decision_payload(rows, tasks, meta),
        provider=provider,
        checker=_ownership_checker(len(rows), qids),
        critic=critic,
        store=store,
        policy_version=EXAMPLE_OWNERSHIP_POLICY_VERSION,
        fixer=fixer,
    )
    return (
        _verdicts_from_response(decision["response"], rows),
        list(decision.get("review_flags") or []),
    )


def _normalized_meta(meta: Mapping[str, Any] | None) -> dict[str, str]:
    """The judge's chapter context, normalized for decision-key stability.

    Every staging exit sources the identity a little differently (the
    captured checkpoint, the live job property, an empty dict); this
    projection makes them all produce byte-identical payload metadata so
    the decide-once replay actually hits across exits. The field
    vocabulary is the models property's own — one tuple, no drift.
    """

    from .. import models

    return {
        field: str((meta or {}).get(field) or "")
        for field in models.CHECKPOINT_TARGET_IDENTITY_FIELDS
    }


def _candidate_tasks(
    inventory: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    from . import generation

    # Hub-kind items (Activities / Info Hubs) are excluded from the
    # candidate owners exactly as the scanner excludes them from the
    # expected owner keys: the closed-world contract says a hub item can
    # never own a public Example, so the judge must not be offered one —
    # and the checker's qid set rejects a hub qid outright.
    hub_kinds = getattr(generation, "_HUB_INVENTORY_KINDS", frozenset())
    return [
        {
            "qid": str(item.get("qid") or ""),
            "task_text": generation._inventory_task_text(item),
        }
        for item in (inventory or {}).get("items") or []
        if isinstance(item, Mapping) and str(item.get("qid") or "")
        and str(item.get("source_kind") or "").strip().lower()
        not in hub_kinds
    ]


def _decision_payload(
    rows: list[Mapping[str, Any]],
    tasks: list[dict[str, str]],
    meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    # The payload is the decision's identity: the live decide and the
    # interactive store replay MUST build it through this one function
    # or the replay silently misses and re-pays.
    return {
        "stage": "concepts.example_ownership",
        "rules": EXAMPLE_OWNERSHIP_SYSTEM,
        "metadata": _normalized_meta(meta),
        "unowned_examples": [
            {
                "example_index": index,
                "example_text": str(
                    row.get("example_text") or row.get("example") or ""
                ),
                "detected_reason": str(row.get("reason") or ""),
            }
            for index, row in enumerate(rows)
        ],
        "inventory_tasks": tasks,
    }


def _verdicts_from_response(
    response: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_index = {
        int(entry.get("example_index")): entry
        for entry in response.get("verdicts") or []
        if isinstance(entry, Mapping)
        and isinstance(entry.get("example_index"), int)
    }
    verdicts = []
    for index, row in enumerate(rows):
        entry = by_index.get(index) or {}
        verdicts.append({
            "example_index": index,
            "example_text": str(
                row.get("example_text") or row.get("example") or ""
            ),
            "detected_reason": str(row.get("reason") or ""),
            "verdict": str(entry.get("verdict") or ""),
            "owner_qid": str(entry.get("owner_qid") or "").strip(),
            "reason": str(entry.get("reason") or ""),
        })
    return verdicts


def replay_example_ownership(
    findings: list[Mapping[str, Any]],
    *,
    inventory: Mapping[str, Any] | None,
    meta: Mapping[str, Any] | None,
    envelope_sha256: str,
    store: kernel.DecisionStore | None,
) -> tuple[list[dict[str, Any]], list[str]] | None:
    """A pure store probe: the recorded verdict, or None — never a call.

    Lets the interactive release route (``allow_live=False``) surface an
    already-paid adjudication instead of downgrading the newest staged
    release to an empty unadjudicated record.
    """

    if store is None:
        return None
    rows = [f for f in findings if isinstance(f, Mapping)]
    if not rows:
        return None
    tasks = _candidate_tasks(inventory)
    decision = kernel.peek(
        kind="concepts.example_ownership",
        unit_id="chapter",
        envelope_sha256=envelope_sha256,
        payload=_decision_payload(rows, tasks, meta),
        store=store,
        policy_version=EXAMPLE_OWNERSHIP_POLICY_VERSION,
    )
    if not isinstance(decision, Mapping):
        return None
    response = decision.get("response")
    if not isinstance(response, Mapping):
        return None
    return (
        _verdicts_from_response(response, rows),
        list(decision.get("review_flags") or []),
    )


def build_issue(
    verdicts: list[Mapping[str, Any]],
    review_flags: list[str],
    *,
    adjudicated: bool,
    durable_store: bool = True,
) -> dict[str, Any]:
    """The release ``issues`` entry carrying the recorded verdicts."""

    from . import build_concepts_release as release

    unowned = [
        v for v in verdicts if str(v.get("verdict") or "") == "unowned"
    ]
    if adjudicated:
        message = (
            f"{len(verdicts)} public Example(s) had no exact owner in the "
            "source Question/Task Inventory; each was adjudicated and "
            f"recorded — {len(unowned)} ruled genuinely unowned. Review "
            "the verdicts in this issue's details before accepting the "
            "chapter."
        )
    else:
        message = (
            f"{len(verdicts)} public Example(s) have no exact owner in "
            "the source Question/Task Inventory and could not be "
            "adjudicated on this run. Review each against the source "
            "before accepting the chapter."
        )
    # Deliberately NO ``qids`` anchor: the release annotates rows as
    # errored by qid intersection, and the only qids a verdict can name
    # belong to Examples ruled LEGITIMATE (source_variant /
    # parser_fragment) — anchoring on them would stamp exactly the wrong
    # rows while the genuinely unowned Examples, which name no qid, mark
    # nothing. This is a chapter-level record; the verdicts in the
    # details say which Example is which, owner qids included.
    return release._issue(
        code=UNOWNED_EXAMPLES_ISSUE_CODE,
        severity="warning",
        phase="concepts_release",
        message=message,
        details={
            "adjudicated": adjudicated,
            # False when the job's artifact directory was unavailable, so
            # the verdict lived only in memory: a re-stage will re-decide
            # (re-spend) instead of replaying — recorded, never silent.
            "durable_store": durable_store,
            "policy_version": EXAMPLE_OWNERSHIP_POLICY_VERSION,
            "verdicts": [copy.deepcopy(dict(v)) for v in verdicts],
            "owner_qids": sorted({
                str(v.get("owner_qid") or "")
                for v in verdicts
                if str(v.get("owner_qid") or "")
            }),
            "review_flags": list(review_flags),
        },
    )


def adjudication_issue(
    records: list[Mapping[str, Any]],
    inventory: Mapping[str, Any] | None,
    *,
    meta: Mapping[str, Any],
    job_id: int,
    allow_live: bool = True,
) -> dict[str, Any] | None:
    """Scan the staging rows and return the issue to stage — never raise.

    Called by ``stage_release`` while it assembles the release's own
    ``issues`` ledger (exactly like the staging-time QC audit), so every
    exit that stages rows carries the record. An empty scan spends
    nothing and returns None. A judge that FAILS — provider error, quota,
    contract exhaustion — still returns the deterministic finding, marked
    ``adjudicated: false`` with the failure named: the finding never
    evaporates because the judge did (R4). The decision is decide-once in
    the job's durable store, so a re-stage replays the verdict for free.

    ``allow_live=False`` records the finding WITHOUT any model call —
    for interactive staging routes (``force_release``) that answer a
    plain HTTP request and must not block on provider latency. The
    deterministic record still ships; only the adjudication is skipped.
    """

    try:
        from . import canonical_source_phase3 as phase3_core
        from . import generation, progress, release_refiner
        from . import grounding_certificate as gc
        from .phase3 import fixer as p3_fixer

        findings = generation._unexpected_rendered_type_examples(
            [dict(r) for r in records if isinstance(r, Mapping)],
            dict(inventory or {}),
        )
        if not findings:
            return None

        def _unadjudicated(note: str) -> list[dict[str, Any]]:
            return [
                {
                    "example_index": index,
                    "example_text": str(
                        row.get("example_text") or row.get("example") or ""
                    ),
                    "detected_reason": str(row.get("reason") or ""),
                    "verdict": "",
                    "owner_qid": "",
                    "reason": note,
                }
                for index, row in enumerate(findings)
            ]

        envelope_sha = next(
            (
                str(row.get(gc.SOURCE_CONTRACT_FIELD) or "").strip()
                for row in records
                if isinstance(row, Mapping) and str(
                    row.get(gc.SOURCE_CONTRACT_FIELD) or ""
                ).strip()
            ),
            "",
        )
        store = release_refiner.decision_store_for_job(int(job_id))
        durable = store is not None

        verdicts: list[dict[str, Any]]
        flags: list[str] = []
        adjudicated = False

        # A re-stage of the SAME rows and inventory replays the recorded
        # verdict from the decide-once store at zero cost — on every
        # route, the interactive one included. Inputs that drifted since
        # the verdict (a newer checkpoint's inventory snapshot, an edited
        # row) legitimately re-key: the probe misses and the route falls
        # through to its own recording below.
        replayed = replay_example_ownership(
            findings,
            inventory=dict(inventory or {}),
            meta=meta,
            envelope_sha256=envelope_sha,
            store=store,
        )
        if replayed is not None:
            verdicts, flags = replayed
            adjudicated = True
        elif not _candidate_tasks(inventory):
            # No candidate owners at all: adjudicating against an empty
            # task list is a foregone conclusion and the empty inventory
            # itself is the anomaly worth reading — record without
            # spending a call.
            verdicts = _unadjudicated(
                "no candidate owners: the resolved inventory holds no "
                "task items"
            )
        elif not allow_live:
            verdicts = _unadjudicated(
                "recorded without live adjudication (interactive "
                "release route)"
            )
        elif phase3_core.semantic_api_enabled():
            try:
                if not durable:
                    progress.log(
                        "Example ownership: the job's artifact directory "
                        "is unavailable, so this verdict cannot be stored "
                        "durably — a later re-stage will decide (and "
                        "spend) again.",
                        level="warning",
                    )
                # Spoken BEFORE the calls: the run's progress bar may
                # already read Done, and this is where the wait is.
                progress.log(
                    f"Example ownership: adjudicating {len(findings)} "
                    "public Example(s) without an exact inventory owner "
                    "before the release is staged…",
                )
                verdicts, flags = decide_example_ownership(
                    findings,
                    inventory=dict(inventory or {}),
                    meta=meta,
                    envelope_sha256=envelope_sha,
                    store=store,
                    fixer=p3_fixer.default_provider(),
                )
                adjudicated = True
            except Exception as exc:  # noqa: BLE001
                # The judge failing is never a reason to lose the
                # finding: record it unadjudicated, failure named.
                verdicts = _unadjudicated(
                    "live adjudication failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                flags = []
        else:
            verdicts = _unadjudicated(
                "live adjudication unavailable on this run"
            )

        issue = build_issue(
            verdicts, flags, adjudicated=adjudicated, durable_store=durable,
        )
        unowned = sum(
            1 for v in verdicts if str(v.get("verdict") or "") == "unowned"
        )
        progress.log(
            f"Example ownership: {len(verdicts)} public Example(s) without "
            "an exact inventory owner were adjudicated and recorded on the "
            f"release ({unowned} ruled genuinely unowned)."
            if adjudicated else
            f"Example ownership: {len(verdicts)} public Example(s) without "
            "an exact inventory owner were recorded on the release for "
            "review (not adjudicated on this run).",
            level="warning",
        )
        return issue
    except Exception as exc:  # pragma: no cover - must never block staging
        try:
            from . import progress

            progress.log(
                "Example ownership scan failed and recorded nothing: "
                f"{type(exc).__name__}: {exc}",
                level="warning",
            )
        except Exception:
            pass
        return None
