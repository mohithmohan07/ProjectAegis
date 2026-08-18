import re

from scripts import run_concept_quality_sample as sample
from app.services import concept_refiner, generation


def test_laws_of_exponents_mock_quality_sample_structure():
    payload = sample.run(live=False, write=False)
    final = payload["final_rows"]
    topics = {r["topic"] for r in final}
    assert topics == {
        "Meaning of Exponents",
        "Laws of Exponents",
        "Negative and Zero Exponents",
    }
    assert not topics & {"Exercise 1.1", "Exercise 1.2", "Exercise 1.3", "Example", "Examples", "General"}
    assert all(r.get("parent_concept") for r in final)
    assert all(r["concept_details"].startswith("Description:") for r in final)
    assert not any(e["severity"] == "error" for e in payload["validator_report"]["errors"])
    assert payload["question_task_inventory"]["items"]
    assert payload["mined_types"]["types"]
    keys = set(payload["question_task_inventory"]["items"][0]["content_objects"])
    assert {"passages", "maps", "experiments", "code_snippets", "grammar_items", "conditions"} <= keys


def test_laws_of_exponents_skeleton_has_no_later_pass_content():
    payload = sample.run(live=False, write=False)
    skeleton = payload["raw_skeleton_rows"]
    assert skeleton
    assert not any("Types:" in r["concept_details"] for r in skeleton)
    assert not any(r["concept_title"].startswith("Culmination -") for r in skeleton)
    assert not any("group" in r["concept_details"].lower() for r in skeleton)
    assert not any("assessment label" in r["concept_details"].lower() for r in skeleton)


def test_laws_of_exponents_final_quality_conditions():
    payload = sample.run(live=False, write=False)
    final = payload["final_rows"]
    for topic in {r["topic"] for r in final}:
        topic_rows = [r for r in final if r["topic"] == topic]
        assert sum(concept_refiner.is_culmination(r["concept_title"]) for r in topic_rows) == 1
        assert concept_refiner.is_culmination(topic_rows[-1]["concept_title"])
        # The culmination Description is the authored consolidation — no
        # code-composed "Recap" stamp.
        culm_details = topic_rows[-1]["concept_details"]
        assert culm_details.startswith("Description: ")
        assert not culm_details.startswith("Description: Recap")
    normal = [r for r in final if not concept_refiner.is_culmination(r["concept_title"])]
    assert all(generation._has_meaningful_types(r["concept_details"]) for r in normal)
    artifact_re = re.compile(r"\b(MMD|Example\s+\d+|Fig(?:ure)?\s+\d+|Table\s+\d+|Exercise\s+\d+(?:\.\d+)?)\b", re.I)
    assert not any(artifact_re.search(r["concept_details"]) for r in final)
    assert any(
        r"[Katex] a \ne 0 [/Katex]" in r["concept_details"]
        for r in final
    )


def test_laws_of_exponents_type_numbering_is_continuous():
    payload = sample.run(live=False, write=False)
    regular_numbers = []
    for row in payload["final_rows"]:
        if concept_refiner.is_culmination(row["concept_title"]):
            continue
        regular_numbers += [
            int(n) for n in re.findall(r"(?<!Miscellaneous )Type\s+(\d{2}):", row["concept_details"])
        ]
    assert regular_numbers == list(range(1, len(regular_numbers) + 1))
