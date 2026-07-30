"""Regression coverage for Phase 3.7 visual topology convergence."""
from __future__ import annotations

from pathlib import Path

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33
from app.services import canonical_source_phase37_visual_topology_convergence_contract as phase37


DATA = Path(__file__).parents[1] / "data" / "Testing"


def _visual_source() -> tuple[dict, dict]:
    graph = {
        "source_contract_hash": "source-hash",
        "metadata": {"subject": "History"},
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "order": 1,
                "title": "A New Conservatism after 1815",
                "structural_number": "2.3",
            }
        ],
        "subtopics": [],
        "blocks": [
            {
                "block_id": "BLK-00001",
                "order": 1,
                "kind": "paragraph",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "",
                "figure_id": "",
            },
            {
                "block_id": "BLK-00002",
                "order": 2,
                "kind": "figure",
                "topic_id": "TOPIC-0001",
                "subtopic_id": "",
                "figure_id": "FIG-00001",
            },
        ],
    }
    canonical = {
        "blocks": [
            {
                "block_id": "BLK-00001",
                "kind": "paragraph",
                "display_text": (
                    "Conservative regimes imposed censorship laws to control "
                    "newspapers, books, plays and songs."
                ),
            },
            {
                "block_id": "BLK-00002",
                "kind": "figure",
                "figure_id": "FIG-00001",
                "raw_text": (
                    "\\begin{figure}\n"
                    "\\includegraphics{https://example.test/club.jpg}\n"
                    "\\caption{Fig. 6 - The Club of Thinkers. The plaque asks "
                    "how long thinking will be allowed; members receive muzzles "
                    "to prevent speech.}\n"
                    "\\end{figure}"
                ),
            },
        ],
        "figures": [
            {
                "figure_id": "FIG-00001",
                "caption_raw": (
                    "Fig. 6 - The Club of Thinkers, an anonymous caricature. "
                    "The plaque asks how long thinking will be allowed and the "
                    "rules distribute muzzles to prevent speech."
                ),
                "reference_ids": ["6"],
                "image_ids": ["IMG-00001"],
            }
        ],
        "images": [
            {
                "image_id": "IMG-00001",
                "alt_raw": "Political caricature of a silenced thinkers' club",
            }
        ],
    }
    return graph, canonical


def _concept_rows() -> tuple[list[dict], list[dict], dict[str, int], dict]:
    records = [
        {
            "topic": "A New Conservatism after 1815",
            "_semantic_topic_id": "TOPIC-0001",
            "parent_concept": "Conservative repression",
            "concept_title": "Censorship under conservative regimes",
            "concept_details": (
                "Description: Conservative governments restricted criticism "
                "through censorship.\nAchieving Mastery: The learner explains "
                "how censorship suppressed dissent."
            ),
            "keywords": "censorship",
        },
        {
            "topic": "A New Conservatism after 1815",
            "_semantic_topic_id": "TOPIC-0001",
            "parent_concept": "Conservative repression",
            "concept_title": "Visual satire of censorship",
            "concept_details": (
                "Description: A political caricature represents the suppression "
                "of speech.\nAchieving Mastery: The learner interprets visual "
                "symbols of censorship."
            ),
            "keywords": "caricature",
        },
    ]
    concepts = [
        {
            "concept_id": "TOPOLOGY-CONCEPT-0001",
            "source_order": 1,
            "current_topic_id": "TOPIC-0001",
            "current_topic_title": "A New Conservatism after 1815",
            "concept_title": records[0]["concept_title"],
            "parent_concept": records[0]["parent_concept"],
            "source_claim": (
                "Conservative governments restricted criticism through censorship."
            ),
            "existing_mastery": "The learner explains how censorship suppressed dissent.",
        },
        {
            "concept_id": "TOPOLOGY-CONCEPT-0002",
            "source_order": 2,
            "current_topic_id": "TOPIC-0001",
            "current_topic_title": "A New Conservatism after 1815",
            "concept_title": records[1]["concept_title"],
            "parent_concept": records[1]["parent_concept"],
            "source_claim": (
                "A political caricature represents the suppression of speech."
            ),
            "existing_mastery": "The learner interprets visual symbols of censorship.",
        },
    ]
    index_by_id = {
        "TOPOLOGY-CONCEPT-0001": 0,
        "TOPOLOGY-CONCEPT-0002": 1,
    }
    graph = {
        "source_contract_hash": "source-hash",
        "topics": [
            {
                "topic_id": "TOPIC-0001",
                "order": 1,
                "title": "A New Conservatism after 1815",
            }
        ],
    }
    return records, concepts, index_by_id, graph


def test_visual_caption_survives_every_semantic_evidence_lane():
    graph, canonical = _visual_source()

    topics, _order = phase32._topic_evidence(graph, canonical)
    topic_text = topics[0]["evidence"]
    assert "The Club of Thinkers" in topic_text
    assert "muzzles" in topic_text
    assert "FIG-00001" in topic_text

    _usable, candidates = phase31._candidate_blocks(
        graph,
        canonical,
        "TOPIC-0001",
    )
    figure_candidate = next(
        row for row in candidates if row["block_id"] == "BLK-00002"
    )
    assert "The Club of Thinkers" in figure_candidate["text"]
    assert "muzzles" in figure_candidate["text"]

    blocks_by_topic, _subtopics = phase33._topic_source_blocks(
        graph,
        canonical,
    )
    host_figure = next(
        row
        for row in blocks_by_topic["TOPIC-0001"]
        if row["block_id"] == "BLK-00002"
    )
    assert "The Club of Thinkers" in host_figure["text"]

    excerpts = phase3.graph_topic_excerpts(graph, canonical)
    assert "The Club of Thinkers" in excerpts[0]["excerpt"]


def test_checked_in_rne_caricature_caption_reaches_topology_evidence():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    compiled = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )
    graph, _report = phase3.compile_semantic_graph(
        compiled.canonical,
        source_text=source,
        metadata={
            "chapter_title": "The Rise of Nationalism in Europe",
            "subject": "History",
            "board": "CBSE",
            "grade": "10",
            "unit": "Modern World History",
        },
    )

    topics, _order = phase32._topic_evidence(
        graph,
        compiled.canonical,
    )
    all_evidence = "\n".join(row["evidence"] for row in topics)
    assert "The Club of Thinkers" in all_evidence
    assert "How long will thinking be allowed to us?" in all_evidence
    assert "muzzles will be distributed" in all_evidence


def test_caricature_query_selects_the_verified_figure_page():
    payload = {
        "concepts": [
            {
                "concept_title": "Visual satire of censorship",
                "source_claim": (
                    "The Club of Thinkers caricature uses a plaque and muzzles "
                    "to depict suppression of speech."
                ),
                "current_topic_title": "A New Conservatism after 1815",
            }
        ]
    }
    bundle = {
        "pages": [
            {
                "page_number": 4,
                "blocks": [
                    {
                        "kind": "figure",
                        "caption": "Europe after the Congress of Vienna",
                    }
                ],
            },
            {
                "page_number": 9,
                "blocks": [
                    {
                        "kind": "figure",
                        "caption": (
                            "The Club of Thinkers caricature. The plaque asks "
                            "how long thinking will be allowed and members wear "
                            "muzzles to prevent speech."
                        ),
                    }
                ],
            },
        ]
    }

    selected = phase37._relevant_visual_page_numbers(payload, bundle)

    assert selected[0] == 9


def test_schema_exposes_retire_only_with_an_explicit_target_field():
    directory = {
        "TOPOLOGY-CONCEPT-0001": {
            "concept_id": "TOPOLOGY-CONCEPT-0001",
        },
        "TOPOLOGY-CONCEPT-0002": {
            "concept_id": "TOPOLOGY-CONCEPT-0002",
        },
    }
    token = phase37._ACTIVE_CONCEPT_DIRECTORY.set(directory)
    try:
        schema = phase32._adjudication_schema(
            list(directory),
            ["TOPIC-0001"],
        )
    finally:
        phase37._ACTIVE_CONCEPT_DIRECTORY.reset(token)

    item = schema["schema"]["properties"]["concepts"]["items"]
    assert "retire" in item["properties"]["decision"]["enum"]
    assert "retire_into_concept_id" in item["required"]
    assert set(item["properties"]["retire_into_concept_id"]["enum"]) == {
        "NONE",
        *directory,
    }


def test_independently_reviewable_retire_removes_only_the_duplicate_row():
    records, concepts, index_by_id, graph = _concept_rows()
    directory = {row["concept_id"]: row for row in concepts}
    response = {
        "concepts": [
            {
                "concept_id": "TOPOLOGY-CONCEPT-0001",
                "decision": "keep",
                "segments": [
                    {
                        "topic_id": "TOPIC-0001",
                        "concept_title": concepts[0]["concept_title"],
                        "parent_concept": concepts[0]["parent_concept"],
                        "description": concepts[0]["source_claim"],
                        "achieving_mastery": concepts[0]["existing_mastery"],
                        "keywords": ["censorship"],
                        "confidence": 0.99,
                        "reason": "Fully supported.",
                    }
                ],
                "retire_into_concept_id": "NONE",
                "confidence": 0.99,
                "reason": "Fully supported.",
            },
            {
                "concept_id": "TOPOLOGY-CONCEPT-0002",
                "decision": "retire",
                "segments": [],
                "retire_into_concept_id": "TOPOLOGY-CONCEPT-0001",
                "confidence": 0.99,
                "reason": (
                    "Every supported claim is already taught by the censorship "
                    "concept and no separate visual objective remains."
                ),
            },
        ]
    }

    token = phase37._ACTIVE_CONCEPT_DIRECTORY.set(directory)
    try:
        parsed, errors = phase32._parse_decisions(
            response,
            concepts=directory,
            topic_ids={"TOPIC-0001"},
        )
        output = phase32._apply_decisions(
            records,
            concepts=concepts,
            index_by_id=index_by_id,
            decisions=parsed,
            graph=graph,
        )
    finally:
        phase37._ACTIVE_CONCEPT_DIRECTORY.reset(token)

    assert errors == []
    assert parsed["TOPOLOGY-CONCEPT-0002"]["decision"] == "retire"
    normal_titles = [
        row["concept_title"]
        for row in output
        if not row["concept_title"].startswith("Culmination -")
    ]
    assert normal_titles == ["Censorship under conservative regimes"]


def test_retirement_cannot_target_another_retiring_concept():
    _records, concepts, _index_by_id, _graph = _concept_rows()
    directory = {row["concept_id"]: row for row in concepts}
    response = {
        "concepts": [
            {
                "concept_id": "TOPOLOGY-CONCEPT-0001",
                "decision": "retire",
                "segments": [],
                "retire_into_concept_id": "TOPOLOGY-CONCEPT-0002",
                "confidence": 0.99,
                "reason": "Duplicate.",
            },
            {
                "concept_id": "TOPOLOGY-CONCEPT-0002",
                "decision": "retire",
                "segments": [],
                "retire_into_concept_id": "TOPOLOGY-CONCEPT-0001",
                "confidence": 0.99,
                "reason": "Duplicate.",
            },
        ]
    }

    token = phase37._ACTIVE_CONCEPT_DIRECTORY.set(directory)
    try:
        _parsed, errors = phase32._parse_decisions(
            response,
            concepts=directory,
            topic_ids={"TOPIC-0001"},
        )
    finally:
        phase37._ACTIVE_CONCEPT_DIRECTORY.reset(token)

    assert any("another retiring concept" in error for error in errors)
