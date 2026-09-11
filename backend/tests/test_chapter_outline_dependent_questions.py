"""Job 129: retain dependent demands while compiling independent source asks.

The source outline API decides semantics. These offline tests exercise its
complete author/critic evidence, exact identity coverage and downstream
compilation; they do not substitute a local text classifier for the API.
"""
from __future__ import annotations

import copy
import json

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase21_structure as structure
from app.services import canonical_source_phase221_fallback as fallback


DNA = "What is DNA fingerprinting? What are its uses?"
BEES = "How do honeybees identify their own honeycombs?"
RAIN = "Why does rain fall in drops?"
DIRECTION = (
    "Can you answer these questions? You will find the authors' answers "
    "on the following page."
)
REFLECTION = (
    "How can one become a scientist, an economist, a historian? "
    "Does it simply involve reading many books on the subject? "
    "Does it involve observing, thinking and doing experiments?"
)


def _source():
    return {
        "pdf_sha256": "job129-dependent-followups",
        "pages": [{
            "page_id": "PDF-PAGE-0001", "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "The Making of a Scientist"},
                {"reading_order": 2, "kind": "task", "source_label": "Talk about it",
                 "text": f"(i) {DNA} (ii) {BEES} (iii) {RAIN} {DIRECTION}"},
                {"reading_order": 3, "kind": "task", "source_label": "Think about it",
                 "text": REFLECTION},
            ],
        }],
    }


def _decision():
    return {
        "chapter_title": "The Making of a Scientist",
        "topics": [{
            "title": "The Making of a Scientist", "kind": "content",
            "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1,
        }],
        "task_partitions": [{
            "page_id": "PDF-PAGE-0001", "reading_order": 2,
            "independent_parts": [
                {"label": "(i)", "stem": "", "text": DNA},
                {"label": "(ii)", "stem": "", "text": BEES},
                {"label": "(iii)", "stem": "", "text": RAIN},
            ],
        }],
        "whole_tasks": [{
            "page_id": "PDF-PAGE-0001", "reading_order": 3,
            "task_kind": "question",
        }],
        "task_dependency_links": [],
        "notes": ["The final direction refers to the listed questions; it is not another task."],
    }


def _provider(monkeypatch, tmp_path, *, decision=None, review=None):
    calls = []

    def respond(**kwargs):
        calls.append(kwargs)
        if kwargs["response_schema"]["name"] == "aegis_chapter_outline_review":
            return copy.deepcopy(review or {"verdict": "verified", "issues": []})
        return copy.deepcopy(decision or _decision())

    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", respond)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    return calls


def test_complete_dependent_question_and_independent_siblings_reach_inventory(
    monkeypatch, tmp_path,
):
    calls = _provider(monkeypatch, tmp_path)
    source = _source()
    original = copy.deepcopy(source)
    outline = fallback.derive_chapter_outline(source)
    assert len(calls) == 2
    assert source == original
    assert DNA in calls[0]["prompt"] and DIRECTION in calls[0]["prompt"]
    author_rules = " ".join(calls[0]["system"].split())
    assert "definition followed by its uses" in author_rules
    assert "does not automatically add another assessment question" in author_rules

    critic = json.loads(calls[1]["prompt"])
    assert critic["source_pages"] == original["pages"]
    assert critic["task_coverage"] == {
        "source_task_refs": [["PDF-PAGE-0001", 2], ["PDF-PAGE-0001", 3]],
        "whole_task_refs": [["PDF-PAGE-0001", 3]],
        "partitioned_task_refs": [["PDF-PAGE-0001", 2]],
        "unruled_task_refs": [],
    }
    assert "ruled_task_kinds" in calls[1]["system"]
    assert "not merely the absence" in " ".join(calls[1]["system"].split())

    source["chapter_outline"] = outline
    rendered = fallback.render_page_acsd_to_mmd(source)
    canonical = phase2.compile_phase2_source(
        rendered, source_filename="source.mmd", consumer_module="build_concepts",
    ).canonical
    fallback._attach_chapter_outline(canonical, source)
    assert fallback.apply_page_acsd_relationships(canonical, source) == 2
    structure.materialize_task_leaf_cases(canonical)
    inventory = phase2.inventory_from_canonical(canonical)

    prompts = [item["raw_task"] for item in inventory["items"]]
    assert sorted(prompts) == sorted([DNA, BEES, RAIN, REFLECTION])
    assert [prompt for prompt in prompts if prompt != REFLECTION] == [DNA, BEES, RAIN]
    # The directive remains complete immutable source evidence without being
    # promoted to a fifth assessment. The dependent uses demand stays present.
    assert DIRECTION in rendered
    assert DIRECTION in source["pages"][0]["blocks"][1]["text"]
    assert len({item["qid"] for item in inventory["items"]}) == 4
    assert fallback.derive_chapter_outline(original) == outline
    assert len(calls) == 2  # Reviewed fresh decisions still replay from cache.


def test_bad_semantic_split_is_visible_to_independent_critic_without_local_rewrite(
    monkeypatch, tmp_path,
):
    candidate = _decision()
    candidate["task_partitions"][0]["independent_parts"] = [
        {"label": "(i)", "stem": "", "text": "What is DNA fingerprinting?"},
        {"label": "", "stem": "", "text": "What are its uses?"},
        {"label": "(ii)", "stem": "", "text": BEES},
        {"label": "(iii)", "stem": "", "text": RAIN},
        {"label": "", "stem": "", "text": DIRECTION},
    ]
    review = {"verdict": "dissent", "issues": [{
        "code": "DEPENDENT_FOLLOWUP_SPLIT", "field": "task_partitions",
        "message": "The uses fragment loses its antecedent and the final direction adds no distinct task.",
        "evidence_refs": [{"page_id": "PDF-PAGE-0001", "reading_order": 2}],
    }]}
    calls = _provider(monkeypatch, tmp_path, decision=candidate, review=review)
    outline = fallback.derive_chapter_outline(_source())

    assert len(calls) == 2
    assert json.loads(calls[1]["prompt"])["outline_to_apply"]["task_partitions"] == candidate["task_partitions"]
    assert outline["task_partitions"] == candidate["task_partitions"]
    assert outline["review_provenance"]["review"] == review
    assert any("DEPENDENT_FOLLOWUP_SPLIT" in flag for flag in outline["review_flags"])


def test_new_outline_decision_does_not_change_sealed_bundle_or_source_reader_keys(
    monkeypatch,
):
    source_hash = _source()["pdf_sha256"]
    current_decision = fallback.OUTLINE_DECISION_VERSION
    current_outline_key = fallback._outline_cache_key(source_hash)
    bundle_key = fallback._bundle_cache_key(source_hash)
    reader = fallback.source_reader_version()

    monkeypatch.setattr(fallback, "OUTLINE_DECISION_VERSION", "chapter-outline-decision-11")

    assert current_decision == "chapter-outline-decision-12"
    assert fallback._outline_cache_key(source_hash) != current_outline_key
    assert fallback._bundle_cache_key(source_hash) == bundle_key
    assert fallback.source_reader_version() == reader
