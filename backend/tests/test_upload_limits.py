from app import config


def test_source_and_bulk_import_uploads_return_413_over_shared_cap(
    client, monkeypatch,
):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4)

    concept = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("source.txt", b"12345", "text/plain")},
    )
    assessment = client.post(
        "/build-assessments/uploads?upload_type=questions",
        files={"file": ("questions.txt", b"12345", "text/plain")},
    )
    bulk_import = client.post(
        "/data/import",
        files={
            "file": (
                "bulk.xlsx",
                b"12345",
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet",
            )
        },
    )

    assert concept.status_code == 413
    assert assessment.status_code == 413
    assert bulk_import.status_code == 413


def test_syllabus_upload_uses_one_combined_cap_without_partial_writes(
    client, tmp_path, monkeypatch,
):
    syllabus_dir = tmp_path / "syllabus"
    syllabus_dir.mkdir()
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 5)
    monkeypatch.setattr(config, "SYLLABUS_DIR", syllabus_dir)

    response = client.post(
        "/data/syllabus/upload",
        files=[
            ("files", ("first.xlsx", b"123", "application/octet-stream")),
            ("files", ("second.xlsx", b"456", "application/octet-stream")),
        ],
    )

    assert response.status_code == 413
    assert list(syllabus_dir.iterdir()) == []
