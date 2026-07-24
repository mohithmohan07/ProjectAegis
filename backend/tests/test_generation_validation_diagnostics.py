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
