"""The Phase 03 coverage plan and its generated questions (Q4 per D3).

Step-7 slice D1 regressions. Q4 (the recorded numeric calibration
targets of Q20 are superseded by register Q26: the Master Governing
Contract v2.0 §8 states complete diagnostic coverage of the Mastery, at
least one routed question and no quotas, with the tier split left to the
model and the 2026-08-21 diagnostic posture standing): neither mandatory
quota nor maximum. The model authors a concept-specific coverage plan;
every plan carries an authored, critic-flagged rationale; an explicit
blueprint may override; a thin pre-concept is never padded.

The obvious implementation — ``if planned != 40: require_rationale()`` —
is a numeric threshold deciding meaning AND the literal in a validator,
forbidden twice over. The decided resolution (spec T6) is to require the
rationale UNCONDITIONALLY, so no code ever compares a plan to anything.
These regressions pin exactly that:

* the rationale is required whatever the numbers are — a plan of exactly
  forty still carries one;
* there is NO literal 40 in any validator, checker, constant, threshold
  or comparison, anywhere under ``app/`` (repo-wide over the validator
  surface); the norm exists only as labelled calibration prose in the
  plan payload's rules;
* a thin pre-concept plans few questions and is never padded; an empty
  plan is legal and spends nothing;
* calibration (level/grade/context/subject/board) reaches the authoring
  payload as EVIDENCE, through The Architect's instruction set, and no
  per-grade or per-subject branch exists in the code;
* no inventory QID reaches a generated question, its answer or its
  rationale (fail-closed), while a CRITIC naming a QID only flags;
* a plan/output shortfall flags or routes to the Fixer and never raises
  a run down;
* the group TIER vocabulary is not the workbook's difficulty vocabulary,
  and an authored question carries neither;
* a second identical run spends nothing.
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib

import pytest

from app.services.phase3 import kernel, premap, prequestions
from tests import test_phase3_premap as premap_golden
from tests import test_phase3_settle_golden as settle_golden

GOLDEN = settle_golden.GOLDEN
APP = pathlib.Path(premap.__file__).resolve().parents[2]
BACKEND = APP.parent


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return settle_golden.envelope_mod.load(GOLDEN / "rne_envelope.json")


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


@pytest.fixture
def pre_map(golden_envelope) -> dict:
    """The compact synthetic Pre map test_phase3_premap.py already builds."""
    return premap_golden._build(golden_envelope)


# ---------------------------------------------------------------------------
# the golden replay provider (shared with the runner golden)


def prequestions_replay_provider(golden: dict):
    """Answer the plan with the recorded plan, and each concept's authoring
    with the recorded questions for exactly that concept.

    The fixture names its pre-concepts by TITLE and the provider resolves
    them against the request's own payload — the shape rne_place.json and
    rne_preanalysis.json already use, so the fixture never hard-codes a
    positional mint.
    """

    plans = {row["pre_concept"]: row for row in golden["plans"]}

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prequestions.plan":
            rows = []
            for entry in request.get("pre_concepts") or []:
                title = str(entry.get("concept_title") or "")
                assert title in plans, f"no recorded plan for {title!r}"
                recorded = plans[title]
                rows.append({
                    "pre_concept_id": entry["pre_concept_id"],
                    "total": recorded["total"],
                    "split": copy.deepcopy(recorded["split"]),
                    "rationale": recorded["rationale"],
                })
            return {"plans": rows}
        title = str(
            (request.get("pre_concept") or {}).get("concept_title") or ""
        )
        assert title in golden["questions"], (
            f"no recorded questions for {title!r}"
        )
        return {"questions": copy.deepcopy(golden["questions"][title])}

    return provider


# ---------------------------------------------------------------------------
# the synthetic provider


DEFAULT_PLAN = {
    "PRC-0001": {
        "total": 6,
        "split": [
            {"tier": "Basic", "count": 4},
            {"tier": "Intermediate", "count": 2},
        ],
        "rationale": (
            "the fundamental is one definition and the single "
            "post-learning concept needing it asks only that the learner "
            "can state who holds final authority, so six questions cover "
            "it without repetition"
        ),
    },
    "PRC-0002": {
        "total": 2,
        "split": [{"tier": "Basic", "count": 2}],
        "rationale": (
            "reading a key is a single procedure; two questions exercise "
            "it and a third would be the same question again"
        ),
    },
}


def _plan_response(plans: dict, request: dict) -> dict:
    wanted = [
        str(row.get("pre_concept_id") or "")
        for row in request.get("pre_concepts") or []
    ]
    return {"plans": [
        {"pre_concept_id": concept_id, **copy.deepcopy(plans[concept_id])}
        for concept_id in wanted
        if concept_id in plans
    ]}


def _questions_for(concept_id: str, count: int) -> list[dict]:
    return [
        {
            "question_id": question_id,
            "question_text": (
                f"Question {index} authored for {concept_id}: state, in your "
                "own words, what the fundamental requires."
            ),
            "answer": f"The complete expected answer for {question_id}.",
            "rationale": "checks the learner already holds the prerequisite",
        }
        for index, question_id in enumerate(
            prequestions.mint_question_ids(count), start=1
        )
    ]


def _provider(plans=None, author=None):
    plans = DEFAULT_PLAN if plans is None else plans

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prequestions.plan":
            return _plan_response(plans, request)
        concept_id = str(
            (request.get("pre_concept") or {}).get("pre_concept_id") or ""
        )
        total = int((request.get("coverage_plan") or {}).get("total") or 0)
        if author is not None:
            return author(request, concept_id, total)
        return {"questions": _questions_for(concept_id, total)}

    return provider


def _build(golden_envelope, pre_map, **kwargs):
    kwargs.setdefault("provider", _provider())
    kwargs.setdefault("critic", _verified_critic)
    kwargs.setdefault("store", kernel.DecisionStore())
    return prequestions.build(golden_envelope, pre_map, **kwargs)


# ---------------------------------------------------------------------------
# the happy path


def test_every_pre_concept_gets_the_plan_it_authored(
    golden_envelope, pre_map
):
    result = _build(golden_envelope, pre_map)
    assert set(result["plans"]) == {"PRC-0001", "PRC-0002"}
    assert result["plans"]["PRC-0001"]["total"] == 6
    assert result["plans"]["PRC-0002"]["total"] == 2
    assert [len(rows) for rows in result["questions"].values()] == [6, 2]
    assert result["blocked"] == {}
    # The durable id says which pre-concept the question belongs to, and
    # is unique across the whole map.
    minted = [
        row["pre_question_id"]
        for rows in result["questions"].values()
        for row in rows
    ]
    assert len(minted) == len(set(minted)) == 8
    assert minted[0] == "PRC-0001-PRQ-0001"


# ---------------------------------------------------------------------------
# Q4 · the rationale is unconditional, and 40 lives nowhere in code


def test_a_plan_of_exactly_forty_still_must_carry_a_rationale(
    golden_envelope, pre_map
):
    """The heart of spec T6.

    A plan of exactly the doc's stated norm is not exempt: the rationale
    is required unconditionally, which is precisely why no code compares
    a plan to a norm to decide whether one is owed.
    """
    at_the_norm = {
        "PRC-0001": {
            "total": 40,
            "split": [
                {"tier": "Basic", "count": 20},
                {"tier": "Intermediate", "count": 20},
            ],
            "rationale": "",
        },
        "PRC-0002": DEFAULT_PLAN["PRC-0002"],
    }
    checker = prequestions._plan_checker(["PRC-0001", "PRC-0002"])
    defects = checker(_plan_response(at_the_norm, {"pre_concepts": [
        {"pre_concept_id": "PRC-0001"}, {"pre_concept_id": "PRC-0002"},
    ]}))
    assert any("empty rationale" in defect for defect in defects), defects
    assert all("PRC-0002" not in defect for defect in defects)

    # …and with a rationale it passes, at forty exactly as at six.
    at_the_norm["PRC-0001"]["rationale"] = (
        "this prerequisite carries three distinct procedures and every "
        "post-learning concept in the chapter needs all three"
    )
    assert checker(_plan_response(at_the_norm, {"pre_concepts": [
        {"pre_concept_id": "PRC-0001"}, {"pre_concept_id": "PRC-0002"},
    ]})) == []


def test_a_plan_of_six_must_carry_a_rationale_on_the_same_terms(
    golden_envelope, pre_map
):
    """Symmetry is the point: the requirement does not key on the size."""
    thin = {
        "PRC-0001": {**DEFAULT_PLAN["PRC-0001"], "rationale": ""},
        "PRC-0002": DEFAULT_PLAN["PRC-0002"],
    }
    checker = prequestions._plan_checker(["PRC-0001", "PRC-0002"])
    defects = checker(_plan_response(thin, {"pre_concepts": [
        {"pre_concept_id": "PRC-0001"}, {"pre_concept_id": "PRC-0002"},
    ]}))
    assert any("empty rationale" in defect for defect in defects), defects


def _numeric_constants(node: ast.AST) -> list[int]:
    return [
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant)
        and isinstance(sub.value, int)
        and not isinstance(sub.value, bool)
    ]


def test_no_literal_forty_in_any_validator_anywhere_in_the_backend():
    """Q4's "no 40 in any validator", pinned over the whole surface.

    Mirrors tests/test_phase3_place.py's printer-position pin in shape: a
    structural absence, asserted over every function in ``app/`` and
    ``aegis_pipeline/`` whose job is to check, validate or verify —
    nested checker closures included, since ``ast.walk`` descends into
    them. The last literal-40 validator in this repo lived in the
    pre-learning CLI (``concept_mapping_to_prelearning.py``'s
    "Types section too short or empty"), which step 7 retired with the
    quota engine; the surface has been empty since, and this keeps it so.
    """
    import warnings

    offenders: list[str] = []
    roots = [APP, BACKEND / "aegis_pipeline"]
    for path in sorted(
        entry for root in roots for entry in root.rglob("*.py")
    ):
        try:
            with warnings.catch_warnings():
                # Legacy modules carry invalid escape sequences; parsing
                # them to prove a structural absence must not add
                # warnings to the suite.
                warnings.simplefilter("ignore", DeprecationWarning)
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - no unparsable file today
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            name = node.name.lower()
            if not any(
                word in name for word in ("check", "valid", "verif")
            ):
                continue
            if 40 in _numeric_constants(node):
                offenders.append(f"{path.name}::{node.name}")
    assert offenders == []


def test_no_numeric_coverage_target_lives_anywhere_in_the_module():
    """Contract v2.0 §8 (register Q26): no quotas, stated once, as prose.

    The former recorded target ("about 5 questions per concept", Q20) is
    gone: no numeric norm — current or superseded — is a literal anywhere
    in the module, no string quotes one, and the coverage rule lives ONLY
    inside ``_plan_rules`` as labelled COVERAGE prose with the 2026-08-21
    diagnostic posture alongside it — never in a constant, a default, a
    threshold, a comparison, or a prompt.
    """
    source = pathlib.Path(prequestions.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, bool):
            continue
        if node.value in (5, 10, 40):
            raise AssertionError(f"numeric norm literal {node.value!r}")
        if isinstance(node.value, str):
            assert "about 5 questions" not in node.value
            assert "about 10 questions" not in node.value
            assert "questions per concept" not in node.value
    rules = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_plan_rules"
    )
    prose = "".join(
        sub.value for sub in ast.walk(rules)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    )
    assert "COVERAGE (Master Governing Contract v2.0 §8" in prose
    assert "there are no quotas" in prose
    assert "complete diagnostic coverage" in prose
    assert "no target count, no minimum and no maximum" in prose
    assert "never padded" in prose
    assert "at least one diagnostic question" in prose
    assert "POSTURE (owner steer, 2026-08-21)" in prose
    assert "minimum coverage that genuinely verifies the prerequisite" in prose
    # The coverage rule is stated in _plan_rules and nowhere else.
    elsewhere = {
        node.value[:60] for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "complete diagnostic coverage" in node.value
    } - {
        sub.value[:60] for sub in ast.walk(rules)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    } - {(ast.get_docstring(tree, clean=False) or "")[:60]}
    assert elsewhere == set()


def test_the_registered_prompts_do_not_carry_the_norm():
    """The norm rides the payload's rules, in one place, never a prompt."""
    from app.services.phase3 import prompts

    for name in (
        "PREQUESTIONS_PLAN_SYSTEM",
        "PREQUESTIONS_AUTHOR_SYSTEM",
        "PREQUESTIONS_CRITIC_SYSTEM",
    ):
        assert "about 5 questions" not in getattr(prompts, name)
        assert "about 10 questions" not in getattr(prompts, name)
        # The diagnostic posture rides the same payload prose as the norm,
        # never a prompt: one location, one provenance, one pin.
        assert "minimum coverage that genuinely verifies" not in getattr(
            prompts, name)


def test_the_coverage_rule_names_no_practice_of_any_school_board_or_grade():
    """The coverage prose must not invent a reference point.

    Contract v2.0 §8 states the rule without a number, and the prose
    says so in as many words: no figure from any other concept, school,
    board or grade is a reference point. Presenting any figure as an
    observed norm — above all of the reference school, whose accepted
    workbooks Q5 makes the schema of record — would turn it into the
    strongest anchor available, which is precisely the hazard the
    unconditional rationale and the critic's ANCHORING dimension exist to
    hold off.
    """
    prose = prequestions._plan_rules("")
    assert "left to your judgment" in prose
    assert (
        "no figure from any other concept, school, board or grade is a "
        "reference point"
    ) in prose
    for invented in (
        "reference school", "'s usual", " usual ", "typical", "average",
        "recorded target", "reference point, not a rule",
    ):
        assert invented not in prose, invented


def test_the_new_prompts_join_the_instruction_sets_hash():
    """A changed authoring prompt must never replay old questions.

    The system prompt rides outside the payload, so it is in neither the
    payload hash nor the envelope seal; the only thing that carries it
    into a decision key is The Architect's frozen core.
    """
    from app.services import instruction_architect as architect
    from app.services.phase3 import prompts

    listed = set(architect._FROZEN_CORE_PHASE3_CONSTANTS)
    for name in (
        "PREQUESTIONS_PLAN_SYSTEM",
        "PREQUESTIONS_AUTHOR_SYSTEM",
        "PREQUESTIONS_CRITIC_SYSTEM",
    ):
        assert name in listed, name
        base = architect.empty_set_sha256()
        original = getattr(prompts, name)
        setattr(prompts, name, original + " One more instruction.")
        try:
            assert architect.empty_set_sha256() != base, name
        finally:
            setattr(prompts, name, original)
        assert architect.empty_set_sha256() == base


def test_the_critic_is_asked_the_anchoring_question_by_name():
    """Anchoring is the real hazard, so it is an explicit named dimension."""
    from app.services.phase3 import prompts

    text = prompts.PREQUESTIONS_CRITIC_SYSTEM
    assert "ANCHORING" in text
    assert "anchored on a stated norm" in text
    assert "CORRECT outcome, not a defect" in text
    # …and paraphrase-of-a-source-question is the other named dimension.
    assert "PARAPHRASE" in text


# ---------------------------------------------------------------------------
# thin, empty, never padded


def test_a_literary_plan_slot_naming_task_qids_never_refuses_the_plan(
    golden_envelope, pre_map,
):
    """[job 64] The same leak the Pre map hit: the Architect's
    ``language_topology_plan`` slot routes ``task_qids``, and the
    question-plan payload runs the SAME no-extraction guard. The suffix
    is redacted at the mint, so questions author instead of the whole
    lane refusing."""
    import copy as _copy
    import json as _json

    from app.services.phase3 import premap as premap_mod
    from tests import test_phase3_settle_golden as settle_golden

    env = _copy.deepcopy(dict(golden_envelope))
    env["metadata"] = {
        **env["metadata"],
        "instruction_slots": {
            "language_topology_plan": _json.dumps({
                "topics": [{
                    "display_name": "A New Year in Which Everyone Takes Part",
                    "task_qids": ["QINV-0003", "QINV-0004"],
                }],
            }),
        },
    }
    env["envelope_sha256"] = settle_golden.envelope_mod.seal_sha256(env)

    seen: list[dict] = []
    base = _provider()

    def capturing(request: dict) -> dict:
        if str(request.get("stage")) == "prequestions.plan":
            seen.append(_copy.deepcopy(request))
        return base(request)

    result = _build(env, pre_map, provider=capturing)
    assert result["questions"]
    assert not result.get("refused")
    payload = _json.dumps(seen[0], ensure_ascii=False)
    assert "QINV-0003" not in payload
    assert "QINV-0004" not in payload
    assert "a source question of this chapter" in payload


def test_a_thin_pre_concept_is_planned_small_and_never_padded(
    golden_envelope, pre_map
):
    thin = {
        "PRC-0001": {
            "total": 1,
            "split": [{"tier": "Basic", "count": 1}],
            "rationale": "one definition, one question",
        },
        "PRC-0002": DEFAULT_PLAN["PRC-0002"],
    }
    result = _build(golden_envelope, pre_map, provider=_provider(thin))
    assert len(result["questions"]["PRC-0001"]) == 1
    assert result["blocked"] == {}


def test_an_empty_plan_is_legal_and_spends_no_authoring_call(
    golden_envelope, pre_map
):
    empty = {
        "PRC-0001": {
            "total": 0,
            "split": [],
            "rationale": (
                "the fundamental is a single word the learner either knows "
                "or does not; a question about it would be a vocabulary "
                "check the chapter itself performs"
            ),
        },
        "PRC-0002": DEFAULT_PLAN["PRC-0002"],
    }
    seen: list[str] = []

    def counting(request: dict) -> dict:
        seen.append(str(request.get("stage") or ""))
        return _provider(empty)(request)

    result = _build(golden_envelope, pre_map, provider=counting)
    assert result["plans"]["PRC-0001"]["total"] == 0
    assert "PRC-0001" not in result["questions"]
    assert seen.count("prequestions.author") == 1  # PRC-0002 only
    assert result["blocked"] == {}


def test_an_empty_pre_map_spends_nothing_at_all(golden_envelope):
    def never(_request: dict) -> dict:  # pragma: no cover - must not run
        raise AssertionError("an empty Pre map must not spend a decision")

    result = prequestions.build(
        golden_envelope, {"rows": []}, provider=never,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )
    assert result == {
        "plans": {}, "questions": {}, "blocked": {},
        "review_flags": {}, "decision_flags": {},
    }


# ---------------------------------------------------------------------------
# calibration is evidence, never a branch


def test_calibration_and_the_architects_slots_reach_both_payloads(
    golden_envelope, pre_map
):
    """Steer point 2, mechanically.

    The chapter's own board/grade/subject/unit ride the payload, and The
    Architect's assembled instruction set rides the rules through
    ``instruction_rules_suffix`` with the PRE-lane slots — including the
    board/publication slot the Pre lane added.
    """
    env = copy.deepcopy(dict(golden_envelope))
    metadata = dict(env.get("metadata") or {})
    metadata["instruction_slots"] = {
        "subject_topology_guidance": "history topology guidance",
        "grade_band_vocabulary": "grade-band vocabulary for this run",
        "board_publication_conventions": "board conventions for this run",
        # The Architect's chapter-CONTEXT slot: a list of short cautions,
        # which is why the renderer joins entries instead of printing a
        # Python repr into the instructions.
        "chapter_cautions": [
            "figure-dependent tasks in the exercises",
            "mixed languages in the source",
        ],
    }
    env["metadata"] = metadata
    env["envelope_sha256"] = settle_golden.envelope_mod.seal_sha256(env)
    payloads: list[dict] = []

    def capturing(request: dict) -> dict:
        payloads.append(copy.deepcopy(request))
        return _provider()(request)

    _build(env, pre_map, provider=capturing)
    plan = next(p for p in payloads if p["stage"] == "prequestions.plan")
    author = next(p for p in payloads if p["stage"] == "prequestions.author")
    for payload in (plan, author):
        for slot in (
            "history topology guidance",
            "grade-band vocabulary for this run",
            "board conventions for this run",
            # …and the five dimensions the steer names include CONTEXT,
            # so the chapter-cautions slot reaches the question lane too,
            # rendered as text rather than as a repr.
            "figure-dependent tasks in the exercises; mixed languages",
        ):
            assert slot in payload["rules"], payload["stage"]
        assert "[" not in payload["rules"].split("RUN INSTRUCTIONS")[-1]
        assert set(payload["chapter"]) == {
            "board", "grade", "subject", "unit", "chapter_title",
        }
    assert "level, grade, subject, board and context" in author["rules"]


def test_no_per_grade_or_per_subject_branch_exists_in_the_code():
    """A curriculum switch is Rule 1's forbidden judgment in a hat.

    Every branch condition in the module is unparsed and checked: none
    of them reads a grade, a subject or a board value. Calibration is
    evidence the model reasons over, and the code never looks at it.
    """
    # The same token pin premap.py carries, on the same terms: no board
    # name and no concrete grade appears in the EXECUTABLE source, so the
    # retired board-substring guidance switch cannot creep back in
    # through a prompt string either.
    source = premap_golden._code_only(prequestions)
    for token in ("CBSE", "ICSE", "IGCSE", "NCERT", "Grade 6", "Class 10"):
        assert token not in source

    tree = ast.parse(
        pathlib.Path(prequestions.__file__).read_text(encoding="utf-8")
    )
    banned = (
        "grade", "subject", "board", "class", "cbse", "icse", "ncert",
        "maths", "mathematics", "science", "english",
    )
    for node in ast.walk(tree):
        tests = []
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            tests.append(node.test)
        elif isinstance(node, ast.Compare):
            tests.append(node)
        elif isinstance(node, ast.comprehension):
            tests.extend(node.ifs)
        for test in tests:
            text = ast.unparse(test).lower()
            for word in banned:
                assert word not in text, ast.unparse(test)


# ---------------------------------------------------------------------------
# no extraction


@pytest.mark.parametrize("field", ["question_text", "answer", "rationale"])
def test_a_source_qid_in_a_generated_question_is_refused(
    golden_envelope, pre_map, field
):
    """Fail-closed, over the question, its answer AND its rationale.

    The guard is ``premap._refuse_source_qids`` — the same one, reused,
    not a second implementation.
    """
    qid = premap.inventory_qids(golden_envelope)[0]

    def leaking(request, concept_id, total):
        rows = _questions_for(concept_id, total)
        if concept_id == "PRC-0001":
            rows[0][field] = f"{rows[0][field]} (see {qid})"
        return {"questions": rows}

    with pytest.raises(premap.PreExtractionError) as caught:
        _build(
            golden_envelope, pre_map, provider=_provider(author=leaking),
        )
    assert qid in str(caught.value)
    assert "generated pre-learning question" in str(caught.value)


def test_a_source_qid_in_a_plan_rationale_is_refused(
    golden_envelope, pre_map
):
    qid = premap.inventory_qids(golden_envelope)[0]
    plans = copy.deepcopy(DEFAULT_PLAN)
    plans["PRC-0001"]["rationale"] += f" (as {qid} shows)"
    with pytest.raises(premap.PreExtractionError):
        _build(golden_envelope, pre_map, provider=_provider(plans))


def test_a_critic_naming_a_source_qid_flags_and_never_kills_the_run(
    golden_envelope, pre_map
):
    """Q10, and the ordering that keeps it true.

    The critic is asked whether a question is a source question reworded;
    it may answer most usefully by naming one. The guard runs over
    AUTHORED content BEFORE any flag is stamped, so an auditor's dissent
    can never become the hardest gate in the pipeline.
    """
    qid = premap.inventory_qids(golden_envelope)[0]

    def naming_critic(_request: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.4,
            "issues": [f"PRQ-0001 reads like {qid} reworded"],
        }

    result = _build(golden_envelope, pre_map, critic=naming_critic)
    assert result["questions"]["PRC-0001"]
    flat = json.dumps(result["review_flags"], ensure_ascii=False)
    assert qid in flat
    # …and the QID is nowhere in the artefact the guard protects.
    assert qid not in json.dumps(
        {"plans": result["plans"], "questions": result["questions"]},
        ensure_ascii=False,
    )


def test_the_payloads_carry_no_source_block_and_no_question_inventory(
    golden_envelope, pre_map
):
    """Generated, not extracted: the lane is never shown a source question.

    The evidence is the Pre lane's own — the pre-concept's teaching, its
    prerequisite texts and what needs it. The chapter's source blocks and
    its question/task inventory are structurally absent.
    """
    payloads: list[dict] = []

    def capturing(request: dict) -> dict:
        payloads.append(copy.deepcopy(request))
        return _provider()(request)

    _build(golden_envelope, pre_map, provider=capturing)
    assert payloads
    for payload in payloads:
        raw = json.dumps(payload, ensure_ascii=False)
        for banned in ("QINV-", "inventory", "source_block", "BLK-"):
            assert banned not in raw, (payload["stage"], banned)


# ---------------------------------------------------------------------------
# plan ↔ output: flags or the Fixer, never a raise


def test_a_shortfall_routes_to_the_fixer(golden_envelope, pre_map):
    def short(request, concept_id, total):
        if request.get("fixer"):  # pragma: no cover - shape guard
            raise AssertionError("the fixer answers on its own seam")
        return {"questions": _questions_for(concept_id, max(total - 2, 0))}

    def fixer(request: dict) -> dict:
        original = request["original_payload"]
        concept_id = original["pre_concept"]["pre_concept_id"]
        total = int(original["coverage_plan"]["total"])
        return {
            "questions": _questions_for(concept_id, total),
            "rationale": "authored the missing questions to the plan",
        }

    result = _build(
        golden_envelope, pre_map, provider=_provider(author=short),
        fixer=fixer,
    )
    assert len(result["questions"]["PRC-0001"]) == 6
    assert result["blocked"] == {}
    flags = result["review_flags"]["PRC-0001"]
    assert any(flag.startswith("fixer: blocked=") for flag in flags)
    assert any("own coverage plan asked for 6" in flag for flag in flags)


def test_a_shortfall_the_fixer_cannot_close_flags_and_never_raises(
    golden_envelope, pre_map
):
    """CLAUDE.md: finished work always ships.

    Taking a chapter down over one concept's questions would discard a
    finished Post map and a finished Pre map. The concept is recorded as
    blocked, loudly, and the run completes.
    """
    def short(request, concept_id, total):
        if concept_id != "PRC-0001":
            return {"questions": _questions_for(concept_id, total)}
        return {"questions": _questions_for(concept_id, max(total - 2, 0))}

    def useless_fixer(request: dict) -> dict:
        original = request["original_payload"]
        concept_id = original["pre_concept"]["pre_concept_id"]
        return {"questions": _questions_for(concept_id, 1)}

    result = _build(
        golden_envelope, pre_map, provider=_provider(author=short),
        fixer=useless_fixer,
    )
    assert "PRC-0001" in result["blocked"]
    assert "PRC-0001" not in result["questions"]
    assert any(
        "could not be authored to contract" in flag
        for flag in result["review_flags"]["PRC-0001"]
    )
    # The other concept is untouched: one blocked concept is not a lane
    # failure.
    assert "PRC-0002" in result["questions"]


def test_an_overshoot_is_a_defect_too(golden_envelope, pre_map):
    """"Never padded" is checked in both directions."""
    checker = prequestions._author_checker("PRC-0001", 6)
    defects = checker({"questions": _questions_for("PRC-0001", 9)})
    assert any("asked for 6" in defect for defect in defects), defects


def test_a_plan_the_fixer_cannot_close_blocks_every_concept_and_never_raises(
    golden_envelope, pre_map
):
    """The plan half obeys the same rule as the authoring half.

    The plan is ONE decision over every pre-concept, so leaving it
    unwrapped would let a single unreconcilable split end a run holding a
    finished Post map AND a finished Pre map — the outcome the authoring
    half's own comment says CLAUDE.md forbids, one decision earlier.
    Every pre-concept is recorded blocked and flagged, and the run
    completes.
    """
    def no_rationale(request: dict) -> dict:
        response = _plan_response(DEFAULT_PLAN, request)
        for row in response["plans"]:
            row["rationale"] = ""
        return response

    def useless_fixer(request: dict) -> dict:
        return no_rationale(request["original_payload"])

    # A plan provider that always answers without a rationale, and a
    # Fixer that cannot do better. No authoring provider is wired at all:
    # a run with no plan must not spend one.
    result = prequestions.build(
        golden_envelope, pre_map, provider=no_rationale,
        critic=_verified_critic, store=kernel.DecisionStore(),
        fixer=useless_fixer,
    )
    assert result["plans"] == {} and result["questions"] == {}
    assert set(result["blocked"]) == {"PRC-0001", "PRC-0002"}
    for concept_id in ("PRC-0001", "PRC-0002"):
        assert any(
            "could not be authored to contract" in flag
            for flag in result["review_flags"][concept_id]
        )
    # R4: what did not ship is named per concept, never dropped silently.
    assert "empty rationale" in result["blocked"]["PRC-0001"]


def test_a_recorded_block_never_carries_a_source_question_id(
    golden_envelope, pre_map
):
    """The error path is closed too (steer point 3).

    ``blocked`` and its review flag are outside the fail-closed guarded
    surface on purpose — refusing there would turn one blocked concept
    into a refusal of the whole lane. So the identity property is kept
    the other way: the recorded block is redacted, and the authoring
    checker names positions rather than echoing the ids it was handed.
    """
    qid = premap.inventory_qids(golden_envelope)[0]

    def echoes_a_source_qid(request, concept_id, total):
        rows = _questions_for(concept_id, total)
        if concept_id == "PRC-0001":
            rows[0]["question_id"] = qid
        return {"questions": rows}

    result = _build(
        golden_envelope, pre_map,
        provider=_provider(author=echoes_a_source_qid),
    )
    assert "PRC-0001" in result["blocked"]
    recorded = json.dumps(result, ensure_ascii=False)
    assert qid not in recorded
    # …and the other concept still ships: one blocked concept is not a
    # lane failure.
    assert "PRC-0002" in result["questions"]


def test_the_authoring_checker_does_not_promise_an_unreachable_route():
    """The re-ask feedback must name a move the model can actually make.

    The authoring decision cannot amend the coverage plan — that is a
    separate, already-recorded decision — so telling it to "say in the
    plan why it should be different" would misdirect every correction
    attempt toward a seam it is not standing at.
    """
    checker = prequestions._author_checker("PRC-0001", 6)
    defects = checker({"questions": _questions_for("PRC-0001", 4)})
    joined = " ".join(defects)
    assert "own coverage plan asked for 6" in joined
    assert "in the plan" not in joined
    assert "cannot be amended from here" in joined


# ---------------------------------------------------------------------------
# the tier / difficulty trap


def test_the_plan_split_reads_the_group_tier_vocabulary_not_difficulty():
    from app.services import assessment_grouping
    from app import bulk_import

    assert prequestions.tier_names() == tuple(assessment_grouping.TIER_CODES)
    assert prequestions.tier_names() == (
        "Basic", "Intermediate", "Advanced"
    )
    # A DIFFERENT vocabulary for a different axis — never conflated.
    assert set(prequestions.tier_names()).isdisjoint(
        bulk_import.DIFFICULTY_LEVELS
    )
    # And the vocabulary is READ from its owner rather than re-typed as a
    # constant here, so the two can never drift: no assignment in the
    # module holds a tier name, and no branch compares against one. The
    # rules PROSE names the tiers, which is what tells the model what
    # vocabulary to answer in — prose is evidence, not code.
    tree = ast.parse(
        pathlib.Path(prequestions.__file__).read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Compare)):
            text = ast.unparse(node)
            for tier in prequestions.tier_names():
                assert f"'{tier}'" not in text, text
                assert f'"{tier}"' not in text, text
    # The workbook's difficulty vocabulary appears nowhere at all.
    source = pathlib.Path(prequestions.__file__).read_text(encoding="utf-8")
    for level in bulk_import.DIFFICULTY_LEVELS:
        assert f"'{level}'" not in source
        assert f'"{level}"' not in source


def test_an_authored_question_carries_no_tier_and_no_difficulty(
    golden_envelope, pre_map
):
    """The level verdict belongs to the assessment lane and is not pre-empted."""
    result = _build(golden_envelope, pre_map)
    for rows in result["questions"].values():
        for row in rows:
            assert set(row) == {
                "pre_question_id", "question_id", "pre_concept_id",
                "question_text", "answer", "rationale",
            }
    from app.services.phase3 import prompts

    assert "tier or a difficulty" in prompts.PREQUESTIONS_AUTHOR_SYSTEM


def test_the_plans_split_is_checked_against_its_own_total_only():
    """The one arithmetic in the checker is model-number vs model-number."""
    checker = prequestions._plan_checker(["PRC-0001"])
    defects = checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 10,
        "split": [{"tier": "Basic", "count": 4}],
        "rationale": "stated",
    }]})
    assert any("split sums to 4" in defect for defect in defects), defects
    assert checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 4,
        "split": [{"tier": "Basic", "count": 4}],
        "rationale": "stated",
    }]}) == []


def test_an_unplanned_pre_concept_is_a_contract_defect():
    """R4: a pre-concept nobody planned is a learner's questions lost."""
    checker = prequestions._plan_checker(["PRC-0001", "PRC-0002"])
    defects = checker({"plans": [{
        "pre_concept_id": "PRC-0001",
        "total": 1,
        "split": [{"tier": "Basic", "count": 1}],
        "rationale": "stated",
    }]})
    assert any("unplanned pre-concept(s): PRC-0002" in d for d in defects)


# ---------------------------------------------------------------------------
# decide-once


def test_a_second_identical_run_spends_nothing(golden_envelope, pre_map):
    store = kernel.DecisionStore()
    calls: list[str] = []

    def counting(request: dict) -> dict:
        calls.append(str(request.get("stage") or ""))
        return _provider()(request)

    first = _build(
        golden_envelope, pre_map, provider=counting, store=store
    )
    spent = len(calls)
    assert spent == 3  # one plan + two authoring decisions
    second = _build(
        golden_envelope, pre_map, provider=counting, store=store
    )
    assert len(calls) == spent
    assert second["questions"] == first["questions"]
    assert second["plans"] == first["plans"]


# ---------------------------------------------------------------------------
# the recorded golden (the real chain's 15 pre-concepts)


@pytest.fixture(scope="module")
def golden_prequestions() -> dict:
    return json.loads(
        (GOLDEN / "rne_prequestions.json").read_text(encoding="utf-8")
    )


def test_the_recorded_plan_varies_with_the_evidence_and_never_anchors(
    golden_prequestions
):
    """The fixture IS the Q4 evidence, so read it as such.

    Fifteen prerequisites, fifteen totals running from 2 to 10, every one
    of them carrying its own rationale, and not one of them at the doc's
    stated norm. That is what "an adaptive target, neither quota nor
    maximum" looks like when the plan is led by the evidence rather than
    anchored on the number.
    """
    plans = golden_prequestions["plans"]
    assert len(plans) == 15
    totals = sorted(row["total"] for row in plans)
    assert totals[0] == 2 and totals[-1] == 10
    assert len(set(totals)) > 4, "a flat plan across a varied map is anchoring"
    for row in plans:
        assert row["rationale"].strip(), row["pre_concept"]
        assert sum(entry["count"] for entry in row["split"]) == row["total"]
        for entry in row["split"]:
            assert entry["tier"] in prequestions.tier_names()
    # The thinnest pre-concept is the one nothing in the chapter needs,
    # and it is planned small rather than padded.
    thin = next(
        row for row in plans
        if row["pre_concept"] == "Finding Information Beyond the Textbook"
    )
    assert thin["total"] == 2


def test_the_golden_replays_the_whole_map_and_matches_its_own_plan(
    golden_envelope, golden_prequestions, pre_map
):
    """A shape-only replay over the synthetic map is not enough: the
    recorded fixture is exercised end to end by the runner golden."""
    counts = {
        row["pre_concept"]: row["total"]
        for row in golden_prequestions["plans"]
    }
    for title, rows in golden_prequestions["questions"].items():
        assert len(rows) == counts[title], title
        assert [row["question_id"] for row in rows] == (
            prequestions.mint_question_ids(len(rows))
        )
        for row in rows:
            assert set(row) == {
                "question_id", "question_text", "answer", "rationale",
            }
            for field in ("question_text", "answer", "rationale"):
                assert row[field].strip()


def test_no_source_qid_appears_anywhere_in_the_recorded_golden(
    golden_envelope, golden_prequestions
):
    """The fixture is a Pre artefact and the steer covers it too."""
    raw = json.dumps(golden_prequestions, ensure_ascii=False)
    for qid in premap.inventory_qids(golden_envelope):
        assert qid not in raw
