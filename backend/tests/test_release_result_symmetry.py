"""S3 — one release result shape, four manifest blocks, one publish gate.

Three defects, measured on the tree before this slice and pinned here.

1. ``build_concepts_release.release_result`` was TWO branches. The Pre
   branch returned ``lane``, ``release_state``, ``structural_defects``,
   ``generated_question_count`` and ``release_bulk_import_url``; the Post
   branch returned none of the five. The Post dict IS the terminal
   ``{"type": "result"}`` event of a Build Concepts run, so the Post
   lane's release state was computed by ``release_state`` and surfaced by
   no endpoint at all.

2. The download manifest has FOUR blocks, not two — a Post and a Pre
   block in each of two modules, one of which monkeypatches the other
   (``build_concepts_release_manifest.install()``). None of the eight
   entries they emitted was an OUTPUT: they were the review workbook, the
   diagnostics zip, the release JSON and the upload action, which are
   evidence. The reviewer never saw the four outputs enumerated anywhere.

3. ``normalize_lane`` answers ``"post"`` for both ``""`` and a missing
   value, and the publish route took the same defaulted lane as the
   downloads — so a publish request that simply omitted the lane
   performed an authenticated write against the Post lane. For a
   DOWNLOAD that default is right and stays; for a PUBLICATION it is a
   write nobody authorised, which is why both halves are pinned here.

The manifest tests are parametrised over the four blocks BY FUNCTION
REFERENCE, and a separate discovery test re-derives the set of blocks
from the two modules' source, so a FIFTH block added later fails until it
is listed here.

Owner numbering throughout (ruling OD4 / register entry D9-Q22):
01 Pre Concept, 02 Pre Master, 03 Post Concept, 04 Post Master.
"""
from __future__ import annotations

import ast
import inspect
import json
import re

import pytest

from app.api import build_concepts as build_concepts_api
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_manifest as release_manifest

from tests.test_assessment_release_run import _chapter_with_concepts
from tests.test_pre_release_lane_wiring import _both_lanes_job


# --------------------------------------------------------------------------- #
# 1. ``release_result`` — one shape, filled per lane
# --------------------------------------------------------------------------- #

_ASYMMETRIC_KEYS = {
    "lane",
    "release_state",
    "structural_defects",
    "generated_question_count",
    "release_bulk_import_url",
}


def test_both_lanes_report_the_same_release_result_shape(db):
    """The exact key SETS, not a subset check.

    A subset check passes on the defect: the Post branch was a strict
    subset of the Pre one. Equality is what makes the five missing keys
    a failure.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)

    post = release.release_result(job)
    pre = release.release_result(job, lane=release.LANE_PRE)

    assert set(post) == set(pre)
    assert _ASYMMETRIC_KEYS <= set(post)
    assert post["lane"] == release.LANE_POST
    assert pre["lane"] == release.LANE_PRE


def test_the_post_release_result_carries_its_release_state(db):
    """The Post lane's state reached no endpoint before this slice.

    ``release_state`` and ``structural_defects`` were computed for the
    Post payload and returned only for the Pre one, so a reviewer could
    be shown a finished run whose publication the structural gate would
    refuse with nothing in the result saying so.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)

    post = release.release_result(job)
    payload = release.release_payload(job)

    assert post["release_state"] == release.release_state(payload)
    assert post["structural_defects"] == release.structural_defects(payload)
    assert post["generated_question_count"] == len(
        payload.get("generated_questions") or []
    )
    assert post["release_bulk_import_url"] == (
        f"/build-concepts/uploads/{job.id}/release-bulk-import.xlsx"
    )


def test_the_post_download_urls_in_the_result_carry_no_lane_suffix(db):
    """No already-published DOWNLOAD url moves.

    Collapsing the branches must not renegotiate the Post lane's four
    download URLs: they are recorded in run logs and reviewer bookmarks,
    and a silently broken download URL would be a worse defect than the
    asymmetry being fixed. The PUBLISH url is the deliberate exception
    and has its own pin below.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    post = release.release_result(job)
    pre = release.release_result(job, lane=release.LANE_PRE)

    base = f"/build-concepts/uploads/{job.id}"
    assert post["release_workbook_url"] == f"{base}/release.xlsx"
    assert post["release_bulk_import_url"] == (
        f"{base}/release-bulk-import.xlsx"
    )
    assert post["diagnostics_url"] == f"{base}/diagnostics.zip"
    assert post["release_payload_url"] == f"{base}/release.json"

    assert pre["release_workbook_url"] == f"{base}/release.xlsx?lane=pre"
    assert pre["release_bulk_import_url"] == (
        f"{base}/release-bulk-import.xlsx?lane=pre"
    )
    assert pre["diagnostics_url"] == f"{base}/diagnostics.zip?lane=pre"
    assert pre["release_payload_url"] == f"{base}/release.json?lane=pre"


# --------------------------------------------------------------------------- #
# 2. THE FOUR MANIFEST BLOCKS
#
# Listed by FUNCTION REFERENCE, never by name lookup on the module: the
# lazy module rebinds ``release_files.release_artifact_entries`` to its own
# twin for the rest of the process (``install()``, which
# ``app.main.bootstrap()`` runs at startup and other test modules run
# mid-session, and which is never restored), so reaching the eager block by
# attribute lookup would hand back the lazy one and this table would pin
# the same implementation twice.
# --------------------------------------------------------------------------- #

def _lazy_post(job):
    return release_manifest.release_artifact_entries(job)


def _lazy_pre(job):
    return release_manifest._pre_entries(
        job, release_files._safe_filename(job.filename, "concepts")
    )


def _eager_post(job):
    return release_files.eager_release_artifact_entries(job)


def _eager_pre(job):
    return release_files._pre_release_entries(job, sizes=True)


# (block function, adapter, the OUTPUT kinds that block must emit)
_MANIFEST_BLOCKS = (
    pytest.param(
        release_manifest.release_artifact_entries,
        _lazy_post,
        ("pre_release_bulk_import", "pre_release_master",
         "release_bulk_import", "release_master"),
        ("database_upload", "pre_database_upload"),
        id="lazy.release_artifact_entries",
    ),
    pytest.param(
        release_manifest._pre_entries,
        _lazy_pre,
        ("pre_release_bulk_import", "pre_release_master"),
        ("pre_database_upload",),
        id="lazy._pre_entries",
    ),
    pytest.param(
        release_files.eager_release_artifact_entries,
        _eager_post,
        ("pre_release_bulk_import", "pre_release_master",
         "release_bulk_import", "release_master"),
        ("database_upload", "pre_database_upload"),
        id="eager.release_artifact_entries",
    ),
    pytest.param(
        release_files._pre_release_entries,
        _eager_pre,
        ("pre_release_bulk_import", "pre_release_master"),
        ("pre_database_upload",),
        id="eager._pre_release_entries",
    ),
)


def _emits_a_manifest_entry(node: ast.FunctionDef) -> bool:
    """True when this function's own body writes a ``"kind":`` entry."""

    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            for key in child.keys:
                if isinstance(key, ast.Constant) and key.value == "kind":
                    return True
    return False


def test_the_four_manifest_blocks_are_the_only_manifest_blocks():
    """A FIFTH block added later fails here until it is listed above.

    The block set is re-derived from the two modules' source rather than
    trusted from the table, because the whole failure mode this slice
    exists to close is an edit that reaches one block and silently misses
    another.
    """

    # ``ast.walk``, not ``tree.body``: a fifth block nested inside a class
    # or inside another function is exactly the block a module-level-only
    # scan would miss, and missing a block is the one thing this test
    # exists to catch. ``AsyncFunctionDef`` for the same reason.
    # [measured] both scans find the same four today, so widening the net
    # pins strictly more without moving the current answer.
    discovered: set[tuple[str, str]] = set()
    for module in (release_manifest, release_files):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and _emits_a_manifest_entry(node):
                discovered.add((module.__name__, node.name))

    listed = {
        (param.values[0].__module__, param.values[0].__name__)
        for param in _MANIFEST_BLOCKS
    }
    assert discovered == listed


@pytest.mark.parametrize(
    "block,call,output_kinds,upload_kinds", _MANIFEST_BLOCKS
)
def test_every_manifest_block_carries_the_bulk_import_entry_in_both_lanes(
    db, block, call, output_kinds, upload_kinds,
):
    """Outputs 01 and 03 — the two Concept Files — in every block.

    Before this slice none of the four blocks emitted a Concept File
    entry at all, in either lane: the eight entries between them were the
    review workbook, the diagnostics zip, the release JSON and the upload
    action, and every one of those is evidence, not an output.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    by_kind = {entry["kind"]: entry for entry in call(job)}
    for kind in output_kinds:
        assert kind in by_kind, f"{block.__name__} never emits {kind}"

    if "release_bulk_import" in output_kinds:
        assert by_kind["release_bulk_import"]["download_url"] == (
            f"/build-concepts/uploads/{job.id}/release-bulk-import.xlsx"
        )
        assert not by_kind["release_bulk_import"].get("disabled")
    assert by_kind["pre_release_bulk_import"]["download_url"] == (
        f"/build-concepts/uploads/{job.id}/release-bulk-import.xlsx?lane=pre"
    )
    assert not by_kind["pre_release_bulk_import"].get("disabled")


@pytest.mark.parametrize(
    "block,call,output_kinds,upload_kinds", _MANIFEST_BLOCKS
)
def test_the_manifest_lists_the_four_outputs_in_the_owner_numbering(
    db, block, call, output_kinds, upload_kinds,
):
    """01 Pre Concept, 02 Pre Master, 03 Post Concept, 04 Post Master.

    The ORDER is asserted from this slice on, with the two Master Files
    present as ``disabled`` placeholders, so S6 — which is where the
    release row that addresses them exists — cannot quietly append them
    at the end of a list and leave the reviewer reading 01, 03, 02, 04.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    kinds = [entry["kind"] for entry in call(job)]
    emitted_outputs = [
        kind for kind in kinds if kind in release_files.OUTPUT_KIND_ORDER
    ]

    assert emitted_outputs == list(output_kinds)
    # ...and they lead the list: the evidence artifacts come after.
    assert kinds[:len(emitted_outputs)] == emitted_outputs

    by_kind = {entry["kind"]: entry for entry in call(job)}
    for master in ("pre_release_master", "release_master"):
        if master in output_kinds:
            assert by_kind[master]["disabled"] is True
            assert by_kind[master]["disabled_reason"]


@pytest.mark.parametrize(
    "block,call,output_kinds,upload_kinds", _MANIFEST_BLOCKS
)
def test_every_manifest_block_states_the_release_state_it_would_publish(
    db, block, call, output_kinds, upload_kinds,
):
    """``release_state`` on the ``database_upload`` entry, in all four.

    Q10: this ADVISES. Nothing here disables a download, and the upload
    entry's own ``disabled`` flag still tracks only whether the lane was
    already published.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    by_kind = {entry["kind"]: entry for entry in call(job)}
    for kind in upload_kinds:
        entry = by_kind[kind]
        lane = (
            release.LANE_PRE if kind.startswith("pre_")
            else release.LANE_POST
        )
        payload = release.release_payload(job, lane=lane)
        assert entry["release_state"] == release.release_state(payload)
        assert entry["structural_defects"] == release.structural_defects(
            payload
        )


@pytest.mark.parametrize(
    "block,call,output_kinds,upload_kinds", _MANIFEST_BLOCKS
)
def test_no_manifest_kind_string_carries_an_output_number(
    db, block, call, output_kinds, upload_kinds,
):
    """T14: a ``kind`` that spelled "01" would be renamed by the next ruling.

    Renaming a manifest kind is a client break, so the numbering lives in
    the ORDER and in the LABEL and never in the wire name.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    for entry in call(job):
        assert not re.search(r"\d", entry["kind"]), entry["kind"]


def test_the_two_manifest_implementations_agree_entry_for_entry(db):
    """``install()`` monkeypatches one over the other.

    Editing one alone is a silent no-op, and the two disagreeing is a
    manifest whose content depends on whether ``bootstrap()`` has run.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    lazy = _lazy_post(job)
    eager = _eager_post(job)

    assert [row["kind"] for row in lazy] == [row["kind"] for row in eager]

    # EVERY key, not just ``kind`` and ``download_url``. A pin that
    # compares two fields cannot catch a one-sided edit to a third, and a
    # one-sided edit is the entire failure mode this test exists for —
    # ``release_state``, ``structural_defects``, ``disabled`` and
    # ``disabled_reason`` all arrived in this slice and would have slipped
    # straight past the narrower comparison.
    #
    # ``size_bytes`` is the one documented exception: the eager block
    # MEASURES the artifacts it builds and the lazy block advertises 0,
    # which is the whole reason the lazy twin exists. It is compared by
    # key presence instead, so a twin that dropped the key still fails.
    for left, right in zip(lazy, eager):
        assert set(left) == set(right), left.get("kind")
        for key in set(left) - {"size_bytes"}:
            assert left[key] == right[key], (left.get("kind"), key)


# --------------------------------------------------------------------------- #
# 3. THE PUBLISH GATE — and the download default it must not disturb
# --------------------------------------------------------------------------- #

def test_publishing_without_an_explicit_lane_is_refused(db, client):
    """A lane-less publish is a 400, never a defaulted Post publication.

    Mechanics in the CLAUDE.md sense: the gate refuses an unsafe REQUEST
    and makes no judgment about content. It lives on the SERVER because a
    server that trusts its caller to send the right lane has the hole
    open for every other caller — curl, a replayed URL, the next client.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)

    for url in (
        f"/build-concepts/uploads/{job.id}/upload-release",
        f"/build-concepts/uploads/{job.id}/upload-release?lane=",
        f"/build-concepts/uploads/{job.id}/upload-release?lane=%20",
    ):
        refusal = client.post(url)
        assert refusal.status_code == 400, url
        detail = refusal.json()["detail"]
        # It names BOTH valid values, so the refusal says what to send.
        assert "lane=post" in detail
        assert "lane=pre" in detail

    # ...and nothing was published by the refused requests.
    db.refresh(job)
    post = release.release_payload(job)
    pre = release.release_payload(job, lane=release.LANE_PRE)
    assert not (post.get("summary") or {}).get("database_uploaded")
    assert not (pre.get("summary") or {}).get("database_uploaded")


def test_publishing_with_an_explicit_lane_still_works(db, client):
    """The gate refuses the ABSENT lane, not the named one."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter, questions=2)

    accepted = client.post(
        f"/build-concepts/uploads/{job.id}/upload-release?lane=post"
    )
    assert accepted.status_code == 200

    db.refresh(job)
    assert (release.release_payload(job).get("summary") or {}).get(
        "database_uploaded"
    )
    # The other lane is untouched — Rule G, one act per lane.
    pre = release.release_payload(job, lane=release.LANE_PRE)
    assert not (pre.get("summary") or {}).get("database_uploaded")


def test_the_four_downloads_still_default_to_the_post_lane(db, client):
    """This matters as much as the gate above.

    Silently breaking every recorded download URL would be a worse defect
    than the one the gate closes: a wrong DOWNLOAD costs a reviewer a
    click and is recoverable, and the Post default is the backward
    compatibility every recorded URL depends on.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    base = f"/build-concepts/uploads/{job.id}"

    for route in (
        "release.json", "release.xlsx", "diagnostics.zip",
        "release-bulk-import.xlsx",
    ):
        default = client.get(f"{base}/{route}")
        explicit = client.get(f"{base}/{route}?lane=post")
        assert default.status_code == 200, route
        assert explicit.status_code == 200, route
        assert default.content == explicit.content, route

    assert json.loads(client.get(f"{base}/release.json").content)[
        "learning_kind"
    ] == "post"


def test_every_publish_url_the_server_emits_names_its_lane(db):
    """The server must not hand out a URL its own gate refuses.

    Every publish affordance the backend advertises — the terminal run
    result and all four manifest blocks — states its lane explicitly,
    including "post", which the DOWNLOAD urls still leave implicit.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    base = f"/build-concepts/uploads/{job.id}/upload-release"

    assert release.release_result(job)["database_upload_url"] == (
        f"{base}?lane=post"
    )
    assert release.release_result(job, lane=release.LANE_PRE)[
        "database_upload_url"
    ] == f"{base}?lane=pre"

    for call in (_lazy_post, _lazy_pre, _eager_post, _eager_pre):
        for entry in call(job):
            if entry.get("action") != "post":
                continue
            url = entry["download_url"]
            assert "?lane=" in url, (call.__name__, entry["kind"], url)
            lane = url.split("?lane=", 1)[1]
            # The gate accepts exactly what the manifest advertises.
            assert build_concepts_api._publish_lane(lane) == lane
