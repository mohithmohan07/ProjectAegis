"""Regression coverage for atomic closed-world source Examples."""

from app.services import generation as g


def _row(details: str = "") -> dict:
    return {
        "topic": "Power Play",
        "parent_concept": "Exponent Methods",
        "concept_title": "Interpreting Exponent Procedures",
        "concept_details": details or (
            "Description: Interpret and verify exponent procedures."
        ),
        "keywords": "exponents, powers",
    }


def _item(qid: str, prompt: str) -> dict:
    return {
        "qid": qid,
        "source_kind": "exercise",
        "source_label": qid,
        "topic_hint": "Power Play",
        "raw_task": prompt,
        "normalized_task": prompt,
    }


def _inventory(*prompts: str) -> dict:
    return {
        "items": [
            _item(f"QINV-{index:04d}", prompt)
            for index, prompt in enumerate(prompts, start=1)
        ],
        "stats": {"total_inventory_items": len(prompts)},
    }


def test_repaired_source_examples_with_type_and_case_tokens_remain_owned():
    first = (
        "A learner labels a step Type 01: repeated multiplication and then "
        "writes Case 01: multiply the exponents. Check the reasoning and "
        "correct the power if necessary."
    )
    second = (
        "Compare the notes Example: 2^3 means three factors of 2 and "
        "Type 02: a power of a power. Explain which note applies to (2^3)^2."
    )
    inventory = _inventory(first, second)

    repaired = g._repair_rendered_inventory_coverage(
        [_row()], inventory, {"types": []}
    )

    assert g._rendered_inventory_coverage_defects(
        repaired, inventory
    ) == {"missing": [], "duplicate": []}
    # The terminal repair boundary and deposit scanner now share one identity.
    assert g._unexpected_rendered_type_examples(repaired, inventory) == []


def test_neutralized_table_reference_keeps_source_example_covered():
    """Deposit cleaning rewrites "Table 11.2" into "the given table".

    The inventory prompt keeps the source pointer, so the coverage key must
    neutralize both sides identically or the only table-citing questions in a
    chapter become permanently "missing + unexpected" at the deposit gate.
    """
    source_prompt = (
        "Use the data in Table 11.2 to answer the following - (a) Which "
        "among iron and mercury is a better conductor? (b) Which material "
        "is the best conductor?"
    )
    rendered_prompt = (
        "Use the data in the given table to answer the following - (a) Which "
        "among iron and mercury is a better conductor? (b) Which material "
        "is the best conductor?"
    )
    inventory = _inventory(source_prompt)
    records = [_row(
        "Description: Interpret and verify exponent procedures. // Types: "
        "Type 01: Comparing conductors Case 01: A resistivity table is "
        f"supplied Example 01: {rendered_prompt}"
    )]

    assert g._rendered_inventory_coverage_defects(
        records, inventory
    ) == {"missing": [], "duplicate": []}
    assert g._unexpected_rendered_type_examples(records, inventory) == []
    out = g._enforce_rendered_inventory_coverage(
        records, inventory, {"types": []}
    )
    assert g._rendered_inventory_coverage_defects(
        out, inventory
    ) == {"missing": [], "duplicate": []}


def test_closed_inventory_still_rejects_a_genuinely_unowned_example():
    source_prompt = (
        "Use the laws of exponents to simplify 2^3 multiplied by 2^4 and "
        "explain the exponent in the result."
    )
    extra_prompt = (
        "Invent a different exponent question that does not occur in the "
        "source inventory."
    )
    records = [_row(
        "Description: Interpret and verify exponent procedures. // Types: "
        "Type 01: Applying exponent laws Case 01: Equal bases are multiplied "
        f"Example 01: {source_prompt} "
        "Case 02: An unrelated generated task is supplied "
        f"Example 01: {extra_prompt}"
    )]
    inventory = _inventory(source_prompt)

    unexpected = g._unexpected_rendered_type_examples(records, inventory)

    assert len(unexpected) == 1
    assert unexpected[0]["reason"] == "not_in_inventory"
    assert g._inventory_coverage_key(unexpected[0]["example"]) == (
        g._inventory_coverage_key(extra_prompt)
    )


def test_duplicate_owned_prompt_is_not_misreported_as_unexpected():
    prompt = (
        "The source itself says Example: compare 3^2 and 2^3, then explain "
        "which value is greater."
    )
    inventory = _inventory(prompt)
    records = [_row(
        "Description: Interpret and verify exponent procedures. // Types: "
        "Type 01: Comparing powers Case 01: Two powers are supplied "
        f"Example 01: {prompt} Example 02: {prompt}"
    )]

    assert g._unexpected_rendered_type_examples(records, inventory) == []
    assert g._rendered_inventory_coverage_defects(
        records, inventory
    )["duplicate"] == ["QINV-0001"]


def test_final_validation_uses_atomic_source_prompt(monkeypatch):
    prompt = (
        "A learner labels a step Type 01: repeated multiplication and then "
        "writes Case 01: multiply the exponents. Explain the faulty reasoning "
        "and state the correction."
    )
    records = [_row(
        "Description: Interpret and verify exponent procedures. // Types: "
        "Type 01: Checking an exponent procedure "
        "Case 01: A learner supplies a labelled procedure "
        f"Example 01: {prompt}"
    )]
    inventory = _inventory(prompt)

    # Isolate the closed source-inventory boundary. The concept hierarchy
    # validator deliberately interprets Type/Case tokens inside this synthetic
    # prompt as structure; production reaches this gate only after that separate
    # validation has already passed. (The retired semantic-defects family no
    # longer runs inside _validate_final_or_raise, so only the row validator
    # needs to be stubbed.)
    monkeypatch.setattr(
        g.cv,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "errors": [],
            "summary": {"warnings": 0, "errors": 0},
        },
    )

    report = g._validate_final_or_raise(records, inventory=inventory)

    assert report["summary"]["errors"] == 0
