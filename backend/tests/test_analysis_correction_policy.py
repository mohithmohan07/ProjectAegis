"""Q68: a paired ``correction`` on every misconception/error-analysis item.

The Science reviewer rewrote 34 concepts to "(1) <false statement>.
Correction: <what is true>." — the inventory pass asked for a text only and
the composer joined the texts unnumbered. Under the envelope-frozen
``analysis_correction_policy`` both lanes' authors carry the correction, the
checker refuses an empty one (mechanics), and the composer numbers the
pairs. A sealed envelope without the key replays byte for byte.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services import analysis_correction_policy as policy
from app.services import concept_refiner as cr
from app.services import generation
from app.services.phase3 import analyse, assemble, kernel, preanalyse
from tests import test_phase3_analyse as analyse_golden
from tests import test_phase3_preanalyse as preanalyse_golden
from tests import test_phase3_premap as premap_golden
from tests import test_phase3_settle_golden as settle_golden

GOLDEN = settle_golden.GOLDEN


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return settle_golden.envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def golden_analysis() -> dict:
    return json.loads((GOLDEN / "rne_analysis.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden_rows() -> list[dict]:
    return json.loads(
        (GOLDEN / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]


def _stamped(env: dict) -> dict:
    new_env = copy.deepcopy(env)
    new_env["metadata"][policy.KEY] = policy.VERSION
    new_env["envelope_sha256"] = settle_golden.envelope_mod.seal_sha256(new_env)
    return new_env


# ---------------------------------------------------------------------------
# the policy module and the checkers


def test_policy_helpers_are_silent_without_the_key():
    assert policy.active({}) is False
    assert policy.active(None) is False
    assert policy.fields({}) == {}
    assert policy.rules_sentence({}) == ""
    assert policy.author_instruction({}) == ""
    assert policy.critic_instruction({}) == ""
    assert policy.suffix({}) == ""
    for shape in ({policy.KEY: policy.VERSION}, {"metadata": {policy.KEY: policy.VERSION}}):
        assert policy.active(shape) is True
        assert policy.fields(shape) == {policy.KEY: policy.VERSION}
        assert policy.rules_sentence(shape) == policy.AUTHOR_INSTRUCTION + " "
        assert policy.author_instruction(shape) == "\n" + policy.AUTHOR_INSTRUCTION
        assert policy.critic_instruction(shape) == "\n" + policy.CRITIC_INSTRUCTION
        assert policy.suffix(shape) == ";" + policy.VERSION
    assert "paired one to one" in policy.AUTHOR_INSTRUCTION
    assert "never a bare negation or restatement" in policy.AUTHOR_INSTRUCTION
    assert "untrue" in policy.CRITIC_INSTRUCTION


@pytest.mark.parametrize("checker, item_id", [
    (analyse._inventory_checker, "LA-0001"),
    (preanalyse._inventory_checker, "PLA-0001"),
])
def test_inventory_checkers_require_a_paired_correction_only_under_the_policy(
    checker, item_id,
):
    bare = {"item_id": item_id, "kind": "misconception", "text": "a belief"}
    strict = checker(require_correction=True)
    assert f"{item_id} has empty correction" in strict({"items": [bare]})
    assert f"{item_id} has empty correction" in strict(
        {"items": [{**bare, "correction": "   "}]}
    )
    assert strict({"items": [{**bare, "correction": "what is true"}]}) == []
    # The default checker is byte-identical to today's acceptance.
    assert checker()({"items": [bare]}) == []


@pytest.mark.parametrize("call, instruction", [
    (analyse._live_build, policy.AUTHOR_INSTRUCTION),
    (preanalyse._live_build, policy.AUTHOR_INSTRUCTION),
    (preanalyse._live_critic, policy.CRITIC_INSTRUCTION),
    (preanalyse._live_allot, None),
])
def test_live_adapters_only_adopt_the_stamped_correction_policy(
    monkeypatch, call, instruction,
):
    seen = []

    def record(system, user, **kwargs):
        seen.append((system, user))
        return {}

    monkeypatch.setattr(generation, "_openai_json", record)
    call({})
    call({policy.KEY: policy.VERSION})
    call({})
    assert seen[0] == seen[2]
    if instruction is None:
        assert seen[1][0] == seen[0][0]
    else:
        assert seen[1][0] == seen[0][0] + "\n" + instruction
    from app.services.phase3 import prompts

    for constant in (
        prompts.ANALYSE_INVENTORY_SYSTEM, prompts.PREANALYSE_INVENTORY_SYSTEM,
        prompts.ANALYSE_CRITIC_SYSTEM, prompts.PREANALYSE_CRITIC_SYSTEM,
    ):
        assert policy.AUTHOR_INSTRUCTION not in constant
        assert policy.CRITIC_INSTRUCTION not in constant


def test_post_critic_adapter_adopts_the_stamped_policy_and_the_allotter_does_not(
    monkeypatch,
):
    seen = []

    def record(system, user, **kwargs):
        seen.append(system)
        return {}

    monkeypatch.setattr(generation, "_openai_json", record)
    analyse._cached_call({"stage": "analyse.review", "items": []}, critic=True)
    analyse._cached_call(
        {"stage": "analyse.review", "items": [], policy.KEY: policy.VERSION},
        critic=True,
    )
    analyse._cached_call({"stage": "analyse.allot", "items": []}, critic=False)
    analyse._cached_call(
        {"stage": "analyse.allot", "items": [], policy.KEY: policy.VERSION},
        critic=False,
    )
    assert seen[1] == seen[0] + "\n" + policy.CRITIC_INSTRUCTION
    assert seen[3] == seen[2]


# ---------------------------------------------------------------------------
# the Post lane


def test_analyse_carries_the_correction_and_keys_the_build_to_the_policy(
    golden_envelope, golden_analysis, golden_rows,
):
    settled = analyse_golden._settled(golden_envelope, golden_rows)
    base = analyse_golden.analyse_replay_provider(golden_analysis)
    calls: list[dict] = []

    def correcting(request: dict) -> dict:
        calls.append(copy.deepcopy(request))
        response = base(request)
        if request.get("stage") == "analyse.inventory":
            for item in response["items"]:
                item["correction"] = "Recorded correction for " + item["item_id"]
        return response

    # A historical envelope: no key, no sentence, the correction carried
    # only because the provider returned one.
    store = kernel.DecisionStore()
    result = analyse.analyse(
        golden_envelope, settled, provider=correcting,
        critic=analyse_golden._verified_critic, store=store,
    )
    build_calls = [c for c in calls if c.get("stage") == "analyse.inventory"]
    assert len(build_calls) == 1
    assert policy.KEY not in build_calls[0]
    assert policy.AUTHOR_INSTRUCTION not in build_calls[0]["rules"]
    assert result["inventory"][0]["correction"] == "Recorded correction for LA-0001"
    before = len(calls)
    analyse.analyse(
        golden_envelope, settled, provider=correcting,
        critic=analyse_golden._verified_critic, store=store,
    )
    assert len(calls) == before, "decide-once: the store answered every stage"

    # The golden provider returns no correction: the historical envelope
    # accepts it exactly as before.
    plain = analyse.analyse(
        golden_envelope, settled,
        provider=analyse_golden.analyse_replay_provider(golden_analysis),
        critic=analyse_golden._verified_critic, store=kernel.DecisionStore(),
    )
    assert all("correction" not in item for item in plain["inventory"])

    # A new envelope names the policy: the payload carries the key and the
    # sentence, the allot requests carry the correction, and the critic
    # sees the key.
    new_env = _stamped(golden_envelope)
    calls.clear()
    critic_payloads: list[dict] = []

    def recording_critic(request: dict) -> dict:
        critic_payloads.append(copy.deepcopy(request))
        return analyse_golden._verified_critic(request)

    analyse.analyse(
        new_env, settled, provider=correcting,
        critic=recording_critic, store=kernel.DecisionStore(),
    )
    build = next(c for c in calls if c.get("stage") == "analyse.inventory")
    assert build[policy.KEY] == policy.VERSION
    assert (
        "never restate one as the other. " + policy.AUTHOR_INSTRUCTION + " "
    ) in build["rules"]
    allots = [c for c in calls if c.get("stage") != "analyse.inventory"]
    assert allots
    assert all(
        item["correction"].startswith("Recorded correction for ")
        for call in allots for item in call["items"]
    )
    assert any(policy.KEY in p for p in critic_payloads)

    # Under the policy the golden provider's correction-less inventory is a
    # defect the bounded corrections cannot repair; without a Fixer the
    # run fails closed naming the item.
    with pytest.raises(kernel.ContractError, match="has empty correction"):
        analyse.analyse(
            new_env, settled,
            provider=analyse_golden.analyse_replay_provider(golden_analysis),
            critic=analyse_golden._verified_critic,
            store=kernel.DecisionStore(),
        )


# ---------------------------------------------------------------------------
# the Pre lane


def test_pre_lane_build_carries_the_correction_and_keys_to_the_policy(
    golden_envelope,
):
    corrected = [
        {**item, "correction": "What is true about " + item["item_id"] + "."}
        for item in preanalyse_golden.INVENTORY
    ]
    result = preanalyse_golden._build(golden_envelope, inventory=corrected)
    details = result["rows"][0]["concept_details"]
    section = details.split("// Misconception/ Error Analysis: ", 1)[1]
    assert section.startswith("Misconceptions: (1) ")
    assert " Correction: What is true about PLA-0001." in section
    assert "; Error Analysis: (1) " in section
    assert " Correction: What is true about PLA-0002." in section

    # The plain inventory renders today's shape, byte for byte.
    plain = preanalyse_golden._build(golden_envelope)
    plain_section = plain["rows"][0]["concept_details"].split(
        "// Misconception/ Error Analysis: ", 1
    )[1]
    assert plain_section.startswith("Misconceptions: The learner may believe")
    assert "; Error Analysis: Students name" in plain_section
    assert "(1)" not in plain_section and "Correction:" not in plain_section

    # A new envelope names the policy on the Pre inventory request.
    new_env = _stamped(golden_envelope)
    requests: list[dict] = []
    base = premap_golden._provider(
        inventory=corrected, allotments=preanalyse_golden.ALLOTMENTS,
    )

    def recording(request: dict) -> dict:
        requests.append(copy.deepcopy(request))
        return base(request)

    preanalyse_golden._build(new_env, inventory=corrected, provider=recording)
    build = next(
        r for r in requests if r.get("stage") == "prelearn.analyse.inventory"
    )
    assert build[policy.KEY] == policy.VERSION
    assert (
        "never restate one as the other. " + policy.AUTHOR_INSTRUCTION + " "
    ) in build["rules"]
    allot = next(
        r for r in requests if r.get("stage") == "prelearn.analyse.allot"
    )
    assert all(item["correction"] for item in allot["items"])


# ---------------------------------------------------------------------------
# the render


def test_stamping_renders_numbered_pairs_per_component_and_keeps_both_kinds():
    rows = analyse_golden._stamp_rows()
    analysis = {
        "inventory": [
            {
                "item_id": "LA-0001",
                "kind": "misconception",
                "text": "Light bends around corners on its own in air",
                "correction": "In air light travels in straight lines; it "
                              "changes direction only at a surface.",
            },
            {
                "item_id": "LA-0002",
                "kind": "misconception",
                "text": "Misconceptions: A mirror adds light of its own.",
                "correction": "A mirror only reflects the light that falls "
                              "on it",
            },
            {
                "item_id": "LA-0003",
                "kind": "error_analysis",
                "text": "The image distance is measured from the object.",
                "correction": "Correction: Measure it from the mirror surface.",
            },
        ],
        "allotments": {
            "LA-0001": "CONCEPT-0001",
            "LA-0002": "CONCEPT-0001",
            "LA-0003": "CONCEPT-0001",
        },
        "review_flags": {},
    }
    allotments, inventory = analyse_golden._stamp(rows, analysis)
    assert allotments == analysis["allotments"]
    assert len(inventory) == 3
    details = rows[0]["concept_details"]
    expected = (
        " // Misconception/ Error Analysis: Misconceptions: "
        "(1) Light bends around corners on its own in air. Correction: In "
        "air light travels in straight lines; it changes direction only at "
        "a surface. "
        "(2) A mirror adds light of its own. Correction: A mirror only "
        "reflects the light that falls on it."
        "; Error Analysis: (1) The image distance is measured from the "
        "object. Correction: Measure it from the mirror surface."
    )
    assert details.endswith(expected), details
    # Numbering is per component and never the LA- id.
    assert "LA-000" not in details
    # The pair body passes every mechanical gate a legacy section passes.
    from app.services import concept_validator as cv
    from app.services import katex_rules

    normalized = cv.ensure_valid_learner_analysis(copy.deepcopy(rows))
    assert [row["concept_details"] for row in normalized] == [
        row["concept_details"] for row in rows
    ]
    assert katex_rules.repair_unwrapped_math(details) == details
    content = dict(cr.split_sections(details))["Misconception/ Error Analysis"]
    assert cv._CANONICAL_ANALYSIS_CONTENT_RE.fullmatch(content)
    assert cr.normalize_analysis_sections(details) == details
    report = cv.validate_concept_rows(
        copy.deepcopy(rows), strict_analysis_section=True,
        analysis_allotted_keys={0}, allow_culmination=True,
    )
    assert not {
        e["code"] for e in report["errors"] if "analysis" in e["code"]
    }


def test_a_recorded_inventory_without_corrections_renders_the_legacy_join():
    items = [
        {"item_id": "LA-0001", "kind": "misconception", "text": "First belief"},
        {"item_id": "LA-0002", "kind": "misconception", "text": "Second belief."},
    ]
    assert assemble._render_analysis_component(items) == (
        assemble._join_analysis_texts(["First belief", "Second belief."])
    ) == "First belief. Second belief."
    # An empty correction on every item is the legacy branch too.
    assert assemble._render_analysis_component(
        [{**item, "correction": ""} for item in items]
    ) == "First belief. Second belief."
    # One correction present: the component numbers every pair and a pair
    # without its half renders its text alone — never dropped.
    partial = [dict(items[0]), {**items[1], "correction": "What is true."}]
    assert assemble._render_analysis_component(partial) == (
        "(1) First belief. (2) Second belief. Correction: What is true."
    )


def test_strip_correction_label_echo_is_one_exact_leading_token():
    assert cr.strip_correction_label_echo("Correction: what is true") == "what is true"
    assert cr.strip_correction_label_echo("correction : x") == "x"
    assert cr.strip_correction_label_echo("Correction: Correction: x") == "Correction: x"
    assert cr.strip_correction_label_echo("A correction: x") == "A correction: x"
    assert cr.strip_correction_label_echo("") == ""


# ---------------------------------------------------------------------------
# the Refiner keeps the pairing


def test_refiner_identity_gate_keeps_the_pairing_and_exempts_legacy_rows():
    from app.services import release_refiner

    paired = (
        "Description: Light travels in straight lines.\nAchieving Mastery: "
        "Draw straight-line ray diagrams. // Misconception/ Error Analysis: "
        "Misconceptions: (1) Light bends in air. Correction: It travels "
        "straight in air. (2) Mirrors add light. Correction: Mirrors only "
        "reflect light."
    )
    row = {"concept_details": paired, "keywords": "light"}
    reworded = paired.replace("It travels straight in air.", "In air it travels straight.")
    assert release_refiner._identity_violations(row, reworded, "light") == []
    merged = paired.replace(
        " (2) Mirrors add light. Correction: Mirrors only reflect light.",
        " Mirrors add light, but they only reflect light.",
    )
    assert any(
        "misconception/correction pairing changed" in v
        for v in release_refiner._identity_violations(row, merged, "light")
    )
    renumbered = paired.replace("(2) Mirrors", "(3) Mirrors")
    assert any(
        "pairing changed" in v
        for v in release_refiner._identity_violations(row, renumbered, "light")
    )
    # A legacy row (no Correction: marker) has no pairing to keep.
    legacy = (
        "Description: Light travels in straight lines.\nAchieving Mastery: "
        "Draw straight-line ray diagrams. // Misconception/ Error Analysis: "
        "Misconceptions: Light bends in air (see equation (2))."
    )
    legacy_row = {"concept_details": legacy, "keywords": "light"}
    assert release_refiner._identity_violations(
        legacy_row, legacy.replace("(see equation (2))", "(see the second equation)"), "light",
    ) == []
    assert "Correction:" in release_refiner._RULES
    assert "never merge, drop or renumber a pair" in release_refiner._RULES


# ---------------------------------------------------------------------------
# the freeze


def test_new_envelopes_freeze_the_policy_and_the_golden_one_does_not(golden_envelope):
    assert policy.KEY not in golden_envelope["metadata"]
    assert policy.active(golden_envelope) is False
    from app.services import concept_topology_contract as contract
    import inspect

    source = inspect.getsource(contract)
    assert "analysis_correction_policy.KEY: analysis_correction_policy.VERSION" in source
