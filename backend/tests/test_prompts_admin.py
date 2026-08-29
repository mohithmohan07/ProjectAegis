"""Editable prompt registry + Admin API."""
import json

import pytest

from app.services import assessment_prompts as ap
from app.services import prompts


@pytest.fixture(autouse=True)
def _clean_overrides():
    prompts.reset_all()
    prompts.retired_overrides_path().unlink(missing_ok=True)
    yield
    prompts.reset_all()
    prompts.retired_overrides_path().unlink(missing_ok=True)


def test_registry_has_core_prompts():
    keys = {s.key for s in prompts.specs()}
    for key in ("assessment.base", "assessment.output", "content.katex_rules.v2",
                "concepts.system", "identify.system",
                "concepts.skeleton.system"):
        assert key in keys
    # Step 7 retired the pre-learning derivation prompts with their lane.
    assert not keys & {"prelearning.system", "prelearning.auditor"}


def test_override_applies_to_build_prompt():
    prompts.set_override("assessment.base", "CUSTOM PERSONA MARKER 4242")
    out = ap.build_prompt(
        question_type="objective", difficulty="Less", skill="Remember",
        marks=1, category="Multiple Choice Question")
    assert "CUSTOM PERSONA MARKER 4242" in out
    prompts.reset("assessment.base")
    out2 = ap.build_prompt(
        question_type="objective", difficulty="Less", skill="Remember",
        marks=1, category="Multiple Choice Question")
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
        question_type="objective", difficulty="Less", skill="Remember",
        marks=1, category="Multiple Choice Question")

    r = client.post("/admin/prompts/assessment.base/reset", headers=headers)
    assert r.status_code == 200
    assert r.json()["overridden"] is False
    assert "EDITED VIA ADMIN 7777" not in ap.build_prompt(
        question_type="objective", difficulty="Less", skill="Remember",
        marks=1, category="Multiple Choice Question")


def test_admin_unknown_key_404(client):
    token = client.post("/admin/login", json={"password": "admin"}).json()["token"]
    r = client.put("/admin/prompts/does.not.exist",
                   json={"text": "x"}, headers={"X-Admin-Token": token})
    assert r.status_code == 404


def test_a_retired_prompt_override_is_pruned_instead_of_stranded():
    """A saved override for a retired key must not become unreachable.

    Deleting a registration would otherwise strand its override in
    ``DATA_DIR/prompt_overrides.json`` forever: absent from ``GET
    /admin/prompts``, rejected by ``PUT`` (``set_override`` raises
    ``KeyError``), and un-resettable (the reset route's ``describe``
    404s). Naming the key in ``RETIRED_PROMPT_KEYS`` makes the next read
    drop it, from memory and from disk.

    The prune must not DESTROY the operator's authored text to achieve that:
    it is copied to ``prompt_overrides.retired.json`` first, so the removal is
    recoverable and recorded rather than silent.
    """
    assert {"prelearning.system", "prelearning.auditor",
            "concepts.misconceptions.system"} <= prompts.RETIRED_PROMPT_KEYS

    # A production data dir written by the previous release.
    prompts._save_overrides({
        "prelearning.system": "operator text for a retired prompt",
        "assessment.base": "operator text for a live prompt",
    })
    prompts._invalidate_cache()

    assert not prompts.is_overridden("prelearning.system")
    assert prompts.is_overridden("assessment.base")
    with pytest.raises(KeyError):
        prompts.get_text("prelearning.system")

    # The prune is persisted, so it survives the next process too.
    prompts._invalidate_cache()
    assert not prompts.is_overridden("prelearning.system")
    assert prompts.get_text("assessment.base") == (
        "operator text for a live prompt")

    # ... and the authored text was archived, not destroyed.
    archived = json.loads(
        prompts.retired_overrides_path().read_text(encoding="utf-8"))
    assert archived["prelearning.system"] == (
        "operator text for a retired prompt")
    assert "assessment.base" not in archived


def test_a_second_retired_override_does_not_overwrite_the_first_archive():
    """Archiving is additive: one retirement must not erase an earlier one."""
    prompts._save_overrides({"prelearning.system": "first authored text"})
    prompts._invalidate_cache()
    prompts._load_overrides()

    prompts._save_overrides({"prelearning.auditor": "second authored text"})
    prompts._invalidate_cache()
    prompts._load_overrides()

    archived = json.loads(
        prompts.retired_overrides_path().read_text(encoding="utf-8"))
    assert archived["prelearning.system"] == "first authored text"
    assert archived["prelearning.auditor"] == "second authored text"
