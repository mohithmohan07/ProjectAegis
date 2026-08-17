"""The Refiner — finishes the rendered output (docs/aegis-restructure.md §8.3).

After assembly and before staging, the Refiner reads the RENDERED release
rows — the row text exactly as the released workbook will carry it — and
refines them to expectation: wording polish, grade-level consistency,
formatting hygiene, description quality. The output, not the process: it
never re-runs a phase and never revisits a pipeline decision.

Identities are untouchable. The editable-field whitelist (``concept_details``
prose — the Description text, the Misconception/ Error Analysis wording, the
mastery sentence wording — and ``keywords``) is mechanics-enforced after every
decision by re-parsing the refined ``concept_details`` and requiring the
section labels, every non-editable section (Types, Activity/Info Hub), the
Type/Case/Example token structure, and every QID string to be byte-identical;
a violating refinement is DISCARDED with a review flag, never applied. Every
refinement is recorded as a diff on the release, the terminal validator is
re-run mechanically afterwards (a refinement that introduces a NEW error is
rolled back per row), and the independent critic's dissent flags (advisory,
Q10 — it never gates). The Refiner must NEVER block a release: on any
failure — provider down, invalid response, re-sealing failure — the caller
stages the UNREFINED rows with a "refiner unavailable" flag.

Name note: this module is distinct from ``app/services/concept_refiner.py``,
the deterministic pre-deposit FORMATTER (numbering, section shape). This
module is §8.3's model-driven pre-stage output Refiner.

Scope exclusions this round (later steps of docs/aegis-restructure.md §10):

* the MES/assessment lane rendered outputs (Concept/Master files);
* group-description quality (Output 02 groups arrive in step 6);
* the pre-learning outputs (step 7);
* the DB-deposit (non-release) path.

The seam accepts an ``output_kind`` parameter so those lanes join without
redesign; only ``"concepts_release"`` is implemented this round.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping, Sequence

from .phase3 import kernel

REFINER_POLICY_VERSION = "refiner-1"

# One decision PER ROW (polish.py precedent): an isolated row converges on
# the first attempt and replays individually from the decision store.
_BATCH_SIZE = 1

# The mechanics-enforced editable whitelist. Everything else on a row —
# titles, topics, placements, audit fields, seals, QIDs, Types — is identity
# and is never swapped, whatever the model returns.
EDITABLE_FIELDS = ("concept_details", "keywords")

_QID_RE = re.compile(r"\bQINV-[A-Za-z0-9_.-]+\b")


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _structure_token_re():
    from . import concept_refiner as cr

    return re.compile(
        "|".join(
            (
                cr._TYPE_TOKEN_RE.pattern,
                cr._CASE_TOKEN_RE.pattern,
                cr._EXAMPLE_TOKEN_RE.pattern,
            )
        ),
        re.IGNORECASE,
    )


def _structure_tokens(details: str) -> list[str]:
    """Ordered, whitespace-normalized Type/Case/Example tokens of one row."""

    return [
        re.sub(r"\s+", " ", token)
        for token in _structure_token_re().findall(str(details or ""))
    ]


def _is_editable_label(label: str) -> bool:
    from . import concept_refiner as cr

    key = str(label or "").strip().casefold()
    return key.startswith("description") or cr.is_learner_analysis_label(label)


def _identity_violations(
    before_row: Mapping[str, Any],
    after_details: str,
    after_keywords: str,
) -> list[str]:
    """Mechanical identity check over one refined row. Not a judgment.

    The Refiner may polish prose inside the Description and learner-analysis
    sections and rewrite keywords; every other part of ``concept_details`` —
    section labels and order, the Types section, Activity/Info Hub, the
    Type/Case/Example token structure, every QID string — must survive
    byte-identical.
    """

    from . import concept_refiner as cr

    before_details = str(before_row.get("concept_details") or "")
    violations: list[str] = []
    if not str(after_details or "").strip():
        violations.append("concept_details was emptied")
        return violations
    before_sections = cr.split_sections(before_details)
    after_sections = cr.split_sections(after_details)
    if [label for label, _ in before_sections] != [
        label for label, _ in after_sections
    ]:
        violations.append("section labels or section order changed")
    else:
        for (label, before_content), (_label, after_content) in zip(
            before_sections, after_sections
        ):
            if _is_editable_label(label):
                continue
            if before_content != after_content:
                violations.append(
                    f"non-editable section {label!r} changed"
                )
    if _structure_tokens(before_details) != _structure_tokens(after_details):
        violations.append("Type/Case/Example structure changed")
    before_qids = sorted(
        _QID_RE.findall(
            before_details + "\n" + str(before_row.get("keywords") or "")
        )
    )
    after_qids = sorted(
        _QID_RE.findall(str(after_details) + "\n" + str(after_keywords or ""))
    )
    if before_qids != after_qids:
        violations.append("QID strings changed")
    return violations


def _pre_post(metadata: Mapping[str, Any]) -> str:
    value = str(metadata.get("pre_post") or "Post").strip().lower()
    return "Pre" if value.startswith("pre") else "Post"


def _transient_projection(
    rows: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    """Render each row the way the released workbook will carry it.

    Reuses the transient-model renderer precedent
    (build_concepts_release_files.build_release_bulk_import_workbook):
    throwaway Chapter/Topic/Concept copies, negative ids, nothing written to
    any file and nothing touching the DB.
    """

    from .. import models
    from ..bulk_import import writer as bi_writer

    chapter = models.Chapter(
        chapter_code=str(metadata.get("chapter_code") or ""),
        board=str(metadata.get("board") or ""),
        grade=str(metadata.get("grade") or ""),
        subject=str(metadata.get("subject") or ""),
        unit=str(metadata.get("unit") or ""),
        chapter_title=str(metadata.get("chapter_title") or ""),
        chapter_display_name=str(metadata.get("chapter_title") or ""),
        chapter_description="",
        chapter_duration="",
        pre_topics="",
        post_topics="",
    )
    chapter.id = -1
    pre_post = _pre_post(metadata)
    topics_by_key: dict[str, models.Topic] = {}
    concepts: list[models.Concept] = []
    entries: dict[int, tuple[models.Concept, models.Topic]] = {}
    counter = 0
    for index, record in enumerate(rows):
        topic_title = str(record.get("topic") or "").strip()
        title = str(
            record.get("concept_title") or record.get("concept") or ""
        ).strip()
        if not topic_title or not title:
            continue
        counter += 1
        key = topic_title.casefold()
        topic = topics_by_key.get(key)
        if topic is None:
            topic = models.Topic(
                topic_title=topic_title,
                pre_post_learning=pre_post,
            )
            topic.id = -(len(topics_by_key) + 1)
            topic.source_order = len(topics_by_key) + 1
            topic.chapter = chapter
            topic.chapter_id = chapter.id
            topics_by_key[key] = topic
        concept = models.Concept(
            concept_title=title,
            concept_display_name=title,
            parent_concept=str(record.get("parent_concept") or ""),
            concept_details=str(
                record.get("concept_details")
                or record.get("concept_description")
                or ""
            ),
            keywords=str(record.get("keywords") or ""),
            sources=str(metadata.get("source_book") or ""),
        )
        concept.id = -counter
        concept.source_order = counter
        concept.topic = topic
        concepts.append(concept)
        entries[index] = (concept, topic)

    export_scope = bi_writer.ConceptExportScope(concepts)
    projection: dict[int, dict[str, Any]] = {}
    for index, (concept, topic) in entries.items():
        placement_key = bi_writer.concept_placement_key(concept, topic)
        projection[index] = {
            "placement_key": tuple(placement_key),
            "unit_id": json.dumps(list(placement_key), ensure_ascii=False),
            "topic_cell": bi_writer.composed_topic_title(topic, export_scope),
            "concept_title_cell": bi_writer._concept_field_value(
                concept,
                topic,
                "concept_title",
                include_group_columns=False,
                parent_column_present=True,
            ),
        }
    return projection


def _deposit_deterministic_pipeline(
    rows: list[dict[str, Any]],
    metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The exact deterministic chain assemble seals against (assemble.py).

    Refined rows must remain deposit-stable, so their normalized form is
    adopted; nothing here judges content.
    """

    from . import concept_cleanup
    from . import concept_refiner
    from . import concept_validator as cv
    from . import generation

    inventory = dict(metadata.get("inventory") or {})
    mined_types = dict(metadata.get("mined_types") or {})
    out_rows = [
        concept_cleanup.clean_concept_record(dict(row)) for row in rows
    ]
    out_rows = concept_cleanup.filter_review_violations(
        out_rows,
        subject=str(metadata.get("subject") or ""),
        board=str(metadata.get("board") or ""),
        chapter_title=str(metadata.get("chapter_title") or ""),
    )
    out_rows = concept_refiner.refine_chapter(out_rows)
    out_rows = cv.ensure_valid_learner_analysis(out_rows)
    out_rows = generation._ensure_mastery_lines_via_api(
        out_rows, meta={}, use_api=False
    )
    out_rows = generation._ensure_terminal_culmination_contract(out_rows)
    out_rows = generation._canonicalize_concept_rich_text(out_rows)
    out_rows = generation._normalize_activity_hubs_from_inventory(
        out_rows, inventory, mined_types
    )
    out_rows = generation._enforce_rendered_inventory_coverage(
        out_rows, inventory, mined_types
    )
    out_rows = generation._canonicalize_concept_rich_text(out_rows)
    out_rows = concept_refiner.renumber_types_continuously(out_rows)
    out_rows = cv.ensure_valid_learner_analysis(out_rows)
    return out_rows


def _terminal_error_keys(
    rows: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> dict[int, set[tuple[str, str]]]:
    """Terminal-gate error identities per row (mechanical re-check)."""

    from . import concept_validator as cv
    from . import generation

    pre_post = _pre_post(metadata)
    inventory = dict(metadata.get("inventory") or {})
    source_text = str(metadata.get("source_text") or "")
    report = cv.validate_concept_rows(
        [dict(row) for row in rows],
        allow_types=True,
        require_culmination=pre_post == "Post",
        allow_culmination=True,
        allowed_source_examples=generation._inventory_source_examples(
            inventory
        ),
        strict_type_hierarchy=pre_post == "Post",
        strict_analysis_section=pre_post == "Post",
        strict_mastery_statement=pre_post == "Post",
        source_text=source_text if pre_post == "Post" else "",
    )
    keys: dict[int, set[tuple[str, str]]] = {}
    for error in report.get("errors") or []:
        if str(error.get("severity") or "") != "error":
            continue
        index = error.get("row_index")
        if isinstance(index, int) and 0 <= index < len(rows):
            keys.setdefault(index, set()).add(
                (
                    str(error.get("field") or ""),
                    str(error.get("code") or ""),
                )
            )
    return keys


def _records_sealed(rows: Sequence[Mapping[str, Any]]) -> bool:
    from . import grounding_certificate as gc

    return bool(rows) and all(
        str(row.get(gc.ROW_CERTIFICATE_FIELD) or "").strip() for row in rows
    )


def _reseal(rows: list[dict[str, Any]]) -> None:
    """Re-seal the complete refined row set (map §7.1).

    Raises on any inconsistency — the caller falls back to the unrefined
    rows with a flag; the Refiner never ships a half-sealed payload.
    """

    from . import grounding_certificate as gc
    from .build_concepts_release import _record_blocks

    source_contracts = {
        str(row.get(gc.SOURCE_CONTRACT_FIELD) or "").strip()
        for row in rows
    } - {""}
    topologies = {
        str(row.get(gc.SEMANTIC_TOPOLOGY_FIELD) or "").strip()
        for row in rows
    } - {""}
    if len(source_contracts) != 1 or len(topologies) != 1:
        raise ValueError(
            "sealed release rows carry inconsistent source-contract or "
            "semantic-topology attestations"
        )
    allowed: set[str] = set()
    for row in rows:
        for value in row.get("_source_block_ids") or []:
            if str(value).strip():
                allowed.add(str(value).strip())
        for value in row.get(gc.EVIDENCE_SET_FIELD) or []:
            if str(value).strip():
                allowed.add(str(value).strip())
        allowed.update(_record_blocks(row))
    gc.seal_records(
        rows,
        source_contract_hash=next(iter(source_contracts)),
        semantic_topology_sha256=next(iter(topologies)),
        allowed_block_ids=allowed,
    )


def _live_refine(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        prompts.REFINER_SYSTEM,
        prompts.render(payload),
        purpose="concept_validation",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        prompts.CRITIC_SYSTEM,
        prompts.render(payload),
        purpose="concept_validation",
    )


def _default_provider() -> kernel.Provider | None:
    """Live when the semantic API is live; ``None`` otherwise.

    The Refiner never fails closed: without a live API the caller stages
    the unrefined rows with the availability flag.
    """

    from . import canonical_source_phase3 as phase3_core

    return _live_refine if phase3_core.semantic_api_enabled() else None


def _default_critic() -> kernel.Critic | None:
    from . import canonical_source_phase3 as phase3_core

    return _live_critic if phase3_core.semantic_api_enabled() else None


def decision_store_for_job(job_id: int) -> kernel.DecisionStore | None:
    """The run's durable decision store, when its artifact directory exists."""

    from pathlib import Path

    from . import uploads

    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper):
        return None
    try:
        directory = Path(helper(int(job_id)))
    except Exception:  # noqa: BLE001 - an unreachable store never blocks
        return None
    if not directory.is_dir():
        return None
    return kernel.DecisionStore(directory / "phase3-decisions")


_RULES = (
    "Refine this one released concept row to expectation: wording polish, "
    "grade-level consistency for the stated board/grade/subject, formatting "
    "hygiene, and description quality. Polish WITHIN the row's identity: "
    "never rename the concept or its topic, never move, add or remove "
    "content sections, never alter the Types section, any 'Type NN:', "
    "'Case NN:' or 'Example:' text, the Activity/Info Hub section, or any "
    "QINV- id. You may reword ONLY the Description prose (including its "
    "'Achieving Mastery:' sentence), the Misconception/ Error Analysis "
    "wording, and the keywords. Keep every section label and the section "
    "order byte-identical, keep all factual content and [Katex] wrapping, "
    "and keep concept_details beginning with 'Description: '. Return the "
    "row unchanged when it already reads at expectation."
)


def _instruction_suffix(instruction_set: Mapping[str, Any] | None) -> str:
    """The Architect's authored slots, appended to the rules (§8.1)."""

    from .phase3 import prompts as p3_prompts

    slots = (
        instruction_set.get("slots")
        if isinstance(instruction_set, Mapping)
        else None
    )
    if not isinstance(slots, Mapping):
        return ""
    return p3_prompts.instruction_rules_suffix(
        {"metadata": {"instruction_slots": dict(slots)}}
    )


def _checker(unit_id: str):
    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("rows")
        if not isinstance(rows, list) or not rows:
            return ["response has no rows array"]
        seen = 0
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a row entry is not an object")
                continue
            if row.get("row_ref") != unit_id:
                defects.append(
                    f"unknown row_ref {row.get('row_ref')!r}; echo the "
                    "requested row_ref exactly"
                )
                continue
            seen += 1
            if seen > 1:
                defects.append(f"row_ref {unit_id} repeated")
                continue
            details = row.get("concept_details")
            if not isinstance(details, str) or not _normal(details).startswith(
                "Description:"
            ):
                defects.append(
                    "concept_details must be a string beginning with "
                    "'Description: '"
                )
            keywords = row.get("keywords")
            if keywords is not None and not isinstance(keywords, str):
                defects.append("keywords must be a string when present")
        if not seen:
            defects.append(f"missing row_ref {unit_id}")
        return defects

    return check


def _empty_diff(*, output_kind: str, summary: str) -> dict[str, Any]:
    return {
        "policy_version": REFINER_POLICY_VERSION,
        "output_kind": output_kind,
        "changes": [],
        "summary": summary,
        "resealed_after_refinement": False,
    }


def _fallback(
    original: list[dict[str, Any]],
    reason: str,
    *,
    output_kind: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    flag = f"refiner unavailable: {_normal(reason)}"
    return (
        copy.deepcopy(original),
        _empty_diff(output_kind=output_kind, summary=flag),
        [flag],
    )


def refine_release(
    records: Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any],
    instruction_set: Mapping[str, Any] | None = None,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    output_kind: str = "concepts_release",
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Refine the captured release rows. Never raises, never blocks.

    Returns ``(refined_records, diff, review_flags)``. On any failure the
    UNREFINED records come back with a ``refiner unavailable`` flag; the
    caller stages whatever this function returns.
    """

    original = [
        copy.deepcopy(dict(row))
        for row in records
        if isinstance(row, Mapping)
    ]
    try:
        return _refine(
            original,
            metadata=metadata,
            instruction_set=instruction_set,
            provider=provider,
            critic=critic,
            store=store,
            output_kind=output_kind,
        )
    except Exception as exc:  # noqa: BLE001 - the Refiner never blocks
        return _fallback(
            original,
            f"{type(exc).__name__}: {exc}",
            output_kind=output_kind,
        )


def _refine(
    original: list[dict[str, Any]],
    *,
    metadata: Mapping[str, Any],
    instruction_set: Mapping[str, Any] | None,
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    store: kernel.DecisionStore | None,
    output_kind: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    from . import grounding_certificate as gc
    from . import progress

    if output_kind != "concepts_release":
        # Later steps of §10 route the other rendered outputs through this
        # same seam; refusing to guess about them is mechanics, not judgment.
        return _fallback(
            original,
            f"output kind {output_kind!r} is a later restructure step",
            output_kind=output_kind,
        )
    if not original:
        return (
            [],
            _empty_diff(
                output_kind=output_kind, summary="no released rows to refine"
            ),
            [],
        )
    if provider is None:
        provider = _default_provider()
        if provider is None:
            return _fallback(
                original,
                "live semantic API is not enabled",
                output_kind=output_kind,
            )
        critic = critic if critic is not None else _default_critic()
    store = store or kernel.DecisionStore()

    projection = _transient_projection(original, metadata)
    if not projection:
        return (
            copy.deepcopy(original),
            _empty_diff(
                output_kind=output_kind,
                summary="no renderable rows to refine",
            ),
            [],
        )
    envelope_sha = next(
        (
            str(row.get(gc.SOURCE_CONTRACT_FIELD) or "").strip()
            for row in original
            if str(row.get(gc.SOURCE_CONTRACT_FIELD) or "").strip()
        ),
        "",
    )
    rules = _RULES + _instruction_suffix(instruction_set)
    meta_block = {
        key: str(metadata.get(key) or "")
        for key in ("board", "grade", "subject", "chapter_title")
    }
    meta_block["pre_post_learning"] = _pre_post(metadata)

    refined = copy.deepcopy(original)
    flags: list[str] = []
    applied: dict[int, str] = {}  # row index -> rationale
    discarded = 0
    progress.log(
        f"Refiner: reading {len(projection)} rendered release row(s) "
        "(identities untouchable; every change is recorded on the release)."
    )
    for index in sorted(projection):
        rendered = projection[index]
        unit_id = rendered["unit_id"]
        payload = {
            "stage": "refine_release",
            "output_kind": output_kind,
            "rules": rules,
            "metadata": meta_block,
            "rows": [
                {
                    "row_ref": unit_id,
                    "rendered_topic_cell": rendered["topic_cell"],
                    "rendered_concept_title_cell": rendered[
                        "concept_title_cell"
                    ],
                    "concept_details": str(
                        original[index].get("concept_details") or ""
                    ),
                    "keywords": str(original[index].get("keywords") or ""),
                }
            ],
        }
        decision = kernel.decide(
            kind="refiner.row",
            unit_id=unit_id,
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_checker(unit_id),
            critic=critic,
            store=store,
            policy_version=REFINER_POLICY_VERSION,
        )
        response_row = next(
            (
                row
                for row in decision["response"].get("rows") or []
                if isinstance(row, Mapping) and row.get("row_ref") == unit_id
            ),
            None,
        )
        if response_row is None:
            continue
        after_details = str(response_row.get("concept_details") or "")
        after_keywords = (
            _normal(response_row.get("keywords"))
            if _normal(response_row.get("keywords"))
            else str(original[index].get("keywords") or "")
        )
        before_details = str(original[index].get("concept_details") or "")
        before_keywords = str(original[index].get("keywords") or "")
        decision_flags = [
            f"refiner[{unit_id}]: {flag}"
            for flag in decision.get("review_flags") or []
        ]
        flags.extend(decision_flags)
        if (
            after_details == before_details
            and after_keywords == before_keywords
        ):
            continue
        violations = _identity_violations(
            original[index], after_details, after_keywords
        )
        if violations:
            # Discarded, never applied — and the flag rides the RELEASE
            # diff, not the row, so the unrefined row stays byte-stable.
            discarded += 1
            flags.append(
                "refiner refinement discarded: identity drift "
                f"({unit_id}: {'; '.join(violations)})"
            )
            continue
        refined[index]["concept_details"] = after_details
        refined[index]["keywords"] = after_keywords
        applied[index] = _normal(response_row.get("rationale")) or (
            "refined to expectation"
        )
        for flag in decision_flags:
            refined[index]["review_flags"] = [
                *(refined[index].get("review_flags") or []),
                flag,
            ]

    rolled_back = 0
    if applied:
        # Fixpoint constraint (map §7.2): refined rows must remain stable
        # under the deterministic deposit chain; adopt its normalized
        # whitelist output for refined rows only.
        normalized = _deposit_deterministic_pipeline(
            copy.deepcopy(refined), metadata
        )
        if len(normalized) != len(refined):
            return _fallback(
                original,
                "deterministic deposit pipeline changed the row count "
                f"({len(refined)} -> {len(normalized)})",
                output_kind=output_kind,
            )
        for index in sorted(applied):
            if _normal(normalized[index].get("concept_title")).casefold() != (
                _normal(refined[index].get("concept_title")).casefold()
            ):
                return _fallback(
                    original,
                    "deterministic deposit pipeline misaligned the rows",
                    output_kind=output_kind,
                )
            candidate_details = str(
                normalized[index].get("concept_details") or ""
            )
            candidate_keywords = str(normalized[index].get("keywords") or "")
            if (
                candidate_details
                == str(refined[index].get("concept_details") or "")
                and candidate_keywords
                == str(refined[index].get("keywords") or "")
            ):
                continue
            violations = _identity_violations(
                original[index], candidate_details, candidate_keywords
            )
            if violations:
                rolled_back += 1
                refined[index] = copy.deepcopy(original[index])
                flags.append(
                    "refiner refinement rolled back: deterministic "
                    "normalization drifted identity "
                    f"({projection[index]['unit_id']})"
                )
                applied.pop(index)
                continue
            refined[index]["concept_details"] = candidate_details
            refined[index]["keywords"] = candidate_keywords

    if applied:
        # Mechanical validator re-check: a refinement that introduces a NEW
        # terminal error is rolled back per row, never shipped.
        baseline_errors = _terminal_error_keys(original, metadata)
        for _pass in range(len(applied) or 1):
            refined_errors = _terminal_error_keys(refined, metadata)
            regressed = [
                index
                for index in sorted(applied)
                if refined_errors.get(index, set())
                - baseline_errors.get(index, set())
            ]
            if not regressed:
                break
            for index in regressed:
                new_codes = sorted(
                    code
                    for _field, code in (
                        refined_errors.get(index, set())
                        - baseline_errors.get(index, set())
                    )
                )
                rolled_back += 1
                refined[index] = copy.deepcopy(original[index])
                flags.append(
                    "refiner refinement rolled back: introduced validator "
                    f"error(s) {', '.join(new_codes)} "
                    f"({projection[index]['unit_id']})"
                )
                applied.pop(index)

    # Identity proof (mechanics): same row count, same multiset of concept
    # placement keys, byte-identical per-row QID coverage.
    if len(refined) != len(original):
        return _fallback(
            original,
            "refinement changed the row count",
            output_kind=output_kind,
        )
    after_projection = _transient_projection(refined, metadata)
    if sorted(
        row["placement_key"] for row in projection.values()
    ) != sorted(row["placement_key"] for row in after_projection.values()):
        return _fallback(
            original,
            "refinement changed the concept placement keys",
            output_kind=output_kind,
        )
    for index, row in enumerate(original):
        if refined[index].get("_aegis_release_qids") != row.get(
            "_aegis_release_qids"
        ):
            return _fallback(
                original,
                "refinement changed a row's released QID coverage",
                output_kind=output_kind,
            )

    resealed = False
    if applied:
        for index in applied:
            refined[index]["_aegis_release_refined"] = True
        if _records_sealed(original):
            # The captured records carry row seals; the refined Description
            # changes the sealed source claim, so the complete set is
            # re-sealed (map §7.1). A re-seal failure ships the unrefined
            # rows instead — the Refiner never blocks a release.
            try:
                _reseal(refined)
                resealed = True
            except Exception as exc:  # noqa: BLE001
                return _fallback(
                    original,
                    f"re-sealing the refined rows failed: {exc}",
                    output_kind=output_kind,
                )

    changes: list[dict[str, Any]] = []
    for index in sorted(applied):
        for field in EDITABLE_FIELDS:
            before_value = str(original[index].get(field) or "")
            after_value = str(refined[index].get(field) or "")
            if before_value != after_value:
                changes.append({
                    "concept_key": projection[index]["unit_id"],
                    "field": field,
                    "before": before_value,
                    "after": after_value,
                    "reason": applied[index],
                })
    summary = (
        f"{len(applied)} of {len(projection)} released row(s) refined "
        f"({len(changes)} field change(s)); {discarded} refinement(s) "
        f"discarded for identity drift; {rolled_back} rolled back"
    )
    progress.log(
        f"Refiner: {summary}.",
        level="success" if not (discarded or rolled_back) else "warning",
    )
    diff = {
        "policy_version": REFINER_POLICY_VERSION,
        "output_kind": output_kind,
        "changes": changes,
        "summary": summary,
        "resealed_after_refinement": resealed,
    }
    return refined, diff, flags
