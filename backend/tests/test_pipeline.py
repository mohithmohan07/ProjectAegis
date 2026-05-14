import pytest


def test_list_stages(client):
    stages = client.get("/pipeline/stages").json()
    keys = {s["key"] for s in stages}
    assert {"extract_pdfs", "extract_mmds", "mmd_to_concepts", "bulk_upload", "assessment_tagging"} <= keys
    # Ordered ascending.
    orders = [s["order"] for s in stages]
    assert orders == sorted(orders)


@pytest.mark.parametrize("stage", [
    "extract_pdfs", "extract_mmds", "mmd_to_concepts",
    "excel_to_prelearning", "concept_mapping_to_prelearning",
    "bulk_upload", "assessment_tagging",
])
def test_each_stage_runs_in_dry_mode(client, stage):
    r = client.post(f"/pipeline/stages/{stage}/run", json={"mode": "dry", "inputs": {}})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "succeeded", run["error"]
    assert run["progress"] == 1.0


def test_mmd_to_concepts_loads_concepts(client):
    before = len(client.get("/concepts").json())
    r = client.post("/pipeline/stages/mmd_to_concepts/run", json={"mode": "dry", "inputs": {}})
    assert r.json()["status"] == "succeeded"
    after = len(client.get("/concepts").json())
    assert after > before


def test_bulk_upload_loads_questions(client):
    before = len(client.get("/questions").json())
    r = client.post("/pipeline/stages/bulk_upload/run", json={"mode": "dry", "inputs": {}})
    assert r.json()["status"] == "succeeded"
    after = len(client.get("/questions").json())
    assert after > before


def test_live_mode_blocked_without_keys(client):
    r = client.post("/pipeline/stages/mmd_to_concepts/run", json={"mode": "live", "inputs": {}})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert "OPENAI_API_KEY" in r.json()["error"]


def test_unknown_stage_404(client):
    assert client.post("/pipeline/stages/nope/run", json={"mode": "dry"}).status_code == 404


def test_runs_listed(client):
    client.post("/pipeline/stages/extract_pdfs/run", json={"mode": "dry"})
    runs = client.get("/pipeline/runs").json()
    assert len(runs) >= 1
