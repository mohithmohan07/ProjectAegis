"""Golden gate 1: Settle reproduces job 23's validated topology.

Job 23 was the first run to validate a complete concept topology (53
rows: 47 normal concepts with full learner analysis plus 6 culmination
recaps). Its sealed envelope and settled rows are recorded fixtures;
replay providers derived from the golden rows drive the new Settle pass,
which must reproduce them.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from app.services import concept_refiner as cr
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel, settle

GOLDEN = Path(__file__).parent / "golden"

_ANALYSIS_SPLIT = re.compile(
    r"\s*//\s*Misconception/?\s*Error Analysis:\s*", re.IGNORECASE
)


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return envelope_mod.load(GOLDEN / "rne_envelope.json")


@pytest.fixture(scope="module")
def golden_rows() -> list[dict]:
    return json.loads(
        (GOLDEN / "rne_settled_rows.json").read_text(encoding="utf-8")
    )["records"]


def _origin_groups_by_topic(golden_rows: list[dict]) -> dict[str, list[list[dict]]]:
    """Golden normal rows as consecutive origin groups, per topic."""

    grouped: dict[str, list[list[dict]]] = {}
    for row in golden_rows:
        if not row.get("_phase32_topology_decision"):
            continue  # culmination
        topic_id = str(row.get("_semantic_topic_id"))
        groups = grouped.setdefault(topic_id, [])
        origin = str(row.get("_phase32_origin_concept_id"))
        if groups and str(
            groups[-1][0].get("_phase32_origin_concept_id")
        ) == origin:
            groups[-1].append(row)
        else:
            groups.append([row])
    return grouped


def _replay_map(golden_envelope: dict, golden_rows: list[dict]) -> dict[str, list[dict]]:
    """Map the new pass's concept ids to golden origin groups.

    Golden rows record ``_phase32_source_order`` — the 1-based index of
    their origin concept over ALL 81% skeleton records (culminations
    included). That pins every origin group to exactly one input row.
    Keep decisions must then agree on the title (case aside); anything
    else is a fixture error worth failing loudly on.
    """

    skeleton = golden_envelope["skeleton_rows"]
    normal_positions: dict[int, str] = {}
    normal_index = 0
    for position, row in enumerate(skeleton, start=1):
        if cr.is_culmination(str(row.get("concept_title") or "")):
            continue
        normal_index += 1
        normal_positions[position] = f"TOPOLOGY-CONCEPT-{normal_index:04d}"

    groups: dict[int, list[dict]] = {}
    for row in golden_rows:
        if not row.get("_phase32_topology_decision"):
            continue
        groups.setdefault(int(row["_phase32_source_order"]), []).append(row)

    mapping: dict[str, list[dict]] = {}
    for source_order, group in groups.items():
        concept_id = normal_positions[source_order]
        group = sorted(
            group, key=lambda row: int(row.get("_phase32_segment_order") or 1)
        )
        input_row = skeleton[source_order - 1]
        if group[0]["_phase32_topology_decision"] == "keep":
            assert _normal(group[0]["concept_title"]).casefold() == (
                _normal(input_row["concept_title"]).casefold()
            ), f"source-order mapping broke at {concept_id}"
        mapping[concept_id] = group
    assert len(mapping) == len(normal_positions)
    return mapping


def _providers(mapping: dict[str, list[dict]]):
    def topology(request: dict) -> dict:
        decisions = []
        for concept in request["concepts"]:
            group = mapping[concept["concept_id"]]
            decisions.append({
                "concept_id": concept["concept_id"],
                "decision": group[0]["_phase32_topology_decision"],
                "confidence": 0.99,
                "reason": "replayed from the golden settled rows",
                "segments": [
                    {
                        "concept_title": row["concept_title"],
                        "parent_concept": row["parent_concept"],
                        "concept_details": _ANALYSIS_SPLIT.split(
                            row["concept_details"], maxsplit=1
                        )[0],
                        "keywords": row["keywords"],
                    }
                    for row in group
                ],
            })
        return {"decisions": decisions}

    def _segment(concept_id: str) -> dict:
        origin, _, order = concept_id.partition("#")
        return mapping[origin][int(order) - 1]

    def grounding(request: dict) -> dict:
        concepts = []
        for concept in request["concepts"]:
            row = _segment(concept["concept_id"])
            concepts.append({
                "concept_id": concept["concept_id"],
                "source_block_ids": list(row["_source_block_ids"]),
                "confidence": float(
                    row.get("_source_grounding_confidence") or 0.99
                ),
                "reason": "replayed from the golden settled rows",
            })
        return {"concepts": concepts}

    def analysis(request: dict) -> dict:
        rows = []
        for concept in request["concepts"]:
            row = _segment(concept["concept_id"])
            # Q1: the golden settled rows carry Description + Mastery
            # only; learner analysis lives in rne_analysis.json (the
            # chapter inventory fixture) and is stamped by Assemble.
            body = _ANALYSIS_SPLIT.split(
                row["concept_details"], maxsplit=1
            )[0]
            description, mastery = re.split(
                r"\nAchieving Mastery:\s*", body, maxsplit=1
            )
            rows.append({
                "concept_id": concept["concept_id"],
                "concept_description": re.sub(
                    r"^Description:\s*", "", description
                ),
                "achieving_mastery": mastery,
            })
        response: dict = {"rows": rows}
        culms = request.get("culminations") or []
        if culms:
            response["culminations"] = [
                {
                    "concept_id": culm["concept_id"],
                    # The golden replay keys culminations by (topic, title),
                    # so the fixture returns the draft name as the authored
                    # one; a planned row must echo it by contract anyway.
                    "culmination_title": culm["draft_culmination_title"],
                    "consolidation": (
                        "Together these concepts let the learner connect "
                        + ", ".join(culm["member_concepts"][:3])
                        + " into one coherent account of the topic, moving "
                        "from each idea on its own to the combined "
                        "understanding the chapter builds toward."
                    ),
                    "achieving_mastery": (
                        ""
                        if culm.get("planned")
                        else "Combine "
                        + ", ".join(culm["member_concepts"][:3])
                        + " in one task that needs them together."
                    ),
                }
                for culm in culms
            ]
        return response

    def critic(_request: dict) -> dict:
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    return topology, grounding, analysis, critic


def test_settle_reproduces_job_23s_validated_topology(
    golden_envelope, golden_rows,
):
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = _providers(mapping)
    store = kernel.DecisionStore()

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=store,
    )

    assert len(settled) == len(golden_rows) == 53

    def _key(row: dict) -> tuple[str, str]:
        return (
            str(row["_semantic_topic_id"]),
            _normal(row["concept_title"]).casefold(),
        )

    produced_by_key = {_key(row): row for row in settled}
    assert len(produced_by_key) == 53, "settled titles collide"

    for golden in golden_rows:
        label = golden["concept_title"]
        produced = produced_by_key[_key(golden)]
        assert _normal(produced["parent_concept"]) == _normal(
            golden["parent_concept"]
        ), label
        assert produced.get("_phase32_topology_decision") == golden.get(
            "_phase32_topology_decision"
        ), label
        if golden.get("_phase32_topology_decision"):
            assert produced["_source_block_ids"] == list(
                golden["_source_block_ids"]
            ), label
            assert _normal(produced["concept_details"]) == _normal(
                golden["concept_details"]
            ), label
            # Q1: Settle mints no learner-analysis section — the chapter
            # inventory (rne_analysis.json) owns it, stamped at Assemble.
            assert "Misconception" not in produced["concept_details"], label
            assert "Achieving Mastery:" in produced["concept_details"], label
        else:
            # Culminations derive their grounding from their topic's rows.
            assert produced["_source_block_ids"], label
            assert produced["_source_grounding_contract"] == (
                "derived-from-verified-topic-concepts"
            )

    # Row order groups by topic exactly like the golden set.
    assert [row["_semantic_topic_id"] for row in settled] == [
        row["_semantic_topic_id"] for row in golden_rows
    ]

    # The verified replay leaves no semantic dissent. Historical fixtures
    # have remote-only figures, so missing pinned pixels remain explicit.
    assert all(
        "concept_visual_evidence_unavailable" in flag
        for row in settled for flag in row.get("review_flags") or []
    )


def test_parallel_topics_produce_the_sequential_output(
    golden_envelope, golden_rows, monkeypatch,
):
    """Per-run decision parallelism is pure orchestration: with workers > 1
    the settled rows are byte-identical to the sequential default."""
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = _providers(mapping)

    monkeypatch.delenv("AEGIS_PHASE3_DECISION_WORKERS", raising=False)
    sequential = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )
    monkeypatch.setenv("AEGIS_PHASE3_DECISION_WORKERS", "3")
    parallel = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    assert parallel == sequential


def test_settle_resume_is_free_and_identical(golden_envelope, golden_rows):
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = _providers(mapping)
    calls = {"n": 0}

    def counted_topology(request: dict) -> dict:
        calls["n"] += 1
        return topology(request)

    store = kernel.DecisionStore()
    first = settle.settle(
        golden_envelope,
        topology_provider=counted_topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=store,
    )
    calls_after_first = calls["n"]
    second = settle.settle(
        golden_envelope,
        topology_provider=counted_topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=store,
    )

    assert calls["n"] == calls_after_first
    assert second == first


def test_cross_topic_grounding_ships_flagged_instead_of_failing(
    golden_envelope, golden_rows,
):
    """A concept whose material is taught in another topic (recovered
    chapter-opening rows) grounds on the teaching blocks and ships flagged
    — bounded corrections push back toward the own topic, but an honest
    cross-topic grounding never kills the chapter."""
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, base_grounding, analysis, critic = _providers(mapping)

    # A real block from a different topic than the first grounded concept.
    blocks = golden_envelope["graph"]["blocks"]
    victim: dict = {}

    def grounding(request: dict) -> dict:
        response = base_grounding(request)
        if not victim:
            topic_id = request["topic"]["topic_id"]
            foreign = next(
                str(row["block_id"]) for row in blocks
                if str(row.get("topic_id") or "") not in ("", topic_id)
            )
            row = response["concepts"][0]
            row["source_block_ids"] = [foreign]
            row["reason"] = "the claim is taught inside a later section"
            victim["concept_id"] = row["concept_id"]
            victim["block_id"] = foreign
        else:
            for row in response["concepts"]:
                if row["concept_id"] == victim["concept_id"]:
                    row["source_block_ids"] = [victim["block_id"]]
        return response

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    assert len(settled) == 53
    flagged = [
        row for row in settled
        if any(
            "outside its topic" in flag
            for flag in row.get("review_flags") or []
        )
    ]
    assert len(flagged) == 1
    assert flagged[0]["_source_block_ids"] == [victim["block_id"]]


def test_grounding_on_an_unknown_block_still_fails_closed(
    golden_envelope, golden_rows,
):
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, base_grounding, analysis, critic = _providers(mapping)

    def grounding(request: dict) -> dict:
        response = base_grounding(request)
        response["concepts"][0]["source_block_ids"] = ["BLOCK-FABRICATED"]
        return response

    with pytest.raises(kernel.ContractError, match="unknown block"):
        settle.settle(
            golden_envelope,
            topology_provider=topology,
            grounding_provider=grounding,
            analysis_provider=analysis,
            critic=critic,
            store=kernel.DecisionStore(),
        )


def _authored_row(concept_id: str = "C-1", **overrides) -> dict:
    row = {
        "concept_id": concept_id,
        "concept_description": (
            "A rational number is any number expressible as p/q with "
            "integers p and q where q is non-zero; the form is not unique "
            "because multiplying numerator and denominator by the same "
            "non-zero integer yields an equivalent rational, so comparisons "
            "and arithmetic first bring numbers to a common denominator."
        ),
        "achieving_mastery": (
            "Learners can express any terminating decimal as p/q in "
            "standard form."
        ),
    }
    row.update(overrides)
    return row


def test_authoring_checker_accepts_a_complete_authored_batch():
    check = settle._authoring_checker(["C-1"])
    assert check({"rows": [_authored_row()]}) == []


def test_authoring_checker_rejects_repeated_mastery():
    check = settle._authoring_checker(["C-1", "C-2"])
    first = _authored_row("C-1")
    second = _authored_row(
        "C-2",
        achieving_mastery=first["achieving_mastery"],
    )
    defects = check({"rows": [first, second]})
    assert any("distinct mastery statement" in d for d in defects)


def test_authoring_checker_demands_no_learner_analysis():
    """Q1: the every-concept analysis contract is retired — a row
    without any misconception_error_analysis field is complete, and a
    description smuggling an analysis label is the defect."""
    check = settle._authoring_checker(["C-1"])
    assert check({"rows": [_authored_row()]}) == []
    smuggled = _authored_row(
        concept_description=_authored_row()["concept_description"]
        + " Misconceptions: learners think fractions are always below one."
    )
    defects = check({"rows": [smuggled]})
    assert any("never authored here" in d for d in defects)


def test_settle_author_policy_version_is_rekeyed_for_q1():
    """The Q1 schema change must mint new settle.author decision keys so
    a stored pre-Q1 authoring decision (with analysis bundled) can never
    replay past the unbundled checker."""
    from app.services import semantic_confidence_policy as confidence_policy

    assert settle.AUTHOR_POLICY_SUFFIX == "-q1"
    author_policy = confidence_policy.POLICY_VERSION + (
        settle.AUTHOR_POLICY_SUFFIX
    )
    assert author_policy != confidence_policy.POLICY_VERSION


def test_description_substance_is_not_a_word_count_gate():
    check = settle._authoring_checker(["C-1"])
    row = _authored_row(concept_description="A prime has two distinct positive factors.")
    assert check({"rows": [row]}) == []
    assert any("empty" in defect for defect in check({"rows": [
        _authored_row(concept_description=""),
    ]}))


def test_authoring_checker_rejects_raw_math_outside_katex_tags():
    """Bare TeX that deterministic repair cannot wrap (an equation with
    bracket groups) must come back as a correction defect; unambiguous
    lone tokens are wrapped silently and pass."""
    check = settle._authoring_checker(["C-1"])
    row = _authored_row(
        concept_description=(
            "For the first n terms of an arithmetic progression, let a be "
            "the first term and d the common difference; the sum formula "
            "S_n = n/2[2a + (n − 1)d] links these quantities, and when any "
            "three are known the fourth follows by substituting into the "
            "formula and solving the resulting linear or quadratic "
            "equation in the unknown."
        ),
    )
    defects = check({"rows": [row]})
    assert any("wire" in d and "[Katex]" in d for d in defects)

    wrappable = _authored_row(
        concept_description=(
            "The nth term a_n of an arithmetic progression grows by the "
            "common difference at every step, so listing successive values "
            "and checking that each consecutive difference is the same "
            "fixed number verifies the progression before any formula is "
            "applied to compute later terms from earlier ones."
        ),
    )
    assert check({"rows": [wrappable]}) == []


def test_culmination_description_is_the_authored_consolidation(
    golden_envelope, golden_rows,
):
    """The culmination Description is exactly the model-authored
    consolidation paragraph — no code-composed 'Recap of <titles>' prefix
    is ever prepended (the recap machinery is deleted)."""
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = _providers(mapping)

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    culminations = [
        row for row in settled if not row.get("_phase32_topology_decision")
    ]
    assert culminations
    for row in culminations:
        details = str(row["concept_details"])
        assert details.startswith(
            "Description: Together these concepts let the learner connect"
        ), row["concept_title"]
        assert "Recap of" not in details
        # Contract §11.1: the culmination carries its own authored mastery
        # line, composed exactly like a normal row's.
        assert "\nAchieving Mastery: Combine " in details, row["concept_title"]
        assert details.count("Achieving Mastery:") == 1
        # The authored consolidation, title and mastery are mandatory, so
        # no culmination ships with a review flag on this golden replay.
        assert not row.get("review_flags")


def test_culmination_title_is_the_authored_synthesis_name(
    golden_envelope, golden_rows,
):
    """The culmination title is what the authoring pass returns from the
    FINAL member set, not the pre-Settle draft the request carries."""
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = _providers(mapping)
    authored_title = "Culmination - Reading the Chapter as One Account"
    drafts: list[str] = []

    def renaming_analysis(request: dict) -> dict:
        response = analysis(request)
        culms = response.get("culminations") or []
        if culms and not request["culminations"][0].get("planned"):
            drafts.append(request["culminations"][0]["draft_culmination_title"])
            culms[0]["culmination_title"] = authored_title
        return response

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=renaming_analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    assert drafts, "no unplanned culmination was authored"
    renamed = [
        row for row in settled if row["concept_title"] == authored_title
    ]
    assert len(renamed) == len(drafts)
    for row in renamed:
        assert not row.get("review_flags")
        assert row["concept_title"] not in drafts
    # The request names the supplied title as a draft, never as the
    # response field, so the authored name is unambiguous.
    assert all(draft.startswith("Culmination - ") for draft in drafts)


def test_authoring_checker_requires_culmination_title_and_mastery():
    check = settle._authoring_checker(["C-1"], ["CULM#0"])
    row = {
        "concept_id": "C-1",
        "concept_description": "The teaching paragraph.",
        "achieving_mastery": "Apply the rule to a new case.",
    }
    good = {
        "concept_id": "CULM#0",
        "culmination_title": "Culmination - Using the Rule End to End",
        "consolidation": "Together these let the learner finish the task.",
        "achieving_mastery": "Combine the rule with its conditions in one solution.",
    }
    assert check({"rows": [row], "culminations": [good]}) == []

    no_title = {**good, "culmination_title": ""}
    assert any(
        "culmination_title must begin with the exact prefix" in defect
        for defect in check({"rows": [row], "culminations": [no_title]})
    )
    # A bare prefix normalises to "Culmination -", which names nothing and
    # so fails the same prefix rule.
    bare = {**good, "culmination_title": "Culmination - "}
    assert any(
        "culmination_title must begin with the exact prefix" in defect
        for defect in check({"rows": [row], "culminations": [bare]})
    )
    list_form = {**good, "culmination_title": "Recap of A, B"}
    assert any(
        "exact prefix 'Culmination - '" in defect
        for defect in check({"rows": [row], "culminations": [list_form]})
    )
    no_mastery = {**good, "achieving_mastery": ""}
    assert check({"rows": [row], "culminations": [no_mastery]}) == [
        "CULM#0 achieving_mastery is empty"
    ]
    repeated = {**good, "achieving_mastery": "Apply the rule to a new case."}
    assert any(
        "CULM#0 achieving_mastery repeats C-1's" in defect
        for defect in check({"rows": [row], "culminations": [repeated]})
    )
    labelled = {**good, "achieving_mastery": "Achieving Mastery: Combine both."}
    assert check({"rows": [row], "culminations": [labelled]}) == []


def test_authoring_checker_makes_a_planned_culmination_echo_its_title():
    planned_title = "Culmination: Planned stanza close"
    check = settle._authoring_checker(
        ["C-1"], ["CULM#0"], planned_titles={"CULM#0": planned_title},
    )
    row = {
        "concept_id": "C-1",
        "concept_description": "The teaching paragraph.",
        "achieving_mastery": "Apply the rule to a new case.",
    }
    echoed = {
        "concept_id": "CULM#0",
        "culmination_title": planned_title,
        "consolidation": "Together these let the learner finish the task.",
        "achieving_mastery": "",
    }
    assert check({"rows": [row], "culminations": [echoed]}) == []
    renamed = {**echoed, "culmination_title": "Culmination - Something new"}
    assert check({"rows": [row], "culminations": [renamed]}) == [
        "CULM#0 is a sealed-plan culmination: culmination_title must be "
        "returned exactly as supplied"
    ]


def test_authoring_critic_dissent_flags_the_authored_rows(
    golden_envelope, golden_rows,
):
    """Settle's content-authoring decision now runs under the live advisory
    critic: its dissent lands as review flags on the authored rows and
    never blocks or rewrites the authored content."""
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, _critic = _providers(mapping)

    def critic(request: dict) -> dict:
        if request.get("stage") == "content_authoring":
            return {
                "verdict": "rejected",
                "confidence": 0.95,
                "issues": ["the analysis restates the description"],
            }
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=critic,
        store=kernel.DecisionStore(),
    )

    assert len(settled) == 53
    assert any(
        "restates the description" in flag
        for row in settled
        for flag in row.get("review_flags") or []
    )
    # Authored content stands despite the dissent.
    for row in settled:
        if row.get("_phase32_topology_decision"):
            assert "Achieving Mastery:" in row["concept_details"]


def test_critic_dissent_ships_flags_on_the_settled_rows(
    golden_envelope, golden_rows,
):
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, _critic = _providers(mapping)

    def dissenting_critic(request: dict) -> dict:
        return {
            "verdict": "rejected",
            "confidence": 0.93,
            "issues": ["the first block does not support the causal claim"],
        }

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=analysis,
        critic=dissenting_critic,
        store=kernel.DecisionStore(),
    )

    assert len(settled) == 53
    flagged = [row for row in settled if row.get("review_flags")]
    assert flagged, "dissent must surface as review flags"
    assert any(
        "causal claim" in flag
        for row in flagged
        for flag in row["review_flags"]
    )


def test_content_authoring_carries_the_chapter_roster_in_teaching_order(
    golden_envelope, golden_rows,
):
    """Q69: every authoring request carries the chapter's topic roster in the
    sealed graph's own order, with this topic's position — evidence for the
    author and critic, composed once, identical across topics."""
    mapping = _replay_map(golden_envelope, golden_rows)
    topology, grounding, analysis, critic = _providers(mapping)
    requests: list[dict] = []

    def capturing(request: dict) -> dict:
        if request.get("stage") == "content_authoring":
            requests.append(copy.deepcopy(request))
        return analysis(request)

    settled = settle.settle(
        golden_envelope,
        topology_provider=topology,
        grounding_provider=grounding,
        analysis_provider=capturing,
        critic=critic,
        store=kernel.DecisionStore(),
    )
    assert len(settled) == 53
    assert requests
    graph_topics = [
        row for row in golden_envelope["graph"]["topics"] if isinstance(row, dict)
    ]
    skeleton = golden_envelope["skeleton_rows"]
    rosters = {json.dumps(r["chapter_topics_in_teaching_order"], sort_keys=True) for r in requests}
    assert len(rosters) == 1
    roster = requests[0]["chapter_topics_in_teaching_order"]
    assert [e["position"] for e in roster] == list(range(1, len(graph_topics) + 1))
    assert [e["topic_id"] for e in roster] == [t["topic_id"] for t in graph_topics]
    assert [e["title"] for e in roster] == [t["title"] for t in graph_topics]
    # Every topic lists its skeleton concepts (settle resolves each row's
    # topic before the roster is composed); culminations stay out.
    assert all(entry["concept_titles"] for entry in roster)
    assert sorted(t for e in roster for t in e["concept_titles"]) == sorted(
        _normal(row.get("concept_title"))
        for row in skeleton
        if not cr.is_culmination(str(row.get("concept_title") or ""))
    )
    for request in requests:
        position = request["this_topic_position"]
        assert roster[position - 1]["topic_id"] == request["topic"]["topic_id"]
        assert "chapter_topics_in_teaching_order" in request["rules"]
        assert "this_topic_position" in request["rules"]
        assert "Author each concept's learner-facing content in ONE pass" in request["rules"]

