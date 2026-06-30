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
