import copy
import io

import openpyxl
import pytest

from app.services import reviewed_file_input as reviewed
from app.services import build_concepts_release as release, assessment_release_snapshot as snapshot
from app.services import build_concepts_release_contract as contract, model_provider
from app.services import reviewed_file_workflow_policy as workflow, model_routing_run, generation
from app.services.phase3 import kernel, prequestions
from tests.test_build_concepts_release import _job, _rendered_records, _inventory, _mined_types
from tests.test_model_routing_run import _job as source_job


def setup_job(db):
    job, chapter = _job(db)
    release.stage_release(db, job, target_chapter_id=chapter.id, records=_rendered_records(),
                          inventory=_inventory(), mined_types=_mined_types())
    release.stage_pre_release(db, job, target_chapter_id=chapter.id,
        pre_map={"rows": [{"_pre_concept_id": "OLD", "topic": "Old", "concept_title": "Old", "concept_details": "Old description"}]},
        pre_questions={"questions": {}}, inventory={})
    release.initialize_concept_review(db, job, target_chapter_id=chapter.id)
    return job


def document(tmp_path, text="Definition and uses\nWhat is DNA, and what are its uses?"):
    path = tmp_path / "my-review.txt"
    path.write_text(text)
    return path


def result(question=True):
    return {"pre_scope_verdict": "retained", "concepts": [{"topic": "Reorganized", "concept_title": "New reviewed concept", "parent_concept": "",
             "concept_details": "Description: Definition and uses.<br>Achieving Mastery: Explain definition and uses.",
             "keywords": "definition", "source_refs": ["B1"]}],
        "questions": [{"concept_index": 0, "source_refs": ["B1"],
            "question_spans": ["What is DNA, and what are its uses?"], "context_spans": [],
            "answer_spans": [], "pre_answer": "A molecule carrying genetic information.", "options": [], "tables": [], "image_refs": [],
            "type_title": "Definition and uses", "type_definition": "Define and explain uses.",
            "case_title": "", "case_definition": "", "placement_section": "types"}] if question else [],
        "dispositions": [{"source_ref": "B1", "disposition": "concept", "rationale": "Reviewed concept and complete dependent question."}],
        "empty_reason": "No questions were supplied." if not question else ""}


def critic(_):
    return {"verdict": "verified", "confidence": 0.99, "issues": []}


def test_upload_accepts_new_layout_and_omissions_without_model_or_old_row_matching(db, tmp_path, monkeypatch):
    job = setup_job(db)
    original = copy.deepcopy(release.release_payload(job))
    monkeypatch.setattr(generation, "_openai_json", lambda *a, **k: pytest.fail("Upload must not spend on extraction"))
    path = document(tmp_path)
    first = reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    assert first["round_recorded"] and first["extraction_status"] == "pending_master_generation"
    assert release.release_payload(job) == original
    assert not reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")["round_recorded"]


def test_step_two_replaces_whole_input_and_snapshot_without_old_ids_or_envelope(db, tmp_path):
    job = setup_job(db)
    original_pre = copy.deepcopy(release.release_payload(job, lane="pre"))
    original_post = copy.deepcopy(release.release_payload(job))
    path = document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    calls = []
    def author(payload):
        calls.append(copy.deepcopy(payload))
        assert model_provider.bound_profile() == model_provider.new_profile()
        assert "original_questions" not in payload and "original_records" not in payload
        return result()
    current = reviewed.prepare(db, job, lane="post", owner_sub="local:default", provider=author, critic=critic, store=kernel.DecisionStore(tmp_path / "decisions"))
    assert len(current["records"]) == 1
    assert current["records"][0]["concept_title"] == "New reviewed concept"
    assert current["source_document_hash"] != original_post["source_document_hash"]
    assert len(current["question_task_inventory"]["items"]) == 1
    atom = current["question_task_inventory"]["items"][0]
    assert atom["raw_task"] == "What is DNA, and what are its uses?"
    assert atom["learner_context"] == ""
    with db.no_autoflush:
        bridge = snapshot.build(db, job, current)
    assert bridge["question_task_inventory"]["items"] == [atom]
    assert bridge["concepts"][0]["concept_title"].startswith("New reviewed concept")
    assert contract._lane_master_eligibility(db, job.id, "post", owner_sub="local:default") == (True, "")
    assert release.release_payload(job, lane="pre") == original_pre
    assert reviewed.prepare(db, job, lane="post", provider=lambda _: pytest.fail("Identical input reparsed")) == current
    assert len(calls) == 1


def test_pre_questions_depend_only_on_reviewed_file_without_source_envelope(db, tmp_path, monkeypatch):
    job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="pre", path=path, filename=path.name, owner_sub="local:default")
    current = reviewed.prepare(db, job, lane="pre", owner_sub="local:default", provider=lambda _: result(False), critic=critic, store=kernel.DecisionStore())
    env = reviewed.pre_envelope(db, current)
    assert env["inventory"] == {"items": []} and env["mined_types"] == {}
    assert "instruction_slots" not in env["metadata"]
    assert "Old description" not in str(env)
    seen = []
    def generate(env, scope, **kwargs):
        seen.append(scope)
        return {"plans": {"PRC-0001": {"total": 1}}, "questions": {"PRC-0001": [
            {"pre_question_id": "PRE-NEW-1", "pre_concept_id": "PRC-0001", "question_text": "Explain the definition.", "answer": "An explanation."}]}}
    monkeypatch.setattr(prequestions, "build", generate)
    reviewed.ensure_pre_questions(db, job, owner_sub="local:default")
    assert reviewed.ensure_pre_questions(db, job, owner_sub="local:default") is None
    assert len(seen) == 1
    assert release.release_payload(job, lane="pre")["records"][0]["concept_title"] == "New reviewed concept"


def test_invalid_extraction_preserves_queued_file_and_previous_release(db, tmp_path):
    job = setup_job(db)
    previous = copy.deepcopy(release.release_payload(job))
    path = document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    bad = result()
    bad["questions"][0]["question_spans"] = ["An invented question"]
    with pytest.raises(kernel.ContractError):
        reviewed.prepare(db, job, lane="post", provider=lambda _: bad, critic=critic,
                         fixer=lambda _: bad, store=kernel.DecisionStore())
    assert release.release_payload(job) == previous
    assert job.question_inventory[reviewed.INPUTS]["post"]["filename"] == path.name


def test_arbitrary_excel_headers_and_complete_grid_are_read(tmp_path):
    path = tmp_path / "rearranged.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "My arrangement"
    sheet.append(["Notes", "Question", "Frequency"])
    sheet.append(["A", "Use the table", 0])
    sheet.append(["B", None, 5])
    book.save(path)
    doc = reviewed.read_document(path, path.name)
    assert doc["blocks"][1]["cells"][-1] == {"cell": "C2", "text": "0"}
    assert doc["blocks"][2]["cells"][-1] == {"cell": "C3", "text": "5"}
    assert doc["blocks"][0]["sheet"] == "My arrangement"


def test_fresh_workflow_is_frozen_and_historical_profile_unchanged(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    fresh = source_job()
    with model_routing_run.bind_job(fresh):
        assert workflow.active(generation._metadata())
    saved = model_routing_run._record_path(fresh).read_bytes()
    with model_routing_run.bind_job(fresh):
        assert workflow.run_fields() == {workflow.KEY: workflow.VERSION}
    assert model_routing_run._record_path(fresh).read_bytes() == saved
    old = source_job(upload_storage_key="old", mmd_text="existing source")
    with model_routing_run.bind_job(old):
        assert workflow.run_fields() == {}


def test_real_pre_planner_and_author_use_only_accepted_file_scope(db, tmp_path, monkeypatch):
    from tests.test_phase3_prequestions import _provider
    job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="pre", path=path, filename=path.name, owner_sub="local:default")
    reviewed.prepare(db, job, lane="pre", provider=lambda _: result(False), critic=critic, store=kernel.DecisionStore())
    real_build = prequestions.build
    author = _provider(plans={"PRC-0001": {"total": 1, "split": [{"tier": "Basic", "count": 1}],
        "rationale": "One diagnostic covers this accepted definition."}})
    seen = []
    def provider(payload):
        seen.append(copy.deepcopy(payload))
        assert "Old description" not in str(payload)
        assert "What is DNA" not in str(payload)
        response = author(payload)
        if payload["stage"] == "prequestions.author":
            for question in response["questions"]:
                question["tier"] = "Basic"
        return response
    def build(env, scope, **kw):
        outcome = real_build(env, scope, provider=provider, critic=critic, store=kernel.DecisionStore())
        assert not outcome.get("blocked"), outcome
        return outcome
    monkeypatch.setattr(prequestions, "build", build)
    reviewed.ensure_pre_questions(db, job, owner_sub="local:default")
    current = release.release_payload(job, lane="pre")
    assert contract._reviewed_pre_missing_question_ids(current) == []
    assert len(current["generated_questions"]) == 1
    assert {p["stage"] for p in seen} == {"prequestions.plan", "prequestions.author"}


def test_reviewed_master_binds_file_snapshot_without_original_checkpoint(db, tmp_path, monkeypatch):
    from app.services import assessment_release_run as run
    job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    reviewed.prepare(db, job, lane="post", provider=lambda _: result(), critic=critic, store=kernel.DecisionStore())
    current = release.release_payload(job)
    class ReachedReviewedContext(Exception):
        pass
    def context(job_id, *, envelope_sha256=None, decision_store=None):
        assert job_id == job.id
        assert envelope_sha256 == snapshot.source_release_sha256(current)
        assert decision_store is not None
        raise ReachedReviewedContext
    monkeypatch.setattr(run, "_decision_context", context)
    with pytest.raises(ReachedReviewedContext):
        run.run_release_for_job(db, job.id, owner_sub="local:default")


def test_workflow_marker_roundtrips_before_source_generation_finishes(db):
    from app.services import checkpoints
    from tests.test_concept_checkpoint_bundles import _job as checkpoint_job
    job = checkpoint_job(db)
    model_routing_run.save_profile_for_job(job, model_provider.new_profile(), workflow_version=workflow.VERSION)
    _, raw = checkpoints.export_bundle(db, job.id)
    restored = checkpoints.import_bundle(db, raw)
    assert restored.question_inventory == job.question_inventory
    with model_routing_run.bind_job(restored):
        assert workflow.active(generation._metadata())


def test_document_readers_preserve_table_cells_and_pdf_evidence(tmp_path):
    from zipfile import ZipFile
    import fitz
    path = tmp_path / "review.docx"
    # A minimal valid Word package uses only the runtime's standard library.
    with ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        package.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        package.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Use this frequency table.</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Frequency</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>0</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>5</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    parsed = reviewed.read_document(path, path.name)
    assert parsed["blocks"][-1]["cells"] == ["0", "5"]
    pdf_path = tmp_path / "review.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50, 50), "Use this frequency table.")
        pdf.save(pdf_path)
    parsed = reviewed.read_document(pdf_path, pdf_path.name)
    assert parsed["blocks"][0]["page"] == 1
    assert "frequency table" in parsed["blocks"][0]["text"]
    assert parsed["blocks"][0]["image_refs"] == [parsed["images"][0]["ref"]]
    rendered = reviewed._render({"document": parsed})
    assert "base64" not in rendered


@pytest.mark.parametrize("status", ["pending_review", "master_building"])
def test_upload_route_accepts_reorganized_text_at_review_or_after_interruption(db, tmp_path, status):
    import asyncio
    from fastapi import UploadFile
    from app.services import build_concepts_release_api_contract as api, auth
    job = setup_job(db)
    release.update_concept_review_state(db, job, status=status)
    previous = release.release_payload(job)
    response = asyncio.run(api._concept_review_upload_endpoint(job.id, lane="post",
        file=UploadFile(io.BytesIO(b"My replacement concept and question"), filename="new-layout.txt"),
        db=db, user=auth.LOCAL_PRINCIPAL))
    db.refresh(job)
    assert response["extraction_status"] == "pending_master_generation"
    assert job.question_inventory[reviewed.INPUTS]["post"]["blocks"][0]["text"] == "My replacement concept and question"
    assert release.release_payload(job) == previous


def test_reviewed_upload_opts_unchanged_sibling_into_file_only_handoff(db, tmp_path):
    job = setup_job(db)
    path = document(tmp_path)
    reviewed.queue(db, job, lane="post", path=path, filename=path.name, owner_sub="local:default")
    seen = []
    def author(payload):
        seen.append(payload)
        assert payload["lane"] == "pre"
        assert payload["document"]["filename"] == "reviewed-pre-concepts.xlsx"
        assert "What is DNA" not in str(payload["document"])
        assert "Old description" in str(payload["document"])
        parsed = result(False)
        parsed["dispositions"] = [{"source_ref": block["ref"], "disposition": "concept",
            "rationale": "The unchanged Pre workbook is the reviewed input."}
            for block in payload["document"]["blocks"]]
        return parsed
    current = reviewed.prepare(db, job, lane="pre", provider=author, critic=critic, store=kernel.DecisionStore())
    assert reviewed.active(current)
    assert len(seen) == 1
    assert current["question_task_inventory"]["items"] == []
