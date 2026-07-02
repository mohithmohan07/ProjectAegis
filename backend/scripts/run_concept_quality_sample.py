from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, models
from app.db import SessionLocal, init_db
from app.bulk_import import writer
from app.services import build_concepts, concept_cleanup, concept_refiner, concept_validator, directory, generation


SAMPLE_MMD = """# Laws of Exponents

## 1.1 Meaning of Exponents

An exponent tells how many times a number is used as a factor. For example, in 2^4, the base is 2 and the exponent is 4. It means 2 × 2 × 2 × 2.

A power has two parts: the base and the exponent. The base is the repeated factor. The exponent shows the number of repetitions.

Example 1: Write 5 × 5 × 5 using exponent notation.
Example 2: Identify the base and exponent in 7^3.

Exercise 1.1:
1. Write 3 × 3 × 3 × 3 in exponential form.
2. Identify the base and exponent in 9^5.
3. Expand 4^3.

## 1.2 Laws of Exponents

When multiplying powers with the same base, add the exponents:
a^m × a^n = a^(m+n)

When dividing powers with the same base, subtract the exponents:
a^m ÷ a^n = a^(m-n), where a ≠ 0 and m > n.

When a power is raised to another power, multiply the exponents:
(a^m)^n = a^(mn)

Example 3: Simplify 2^3 × 2^5.
Example 4: Simplify 7^6 ÷ 7^2.
Example 5: Simplify (3^2)^4.

Exercise 1.2:
1. Simplify x^4 × x^7.
2. Simplify p^9 ÷ p^3.
3. Simplify (a^5)^2.
4. A bacteria culture doubles every hour. Express the number of bacteria after 6 hours if the initial count is 100.

## 1.3 Negative and Zero Exponents

Any non-zero number raised to the power zero is 1:
a^0 = 1, where a ≠ 0.

A negative exponent represents reciprocal form:
a^(-m) = 1/a^m, where a ≠ 0.

Example 6: Simplify 5^0.
Example 7: Express 2^-3 as a fraction.

Exercise 1.3:
1. Simplify 8^0.
2. Write 3^-2 in reciprocal form.
3. Compare 2^-3 and 3^-2.
"""

METADATA = {
    "board": "CBSE",
    "grade": "08",
    "subject": "Mathematics",
    "unit": "Number System",
    "chapter_title": "Laws of Exponents",
    "learning_kind": "Post",
}

CONTENT_OBJECT_KEYS = [
    "numbers", "variables", "equations", "coordinates", "ratios", "diagrams",
    "graphs", "tables", "maps", "passages", "sources", "experiments",
    "observations", "characters", "events", "dates", "places", "terms",
    "definitions", "processes", "comparisons", "causes", "effects",
    "code_snippets", "grammar_items", "unknowns", "given_values", "conditions",
]


def _content_objects(**values: list[str]) -> dict:
    return {key: list(values.get(key, [])) for key in CONTENT_OBJECT_KEYS}


def _row(topic: str, parent: str, concept: str, details: str, keywords: str) -> dict:
    return {
        "topic": topic,
        "parent_concept": parent,
        "concept_title": concept,
        "concept_details": details,
        "keywords": keywords,
    }


def _mock_stages() -> dict:
    inventory = {
        "items": [
            {
                "qid": "QINV-0001",
                "source_kind": "exercise",
                "source_label": "Exercise 1.1 Q1",
                "parent_source_label": "Exercise 1.1",
                "topic_hint": "Meaning of Exponents",
                "page_hint": "",
                "block_ids": [],
                "raw_task": "Write 3 × 3 × 3 × 3 in exponential form.",
                "raw_solution_or_answer": "",
                "normalized_task": "Convert repeated multiplication to exponential form.",
                "shared_context": "",
                "subpart_label": "",
                "content_objects": _content_objects(numbers=["3"], terms=["exponential form"]),
                "requires_visual": False,
                "requires_context": False,
                "order_index": 1,
            },
            {
                "qid": "QINV-0002",
                "source_kind": "exercise",
                "source_label": "Exercise 1.2 Q2",
                "parent_source_label": "Exercise 1.2",
                "topic_hint": "Laws of Exponents",
                "page_hint": "",
                "block_ids": [],
                "raw_task": "Simplify p^9 ÷ p^3.",
                "raw_solution_or_answer": "",
                "normalized_task": "Divide powers with the same base.",
                "shared_context": "",
                "subpart_label": "",
                "content_objects": _content_objects(
                    variables=["p"], equations=["p^9 ÷ p^3"], conditions=["a ≠ 0", "m > n"]),
                "requires_visual": False,
                "requires_context": False,
                "order_index": 2,
            },
        ],
        "stats": {
            "worked_examples": 0,
            "solved_examples": 0,
            "exercise_questions": 2,
            "objective_items": 0,
            "subjective_items": 2,
            "descriptive_items": 0,
            "subparts": 0,
            "visual_tasks": 0,
            "table_or_graph_tasks": 0,
            "source_or_passage_tasks": 0,
            "total_inventory_items": 2,
        },
    }
    mined_types = {
        "types": [
            {
                "type_id": "TYPE-0001",
                "type_title": "Converting Repeated Multiplication to Exponential Form",
                "type_description": "Reusable exponent notation task pattern.",
                "task_pattern": "Rewrite repeated factors as a power.",
                "source_question_ids": ["QINV-0001"],
                "case_prompts": [{"case_id": "CASE-0001", "source_question_id": "QINV-0001", "case_prompt": "Write 3 × 3 × 3 × 3 in exponential form.", "case_signature": "repeated multiplication -> power"}],
                "concept_match_hint": "Writing Repeated Multiplication as Powers",
                "parent_concept_match_hint": "Exponent Notation",
                "topic_match_hint": "Meaning of Exponents",
                "difficulty_hint": "Basic",
                "cognitive_skill_hint": "Apply",
                "subject_skill_hint": "Mathematical Calculation",
            },
            {
                "type_id": "TYPE-0002",
                "type_title": "Dividing Powers with the Same Base",
                "type_description": "Reusable quotient law task pattern.",
                "task_pattern": "Subtract exponents when dividing powers with the same non-zero base.",
                "source_question_ids": ["QINV-0002"],
                "case_prompts": [{"case_id": "CASE-0002", "source_question_id": "QINV-0002", "case_prompt": "Simplify p^9 ÷ p^3.", "case_signature": "same-base division"}],
                "concept_match_hint": "Dividing Powers with the Same Base",
                "parent_concept_match_hint": "Operations on Powers with the Same Base",
                "topic_match_hint": "Laws of Exponents",
                "difficulty_hint": "Basic",
                "cognitive_skill_hint": "Apply",
                "subject_skill_hint": "Algebraic Reasoning",
            },
        ]
    }
    raw = [
        _row("Meaning of Exponents", "Exponent Notation", "Writing Repeated Multiplication as Powers", "Description: Repeated factors can be written as a base raised to an exponent.", "exponents, repeated multiplication, powers"),
        _row("Meaning of Exponents", "Parts of a Power", "Identifying Base and Exponent", "Description: A power has a base and an exponent.", "base, exponent, power"),
        _row("Meaning of Exponents", "Exponent Expansion", "Expanding Powers into Repeated Multiplication", "Description: A power can be expanded into repeated multiplication.", "expand, powers, factors"),
        _row("Laws of Exponents", "Operations on Powers with the Same Base", "Multiplying Powers with the Same Base", "Description: Products of powers with the same base use exponent addition.", "same base, multiplication, index law"),
        _row("Laws of Exponents", "Operations on Powers with the Same Base", "Dividing Powers with the Same Base", "Description: Quotients of powers with the same base use exponent subtraction.", "same base, division, quotient"),
        _row("Laws of Exponents", "Powers of Powers", "Simplifying a Power Raised to a Power", "Description: A power raised to another power uses exponent multiplication.", "power of power, multiplication"),
        _row("Laws of Exponents", "Operations on Powers with the Same Base", "Multiplying Powers with the Same Base", "Description: Duplicate row for canonicalization.", "duplicate"),
        _row("Negative and Zero Exponents", "Zero Exponent Rule", "Simplifying Zero Exponents", "Description: Non-zero bases raised to zero equal one.", "zero exponent, non-zero base"),
        _row("Negative and Zero Exponents", "Negative Exponent Rule", "Converting Negative Exponents to Reciprocal Form", "Description: Negative exponents represent reciprocals.", "negative exponent, reciprocal"),
        _row("Negative and Zero Exponents", "Negative Exponent Comparison", "Comparing Negative Exponent Values", "Description: Negative exponent values can be compared after reciprocal conversion.", "compare, reciprocal, powers"),
    ]
    canonical = [r for i, r in enumerate(raw) if not (i == 6)]
    desc = [
        _row("Meaning of Exponents", "Exponent Notation", "Writing Repeated Multiplication as Powers", "Description: Exponent notation records repeated multiplication compactly by writing the repeated factor as the base and the number of repetitions as the exponent. Students should translate products such as 3 × 3 × 3 × 3 into a power and explain what each part means. Mastery means moving accurately between repeated multiplication and exponential form.", "exponents, repeated multiplication, powers"),
        _row("Meaning of Exponents", "Parts of a Power", "Identifying Base and Exponent", "Description: A power has two parts: the base, which is the repeated factor, and the exponent, which tells how many times it is used. Students use this idea when reading or writing expressions such as 9^5. Mastery means naming both parts and explaining their roles without confusing them.", "base, exponent, power"),
        _row("Meaning of Exponents", "Exponent Expansion", "Expanding Powers into Repeated Multiplication", "Description: Expanding a power means writing the base as a repeated factor the number of times shown by the exponent. This is used to connect compact exponent notation with multiplication. Mastery means expanding expressions such as 4^3 correctly and preserving the base each time.", "expand, powers, factors"),
        _row("Laws of Exponents", "Operations on Powers with the Same Base", "Multiplying Powers with the Same Base", "Description: Multiplying powers with the same base uses the rule a^m × a^n = a^(m+n). Students should recognize that the base remains unchanged while the exponents are added because the repeated factors combine into one longer product. Mastery means choosing this rule only when the bases are the same.", "exponents, same base, multiplication, powers, index law"),
        _row("Laws of Exponents", "Operations on Powers with the Same Base", "Dividing Powers with the Same Base", "Description: Dividing powers with the same base uses the rule a^m ÷ a^n = a^(m-n), where a ≠ 0 and m > n. The base remains unchanged while common repeated factors cancel. Mastery means applying the rule only when the base is non-zero and the expression fits the stated condition.", "exponents, same base, division, non-zero base"),
        _row("Laws of Exponents", "Powers of Powers", "Simplifying a Power Raised to a Power", "Description: A power raised to another power uses the rule (a^m)^n = a^(mn). This matters when an exponential expression repeats an already formed power. Mastery means multiplying the exponents, keeping the base unchanged, and distinguishing this case from same-base multiplication.", "power of power, exponent multiplication, powers"),
        _row("Negative and Zero Exponents", "Zero Exponent Rule", "Simplifying Zero Exponents", "Description: Any non-zero number raised to the power zero equals 1, so a^0 = 1 where a ≠ 0. Students use this rule to simplify powers without expanding them. Mastery means applying the result only for non-zero bases and avoiding the error of making the value zero.", "zero exponent, non-zero base, simplify"),
        _row("Negative and Zero Exponents", "Negative Exponent Rule", "Converting Negative Exponents to Reciprocal Form", "Description: A negative exponent shows reciprocal form: a^(-m) = 1/a^m where a ≠ 0. This is used to rewrite powers with positive exponents in fractions. Mastery means moving correctly from negative exponent notation to reciprocal notation without changing the base incorrectly.", "negative exponent, reciprocal, non-zero base"),
        _row("Negative and Zero Exponents", "Negative Exponent Comparison", "Comparing Negative Exponent Values", "Description: Negative exponent values are compared by first converting each expression to reciprocal form and then comparing the resulting fractions. Students use this when deciding which of two negative powers is larger. Mastery means converting accurately and comparing the fractional values, not just the visible exponents.", "negative exponent, compare, reciprocal"),
    ]
    # Culminations are added BEFORE the Types pass (mirrors the live pipeline),
    # so mined mixed/synthesis Types could also land on culmination rows.
    culminated = generation._ensure_culmination_rows([dict(r) for r in desc])
    typed = [dict(r) for r in culminated]
    type_bodies = [
        "Type 01: Converting repeated multiplication to exponential form Case 01: Write 3 × 3 × 3 × 3 in exponential form Case 02: Write 5 × 5 × 5 using exponent notation",
        "Type 01: Identifying base and exponent Case 01: Identify the base and exponent in 9^5 Case 02: Identify the base and exponent in 7^3",
        "Type 01: Expanding powers into repeated multiplication Case 01: Expand 4^3 Case 02: Expand 2^4 as repeated multiplication",
        "Type 01: Multiplying powers with the same base Case 01: Simplify x^4 × x^7 Case 02: Simplify 2^3 × 2^5 // Misconception: Students may multiply the exponents instead of adding them.",
        "Type 01: Dividing powers with the same base Case 01: Simplify p^9 ÷ p^3 Case 02: Simplify 7^6 ÷ 7^2 // Misconception: Students may subtract bases or ignore a ≠ 0.",
        "Type 01: Simplifying powers raised to powers Case 01: Simplify (a^5)^2 Case 02: Simplify (3^2)^4",
        "Type 01: Simplifying zero exponents Case 01: Simplify 8^0 Case 02: Simplify 5^0 // Misconception: Students may think a^0 equals 0.",
        "Type 01: Converting negative exponents to reciprocal form Case 01: Write 3^-2 in reciprocal form Case 02: Express 2^-3 as a fraction",
        "Type 01: Comparing negative exponent values Case 01: Compare 2^-3 and 3^-2 Case 02: Order two reciprocal-form powers after conversion",
    ]
    normal_rows = [
        row for row in typed
        if not concept_refiner.is_culmination(row["concept_title"])
    ]
    for row, body in zip(normal_rows, type_bodies):
        row["concept_details"] = generation._inject_types(row["concept_details"], body)
    final = [concept_cleanup.clean_concept_record(dict(r)) for r in typed]
    final = concept_refiner.refine_chapter(final)
    report = concept_validator.validate_concept_rows(final, require_culmination=True)
    return {
        "raw_skeleton_rows": raw,
        "canonicalized_rows": canonical,
        "description_refined_rows": desc,
        "culmination_added_rows": culminated,
        "types_assigned_rows": typed,
        "final_rows": final,
        "validator_report": report,
        "question_task_inventory": inventory,
        "mined_types": mined_types,
    }


def _live_stages() -> dict:
    meta = generation._metadata(**METADATA)
    chunks = generation._section_aware_chunks(SAMPLE_MMD)
    sections = [s for c in chunks for s in c["sections"]]
    raw = generation._extract_skeleton_via_api(chunks, meta=meta)
    canonical = generation._consolidate_concepts_via_api(raw, subject=METADATA["subject"], mmd_text=SAMPLE_MMD, meta=meta)
    desc = generation._refine_descriptions_via_api(canonical, subject=METADATA["subject"], mmd_text=SAMPLE_MMD, meta=meta, sections=sections)
    inventory = generation._extract_question_task_inventory_via_api(meta=meta, sections=sections)
    mined_types = generation._mine_types_from_inventory_via_api(meta=meta, inventory=inventory)
    # Culminations are built BEFORE the Types pass so mixed/synthesis Types can
    # be placed on culmination rows too.
    culminated = generation._build_culminations_via_api(desc, meta=meta)
    typed = generation._assign_types_via_api(
        culminated,
        subject=METADATA["subject"],
        mmd_text=SAMPLE_MMD,
        meta=meta,
        sections=sections,
        question_task_inventory=inventory,
        mined_types=mined_types,
    )
    final = generation._repair_records_via_api(typed, meta=meta, stage="final", source_context=SAMPLE_MMD, strict=True)
    final = [concept_cleanup.clean_concept_record(dict(r)) for r in final]
    final = concept_refiner.refine_chapter(final)
    report = generation._validate_final_or_raise(final, stage="quality-sample")
    return {
        "raw_skeleton_rows": raw,
        "canonicalized_rows": canonical,
        "description_refined_rows": desc,
        "culmination_added_rows": culminated,
        "types_assigned_rows": typed,
        "final_rows": final,
        "validator_report": report,
        "question_task_inventory": inventory,
        "mined_types": mined_types,
    }


def _quality_warnings(rows: list[dict], report: dict) -> list[str]:
    warnings = [e["message"] for e in report.get("errors", []) if e.get("severity") == "warning"]
    topics = {r["topic"] for r in rows}
    bad_topics = {"Exercise 1.1", "Exercise 1.2", "Exercise 1.3", "Example", "Examples", "General"}
    if topics & bad_topics:
        warnings.append(f"bad topic(s) present: {sorted(topics & bad_topics)}")
    if not any("a ≠ 0" in r["concept_details"] for r in rows):
        warnings.append("condition 'a ≠ 0' was not preserved")
    if not any(generation._has_meaningful_types(r["concept_details"]) for r in rows if not concept_refiner.is_culmination(r["concept_title"])):
        warnings.append("normal assessable concepts do not have Types")
    return warnings


def _print_report(payload: dict) -> None:
    print("=== Concept Quality Sample ===")
    print(json.dumps(payload["metadata"], indent=2, ensure_ascii=False))
    print(f"Mode: {'live' if payload['live'] else 'mock'} | Write: {payload['write']}")
    print(f"Question / Task Inventory items: {len(payload.get('question_task_inventory', {}).get('items', []))}")
    print(f"Mined Types: {len(payload.get('mined_types', {}).get('types', []))}")
    by_topic: dict[str, list[dict]] = {}
    for row in payload["final_rows"]:
        by_topic.setdefault(row["topic"], []).append(row)
    for topic, rows in by_topic.items():
        print(f"\n# {topic}")
        parents: dict[str, list[dict]] = {}
        for row in rows:
            parents.setdefault(row.get("parent_concept", ""), []).append(row)
        for parent, parent_rows in parents.items():
            print(f"  Parent: {parent}")
            for row in parent_rows:
                print(f"    - {row['concept_title']}")
                for label, content in concept_refiner.split_sections(row["concept_details"]):
                    print(f"      {label}: {content}")
    print("\nValidator:")
    print(json.dumps(payload["validator_report"], indent=2, ensure_ascii=False))
    print(f"Final concept count: {len(payload['final_rows'])}")
    if payload["warnings"]:
        print("Warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def _save_json(payload: dict) -> Path:
    out_dir = config.DATA_DIR / "quality_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"concept_quality_sample_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _write_to_workbook(final_rows: list[dict]) -> dict:
    init_db()
    db = SessionLocal()
    try:
        chapter = db.query(models.Chapter).filter_by(chapter_code="08CBMA_LawsExpQuality").first()
        if chapter is None:
            chapter = models.Chapter(
                chapter_code="08CBMA_LawsExpQuality",
                board=METADATA["board"],
                grade=METADATA["grade"],
                subject=METADATA["subject"],
                unit=METADATA["unit"],
                chapter_title=METADATA["chapter_title"],
                chapter_display_name=directory.chapter_titled_cell(
                    METADATA["chapter_title"],
                    METADATA["board"],
                    METADATA["grade"],
                    METADATA["subject"],
                    book="NCERT",
                ),
            )
            db.add(chapter)
            db.flush()
        created, merged = build_concepts._deposit_concepts(db, chapter, final_rows, "Post", "Quality Sample")
        build_concepts._sync_chapter_topic_summary(chapter)
        db.commit()
        written = writer.append_concepts(db, config.BULK_IMPORT_OUTPUT, created + merged)
        return {"created": len(created), "merged": len(merged), **written}
    finally:
        db.close()


def run(*, live: bool, write: bool) -> dict:
    if live and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--live requires OPENAI_API_KEY")
    stages = _live_stages() if live else _mock_stages()
    payload = {
        "metadata": {**METADATA, "live_or_mock_mode": "live" if live else "mock"},
        "live": live,
        "write": write,
        **stages,
    }
    payload["warnings"] = _quality_warnings(payload["final_rows"], payload["validator_report"])
    if write:
        payload["write_result"] = _write_to_workbook(payload["final_rows"])
    if live and not write:
        payload["json_path"] = str(_save_json(payload))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Laws of Exponents concept quality sample.")
    parser.add_argument("--live", action="store_true", help="Use live OpenAI generation.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", dest="write", action="store_true", help="Append final rows to Bulk Import output.")
    group.add_argument("--no-write", dest="write", action="store_false", help="Do not write to workbook (default).")
    parser.set_defaults(write=False)
    args = parser.parse_args()
    payload = run(live=args.live, write=args.write)
    _print_report(payload)
    if payload.get("json_path"):
        print(f"\nSaved JSON: {payload['json_path']}")


if __name__ == "__main__":
    main()
