"""The Phase 03 Pre-Learning concept map (phase3/premap.py).

Step-7 slice C1 regressions: the map is built from the CAPTURE and never
from the Post map; it carries no quota of any kind; NO question is ever
lifted out of the source into a Pre artefact (the mechanical guard fails
closed); a Pre row grounds on its own contract while a Post row still
cannot ship ungrounded; needed-for links resolve or route to The Fixer;
the four critic verifications flag and never gate (Q10); and a second
identical run spends nothing.
"""
from __future__ import annotations

import copy
import json
import re

import pytest

from app.services.phase3 import kernel, premap
from tests import test_phase3_settle_golden as settle_golden

GOLDEN = settle_golden.GOLDEN


# ---------------------------------------------------------------------------
# the golden replay provider (shared with the runner golden)


def premap_replay_provider(golden_premap: dict):
    """Answer the map decision with the recorded map, and each link batch
    with the recorded links for exactly the pre-concepts it asks about.

    The fixture names post-learning concepts by TITLE and the provider
    resolves them against the request's own ``post_concepts`` payload —
    the same shape rne_place.json's replay uses for placement
    destinations, so the fixture never hard-codes a positional mint.
    """

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "premap.map":
            return copy.deepcopy(golden_premap["map"])
        by_title = {
            str(row.get("concept_title") or ""): str(row.get("concept_id") or "")
            for row in request.get("post_concepts") or []
        }
        wanted = {
            str(row.get("pre_concept_id") or "")
            for row in request.get("pre_concepts") or []
        }
        links = []
        for row in golden_premap["needed_for"]:
            if row["pre_concept_id"] not in wanted:
                continue
            for title in row["post_concepts"]:
                assert title in by_title, f"no recorded post concept {title!r}"
            links.append({
                "pre_concept_id": row["pre_concept_id"],
                "post_concept_ids": [
                    by_title[title] for title in row["post_concepts"]
                ],
                "rationale": row["rationale"],
            })
        return {"links": links}

    return provider


# ---------------------------------------------------------------------------
# fixtures


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return settle_golden.envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def golden_premap() -> dict:
    return json.loads(
        (GOLDEN / "rne_premap.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def golden_prelearn() -> dict:
    return json.loads(
        (GOLDEN / "rne_prelearn.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def merged_prerequisites(golden_envelope, golden_prelearn) -> dict:
    """The real merged capture, produced by prelearn.merge itself."""
    from app.services.phase3 import prelearn

    captures = [
        {
            "stage": stage,
            "items": copy.deepcopy(golden_prelearn["captures"][stage]),
            "review_flags": {},
            "stage_flags": [],
        }
        for stage in prelearn.STAGES
    ]

    def provider(_request: dict) -> dict:
        return {
            "prerequisites": [
                {
                    key: row[key]
                    for key in (
                        "prerequisite_id", "text", "captures", "rationale",
                    )
                }
                for row in golden_prelearn["merge"]
            ]
        }

    return prelearn.merge(
        golden_envelope, captures, provider=provider,
        critic=_verified_critic, store=kernel.DecisionStore(),
    )


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


def _code_only(module) -> str:
    """The module's EXECUTABLE source: comments and docstrings removed.

    The source pins below are about what the code DOES. Prose that
    explains a retired defect — a word-count predicate, a board-name
    switch — has to be quotable in a comment without tripping the pin
    that forbids re-introducing it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(
        Path(module.__file__).read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(tree)


# A compact, explicitly synthetic Post map: enough to link to, small
# enough to read. The real chain's 53 rows are exercised by the runner
# golden.
POST_ROWS = [
    {
        "topic": "The Making of Nationalism",
        "concept_title": "Sovereignty After 1789",
        "concept_details": (
            "Description: The Revolution moved sovereignty from the "
            "monarch to the body of citizens.\nAchieving Mastery: "
            "Explaining where sovereignty lay before and after 1789."
        ),
    },
    {
        "topic": "The Making of Nationalism",
        "concept_title": "Restoration Europe After Vienna",
        "concept_details": (
            "Description: The 1815 settlement restored dynasties and "
            "redrew boundaries across the continent. // Types: Type 01: "
            "Map work Case 01: Compare two dates Example 01: "
            "QINV-0004 Describe the change."
        ),
    },
    {
        "topic": "The Making of Nationalism",
        "concept_title": "Culmination - The Making of Nationalism",
        "concept_details": "Description: Recap of the topic.",
    },
]

PREREQUISITES = {
    "prerequisites": [
        {
            "prerequisite_id": "PR-0001",
            "text": "What sovereignty is, and who held it before 1789.",
            "evidence": ["BLK-00074", "QINV-0004"],
            "rationale": "The chapter says 'you will recall'.",
            "stages": ["settle"],
            "captures": ["settle:PR-0001"],
        },
        {
            "prerequisite_id": "PR-0002",
            "text": "Reading a map of Europe against its key.",
            "evidence": ["BLK-00050"],
            "rationale": "The prose tells the learner to look at the map.",
            "stages": ["settle", "host"],
            "captures": ["settle:PR-0002", "host:PR-0001"],
        },
    ],
}

MAP_RESPONSE = {
    "topics": [
        {
            "pre_topic_id": "PRT-0001",
            "title": "Political Vocabulary",
            "concepts": [
                {
                    "pre_concept_id": "PRC-0001",
                    "concept_title": "Sovereignty",
                    "description": (
                        "Sovereignty is the highest law-making authority in "
                        "a state, and before the age of revolutions it "
                        "belonged to hereditary rulers alone."
                    ),
                    "achieving_mastery": (
                        "Saying who holds the final authority to make law in "
                        "a given state"
                    ),
                    "keywords": "sovereignty, authority, law",
                    "prerequisites": ["PR-0001"],
                    "rationale": "one captured prerequisite, taught alone",
                },
            ],
        },
        {
            "pre_topic_id": "PRT-0002",
            "title": "Reading the Sources of History",
            "concepts": [
                {
                    "pre_concept_id": "PRC-0002",
                    "concept_title": "Reading a Map",
                    "description": (
                        "A historical map shows how territory was divided at "
                        "one moment; its key tells you what every colour and "
                        "line on it means."
                    ),
                    "achieving_mastery": (
                        "Locating states and boundaries on a map using its "
                        "key"
                    ),
                    "keywords": "map, key, boundary",
                    "prerequisites": ["PR-0002"],
                    "rationale": "one captured prerequisite, taught alone",
                },
            ],
        },
    ]
}


def _links_for(request: dict, mapping: dict[str, list[str]]) -> dict:
    by_title = {
        row["concept_title"]: row["concept_id"]
        for row in request.get("post_concepts") or []
    }
    return {"links": [
        {
            "pre_concept_id": row["pre_concept_id"],
            "post_concept_ids": [
                by_title[title]
                for title in mapping.get(row["pre_concept_id"], [])
            ],
            "rationale": "replayed",
        }
        for row in request.get("pre_concepts") or []
    ]}


DEFAULT_LINKS = {
    "PRC-0001": ["Sovereignty After 1789"],
    "PRC-0002": ["Restoration Europe After Vienna"],
}


def _analysis_answer(request: dict, inventory=None, allotments=None) -> dict:
    """Answer the Pre lane's own Q1 inventory stages (preanalyse.py).

    The default is an EMPTY inventory — the legal answer for a Pre map
    this thin, and the one that keeps every map-shape assertion below
    about Description + Achieving Mastery only. Slice C2's inventory
    behaviour has its own module (test_phase3_preanalyse.py).
    """
    if request.get("stage") == "prelearn.analyse.inventory":
        return {"items": copy.deepcopy(list(inventory or []))}
    allotments = dict(allotments or {})
    return {"allotments": [
        {
            "item_id": item["item_id"],
            "pre_concept_id": allotments.get(item["item_id"], ""),
            "rationale": "replayed",
        }
        for item in request.get("items") or []
    ]}


def _provider(map_response=None, links=None, inventory=None, allotments=None):
    map_response = map_response if map_response is not None else MAP_RESPONSE
    links = DEFAULT_LINKS if links is None else links

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "premap.map":
            return copy.deepcopy(map_response)
        if stage.startswith("prelearn.analyse."):
            return _analysis_answer(request, inventory, allotments)
        return _links_for(request, links)

    return provider


def _build(golden_envelope, **kwargs):
    kwargs.setdefault("provider", _provider())
    kwargs.setdefault("critic", _verified_critic)
    kwargs.setdefault("store", kernel.DecisionStore())
    prerequisites = kwargs.pop("prerequisites", PREREQUISITES)
    post_rows = kwargs.pop("post_rows", POST_ROWS)
    return premap.build(
        golden_envelope, prerequisites, post_rows, **kwargs
    )


# ---------------------------------------------------------------------------
# built from the capture, never from the Post map


def test_the_build_payload_carries_the_capture_and_no_post_concept_row(
    golden_envelope,
):
    """Hard constraint 1: deriving the Pre map from finished Post concepts
    is the derive-from-existing flow Q3 retired. The map decision sees the
    merged prerequisites and the source blocks behind them — and no Post
    row. The needed-for decision DOES see Post Descriptions, because
    linking to them is a different act from building from them; the two
    payloads are pinned apart here."""
    seen: dict[str, dict] = {}
    base = _provider()

    def capturing(request: dict) -> dict:
        seen[str(request.get("stage"))] = copy.deepcopy(request)
        return base(request)

    _build(golden_envelope, provider=capturing)

    build_payload = seen["premap.map"]
    assert set(build_payload["evidence"]) == {
        "prerequisites", "source_blocks",
    }
    assert [
        row["prerequisite_id"]
        for row in build_payload["evidence"]["prerequisites"]
    ] == ["PR-0001", "PR-0002"]
    # The source the prerequisites were read from rides along; the
    # capture's non-block citations (QIDs, LA ids, hub refs) do not.
    assert [
        row["block_id"] for row in build_payload["evidence"]["source_blocks"]
    ] == ["BLK-00074", "BLK-00050"]
    flat = json.dumps(build_payload, ensure_ascii=False)
    for post in POST_ROWS:
        assert post["concept_title"] not in flat
    assert "concept_details" not in flat

    # And the LINK payload legitimately carries Post Descriptions.
    link_payload = seen["premap.needed_for"]
    titles = [
        row["concept_title"] for row in link_payload["post_concepts"]
    ]
    assert titles == ["Sovereignty After 1789", "Restoration Europe After Vienna"]
    assert "sovereignty from the monarch" in json.dumps(
        link_payload, ensure_ascii=False
    )
    # Culmination recaps are never link targets (analyse.py doctrine).
    assert not any("Culmination" in title for title in titles)


def test_the_map_ships_description_and_mastery_and_nothing_else(
    golden_envelope,
):
    """The owner steer: the Pre map ships Description and Achieving
    Mastery. Types, Cases and Examples reach the Pre detailing only once
    GENERATED questions exist to fill them — there is no code path here
    that can mint one."""
    result = _build(golden_envelope)
    assert [row["concept_title"] for row in result["rows"]] == [
        "Sovereignty", "Reading a Map",
    ]
    for row in result["rows"]:
        details = row["concept_details"]
        assert details.startswith("Description: ")
        assert "\nAchieving Mastery: " in details
        assert "// Types:" not in details
        assert "Type 01:" not in details
        assert "Case 01:" not in details
        assert "Example" not in details
        assert "Misconception" not in details
    assert [topic["title"] for topic in result["topics"]] == [
        "Political Vocabulary", "Reading the Sources of History",
    ]
    assert [topic["pre_concept_ids"] for topic in result["topics"]] == [
        ["PRC-0001"], ["PRC-0002"],
    ]


# ---------------------------------------------------------------------------
# no quota (Rule 1: no volume-derived structure)


def test_no_count_quota_rides_the_prompt_the_payload_or_the_code(
    golden_envelope,
):
    """§9 records that a "4-6 topics / 5-7 concepts" quota engine was
    RETIRED from this lane. Nothing may scale a topic or concept count
    from the size of the capture, the source, or the Post map."""
    from app.services.phase3 import prompts

    seen: list[dict] = []
    base = _provider()

    def capturing(request: dict) -> dict:
        seen.append(copy.deepcopy(request))
        return base(request)

    _build(golden_envelope, provider=capturing)
    # map, needed-for links, and the Pre lane's own Q1 inventory (which
    # answers empty here, so no allot decision follows).
    assert [request["stage"] for request in seen] == [
        "premap.map", "premap.needed_for", "prelearn.analyse.inventory",
    ]

    quota_language = re.compile(
        r"at least \d|at most \d|minimum of|maximum of|exactly \d+ |"
        r"per concept|per topic|one per|quota|target count|"
        r"\d+\s*(?:-|to)\s*\d+ (?:topics|concepts|items|elements)",
        re.IGNORECASE,
    )
    for request in seen:
        assert not quota_language.search(request["rules"]), request["stage"]
        numeric = {
            key for key, value in request.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        assert numeric == {"attempt", "max_attempts"}
    for system in (
        prompts.PREMAP_SYSTEM,
        prompts.PREMAP_NEEDED_FOR_SYSTEM,
        prompts.PREMAP_CRITIC_SYSTEM,
        prompts.PREANALYSE_INVENTORY_SYSTEM,
        prompts.PREANALYSE_ALLOT_SYSTEM,
        prompts.PREANALYSE_CRITIC_SYSTEM,
    ):
        assert not quota_language.search(system)

    # And no numeric literal decides anything in the module itself: no
    # count comparison, and no arithmetic over a length.
    source = _code_only(premap)
    assert not re.search(r"\blen\([^)]*\)\s*[<>]=?\s*\d", source)
    assert not re.search(r"\blen\([^)]*\)\s*[*/]\s*\d", source)
    assert "MIN_PER_TOPIC" not in source
    # The only numeric constant is the payload-size batch, and it bounds
    # a request, never an authored count.
    assert premap._LINK_BATCH_SIZE == 8


def test_a_thin_capture_yields_a_small_map_and_is_never_padded(
    golden_envelope,
):
    """One captured prerequisite is allowed to become one concept in one
    topic. Nothing pads it toward a fuller-looking map."""
    thin = {"prerequisites": [PREREQUISITES["prerequisites"][0]]}
    response = {
        "topics": [{
            "pre_topic_id": "PRT-0001",
            "title": "Political Vocabulary",
            "concepts": [
                copy.deepcopy(MAP_RESPONSE["topics"][0]["concepts"][0]),
            ],
        }]
    }
    result = _build(
        golden_envelope,
        prerequisites=thin,
        provider=_provider(map_response=response),
    )
    assert len(result["rows"]) == 1
    assert len(result["topics"]) == 1
    assert result["rows"][0][premap.PREREQUISITES_FIELD] == [{
        "prerequisite_id": "PR-0001",
        "text": "What sovereignty is, and who held it before 1789.",
    }]


def _empty_capture_provider(verdict="assumes_nothing", rationale="decided"):
    def provider(request: dict) -> dict:
        assert request["stage"] == premap.EMPTY_CAPTURE_KIND
        return {
            "verdict": verdict, "rationale": rationale, "confidence": 0.97,
        }
    return provider


def test_an_empty_capture_spends_exactly_one_recorded_verdict(
    golden_envelope,
):
    """INVERTED by spec-step8 D8.3/S9, and inverted rather than deleted.

    [measured at 76c84fb] this branch returned the empty map "without
    spending a decision" and its own docstring said so — it answered a
    JUDGMENT question ("does this chapter genuinely assume no prior
    knowledge, or did the capture fail to reach it?") with the shape of a
    list. An OCR-degraded scan, a thin lower-grade chapter and a chapter
    that genuinely opens from nothing are indistinguishable under that
    inference, which is the shape-matching Rule 1 exists to forbid.

    What the old test really pinned — that an empty capture is never
    padded and never raises — is pinned here still. What is added is the
    one recorded verdict, taken against the chapter's own source.
    """
    seen: list[dict] = []

    def provider(request: dict) -> dict:
        seen.append(copy.deepcopy(request))
        return {
            "verdict": "assumes_nothing",
            "rationale": "the chapter opens from first principles",
            "confidence": 0.96,
        }

    result = _build(
        golden_envelope, prerequisites={"prerequisites": []},
        provider=provider,
    )
    # Never padded: not one invented row, topic or link.
    assert result["rows"] == []
    assert result["topics"] == []
    assert result["needed_for"] == {}
    assert result["validation"] == []
    # And exactly ONE decision, on the NEW kind.
    assert len(seen) == 1
    assert seen[0]["stage"] == premap.EMPTY_CAPTURE_KIND
    # It sees the chapter's own source — the only evidence that can answer
    # the question, since the capture returned nothing to reason from.
    assert seen[0]["evidence"]["source_blocks"]
    assert seen[0]["chapter"]["grade"]
    # The verdict row's WHOLE shape, ``review_flags`` included. That key
    # was added after the first cut of this slice, when [measured] the
    # critic's dissent on this decision reached the release nowhere:
    # ``build_concepts_release`` has no reader for ``decision_flags``, and
    # ``stage_pre_release`` copies this row and nothing else. Q10 makes the
    # dissent an advisory flag, and a flag no reviewer of the artefact can
    # read is discarded, not recorded. Pinned by equality here so a later
    # slice cannot drop it back out.
    assert result[premap.EMPTY_CAPTURE_VERDICT_FIELD] == {
        "verdict": "assumes_nothing",
        "rationale": "the chapter opens from first principles",
        "review_flags": [],
    }


def test_the_empty_capture_verdict_has_its_own_kind_and_policy_version():
    """spec-step8 §4: a NEW kind with its OWN policy version, never an edit.

    Editing ``premap.map``'s text or ``POLICY_VERSION`` would replay every
    verdict taken under the old text against the new one — cache poisoning
    with no symptom. Pinned by value so the next slice cannot quietly fold
    this verdict into an existing identity.
    """
    assert premap.EMPTY_CAPTURE_KIND == "premap.empty_capture"
    assert premap.EMPTY_CAPTURE_POLICY_VERSION == "premap-empty-capture-1"
    assert premap.POLICY_VERSION == "premap-1"
    assert premap.EMPTY_CAPTURE_POLICY_VERSION != premap.POLICY_VERSION


def test_a_capture_incomplete_verdict_is_recorded_and_the_run_completes(
    golden_envelope,
):
    """The other verdict, and the reason the whole thing exists.

    A capture that FAILED must never be reported as a chapter that needed
    nothing (R4). The run still completes — this is not a halt — and the
    verdict rides the map for the release lane to read.
    """
    result = _build(
        golden_envelope, prerequisites={"prerequisites": []},
        provider=_empty_capture_provider(
            "capture_incomplete", "the source ends mid-sentence"),
    )
    assert result["rows"] == []
    assert result[premap.EMPTY_CAPTURE_VERDICT_FIELD] == {
        "verdict": "capture_incomplete",
        "rationale": "the source ends mid-sentence",
        "review_flags": [],
    }


def test_the_empty_capture_critic_advises_and_never_gates(golden_envelope):
    """Q10: the critic's dissent is a recorded flag, not a veto.

    It also has to be recorded WHERE THE RELEASE CAN READ IT, which is why
    this test no longer stops at ``decision_flags``. [measured] with the
    dissent in ``decision_flags`` alone, staging a Pre release from this
    map put the critic's words nowhere in the artefact — not an issue, not
    a manifest row, not even as a substring of the payload — because
    ``stage_pre_release`` copies the verdict row and nothing else.
    ``test_pre_release_lane_wiring`` carries the end-to-end half.
    """

    def dissenting(payload: dict) -> dict:
        return {
            "verdict": "rejected", "confidence": 0.42,
            "issues": ["the source shows two missing pages"],
        }

    result = _build(
        golden_envelope, prerequisites={"prerequisites": []},
        provider=_empty_capture_provider(),
        critic=dissenting,
    )
    # The decision STANDS — the critic did not overturn it.
    assert result[premap.EMPTY_CAPTURE_VERDICT_FIELD]["verdict"] == (
        "assumes_nothing"
    )
    flags = result["decision_flags"]["empty_capture"]
    assert any("rejected" in flag for flag in flags)
    assert any("missing pages" in flag for flag in flags)
    # And on the verdict row itself — the ONE thing the release copies.
    carried = result[premap.EMPTY_CAPTURE_VERDICT_FIELD]["review_flags"]
    assert carried == flags


def test_an_undecidable_empty_capture_goes_to_the_fixer_and_completes(
    golden_envelope,
):
    """Q13: no positive decision mid-run means ONE flagged Fixer decision.

    The run COMPLETES. An empty capture is not one of the three pre-spend
    pauses and it is not genuine impossibility, so it may not halt.
    """

    def refusing(request: dict) -> dict:
        return {"verdict": "", "rationale": ""}

    def fixer(request: dict) -> dict:
        assert request["fixer"] is True
        assert request["contract"]["kind"] == premap.EMPTY_CAPTURE_KIND
        return {
            "verdict": "capture_incomplete",
            "rationale": "no verdict could be reached on this source",
        }

    result = _build(
        golden_envelope, prerequisites={"prerequisites": []},
        provider=refusing, fixer=fixer,
    )
    assert result[premap.EMPTY_CAPTURE_VERDICT_FIELD]["verdict"] == (
        "capture_incomplete"
    )
    assert any(
        flag.startswith("fixer: ")
        for flag in result["decision_flags"]["empty_capture"]
    )
    # And on the verdict row, so the release records that this verdict was
    # the Fixer's best judgment rather than a decision the pass reached.
    assert any(
        flag.startswith("fixer: ")
        for flag in result[premap.EMPTY_CAPTURE_VERDICT_FIELD]["review_flags"]
    )


# ---------------------------------------------------------------------------
# NO EXTRACTION OF ANY QUESTIONS — the mechanical guard, fail-closed


def test_no_inventory_qid_appears_anywhere_in_a_pre_row(golden_envelope):
    result = _build(golden_envelope)
    qids = premap.inventory_qids(golden_envelope)
    assert qids  # the golden chapter really does have an inventory
    flat = json.dumps(result["rows"], ensure_ascii=False)
    for qid in qids:
        assert qid not in flat
    # The build payload never showed the model a source question id
    # either, even though the capture cited one (PR-0001 cites QINV-0004).
    assert "QINV-0004" in json.dumps(PREREQUISITES, ensure_ascii=False)


def test_the_guard_fails_closed_when_a_qid_is_injected_into_a_row(
    golden_envelope,
):
    """Not advisory, not routed to The Fixer: a source QID on a Pre row
    would give that QID two final homes, and Q2's exactly-once (Rule C)
    has no correction that keeps both."""
    fixer_calls = {"n": 0}

    def fixer(request: dict) -> dict:
        fixer_calls["n"] += 1
        raise AssertionError("the guard must not route to the Fixer")

    # Injected where only the finished ROW can carry it: the mastery line
    # never rides the needed-for payload, so this is the row guard.
    poisoned = copy.deepcopy(MAP_RESPONSE)
    poisoned["topics"][0]["concepts"][0]["achieving_mastery"] += (
        " (see QINV-0004)"
    )
    with pytest.raises(premap.PreExtractionError) as excinfo:
        _build(
            golden_envelope, provider=_provider(map_response=poisoned),
            fixer=fixer,
        )
    assert "QINV-0004" in str(excinfo.value)
    assert "a Pre-Learning concept row carries source question id" in str(
        excinfo.value
    )
    assert fixer_calls["n"] == 0

    # And a QID smuggled into a Description is caught one step earlier,
    # on the payload the link decision would have been shown.
    leaking = copy.deepcopy(MAP_RESPONSE)
    leaking["topics"][0]["concepts"][0]["description"] += (
        " See QINV-0004 for the worked example."
    )
    with pytest.raises(
        premap.PreExtractionError, match="needed-for link payload"
    ):
        _build(
            golden_envelope, provider=_provider(map_response=leaking),
            fixer=fixer,
        )
    assert fixer_calls["n"] == 0


def test_upstream_evidence_naming_a_qid_is_redacted_never_refused(
    golden_envelope,
):
    """The model is never SHOWN a source question's identity — but a
    capture rationale that names one is legitimate upstream output, not
    corruption, so it is redacted rather than refused.

    ``prelearn``'s host-stage capture shows the model each question's id
    and text and asks it to say what in that evidence assumes the
    element, so provenance naming a QID is expected. Refusing it would
    kill the run at 0.97 with a finished, paid-for Post map in hand —
    and kill it permanently, because the offending rationale lives in a
    content-addressed decision that replays identically at zero spend.
    CLAUDE.md allows a stop only where a decision cannot be made
    mechanically applicable; dropping an id token from provenance
    plainly can be."""
    leaking = copy.deepcopy(PREREQUISITES)
    leaking["prerequisites"][0]["text"] += " (see QINV-0004)"
    leaking["prerequisites"][0]["rationale"] = (
        "The exercise QINV-0004 asks the learner to describe the change, "
        "which assumes they already hold this."
    )
    seen: list[dict] = []

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage.startswith("prelearn.analyse."):
            return _analysis_answer(request)
        if stage != "premap.map":
            return _links_for(request, DEFAULT_LINKS)
        seen.append(copy.deepcopy(request))
        return copy.deepcopy(MAP_RESPONSE)

    result = _build(
        golden_envelope, prerequisites=leaking, provider=provider,
    )
    # The run completes and the map ships.
    assert result["rows"]
    # The model saw the evidence, with the identity gone and the
    # sentence still readable.
    payload = json.dumps(seen[0], ensure_ascii=False)
    assert "QINV-0004" not in payload
    assert "a source question of this chapter" in payload
    assert "asks the learner to describe the change" in payload

    # The payload guard survives as the post-condition of that
    # filtering: it is what catches a channel added here without being
    # redacted, and it still runs before any spend.
    calls = {"n": 0}

    def counting(request: dict) -> dict:
        calls["n"] += 1
        return copy.deepcopy(MAP_RESPONSE)

    with pytest.raises(premap.PreExtractionError, match="build payload"):
        premap._refuse_source_qids(
            {"chapter": {"unit": "QINV-0004"}},
            premap.inventory_qids(golden_envelope),
            where="the Pre map build payload",
        )
    assert calls["n"] == 0


def test_the_known_qid_set_covers_umbrella_parent_questions(
    golden_envelope,
):
    """A parent_qid is a real current-chapter question — its stem is the
    sub-part's shared_context — that is simply not itself an inventory
    row. Reading only ``qid`` would leave those identities outside the
    known set and a Pre row could carry one unrefused."""
    items = golden_envelope["inventory"]["items"]
    item_qids = {str(item.get("qid") or "") for item in items}
    parents = {
        str(item.get("parent_qid") or "") for item in items
    } - {""} - item_qids
    assert parents  # the golden chapter really does have umbrellas
    assert "QINV-0011" in parents and "QINV-0016" in parents

    known = premap.inventory_qids(golden_envelope)
    for parent in parents:
        assert parent in known

    # And one of them, injected into a Pre row, is now refused.
    poisoned = copy.deepcopy(MAP_RESPONSE)
    poisoned["topics"][0]["concepts"][0]["achieving_mastery"] += (
        " (see QINV-0011)"
    )
    with pytest.raises(premap.PreExtractionError, match="QINV-0011"):
        _build(golden_envelope, provider=_provider(map_response=poisoned))


def test_a_qid_in_an_empty_topics_title_is_refused_not_carried(
    golden_envelope,
):
    """``topics`` is guarded, not only ``rows``. A topic the model
    authored with no concepts under it never becomes a row, so a
    rows-only scan would let its title carry a QID into the ``pre_map``
    carry channel and its snapshot — the artefact the steer names."""
    poisoned = copy.deepcopy(MAP_RESPONSE)
    poisoned["topics"].append({
        "pre_topic_id": f"PRT-{len(poisoned['topics']) + 1:04d}",
        "title": "Leftovers: QINV-0004, QINV-0011",
        "concepts": [],
    })
    # The map checker is clean on it — every prerequisite is still
    # accounted for by the other topics — so nothing else would catch it.
    assert premap._map_checker(
        [row["prerequisite_id"] for row in PREREQUISITES["prerequisites"]]
    )(poisoned) == []

    with pytest.raises(premap.PreExtractionError) as excinfo:
        _build(golden_envelope, provider=_provider(map_response=poisoned))
    assert "QINV-0004" in str(excinfo.value)


def test_a_critic_naming_a_qid_flags_the_row_and_never_kills_the_run(
    golden_envelope,
):
    """Q10: the critic's dissent is an advisory review flag, never a gate.

    ``PREMAP_CRITIC_SYSTEM`` asks in as many words whether a concept
    carries "its questions", so the most useful possible answer names
    the question. If the guard scanned flags, that answer would become
    the hardest gate in the pipeline — and a permanent one, since the
    verdict is stored content-addressed and replays at zero spend. The
    guard therefore runs over the authored artefact BEFORE any flag is
    stamped."""

    def critic(payload: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.4,
            "issues": [
                "LEAKAGE: PRC-0001 restates the task QINV-0004 rather "
                "than the prior knowledge it assumes",
            ],
        }

    result = _build(golden_envelope, critic=critic)

    # The run completed and every row shipped.
    assert len(result["rows"]) == sum(
        len(topic["concepts"]) for topic in MAP_RESPONSE["topics"]
    )
    # The dissent is recorded, verbatim, where a reviewer will see it.
    flat = json.dumps(result["review_flags"], ensure_ascii=False)
    assert "QINV-0004" in flat
    assert "LEAKAGE" in flat
    assert result["decision_flags"]

    # And it reached the shipped rows, which is the point of a flag.
    assert any(
        any("LEAKAGE" in flag for flag in row.get("review_flags") or [])
        for row in result["rows"]
    )


def test_the_guard_is_boundary_aware_and_ignores_a_longer_id(
    golden_envelope,
):
    """Identity accounting over minted ids, not substring matching: an id
    that merely CONTAINS a real qid is a different id."""
    assert premap._mentions_id("see QINV-0004 here", "QINV-0004")
    assert premap._mentions_id("QINV-0004", "QINV-0004")
    assert not premap._mentions_id("QINV-00041", "QINV-0004")
    assert not premap._mentions_id("XQINV-0004", "QINV-0004")


def test_the_module_can_never_mint_a_type_case_or_example(golden_envelope):
    """Structural, not a content check: the row's detailing is composed
    from exactly two authored fields, so no Types section has a way in."""
    from pathlib import Path

    source = Path(premap.__file__).read_text(encoding="utf-8")
    assert '"Description: " + _normal(description)' in source
    assert "// Types:" not in source
    assert "Example 01" not in source


def test_a_model_authored_types_section_is_a_contract_defect(
    golden_envelope,
):
    """The pin above is a source pin — it proves the module cannot MINT a
    section, not that one cannot ARRIVE. The model can author a Types
    block inside ``description``, and the composed row would then carry
    it. That row must not ship: ``types_too_early`` is in
    ``generation._FATAL_CODES`` and the Pre lane has no Fixer at deposit
    yet, so a merely-flagged row walks into a fatal gate with no Q13
    route out.

    So it is a mechanical defect on the map checker, which means bounded
    corrections and then The Fixer's one recorded decision — never a
    halt, and never a knowingly-fatal shipped row."""
    from app.services import generation

    assert "types_too_early" in generation._FATAL_CODES

    poisoned = copy.deepcopy(MAP_RESPONSE)
    poisoned["topics"][0]["concepts"][0]["description"] += (
        " // Types: Type 01: Define the term "
        "Case 01: In your own words "
        "Example 01: Say what sovereignty means."
    )
    defects = premap._map_checker(
        [row["prerequisite_id"] for row in PREREQUISITES["prerequisites"]]
    )(poisoned)
    assert any("Types" in defect for defect in defects)
    assert any("no Types, no Cases, no Examples" in defect for defect in defects)

    # It corrects: the model's next attempt drops the section and ships.
    attempts = {"n": 0}

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage.startswith("prelearn.analyse."):
            return _analysis_answer(request)
        if stage != "premap.map":
            return _links_for(request, DEFAULT_LINKS)
        attempts["n"] += 1
        return copy.deepcopy(
            poisoned if attempts["n"] == 1 else MAP_RESPONSE
        )

    result = _build(golden_envelope, provider=provider)
    assert attempts["n"] == 2
    assert result["rows"]
    for row in result["rows"]:
        assert "// Types:" not in row["concept_details"]

    # And an unrepentant model routes to The Fixer, flagged — the run
    # completes rather than halting (Q13).
    fixer_calls = {"n": 0}

    def fixer(request: dict) -> dict:
        fixer_calls["n"] += 1
        return {**copy.deepcopy(MAP_RESPONSE), "rationale": "section removed"}

    result = _build(
        golden_envelope,
        provider=_provider(map_response=poisoned),
        fixer=fixer,
    )
    assert fixer_calls["n"] == 1
    assert result["rows"]
    assert any(
        "fixer: blocked=" in flag
        for flags in result["review_flags"].values()
        for flag in flags
    )


# ---------------------------------------------------------------------------
# grounding (spec T4)


def test_a_pre_row_passes_the_seal_on_its_own_contract(golden_envelope):
    from app.services import grounding_certificate

    result = _build(golden_envelope)
    for index, row in enumerate(result["rows"]):
        assert row["_source_grounding_contract"] == (
            "derived-from-prerequisite-capture"
        )
        assert row["_source_block_ids"] == []
        assert row[grounding_certificate.ROW_CERTIFICATE_FIELD]
        # The seal verifies, so a later pass can detect tampering exactly
        # as it does for a Post row.
        grounding_certificate.verify_row(row, row_index=index)


def test_a_post_row_still_cannot_ship_without_grounding(golden_envelope):
    """The exclusion is NARROW: it is keyed on the Pre lane's contract
    value and on nothing else, so an ordinary row with no source blocks
    still fails exactly as before."""
    from app.services import grounding_certificate

    row = {
        "topic": "T",
        "concept_title": "Ungrounded",
        "concept_details": "Description: x",
        "_source_block_ids": [],
        "_source_grounding_contract": "api-verified-source-block-ids",
        "_source_grounding_version": "v",
    }
    with pytest.raises(
        grounding_certificate.GroundingCertificateError,
        match="has no source blocks",
    ):
        grounding_certificate.seal_records(
            [row],
            source_contract_hash="h",
            semantic_topology_sha256="a" * 64,
            allowed_block_ids={"BLK-1"},
        )


def test_the_pre_exclusion_is_two_sided(golden_envelope):
    """A row contracted to the prerequisite capture that cites a
    current-chapter block is a defect, not a bonus — the Sorrieu passage
    is exactly the case that must not be quietly absorbed."""
    from app.services import grounding_certificate

    row = {
        "topic": "T",
        "concept_title": "Pre",
        "concept_details": "Description: x",
        "_source_block_ids": ["BLK-1"],
        "_source_grounding_contract": (
            grounding_certificate.PRELEARN_GROUNDING_CONTRACT
        ),
        "_source_grounding_version": "v",
    }
    with pytest.raises(
        grounding_certificate.GroundingCertificateError,
        match="cites current-chapter source block",
    ):
        grounding_certificate.seal_records(
            [row],
            source_contract_hash="h",
            semantic_topology_sha256="a" * 64,
            allowed_block_ids={"BLK-1"},
        )


# ---------------------------------------------------------------------------
# the validator stance, decided rather than defaulted


def test_require_culmination_is_explicitly_off_for_the_pre_lane(
    golden_envelope,
):
    """Constraint 4, recorded as a decision in code. A prerequisite topic
    has nothing to consolidate, so the Pre map ships no culmination row —
    and with the flag ON the same rows would be told they owe one."""
    from app.services import concept_validator

    assert premap.PRE_VALIDATOR_FLAGS["require_culmination"] is False
    assert premap.PRE_VALIDATOR_FLAGS["strict_type_hierarchy"] is False
    assert premap.PRE_VALIDATOR_FLAGS["allow_types"] is False
    assert premap.PRE_VALIDATOR_FLAGS["strict_mastery_statement"] is False

    rows = [
        {
            "topic": "Political Vocabulary",
            "concept_title": f"Concept {index}",
            "concept_details": (
                "Description: A short prerequisite explanation that a "
                "learner can hold before the chapter begins.\nAchieving "
                "Mastery: Using the idea in the chapter's first pages."
            ),
        }
        for index in (1, 2)
    ]
    decided = premap.validate_rows(rows)
    assert not [
        finding for finding in decided
        if finding["code"].startswith("culmination")
    ]
    with_flag = concept_validator.validate_concept_rows(
        [dict(row) for row in rows],
        **{**premap.PRE_VALIDATOR_FLAGS, "require_culmination": True},
    )
    assert [
        finding["code"] for finding in with_flag["errors"]
        if finding["code"].startswith("culmination")
    ] == ["culmination_count"]


def test_validator_findings_flag_the_row_and_never_gate(golden_envelope):
    """``description_length`` is a word-count band deciding meaning, and a
    full teaching paragraph — which is exactly what "the same detailing
    standard as Post-Learning" asks for — falls outside its upper bound.
    The Pre lane records validator findings as review flags: it never
    promotes them to fatals and never drops the row."""
    long_paragraph = " ".join(
        "Sovereignty is the highest law-making authority in a state and "
        "the learner needs it before this chapter begins."
        for _ in range(9)
    )
    terse = copy.deepcopy(MAP_RESPONSE)
    terse["topics"][0]["concepts"][0]["description"] = long_paragraph
    result = _build(
        golden_envelope, provider=_provider(map_response=terse),
    )
    assert len(result["rows"]) == 2  # nothing dropped
    codes = {finding["code"] for finding in result["validation"]}
    assert "description_length" in codes
    assert any(
        flag.startswith("Pre validator [description_length]")
        for flag in result["rows"][0]["review_flags"]
    )


# ---------------------------------------------------------------------------
# needed-for links


def test_needed_for_links_resolve_to_real_post_concepts(golden_envelope):
    result = _build(golden_envelope)
    assert result["needed_for"] == {
        "PRC-0001": ["CONCEPT-0001"],
        "PRC-0002": ["CONCEPT-0002"],
    }
    assert result["rows"][0][premap.NEEDED_FOR_FIELD] == [{
        "post_concept_id": "CONCEPT-0001",
        "post_concept_title": "Sovereignty After 1789",
    }]
    # The links are row-private audit, registered for stripping before DB
    # upload — they add no house-format section and no DB column here.
    from app.services import build_concepts_release as release

    assert premap.NEEDED_FOR_FIELD in release._RELEASE_AUDIT_FIELDS
    assert premap.PREREQUISITES_FIELD in release._RELEASE_AUDIT_FIELDS
    stripped = release._strip_release_fields(result["rows"][0])
    assert premap.NEEDED_FOR_FIELD not in stripped
    assert premap.PREREQUISITES_FIELD not in stripped


def test_an_unresolvable_link_routes_to_the_fixer_and_is_never_dropped(
    golden_envelope,
):
    """R4: every link must resolve to a real Post concept id. A link that
    names a concept the chapter does not have exhausts the bounded
    corrections and goes to The Fixer for one recorded, flagged decision
    — the pre-concept still ships."""
    def broken(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "premap.map":
            return copy.deepcopy(MAP_RESPONSE)
        if stage.startswith("prelearn.analyse."):
            return _analysis_answer(request)
        return {"links": [
            {
                "pre_concept_id": row["pre_concept_id"],
                "post_concept_ids": ["CONCEPT-9999"],
                "rationale": "a concept this chapter does not have",
            }
            for row in request.get("pre_concepts") or []
        ]}

    def fixer(request: dict) -> dict:
        response = _links_for(request["original_payload"], DEFAULT_LINKS)
        response["rationale"] = "resolved the link against the real map"
        return response

    result = _build(golden_envelope, provider=broken, fixer=fixer)
    assert result["needed_for"] == {
        "PRC-0001": ["CONCEPT-0001"],
        "PRC-0002": ["CONCEPT-0002"],
    }
    assert any(
        flag.startswith("fixer: blocked=")
        for flags in result["review_flags"].values()
        for flag in flags
    )


def test_an_unresolvable_link_without_a_fixer_fails_closed(golden_envelope):
    def broken(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "premap.map":
            return copy.deepcopy(MAP_RESPONSE)
        if stage.startswith("prelearn.analyse."):
            return _analysis_answer(request)
        return {"links": [
            {
                "pre_concept_id": row["pre_concept_id"],
                "post_concept_ids": ["CONCEPT-9999"],
            }
            for row in request.get("pre_concepts") or []
        ]}

    with pytest.raises(kernel.ContractError, match="unknown post concept id"):
        _build(golden_envelope, provider=broken)


def test_a_pre_concept_nothing_requires_still_ships_flagged(golden_envelope):
    """Necessity is an ADVISORY critic dimension (Q10): an empty link list
    is a legitimate verdict, so the concept ships and the reviewer is
    told. It is never dropped, never retried."""
    result = _build(
        golden_envelope,
        provider=_provider(links={"PRC-0001": ["Sovereignty After 1789"]}),
    )
    assert len(result["rows"]) == 2
    assert result["needed_for"]["PRC-0002"] == []
    assert any(
        "its necessity needs review" in flag
        for flag in result["review_flags"]["PRC-0002"]
    )
    assert result["rows"][1][premap.NEEDED_FOR_FIELD] == []


# ---------------------------------------------------------------------------
# R4 exact-once over the capture


def test_an_unmapped_prerequisite_is_a_contract_defect(golden_envelope):
    """A captured prerequisite the map never names is a learner's
    prerequisite lost silently, which R4 forbids outright."""
    check = premap._map_checker(["PR-0001", "PR-0002"])
    dropped = copy.deepcopy(MAP_RESPONSE)
    del dropped["topics"][1]
    dropped["topics"][0]["concepts"][0]["prerequisites"] = ["PR-0001"]
    assert any(
        "unmapped prerequisite(s): PR-0002" in defect
        for defect in check(dropped)
    )
    doubled = copy.deepcopy(MAP_RESPONSE)
    doubled["topics"][1]["concepts"][0]["prerequisites"] = ["PR-0001"]
    assert any(
        "unknown or repeated prerequisite_id PR-0001" in defect
        for defect in check(doubled)
    )
    assert check(MAP_RESPONSE) == []


def test_the_map_checker_is_mechanics_only():
    check = premap._map_checker(["PR-0001"])
    defects = check({"topics": [{
        "pre_topic_id": "PRT-0002",
        "title": "",
        "concepts": [{
            "pre_concept_id": "PRC-0007",
            "concept_title": "",
            "description": "d",
            "achieving_mastery": "",
            "prerequisites": ["PR-0009"],
        }],
    }]})
    assert any("expected PRT-0001" in row for row in defects)
    assert any("has an empty title" in row for row in defects)
    assert any("expected PRC-0001" in row for row in defects)
    assert any("has an empty concept_title" in row for row in defects)
    assert any("has an empty achieving_mastery" in row for row in defects)
    assert any(
        "unknown or repeated prerequisite_id PR-0009" in row
        for row in defects
    )
    assert check({}) == ["response has no topics array"]


# ---------------------------------------------------------------------------
# Q10: the four verifications are ADVISORY


def test_critic_dissent_on_all_four_dimensions_flags_and_never_gates(
    golden_envelope,
):
    """Necessity, grade boundary, non-duplication and zero
    current-chapter leakage are critic dimensions. Dissent on every one
    of them lands as a review flag; nothing is dropped, retried, or
    escalated, and every concept still ships."""
    issues = [
        "necessity: PRC-0002 is not required by anything this chapter "
        "teaches",
        "grade boundary: PRC-0001 restates this chapter's own teaching "
        "rather than the prior knowledge it assumes",
        "non-duplication: PRC-0001 and PRC-0002 teach the same "
        "fundamental in different words",
        "leakage: PRC-0002 carries this chapter's own worked content",
    ]

    def dissenting(_request: dict) -> dict:
        return {"verdict": "rejected", "confidence": 0.4, "issues": issues}

    result = _build(golden_envelope, critic=dissenting)
    assert len(result["rows"]) == 2
    flags = [
        flag
        for row_flags in result["review_flags"].values()
        for flag in row_flags
    ]
    for issue in issues:
        assert any(issue in flag for flag in flags)
    # The dissent is recorded at the decision too, so a reviewer sees
    # WHICH decision was doubted rather than only which rows carry a flag.
    assert set(result["decision_flags"]) == {"map", "needed_for#0"}
    for row in result["rows"]:
        assert row["review_flags"]


# ---------------------------------------------------------------------------
# calibration is evidence, never a branch (steer point 2)


def test_calibration_rides_the_payload_and_branches_nowhere_in_code(
    golden_envelope,
):
    seen: list[dict] = []
    base = _provider()

    def capturing(request: dict) -> dict:
        seen.append(copy.deepcopy(request))
        return base(request)

    _build(golden_envelope, provider=capturing)
    for request in seen:
        assert request["chapter"] == {
            "board": "CBSE",
            "grade": "10",
            "subject": "History",
            "unit": "Modern World History",
            "chapter_title": "The Rise of Nationalism in Europe",
        }

    # The Architect's assembled instruction set is the calibration
    # mechanism, and the Pre lane reads the board slot the other passes do
    # not — without moving any existing pass's payload.
    from app.services.phase3 import prompts

    assert prompts.DEFAULT_SLOTS == (
        ("Subject topology guidance", "subject_topology_guidance"),
        ("Grade-band vocabulary", "grade_band_vocabulary"),
    )
    assert prompts.PRE_LEARNING_SLOTS == prompts.DEFAULT_SLOTS + (
        (
            "Board and publication conventions",
            "board_publication_conventions",
        ),
    )
    env = copy.deepcopy(dict(golden_envelope))
    env["metadata"] = {
        **env["metadata"],
        "instruction_slots": {
            "grade_band_vocabulary": "Grade 10 vocabulary guidance.",
            "board_publication_conventions": "CBSE publication conventions.",
        },
    }
    assert prompts.instruction_rules_suffix(env) == (
        " RUN INSTRUCTIONS (Architect): Grade-band vocabulary: Grade 10 "
        "vocabulary guidance."
    )
    assert "CBSE publication conventions." in prompts.instruction_rules_suffix(
        env, slots=prompts.PRE_LEARNING_SLOTS
    )

    # And no per-grade / per-subject / per-board branch in the module —
    # the retired board-substring guidance switch must not return. The
    # pin reads the EXECUTABLE source, so the prose recording why that
    # switch was wrong stays quotable in a comment.
    source = _code_only(premap)
    for token in ("CBSE", "ICSE", "IGCSE", "NCERT", "Grade 6", "Class 10"):
        assert token not in source
    assert not re.search(
        r'(?:board|grade|subject)\s*(?:==|!=| in )\s*[("\'\[{]', source
    )


# ---------------------------------------------------------------------------
# decide-once


def test_decide_once_replays_the_whole_map_for_free(golden_envelope):
    calls = {"n": 0}
    critic_calls = {"n": 0}
    base = _provider()

    def counted(request: dict) -> dict:
        calls["n"] += 1
        return base(request)

    def counted_critic(request: dict) -> dict:
        critic_calls["n"] += 1
        return _verified_critic(request)

    def fixer(_request: dict) -> dict:
        raise AssertionError("the fixer must not be engaged")

    store = kernel.DecisionStore()
    first = _build(
        golden_envelope, provider=counted, critic=counted_critic,
        store=store, fixer=fixer,
    )
    assert calls["n"] == 3  # map + one link batch + the Pre inventory
    after = calls["n"], critic_calls["n"]
    second = _build(
        golden_envelope, provider=counted, critic=counted_critic,
        store=store, fixer=fixer,
    )
    assert (calls["n"], critic_calls["n"]) == after
    assert second == first


# ---------------------------------------------------------------------------
# the existing passes are untouched


def test_no_existing_pass_moved_for_this_slice():
    from app.services import semantic_confidence_policy as confidence_policy
    from app.services.phase3 import analyse, place, prelearn

    assert confidence_policy.POLICY_VERSION == (
        "semantic-confidence-policy-3"
    )  # settle + host
    assert place.POLICY_VERSION == "place-1"
    assert analyse.POLICY_VERSION == "analysis-1"
    assert prelearn.POLICY_VERSION == "prelearn-1"
    assert premap.POLICY_VERSION == "premap-1"

    from pathlib import Path

    root = Path(place.__file__).parent
    for name in ("settle.py", "host.py", "place.py", "analyse.py",
                 "polish.py", "assemble.py", "prelearn.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "premap" not in text.lower(), name


# ---------------------------------------------------------------------------
# the carry channel and the snapshot


def test_the_pre_map_rides_the_carry_channel_and_its_own_snapshot(
    monkeypatch, tmp_path,
):
    """Slice B widened ``_run_rewritten_phase3`` to copy every non-record
    key out of the sealed boundary; the Pre map rides that same exit, and
    the Post lane's records are byte-identical with and without it. The
    snapshot beside the decision store is the durable copy (runner.py's
    _snapshot_* helpers)."""
    from app.services import canonical_source_phase3 as phase3_core
    from app.services import concept_topology_contract as topology_contract
    from app.services import generation
    from app.services.phase3 import runner as p3_runner
    from tests import test_instruction_architect as ia_tests

    stored = ia_tests._sample_envelope("a" * 64)
    session = {
        "artifact_dir": str(tmp_path),
        "canonical": {"blocks": [{"block_id": "BLK-1", "display_text": "t"}]},
    }
    monkeypatch.setattr(phase3_core, "active_session", lambda: session)
    monkeypatch.setattr(
        phase3_core, "active_graph", lambda: copy.deepcopy(stored["graph"])
    )
    out = [{"concept_title": "Place Value", "topic": "Numbers"}]
    (tmp_path / "source.phase3-envelope.json").write_text(
        json.dumps(
            {
                "boundary_skeleton_sha256": phase3_core._sha256_json(
                    list(out)
                ),
                "envelope": stored,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pre_map = {
        "rows": [{"concept_title": "Counting", "_pre_concept_id": "PRC-0001"}],
        "topics": [{"pre_topic_id": "PRT-0001", "title": "Number"}],
        "needed_for": {"PRC-0001": ["CONCEPT-0001"]},
        "review_flags": {},
        "decision_flags": {},
        "validation": [],
    }

    def fake_run(env, store_dir=None):
        p3_runner._snapshot_premap(pre_map, store_dir)
        return {
            "records": [dict(out[0])],
            "prerequisites": {"prerequisites": []},
            "pre_map": copy.deepcopy(pre_map),
            "analysis": {"inventory": []},
            "summary": {
                "row_count": 1, "routed_qids": 0,
                "unrouted_items": 0, "flagged_row_count": 0,
            },
        }

    monkeypatch.setattr(p3_runner, "run", fake_run)
    kwargs = {
        "meta": {"board": "CBSE", "instruction_set_sha256": "a" * 64},
        "question_task_inventory": {},
        "mined_types": {},
    }
    carry: dict = {}
    records = topology_contract._run_rewritten_phase3(
        generation, out, dict(kwargs), carry=carry
    )
    assert records == [{"concept_title": "Place Value", "topic": "Numbers"}]
    assert carry["pre_map"] == pre_map
    assert "records" not in carry
    # and the durable copy landed beside the decision store
    snapshot = tmp_path / "source.phase3-prelearn-map.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == pre_map


# ---------------------------------------------------------------------------
# the fixture is authored, not invented


def test_the_golden_fixture_maps_the_real_capture_exactly_once(
    golden_premap, merged_prerequisites,
):
    """Every prerequisite_id in the fixture is one the REAL merge
    produced, and the recorded map satisfies the pass's own checker."""
    ids = [
        row["prerequisite_id"]
        for row in merged_prerequisites["prerequisites"]
    ]
    assert len(ids) == 16
    assert premap._map_checker(ids)(golden_premap["map"]) == []
    concepts = [
        row
        for topic in golden_premap["map"]["topics"]
        for row in topic["concepts"]
    ]
    assert len(golden_premap["map"]["topics"]) == 6
    assert len(concepts) == 15
    # One concept consolidates two captured prerequisites; the rest stay
    # apart. That grouping is the judgment the pass exists to make.
    assert sorted(
        len(row["prerequisites"]) for row in concepts
    ) == [1] * 14 + [2]
    # Every pre-concept has a recorded link verdict, and exactly one of
    # them is the honest empty list (necessity, advisory).
    linked = {row["pre_concept_id"] for row in golden_premap["needed_for"]}
    assert linked == {row["pre_concept_id"] for row in concepts}
    assert [
        row["pre_concept_id"] for row in golden_premap["needed_for"]
        if not row["post_concepts"]
    ] == ["PRC-0015"]
    # No source question, no Types/Cases/Examples, anywhere in the map
    # itself (the fixture's own _comment explains those words and is not
    # part of the recorded artefact).
    flat = json.dumps(
        {key: value for key, value in golden_premap.items()
         if key != "_comment"},
        ensure_ascii=False,
    )
    assert "QINV-" not in flat
    assert "Type 01" not in flat
    assert "Example 01" not in flat
    assert "// Types:" not in flat
    assert golden_premap["_comment"]
