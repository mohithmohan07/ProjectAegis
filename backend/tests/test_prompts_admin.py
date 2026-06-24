"""Editable prompt registry + Admin API."""
import pytest

from app.services import assessment_prompts as ap
from app.services import prompts


@pytest.fixture(autouse=True)
def _clean_overrides():
    prompts.reset_all()
    yield
    prompts.reset_all()


def test_registry_has_core_prompts():
    keys = {s.key for s in prompts.specs()}
    for key in ("assessment.base", "assessment.output", "content.katex_rules",
                "concepts.system", "identify.system", "prelearning.system"):
        assert key in keys


def test_override_applies_to_build_prompt():
    prompts.set_override("assessment.base", "CUSTOM PERSONA MARKER 4242")
    out = ap.build_prompt(question_type="objective", difficulty="Less", skill="Remember")
    assert "CUSTOM PERSONA MARKER 4242" in out
    prompts.reset("assessment.base")
    out2 = ap.build_prompt(question_type="objective", difficulty="Less", skill="Remember")
    assert "CUSTOM PERSONA MARKER 4242" not in out2


def test_render_substitutes_double_brace_tokens():
    prompts.set_override("assessment.context_footer", "BOARD={{board}} SUBJECT={{subject}}")
    rendered = prompts.render("assessment.context_footer", board="CBSE", subject="Maths")
    assert rendered == "BOARD=CBSE SUBJECT=Maths"


def test_admin_login_and_token_required(client):
    assert client.post("/admin/login", json={"password": "wrong"}).status_code == 401
    token = client.post("/admin/login", json={"password": "admin"}).json()["token"]
    assert token

    # Without a token, listing is rejected.
    assert client.get("/admin/prompts").status_code == 401
    listed = client.get("/admin/prompts", headers={"X-Admin-Token": token}).json()
    assert listed["prompts"] and "categories" in listed


def test_admin_edit_and_reset_prompt(client):
    token = client.post("/admin/login", json={"password": "admin"}).json()["token"]
    headers = {"X-Admin-Token": token}

    r = client.put("/admin/prompts/assessment.base",
                   json={"text": "EDITED VIA ADMIN 7777"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["overridden"] is True

    # The edit is live for the next generation immediately.
    assert "EDITED VIA ADMIN 7777" in ap.build_prompt(
        question_type="objective", difficulty="Less", skill="Remember")

    r = client.post("/admin/prompts/assessment.base/reset", headers=headers)
    assert r.status_code == 200
    assert r.json()["overridden"] is False
    assert "EDITED VIA ADMIN 7777" not in ap.build_prompt(
        question_type="objective", difficulty="Less", skill="Remember")


def test_admin_unknown_key_404(client):
    token = client.post("/admin/login", json={"password": "admin"}).json()["token"]
    r = client.put("/admin/prompts/does.not.exist",
                   json={"text": "x"}, headers={"X-Admin-Token": token})
    assert r.status_code == 404
