"""Job 130: a faithful quote refused because the cell renders its own break.

The owner's log, verbatim:

    Step 2 could not prepare the reviewed files: reviewed_file.extract decision
    for post failed its mechanical response contract after 3 bounded correction
    attempt(s), and the Fixer could not produce a contract-satisfying decision
    either: Invalid reviewed-file schema: 1 validation error for Extracted
    rationale — Extra inputs are not permitted
    [input_value='The block was caused by ... cited reviewed blocks.']

The Fixer's rationale ends with "cited reviewed blocks." — the tail of the
checker's quoting defect. That gate, not the images the owner suspected, is
what stopped the run. (The reviewed files carry no embedded pictures at all:
no ``xl/media``, no drawings. And an image would have DISABLED the gate, via
its ``not visual`` escape.)

A reviewed workbook cell carries the contract's paired form — the ``<br>``
import marker beside the native line break it displays as (Q38). A model
copying what it sees writes the break and not the marker. Measured on the
owner's own files, every display-view quote spanning a ``<br>`` fails the raw
comparison: 13 of 13 in job 130's reviewed file, 87 of 87 in job 139's. The
Triangles file carries no ``<br>`` at all and its extraction passed — the
control.
"""
import copy

import openpyxl
import pytest

from app.services import reviewed_file_input as reviewed
from app.services.phase3 import kernel
from tests.test_independent_reviewed_files import critic, setup_job


PAIRED_CELL = (
    "Description: A lens bends light at each curved surface.<br>\n"
    "Achieving Mastery: A learner can trace the change in ray direction.<br>\n"
    "Types: Type 01: Reading a ray diagram<br>\n"
    "Case 01: Naming the image a converging lens forms<br>\n"
    "Example 01: Where is the image formed when the object is beyond 2F?"
)


def reviewed_workbook(path):
    """The canonical Concept workbook shape the reviewer actually edits."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Objective"
    sheet["A1"] = "Concept"
    sheet["A2"] = "concept_title"
    sheet["B2"] = "concept_details"
    sheet["A3"] = "Refraction through a lens"
    sheet["B3"] = PAIRED_CELL
    book.save(path)
    return path


def display_view_extraction(document):
    """What a model returns when it copies the cell as displayed."""
    from app.services.concept_question_quote import view

    block = document["blocks"][2]
    question = "Example 01: Where is the image formed when the object is beyond 2F?"
    return {
        "pre_scope_verdict": "retained",
        "concepts": [{
            "topic": "Lenses", "concept_title": "Refraction through a lens",
            "parent_concept": "", "concept_details": view(PAIRED_CELL),
            "keywords": "lens", "source_refs": [block["ref"]],
        }],
        "questions": [{
            "concept_index": 0, "source_refs": [block["ref"]],
            # The model quotes the DISPLAYED wording, across the paired break.
            "question_spans": [view(
                "Case 01: Naming the image a converging lens forms<br>\n" + question)],
            "context_spans": [], "answer_spans": [], "pre_answer": "",
            "options": [], "tables": [], "image_refs": [],
            "type_title": "Reading a ray diagram", "type_definition": "Read the diagram.",
            "case_title": "", "case_definition": "", "placement_section": "types",
        }],
        "dispositions": [
            {"source_ref": b["ref"], "disposition": "concept", "rationale": "Reviewed."}
            for b in document["blocks"]],
        "empty_reason": "",
    }


def test_the_paired_break_is_the_same_break(tmp_path):
    """The two forms of one break differ only in representation."""
    from app.services.concept_question_quote import locate, view

    assert view(PAIRED_CELL) != PAIRED_CELL
    assert view(PAIRED_CELL) not in PAIRED_CELL       # what the gate refused
    assert locate(PAIRED_CELL, view(PAIRED_CELL)).raw == PAIRED_CELL


def test_a_display_view_quote_is_accepted_and_stored_as_the_cell_wrote_it(db, tmp_path):
    job = setup_job(db)
    path = reviewed_workbook(tmp_path / "reviewed.xlsx")
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    document = job.question_inventory[reviewed.INPUTS]["post"]

    current = reviewed.prepare(
        db, job, lane="post", owner_sub="local:default",
        provider=lambda _r: copy.deepcopy(display_view_extraction(document)),
        critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"))

    item = current["question_task_inventory"]["items"][0]
    # Stored in the cell's own raw form, so the <br> markers Rule 0 requires in
    # the Master survive instead of a rendered break being frozen in.
    assert "<br>" in item["raw_task"]
    assert item["raw_task"] in document["blocks"][2]["text"]
    assert item["raw_task"].endswith(
        "Example 01: Where is the image formed when the object is beyond 2F?")


def test_an_invented_quote_is_still_refused(db, tmp_path):
    """The transport must not become a licence to paraphrase."""
    job = setup_job(db)
    path = reviewed_workbook(tmp_path / "reviewed.xlsx")
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    document = job.question_inventory[reviewed.INPUTS]["post"]
    invented = copy.deepcopy(display_view_extraction(document))
    invented["questions"][0]["question_spans"] = [
        "Where is the image formed when the object is placed beyond twice the focal length?"]

    with pytest.raises(kernel.ContractError) as raised:
        reviewed.prepare(
            db, job, lane="post", owner_sub="local:default",
            provider=lambda _r: copy.deepcopy(invented), critic=critic,
            fixer=lambda _r: copy.deepcopy(invented),
            store=kernel.DecisionStore(tmp_path / "decisions"))

    assert "cited reviewed blocks" in str(raised.value)
