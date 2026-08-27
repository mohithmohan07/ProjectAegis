"""Assessment-only Master Refiner (restructure Slice 5).

The pass reads the rendered Master workbook after every assessment decision
has settled and before the immutable release is created.  A model may polish
only answer/rubric prose and occupied-group descriptions.  Local mechanics
prove every other byte remains type-stable, re-run release arithmetic and the
production workbook read-back after each proposal, and roll back the affected
unit when a new defect appears.

Unlike the concepts Refiner, this module never owns staging.  It receives one
complete assessment payload and returns one complete payload, its durable
diff, and review flags.  Any failure keeps authored values unchanged and adds
review evidence; the Refiner never blocks publication.
"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from collections import Counter
from typing import Any, Mapping, Sequence

from .. import config
from ..bulk_import import assessment_workbook
from . import assessment_profile
from . import katex_rules
from . import assessment_release as rel
from . import assessment_release_service as release_service
from . import semantic_confidence_policy as confidence_policy
from .phase3 import kernel


MASTER_REFINER_POLICY_VERSION = "assessment-master-refiner-3"
CANDIDATE_POLICY_VERSION = "assessment-master-refiner-candidate-3"
GROUP_POLICY_VERSION = "assessment-master-refiner-group-1"
CANDIDATE_KIND = "assessment.master_refiner.candidate"
GROUP_KIND = "assessment.master_refiner.group"
AUDIT_FIELD = "_aegis_assessment_master_refinement"
WARNING = "assessment_master_refiner_review"

_PRINT_POSITION_FIELDS = frozenset({
    "bbox",
    "example_number",
    "page",
    "page_hint",
    "position",
    "print_position",
    "printer_page",
    "reading_order",
    "row",
    "row_index",
    "row_number",
    "source_end",
    "source_order",
    "source_page",
    "source_paper_number",
    "source_position",
    "source_start",
    "_phase32_segment_order",
    "_phase32_source_order",
    "_print_position",
    "_reading_order",
    "_source_position",
})

_RESPONSE_FIELDS = frozenset({
    "record_kind", "row_ref", "record", "rationale",
})

# Unicode assigns these legacy filler glyphs visible-letter/symbol categories
# even though they render blank. They cannot satisfy a mechanics-level
# "non-erased prose" contract on their own.
_INVISIBLE_FILLERS = frozenset({
    "\u115f",  # Hangul choseong filler
    "\u1160",  # Hangul jungseong filler
    "\u2800",  # Braille pattern blank
    "\u3164",  # Hangul filler
    "\uffa0",  # halfwidth Hangul filler
})

# Identity-bearing tokens embedded in otherwise editable prose.  Matching is
# ordered and non-overlapping, so prose may move around a token but may not
# alter, duplicate, remove, or reorder it.
_PROTECTED_TOKEN_RE = re.compile(
    r"\[img\b[^\]]*\]"
    r"|\[Katex\][\s\S]*?\[/Katex\]"
    # Keep the full non-whitespace URL token, including balanced or literal
    # closing punctuation used by real URLs. Conservatively binding adjacent
    # punctuation is safer than letting a URL suffix drift invisibly.
    r"|https://[^\s<>\"']+"
    r"|\bQINV-[A-Za-z0-9_.-]+\b",
    re.IGNORECASE,
)

CANDIDATE_SYSTEM = (
    "You are the Aegis assessment Master Refiner. Polish ONE already-final "
    "assessment row only around its settled identity. The clustered question "
    "wording is immutable: never alter question or question_text. You may "
    "polish only answer_explanation, every answers[].answer_content, "
    "Descriptive display_answer, and sub_questions[].keywords[].keyword. "
    "Preserve meaning, accepted answer space, option order, correct markers, "
    "marks, all weightages, subquestion text/decomposition, duration, keyboard "
    "mode, QIDs, URLs, image/KaTeX tokens, assets, provenance, routing, tier, "
    "labels, audits, flags, and every other field byte-for-byte and type-for-"
    "type. Preserve each typed answer_content/keyword medium exactly: "
    "Equation is one full raw-LaTeX cell without [Katex] and with any words "
    "inside \\text{...}; Phrases is wholly plain text without TeX. Never "
    "introduce tabular/array "
    "markup or collapse a 4-mark Descriptive rubric to one block. If no "
    "polish is warranted, echo the record unchanged. Return only "
    "strict JSON with record_kind='candidate', the exact row_ref, the complete "
    "record, and a non-empty rationale."
)

GROUP_SYSTEM = (
    "You are the Aegis assessment Master Refiner. Polish ONE already-final "
    "occupied assessment group's semantic_description only. Preserve its "
    "group/concept identity, tier, family, sequence, visible names, member "
    "candidate IDs and their order, status, audits, flags, assets, tokens and "
    "every other field byte-for-byte and type-for-type. Do not revisit routing, "
    "level, clustering, membership, or any question. If no polish is warranted, "
    "echo the record unchanged. Return only strict JSON with "
    "record_kind='group', the exact row_ref, the complete record, and a "
    "non-empty rationale."
)

CRITIC_SYSTEM = (
    "You are the independent advisory critic for one assessment Master "
    "Refiner proposal. Audit prose quality, meaning preservation, grade fit, "
    "and the immutable identity boundary against the rendered Master evidence. "
    "Never rewrite, gate, retry, adjudicate, or choose alternate content. A "
    "mechanically valid author decision always stands; dissent is recorded for "
    "review. Return only strict JSON: "
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}.'
)


class MasterRefinerError(ValueError):
    """The assessment Refiner input cannot be bound mechanically."""


def _normal(value: Any) -> str:
    return " ".join(str(value or "").split())


def _is_four_marks(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number == 4


def _has_visible_text(value: Any) -> bool:
    """Whether a string contains more than separators/control/format marks."""

    if not isinstance(value, str):
        return False
    return any(
        not character.isspace()
        and character not in _INVISIBLE_FILLERS
        and unicodedata.category(character)[0] not in {"C", "M", "Z"}
        for character in value
    )


def _content_evidence(value: Any) -> Any:
    """Deep-copy semantic evidence without printer coordinates."""

    if isinstance(value, Mapping):
        return {
            str(key): _content_evidence(raw)
            for key, raw in value.items()
            if str(key) not in _PRINT_POSITION_FIELDS
        }
    if isinstance(value, list):
        return [_content_evidence(raw) for raw in value]
    if isinstance(value, tuple):
        return [_content_evidence(raw) for raw in value]
    return copy.deepcopy(value)


def _typed(value: Any) -> Any:
    """A deterministic type-sensitive representation (``1 != 1.0``)."""

    if isinstance(value, Mapping):
        return [
            "mapping",
            [
                [str(key), _typed(raw)]
                for key, raw in sorted(value.items(), key=lambda row: str(row[0]))
            ],
        ]
    if isinstance(value, list):
        return ["list", [_typed(raw) for raw in value]]
    if isinstance(value, tuple):
        return ["tuple", [_typed(raw) for raw in value]]
    if value is None:
        return ["none", None]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, str):
        return ["str", value]
    return [type(value).__name__, repr(value)]


def _same_typed(left: Any, right: Any) -> bool:
    return _typed(left) == _typed(right)


def _candidate_editable_items(
    candidate: Mapping[str, Any],
) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = [
        ("answer_explanation", candidate.get("answer_explanation"))
    ]
    if str(candidate.get("sheet_kind") or "") == "descriptive":
        items.append(("display_answer", candidate.get("display_answer")))
    for answer_index, answer in enumerate(candidate.get("answers") or []):
        if isinstance(answer, Mapping):
            items.append((
                f"answers[{answer_index}].answer_content",
                answer.get("answer_content"),
            ))
    for sub_index, subquestion in enumerate(
        candidate.get("sub_questions") or []
    ):
        if not isinstance(subquestion, Mapping):
            continue
        for key_index, keyword in enumerate(
            subquestion.get("keywords") or []
        ):
            if isinstance(keyword, Mapping):
                items.append((
                    (
                        f"sub_questions[{sub_index}].keywords"
                        f"[{key_index}].keyword"
                    ),
                    keyword.get("keyword"),
                ))
    return items


def _protected_token_inventory(
    items: Sequence[tuple[str, Any]],
) -> list[tuple[str, list[str]]]:
    inventory: list[tuple[str, list[str]]] = []
    for path, value in items:
        tokens: list[str] = []
        if isinstance(value, str):
            tokens.extend(
                match.group(0)
                for match in _PROTECTED_TOKEN_RE.finditer(value)
            )
        inventory.append((path, tokens))
    return inventory


def _locked_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    locked = copy.deepcopy(dict(candidate))
    locked["answer_explanation"] = {"editable": "answer_explanation"}
    if str(locked.get("sheet_kind") or "") == "descriptive":
        locked["display_answer"] = {"editable": "display_answer"}
    answers = locked.get("answers")
    if isinstance(answers, list):
        for position, answer in enumerate(answers):
            if isinstance(answer, dict):
                answer["answer_content"] = {
                    "editable": f"answers[{position}].answer_content"
                }
    subquestions = locked.get("sub_questions")
    if isinstance(subquestions, list):
        for sub_position, subquestion in enumerate(subquestions):
            if not isinstance(subquestion, dict):
                continue
            keywords = subquestion.get("keywords")
            if isinstance(keywords, list):
                for key_position, keyword in enumerate(keywords):
                    if isinstance(keyword, dict):
                        keyword["keyword"] = {
                            "editable": (
                                f"sub_questions[{sub_position}].keywords"
                                f"[{key_position}].keyword"
                            )
                        }
    return locked


def _locked_group(group: Mapping[str, Any]) -> dict[str, Any]:
    locked = copy.deepcopy(dict(group))
    locked["semantic_description"] = {"editable": "semantic_description"}
    return locked


def _response_checker(
    *, unit_kind: str, unit_id: str, original: Mapping[str, Any],
) -> kernel.Checker:
    expected_locked = (
        _locked_candidate(original)
        if unit_kind == "candidate"
        else _locked_group(original)
    )
    expected_items = (
        _candidate_editable_items(original)
        if unit_kind == "candidate"
        else [("semantic_description", original.get("semantic_description"))]
    )
    expected_tokens = _protected_token_inventory(expected_items)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        if not isinstance(response, Mapping):
            return ["response must be an object"]
        unexpected = sorted(str(field) for field in set(response) - _RESPONSE_FIELDS)
        if unexpected:
            defects.append(f"response has unexpected fields {unexpected!r}")
        if response.get("record_kind") != unit_kind:
            defects.append(f"record_kind must echo {unit_kind!r}")
        if response.get("row_ref") != unit_id:
            defects.append(f"row_ref must echo {unit_id!r}")
        record = response.get("record")
        if not isinstance(record, Mapping):
            defects.append("record must be an object")
            return defects
        actual_locked = (
            _locked_candidate(record)
            if unit_kind == "candidate"
            else _locked_group(record)
        )
        if not _same_typed(actual_locked, expected_locked):
            defects.append(
                "every field outside the editable prose whitelist must remain "
                "byte- and type-identical"
            )
        editable_items = (
            _candidate_editable_items(record)
            if unit_kind == "candidate"
            else [("semantic_description", record.get("semantic_description"))]
        )
        if any(not isinstance(value, str) for _path, value in editable_items):
            defects.append("every editable prose value must remain a string")
        for (path, before), (_actual_path, after) in zip(
            expected_items, editable_items
        ):
            if (
                isinstance(before, str)
                and _has_visible_text(before)
                and isinstance(after, str)
                and not _has_visible_text(after)
            ):
                defects.append(
                    f"editable prose {path} was non-empty and must not be "
                    "erased"
                )
        if _protected_token_inventory(editable_items) != expected_tokens:
            defects.append(
                "ordered QID/URL/image/KaTeX identity tokens must remain "
                "exactly unchanged at each editable field path"
            )
        if unit_kind == "candidate":
            for answer_index, answer in enumerate(
                record.get("answers") or [], start=1
            ):
                if not isinstance(answer, Mapping):
                    continue
                for issue in katex_rules.answer_cell_issues(
                    str(answer.get("answer_type") or ""),
                    str(answer.get("answer_content") or ""),
                ):
                    defects.append(
                        f"answer {answer_index} violates declared medium: "
                        f"{issue}"
                    )
            for sub_index, subquestion in enumerate(
                record.get("sub_questions") or [], start=1
            ):
                if not isinstance(subquestion, Mapping):
                    continue
                for keyword_index, keyword in enumerate(
                    subquestion.get("keywords") or [], start=1
                ):
                    if not isinstance(keyword, Mapping):
                        continue
                    for issue in katex_rules.answer_cell_issues(
                        str(keyword.get("answer_type") or ""),
                        str(keyword.get("keyword") or ""),
                    ):
                        defects.append(
                            f"subquestion {sub_index} keyword "
                            f"{keyword_index} violates declared medium: "
                            f"{issue}"
                        )
            if (
                str(record.get("sheet_kind") or "") == "descriptive"
                and _is_four_marks(record.get("marks"))
                and len(record.get("answers") or []) < 2
            ):
                defects.append(
                    "4-mark descriptive candidate requires at least two "
                    "rubric blocks"
                )
            for field in ("answer_explanation", "display_answer"):
                if field == "display_answer" and str(
                    record.get("sheet_kind") or ""
                ) != "descriptive":
                    continue
                for issue in katex_rules.rich_text_issues(
                    str(record.get(field) or "")
                ):
                    defects.append(f"{field} rich-text: {issue}")
        if not _has_visible_text(response.get("rationale")):
            defects.append("rationale must be a non-empty string")
        return defects

    return check


def _kernel_provider(provider: kernel.Provider) -> kernel.Provider:
    """Keep non-object author JSON inside the same-checker Fixer lane."""

    def call(request: dict[str, Any]) -> Mapping[str, Any]:
        response = provider(request)
        if isinstance(response, Mapping):
            return response
        return {
            "_aegis_invalid_response_type": type(response).__name__,
            "_aegis_raw_response": copy.deepcopy(response),
        }

    return call


def _kernel_critic(critic: kernel.Critic | None) -> kernel.Critic | None:
    """Malformed advisory output is visible but can never gate a decision."""

    if critic is None:
        return None

    def call(request: dict[str, Any]) -> Mapping[str, Any]:
        review = critic(request)
        defects: list[str] = []
        if not isinstance(review, Mapping):
            defects.append(
                f"critic response is not an object ({type(review).__name__})"
            )
            copied: dict[str, Any] = {}
            confidence = float("nan")
        else:
            copied = copy.deepcopy(dict(review))
            unexpected = sorted(
                str(field)
                for field in set(copied) - {"verdict", "confidence", "issues"}
            )
            if unexpected:
                defects.append(f"critic response has unexpected fields {unexpected!r}")
            if copied.get("verdict") not in {"verified", "dissent"}:
                defects.append("critic verdict must be verified or dissent")
            raw_confidence = copied.get("confidence")
            if isinstance(raw_confidence, bool):
                confidence = float("nan")
            else:
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError):
                    confidence = float("nan")
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                defects.append("critic confidence must be finite in [0, 1]")
            if not isinstance(copied.get("issues"), list):
                defects.append("critic issues must be an array")
        if defects:
            return {"verdict": "malformed", "confidence": 0.0, "issues": defects}
        if confidence_policy.semantic_band(confidence) == "rejected":
            copied["issues"] = [
                *copied["issues"],
                f"critic confidence {confidence:.3f} is below the semantic "
                "acceptance floor; decision stands flagged",
            ]
        return copied

    return call


def _live_author(request: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    system = (
        CANDIDATE_SYSTEM
        if request.get("unit_kind") == "candidate"
        else GROUP_SYSTEM
    )
    return generation._openai_json(
        system, json.dumps(request, ensure_ascii=False), purpose="concept_mapping"
    )


def _live_critic(request: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        CRITIC_SYSTEM,
        json.dumps(request, ensure_ascii=False),
        purpose="concept_validation",
    )


def _instruction_suffix(instruction_set: Mapping[str, Any] | None) -> str:
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


def _append_warning(record: dict[str, Any]) -> None:
    raw = record.get("flags")
    flags = list(raw) if isinstance(raw, list) else []
    if WARNING not in flags:
        flags.append(WARNING)
    record["flags"] = flags


def _authority(decision: Mapping[str, Any]) -> dict[str, Any]:
    flags = [
        str(flag)
        for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]
    result = {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": flags,
    }
    if bool(decision.get("fixer")):
        result["fixer"] = True
    return result


def _audit(
    *,
    unit_kind: str,
    policy_version: str,
    status: str,
    changed_paths: Sequence[str],
    rationale: str,
    review_flags: Sequence[str],
    decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    authority = _authority(decision or {})
    return {
        "unit_kind": unit_kind,
        "decision_key": str(authority.get("decision_key") or ""),
        "policy_version": str(
            authority.get("policy_version") or policy_version
        ),
        "changed_paths": list(changed_paths),
        "rationale": str(rationale or ""),
        "review_flags": [str(flag) for flag in review_flags if str(flag).strip()],
        "fixer": bool(authority.get("fixer")),
        "status": status,
    }


def _candidate_changes(
    before: Mapping[str, Any], after: Mapping[str, Any], rationale: str,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def add(path: str, old: Any, new: Any) -> None:
        if not _same_typed(old, new):
            changes.append({
                "unit_kind": "candidate",
                "unit_id": str(before.get("candidate_id") or ""),
                "field_path": path,
                "before": copy.deepcopy(old),
                "after": copy.deepcopy(new),
                "reason": rationale,
            })

    add(
        "answer_explanation",
        before.get("answer_explanation"),
        after.get("answer_explanation"),
    )
    if str(before.get("sheet_kind") or "") == "descriptive":
        add("display_answer", before.get("display_answer"), after.get("display_answer"))
    for index, (old, new) in enumerate(
        zip(before.get("answers") or [], after.get("answers") or [])
    ):
        add(
            f"answers[{index}].answer_content",
            old.get("answer_content"),
            new.get("answer_content"),
        )
    for sub_index, (old_sub, new_sub) in enumerate(
        zip(before.get("sub_questions") or [], after.get("sub_questions") or [])
    ):
        for key_index, (old_key, new_key) in enumerate(
            zip(old_sub.get("keywords") or [], new_sub.get("keywords") or [])
        ):
            add(
                f"sub_questions[{sub_index}].keywords[{key_index}].keyword",
                old_key.get("keyword"),
                new_key.get("keyword"),
            )
    return changes


def _group_changes(
    before: Mapping[str, Any], after: Mapping[str, Any], rationale: str,
) -> list[dict[str, Any]]:
    if _same_typed(
        before.get("semantic_description"),
        after.get("semantic_description"),
    ):
        return []
    return [{
        "unit_kind": "group",
        "unit_id": str(before.get("group_key") or ""),
        "field_path": "semantic_description",
        "before": copy.deepcopy(before.get("semantic_description")),
        "after": copy.deepcopy(after.get("semantic_description")),
        "reason": rationale,
    }]


def _apply_candidate_prose(
    target: dict[str, Any], proposal: Mapping[str, Any],
) -> None:
    target["answer_explanation"] = copy.deepcopy(proposal["answer_explanation"])
    if str(target.get("sheet_kind") or "") == "descriptive":
        target["display_answer"] = copy.deepcopy(proposal["display_answer"])
    for old, new in zip(target.get("answers") or [], proposal.get("answers") or []):
        old["answer_content"] = copy.deepcopy(new["answer_content"])
    for old_sub, new_sub in zip(
        target.get("sub_questions") or [], proposal.get("sub_questions") or []
    ):
        for old_key, new_key in zip(
            old_sub.get("keywords") or [], new_sub.get("keywords") or []
        ):
            old_key["keyword"] = copy.deepcopy(new_key["keyword"])


def _wire_text(value: Any) -> str:
    return "" if value is None else str(value)


def _validation_state(
    payload: Mapping[str, Any], profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Run release mechanics and exact production workbook read-back."""

    frozen = rel.freeze_payload(payload, profile)
    errors = [f"payload: {error}" for error in frozen.get("errors") or []]
    snapshot = release_service.snapshot_from_staged_release(payload)
    errors.extend(
        f"payload: {finding['code']}: {finding['message']}"
        for finding in rel.unresolved_question_homes(snapshot, profile)
    )
    output = assessment_workbook.build_dual_output(snapshot, profile)
    manifest = output["manifest"]
    errors.extend(
        f"concept-readback: {error}"
        for error in manifest.get("read_back", {}).get("concepts_errors") or []
    )
    errors.extend(
        f"master-readback: {error}"
        for error in manifest.get("read_back", {}).get("master_errors") or []
    )
    parsed = assessment_workbook.parse_workbook(output["master_xlsx"])
    provenance = {
        (str(row.get("sheet") or ""), int(row.get("row") or 0)): str(
            row.get("group_key") or ""
        )
        for row in manifest.get("issues", {}).get("group_provenance") or []
    }
    candidate_rows: dict[str, list[dict[str, Any]]] = {}
    group_rows: dict[str, list[dict[str, Any]]] = {}
    label_to_id = {
        str(candidate.get("question_label") or ""): str(
            candidate.get("candidate_id") or ""
        )
        for candidate in payload.get("candidates") or []
        if str(candidate.get("question_label") or "")
    }
    for sheet_name, sheet in parsed.get("sheets", {}).items():
        rows = list(sheet.get("rows") or [])
        row_numbers = list(sheet.get("row_numbers") or [])
        for row_number, row in zip(row_numbers, rows):
            copied = copy.deepcopy(dict(row))
            label = str(row.get("question_label") or "")
            candidate_id = label_to_id.get(label)
            if candidate_id:
                candidate_rows.setdefault(candidate_id, []).append(copied)
            group_key = provenance.get((str(sheet_name), int(row_number)))
            if group_key:
                group_rows.setdefault(group_key, []).append(copied)

    for candidate in payload.get("candidates") or []:
        candidate_id = str(candidate.get("candidate_id") or "")
        rows = candidate_rows.get(candidate_id, [])
        if len(rows) != 1:
            errors.append(
                f"refiner-readback: candidate {candidate_id!r} has {len(rows)} "
                "rendered Master rows"
            )
            continue
        row = rows[0]
        expected: list[tuple[str, Any]] = [
            ("answer_explanation", candidate.get("answer_explanation")),
        ]
        if str(candidate.get("sheet_kind") or "") == "descriptive":
            expected.append(("display_answer", candidate.get("display_answer")))
        for index, answer in enumerate(candidate.get("answers") or [], start=1):
            expected.append((f"answer_content_{index}", answer.get("answer_content")))
        for sub_index, subquestion in enumerate(
            candidate.get("sub_questions") or [], start=1
        ):
            for key_index, keyword in enumerate(
                subquestion.get("keywords") or [], start=1
            ):
                expected.append(
                    (f"sq{sub_index}_keyword_{key_index}", keyword.get("keyword"))
                )
        for field, value in expected:
            if _wire_text(row.get(field)) != _wire_text(value):
                errors.append(
                    f"refiner-readback: candidate {candidate_id!r} {field} "
                    "does not round-trip exactly"
                )

    groups_by_key = {
        str(group.get("group_key") or ""): group
        for group in payload.get("groups") or []
    }
    # The SECOND read-back enforcing "every group has a rendered Master
    # row", scoped exactly like ``validate_master_file``'s
    # ``expected_group_keys`` and in the same change (OWNER RULING OD5 /
    # spec-step8 T16). A concept with zero placed question rows gets one
    # tail row stopping at the concept columns and NO Group row, so an
    # unscoped read-back would turn every questionless concept into an
    # error. It is NOT "a group with no questions" — that would stop
    # catching a genuinely missing catalogue row on a concept that does
    # have questions.
    #
    # Scoped through the SAME decision the renderer takes, not a looser
    # restatement of it: ``rel.candidate_home_finding`` is what decides
    # whether a candidate reaches a data row, and the renderable set is
    # ``sheet_for_kind()`` — the module constant INTERSECTED with the
    # layout's sheet-name map — not the constant alone. The former version
    # required only a label and a renderable kind, so on an already-defective
    # release it reported extra "has no rendered Master row" errors the
    # renderer legitimately caused.
    snapshot_concept_keys = {
        str(concept.get("concept_key") or "")
        for topic in snapshot.get("topics") or []
        for concept in topic.get("concepts") or []
    }
    snapshot_groups_by_key: dict[str, Any] = {}
    for group in snapshot.get("groups") or []:
        key = str(group.get("group_key") or "")
        if key and key not in snapshot_groups_by_key:
            snapshot_groups_by_key[key] = group
    renderable = tuple(assessment_workbook.sheet_for_kind())
    placed_concepts = {
        str(candidate.get("concept_key") or "")
        for candidate in snapshot.get("candidates") or []
        if rel.candidate_home_finding(
            candidate, snapshot_concept_keys, snapshot_groups_by_key,
            renderable) is None
    }
    concept_key_by_group = {
        str(group.get("group_key") or ""): str(group.get("concept_key") or "")
        for group in snapshot.get("groups") or []
    }
    for group_key, group in groups_by_key.items():
        rows = group_rows.get(group_key, [])
        if not rows:
            if concept_key_by_group.get(group_key, "") not in placed_concepts:
                continue
            errors.append(
                f"refiner-readback: group {group_key!r} has no rendered Master row"
            )
            continue
        expected = _wire_text(group.get("semantic_description"))
        for row in rows:
            if _wire_text(row.get("group_description")) != expected:
                errors.append(
                    f"refiner-readback: group {group_key!r} description does "
                    "not round-trip exactly"
                )

    return {
        "errors": errors,
        "candidate_rows": candidate_rows,
        "group_rows": group_rows,
    }


def _new_errors(before: Sequence[str], after: Sequence[str]) -> list[str]:
    remaining = Counter(after) - Counter(before)
    return list(remaining.elements())


def _model_record(record: Mapping[str, Any]) -> dict[str, Any]:
    copied = _content_evidence(record)
    if isinstance(copied, dict):
        copied.pop(AUDIT_FIELD, None)
        flags = copied.get("flags")
        if isinstance(flags, list):
            copied["flags"] = [flag for flag in flags if flag != WARNING]
    return copied


def _unit_payload(
    *,
    unit_kind: str,
    unit_id: str,
    record: Mapping[str, Any],
    rendered_rows: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    instruction_set: Mapping[str, Any] | None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    rules = CANDIDATE_SYSTEM if unit_kind == "candidate" else GROUP_SYSTEM
    payload: dict[str, Any] = {
        "stage": (
            CANDIDATE_KIND if unit_kind == "candidate" else GROUP_KIND
        ),
        "unit_kind": unit_kind,
        "row_ref": unit_id,
        "rules": rules + _instruction_suffix(instruction_set),
        "critic_rules": CRITIC_SYSTEM,
        "metadata": _content_evidence(metadata),
        "rendered_master_rows": _content_evidence(list(rendered_rows)),
        "context": _content_evidence(context),
    }
    payload[unit_kind] = _model_record(record)
    return payload


def _empty_diff(summary: str, review_flags: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "policy_version": MASTER_REFINER_POLICY_VERSION,
        "decision_policies": {
            CANDIDATE_KIND: CANDIDATE_POLICY_VERSION,
            GROUP_KIND: GROUP_POLICY_VERSION,
        },
        "output_kind": "assessment_master",
        "changes": [],
        "review_flags": list(review_flags),
        "summary": summary,
        "resealed_after_refinement": False,
    }


def _global_fallback(
    payload: Mapping[str, Any], reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    result = copy.deepcopy(dict(payload))
    flag = f"assessment Master Refiner unavailable: {_normal(reason)}"
    for unit_kind, collection, policy in (
        ("candidate", "candidates", CANDIDATE_POLICY_VERSION),
        ("group", "groups", GROUP_POLICY_VERSION),
    ):
        for record in result.get(collection) or []:
            if (
                unit_kind == "group"
                and not list(record.get("member_candidate_ids") or [])
            ):
                continue
            _append_warning(record)
            record[AUDIT_FIELD] = _audit(
                unit_kind=unit_kind,
                policy_version=policy,
                status="unavailable",
                changed_paths=[],
                rationale=flag,
                review_flags=[flag],
            )
    diff = _empty_diff(flag, [flag])
    result["refinements"] = copy.deepcopy(diff)
    return [result], diff, [flag]


def _locked_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Ignore only whitelisted prose and this pass's mechanics annotations."""

    locked = copy.deepcopy(dict(payload))
    for record in locked.get("candidates") or []:
        record.pop(AUDIT_FIELD, None)
        if isinstance(record.get("flags"), list):
            record["flags"] = [flag for flag in record["flags"] if flag != WARNING]
        replacement = _locked_candidate(record)
        record.clear()
        record.update(replacement)
    for record in locked.get("groups") or []:
        record.pop(AUDIT_FIELD, None)
        if isinstance(record.get("flags"), list):
            record["flags"] = [flag for flag in record["flags"] if flag != WARNING]
        replacement = _locked_group(record)
        record.clear()
        record.update(replacement)
    return locked


def refine_master(
    records: Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any],
    instruction_set: Mapping[str, Any] | None = None,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    envelope_sha256: str | None = None,
    fixer: kernel.Provider | None = None,
    on_unit=None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Refine one complete assessment payload; never raise or block.

    ``on_unit(done, total)`` is an optional progress hook called after
    each unit is applied or contained: reporting mechanics only, never an
    input to any decision, and a raising observer is swallowed.
    """

    originals = [
        copy.deepcopy(dict(record))
        for record in records
        if isinstance(record, Mapping)
    ]
    if not originals:
        return [], _empty_diff("no assessment release payload to refine"), []
    if len(originals) != 1:
        # There is no safe way to attach row warnings to a malformed batch;
        # retain every input mapping exactly and expose the release-level flag.
        flag = "assessment Master Refiner requires exactly one release payload"
        return originals, _empty_diff(flag, [flag]), [flag]
    original = originals[0]
    try:
        if not isinstance(metadata, Mapping):
            raise MasterRefinerError("metadata must be an object")
        envelope_sha = str(envelope_sha256 or "").strip()
        if not envelope_sha:
            raise MasterRefinerError("a sealed envelope hash is required")
        profile = assessment_profile.resolve(
            metadata.get("assessment_profile") or metadata.get("profile")
        )
        baseline = _validation_state(original, profile)
        if baseline["errors"]:
            return _global_fallback(
                original,
                "baseline assessment Master failed mechanics/read-back: "
                + "; ".join(baseline["errors"][:8]),
            )
        if provider is None:
            from . import canonical_source_phase3 as phase3_core
            from .phase3 import fixer as fixer_mod

            if not phase3_core.semantic_api_enabled():
                return _global_fallback(original, "live semantic API is not enabled")
            provider = _live_author
            critic = critic or _live_critic
            fixer = fixer or fixer_mod.live_fixer
        provider = _kernel_provider(provider)
        critic = _kernel_critic(critic)
        decision_store = store or kernel.DecisionStore()

        current = copy.deepcopy(original)
        current_state = baseline
        changes: list[dict[str, Any]] = []
        release_flags: list[str] = []

        concept_snapshot = current.get("concept_snapshot")
        context = {
            "source_concept_release_sha256": str(
                current.get("source_concept_release_sha256") or ""
            ),
            "chapter": (
                _content_evidence(concept_snapshot.get("chapter") or {})
                if isinstance(concept_snapshot, Mapping)
                else {}
            ),
        }

        units: list[tuple[str, int]] = [
            ("candidate", index)
            for index in range(len(current.get("candidates") or []))
        ]
        units.extend(
            ("group", index)
            for index, group in enumerate(current.get("groups") or [])
            if list(group.get("member_candidate_ids") or [])
        )

        # The units run in TWO WAVES — every candidate, then every group —
        # each wave deciding in parallel and applying sequentially, the
        # pattern release_refiner.py already uses for the concepts lane.
        # The evidence each unit is shown is unchanged from the serial
        # loop this replaces: a candidate's rendered rows are its own and
        # are untouched by any other candidate's whitelisted prose, so the
        # wave-opening render is the same evidence the serial loop showed
        # it; a group's rendered rows DO include its members' rows, which
        # is why groups wait for every candidate application before their
        # wave's payloads are assembled. Application, rollback, audits and
        # flags stay strictly in unit order, so the refined payload is the
        # one the serial loop produced.
        workers = config.phase3_decision_workers()
        done_units = 0
        total_units = len(units)

        def _report_unit() -> None:
            # Reporting mechanics only; a raising observer must not reach
            # the never-raise seam and convert into a global fallback.
            if on_unit is None:
                return
            try:
                on_unit(done_units, total_units)
            except Exception:  # noqa: BLE001 - progress is an assist
                pass

        def _apply_outcome(entry: dict[str, Any], outcome: object) -> None:
            nonlocal current, current_state, done_units
            unit_kind = entry["unit_kind"]
            collection = entry["collection"]
            index = entry["index"]
            policy = entry["policy"]
            unit_id = entry["unit_id"]
            record = current[collection][index]
            decision = outcome if isinstance(outcome, Mapping) else None
            decision_flags: list[str] = []
            try:
                if entry.get("no_evidence"):
                    reason = (
                        f"{unit_kind} {unit_id or '<blank>'!r} has no unique "
                        "rendered Master evidence"
                    )
                    _append_warning(record)
                    record[AUDIT_FIELD] = _audit(
                        unit_kind=unit_kind,
                        policy_version=policy,
                        status="unavailable",
                        changed_paths=[],
                        rationale=reason,
                        review_flags=[reason],
                    )
                    release_flags.append(reason)
                    return
                if isinstance(outcome, BaseException):
                    raise outcome
                decision_flags = [
                    str(flag)
                    for flag in decision.get("review_flags") or []
                    if str(flag).strip()
                ]
                response = decision.get("response") or {}
                proposal = response.get("record")
                if not isinstance(proposal, Mapping):
                    raise MasterRefinerError("kernel returned no proposal record")
                rationale = str(response.get("rationale") or "")
                if unit_kind == "candidate":
                    before_changes = _candidate_changes(record, proposal, rationale)
                else:
                    before_changes = _group_changes(record, proposal, rationale)
                if before_changes:
                    trial = copy.deepcopy(current)
                    trial_record = trial[collection][index]
                    if unit_kind == "candidate":
                        _apply_candidate_prose(trial_record, proposal)
                    else:
                        trial_record["semantic_description"] = copy.deepcopy(
                            proposal["semantic_description"]
                        )
                    trial_state = _validation_state(trial, profile)
                    regressions = _new_errors(
                        current_state["errors"], trial_state["errors"]
                    )
                    if regressions:
                        reason = (
                            "Refiner proposal introduced a new "
                            "mechanical/read-back defect: "
                            + "; ".join(regressions[:6])
                        )
                        _append_warning(record)
                        record[AUDIT_FIELD] = _audit(
                            unit_kind=unit_kind,
                            policy_version=policy,
                            status="rolled_back",
                            changed_paths=[],
                            rationale=reason,
                            review_flags=[*decision_flags, reason],
                            decision=decision,
                        )
                        release_flags.append(f"{unit_kind}[{unit_id}]: {reason}")
                        return
                    current = trial
                    current_state = trial_state
                    record = current[collection][index]
                # An accepted no-op proposal applies nothing and re-proves
                # nothing: typed-equality across the whole whitelist means
                # the rendered workbook is byte-identical, so the per-unit
                # workbook render/read-back is skipped instead of re-run —
                # the single largest mechanical cost of this pass on a
                # payload the model mostly leaves alone. The final combined
                # validation below still proves the complete result.
                paths = [row["field_path"] for row in before_changes]
                if decision_flags:
                    _append_warning(record)
                    release_flags.extend(
                        f"{unit_kind}[{unit_id}]: {flag}"
                        for flag in decision_flags
                    )
                record[AUDIT_FIELD] = _audit(
                    unit_kind=unit_kind,
                    policy_version=policy,
                    status="applied" if paths else "unchanged",
                    changed_paths=paths,
                    rationale=rationale,
                    review_flags=decision_flags,
                    decision=decision,
                )
                changes.extend(before_changes)
            except Exception as exc:  # noqa: BLE001 - per-unit never blocks
                reason = f"{type(exc).__name__}: {_normal(exc)}"
                record = current[collection][index]
                _append_warning(record)
                record[AUDIT_FIELD] = _audit(
                    unit_kind=unit_kind,
                    policy_version=policy,
                    status=("rolled_back" if decision else "unavailable"),
                    changed_paths=[],
                    rationale=reason,
                    review_flags=[*decision_flags, reason],
                    decision=decision,
                )
                release_flags.append(f"{unit_kind}[{unit_id}]: {reason}")
            finally:
                done_units += 1
                _report_unit()

        def _run_wave(unit_list: list[tuple[str, int]]) -> None:
            if not unit_list:
                return
            # Payloads are assembled up front, in this thread, from the
            # wave's opening records and render — pool workers only run
            # the content-addressed decision itself, so nothing they read
            # is mutated while they run.
            state = current_state
            prepared: list[dict[str, Any]] = []
            for unit_kind, index in unit_list:
                collection = (
                    "candidates" if unit_kind == "candidate" else "groups"
                )
                record = current[collection][index]
                id_field = (
                    "candidate_id" if unit_kind == "candidate" else "group_key"
                )
                unit_id = str(record.get(id_field) or "").strip()
                entry: dict[str, Any] = {
                    "unit_kind": unit_kind,
                    "index": index,
                    "collection": collection,
                    "unit_id": unit_id,
                    "policy": (
                        CANDIDATE_POLICY_VERSION
                        if unit_kind == "candidate"
                        else GROUP_POLICY_VERSION
                    ),
                    "kind": (
                        CANDIDATE_KIND
                        if unit_kind == "candidate"
                        else GROUP_KIND
                    ),
                }
                rendered_rows = (
                    state["candidate_rows"].get(unit_id, [])
                    if unit_kind == "candidate"
                    else state["group_rows"].get(unit_id, [])
                )
                if not unit_id or not rendered_rows:
                    entry["no_evidence"] = True
                    prepared.append(entry)
                    continue
                entry["decision_record"] = _model_record(record)
                entry["payload"] = _unit_payload(
                    unit_kind=unit_kind,
                    unit_id=unit_id,
                    record=record,
                    rendered_rows=rendered_rows,
                    metadata=metadata,
                    instruction_set=instruction_set,
                    context={
                        **context,
                        "member_questions": [
                            {
                                "candidate_id": str(
                                    candidate.get("candidate_id") or ""
                                ),
                                "question": str(candidate.get("question") or ""),
                                "question_text": str(
                                    candidate.get("question_text") or ""
                                ),
                            }
                            for candidate in current.get("candidates") or []
                            if unit_kind == "group"
                            and str(candidate.get("candidate_id") or "")
                            in set(record.get("member_candidate_ids") or [])
                        ],
                    },
                )
                prepared.append(entry)

            def _decide(entry: dict[str, Any]) -> object:
                if entry.get("no_evidence"):
                    return None
                try:
                    return kernel.decide(
                        kind=entry["kind"],
                        unit_id=entry["unit_id"],
                        envelope_sha256=envelope_sha,
                        payload=entry["payload"],
                        provider=provider,
                        checker=_response_checker(
                            unit_kind=entry["unit_kind"],
                            unit_id=entry["unit_id"],
                            original=entry["decision_record"],
                        ),
                        critic=critic,
                        store=decision_store,
                        policy_version=entry["policy"],
                        fixer=fixer,
                    )
                except Exception as exc:  # noqa: BLE001 - applied per unit
                    return exc

            outcomes = kernel.parallel_map_in_order(
                prepared,
                _decide,
                max_workers=workers,
                labels=[
                    f"Master Refiner {entry['unit_kind']} "
                    f"{position + 1}/{len(prepared)}"
                    for position, entry in enumerate(prepared)
                ],
                announce="Master Refiner decisions",
            )
            for entry, outcome in zip(prepared, outcomes):
                _apply_outcome(entry, outcome)

        _run_wave([unit for unit in units if unit[0] == "candidate"])
        _run_wave([unit for unit in units if unit[0] == "group"])

        final_state = _validation_state(current, profile)
        final_regressions = _new_errors(baseline["errors"], final_state["errors"])
        if final_regressions:
            return _global_fallback(
                original,
                "combined final validation regressed: "
                + "; ".join(final_regressions[:8]),
            )
        if not _same_typed(_locked_payload(original), _locked_payload(current)):
            return _global_fallback(
                original, "a field outside the Refiner whitelist changed"
            )

        summary = (
            f"applied {len(changes)} assessment Master prose change(s)"
            if changes
            else "assessment Master required no accepted prose changes"
        )
        diff = _empty_diff(summary, release_flags)
        diff["changes"] = changes
        current["refinements"] = copy.deepcopy(diff)
        return [current], diff, release_flags
    except Exception as exc:  # noqa: BLE001 - whole pass never blocks
        return _global_fallback(original, f"{type(exc).__name__}: {exc}")
