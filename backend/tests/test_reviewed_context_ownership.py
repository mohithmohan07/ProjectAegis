"""Q51 D5: the reviewed-file author owns a reviewed row's placement.

The governing response instruction fold in ``assessment_source_inventory``
reads wording and adjacency to decide that a row is context only. That stays
for a SOURCE-EXTRACTED bank. On a reviewed Concept file the author already
returned each row's explicit placement and separated its question spans from
its context spans, and the team fixed that set by hand, so the fold must not
demote a question the reviewer deliberately kept. The exemption is decided
from the row's recorded ``source_kind`` provenance only.
"""
from __future__ import annotations

import pathlib

import pytest

from app.services import assessment_source_inventory as si


# The exact provenance string ``reviewed_file_input.prepare`` stamps on every
# row it extracts from the team's reviewed Concept workbook.
REVIEWED_KIND = "reviewed_file"
DIRECTIVE = "Read the following questions and tick ☑ the correct answer."
REVIEWER_CONTEXT = "Context the reviewed-file author kept with this row."


def test_reviewed_provenance_is_the_signal_reviewed_file_input_writes():
    """The exemption keys off recorded provenance, not on any content test."""
    from app.services import reviewed_file_input, reviewed_question_polishing

    assert si.REVIEWED_SOURCE_KIND == REVIEWED_KIND
    assert reviewed_question_polishing.REVIEWED_SOURCE_KIND == REVIEWED_KIND
    assert si.from_reviewed_file({"source_kind": REVIEWED_KIND}) is True
    assert si.from_reviewed_file({"source_kind": "question"}) is False
    assert si.from_reviewed_file({}) is False
    assert '"source_kind": "reviewed_file"' in (
        pathlib.Path(reviewed_file_input.__file__).read_text()
    )


def _row(qid, raw_task, *, source_kind, shared_context=""):
    row = {"qid": qid, "raw_task": raw_task}
    if source_kind:
        row["source_kind"] = source_kind
    if shared_context:
        row["shared_context"] = shared_context
    return row


def _block(prefix, *, source_kind, context=""):
    """A directive row followed by two option-bearing rows, one provenance."""
    return [
        _row(f"{prefix}-1", DIRECTIVE, source_kind=source_kind,
             shared_context=context),
        _row(f"{prefix}-2", "Which word rhymes?\n(a) Cat\n(b) Drum",
             source_kind=source_kind, shared_context=context),
        _row(f"{prefix}-3", "Which word is a naming word?\n(a) Run\n(b) Cup",
             source_kind=source_kind, shared_context=context),
    ]


def _accounting(built, items):
    """Every inventory QID is accounted exactly once, atoms or dispositions."""
    accepted = [atom["source_qid"] for atom in built["atoms"]]
    flagged = [row["source_qid"] for row in built["context_only"]]
    assert built["zero_loss"] == {
        "missing": [], "unexpected": [], "double_counted": [], "holds": True,
    }
    assert sorted(accepted + flagged) == sorted(item["qid"] for item in items)
    assert len(accepted + flagged) == len(items)
    assert built["ledger"] == {qid: index for index, qid in enumerate(accepted)}


def test_reviewed_directive_row_keeps_its_own_assessment_candidate():
    """(a) The reviewed row the reviewer kept is not demoted to context."""
    items = _block("QFILE", source_kind=REVIEWED_KIND,
                   context=REVIEWER_CONTEXT)

    built = si.build_source_atoms({"items": items}, source_document_hash="doc")

    assert [atom["source_qid"] for atom in built["atoms"]] == [
        "QFILE-1", "QFILE-2", "QFILE-3",
    ]
    assert built["atoms"][0]["raw_text"] == DIRECTIVE
    assert built["context_only"] == []
    # The reviewed author's own context spans reach every row unchanged.
    assert [atom["shared_context"] for atom in built["atoms"]] == [
        REVIEWER_CONTEXT, REVIEWER_CONTEXT, REVIEWER_CONTEXT,
    ]
    _accounting(built, items)


def test_reviewed_exemption_is_recorded_for_every_reviewed_row():
    """The skip is visible: each exempt row names its reason and policy."""
    items = _block("QFILE", source_kind=REVIEWED_KIND)

    built = si.build_source_atoms({"items": items}, source_document_hash="doc")

    retained = built["reviewed_placement_retained"]
    assert [row["source_qid"] for row in retained] == [
        "QFILE-1", "QFILE-2", "QFILE-3",
    ]
    for row in retained:
        assert row["source_kind"] == REVIEWED_KIND
        assert row["role"] == "reviewed_file_placement"
        assert row["policy"] == si.REVIEWED_PLACEMENT_AUTHORITY
        assert "reviewed-file author" in row["reason"]
        assert "not applied" in row["reason"]


@pytest.mark.parametrize("source_kind", ["", "question", "activity"])
def test_source_extracted_directive_is_still_folded_exactly_as_today(source_kind):
    """(b) Every other provenance keeps the long-standing behaviour."""
    items = _block("QINV", source_kind=source_kind)

    built = si.build_source_atoms({"items": items}, source_document_hash="doc")

    assert [atom["source_qid"] for atom in built["atoms"]] == [
        "QINV-2", "QINV-3",
    ]
    assert built["context_only"] == [{
        "source_qid": "QINV-1",
        "role": "shared_response_instruction",
        "raw_text": DIRECTIVE,
        "attached_source_qids": ["QINV-2", "QINV-3"],
    }]
    assert [atom["shared_context"] for atom in built["atoms"]] == [
        DIRECTIVE, DIRECTIVE,
    ]
    assert built["reviewed_placement_retained"] == []
    _accounting(built, items)


def test_mixed_inventory_folds_only_the_source_extracted_directive():
    """(c) The decision is per row, never per batch."""
    items = [
        *_block("QINV", source_kind="question"),
        *_block("QFILE", source_kind=REVIEWED_KIND,
                context=REVIEWER_CONTEXT),
    ]

    built = si.build_source_atoms({"items": items}, source_document_hash="doc")

    assert [atom["source_qid"] for atom in built["atoms"]] == [
        "QINV-2", "QINV-3", "QFILE-1", "QFILE-2", "QFILE-3",
    ]
    assert [row["source_qid"] for row in built["context_only"]] == ["QINV-1"]
    assert built["context_only"][0]["attached_source_qids"] == [
        "QINV-2", "QINV-3",
    ]
    assert [row["source_qid"] for row in built["reviewed_placement_retained"]] == [
        "QFILE-1", "QFILE-2", "QFILE-3",
    ]
    # The source-extracted directive reaches its own option rows only; the
    # reviewed rows keep exactly the context their author wrote for them.
    assert [atom["shared_context"] for atom in built["atoms"]] == [
        DIRECTIVE, DIRECTIVE,
        REVIEWER_CONTEXT, REVIEWER_CONTEXT, REVIEWER_CONTEXT,
    ]
    _accounting(built, items)


def test_reviewed_rows_are_not_governed_by_an_adjacent_source_directive():
    """A reviewed row never absorbs a neighbouring source-extracted directive."""
    items = [
        _row("QINV-1", DIRECTIVE, source_kind="question"),
        *[
            _row(f"QFILE-{n}", f"Which one is correct?\n(a) A{n}\n(b) B{n}",
                 source_kind=REVIEWED_KIND,
                 shared_context=REVIEWER_CONTEXT)
            for n in (2, 3)
        ],
    ]

    built = si.build_source_atoms({"items": items}, source_document_hash="doc")

    assert [atom["source_qid"] for atom in built["atoms"]] == [
        "QINV-1", "QFILE-2", "QFILE-3",
    ]
    assert built["context_only"] == []
    assert [atom["shared_context"] for atom in built["atoms"]] == [
        "", REVIEWER_CONTEXT, REVIEWER_CONTEXT,
    ]
    _accounting(built, items)


@pytest.mark.parametrize(
    "source_kind", [REVIEWED_KIND, "question"],
)
def test_accounting_holds_for_both_provenances(source_kind):
    """(d) Nothing disappears from the zero-loss accounting either way."""
    items = _block("QX", source_kind=source_kind)

    built = si.build_source_atoms({"items": items}, source_document_hash="doc")

    _accounting(built, items)
    named = (
        [atom["source_qid"] for atom in built["atoms"]]
        + [row["source_qid"] for row in built["context_only"]]
    )
    assert set(named) == {"QX-1", "QX-2", "QX-3"}
    exempt = {row["source_qid"] for row in built["reviewed_placement_retained"]}
    assert exempt == (
        {"QX-1", "QX-2", "QX-3"}
        if source_kind == REVIEWED_KIND else set()
    )


def test_reviewed_row_without_a_qid_still_fails_the_identity_gate():
    """The exemption never launders a malformed reviewed row."""
    items = [
        {"raw_task": DIRECTIVE, "source_kind": REVIEWED_KIND},
        _row("QFILE-2", "Which one?\n(a) A\n(b) B",
             source_kind=REVIEWED_KIND),
    ]

    with pytest.raises(si.SourceInventoryError, match=r"without a QID"):
        si.build_source_atoms({"items": items})
