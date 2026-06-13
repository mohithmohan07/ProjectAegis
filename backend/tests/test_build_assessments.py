import io


def test_concept_mapping_stackable_batches_generate(client, first_concept):
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()

    # Two stacked blueprint batches in one session.
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Remembering", "Applying"],
        "difficulty_levels": ["Less"],
        "categories": ["Multiple Choice Question"],
        "question_type": "objective",
        "num_questions": 2,
    })
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Analysing"],
        "difficulty_levels": ["High"],
        "categories": ["Long Answer"],
        "question_type": "descriptive",
        "num_questions": 1,
    })

    result = client.post(f"/build-assessments/sessions/{session['id']}/generate").json()
    # batch1: 2 skills x 1 diff x 1 cat x 2 = 4 ; batch2: 1x1x1x1 = 1 -> 5
    assert result["created"] == 5
    assert result["pipeline"]["appended"]["objective"] == 4
    assert result["pipeline"]["appended"]["descriptive"] == 1


def test_chapter_scope_fans_out_to_concepts(client, first_chapter):
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "chapter", "scope_ids": [first_chapter["id"]],
    }).json()
    client.post(f"/build-assessments/sessions/{session['id']}/batches", json={
        "cognitive_skills": ["Understanding"],
        "difficulty_levels": ["Moderate"],
        "categories": ["Multiple Choice Question"],
        "question_type": "objective",
        "num_questions": 1,
    })
    result = client.post(f"/build-assessments/sessions/{session['id']}/generate").json()
    # One question per concept in the chapter.
    detail = client.get(f"/directory/chapters/{first_chapter['id']}").json()
    n_concepts = sum(len(t["concepts"]) for t in detail["topics"])
    assert result["created"] == n_concepts


def test_generate_without_batches_is_rejected(client, first_concept):
    session = client.post("/build-assessments/sessions", json={
        "scope_type": "concept", "scope_ids": [first_concept["id"]],
    }).json()
    r = client.post(f"/build-assessments/sessions/{session['id']}/generate")
    assert r.status_code == 400


def test_upload_path_extract_and_deposit(client, first_chapter):
    files = {"file": ("quiz.txt", io.BytesIO(
        b"What is a tangent line?\n\nDefine a chord.\n\nState the tangent-radius theorem."
    ), "text/plain")}
    job = client.post("/build-assessments/uploads?upload_type=questions", files=files).json()
    assert job["status"] == "converted"
    assert job["mmd_text"].startswith("#")

    client.post(f"/build-assessments/uploads/{job['id']}/deposit", json={
        "scope_type": "chapter", "scope_ids": [first_chapter["id"]],
    })
    result = client.post(f"/build-assessments/uploads/{job['id']}/generate", json={
        "question_type": "objective",
    }).json()
    assert result["created"] == 3


def test_upload_textbook_mode(client):
    files = {"file": ("book.txt", io.BytesIO(b"Chapter on circles."), "text/plain")}
    job = client.post("/build-assessments/uploads?upload_type=textbook", files=files).json()
    updated = client.post(
        f"/build-assessments/uploads/{job['id']}/textbook-mode", json={"mode": "extract"}
    ).json()
    assert updated["textbook_mode"] == "extract"


def test_invalid_upload_type_rejected(client):
    files = {"file": ("x.txt", io.BytesIO(b"x"), "text/plain")}
    r = client.post("/build-assessments/uploads?upload_type=bogus", files=files)
    assert r.status_code == 400


def test_upload_auto_is_default_and_generates(client, first_chapter):
    """No question_type -> 'auto'; the upload is absorbed without forcing a type."""
    files = {"file": ("mix.txt", io.BytesIO(
        b"What is a tangent?\n\nExplain why a tangent is perpendicular to the radius."
        b"\n\nDescribe and prove the two-tangent theorem."
    ), "text/plain")}
    job = client.post("/build-assessments/uploads?upload_type=questions", files=files).json()
    client.post(f"/build-assessments/uploads/{job['id']}/deposit", json={
        "scope_type": "chapter", "scope_ids": [first_chapter["id"]],
    })
    # Empty body -> question_type defaults to "auto".
    result = client.post(
        f"/build-assessments/uploads/{job['id']}/generate", json={}).json()
    assert result["created"] == 3
    assert "question_ids" in result


def test_upload_invalid_question_type_rejected(client, first_chapter):
    files = {"file": ("q.txt", io.BytesIO(b"What is a tangent?"), "text/plain")}
    job = client.post("/build-assessments/uploads?upload_type=questions", files=files).json()
    client.post(f"/build-assessments/uploads/{job['id']}/deposit", json={
        "scope_type": "chapter", "scope_ids": [first_chapter["id"]],
    })
    r = client.post(f"/build-assessments/uploads/{job['id']}/generate",
                    json={"question_type": "bogus"})
    assert r.status_code == 400


def test_identify_auto_dry_falls_back_to_objective():
    from app.services import generation as g

    recs = g.identify_questions_from_mmd(
        "# t\n\nQ1?\n\nQ2?", upload_type="questions",
        question_type="auto", live=False)
    assert recs and all(r["sheet_kind"] == "objective" for r in recs)


def test_normalize_sheet_kind():
    from app.services import generation as g

    assert g._normalize_sheet_kind("Descriptive") == "descriptive"
    assert g._normalize_sheet_kind("SUBJECTIVE") == "subjective"
    assert g._normalize_sheet_kind("mcq") == "objective"
    assert g._normalize_sheet_kind("long answer") == "descriptive"
    assert g._normalize_sheet_kind("") == "objective"
    assert g._normalize_sheet_kind("weird", default="descriptive") == "descriptive"
