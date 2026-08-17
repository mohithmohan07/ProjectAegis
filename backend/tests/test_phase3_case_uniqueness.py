"""Q2 — Case/Example-granular uniqueness (docs/aegis-restructure.md §12 Q2).

Host's per-QID routing authority stands; Q2 conformance is restored at
assemble by splitting a fanned-out Case's identity per destination
(mechanics: minted ids, definitions copied verbatim, splits recorded in
the route audit field + coverage + review flags), and a deterministic
audit closes the fan-out, text-collapse, and shell holes. Violations are
release-audit errors — recorded, never silently repaired.
"""
from __future__ import annotations

from app.services import generation as g
from app.services.phase3 import assemble as assemble_mod
from app.services.phase3 import envelope as envelope_mod

PROMPT_A = (
    "Explain why the pinhole image appears inverted on the screen "
    "behind the hole."
)
PROMPT_B = (
    "Describe how a plane mirror image differs from the object in "
    "left-right orientation."
)


def _fanout_envelope() -> dict:
    graph = {
        "source_contract_hash": "contract-hash-q2",
        "metadata": {"subject": "Science", "board": "CBSE", "grade": "6"},
        "topics": [{"topic_id": "TOPIC-0001", "title": "Light"}],
        "subtopics": [],
        "blocks": [
            {
                "block_id": "BLK-0001",
                "topic_id": "TOPIC-0001",
                "kind": "paragraph",
            },
        ],
    }
    canonical = {
        "blocks": [{
            "block_id": "BLK-0001",
            "kind": "paragraph",
            "display_text": "Light travels in straight lines.",
        }],
    }
    skeleton_rows = [
        {"concept_title": "Rectilinear Propagation Of Light",
         "topic": "Light"},
        {"concept_title": "Reflection From Plane Mirrors", "topic": "Light"},
    ]
    inventory = {
        "items": [
            {
                "qid": "QINV-0101",
                "source_kind": "exercise",
                "raw_task": PROMPT_A,
            },
            {
                "qid": "QINV-0102",
                "source_kind": "exercise",
                "raw_task": PROMPT_B,
            },
        ],
    }
    mined_types = {
        "types": [{
            "type_id": "TYPE-0001",
            "type_title": "Interpret optical images",
            "type_description": (
                "Reason from ray paths to the properties of the image "
                "an optical arrangement forms."
            ),
            "owner_topic_id": "TOPIC-0001",
            "case_prompts": [{
                "case_id": "CASE-0001",
                "case_title": (
                    "Predict the image a simple optical setup forms"
                ),
                "examples": [
                    {
                        "source_question_id": "QINV-0101",
                        "example_prompt": PROMPT_A,
                    },
                    {
                        "source_question_id": "QINV-0102",
                        "example_prompt": PROMPT_B,
                    },
                ],
            }],
        }],
    }
    return envelope_mod.build(
        graph=graph,
        canonical=canonical,
        skeleton_rows=skeleton_rows,
        inventory=inventory,
        mined_types=mined_types,
        metadata={"subject": "Science", "board": "CBSE", "grade": "6"},
    )


def _settled_rows() -> list[dict]:
    return [
        {
            "topic": "Light",
            "parent_concept": "Light",
            "concept_title": "Rectilinear Propagation Of Light",
            "_semantic_topic_id": "TOPIC-0001",
            "_source_block_ids": ["BLK-0001"],
            "_source_grounding_contract": "api-verified-source-block-ids",
            "keywords": "light, rectilinear, shadow",
            "concept_details": (
                "Description: Light travels in straight lines through a "
                "uniform medium, which is why opaque objects cast sharp "
                "shadows and a pinhole camera forms a small inverted "
                "image on its screen. Because the path is straight, "
                "predicting where light reaches only requires drawing "
                "straight rays from the source past the object's edges.\n"
                "Achieving Mastery: The learner can draw straight-line "
                "ray diagrams to predict shadows and pinhole images."
            ),
        },
        {
            "topic": "Light",
            "parent_concept": "Light",
            "concept_title": "Reflection From Plane Mirrors",
            "_semantic_topic_id": "TOPIC-0001",
            "_source_block_ids": ["BLK-0001"],
            "_source_grounding_contract": "api-verified-source-block-ids",
            "keywords": "reflection, mirror, image",
            "concept_details": (
                "Description: A plane mirror reflects light so that the "
                "image appears erect, virtual, and as far behind the "
                "mirror as the object stands in front of it. The mirror "
                "reverses left and right, which is why printed text seen "
                "in a mirror reads backwards while its size is "
                "unchanged.\n"
                "Achieving Mastery: The learner can state the properties "
                "of a plane-mirror image and locate it by distance."
            ),
        },
    ]


def _fanout_hosts() -> dict:
    """One mined Case whose two Examples route to two concepts (per-QID
    authority — the reviewers' verdict stands)."""
    first = {
        "topic_id": "TOPIC-0001",
        "topic": "Light",
        "concept_title": "Rectilinear Propagation Of Light",
        "parent_concept": "Light",
        "decision": "existing",
        "confidence": 0.99,
    }
    second = {
        "topic_id": "TOPIC-0001",
        "topic": "Light",
        "concept_title": "Reflection From Plane Mirrors",
        "parent_concept": "Light",
        "decision": "existing",
        "confidence": 0.99,
    }
    return {
        "host_map": {"TYPE-0001::CASE-0001::0001": dict(first)},
        "qid_map": {
            "QINV-0101": dict(first),
            "QINV-0102": dict(second),
        },
        "new_concepts": [],
    }


def test_a_fanned_out_case_is_split_into_distinct_rendered_cases():
    env = _fanout_envelope()
    result = assemble_mod.assemble(
        env, _settled_rows(), _fanout_hosts()
    )
    rows = result["rows"]
    # The deposit-parity pipeline Title-Cases titles ("From" -> "from").
    by_title = {row["concept_title"].casefold(): row for row in rows}
    first = by_title["rectilinear propagation of light"]
    second = by_title["reflection from plane mirrors"]

    # Each rendered Case lands on exactly one concept: the first
    # destination keeps the original identity, the second renders a
    # freshly minted one, definitions copied verbatim.
    assert "TYPE-0001::CASE-0001::0001" in (
        first["_aegis_release_type_case_routes"]
    )
    assert second["_aegis_release_type_case_routes"] == [
        "TYPE-0001::CASE-0002::split-of:CASE-0001"
    ]
    assert "Predict the image a simple optical setup forms" in (
        first["concept_details"]
    )
    assert "Predict the image a simple optical setup forms" in (
        second["concept_details"]
    )
    # Each Example renders exactly once, on its own question's row.
    assert PROMPT_A in first["concept_details"]
    assert PROMPT_A not in second["concept_details"]
    assert PROMPT_B in second["concept_details"]
    assert PROMPT_B not in first["concept_details"]

    # The split is recorded: coverage entry + review flags on BOTH rows.
    assert result["coverage"]["case_splits"] == [{
        "type_id": "TYPE-0001",
        "case_id": "CASE-0001",
        "destinations": [
            {
                "case_id": "CASE-0001",
                "topic_id": "TOPIC-0001",
                "concept_title": "Rectilinear Propagation Of Light",
                "qids": ["QINV-0101"],
            },
            {
                "case_id": "CASE-0002",
                "topic_id": "TOPIC-0001",
                "concept_title": "Reflection From Plane Mirrors",
                "qids": ["QINV-0102"],
            },
        ],
    }]
    for row in (first, second):
        assert any(
            flag.startswith("Q2 case split: TYPE-0001::CASE-0001")
            for flag in row.get("review_flags") or []
        ), row["concept_title"]
    # The deterministic audit is clean after the split.
    assert result["coverage"]["case_audit"] == []


def test_split_assembly_is_deterministic():
    env = _fanout_envelope()
    first = assemble_mod.assemble(env, _settled_rows(), _fanout_hosts())
    second = assemble_mod.assemble(env, _settled_rows(), _fanout_hosts())
    assert first == second


# ---------------------------------------------------------------------------
# the deterministic audit


def test_audit_catches_a_duplicated_rendered_case():
    records = [
        {
            "concept_title": "Row One",
            "_aegis_release_type_case_routes": [
                "TYPE-0001::CASE-0001::0001"
            ],
        },
        {
            "concept_title": "Row Two",
            "_aegis_release_type_case_routes": [
                "TYPE-0001::CASE-0001::0001"
            ],
        },
    ]
    findings = assemble_mod.audit_case_uniqueness(records)
    assert [f["code"] for f in findings] == ["duplicate_case_identity"]
    assert findings[0]["row_indexes"] == [0, 1]
    assert "TYPE-0001::CASE-0001" in findings[0]["message"]


def test_audit_catches_a_duplicated_qid_route():
    records = [
        {"concept_title": "Row One", "_aegis_release_qids": ["QINV-0001"]},
        {"concept_title": "Row Two", "_aegis_release_qids": ["QINV-0001"]},
    ]
    findings = assemble_mod.audit_case_uniqueness(records)
    assert [f["code"] for f in findings] == ["duplicate_qid_route"]
    assert findings[0]["qids"] == ["QINV-0001"]


def test_audit_keys_rendered_coverage_on_qid_not_wording():
    """Two DIFFERENT questions with identical wording must render twice;
    the normalized-text collapse (one render 'covering' both) is exactly
    the silent hole the QID-keyed audit closes."""
    shared = (
        "Explain why the image forms upside down in a pinhole camera."
    )
    records = [{
        "concept_title": "Row One",
        "concept_details": (
            "Description: d // Types: Type 01: T Case 01: C "
            f"Example: {shared}"
        ),
        "_aegis_release_qids": ["QINV-0001", "QINV-0002"],
    }]
    findings = assemble_mod.audit_case_uniqueness(
        records,
        expected_examples=[
            {"qid": "QINV-0001", "prompt": shared},
            {"qid": "QINV-0002", "prompt": shared},
        ],
    )
    assert [f["code"] for f in findings] == ["qid_render_count_mismatch"]
    assert findings[0]["qids"] == ["QINV-0001", "QINV-0002"]
    # Rendering it twice satisfies the QID-keyed contract.
    records[0]["concept_details"] += f" Example: {shared}"
    assert assemble_mod.audit_case_uniqueness(
        records,
        expected_examples=[
            {"qid": "QINV-0001", "prompt": shared},
            {"qid": "QINV-0002", "prompt": shared},
        ],
    ) == []


def test_audit_catches_an_example_less_case_shell():
    prompt = (
        "Explain why the pinhole image appears inverted on the screen."
    )
    cases = {
        ("TYPE-0001", "CASE-0001"): {
            "title": "Predict the pinhole image",
            "examples": [{"qid": "QINV-0001", "prompt": prompt}],
        },
        ("TYPE-0001", "CASE-0002"): {
            # A catalog Case with no examples is the taxonomy's own
            # legitimate exampleless mark — never a shell.
            "title": "Taxonomy-visibility case",
            "examples": [],
        },
    }
    records = [
        {
            "concept_title": "Shell Carrier",
            "concept_details": (
                "Description: d // Types: Type 01: T "
                "Case 01: Predict the pinhole image "
                "Case 02: Taxonomy-visibility case"
            ),
        },
        {
            "concept_title": "Example Carrier",
            "concept_details": (
                "Description: d // Types: Type 01: T "
                f"Case 01: Predict the pinhole image Example: {prompt}"
            ),
            "_aegis_release_qids": ["QINV-0001"],
        },
    ]
    findings = assemble_mod.audit_case_uniqueness(records, cases=cases)
    assert [f["code"] for f in findings] == ["example_less_case_shell"]
    assert findings[0]["row_indexes"] == [0]
    assert "Example Carrier" in findings[0]["message"]
    # Only the shell whose catalog Case HAS examples is a violation:
    # the taxonomy-visibility case raised nothing.
    assert "Taxonomy-visibility" not in findings[0]["message"]


# ---------------------------------------------------------------------------
# the dedupe shell removal is flagged, never silent (R4)


def test_dedupe_flags_the_shell_it_removes_and_names_the_carrier():
    prompt = (
        "Explain why the pinhole image appears inverted on the screen."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "raw_task": prompt,
    }]}
    records = [
        {
            "concept_title": "First Carrier",
            "concept_details": (
                "Description: d // Types: Type 01: T "
                f"Case 01: Read the diagram Example: {prompt}"
            ),
        },
        {
            "concept_title": "Duplicate Carrier",
            "concept_details": (
                "Description: d // Types: Type 01: T "
                f"Case 01: Read the diagram again Example: {prompt}"
            ),
        },
    ]
    out, removed = g._dedupe_rendered_inventory_examples(
        records, inventory
    )
    assert removed == 1
    # The duplicate render went; the emptied Case shell went WITH a flag
    # naming where the Example lives.
    assert prompt not in out[1]["concept_details"]
    assert "Read the diagram again" not in out[1]["concept_details"]
    flags = out[1].get("review_flags") or []
    assert any(
        flag.startswith("Q2: removed the example-less Case shell")
        and "'First Carrier'" in flag
        for flag in flags
    )
    # The surviving carrier keeps its Case intact, unflagged.
    assert prompt in out[0]["concept_details"]
    assert not out[0].get("review_flags")


def test_release_audit_converts_findings_into_issues():
    from app.services import build_concepts_release as release

    records = [
        {"concept_title": "Row One", "_aegis_release_qids": ["QINV-0001"]},
        {"concept_title": "Row Two", "_aegis_release_qids": ["QINV-0001"]},
    ]
    issues = release._case_uniqueness_issues(records, {"types": []})
    assert [issue["code"] for issue in issues] == [
        "case_uniqueness_duplicate_qid_route"
    ]
    assert issues[0]["severity"] == "error"
    assert issues[0]["qids"] == ["QINV-0001"]


def test_a_clean_release_has_no_case_uniqueness_issues():
    from app.services import build_concepts_release as release

    records = [{
        "concept_title": "Row One",
        "concept_details": "Description: d",
        "_aegis_release_qids": ["QINV-0001"],
    }]
    assert release._case_uniqueness_issues(records, {"types": []}) == []
