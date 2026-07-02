import io

from tests.conftest import convert_concept_upload, stream_result


def test_post_learning_creates_concepts(client, first_chapter):
    files = {"file": ("notes.txt", io.BytesIO(
        b"## Trigonometry Basics\nSine ratio: opposite over hypotenuse\n"
        b"Cosine ratio: adjacent over hypotenuse"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    assert job["learning_kind"] == "post"
    assert job["status"] == "uploaded"  # upload stages only

    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] >= 2
    assert result["rows_appended"] >= 2


def test_post_learning_groups_concepts_under_one_topic(client, db, first_chapter):
    """Concepts sharing a topic name must share ONE Topic row (no duplicates)."""
    files = {"file": ("grouping.txt", io.BytesIO(
        b"## Grouping Topic 9912\nGrouping concept alpha 9912\n"
        b"Grouping concept beta 9912\nGrouping concept gamma 9912"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] == 4

    import app.models as models
    topics = (
        db.query(models.Topic)
        .filter_by(chapter_id=first_chapter["id"], topic_title="Grouping Topic 9912")
        .all()
    )
    assert len(topics) == 1
    assert len(topics[0].concepts) == 4
    assert sum(c.concept_title.startswith("Culmination -") for c in topics[0].concepts) == 1


def test_inventory_csv_download(client, db, first_chapter):
    """The stored Question / Task Inventory downloads as an audit CSV."""
    files = {"file": ("inv.txt", io.BytesIO(
        b"## Inventory Topic 7731\nInventory concept alpha 7731"
    ), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    convert_concept_upload(client, job["id"])
    stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))

    # Dry mode produces no inventory, so simulate what a live run stores.
    import app.models as models
    job_row = db.get(models.UploadJob, job["id"])
    job_row.question_inventory = {
        "items": [
            {"qid": "QINV-0001", "order_index": 1, "source_kind": "exercise",
             "source_label": "Exercise 1.1 Q1", "topic_hint": "T",
             "raw_task": "Simplify, p^9 ÷ p^3.", "normalized_task": "Divide powers.",
             "requires_visual": False, "requires_context": False,
             "content_objects": {"variables": ["p"]}},
            {"qid": "QINV-0002", "order_index": 2, "source_kind": "mcq",
             "source_label": "Q2", "topic_hint": "T",
             "raw_task": "Pick the value of 2^3.", "normalized_task": "Evaluate a power.",
             "requires_visual": False, "requires_context": False,
             "content_objects": {}},
        ],
        "stats": {"total_inventory_items": 2},
        "mined_types": [
            {"type_id": "TYPE-0001", "type_title": "Dividing Powers with the Same Base",
             "source_question_ids": ["QINV-0001"], "case_prompts": []},
        ],
    }
    db.commit()

    resp = client.get(f"/build-concepts/uploads/{job['id']}/inventory.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    import csv
    import io as _io
    rows = list(csv.DictReader(_io.StringIO(resp.text)))
    assert len(rows) == 2
    by_qid = {r["qid"]: r for r in rows}
    assert by_qid["QINV-0001"]["raw_task"] == "Simplify, p^9 ÷ p^3."
    assert by_qid["QINV-0001"]["classified"] == "yes"
    assert by_qid["QINV-0001"]["mined_type_ids"] == "TYPE-0001"
    assert "Dividing Powers" in by_qid["QINV-0001"]["mined_type_titles"]
    # Unclassified items are visible at a glance in the audit CSV.
    assert by_qid["QINV-0002"]["classified"] == "no"
    assert by_qid["QINV-0002"]["mined_type_ids"] == ""


def test_inventory_csv_missing_returns_404(client):
    files = {"file": ("noinv.txt", io.BytesIO(b"## X\nY"), "text/plain")}
    job = client.post("/build-concepts/post-learning/uploads", files=files).json()
    resp = client.get(f"/build-concepts/uploads/{job['id']}/inventory.csv")
    assert resp.status_code == 404


def test_pre_learning_from_upload(client, first_chapter):
    files = {"file": ("doc.txt", io.BytesIO(
        b"## Foundations\nNumber line basics\nInteger operations"
    ), "text/plain")}
    job = client.post("/build-concepts/pre-learning/uploads", files=files).json()
    assert job["learning_kind"] == "pre"
    convert_concept_upload(client, job["id"])
    result = stream_result(client.post(
        f"/build-concepts/pre-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]}))
    assert result["concepts_created"] >= 2


def test_pre_learning_from_existing_post_learning(client, first_chapter):
    result = stream_result(client.post("/build-concepts/pre-learning/from-existing", json={
        "chapter_ids": [first_chapter["id"]],
    }))
    assert result["chapters"] == 1
    assert result["concepts_created"] >= 1
    assert str(first_chapter["id"]) in {str(k) for k in result["per_chapter"]}
