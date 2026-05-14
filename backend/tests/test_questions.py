def test_questions_seeded(client):
    r = client.get("/questions")
    assert r.status_code == 200
    assert len(r.json()) >= 4


def test_filter_by_sheet_kind(client):
    for kind in ("objective", "subjective", "descriptive"):
        items = client.get("/questions", params={"sheet_kind": kind}).json()
        assert all(q["sheet_kind"] == kind for q in items)


def test_create_and_update_question(client):
    created = client.post("/questions", json={
        "question": "Define a rational number.",
        "sheet_kind": "subjective",
        "question_category": "Very Short Answer",
        "marks": 1,
        "answers": [{"answer_type": "Phrases", "answer_content": "p/q form", "answer_weightage": 1}],
    })
    assert created.status_code == 200
    qid = created.json()["id"]

    patched = client.patch(f"/questions/{qid}", json={
        "question": "Define a rational number with an example.",
        "level_of_difficulty": "Less",
    })
    assert patched.status_code == 200
    assert patched.json()["level_of_difficulty"] == "Less"

    assert client.delete(f"/questions/{qid}").status_code == 204


def test_export_bulk_upload(client):
    r = client.get("/export/bulk-upload")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
    assert len(r.content) > 0
