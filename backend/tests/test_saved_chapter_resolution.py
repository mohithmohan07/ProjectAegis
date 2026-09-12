"""Resolving a saved checkpoint's chapter across the social-science fold.

A checkpoint records the chapter's RAW subject; the directory presents CBSE
History under Social Science so the dropdowns line up with the chapter codes.
Matching those as plain strings stranded an 81% run behind five empty
dropdowns. These pin the lookup that fixes it — and, more importantly, that the
lookup never rewrites what a checkpoint stored, because that is what keeps the
run resumable.
"""
import pytest

from app import models
from app.db import SessionLocal
from app.services import build_concepts, directory


@pytest.fixture()
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _chapter(db, **kwargs):
    chapter = models.Chapter(
        chapter_code=kwargs["code"],
        board=kwargs.get("board", "CBSE"),
        grade=kwargs.get("grade", "10"),
        subject=kwargs.get("subject", "History"),
        unit=kwargs.get("unit", "Print Culture"),
        chapter_title=kwargs["title"],
        chapter_display_name=kwargs.get("display", kwargs["title"]),
    )
    db.add(chapter)
    db.commit()
    return chapter


def test_a_history_checkpoint_finds_its_chapter_under_social_science(session):
    chapter = _chapter(
        session, code="10CBSS_PCMW_A",
        title="Print Culture and the Modern World A",
    )
    saved = build_concepts._generation_target_identity(chapter)
    assert saved["subject"] == "history"

    resolved = directory.resolve_saved_chapter(session, saved)
    assert resolved["resolved"] is True
    assert resolved["chapter"]["id"] == chapter.id
    # The dropdown holds the directory's subject, not the stored one.
    assert resolved["subject"] == "Social Science"
    assert resolved["subject_folded"] is True


def test_the_lookup_never_changes_what_a_checkpoint_stored(session):
    """The resume check compares the saved identity against a rebuilt one.

    If resolving rewrote the stored subject, every existing checkpoint would
    stop matching and the run this is meant to recover would be refused.
    """
    chapter = _chapter(
        session, code="10CBSS_PCMW_B", title="Print Culture and the Modern World B",
    )
    saved = build_concepts._generation_target_identity(chapter)
    before = dict(saved)

    directory.resolve_saved_chapter(session, saved)

    assert saved == before
    assert build_concepts._generation_target_identity(chapter) == before
    assert before["subject"] == "history"


def test_a_subject_outside_the_fold_is_untouched(session):
    chapter = _chapter(
        session, board="Maharashtra", grade="09", subject="Mathematics",
        unit="Algebra", code="09MHMA_LINEQ", title="Linear Equations",
    )
    saved = build_concepts._generation_target_identity(chapter)
    resolved = directory.resolve_saved_chapter(session, saved)
    assert resolved["resolved"] is True
    assert resolved["subject"] == "Mathematics"
    assert resolved["subject_folded"] is False


def test_icse_geography_does_not_fold_into_history_and_civics(session):
    """ICSE examines History and Civics as one paper; Geography stands alone."""
    history = _chapter(
        session, board="ICSE", grade="09", subject="History", unit="Empire",
        code="09ICHC_EMPIRE", title="The Empire",
    )
    geography = _chapter(
        session, board="ICSE", grade="09", subject="Geography", unit="Maps",
        code="09ICGE_MAPS", title="Reading Maps",
    )

    got_history = directory.resolve_saved_chapter(
        session, build_concepts._generation_target_identity(history))
    got_geography = directory.resolve_saved_chapter(
        session, build_concepts._generation_target_identity(geography))

    assert got_history["chapter"]["id"] == history.id
    assert got_history["subject"] == "History and Civics"
    assert got_geography["chapter"]["id"] == geography.id
    assert got_geography["subject"] == "Geography"


def test_two_folded_subjects_keep_their_own_chapters(session):
    """History and Civics both fold to Social Science and must not swap."""
    history = _chapter(
        session, subject="History", unit="Nationalism",
        code="10CBSS_NATION", title="Rise of Nationalism",
    )
    civics = _chapter(
        session, subject="Civics", unit="Democracy",
        code="10CBSS_POWER", title="Power Sharing",
    )

    got_history = directory.resolve_saved_chapter(
        session, build_concepts._generation_target_identity(history))
    got_civics = directory.resolve_saved_chapter(
        session, build_concepts._generation_target_identity(civics))

    assert got_history["chapter"]["id"] == history.id
    assert got_civics["chapter"]["id"] == civics.id
    assert got_history["subject"] == got_civics["subject"] == "Social Science"


def test_a_renamed_unit_does_not_hide_a_chapter_that_plainly_exists(session):
    chapter = _chapter(
        session, unit="Print Culture and Books", code="10CBSS_RENAMED",
        title="Print Culture Renamed",
    )
    saved = build_concepts._generation_target_identity(chapter)
    saved["unit"] = "a unit that was since renamed"

    resolved = directory.resolve_saved_chapter(session, saved)
    assert resolved["resolved"] is True
    assert resolved["chapter"]["id"] == chapter.id
    # The dropdown is set to the unit the chapter actually lives in now.
    assert resolved["unit"] == "Print Culture and Books"


def test_a_chapter_that_was_never_imported_says_so(session):
    _chapter(session, board="ZZABSENTBOARD", code="10ZZSS_PRESENT",
             title="A Present Chapter")
    refused = directory.resolve_saved_chapter(session, {
        "board": "ZZABSENTBOARD", "grade": "10", "subject": "History",
        "unit": "Print Culture", "chapter_title": "A Chapter Nobody Imported",
        "chapter_code": "10ZZSS_ABSENT",
    })
    assert refused["resolved"] is False
    assert refused["chapter"] is None
    assert "A Chapter Nobody Imported" in refused["reason"]


def test_each_missing_level_names_itself(session):
    """'Not in the current directory' is true of four different problems.

    Scoped to a board of its own: the suite seeds a real catalogue, so asking
    about CBSE would be answered by fixture chapters rather than by this test's.
    """
    # Karnataka folds the social-science strands like CBSE; class 13 is a
    # class no seeded fixture uses, so only this chapter can answer.
    _chapter(session, board="Karnataka", grade="13", subject="History",
             code="13KASS_LEVELS", title="Levels")

    no_board = directory.resolve_saved_chapter(session, {"board": "NOSUCHBOARD"})
    assert "NOSUCHBOARD" in no_board["reason"]

    no_grade = directory.resolve_saved_chapter(
        session, {"board": "Karnataka", "grade": "14"})
    assert "class 14" in no_grade["reason"]

    no_subject = directory.resolve_saved_chapter(
        session, {"board": "Karnataka", "grade": "13", "subject": "Physics"})
    assert "Physics" in no_subject["reason"]
    # It also says what the class DOES hold — under the name the dropdown
    # shows, so the next click is obvious.
    assert "Social Science" in no_subject["reason"]


def test_an_empty_identity_is_refused_rather_than_guessed(session):
    refused = directory.resolve_saved_chapter(session, {})
    assert refused["resolved"] is False
    assert refused["chapter"] is None


def test_the_route_always_answers_200_with_its_reason(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "AUTH_MODE", "local")
    response = client.get("/directory/resolve-chapter", params={
        "board": "ZZZBOARD", "grade": "10", "subject": "History",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is False
    assert body["reason"]


# --------------------------------------------------------------------------- #
# Evidence beats row order. A resolver that returns the first row that looks
# plausible is worse than one that refuses: a reviewer cannot see the guess.
# --------------------------------------------------------------------------- #

def test_a_code_match_outranks_a_chapter_that_merely_shares_the_title(session):
    """The title was edited after the checkpoint; the code still identifies it."""
    decoy = _chapter(
        session, board="ZZRANK", grade="09", subject="History", unit="U",
        code="09ZZSS_DECOY", title="The Rise",
    )
    real = _chapter(
        session, board="ZZRANK", grade="09", subject="History", unit="U",
        code="09ZZSS_RISE", title="The Rise of Nationalism",
    )
    assert decoy.id < real.id  # the decoy would win on row order

    resolved = directory.resolve_saved_chapter(session, {
        "board": "ZZRANK", "grade": "09", "subject": "History", "unit": "U",
        "chapter_title": "The Rise", "chapter_code": "09ZZSS_RISE",
    })
    assert resolved["resolved"] is True
    assert resolved["chapter"]["id"] == real.id


def test_a_moved_chapter_is_found_even_though_a_sibling_stayed_behind(session):
    """The saved unit is a preference, never a filter.

    Pre-filtering on the unit made a chapter that had MOVED unreachable
    whenever any sibling remained in its old unit — and then handed back that
    sibling.
    """
    sibling = _chapter(
        session, board="ZZMOVE", grade="09", subject="History",
        unit="Old Unit", code="09ZZSS_SIB", title="Nationalism",
    )
    moved = _chapter(
        session, board="ZZMOVE", grade="09", subject="History",
        unit="New Unit", code="09ZZSS_MOVED", title="Nationalism",
    )
    assert sibling.id < moved.id

    resolved = directory.resolve_saved_chapter(session, {
        "board": "ZZMOVE", "grade": "09", "subject": "History",
        "unit": "Old Unit",
        "chapter_title": "Nationalism", "chapter_code": "09ZZSS_MOVED",
    })
    assert resolved["resolved"] is True
    assert resolved["chapter"]["id"] == moved.id
    assert resolved["unit"] == "New Unit"


def test_a_genuinely_ambiguous_identity_is_refused_not_guessed(session):
    """Two equally good matches and nothing recorded to separate them."""
    # Karnataka folds History and Civics into one Social Science subject, so
    # these two genuinely collide. On a board outside the fold they would sit
    # in different subjects and never compete.
    first = _chapter(
        session, board="Karnataka", grade="15", subject="History", unit="U",
        code="15KASS_ONE", title="Shared Title",
    )
    _chapter(
        session, board="Karnataka", grade="15", subject="Civics", unit="U",
        code="15KASS_TWO", title="Shared Title",
    )

    refused = directory.resolve_saved_chapter(session, {
        "board": "Karnataka", "grade": "15", "subject": "History", "unit": "U",
        "chapter_title": "Shared Title",
    })
    assert refused["resolved"] is False
    assert refused["chapter"] is None
    assert "more than one chapter" in refused["reason"]
    assert "15KASS_ONE" in refused["reason"]
    # And it did not quietly hand back the lower id.
    assert str(first.id) not in str(refused.get("chapter") or "")


def test_a_unique_title_still_resolves_without_a_code(session):
    chapter = _chapter(
        session, board="ZZTITLE", grade="09", subject="History", unit="U",
        code="09ZZSS_UNIQUE", title="A Unique Title",
    )
    resolved = directory.resolve_saved_chapter(session, {
        "board": "ZZTITLE", "grade": "09", "subject": "History", "unit": "U",
        "chapter_title": "A Unique Title",
    })
    assert resolved["resolved"] is True
    assert resolved["chapter"]["id"] == chapter.id


def test_the_saved_unit_breaks_a_tie_between_equal_titles(session):
    """Same title twice, but one is where the checkpoint said it was."""
    away = _chapter(
        session, board="ZZTIE", grade="09", subject="History",
        unit="Elsewhere", code="09ZZSS_AWAY", title="Tied Title",
    )
    here = _chapter(
        session, board="ZZTIE", grade="09", subject="History",
        unit="Saved Unit", code="09ZZSS_HERE", title="Tied Title",
    )
    assert away.id < here.id

    resolved = directory.resolve_saved_chapter(session, {
        "board": "ZZTIE", "grade": "09", "subject": "History",
        "unit": "Saved Unit", "chapter_title": "Tied Title",
    })
    assert resolved["resolved"] is True
    assert resolved["chapter"]["id"] == here.id
