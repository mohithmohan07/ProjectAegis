"""Recorded question, answer, and rubric materialization for Output 02.

Each source-atom/blueprint-cell obligation is authored once through the
Phase-3 decision kernel. Mechanical defects receive bounded correction, an
independent critic is advisory, and The Fixer handles a structurally blocked
response. The decision store is the sole replay authority.

This pass keeps the existing workbook wire fields intact while Step 6 moves
Open/Specific and marking authorship into their own later decisions. It does
not infer either field locally: in particular, Objective is not a deterministic
alias for Specific.
"""
from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Mapping

from .. import bulk_import as bi
from .. import config
from . import assessment_profile
from . import assessment_release as rel
from . import katex_rules
from .phase3 import kernel

# ``-6`` places the stable rules, metadata, and curricular evidence before
# the candidate suffix and marks the explicit GPT-5.6 cache breakpoint.
# ``-7`` layers the owner-format answer/rubric contract on that transport
# shape. ``-8`` and ``-9`` pin lowercase paper-option labels. ``-10`` adds
# the supported Subjective wire and makes multipart Descriptive questions
# use the dedicated sub-question rubric columns rather than scoring the same
# parts twice. ``-11`` binds the selected Master-workbook Descriptive answer
# capacity into both the authored decision and its mechanical checker.
# ``-12`` bans book-referencing stems and carried source enumerators, and
# makes source figures travel: every image the item depends on is carried
# into the question or its Image-typed answer/rubric cells rather than
# silently dropped (owner audit 2026-08-27: self-contained wording; images
# absent from Assessments and Rubrics).
MATERIALIZE_POLICY_VERSION = "assessment-materialize-12"

_PROMPT_CACHE_STABLE_KEYS = (
    "stage",
    "rules",
    "metadata",
    "curricular_evidence",
)

# A candidate whose obligation exhausted the bounded corrections AND The
# Fixer. It answers for its obligation in the zero-loss accounting but is
# EXCLUDED from every stage after materialization and never ships as a
# row — the release records it instead (assessment_release_run).
BLOCKED_ELIGIBILITY = "blocked"

# Workbook capacities are positional mechanics, not content judgments.
MAX_OBJECTIVE_OPTIONS = 6
MAX_SUBJECTIVE_ANSWERS = 20
MAX_DESCRIPTIVE_ANSWERS = 10
MAX_SUBQUESTIONS = 15
MAX_SUBQUESTION_KEYWORDS = 6


def _descriptive_answer_capacity(
    profile: Mapping | str | None,
    *,
    learning_phase: str = "",
) -> int:
    """Return the Descriptive answer slots the selected Master can render.

    Ten remains the compatibility floor for historical/reference profiles.
    A resolved run profile may widen that positional capacity for one
    explicitly identified lane (the audited Grade-6 English Post contract
    uses thirty).  The scalar capacity, rather than the profile itself, is
    later bound into each decision payload.
    """

    contract = assessment_profile.master_workbook_contract(
        profile,
        learning_phase=learning_phase,
    )
    try:
        capacity = int(
            contract.get("descriptive_answer_slots", MAX_DESCRIPTIVE_ANSWERS)
        )
    except (TypeError, ValueError):
        capacity = MAX_DESCRIPTIVE_ANSWERS
    return max(MAX_DESCRIPTIVE_ANSWERS, capacity)


MATERIALIZE_SYSTEM = (
    "You are the Aegis assessment materialization author. Materialize ONE "
    "complete assessment item from the supplied source atom and blueprint "
    "cell. The cell's sheet kind, category, cognitive skill, difficulty, and "
    "marks are fixed and are not yours to change. There is no quota.\n"
    "For a standalone or exercise source, write a clear, complete, "
    "self-contained item while preserving its meaning and answer space. "
    "Self-contained means the learner never needs the book in hand: never "
    "keep wording that references the book or its apparatus — 'According "
    "to the passage about zero', \"According to 'At a Glance' in the "
    "book\", 'as given in your textbook' — state the needed context "
    "inside the item instead (owner audit, 2026-08-27). A leading "
    "enumerator the book printed before the ask ('6.', 'Q3.', '(iii)') "
    "is page apparatus and never opens the stem; sub-part labels inside "
    "a multipart item stay. For "
    "an activity, checkpoint, or experiment, preserve its procedure, "
    "materials, sequence, context, and required assets. With no source atom, "
    "stay strictly inside the supplied curricular evidence. Never invent "
    "facts, values, or constraints.\n"
    "When the source is a poem, story, or other literary passage, the "
    "question quotes ONLY the exact lines or stanza it actually asks "
    "about (owner ruling, 2026-08-21) — never the whole poem or passage, "
    "and never a long extract repeated as preamble. Name the location "
    "instead (e.g. 'in the second stanza') when the learner needs it; "
    "the learner has the chapter.\n"
    "For Objective cells, return no more than six canonical options with "
    "exactly one correct marker: each answers[] entry is an object whose "
    "answer_content carries the option text (never empty, never a "
    "duplicate), whose correct_answer is \"1\" on exactly one option "
    "and \"0\" on every other, and whose answer_type names the option's "
    "medium — exactly Phrases, Equation, or Image. The answers array is the "
    "paper display order and maps to lowercase a), b), c), d), e), f); option "
    "labels are never uppercase. Do not include a label inside "
    "answer_content because the workbook adds it. The question stem must "
    "not enumerate the options: options ride only answers[]; a stem that "
    "restates \"a) ... b) ...\" is a defect. For Subjective cells, return "
    "one answer object per response blank, in blank order, with "
    "answer_content, answer_display, answer_type, and a lowercase "
    "single-letter placeholder. The question uses the matching tokens "
    "$$a$$, $$b$$, ...; correct_answer is the empty string because these "
    "are expected responses rather than options, and the item carries no "
    "subquestions. For "
    "Descriptive cells, return a complete "
    "display answer, complete semantic answer/rubric blocks (each "
    "answers[] entry an object whose answer_content carries the block "
    "text and whose answer_type is Phrases, Equation, or Image), and "
    "every source-owned subquestion with its complete keyword "
    "evidence (each sub_questions[] entry an object with its text and its "
    "keywords array of {\"answer_type\":\"Phrases|Equation|Image\","
    "\"keyword\":\"...\"} objects). Author no "
    "subquestion the source item does not itself carry: a single-part "
    "question ships with answer/rubric blocks and an empty "
    "sub_questions[] — never wrapped in an invented part restating the "
    "stem. A genuinely multipart question instead ships with answers=[] "
    "and places all scoring evidence only in sub_questions[].keywords, so "
    "the main rubric and the sub-question rubric never score the same "
    "content twice. Its main question contains only shared instruction or "
    "context; part text lives only in sub_questions[]. Each "
    "sub_questions[] text begins "
    "with its enumeration label — a), b), c)… or (i), (ii), (iii)… — "
    "using the same scheme and order the item itself uses (SOP §5.4), so "
    "each part maps to its marking cleanly.\n"
    "answer_explanation must open by naming the correct answer — for an "
    "Objective item, the correct option by its lowercase letter and text (e.g. "
    "\"b) Get ready — ...\") — and then explain why it is correct. For "
    "Descriptive cells, display_answer and answer_explanation carry the "
    "SAME model answer (SOP §5.4 keeps them the same): the explanation "
    "restates the display answer's content, adding at most brief marking "
    "clarity — never a different answer.\n"
    "Do not decide Open or Specific and do not allocate weights, subquestion "
    "marks, keyword weights, duration, or keyboard mode. Dedicated later "
    "decisions own answer restriction and marking; any such extra values in "
    "your response are ignored.\n"
    "Use [Katex]...[/Katex] for rich mathematics in the question, display "
    "answer, and explanation. A type-declared answer_content uses exactly "
    "one whole-cell medium: Equation means full raw LaTeX with NO [Katex] "
    "wrapper and with any words inside \\text{...}; Phrases means wholly "
    "plain text with no TeX or [Katex]. Never mix the two. Prefix each "
    "Descriptive Phrases rubric block with one exact functional tag such "
    "as [content]:, [method]:, [accuracy]:, [working]:, [language]:, "
    "[creative]:, [evidence]:, or [diagram]:; a bracketed tag without its "
    "colon is malformed. A 4-mark single-part Descriptive item must have "
    "at least two distinct rubric blocks; one 4-mark block is invalid. "
    "In a rich-text field, a text-only table uses one complete "
    "\\begin{array}{column-spec}...\\end{array} inside [Katex]. In an "
    "Equation answer/keyword cell, use that complete array raw, without "
    "the wrapper. If "
    "the supplied source atom already associates an image with a table, "
    "preserve that full table as one source image rather than a partial array "
    "or separate cell screenshots. Every source figure, diagram, or picture "
    "the item depends on travels with it (owner audit, 2026-08-27: source "
    "images were silently absent from Assessments and Rubrics): reference it "
    "with its exact canonical [img src=\"...\" alt=\"...\"] tag from the "
    "supplied assets — in the question when the learner must read it, and in "
    "the answer/rubric evidence when the marked response IS a figure (an "
    "answers[] entry or keyword whose answer_type is Image carries that "
    "image's canonical tag or its URL, never a prose description alone). "
    "Never declare answer_type Image without an image source in the cell, "
    "and never invent an image URL the supplied assets do not carry. "
    "Use meaningful, neutral alt text for every "
    "image. If the source deliberately leaves a quantity, table cell, or "
    "learner choice blank, preserve that openness or express the solution "
    "symbolically; never invent convenient numbers merely to manufacture one "
    "numeric answer. Do "
    "not leak an answer in the question, options, or alt text.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","question":"","display_answer":"",'
    '"answers":[{"answer_content":"","answer_display":"",'
    '"placeholder":"a","correct_answer":"1|0|",'
    '"answer_type":"Phrases"}],'
    '"sub_questions":[{"text":"","keywords":['
    '{"answer_type":"Phrases","keyword":""}]}],'
    '"answer_explanation":"",'
    '"requires_visual":false,"rationale":"evidence-bound reason"}'
)

MATERIALIZE_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis assessment "
    "materialization decision. Audit the exact proposed item against the "
    "complete source atom, curricular evidence, assets, and blueprint cell: "
    "source fidelity, answer correctness, answer-space preservation, "
    "clarity and grade fit, semantic answer/rubric completeness, visual "
    "dependence, answer leakage, stem/option separation (an Objective stem "
    "that enumerates its own options), invented subquestions (a part "
    "the source item does not itself carry), explanation/answer "
    "consistency (the explanation must name the correct answer — the "
    "correct option's lowercase letter and text on an Objective item — and "
    "agree "
    "with the display answer), literary over-quoting (a whole poem "
    "or passage quoted where only the asked-about lines belong), "
    "lowercase paper-option order with no label duplicated inside option "
    "content, declared answer-cell medium purity, table/image integrity "
    "(a source figure the item depends on that no field carries, or an "
    "Image-typed cell holding no image source), "
    "fabricated values where the source intentionally leaves inputs open, "
    "Subjective placeholder/answer alignment, multipart content or rubrics "
    "duplicated between main fields and sub-question fields, malformed "
    "rubric tags, and the minimum two rubric blocks on a 4-mark single-part "
    "Descriptive item. Do not "
    "classify Open/Specific or audit "
    "mark allocation here. Do not "
    "rewrite, retry, or gate the proposal. Your dissent ships for review and "
    "the authored decision stands. State your honest confidence. There is no "
    "quota.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)

_AUDIT_FIELD = "_aegis_assessment_materialization"


class MaterializationError(ValueError):
    """The materialization obligation cannot be bound mechanically."""


def _to_float(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _candidate_id(atom: Mapping | None, cell: Mapping) -> str:
    seed = rel.canonical_json([
        (atom or {}).get("source_qid"), cell.get("cell_id"),
    ])
    return "CAND-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _envelope_hash(value: str) -> str:
    envelope_sha = str(value or "").strip()
    if not envelope_sha:
        raise MaterializationError(
            "assessment materialization requires an envelope hash"
        )
    return envelope_sha


def _validate_obligation(
    atom: Mapping | None, cell: Mapping, meta: Mapping,
    profile: Mapping | str | None = None,
) -> str:
    if atom is not None and not isinstance(atom, Mapping):
        raise MaterializationError("source atom is not an object")
    if not isinstance(cell, Mapping):
        raise MaterializationError("blueprint cell is not an object")
    if not isinstance(meta, Mapping):
        raise MaterializationError("materialization metadata is not an object")
    cell_id = str(cell.get("cell_id") or "").strip()
    if not cell_id:
        raise MaterializationError("blueprint cell has no cell_id")
    if str(cell.get("sheet_kind") or "") not in (
        # The RUN profile decides the allowed kinds (spec-step8 B2); bare
        # sheet_kinds() resolved DEFAULT_PROFILE and rejected a widened
        # profile's cells before staging saw them.
        assessment_profile.sheet_kinds(profile)
    ):
        raise MaterializationError(
            f"blueprint cell {cell_id!r} has an unknown sheet_kind"
        )
    marks = _to_float(cell.get("marks"))
    if marks is None or marks <= 0:
        raise MaterializationError(
            f"blueprint cell {cell_id!r} has non-positive or non-numeric marks"
        )
    if atom is not None and not str(atom.get("source_qid") or "").strip():
        raise MaterializationError("source atom has no source_qid")
    return _candidate_id(atom, cell)


def _learner_rich_text(proposal: Mapping) -> list[str]:
    values = [
        proposal.get("question"),
        proposal.get("display_answer"),
        proposal.get("answer_explanation"),
    ]
    # ``answers[].answer_content`` is type-declared, not general rich text;
    # its Equation/Phrases contract is checked separately below.
    raw_subquestions = proposal.get("sub_questions")
    for subquestion in (
        raw_subquestions if isinstance(raw_subquestions, list) else []
    ):
        if not isinstance(subquestion, Mapping):
            continue
        values.append(subquestion.get("text"))
        # Keyword values are also type-declared and checked below.
    return [str(value or "") for value in values]


def _rich_text_defects(
    proposal: Mapping, *, sheet_kind: str = "",
) -> list[str]:
    values = _learner_rich_text(proposal)
    if sheet_kind == "subjective":
        for answer in proposal.get("answers") or []:
            if isinstance(answer, Mapping):
                values.append(str(answer.get("answer_display") or ""))
    if sheet_kind == "subjective" and values:
        # ``$$a$$`` is the Subjective importer's placeholder token, not a
        # raw-math delimiter. Mask only the exact tokens declared by the
        # ordered answer blocks before applying the general rich-text gate.
        question = values[0]
        for answer in proposal.get("answers") or []:
            if not isinstance(answer, Mapping):
                continue
            placeholder = str(answer.get("placeholder") or "")
            if len(placeholder) == 1 and "a" <= placeholder <= "t":
                question = question.replace(f"$${placeholder}$$", "")
        values[0] = question
    blob = "\n".join(values)
    return [
        f"rich-text: {code}" for code in katex_rules.rich_text_issues(blob)
    ]


def _duplicated_option_label(value: Any) -> bool:
    """Whether an option already carries the label the renderer supplies."""

    return rel.option_content_has_label(value)


def _malformed_rubric_tag(value: Any, answer_type: Any) -> bool:
    """Functional rubric prefixes are a wire format, not a judgment."""

    return rel.malformed_rubric_tag(value, answer_type)


def _proposal_defects(
    proposal: Mapping[str, Any],
    cell: Mapping,
    candidate_id: str,
    *,
    descriptive_answer_capacity: int = MAX_DESCRIPTIVE_ANSWERS,
) -> list[str]:
    """Validate response mechanics only; semantic quality belongs to models."""

    if not isinstance(proposal, Mapping):
        return ["response is not an object"]
    defects: list[str] = []
    if str(proposal.get("candidate_id") or "") != candidate_id:
        defects.append(f"candidate_id must echo {candidate_id!r}")
    if not isinstance(proposal.get("question"), str) or not str(
        proposal.get("question") or ""
    ).strip():
        defects.append("question must be a non-empty string")
    for field in (
        "display_answer", "answer_explanation", "rationale",
    ):
        if not isinstance(proposal.get(field), str):
            defects.append(f"{field} must be a string")
    if not str(proposal.get("rationale") or "").strip():
        defects.append("response has no rationale")
    if not isinstance(proposal.get("requires_visual"), bool):
        defects.append("requires_visual must be boolean")

    raw_answers = proposal.get("answers")
    if not isinstance(raw_answers, list):
        defects.append("answers must be an array")
        answers: list[Mapping] = []
    else:
        answers = []
        for position, answer in enumerate(raw_answers, start=1):
            if not isinstance(answer, Mapping):
                defects.append(f"answer {position} is not an object")
            else:
                answers.append(answer)

    raw_subquestions = proposal.get("sub_questions")
    if not isinstance(raw_subquestions, list):
        defects.append("sub_questions must be an array")
        subquestions: list[Mapping] = []
    else:
        subquestions = []
        for position, subquestion in enumerate(raw_subquestions, start=1):
            if not isinstance(subquestion, Mapping):
                defects.append(f"subquestion {position} is not an object")
            else:
                subquestions.append(subquestion)

    kind = str(cell.get("sheet_kind") or "")
    if kind in {"objective", "subjective", "descriptive"}:
        # The CMS's answer_type column was shipping empty because no pass
        # ever authored it (owner review, job 65). The medium is the
        # model's call; the gate is enum membership — the same shape as
        # the sheet-kind and cognitive-skill gates.
        for position, answer in enumerate(answers, start=1):
            if answer.get("answer_type") not in bi.ANSWER_TYPES:
                defects.append(
                    f"answer {position} answer_type must be one of "
                    f"{tuple(bi.ANSWER_TYPES)} "
                    f"(got {answer.get('answer_type')!r})"
                )
            for issue in katex_rules.answer_cell_issues(
                str(answer.get("answer_type") or ""),
                str(answer.get("answer_content") or ""),
            ):
                defects.append(
                    f"answer {position} medium-format: {issue}"
                )
    if kind == "objective":
        if not 1 <= len(answers) <= MAX_OBJECTIVE_OPTIONS:
            defects.append(
                f"objective needs 1..{MAX_OBJECTIVE_OPTIONS} options "
                f"(got {len(answers)})"
            )
        correct = [
            answer for answer in answers
            if rel.is_correct_option(answer.get("correct_answer"))
        ]
        if len(correct) != 1:
            defects.append(
                f"exactly one correct option required (got {len(correct)}): "
                "each answers[] object needs correct_answer \"1\" on the "
                "one correct option and \"0\" on the rest"
            )
        contents = [
            str(answer.get("answer_content") or "").strip()
            for answer in answers
        ]
        if any(not content for content in contents):
            defects.append(
                "objective options must have non-empty content in each "
                "answers[] object's answer_content field"
            )
        populated = [content for content in contents if content]
        if len(set(populated)) != len(populated):
            defects.append("duplicate option text")
        for position, content in enumerate(contents, start=1):
            if _duplicated_option_label(content):
                defects.append(
                    f"objective option {position} must not include its own "
                    "letter label"
                )
        if not str(proposal.get("answer_explanation") or "").strip():
            defects.append("missing answer explanation")
    elif kind == "subjective":
        if not 1 <= len(answers) <= MAX_SUBJECTIVE_ANSWERS:
            defects.append(
                f"subjective needs 1..{MAX_SUBJECTIVE_ANSWERS} answers "
                f"(got {len(answers)})"
            )
        if subquestions:
            defects.append("subjective candidate must not have subquestions")
        question = str(proposal.get("question") or "")
        for position, answer in enumerate(answers, start=1):
            content = str(answer.get("answer_content") or "").strip()
            if not content:
                defects.append(f"subjective answer {position} has no content")
            display = answer.get("answer_display")
            if not isinstance(display, str) or not display.strip():
                defects.append(
                    f"subjective answer {position} needs answer_display"
                )
            expected = chr(ord("a") + position - 1)
            if answer.get("placeholder") != expected:
                defects.append(
                    f"subjective answer {position} placeholder must be "
                    f"{expected!r}"
                )
            token = f"$${expected}$$"
            if question.count(token) != 1:
                defects.append(
                    f"subjective question must contain placeholder token "
                    f"{token!r} exactly once"
                )
            if str(answer.get("correct_answer") or "").strip():
                defects.append(
                    f"subjective answer {position} must not carry an "
                    "objective correct marker"
                )
    elif kind == "descriptive":
        if not str(proposal.get("display_answer") or "").strip():
            defects.append("missing display answer")
        if not answers and not subquestions:
            defects.append("descriptive needs at least one answer/rubric block")
        if len(answers) > descriptive_answer_capacity:
            defects.append(
                f"more than {descriptive_answer_capacity} answer blocks"
            )
        marks = _to_float(cell.get("marks"))
        if subquestions and answers:
            defects.append(
                "multipart descriptive must keep main answers empty and use "
                "only subquestion keyword rubrics"
            )
        if marks == 4 and not subquestions and len(answers) < 2:
            defects.append(
                "a 4-mark descriptive item requires at least two "
                "answer/rubric blocks"
            )
        for position, answer in enumerate(answers, start=1):
            if not str(answer.get("answer_content") or "").strip():
                defects.append(
                    f"answer/rubric block {position} has no content"
                )
            if _malformed_rubric_tag(
                answer.get("answer_content"), answer.get("answer_type"),
            ):
                defects.append(
                    f"answer/rubric block {position} does not start with an "
                    "allowed functional tag or is without its required colon"
                )
        if len(subquestions) > MAX_SUBQUESTIONS:
            defects.append(f"more than {MAX_SUBQUESTIONS} subquestions")
        for position, subquestion in enumerate(subquestions, start=1):
            subquestion_text = str(subquestion.get("text") or "").strip()
            if not subquestion_text:
                defects.append(f"subquestion {position} has no text")
            elif subquestion_text in str(proposal.get("question") or ""):
                defects.append(
                    f"subquestion {position} text is duplicated in the main "
                    "question"
                )
            keywords = subquestion.get("keywords")
            if not isinstance(keywords, list):
                defects.append(
                    f"subquestion {position} keywords must be an array"
                )
                continue
            if len(keywords) > MAX_SUBQUESTION_KEYWORDS:
                defects.append(
                    f"subquestion with more than "
                    f"{MAX_SUBQUESTION_KEYWORDS} keyword slots"
                )
            if not keywords:
                defects.append(
                    f"subquestion {position} needs at least one keyword "
                    "rubric"
                )
            for keyword_position, keyword in enumerate(keywords, start=1):
                if not isinstance(keyword, Mapping):
                    defects.append(
                        f"subquestion {position} keyword {keyword_position} "
                        "is not an object"
                    )
                    continue
                if not str(keyword.get("keyword") or "").strip():
                    defects.append(
                        f"subquestion {position} keyword {keyword_position} "
                        "has no keyword text"
                    )
                keyword_type = keyword.get("answer_type")
                if keyword_type not in bi.ANSWER_TYPES:
                    defects.append(
                        f"subquestion {position} keyword {keyword_position} "
                        f"answer_type must be one of {tuple(bi.ANSWER_TYPES)}"
                    )
                for issue in katex_rules.answer_cell_issues(
                    str(keyword_type or ""),
                    str(keyword.get("keyword") or ""),
                ):
                    defects.append(
                        f"subquestion {position} keyword {keyword_position} "
                        f"medium-format: {issue}"
                    )
                if _malformed_rubric_tag(
                    keyword.get("keyword"), keyword_type,
                ):
                    defects.append(
                        f"subquestion {position} keyword {keyword_position} "
                        "does not start with an allowed functional tag or is "
                        "without its required colon"
                    )
    defects.extend(_rich_text_defects(proposal, sheet_kind=kind))
    return defects


def _checker(
    cell: Mapping,
    candidate_id: str,
    *,
    descriptive_answer_capacity: int = MAX_DESCRIPTIVE_ANSWERS,
) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        return _proposal_defects(
            response,
            cell,
            candidate_id,
            descriptive_answer_capacity=descriptive_answer_capacity,
        )

    return check


def _live_materialize(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload,
        stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    return generation._openai_json(
        MATERIALIZE_SYSTEM,
        suffix,
        purpose="concept_mapping",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "materialize-author-v6",
            prefix,
            shard_seed=str(payload.get("candidate_id") or ""),
        ),
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    prefix, suffix = generation._json_prompt_cache_parts(
        payload,
        stable_keys=_PROMPT_CACHE_STABLE_KEYS,
    )
    return generation._openai_json(
        MATERIALIZE_CRITIC_SYSTEM,
        suffix,
        purpose="concept_validation",
        prompt_cache_prefix=prefix,
        prompt_cache_key=generation._prompt_cache_key(
            "materialize-critic-v6",
            prefix,
            shard_seed=str(payload.get("candidate_id") or ""),
        ),
    )


def _live_authorities(
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    fixer: kernel.Provider | None,
) -> tuple[kernel.Provider, kernel.Critic | None, kernel.Provider | None]:
    if provider is not None:
        return provider, critic, fixer
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod

    envelope_mod.require_live_api()
    return _live_materialize, critic or _live_critic, fixer or fixer_mod.live_fixer


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag) for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]


def _stable_authority(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": _review_flags(decision),
        "fixer": bool(decision.get("fixer")),
    }


def _assemble(
    response: Mapping,
    atom: Mapping | None,
    cell: Mapping,
    *,
    candidate_id: str,
    decision: Mapping[str, Any],
) -> dict:
    question = str(response.get("question") or "")
    source = dict(atom or {})
    review_flags = _review_flags(decision)
    authority = _stable_authority(decision)
    audit = {
        "rationale": str(response.get("rationale") or ""),
        "flags": review_flags,
        "authority": authority,
    }
    answers = copy.deepcopy(list(response.get("answers") or []))
    for answer in answers:
        if isinstance(answer, dict):
            # Slice 4's dedicated marking pass is the sole authority for
            # workbook weightage.  Preserve semantic option/rubric content,
            # but never let an upstream draft allocation leak into Output 02.
            answer["answer_weightage"] = ""
            answer.pop("weightage", None)
    sub_questions = copy.deepcopy(
        list(response.get("sub_questions") or [])
    )
    for subquestion in sub_questions:
        if not isinstance(subquestion, dict):
            continue
        subquestion["marks"] = ""
        for keyword in subquestion.get("keywords") or []:
            if isinstance(keyword, dict):
                keyword["weightage"] = ""

    return {
        "candidate_id": candidate_id,
        "source_atom_ids": (
            [str(atom.get("source_qid"))]
            if atom and atom.get("source_qid") else []
        ),
        "blueprint_cell_id": str(cell.get("cell_id") or ""),
        "question": question,
        "question_text": question,
        "sheet_kind": str(cell.get("sheet_kind") or ""),
        "question_category": str(cell.get("question_category") or ""),
        "cognitive_skill": str(cell.get("cognitive_skill") or ""),
        "difficulty": str(cell.get("difficulty") or ""),
        "marks": cell.get("marks"),
        "appears_in": list(cell.get("appears_in") or []),
        "question_appears_in": ", ".join(
            str(value) for value in cell.get("appears_in") or []
        ),
        # Populated only by assessment.answer_restriction.
        "answer_restriction": "",
        "restriction_reason": "",
        "display_answer": str(response.get("display_answer") or ""),
        "answers": answers,
        "sub_questions": sub_questions,
        "answer_explanation": str(
            response.get("answer_explanation") or ""
        ),
        "requires_visual": bool(response.get("requires_visual")),
        # Populated only by assessment.marking.
        "question_duration": None,
        "math_keyboard": "",
        "assets": copy.deepcopy(list(source.get("assets") or [])),
        "image_manifest": copy.deepcopy(source.get("image_manifest") or []),
        "image_urls": copy.deepcopy(source.get("image_urls") or []),
        "tables": copy.deepcopy(source.get("tables") or []),
        "content_objects": copy.deepcopy(
            source.get("content_objects") or {}
        ),
        "source_qid": str(source.get("source_qid") or ""),
        "source_document_hash": str(
            source.get("source_document_hash") or ""
        ),
        "source_kind": str(source.get("source_kind") or ""),
        "source_evidence": str(source.get("raw_text") or ""),
        "shared_context": copy.deepcopy(source.get("shared_context")),
        "source_context": copy.deepcopy(
            source.get("source_context") or {
                "raw_text": source.get("raw_text"),
                "normalized_public_text": source.get(
                    "normalized_public_text"
                ),
                "source_answer": source.get("source_answer"),
                "shared_context": source.get("shared_context"),
                "parent_qid": source.get("parent_qid"),
                "subpart": source.get("subpart"),
                "alternative_set_id": source.get("alternative_set_id"),
            }
        ),
        "route_evidence": copy.deepcopy(source.get("route_evidence") or {}),
        "assessment_gist": copy.deepcopy(source.get("assessment_gist")),
        "assessment_eligibility": (
            "flagged" if review_flags else "accepted"
        ),
        "flags": list(review_flags),
        "authority": authority,
        _AUDIT_FIELD: audit,
    }


def _decision_payload(
    atom: Mapping | None,
    cell: Mapping,
    *,
    candidate_id: str,
    meta: Mapping,
    context: Any,
    descriptive_answer_capacity: int,
) -> dict[str, Any]:
    return {
        "stage": "assessment.materialize",
        "rules": MATERIALIZE_SYSTEM,
        "candidate_id": candidate_id,
        "metadata": copy.deepcopy(dict(meta)),
        "workbook_capacities": {
            "descriptive_answer_slots": descriptive_answer_capacity,
        },
        "source_atom": copy.deepcopy(dict(atom)) if atom is not None else None,
        "blueprint_cell": copy.deepcopy(dict(cell)),
        "curricular_evidence": copy.deepcopy(context),
    }


def _materialize_prepared(
    atom: Mapping | None,
    cell: Mapping,
    *,
    candidate_id: str,
    meta: Mapping,
    context: Any,
    descriptive_answer_capacity: int,
    envelope_sha256: str,
    provider: kernel.Provider,
    critic: kernel.Critic | None,
    store: kernel.DecisionStore,
    fixer: kernel.Provider | None,
) -> dict:
    payload = _decision_payload(
        atom,
        cell,
        candidate_id=candidate_id,
        meta=meta,
        context=context,
        descriptive_answer_capacity=descriptive_answer_capacity,
    )
    decision = kernel.decide(
        kind="assessment.materialize",
        unit_id=candidate_id,
        envelope_sha256=envelope_sha256,
        payload=payload,
        provider=provider,
        checker=_checker(
            cell,
            candidate_id,
            descriptive_answer_capacity=descriptive_answer_capacity,
        ),
        critic=critic,
        store=store,
        policy_version=MATERIALIZE_POLICY_VERSION,
        fixer=fixer,
    )
    return _assemble(
        decision["response"], atom, cell,
        candidate_id=candidate_id, decision=decision,
    )


def materialize_candidate(
    atom: Mapping | None,
    cell: Mapping,
    *,
    meta: Mapping,
    context: Any = "",
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
    profile: Mapping | str | None = None,
    learning_phase: str = "",
) -> dict:
    """Materialize one obligation through a content-addressed decision.

    ``profile`` remains validation input (spec-step8 B2), not authored
    metadata.  Only the selected workbook's scalar Descriptive capacity is
    bound into the decision payload, so a widened Post lane cannot replay a
    decision checked against the ten-slot reference layout (or vice versa).
    """

    run_profile = assessment_profile.resolve_for_metadata(profile, meta)
    descriptive_answer_capacity = _descriptive_answer_capacity(
        run_profile,
        learning_phase=learning_phase,
    )
    candidate_id = _validate_obligation(atom, cell, meta, run_profile)
    envelope_sha = _envelope_hash(envelope_sha256)
    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    return _materialize_prepared(
        atom,
        cell,
        candidate_id=candidate_id,
        meta=meta,
        context=context,
        descriptive_answer_capacity=descriptive_answer_capacity,
        envelope_sha256=envelope_sha,
        provider=provider,
        critic=critic,
        store=store or kernel.DecisionStore(),
        fixer=fixer,
    )


def materialize_candidates(
    pairs: list[tuple[Mapping | None, Mapping]],
    *,
    meta: Mapping,
    context: Any = "",
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
    profile: Mapping | str | None = None,
    learning_phase: str = "",
    on_result=None,
) -> dict:
    """Materialize every obligation in order with exact-once accounting.

    ``profile`` is validation input (spec-step8 B2); see
    ``materialize_candidate``. ``learning_phase`` selects only a declared
    lane-specific workbook-capacity override.

    ``on_result`` is forwarded verbatim to the fan-out
    (``kernel.parallel_map_in_order``): an ordered progress hook only,
    never an input to any decision.
    """

    if not isinstance(meta, Mapping):
        raise MaterializationError("materialization metadata is not an object")
    envelope_sha = _envelope_hash(envelope_sha256)
    run_profile = assessment_profile.resolve_for_metadata(profile, meta)
    descriptive_answer_capacity = _descriptive_answer_capacity(
        run_profile,
        learning_phase=learning_phase,
    )
    prepared: list[tuple[Mapping | None, Mapping, str]] = []
    seen: set[str] = set()
    for position, pair in enumerate(pairs, start=1):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise MaterializationError(
                f"materialization pair {position} is not an atom/cell pair"
            )
        atom, cell = pair
        candidate_id = _validate_obligation(
            atom, cell, meta, run_profile,
        )
        if candidate_id in seen:
            raise MaterializationError(
                "materialization obligations repeat candidate_id "
                f"{candidate_id!r}"
            )
        seen.add(candidate_id)
        prepared.append((atom, cell, candidate_id))
    if not prepared:
        return {
            "candidates": [], "accepted": 0, "flagged": 0,
            "zero_loss": rel.zero_loss_report([], [], []),
        }

    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    store = store or kernel.DecisionStore()

    def decide_one(unit: tuple[Mapping | None, Mapping, str]) -> dict:
        atom, cell, candidate_id = unit
        try:
            return _materialize_prepared(
                atom,
                cell,
                candidate_id=candidate_id,
                meta=meta,
                context=context,
                descriptive_answer_capacity=descriptive_answer_capacity,
                envelope_sha256=envelope_sha,
                provider=provider,
                critic=critic,
                store=store,
                fixer=fixer,
            )
        except kernel.ContractError as error:
            # The bounded corrections AND The Fixer both exhausted on this
            # ONE obligation (kernel.decide raises only after both). Taking
            # the whole Master file down here would discard every other
            # finished, paid-for question over it — the same trade
            # prequestions.py refuses, and CLAUDE.md refuses in as many
            # words ("finished work always ships"). So the obligation
            # returns as a BLOCKED marker: no fabricated content ships
            # (shipping a row that failed the mechanical contract is the
            # broken artifact the checker exists to refuse), every defect
            # is named, and the caller excludes it from the shipped set
            # while recording it loudly. Exact-once accounting holds — the
            # marker answers for its obligation in the zero-loss report.
            return {
                "candidate_id": candidate_id,
                "assessment_eligibility": BLOCKED_ELIGIBILITY,
                "source_atom_ids": (
                    [str(atom.get("source_qid") or "")] if atom else []
                ),
                "blueprint_cell_id": str(cell.get("cell_id") or ""),
                "sheet_kind": str(cell.get("sheet_kind") or ""),
                "flags": [
                    "materialization blocked after bounded corrections and "
                    "the Fixer: " + str(defect)
                    for defect in (error.defects or [str(error)])
                ],
            }

    candidates = kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
        on_result=on_result,
    )
    returned_ids = [
        str(candidate.get("candidate_id") or "") for candidate in candidates
    ]
    if len(returned_ids) != len(set(returned_ids)):
        raise MaterializationError(
            "materialization returned a duplicate candidate_id"
        )
    accepted_ids = [
        candidate["candidate_id"] for candidate in candidates
        if candidate["assessment_eligibility"] == "accepted"
    ]
    flagged_ids = [
        candidate["candidate_id"] for candidate in candidates
        if candidate["assessment_eligibility"] != "accepted"
    ]
    report = rel.zero_loss_report(
        [candidate_id for _atom, _cell, candidate_id in prepared],
        accepted_ids,
        flagged_ids,
    )
    if (
        not report["holds"]
        or len(returned_ids) != len(prepared)
        or returned_ids != [unit[2] for unit in prepared]
    ):
        raise MaterializationError(
            "materialization broke exact ordered coverage: "
            f"missing={report['missing']} unexpected={report['unexpected']}"
        )
    return {
        "candidates": candidates,
        "accepted": len(accepted_ids),
        "flagged": len(flagged_ids),
        "zero_loss": report,
    }
