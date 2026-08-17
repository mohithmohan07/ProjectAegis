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

# One decision PER ROW: batching couples unrelated rows through the
# bounded-correction loop (a row repaired on attempt 1 can regress on
# attempt 2 while a sibling converges — rehearsal 9 looped exactly this
# way), while an isolated row converges on the first attempt. Per-row
# decisions also replay individually from the store.
_BATCH_SIZE = 1

# The subset of the deposit gate's fatal codes that are row-local content
# quality (repairable by rewriting concept_details alone).
CONTENT_CODES = {
    "verbatim_source_description",
    "generic_misconception",
    "generic_error_analysis",
    "misconception_framing",
    "error_analysis_framing",
    "description_truncated_clause",
    # A row can reach the boundary with no (or malformed) learner
    # analysis: the old path papered that over with a deterministic
    # fallback the gate forbids; authoring real analysis is model work.
    # Either section alone is sufficient — only an analysis carrying
    # neither insight is missing content.
    "analysis_section_format",
    "missing_learner_analysis",
    # A row can reach the boundary with no mastery statement: the old
    # path backfilled a template line the gate forbids; authoring the
    # real capability statement is model work.
    "missing_mastery_statement",
    "mastery_statement_not_substantive",
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
    from .. import concept_refiner as _cr

    normalized_rows = cv.ensure_valid_learner_analysis(
        [dict(row) for row in rows]
    )
    for row in normalized_rows:
        # The terminal boundary normalizes mastery-line FORMAT before it
        # validates; measure the same shape so only genuinely missing or
        # non-substantive mastery (model work) reaches the repair pass.
        if not _cr.is_culmination(str(row.get("concept_title") or "")):
            row["concept_details"] = _cr.format_mastery_statement(
                str(row.get("concept_details") or "")
            )
    report = cv.validate_concept_rows(
        normalized_rows,
        allow_culmination=True,
        strict_analysis_section=True,
        strict_mastery_statement=True,
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
                entry = {
                    "code": str(error.get("code") or ""),
                    "message": str(error.get("message") or ""),
                }
                title = _normal(
                    dict(rows[index]).get("concept_title")
                )
                if entry["code"] in (
                    "generic_error_analysis", "error_analysis_framing",
                ):
                    # A concrete, filter-verified skeleton the model can
                    # adapt: actor + faulty action + 'instead of' contrast.
                    # Either-one contract: deleting the failing section is
                    # a legitimate repair when the other one is genuine.
                    entry["example_repair"] = (
                        f"Students may place {title} in the wrong "
                        "sequence instead of locating it at its actual "
                        "point in the chapter's chronology. — OR, if no "
                        "genuinely distinct procedural mistake exists "
                        "for this concept, DELETE the Error Analysis "
                        "part and keep only the Misconceptions "
                        "sentence; either section alone satisfies the "
                        "gate."
                    )
                elif entry["code"] in (
                    "generic_misconception", "misconception_framing",
                ):
                    entry["example_repair"] = (
                        "The learner may believe <state one specific "
                        f"wrong claim about {title}> — a belief "
                        "statement, not an action or a correction. — "
                        "OR, if no genuine wrong belief exists for "
                        "this concept, DELETE the Misconceptions part "
                        "and keep only the Error Analysis sentence; "
                        "either section alone satisfies the gate."
                    )
                elif entry["code"] in (
                    "missing_mastery_statement",
                    "mastery_statement_not_substantive",
                ):
                    entry["example_repair"] = (
                        "End the Description with one line-broken "
                        "'Achieving Mastery: <ONE substantive sentence "
                        "naming what a learner can DO once "
                        f"{title} is mastered>' — specific to this "
                        "concept, never a generic applying-it-correctly "
                        "template."
                    )
                elif entry["code"] in (
                    "analysis_section_format",
                    "missing_learner_analysis",
                ):
                    entry["example_repair"] = (
                        "End concept_details with exactly one section "
                        "'// Misconception/ Error Analysis: ' carrying "
                        "the genuine insight(s): 'Misconceptions: <one "
                        f"specific wrong belief about {title}>.' or "
                        "'Error Analysis: <the learner performing one "
                        "concrete faulty action, with an instead-of "
                        "contrast>.' — or both joined with '; ' ONLY "
                        "when they say genuinely different things; "
                        "never write one as a paraphrase of the other."
                    )
                failures.setdefault(index, []).append(entry)
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
            # Show the model (and the failure log) the exact text the
            # gate judged, post-normalization — the normalizer may have
            # replaced or dropped what the model wrote.
            from .. import concept_validator as cv

            judged = cv.ensure_valid_learner_analysis(
                [dict(candidates[position][1])]
            )[0]
            analysis_tail = str(judged.get("concept_details") or "")
            if "// Misconception/ Error Analysis:" in analysis_tail:
                analysis_tail = analysis_tail.split(
                    "// Misconception/ Error Analysis:", 1
                )[1]
            for code in codes:
                message = (
                    f"row_ref {ref} ({title[:50]}) still fails "
                    f"{code['code']}: {code['message']}; the gate judged "
                    f"this normalized analysis text: "
                    f"{analysis_tail.strip()[:300]!r}"
                )
                if code["code"] in (
                    "generic_error_analysis", "error_analysis_framing",
                ):
                    # Distinguish the normalizer's two silent kill paths:
                    # a shape rejection, and the overlap filter dropping
                    # an EA that restates the Misconception's content.
                    # Without naming the right one the model cannot
                    # converge (dress rehearsal 11: every truthful EA for
                    # one row overlapped its misconception and vanished).
                    from .. import concept_refiner as cr

                    raw_details = str(
                        seen[ref].get("concept_details") or ""
                    )
                    _misc, raw_ea = cr.analysis_components(
                        cr.normalize_analysis_sections(raw_details)
                    )
                    wrote_valid_ea = any(
                        cv.is_valid_error_analysis(stmt)
                        for stmt in cv._learner_analysis_statements(
                            raw_ea
                        )
                    )
                    if wrote_valid_ea:
                        message += (
                            "; your Error Analysis sentence was VALID "
                            "but was dropped by the overlap filter "
                            "because it restates the Misconceptions "
                            "sentence — write an Error Analysis about a "
                            "DIFFERENT concrete faulty action, sharing "
                            "as few words as possible with the "
                            "Misconceptions sentence (you may also "
                            "rephrase the Misconceptions sentence to "
                            "free up vocabulary)"
                        )
                    else:
                        message += (
                            "; your Error Analysis text was rejected by "
                            "the shape filter and replaced with a "
                            "forbidden fallback — write ONE sentence "
                            "where 'Students' or 'The learner' performs "
                            "a faulty ACTION (misapplies, misplaces, "
                            "reverses, swaps, omits, skips, mislabels, "
                            "misreads, 'fails to ...') with an 'instead "
                            "of'/'rather than' contrast; NEVER use "
                            "believe/think/assume/expect/interpret/"
                            "misunderstand/regard/consider/confuse/"
                            "mistake/treat as the verb and never write "
                            "'did not'/'does not' corrections. If no "
                            "genuinely distinct procedural mistake "
                            "exists for this concept, DELETE the Error "
                            "Analysis part and keep only the "
                            "Misconceptions sentence — either section "
                            "alone satisfies the gate"
                        )
                elif code["code"] in (
                    "misconception_framing", "generic_misconception",
                ):
                    # The framing filter accepts only sentences EXPLICITLY
                    # phrased as the learner's belief; a bare wrong
                    # proposition with identical content is rejected
                    # (production job 27 looped to fail-closed on this).
                    message += (
                        "; the Misconceptions sentence must be phrased "
                        "as the learner's belief — begin it 'The learner "
                        "may believe that ...' (or 'Students may think "
                        "that ...') and keep your same wrong claim as "
                        "the belief's content; a bare proposition, a "
                        "correction, or an action statement is rejected "
                        "by the framing filter"
                    )
                defects.append(message)
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
    fixer: kernel.Provider | None = None,
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
        from . import fixer as fixer_mod

        envelope_mod.require_live_api()
        provider = _live_polish
        fixer = fixer or fixer_mod.live_fixer
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
                "language and complete truncated sentences. "
                "Misconceptions have a REQUIRED SHAPE: a sentence "
                "explicitly phrased as the learner's belief — begin it "
                "'The learner may believe that ...' or 'Students may "
                "think that ...' with the concept-specific wrong claim "
                "as the belief's content; a bare wrong proposition, a "
                "correction, or an action statement is rejected. "
                "Error Analysis has a REQUIRED SHAPE: one sentence in "
                "which 'Students' or 'The learner' performs a concrete "
                "faulty ACTION (misapplies, misplaces, reverses, swaps, "
                "omits, skips, mislabels, misreads, 'fails to ...') "
                "combined with an 'instead of'/'rather than' contrast "
                "naming the correct action, e.g. 'Students misplace X "
                "at ... instead of ...'. NEVER use believe, think, "
                "assume, expect, interpret, misunderstand, regard, "
                "consider, confuse, mistake, or treat as the verb, and "
                "never write 'did not'/'does not' corrections — those "
                "shapes are rejected. The Error Analysis must also NOT "
                "restate the Misconceptions sentence: describe a "
                "different concrete faulty action sharing as few words "
                "as possible with it, or the overlap filter deletes "
                "your sentence. Keep every other section and its "
                "meaning exactly as it is; never rename the concept."
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
            # The gate codes ARE the contract: tightening them must mint
            # new decision keys, or a stored repair that predates a code
            # replays past the stricter checker (rehearsal 15: a
            # section-dropping repair replayed from the store).
            policy_version="content-codes:" + ",".join(
                sorted(CONTENT_CODES)
            ),
            fixer=fixer,
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
