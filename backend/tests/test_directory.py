from app.services import directory


def test_parse_code_prefix():
    assert directory.parse_code_prefix("10CBMA_Circles_PL") == ("10", "CBSE", "Mathematics")
    assert directory.parse_code_prefix("09ICPH_Motion") == ("09", "ICSE", "Physics")
    assert directory.parse_code_prefix("nothing here") is None


def test_make_chapter_code_roundtrips_board_and_subject():
    code = directory.make_chapter_code("CBSE", "10", "Mathematics", "Circles")
    assert code.startswith("10CBMA_")
    assert directory.parse_code_prefix(code) == ("10", "CBSE", "Mathematics")


def test_chapter_tag_includes_book_and_board_tokens():
    assert directory.chapter_tag("CBSE", "09", "Mathematics", book="RS Aggarwal") \
        == "09_Mathematics_CBSE_RS"
    assert directory.chapter_tag("CBSE", "09", "Mathematics", book="RD Sharma") \
        == "09_Mathematics_CBSE_RD"
    assert directory.chapter_tag("CBSE", "09", "Mathematics", book="NCERT") \
        == "09_Mathematics_CBSE_NCERT"
    assert directory.chapter_tag("Karnataka", "09", "Mathematics", book="KTS") \
        == "09_Mathematics_KSTATE_KTS"
    assert directory.chapter_tag("MSBSHSE", "09", "Mathematics", book="MSBT") \
        == "09_Mathematics_MSBSHSE_MSBT"
    assert directory.chapter_tag("ICSE", "09", "Mathematics", book="Selina") \
        == "09_Mathematics_ICSE_SELINA"
    assert directory.chapter_tag("ICSE", "09", "Mathematics", book="Oswaal") \
        == "09_Mathematics_ICSE_OSWAAL"


def test_chapter_titled_cell_matches_required_format():
    title = directory.chapter_titled_cell(
        "Number System", "CBSE", "09", "Mathematics", book="RS Aggarwal")
    assert title == "Number System (09_Mathematics_CBSE_RS)"


def test_parse_chapter_human_tag():
    parsed = directory.parse_chapter_human_tag("Number System (09_Mathematics_CBSE_RS)")
    assert parsed["grade"] == "09"
    assert parsed["board"] == "CBSE"
    assert parsed["subject"] == "Mathematics"
    assert parsed["book"] == "RS"


def test_derive_chapter_meta_from_human_tag():
    meta = directory.derive_chapter_meta(
        "Number System",
        "Number System (09_Mathematics_CBSE_RD)",
    )
    assert meta["grade"] == "09"
    assert meta["board"] == "CBSE"
    assert meta["subject"] == "Mathematics"
    assert meta["chapter_code"].startswith("09CBMA_")


def test_parse_ncert_source_filenames():
    """The team's actual Class 08 NCERT source-file names parse correctly."""
    cases = {
        "CBSE_NCERT_G08_CH02_RESHAPING_INDIAS_POLITICAL_MAP.pdf":
            ("CBSE", "08", "CH", 2, "Reshaping Indias Political Map"),
        "CBSE_NCERT_G08_UN02_VALUES_AND_DISPOSITIONS.pdf":
            ("CBSE", "08", "UN", 2, "Values And Dispositions"),
        "CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf":
            ("CBSE", "08", "CH", 4, "Quadrilaterals"),
        "CBSE_NCERT_G08_CH02_THE_INVISIBLE_LIVING_WORLD_BEYOND_OUR_NAKED_EYE.pdf":
            ("CBSE", "08", "CH", 2,
             "The Invisible Living World Beyond Our Naked Eye"),
    }
    for filename, (board, grade, kind, number, title) in cases.items():
        parsed = directory.parse_ncert_source(filename)
        assert parsed, filename
        assert parsed["board"] == board
        assert parsed["grade"] == grade
        assert parsed["unit_kind"] == kind
        assert parsed["number"] == number
        assert parsed["title"] == title


def test_derive_chapter_meta_ncert_with_subject_folder_probe():
    """Folder-style probes (CBSE_NCERT_G08_SocialScience) supply the subject."""
    meta = directory.derive_chapter_meta(
        "CBSE_NCERT_G08_CH02_RESHAPING_INDIAS_POLITICAL_MAP",
        "", "CBSE_NCERT_G08_SocialScience",
    )
    assert meta["board"] == "CBSE"
    assert meta["grade"] == "08"
    assert meta["subject"] == "Social Science"
    assert meta["chapter_code"].startswith("08CBSS_")

    meta_math = directory.derive_chapter_meta(
        "CBSE_NCERT_G08_CH04_QUADRILATERALS", "", "CBSE_NCERT_G08_Mathematics",
    )
    assert meta_math["subject"] == "Mathematics"
    assert meta_math["chapter_code"].startswith("08CBMA_")

    meta_sci = directory.derive_chapter_meta(
        "CBSE_NCERT_G08_CH02_THE_INVISIBLE_LIVING_WORLD_BEYOND_OUR_NAKED_EYE",
        "", "CBSE_NCERT_G08_Science",
    )
    assert meta_sci["subject"] == "Science"
    assert meta_sci["chapter_code"].startswith("08CBSC_")

    meta_eng = directory.derive_chapter_meta(
        "CBSE_NCERT_G08_UN02_VALUES_AND_DISPOSITIONS",
        "", "CBSE_NCERT_G08_English",
    )
    assert meta_eng["subject"] == "English"
    assert meta_eng["chapter_code"].startswith("08CBEN_")


def test_derive_chapter_meta_ncert_without_subject_probe():
    """Without a subject hint the NCERT pattern still gives board + grade."""
    meta = directory.derive_chapter_meta("CBSE_NCERT_G08_CH04_QUADRILATERALS", "")
    assert meta["board"] == "CBSE"
    assert meta["grade"] == "08"
    assert meta["subject"] == "General"


def test_new_subject_codes_roundtrip():
    for subject, code in (("Science", "SC"), ("Social Science", "SS"), ("English", "EN")):
        chapter_code = directory.make_chapter_code("CBSE", "08", subject, "Sample")
        assert chapter_code.startswith(f"08CB{code}_")
        assert directory.parse_code_prefix(chapter_code) == ("08", "CBSE", subject)


def test_tree_has_boards_grades_subjects(client):
    tree = client.get("/directory/tree").json()
    boards = {b["board"] for b in tree}
    assert {"CBSE", "ICSE"} <= boards
    cbse = next(b for b in tree if b["board"] == "CBSE")
    grades = {g["grade"] for g in cbse["grades"]}
    assert "10" in grades


def test_chapter_detail_drills_to_concepts(client, first_chapter):
    detail = client.get(f"/directory/chapters/{first_chapter['id']}").json()
    assert detail["topics"]
    assert detail["topics"][0]["concepts"]


def test_vocab_endpoint(client):
    v = client.get("/directory/vocab").json()
    # Standard action-verb cognitive skills (gerunds are legacy).
    assert v["cognitive_skills"] == [
        "Remember", "Understand", "Apply", "Analyse", "Evaluate", "Create",
    ]
    assert v["difficulty_levels"] == ["Less", "Moderate", "High"]
    assert set(v["question_types"]) == {"objective", "subjective", "descriptive"}


def test_stats(client):
    s = client.get("/directory/stats").json()
    assert s["chapters"] >= 3
    assert s["questions"] >= 1
