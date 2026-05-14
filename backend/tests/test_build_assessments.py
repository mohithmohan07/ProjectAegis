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
