"""Pass 2.5 — Polish: converge row content on the terminal quality gate.

The terminal validators reject generic learner analysis, verbatim
source Descriptions, and truncated clauses — content-quality judgments
only the model can repair. This pass validates every row against those
exact codes BEFORE Assemble seals anything, sends only the failing rows
through the kernel (bounded corrections, decide-once store), and swaps
in the repaired ``concept_details``/``keywords`` alone: row identity,
topology, grounding and routing metadata are never touched.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from . import envelope as envelope_mod
from . import kernel
from .. import progress

_BATCH_SIZE = 8

# The subset of the deposit gate's fatal codes that are row-local content
# quality (repairable by rewriting concept_details alone).
CONTENT_CODES = {
    "verbatim_source_description",
    "generic_misconception",
    "generic_error_analysis",
    "description_truncated_clause",
}


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _failures(
    rows: list[Mapping[str, Any]], *, source_text: str,
) -> dict[int, list[dict[str, str]]]:
    from .. import concept_validator as cv

    # Measure the shape the gate actually judges: the terminal boundary
    # normalizes learner analysis BEFORE validating, and normalization
    # changes the verdict (4 failing rows raw vs 34 normalized, dress
    # rehearsals 5-6). Normalize first, then apply the gate's strict
    # analysis yardstick.
    report = cv.validate_concept_rows(
        cv.ensure_valid_learner_analysis([dict(row) for row in rows]),
        allow_culmination=True,
        strict_analysis_section=True,
        source_text=source_text,
    )
    failures: dict[int, list[dict[str, str]]] = {}
    for error in report.get("errors") or []:
        if (
            error.get("severity") == "error"
            and error.get("code") in CONTENT_CODES
        ):
            index = error.get("row_index", -1)
            if isinstance(index, int) and 0 <= index < len(rows):
                failures.setdefault(index, []).append({
                    "code": str(error.get("code") or ""),
                    "message": str(error.get("message") or ""),
                })
    return failures


def _checker(
    batch: list[dict[str, Any]],
    *,
    source_text: str,
):
    expected_refs = {row["row_ref"] for row in batch}
    originals = {row["row_ref"]: row for row in batch}

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("rows")
        if not isinstance(rows, list):
            return ["response has no rows array"]
        seen: dict[int, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a row entry is not an object")
                continue
            ref = row.get("row_ref")
            if ref not in expected_refs or ref in seen:
                defects.append(f"unknown or repeated row_ref {ref!r}")
                continue
            seen[ref] = row
            if not _normal(row.get("concept_details")).startswith(
                "Description:"
            ):
                defects.append(
                    f"row_ref {ref} concept_details must begin with "
                    "'Description: '"
                )
        missing = sorted(expected_refs - set(seen))
        if missing:
            defects.append(
                "unrepaired row_ref(s): "
                + ", ".join(str(ref) for ref in missing)
            )
        if defects:
            return defects
        candidates = []
        for ref, row in seen.items():
            candidate = dict(originals[ref]["row"])
            candidate["concept_details"] = str(
                row.get("concept_details") or ""
            )
            candidates.append((ref, candidate))
        remaining = _failures(
            [candidate for _ref, candidate in candidates],
            source_text=source_text,
        )
        for position, codes in remaining.items():
            ref = candidates[position][0]
            title = _normal(candidates[position][1].get("concept_title"))
            for code in codes:
                defects.append(
                    f"row_ref {ref} ({title[:50]}) still fails "
                    f"{code['code']}: {code['message']}"
                )
        return defects

    return check


def _live_polish(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.POLISH_SYSTEM,
        prompts.render(payload),
        purpose="concept_validation",
    )


def polish(
    env: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    provider: kernel.Provider | None = None,
    store: kernel.DecisionStore | None = None,
) -> list[dict[str, Any]]:
    """Return rows with every terminal content failure repaired in place."""

    env = envelope_mod.validate(env)
    text_by_id = {
        str(block.get("block_id") or ""): str(
            block.get("display_text") or ""
        )
        for block in env["canonical"]["blocks"]
        if isinstance(block, Mapping)
    }
    source_text = "\n".join(text for text in text_by_id.values() if text)
    # Work on the normalized form throughout: the model repairs the text
    # the gate will actually judge, and Assemble's own normalization pass
    # then finds nothing left to change.
    from .. import concept_validator as cv

    out = cv.ensure_valid_learner_analysis([dict(row) for row in rows])
    failures = _failures(out, source_text=source_text)
    if not failures:
        return out
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_polish
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    indexes = sorted(failures)
    progress.log(
        f"Polish: {len(indexes)} row(s) fall below the terminal content "
        "gate; repairing their content only "
        "(identity, topology, and routing stay untouched)."
    )

    for start in range(0, len(indexes), _BATCH_SIZE):
        batch_indexes = indexes[start:start + _BATCH_SIZE]
        batch = [
            {"row_ref": index, "row": out[index]}
            for index in batch_indexes
        ]
        payload = {
            "stage": "polish",
            "rules": (
                "Repair ONLY what each row's validation_errors name. "
                "Rewrite copied source prose as original teaching "
                "language, make Misconceptions name a concept-specific "
                "incorrect belief, make Error Analysis name the learner "
                "and a concrete faulty action, and complete truncated "
                "sentences. Keep every other section and its meaning "
                "exactly as it is; never rename the concept."
            ),
            "rows": [
                {
                    "row_ref": index,
                    "concept_title": out[index].get("concept_title"),
                    "parent_concept": out[index].get("parent_concept"),
                    "topic": out[index].get("topic"),
                    "concept_details": out[index].get("concept_details"),
                    "keywords": out[index].get("keywords"),
                    "validation_errors": failures[index],
                    "source_blocks": [
                        {
                            "block_id": block_id,
                            "text": text_by_id.get(block_id, "")[:800],
                        }
                        for block_id in (
                            out[index].get("_source_block_ids") or []
                        )
                    ],
                }
                for index in batch_indexes
            ],
        }
        decision = kernel.decide(
            kind="polish.rows",
            unit_id=f"rows#{start}",
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_checker(batch, source_text=source_text),
            store=store,
        )
        for row in decision["response"].get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            ref = row.get("row_ref")
            if ref not in set(batch_indexes):
                continue
            out[ref]["concept_details"] = str(
                row.get("concept_details") or ""
            )
            if _normal(row.get("keywords")):
                out[ref]["keywords"] = _normal(row.get("keywords"))
        flags = list(decision.get("review_flags") or [])
        for flag in flags:
            for index in batch_indexes:
                out[index]["review_flags"] = [
                    *(out[index].get("review_flags") or []), flag,
                ]
    progress.log(
        f"Polish: {len(indexes)} row(s) repaired; terminal content gate "
        "satisfied.",
        level="success",
    )
    return out
