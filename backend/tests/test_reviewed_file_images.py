"""Reviewed-file pictures are uploaded to this server and carry their links.

A reviewer who opens a generated Concept file sees image URL tags where the
chapter's figures belong, and pastes the real pictures in. Before this, those
pictures lived only as base64 ``data:`` URIs inside ``question_inventory`` —
copied again into the upload history on every re-upload, hashed into the
decision key, and dropped outright for any picture the extraction did not
attach to a question. Now every picture is pinned to the content-addressed
asset store as the file is read, and the durable record carries the signed
``/source-assets`` link.

The ordering test guards a regression the Q57 streaming reader introduced:
image blocks were emitted after every sheet's cells instead of among their
own sheet's rows, which moves a figure away from the row it was pasted beside.
"""
import base64
import copy
import io

import openpyxl
import pytest
from openpyxl.drawing.image import Image as SheetImage
from PIL import Image

from app import config
from app.services import canonical_source_phase221_fallback as fallback
from app.services import reviewed_file_input as reviewed, source_asset_store
from app.services.phase3 import kernel
from tests.test_independent_reviewed_files import critic, result, setup_job


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    return tmp_path


def picture(color, size=(40, 24), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def workbook(path, *, transparent=False):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Concepts"
    sheet["A1"] = "Concept"
    sheet["A2"] = "Similar triangles"
    sheet.add_image(SheetImage(
        picture((0, 0, 0, 0), mode="RGBA") if transparent else picture((200, 30, 30))), "C2")
    second = book.create_sheet("Types")
    second["A1"] = "Prove the triangles are similar"
    second.add_image(SheetImage(picture((30, 30, 200))), "C1")
    book.save(path)
    return path


def test_every_picture_is_uploaded_and_linked_even_when_no_question_cites_it(assets):
    path = workbook(assets / "reviewed.xlsx")

    document = reviewed.read_document(path, path.name, job_id=41)

    assert len(document["images"]) == 2
    for image in document["images"]:
        assert image["url"] == image["asset_url"]
        assert image["url"].startswith(
            f"https://aegis.example/source-assets/41/{image['sha256']}.jpg?sig=")
        stored = source_asset_store.stored_asset_path(f"{image['sha256']}.jpg")
        assert stored.is_file()
        assert fallback.validate_asset_signature(
            41, f"{image['sha256']}.jpg", image["url"].rsplit("sig=", 1)[1])
    # Distinct pictures keep distinct content identities.
    assert len({image["sha256"] for image in document["images"]}) == 2
    # Not one byte of the picture is left in the durable record.
    assert "base64" not in str(document)


def test_pictures_are_read_among_their_own_sheets_rows(tmp_path):
    # No ``job_id``: block order is a property of the reader alone, and this
    # is the assertion the Q57 streaming change broke.
    path = workbook(tmp_path / "reviewed.xlsx")

    blocks = reviewed.read_document(path, path.name)["blocks"]

    assert [(b["sheet"], b["text"]) for b in blocks] == [
        ("Concepts", "Concept"),
        ("Concepts", "Similar triangles"),
        ("Concepts", "Embedded image I1"),
        ("Types", "Prove the triangles are similar"),
        ("Types", "Embedded image I2"),
    ]


def test_transparency_is_flattened_onto_white_not_black(assets):
    path = workbook(assets / "transparent.xlsx", transparent=True)

    document = reviewed.read_document(path, path.name, job_id=41)
    stored = source_asset_store.stored_asset_path(f"{document['images'][0]['sha256']}.jpg")
    with Image.open(stored) as flattened:
        assert flattened.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_a_deployment_without_a_public_origin_still_stores_the_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "CORS_ORIGINS", ["http://localhost:5173"])
    path = workbook(tmp_path / "reviewed.xlsx")

    document = reviewed.read_document(path, path.name, job_id=41)

    for image in document["images"]:
        assert image["asset_url"] == ""
        assert image["url"].startswith("data:image/")
        assert source_asset_store.stored_asset_path(f"{image['sha256']}.jpg").is_file()


def test_the_readable_payload_carries_no_form_of_the_link(assets):
    path = workbook(assets / "reviewed.xlsx")
    document = reviewed.read_document(path, path.name, job_id=41)

    rendered = reviewed._render({"document": copy.deepcopy(document)})

    for image in document["images"]:
        assert image["ref"] in rendered
        assert image["sha256"] not in rendered
        assert "source-assets" not in rendered
    assert document["images"][0]["url"].startswith("https://")


def _extraction(document):
    extracted = copy.deepcopy(result())
    extracted["concepts"][0]["source_refs"] = ["B2"]
    extracted["questions"][0].update({
        "source_refs": ["B4"], "image_refs": ["I2"],
        "question_spans": ["Prove the triangles are similar"]})
    extracted["dispositions"] = [
        {"source_ref": block["ref"], "disposition": "concept", "rationale": "Reviewed."}
        for block in document["blocks"]]
    return extracted


def test_step_two_records_every_link_and_attaches_cited_pictures(db, assets):
    job = setup_job(db)
    path = workbook(assets / "reviewed.xlsx")
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    document = job.question_inventory[reviewed.INPUTS]["post"]
    assert all(i["url"].startswith("https://") for i in document["images"])

    current = reviewed.prepare(
        db, job, lane="post", owner_sub="local:default",
        provider=lambda _: _extraction(document), critic=critic,
        store=kernel.DecisionStore(assets / "decisions"))

    # Every picture in the file is on the manifest, cited or not.
    assert [i["ref"] for i in current["reviewed_file_images"]] == ["I1", "I2"]
    assert all(i["url"].startswith("https://aegis.example/source-assets/")
               for i in current["reviewed_file_images"])
    item = current["question_task_inventory"]["items"][0]
    assert item["image_urls"] == [current["reviewed_file_images"][1]["url"]]
    assert [i["ref"] for i in item["source_context"]["reviewed_file_images"]] == ["I2"]
    # The concept cites a text-only block, so it carries no picture.
    assert current["records"][0]["_aegis_source_evidence"]["reviewed_file_images"] == []


def test_a_file_queued_before_uploads_pinned_pictures_is_published_on_use(db, assets):
    job = setup_job(db)
    path = workbook(assets / "reviewed.xlsx")
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    # Rewrite the stored document into the pre-change inline form.
    durable = copy.deepcopy(job.question_inventory)
    document = durable[reviewed.INPUTS]["post"]
    for image in document["images"]:
        raw = source_asset_store.stored_asset_path(f"{image['sha256']}.jpg").read_bytes()
        image["url"] = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        image.pop("asset_url"), image.pop("sha256")
    job.question_inventory = durable
    db.commit()

    current = reviewed.prepare(
        db, job, lane="post", owner_sub="local:default",
        provider=lambda _: _extraction(document), critic=critic,
        store=kernel.DecisionStore(assets / "decisions"))

    assert all(i["url"].startswith("https://aegis.example/source-assets/")
               for i in current["reviewed_file_images"])
    assert current["question_task_inventory"]["items"][0]["image_urls"] == [
        current["reviewed_file_images"][1]["url"]]
