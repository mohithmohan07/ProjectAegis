from __future__ import annotations

import pytest

from app.services import generation as g


def _row(
    title: str,
    details: str,
    *,
    topic: str = "General Term",
    parent: str = "Arithmetic Progressions",
    evidence: str = "",
) -> dict:
    return {
        "topic": topic,
        "parent_concept": parent,
        "concept_title": title,
        "concept_details": details,
        "keywords": "sequence, term",
        "source_evidence": evidence,
    }


def _culmination(topic: str = "General Term") -> dict:
    return _row(
        "Culmination - General Term",
        "Description: Recap the topic. // Types: Type 01: Mixed reasoning "
        "Case 01: Connect the ideas Example: Combine the listed concepts "
        "to solve a mixed review task.",
        topic=topic,
        parent="Culmination",
    )


def test_final_validation_logs_every_fatal_with_exact_location(monkeypatch):
    records = [
        _row(
            "Deriving the General Term",
            r"Description: The raw expression \frac{n}{2} is not canonical. "
            "// Error Analysis: Students may omit the common difference "
            "while substituting values.",
        ),
        _culmination(),
    ]
    logs: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **kwargs: logs.append((message, kwargs)),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"final validation failed: .*rich_text_format.*"
            r"row_index=0.*concept='Deriving the General Term'.*"
            r"field='concept_details'"
        ),
    ):
        g._validate_final_or_raise(records)

    fatal_logs = [
        message for message, _ in logs
        if "fatal validation error:" in message
    ]
    assert fatal_logs
    assert any(
        all(
            fragment in message
            for fragment in (
                "row_index=0",
                "concept='Deriving the General Term'",
                "field='concept_details'",
                "code='rich_text_format'",
                "message='concept_details violates canonical",
                r"snippet='Description: The raw expression \\frac{n}{2}",
            )
        )
        for message in fatal_logs
    )


def test_late_canonicalization_normalizes_common_mathpix_wrappers():
    records = [_row(
        "Reading a Source Figure",
        r"""Description: \begin{figure}[h]
\centering
\includegraphics[width=\textwidth]{https://cdn.example.org/figure.png}
\captionof{figure}{A resistance diagram}
\label{fig:resistance}
\end{figure}
\begin{itemize}
\item Compare both branches.
\item Record the current.
\end{itemize} // Error Analysis: Students may read the two branches as a
single branch.""",
    )]

    out = g._canonicalize_concept_rich_text(records)
    details = out[0]["concept_details"]

    assert r"\begin{figure}" not in details
    assert r"\caption" not in details
    assert r"\item" not in details
    assert (
        '[img src="https://cdn.example.org/figure.png" alt="Source visual"]'
        in details
    )
    assert "Caption: A resistance diagram" in details
    assert "• Compare both branches." in details
    assert g.kr.rich_text_issues(details) == []


def test_mathpix_normalization_does_not_nest_existing_katex():
    details = (
        r"Description: [Katex] \begin{aligned}a&=b\\c&=d\end{aligned} "
        r"[/Katex] // Error Analysis: Students may omit the second relation."
    )

    normalized = g._canonicalize_concept_rich_text([
        _row("Aligned Relations", details)
    ])[0]["concept_details"]

    assert normalized.count("[Katex]") == 1
    assert normalized.count("[/Katex]") == 1
    assert g.kr.rich_text_issues(normalized) == []


def test_inventory_coverage_ignores_katex_wrapper_only_repairs():
    raw = r"Calculate \frac{a}{b}+\frac{c}{d} and explain each step."
    canonical = (
        r"Calculate [Katex] \frac{a}{b}+\frac{c}{d} [/Katex] "
        "and explain each step."
    )

    assert g._inventory_coverage_key(raw) == (
        g._inventory_coverage_key(canonical)
    )


def test_final_rich_text_repair_rejects_non_formatting_changes(monkeypatch):
    details = (
        r"Description: Compare the quantities using \frac{a}{b}."
        "\nAchieving Mastery: Explaining the comparison correctly. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "the denominator is irrelevant.; Error Analysis: Students may omit "
        "the denominator."
    )
    records = [
        _row("Compare fractions", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    calls = 0

    def changed_api(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"rows": [{
            "topic": "T",
            "parent_concept": "P",
            "concept": "Compare fractions",
            "concept_description": (
                details.replace(
                    r"Compare the quantities using \frac{a}{b}.",
                    (
                        r"Replace the quantities using "
                        r"[Katex] \frac{a}{b} [/Katex]."
                    ),
                )
            ),
            "keywords": "sequence, term",
        }]}

    monkeypatch.setattr(g, "_openai_json", changed_api)

    repaired, changed = g._repair_final_rich_text_via_api(
        records,
        meta=g._metadata(subject="Mathematics"),
        inventory={"items": [], "stats": {}},
        mined_types={"types": []},
    )

    assert calls == 1
    assert changed is False
    assert repaired == records


def test_saved_final_checkpoint_repairs_rich_text_once_and_persists(
    monkeypatch,
):
    source = "# T\nA short source-grounded discussion of a numerical pattern."
    raw_title = r"Use the Finite-sum Formula S_n = \frac{n}{2}(a+l)"
    analysis = (
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "unlike denominators can be added directly.; Error Analysis: Students "
        "may add denominators without first finding a common denominator."
    )
    raw_records = [
        _row(
            raw_title,
            (
                r"Description: Compare two fractional quantities using "
                r"\frac{a}{b} before combining them."
                "\nAchieving Mastery: Explaining why a common denominator is "
                "needed before adding fractions. // Types: Type 01: Add unlike "
                "fractions Case 01: Build equivalent fractional quantities "
                r"Example 01: Calculate \frac{1}{2}+\frac{1}{3} and explain "
                "each transformation. // " + analysis
            ),
            topic="T",
            parent="P",
        ),
        _row(
            f"Culmination - {raw_title}",
            (
                "Description: Recap of Use the Finite-sum Formula "
                r"S_n = \frac{n}{2}(a+l)."
            ),
            topic="T",
            parent="Culmination",
        ),
    ]
    raw_records[0]["source_evidence"] = "SRC-LOCKED-01"
    raw_records[0]["review_metadata"] = {"locked": True}
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=raw_records,
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    repaired_records = [
        _row(
            raw_title,
            (
                "Description: Compare two fractional quantities using "
                r"[Katex] \frac{a}{b} [/Katex] before combining them."
                "\nAchieving Mastery: Explaining why a common denominator is "
                "needed before adding fractions. // Types: Type 01: Add unlike "
                "fractions Case 01: Build equivalent fractional quantities "
                "Example 01: Calculate "
                r"[Katex] \frac{1}{2}+\frac{1}{3} [/Katex] and explain each "
                "transformation. // " + analysis
            ),
            topic="T",
            parent="P",
        ),
        _row(
            f"Culmination - {raw_title}",
            (
                "Description: Recap of Use the Finite-sum Formula "
                r"[Katex] S_n = \frac{n}{2}(a+l) [/Katex]."
            ),
            topic="T",
            parent="Culmination",
        ),
    ]
    calls: list[tuple[str, str]] = []

    def repair_api(system, user, **_kwargs):
        calls.append((system, user))
        return {
            "rows": [{
                "topic": row["topic"],
                "parent_concept": row["parent_concept"],
                "concept": row["concept_title"],
                "concept_description": row["concept_details"],
                "keywords": "model attempted to replace keywords",
                "source_evidence": "MODEL-REWRITE",
            } for row in repaired_records],
        }

    monkeypatch.setattr(g, "_openai_json", repair_api)
    emitted: list[dict] = []

    repaired = g.concepts_from_mmd(
        source,
        subject="Mathematics",
        live=True,
        resume_checkpoint=checkpoint,
        checkpoint_callback=emitted.append,
    )

    assert len(calls) == 1
    assert "rich_text_format" in calls[0][1]
    assert "Equations MUST be wrapped" in calls[0][0]
    assert all(
        g.kr.rich_text_issues(row["concept_details"]) == []
        for row in repaired
    )
    assert repaired[0]["keywords"] == "sequence, term"
    assert repaired[0]["source_evidence"] == "SRC-LOCKED-01"
    assert repaired[0]["review_metadata"] == {"locked": True}
    assert emitted[-1]["stage"] == "final_content_ready"
    assert emitted[-1]["records"] == repaired

    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the repaired final checkpoint must not call the API")
        ),
    )
    second_emitted: list[dict] = []
    resumed = g.concepts_from_mmd(
        source,
        subject="Mathematics",
        live=True,
        resume_checkpoint=emitted[-1],
        checkpoint_callback=second_emitted.append,
    )

    assert resumed == repaired
    assert second_emitted == []


def test_final_checkpoint_is_emitted_only_after_validation(monkeypatch):
    records = [
        _row(
            "C",
            (
                "Description: A complete concept description."
                "\nAchieving Mastery: Applying the concept correctly. // "
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "believe every condition is optional.; Error Analysis: "
                "Students may omit a required condition."
            ),
            topic="T",
            parent="P",
        ),
        _culmination(topic="T"),
    ]
    checkpoint = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    monkeypatch.setattr(
        g,
        "_prepare_final_concept_content",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        g,
        "_validate_final_or_raise",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("final gate rejected rows")
        ),
    )
    emitted: list[dict] = []

    with pytest.raises(RuntimeError, match="final gate rejected rows"):
        g.concepts_from_mmd(
            "# T\nA short source section.",
            subject="Mathematics",
            live=True,
            resume_checkpoint=checkpoint,
            checkpoint_callback=emitted.append,
        )

    assert not any(
        item.get("stage") == "final_content_ready"
        for item in emitted
    )


def test_rejected_saved_final_falls_back_to_preceding_checkpoint(monkeypatch):
    details = (
        "Description: A complete concept description."
        "\nAchieving Mastery: Applying the concept correctly. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "every condition is optional.; Error Analysis: Students may omit a "
        "required condition."
    )
    prior_records = [
        _row("Prior-stage concept", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    stale_final_records = [
        _row("Rejected final concept", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    inventory = {"items": [], "stats": {}}
    mined_types = {"types": []}
    prior = g._make_concept_checkpoint(
        "post_type_assignment",
        records=prior_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    stale_final = g._make_concept_checkpoint(
        "final_content_ready",
        records=stale_final_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "stage": "final_content_ready",
        "checkpoints": [prior, stale_final],
    }
    finalized: list[list[str]] = []

    def finalize(current, **_kwargs):
        finalized.append([row["concept_title"] for row in current])
        return current

    validations: list[list[str]] = []

    def validate(current, **_kwargs):
        validations.append([row["concept_title"] for row in current])
        if len(validations) == 1:
            raise RuntimeError("legacy final rejected")
        return {"ok": True, "errors": [], "summary": {}}

    monkeypatch.setattr(g, "_prepare_final_concept_content", finalize)
    monkeypatch.setattr(g, "_validate_final_or_raise", validate)
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback should reuse the preceding checkpoint")
        ),
    )
    emitted: list[dict] = []

    out = g.concepts_from_mmd(
        "# T\nA short source section.",
        subject="Mathematics",
        live=True,
        resume_checkpoint=history,
        checkpoint_callback=emitted.append,
    )

    assert validations[0][0] == "Rejected final concept"
    assert validations[1][0] == "Prior-stage concept"
    assert finalized == [[
        "Prior-stage concept",
        "Culmination - General Term",
    ]]
    assert out[0]["concept_title"] == "Prior-stage concept"
    assert [item["stage"] for item in emitted] == ["final_content_ready"]
    assert emitted[0]["records"] == out


def test_method_recovery_canonicalizes_raw_math_before_strict_validation(
    monkeypatch,
):
    anchor_id = "METHOD-1DCE76C4D2"
    anchor = {
        "anchor_id": anchor_id,
        "topic_hint": "General Term",
        "source_evidence": "derive the general term from repeated addition",
        "required_formulas": [r"a_n=a+(n-1)d"],
    }
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *args, **kwargs: {
            "rows": [{
                "topic": "General Term",
                "parent_concept": "Arithmetic Progressions",
                "concept": "Deriving the General Term",
                "concept_description": (
                    r"Description: Repeated addition gives "
                    r"$a_n=a+(n-1)d$. // Error Analysis: Students may omit "
                    "the common difference while substituting values."
                ),
                "keywords": "sequence, term",
                "source_evidence": anchor_id,
            }],
        },
    )

    recovered = g._recover_method_anchor_rows_via_api(
        [anchor],
        chunk_text="Relevant source text",
        meta=g._metadata(subject="Mathematics"),
        max_attempts=1,
    )

    assert len(recovered) == 1
    assert "[Katex] a_n=a+(n-1)d [/Katex]" in (
        recovered[0]["concept_details"])
    assert g.kr.rich_text_issues(recovered[0]["concept_details"]) == []


def test_method_recovery_logs_precise_row_rejection_reason(monkeypatch):
    anchor_id = "METHOD-1DCE76C4D2"
    anchor = {
        "anchor_id": anchor_id,
        "topic_hint": "Electric Power",
        "source_evidence": "electric power is given by current times voltage",
        "required_formulas": ["P=VI"],
    }
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *args, **kwargs: {
            "rows": [{
                "topic": "Electric Power",
                "parent_concept": "",
                "concept": "Calculating Electric Power",
                "concept_description": "Description: Power is energy per time.",
                "keywords": "power",
                "source_evidence": anchor_id,
            }],
        },
    )
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **kwargs: logs.append(message),
    )

    with pytest.raises(RuntimeError, match=anchor_id):
        g._recover_method_anchor_rows_via_api(
            [anchor],
            chunk_text="Relevant source text",
            meta=g._metadata(subject="Physics"),
            max_attempts=1,
        )

    assert any(
        "attempt=1" in message
        and "row_index=0" in message
        and "anchor='METHOD-1DCE76C4D2'" in message
        and "missing or non-string required field(s): parent_concept" in message
        for message in logs
    )


def test_rejected_types_rewrite_keeps_non_type_repairs():
    question = (
        "Find the tenth term of the progression 3, 7, 11, 15 and explain "
        "which values were substituted."
    )
    inventory = {
        "items": [{
            "qid": "QINV-0001",
            "topic_hint": "General Term",
            "raw_task": question,
        }],
    }
    original = [_row(
        "Applying the General Term",
        "Description: The description still needs repair. // Types: "
        "Type 01: Direct substitution Case 01: Locate a specified term "
        f"Example: {question} // Error Analysis: Students may omit the "
        "common difference while substituting values.",
    )]
    candidate = [dict(original[0])]
    candidate[0]["parent_concept"] = "Term Formula"
    candidate[0]["concept_details"] = (
        "Description: The repaired description explains how the first term, "
        "common difference, and position determine a selected term. // Types: "
        "Type 01: Direct substitution Case 01: Locate a specified term "
        "Example: Use the formula. // Error Analysis: Students may omit the "
        "common difference while substituting values."
    )

    accepted = g._accept_exact_inventory_type_review(
        original, candidate, inventory)

    assert accepted is not original
    assert accepted[0]["parent_concept"] == "Term Formula"
    assert "The repaired description explains" in accepted[0]["concept_details"]
    assert f"Example: {question}" in accepted[0]["concept_details"]
    assert "Example: Use the formula." not in accepted[0]["concept_details"]
    assert not g._rendered_inventory_coverage_defects(
        accepted, inventory)["missing"]
