def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_stats(client):
    s = client.get("/stats").json()
    assert s["concepts"] >= 1
    assert s["questions"] >= 1
    assert "objective" in s["questions_by_sheet"]


def test_tag_suggest_matches_concept(client):
    r = client.post("/tags/suggest", json={"text": "What is a rational number expressed as p/q?"})
    assert r.status_code == 200
    data = r.json()
    assert data["concept_id"] is not None
    assert "Rational" in data["concept_path"]
    assert data["cognitive_skills"] in {"Remembering", "Understanding", "Applying"}


def test_tag_suggest_no_match(client):
    r = client.post("/tags/suggest", json={"text": "zzzz qqqq xxxx unknown gibberish"})
    assert r.json()["concept_id"] is None


def test_tag_apply_to_question(client):
    created = client.post("/questions", json={
        "question": "Explain Newton's third law of motion.",
        "sheet_kind": "subjective",
        "marks": 3,
    })
    qid = created.json()["id"]
    r = client.post(f"/tags/apply/{qid}")
    assert r.status_code == 200
    assert r.json()["concept_id"] is not None
    assert r.json()["tagging_notes"]
