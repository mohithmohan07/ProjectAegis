from app.services import directory


def test_parse_code_prefix():
    assert directory.parse_code_prefix("10CBMA_Circles_PL") == ("10", "CBSE", "Mathematics")
    assert directory.parse_code_prefix("09ICPH_Motion") == ("09", "ICSE", "Physics")
    assert directory.parse_code_prefix("nothing here") is None


def test_make_chapter_code_roundtrips_board_and_subject():
    code = directory.make_chapter_code("CBSE", "10", "Mathematics", "Circles")
    assert code.startswith("10CBMA_")
    assert directory.parse_code_prefix(code) == ("10", "CBSE", "Mathematics")


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
    assert "Applying" in v["cognitive_skills"]
    assert v["difficulty_levels"] == ["Less", "Moderate", "High"]
    assert set(v["question_types"]) == {"objective", "subjective", "descriptive"}


def test_stats(client):
    s = client.get("/directory/stats").json()
    assert s["chapters"] >= 3
    assert s["questions"] >= 1
