"""One logical break is one break, however the model writes it (Q62).

Q38 writes a break in a workbook cell as a PAIR: the ``<br>`` import marker
beside the native line break it renders as, and states that a paired prose
break imports as ONE logical break.

``concept_question_quote.view`` mapped ``<br>`` to a newline WITHOUT consuming
its partner, so ``A<br>\\nB`` viewed as ``A\\n\\nB`` — two breaks for one. The
consequence is that neither faithful transcription a model can actually
produce matched the cell it copied from:

    marker kept, break dropped   "A<br>B"   -> view "A\\nB"    != "A\\n\\nB"
    break kept, marker dropped   "A\\nB"     -> view "A\\nB"    != "A\\n\\nB"

Only the cell's exact bytes, or the double-newline form nothing produces,
were accepted. Q60 reported "locate recovers 100 of 100" for this — that
measurement fed ``locate`` the output of ``view(raw_slice)``, which is by
construction the one form ``view(source)`` contains. It was a tautology.
Measured properly on the owner's files, the pre-fix transport recovers 0 of
13 (job 130) and 0 of 87 (job 139) for BOTH realistic forms; after the fix,
all of them.
"""
import pytest

from app.bulk_import import presentation
from app.services.concept_question_quote import _view_with_raw_offsets, locate, view


STORED = "Types:\nType 01: Reading a ray diagram\nCase 01: Naming the image"


@pytest.mark.parametrize("written, expected", [
    ("A<br>\nB", "A\nB"),          # the Q38 pair — ONE break
    ("A<br>\r\nB", "A\nB"),        # the same pair saved with CRLF
    ("A<br>B", "A\nB"),            # a bare marker
    ("A\nB", "A\nB"),              # a bare break
    ("A<br>\n<br>\nB", "A\n\nB"),  # a paragraph keeps its two breaks
    ("A<br> \nB", "A\nB"),         # the writer's pair with stray spacing
])
def test_one_logical_break_views_as_one_newline(written, expected):
    assert view(written) == expected


def test_katex_row_separators_are_untouched():
    assert "\\\\" in view(r"[Katex]a\\b[/Katex]")


@pytest.mark.parametrize("transcription", [
    "Type 01: Reading a ray diagram<br>\nCase 01: Naming the image",  # exact bytes
    "Type 01: Reading a ray diagram<br>Case 01: Naming the image",    # marker only
    "Type 01: Reading a ray diagram\nCase 01: Naming the image",      # break only
])
def test_every_faithful_transcription_resolves_to_the_cells_own_bytes(transcription):
    """Whichever form the model writes, the STORED text is the cell's bytes."""
    cell = presentation.to_display_rich_text(STORED)
    assert "<br>\n" in cell

    match = locate(cell, transcription)

    assert match is not None
    assert match.raw == "Type 01: Reading a ray diagram<br>\nCase 01: Naming the image"
    assert match.raw in cell


def test_the_offset_map_stays_consistent_with_the_view():
    """locate indexes the raw slice through this map; a drift corrupts quotes."""
    for source in ("A<br>\nB<br>C\nD", STORED, presentation.to_display_rich_text(STORED)):
        rendered, offsets = _view_with_raw_offsets(source)
        assert rendered == view(source)
        assert len(offsets) == len(rendered)


def test_a_paraphrase_is_still_refused():
    """The transport bridges representation only — never wording."""
    cell = presentation.to_display_rich_text(STORED)
    assert locate(cell, "Type 01: reading ray diagrams") is None
    assert locate(cell, "Case 1: Naming the image") is None
