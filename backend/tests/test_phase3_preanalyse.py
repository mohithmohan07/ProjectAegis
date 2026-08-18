"""The PRE lane's own misconception/error-analysis inventory (Q1).

Step-7 slice C2 regressions. Q1 reaches the Pre lane through an
inventory OF ITS OWN (``phase3/preanalyse.py``), and everything Q1 says
for the Post lane holds here unchanged:

* the inventory is the only analysis mechanism — a Pre concept's
  ``Misconception/ Error Analysis`` section exists ONLY where an item was
  allotted to it, and NOT every Pre concept receives one;
* Achieving Mastery stays on EVERY Pre concept;
* every item is allotted exactly once (R4); an item left undecided takes
  the bounded corrections and then The Fixer, never a silent drop;
* there is NO count quota of any kind — an empty inventory over a thin
  Pre map is legal and returns cleanly, never padded;
* the evidence is the Pre lane's own: the merged prerequisite capture and
  the Pre map's Descriptions, with NO current-chapter source block and NO
  question/task inventory QID anywhere in it;
* and the POST analyse pass is untouched — same POLICY_VERSION, same
  payload, same stored decision keys.
"""
from __future__ import annotations

import copy
import json
import re

import pytest

from app.services.phase3 import kernel, preanalyse, premap
from tests import test_phase3_premap as premap_golden
from tests import test_phase3_settle_golden as settle_golden

GOLDEN = settle_golden.GOLDEN


# ---------------------------------------------------------------------------
# the golden replay provider (shared with the runner golden)


def preanalyse_replay_provider(golden_preanalysis: dict):
    """Answer the inventory with the recorded items, and each allot batch
    with the recorded destinations for exactly the items it asks about.

    ``allot_to`` names the destination by pre-learning concept TITLE and
    is resolved against the request's own ``pre_concepts`` payload — the
    shape rne_place.json and rne_analysis.json already use, so the
    fixture never hard-codes a positional mint.
    """

    def provider(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prelearn.analyse.inventory":
            return {"items": [
                {
                    key: item[key]
                    for key in ("item_id", "kind", "text", "evidence",
                                "rationale")
                }
                for item in golden_preanalysis["items"]
            ]}
        by_title = {
            str(row.get("concept_title") or ""): str(
                row.get("pre_concept_id") or ""
            )
            for row in request.get("pre_concepts") or []
        }
        wanted = {
            str(row.get("item_id") or "")
            for row in request.get("items") or []
        }
        allotments = []
        for item in golden_preanalysis["items"]:
            if item["item_id"] not in wanted:
                continue
            assert item["allot_to"] in by_title, (
                f"no recorded pre-concept {item['allot_to']!r}"
            )
            allotments.append({
                "item_id": item["item_id"],
                "pre_concept_id": by_title[item["allot_to"]],
                "rationale": item["rationale"],
            })
        return {"allotments": allotments}

    return provider


# ---------------------------------------------------------------------------
# fixtures


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return settle_golden.envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def golden_preanalysis() -> dict:
    return json.loads(
        (GOLDEN / "rne_preanalysis.json").read_text(encoding="utf-8")
    )


def _verified_critic(_request: dict) -> dict:
    return {"verdict": "verified", "confidence": 0.999, "issues": []}


# A compact synthetic inventory over the compact synthetic Pre map that
# test_phase3_premap.py already carries: two pre-concepts, and only ONE
# of them receives items. The real chain's 15 rows are exercised by the
# runner golden.
INVENTORY = [
    {
        "item_id": "PLA-0001",
        "kind": "misconception",
        "text": (
            "The learner may believe that a sovereign ruler is simply the "
            "most powerful person in a state rather than the holder of its "
            "highest law-making authority."
        ),
        "evidence": "PR-0001",
        "rationale": "the belief the captured prerequisite exists to correct",
    },
    {
        "item_id": "PLA-0002",
        "kind": "error_analysis",
        "text": (
            "Students name who ruled a state and stop there, without saying "
            "where the authority to make its law was held, so the question "
            "of sovereignty is never answered."
        ),
        "evidence": "PR-0001",
        "rationale": "a process error in applying the prerequisite",
    },
]

ALLOTMENTS = {"PLA-0001": "PRC-0001", "PLA-0002": "PRC-0001"}


def _build(golden_envelope, *, inventory=None, allotments=None, **kwargs):
    inventory = INVENTORY if inventory is None else inventory
    allotments = ALLOTMENTS if allotments is None else allotments
    kwargs.setdefault(
        "provider",
        premap_golden._provider(inventory=inventory, allotments=allotments),
    )
    kwargs.setdefault("critic", _verified_critic)
    kwargs.setdefault("store", kernel.DecisionStore())
    return premap.build(
        golden_envelope,
        kwargs.pop("prerequisites", premap_golden.PREREQUISITES),
        kwargs.pop("post_rows", premap_golden.POST_ROWS),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Q1's law, carried over unchanged


def test_the_section_exists_only_where_an_item_was_allotted(golden_envelope):
    """Q1: the inventory is the ONLY analysis mechanism, and NOT every Pre
    concept receives one. PRC-0002 gets no item and therefore no section —
    that is the design, not a gap, and nothing spreads items to cover it."""
    result = _build(golden_envelope)

    by_id = {row["_pre_concept_id"]: row for row in result["rows"]}
    assert set(by_id) == {"PRC-0001", "PRC-0002"}

    allotted = by_id["PRC-0001"]
    assert "// Misconception/ Error Analysis: " in allotted["concept_details"]
    assert allotted["_aegis_analysis_allotments"] == ["PLA-0001", "PLA-0002"]

    unallotted = by_id["PRC-0002"]
    assert "Misconception" not in unallotted["concept_details"]
    assert "_aegis_analysis_allotments" not in unallotted

    # Achieving Mastery is unaffected: it is owed by, and present on,
    # EVERY Pre concept, allotted or not.
    for row in result["rows"]:
        assert row["concept_details"].startswith("Description: ")
        assert "\nAchieving Mastery: " in row["concept_details"]


def test_every_pre_concept_is_a_legal_destination_whatever_it_is_called(
    golden_envelope,
):
    """Rule 1: no title keyword decides which Pre concepts may receive a
    learner-visible item. The Post pass excludes culmination rows because
    the PIPELINE mints those rows and their titles; the Pre lane mints no
    culmination at all (``require_culmination=False``), so the same prefix
    test here would read MODEL-AUTHORED free text and re-home a
    prerequisite's item onto the wrong concept."""
    map_response = copy.deepcopy(premap_golden.MAP_RESPONSE)
    map_response["topics"][1]["concepts"][0]["concept_title"] = (
        "Culmination of the Map-Reading Skills of Earlier Years"
    )
    items = copy.deepcopy(INVENTORY)
    items[1]["evidence"] = "PR-0002"
    seen: dict[str, dict] = {}
    base = premap_golden._provider(
        map_response=map_response,
        inventory=items,
        allotments={"PLA-0001": "PRC-0001", "PLA-0002": "PRC-0002"},
    )

    def capturing(request: dict) -> dict:
        seen[str(request.get("stage") or "")] = copy.deepcopy(request)
        return base(request)

    # No fixer is wired: had the target set refused PRC-0002, the allot
    # checker would have raised rather than quietly re-homing the item.
    result = _build(golden_envelope, provider=capturing)

    assert [
        row["pre_concept_id"]
        for row in seen["prelearn.analyse.allot"]["pre_concepts"]
    ] == ["PRC-0001", "PRC-0002"]
    by_id = {row["_pre_concept_id"]: row for row in result["rows"]}
    assert by_id["PRC-0002"]["_aegis_analysis_allotments"] == ["PLA-0002"]
    assert "// Misconception/ Error Analysis: " in (
        by_id["PRC-0002"]["concept_details"]
    )
    assert "\nAchieving Mastery: " in by_id["PRC-0002"]["concept_details"]


def test_misconception_and_error_analysis_stay_two_distinct_meanings(
    golden_envelope,
):
    """They are never restated as one another: the rendered section keeps
    the two labelled halves, in the canonical order the Post lane uses."""
    result = _build(golden_envelope)

    details = result["rows"][0]["concept_details"]
    section = details.split("// Misconception/ Error Analysis: ", 1)[1]
    assert section.startswith("Misconceptions: ")
    misconception, error_analysis = section.split("; Error Analysis: ", 1)
    assert "highest law-making authority" in misconception
    assert "where the authority to make its law was held" in error_analysis
    assert "Students name who ruled" in error_analysis
    # and the prompts hold the two apart in as many words.
    from app.services.phase3 import prompts

    system = prompts.PREANALYSE_INVENTORY_SYSTEM
    assert "never restate one as the other" in system
    assert "incorrect learner belief" in system
    assert "concrete process error" in system


def test_the_rendered_section_is_the_existing_house_format_section(
    golden_envelope,
):
    """Not a new section: the SAME canonical label, the same " // "
    separator ``concept_refiner`` splits on, and the same row-private
    allotment marker the Post lane stamps — so the strict detailing
    parsers accept a Pre row carrying it with nothing new to learn."""
    from app.services import concept_refiner as cr
    from app.services import concept_validator
    from app.services.phase3 import assemble

    result = _build(golden_envelope)
    row = result["rows"][0]

    labels = [label for label, _ in cr.split_sections(row["concept_details"])]
    assert labels == ["Description", "Misconception/ Error Analysis"]
    assert cr.is_learner_analysis_label(labels[1])
    assert cr.is_combined_analysis_label(labels[1])
    # The marker field is the one already registered as a release audit
    # field and already read by the shared validator.
    assert concept_validator.ANALYSIS_ALLOTMENTS_FIELD == (
        assemble.ANALYSIS_ALLOTMENTS_FIELD
    )
    assert concept_validator.analysis_allotted_keys(result["rows"]) == {0}

    from app.services import build_concepts_release as release

    assert (
        concept_validator.ANALYSIS_ALLOTMENTS_FIELD
        in release._RELEASE_AUDIT_FIELDS
    )
    # The strict detailing parser accepts the composed content as the one
    # canonical learner-analysis section.
    content = dict(cr.split_sections(row["concept_details"]))[
        "Misconception/ Error Analysis"
    ]
    assert concept_validator._CANONICAL_ANALYSIS_CONTENT_RE.fullmatch(content)
    # And the advisory validator raises no analysis finding at all.
    codes = {finding["code"] for finding in result["validation"]}
    assert "unallotted_analysis_section" not in codes
    assert "analysis_section_format" not in codes

    # The actual publication cleaner used by stage_release upload and the
    # bulk-import writer keeps the section on a Pre row untouched.
    from app.services import concept_cleanup

    cleaned = concept_cleanup.clean_concept_record(dict(row))
    assert cleaned["concept_details"].startswith("Description: ")
    assert "// Misconception/ Error Analysis: " in cleaned["concept_details"]
    assert "\nAchieving Mastery: " in cleaned["concept_details"]


def test_a_section_on_an_unallotted_row_is_named_by_the_validator(
    golden_envelope,
):
    """Marker accounting, the other direction: Q1's allotment context is
    DERIVED from the rows, so a section on a row the Pre inventory never
    allotted an item to is reported (never silently accepted)."""
    result = _build(golden_envelope)
    rows = copy.deepcopy(result["rows"])
    rows[1]["concept_details"] += (
        " // Misconception/ Error Analysis: Misconceptions: smuggled in."
    )
    codes = {
        finding["code"] for finding in premap.validate_rows(rows)
    }
    assert "unallotted_analysis_section" in codes


# ---------------------------------------------------------------------------
# no quota, ever


def test_an_empty_inventory_over_a_thin_pre_map_is_legal_and_clean(
    golden_envelope,
):
    """A thin Pre map yields few items or NONE and is never padded. The
    allot decision is never even spent, and every row ships without a
    section — which is a complete Pre map, not an incomplete one."""
    stages: list[str] = []
    base = premap_golden._provider(inventory=[])

    def counting(request: dict) -> dict:
        stages.append(str(request.get("stage") or ""))
        return base(request)

    result = _build(golden_envelope, provider=counting)

    assert "prelearn.analyse.inventory" in stages
    assert "prelearn.analyse.allot" not in stages
    assert result["analysis"] == {
        "inventory": [],
        "allotments": {},
        "rationales": {},
        "review_flags": {},
    }
    for row in result["rows"]:
        assert "Misconception" not in row["concept_details"]
        assert "_aegis_analysis_allotments" not in row
        assert "\nAchieving Mastery: " in row["concept_details"]


def test_no_count_quota_rides_the_prompt_the_payload_or_the_code(
    golden_envelope,
):
    """No numeric bound anywhere: not in the rules text, not in the
    payload, not in the module. How many items the captured evidence
    supports is the model's judgment alone (Rule 1: no volume-derived
    structure)."""
    from app.services.phase3 import prompts

    seen: list[dict] = []
    base = premap_golden._provider(
        inventory=INVENTORY, allotments=ALLOTMENTS
    )

    def capturing(request: dict) -> dict:
        if str(request.get("stage") or "").startswith("prelearn.analyse."):
            seen.append(copy.deepcopy(request))
        return base(request)

    _build(golden_envelope, provider=capturing)
    assert [request["stage"] for request in seen] == [
        "prelearn.analyse.inventory", "prelearn.analyse.allot",
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
        prompts.PREANALYSE_INVENTORY_SYSTEM,
        prompts.PREANALYSE_ALLOT_SYSTEM,
        prompts.PREANALYSE_CRITIC_SYSTEM,
    ):
        assert not quota_language.search(system)

    source = premap_golden._code_only(preanalyse)
    assert not re.search(r"\blen\([^)]*\)\s*[<>]=?\s*\d", source)
    assert not re.search(r"\blen\([^)]*\)\s*[*/]\s*\d", source)
    # The only numeric constant is the payload-size batch, and it bounds
    # a request, never an authored count.
    assert preanalyse._ALLOT_BATCH_SIZE == 8
    # …and it is the ONLY multi-digit-free numeric decision in the module:
    # no literal survives anywhere else, not even as a message truncation
    # (the shape that put the repo's last literal-40 inside a validator).
    assert not re.search(r"\b(?<!PLA-)\d{2,}\b", source)
    assert not re.search(r"MIN_|MAX_|_QUOTA|TARGET_", source)


# ---------------------------------------------------------------------------
# evidence isolation: the Pre lane's own evidence, and nothing else


def test_the_inventory_payload_carries_no_chapter_source_and_no_qid(
    golden_envelope,
):
    """A prerequisite misconception is about the PREREQUISITE. The payload
    carries the merged capture and the Pre map's Descriptions — and no
    current-chapter source block, no question/task inventory entry, and
    no source QID anywhere (the no-extraction steer, mechanically)."""
    seen: list[dict] = []
    base = premap_golden._provider(
        inventory=INVENTORY, allotments=ALLOTMENTS
    )

    def capturing(request: dict) -> dict:
        if str(request.get("stage") or "").startswith("prelearn.analyse."):
            seen.append(copy.deepcopy(request))
        return base(request)

    _build(golden_envelope, provider=capturing)
    inventory_request, allot_request = seen

    assert set(inventory_request["evidence"]) == {
        "prerequisites", "pre_concepts"
    }
    # No chapter source block rides it — neither a block id nor a block
    # text — and the question/task inventory is absent entirely.
    flat = json.dumps(seen, ensure_ascii=False)
    for block in golden_envelope["graph"]["blocks"]:
        assert str(block["block_id"]) not in flat
    for item in golden_envelope["inventory"]["items"]:
        assert str(item["qid"]) not in flat
    assert "source_blocks" not in json.dumps(
        inventory_request["evidence"], ensure_ascii=False
    )
    assert "question_task_inventory" not in flat
    assert "pre_concepts" in allot_request
    assert "source_blocks" not in allot_request

    # The evidence IS the capture and the Pre Descriptions.
    assert [
        row["prerequisite_id"]
        for row in inventory_request["evidence"]["prerequisites"]
    ] == ["PR-0001", "PR-0002"]
    assert [
        row["pre_concept_id"]
        for row in inventory_request["evidence"]["pre_concepts"]
    ] == ["PRC-0001", "PRC-0002"]


def test_a_capture_naming_a_source_question_is_redacted_before_the_payload(
    golden_envelope,
):
    """Upstream provenance may legitimately name the question it was read
    from; the Pre analysis lane is SHOWN it redacted, exactly as the map
    payload is — the run completes and the identity never arrives."""
    leaking = copy.deepcopy(premap_golden.PREREQUISITES)
    leaking["prerequisites"][0]["rationale"] = (
        "The exercise QINV-0004 assumes the learner already holds this."
    )
    seen: list[dict] = []
    base = premap_golden._provider(
        inventory=INVENTORY, allotments=ALLOTMENTS
    )

    def capturing(request: dict) -> dict:
        if str(request.get("stage") or "") == "prelearn.analyse.inventory":
            seen.append(copy.deepcopy(request))
        return base(request)

    result = _build(
        golden_envelope, prerequisites=leaking, provider=capturing,
    )
    assert result["rows"]
    payload = json.dumps(seen[0], ensure_ascii=False)
    assert "QINV-0004" not in payload
    assert "a source question of this chapter" in payload
    assert "assumes the learner already holds this" in payload


def test_an_item_carrying_a_source_question_id_is_refused(golden_envelope):
    """An item that lifted a question's identity fails closed, and it does
    so BEFORE any spend: the item text rides the allot payload, which is
    guarded pre-spend, so the refusal names that payload rather than the
    row it would have been stamped onto."""
    poisoned = copy.deepcopy(INVENTORY)
    poisoned[0]["text"] += " (compare QINV-0004)"

    with pytest.raises(
        premap.PreExtractionError,
        match="Pre analysis allotment payload carries source question",
    ):
        _build(golden_envelope, inventory=poisoned)


def test_an_analysis_rationale_carrying_a_source_question_id_is_refused(
    golden_envelope,
):
    """The whole analysis is inside the guarded surface, not just the two
    fields that reach a row. An inventory item's ``rationale`` and an
    allotment's are never stamped anywhere — but they ride the ``pre_map``
    carry channel and its snapshot, which is the artefact the steer names
    ("a Pre row or the Pre release payload"), exactly as ``topics`` does.
    Both fail closed at the map guard."""
    poisoned = copy.deepcopy(INVENTORY)
    poisoned[0]["rationale"] += " (the belief QINV-0004 tests)"
    with pytest.raises(
        premap.PreExtractionError, match="a Pre-Learning concept row",
    ):
        _build(golden_envelope, inventory=poisoned)

    def leaking_allot(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "prelearn.analyse.allot":
            return {"allotments": [
                {
                    "item_id": str(row.get("item_id") or ""),
                    "pre_concept_id": "PRC-0001",
                    "rationale": "the belief QINV-0004 tests",
                }
                for row in request.get("items") or []
            ]}
        return premap_golden._provider(
            inventory=INVENTORY, allotments=ALLOTMENTS
        )(request)

    with pytest.raises(
        premap.PreExtractionError, match="a Pre-Learning concept row",
    ):
        _build(golden_envelope, provider=leaking_allot)


def test_a_critic_naming_a_source_question_flags_and_never_gates(
    golden_envelope,
):
    """Q10, and the reason the guard covers the analysis MINUS its review
    flags: the Pre-analysis critic is asked about a prerequisite's
    misconceptions, so a critic answering usefully by naming the question
    must not turn its own advisory dissent into a permanent gate over
    work already paid for. The flag is recorded; the run completes."""
    def dissenting(_request: dict) -> dict:
        return {
            "verdict": "revise",
            "confidence": 0.4,
            "issues": ["this item restates the source question QINV-0004"],
        }

    result = _build(golden_envelope, critic=dissenting)

    assert [row["_pre_concept_id"] for row in result["rows"]] == [
        "PRC-0001", "PRC-0002",
    ]
    flat = json.dumps(result["rows"], ensure_ascii=False)
    assert "QINV-0004" in flat  # in a review flag, never in the content
    for row in result["rows"]:
        assert "QINV-0004" not in row["concept_details"]


# ---------------------------------------------------------------------------
# R4 exact-once


def test_an_unallotted_item_routes_to_the_fixer_never_dropped(
    golden_envelope,
):
    """R4: every item is allotted to exactly one Pre concept. An item the
    model leaves undecided is a contract defect — bounded corrections,
    then one recorded, flagged Fixer decision (Q13). It is never
    dropped."""
    def broken(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "premap.map":
            return copy.deepcopy(premap_golden.MAP_RESPONSE)
        if stage == "prelearn.analyse.inventory":
            return {"items": copy.deepcopy(INVENTORY)}
        if stage == "prelearn.analyse.allot":
            return {"allotments": [
                {
                    "item_id": "PLA-0001",
                    "pre_concept_id": "PRC-0001",
                    "rationale": "and PLA-0002 quietly forgotten",
                },
            ]}
        return premap_golden._links_for(
            request, premap_golden.DEFAULT_LINKS
        )

    fixer_calls = {"n": 0}

    def fixer(request: dict) -> dict:
        fixer_calls["n"] += 1
        assert any(
            "unallotted item(s): PLA-0002" in defect
            for defect in request["blocked_check"]
        )
        return {
            "allotments": [
                {
                    "item_id": item["item_id"],
                    "pre_concept_id": "PRC-0001",
                    "rationale": "recovered",
                }
                for item in INVENTORY
            ],
            "rationale": "the forgotten item was allotted, not dropped",
        }

    result = _build(golden_envelope, provider=broken, fixer=fixer)
    assert fixer_calls["n"] == 1
    assert set(result["analysis"]["allotments"]) == {"PLA-0001", "PLA-0002"}
    assert result["rows"][0]["_aegis_analysis_allotments"] == [
        "PLA-0001", "PLA-0002",
    ]
    assert any(
        flag.startswith("fixer: blocked=")
        for flags in result["review_flags"].values()
        for flag in flags
    )


def test_an_unallotted_item_without_a_fixer_fails_closed(golden_envelope):
    def broken(request: dict) -> dict:
        stage = str(request.get("stage") or "")
        if stage == "premap.map":
            return copy.deepcopy(premap_golden.MAP_RESPONSE)
        if stage == "prelearn.analyse.inventory":
            return {"items": copy.deepcopy(INVENTORY)}
        if stage == "prelearn.analyse.allot":
            return {"allotments": []}
        return premap_golden._links_for(
            request, premap_golden.DEFAULT_LINKS
        )

    with pytest.raises(kernel.ContractError, match="unallotted item"):
        _build(golden_envelope, provider=broken)


def test_an_item_naming_an_unknown_pre_concept_is_a_defect(golden_envelope):
    with pytest.raises(kernel.ContractError, match="unknown pre_concept_id"):
        _build(
            golden_envelope,
            allotments={"PLA-0001": "PRC-9999", "PLA-0002": "PRC-0001"},
        )


# ---------------------------------------------------------------------------
# the POST analyse pass is untouched


def test_the_post_analyse_pass_is_untouched(golden_envelope):
    """A prerequisite misconception must never compete with a Post row for
    the same item, and the Post pass's stored decisions must not re-key.
    Its POLICY_VERSION, its item mint, and its payload are pinned here."""
    from app.services.phase3 import analyse as post

    assert post.POLICY_VERSION == "analysis-1"
    assert preanalyse.POLICY_VERSION == "pre-analysis-1"
    assert post.POLICY_VERSION != preanalyse.POLICY_VERSION
    assert post.mint_item_ids(2) == ["LA-0001", "LA-0002"]
    assert preanalyse.mint_item_ids(2) == ["PLA-0001", "PLA-0002"]

    # The Post pass's evidence builder still reads the chapter's source
    # blocks and its question/task inventory, unchanged — the two payload
    # keys whose absence identifies the Pre payload.
    evidence = post.build_evidence(golden_envelope)
    assert set(evidence) == {"source_blocks", "question_task_inventory"}
    assert evidence["source_blocks"]
    assert evidence["question_task_inventory"]

    # And the decision key of a Post inventory build is unchanged by
    # anything in this slice: recomputed here from the same payload shape
    # the pass builds.
    from app.services.phase3 import prompts

    payload = {
        "stage": "analyse.inventory",
        "rules": "…",
        "evidence": evidence,
    }
    assert kernel.decision_key(
        kind="analyse.inventory",
        unit_id="chapter",
        envelope_sha256=golden_envelope["envelope_sha256"],
        payload=payload,
        policy_version=post.POLICY_VERSION,
    ) == kernel.decision_key(
        kind="analyse.inventory",
        unit_id="chapter",
        envelope_sha256=golden_envelope["envelope_sha256"],
        payload=payload,
        policy_version="analysis-1",
    )
    # The two lanes never share a decision kind either.
    assert prompts.ANALYSE_INVENTORY_SYSTEM != (
        prompts.PREANALYSE_INVENTORY_SYSTEM
    )


def test_the_two_lanes_allot_over_disjoint_target_sets(golden_envelope):
    """The Pre pass allots over PRC- ids only; the Post pass over the
    settled ordering's CONCEPT- ids only. Neither can name the other's
    rows, which is why a prerequisite misconception can never compete
    with a Post row for the same item."""
    seen: list[dict] = []
    base = premap_golden._provider(
        inventory=INVENTORY, allotments=ALLOTMENTS
    )

    def capturing(request: dict) -> dict:
        if str(request.get("stage") or "") == "prelearn.analyse.allot":
            seen.append(copy.deepcopy(request))
        return base(request)

    _build(golden_envelope, provider=capturing)
    targets = {
        row["pre_concept_id"] for row in seen[0]["pre_concepts"]
    }
    assert targets == {"PRC-0001", "PRC-0002"}
    assert not any(target.startswith("CONCEPT-") for target in targets)
    # And no Post concept title rides the Pre allot payload at all.
    flat = json.dumps(seen[0], ensure_ascii=False)
    for row in premap_golden.POST_ROWS:
        assert row["concept_title"] not in flat


# ---------------------------------------------------------------------------
# decide-once


def test_decide_once_replays_the_pre_inventory_for_free(golden_envelope):
    calls = {"n": 0}
    base = premap_golden._provider(
        inventory=INVENTORY, allotments=ALLOTMENTS
    )

    def counted(request: dict) -> dict:
        if str(request.get("stage") or "").startswith("prelearn.analyse."):
            calls["n"] += 1
        return base(request)

    store = kernel.DecisionStore()
    first = _build(golden_envelope, provider=counted, store=store)
    assert calls["n"] == 2  # one inventory decision + one allot batch
    second = _build(golden_envelope, provider=counted, store=store)
    assert calls["n"] == 2
    assert first["analysis"] == second["analysis"]


# ---------------------------------------------------------------------------
# the golden chapter


def test_the_golden_chapter_pre_inventory_replays(
    golden_envelope, golden_preanalysis,
):
    """The recorded fixture, replayed against the compact Pre map is not
    meaningful — the golden chapter's own 15-row map is exercised by the
    runner golden. What this pins is the fixture's own shape: positional
    ids, two distinct kinds, and a destination for every item."""
    items = golden_preanalysis["items"]
    assert [item["item_id"] for item in items] == preanalyse.mint_item_ids(
        len(items)
    )
    assert {item["kind"] for item in items} == set(preanalyse.ITEM_KINDS)
    assert all(item["text"] and item["evidence"] and item["allot_to"]
               for item in items)
