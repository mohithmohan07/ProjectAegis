"""Focused regressions for Master ENOSPC containment and recovery."""
from __future__ import annotations

import copy
import errno
import json
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import bulk_import, config, models
from app.bulk_import import assessment_workbook
from app.services import assessment_profile
from app.services import assessment_release_snapshot
from app.services import assessment_release_service as release_service
from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as release_contract
from app.services import build_concepts_release_files as release_files
from app.services import identity, storage_capacity, uploads
from tests.test_mes_release_lifecycle import _fresh_release
from tests.test_release_core import _both_lanes_job, _chapter_with_concepts


def _statvfs(*, available_bytes: int, available_inodes: int = 10_000):
    block = 1
    return SimpleNamespace(
        f_frsize=block,
        f_bsize=block,
        f_bavail=available_bytes // block,
        f_files=max(1, available_inodes * 2),
        f_favail=available_inodes,
    )


def _small_thresholds(monkeypatch) -> None:
    monkeypatch.setenv("AEGIS_MASTER_STORAGE_RESERVATION_BYTES", "100")
    monkeypatch.setenv("AEGIS_STORAGE_LEDGER_HEADROOM_BYTES", "10")
    monkeypatch.setenv("AEGIS_MASTER_PUBLICATION_MARGIN_BYTES", "5")
    monkeypatch.setenv("AEGIS_MASTER_STORAGE_RESERVATION_INODES", "5")
    monkeypatch.setenv("AEGIS_STORAGE_LEDGER_HEADROOM_INODES", "1")


_SHARED_CONCEPT_AUTHORITY_FIELDS = (
    "chapter_title",
    "chapter_display_name",
    "chapter_duration",
    "pre_topics",
    "post_topics",
    "chapter_description",
    "topic_title",
    "topic_display_name",
    "pre_post_learning",
    "topic_concept_labels",
    "related_topics",
    "topic_description",
    "concept_title",
    "concept_display_name",
    "concept_details",
    "keywords",
    "digicards",
    "related_concepts",
)


def _concept_authority(
    row: dict,
    *,
    profile: dict | str | None = None,
) -> dict[str, str]:
    """Authored front-band values shared by Concept and Master formats.

    The reference Master deliberately formats ``topic_title`` without the
    Concept File's ``Topic NN:`` prefix, so the one reader-owned normalizer
    compares their common identity. ``chapter_duration`` remains part of the
    shared authority, but the run profile owns whether it ships blank: the
    generic reference profile blanks it, while the Grade-6 MSBSHSE override
    carries the authored value. Applying that same declarative projection to
    the Concept side preserves strict equality without weakening the authority
    invariant. Group aggregates and concept-question labels are assessment
    linkage, not preserved Concept authority.
    """

    projected = {
        field: str(row.get(field) or "")
        for field in _SHARED_CONCEPT_AUTHORITY_FIELDS
    }
    projected["topic_title"] = bulk_import.strip_topic_title(
        projected["topic_title"]
    )
    for field in assessment_profile.forced_blank_fields(profile):
        if field in projected:
            projected[field] = ""
    return projected


def _concept_authority_by_identity(
    workbook: bytes,
    *,
    profile: dict | str | None = None,
) -> dict[str, dict]:
    parsed = assessment_workbook.parse_workbook(workbook)
    authority: dict[str, dict] = {}
    for sheet in assessment_workbook.SHEET_ORDER:
        for row in parsed["sheets"][sheet]["rows"]:
            projected = _concept_authority(row, profile=profile)
            if not projected["concept_title"]:
                continue
            key = identity.title_tag(projected["concept_title"])
            assert key, (
                f"{sheet} concept title has no stable machine identity: "
                f"{projected['concept_title']!r}"
            )
            # ``concept_source`` is authored Concept authority, but the
            # Descriptive reference layout has no such column. Compare it on
            # every sheet that can carry it and do not invent it on a sheet
            # whose positional contract cannot.
            if "concept_source" in row:
                projected["concept_source"] = str(
                    row.get("concept_source") or ""
                )
            previous = authority.setdefault(key, projected)
            for field, value in projected.items():
                if field in previous:
                    assert value == previous[field], (
                        f"{sheet} carries two {field!r} values for {key!r}"
                    )
                else:
                    previous[field] = value
    return authority


def test_per_lane_reservations_cannot_approve_the_same_free_bytes(monkeypatch):
    _small_thresholds(monkeypatch)
    monkeypatch.setattr(
        storage_capacity.os,
        "statvfs",
        lambda _path: _statvfs(available_bytes=200),
    )

    with storage_capacity.reserve_master_capacity(job_id=880001, lane="pre"):
        with pytest.raises(storage_capacity.StorageCapacityError):
            with storage_capacity.reserve_master_capacity(
                job_id=880002, lane="post",
            ):
                pytest.fail("the second lane reused the first lane's capacity")


def test_exact_publication_gate_uses_rendered_bytes(monkeypatch):
    _small_thresholds(monkeypatch)
    monkeypatch.setattr(
        storage_capacity.os,
        "statvfs",
        lambda _path: _statvfs(available_bytes=100),
    )

    with pytest.raises(storage_capacity.StorageCapacityError) as caught:
        storage_capacity.require_publication_capacity(90)

    assert caught.value.phase == "master_publication_preflight"
    assert caught.value.required_bytes == 105


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EDQUOT])
def test_os_capacity_errors_have_one_stable_domain_contract(error_number):
    normalized = storage_capacity.capacity_error_from(
        OSError(error_number, "filesystem refused the write"),
        phase="Master write",
    )

    assert isinstance(normalized, storage_capacity.StorageCapacityError)
    assert normalized.public_detail()["code"] == "insufficient_storage"
    assert normalized.public_detail()["retryable"] is True
    assert normalized.public_detail()["capacity"] == {}
    assert "storage used by the application" in str(normalized)
    assert str(config.DATA_DIR) not in str(normalized)


def test_batch_refusal_records_both_lanes_and_never_launches_workers(
    db, monkeypatch,
):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    error = storage_capacity.StorageCapacityError(
        "batch has no capacity",
        phase="master_batch_preflight",
    )
    recorded: list[str] = []
    console: list[tuple[str, str]] = []
    monkeypatch.setattr(
        storage_capacity,
        "reserve_master_batch_capacity",
        lambda **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        release,
        "record_assessment_lane_unavailable",
        lambda _db, _job, *, lane, error: recorded.append(lane),
    )
    monkeypatch.setattr(
        release_contract.kernel,
        "parallel_map_in_order",
        lambda *_args, **_kwargs: pytest.fail("a Master worker was launched"),
    )
    monkeypatch.setattr(
        release_contract.progress,
        "log",
        lambda message, *, level="info": console.append((message, level)),
    )

    result = release_contract._build_master_siblings(
        db,
        job.id,
        chapter.id,
        owner_sub=job.owner_sub,
    )

    assert result == {release.LANE_PRE: None, release.LANE_POST: None}
    assert recorded == [release.LANE_PRE, release.LANE_POST]
    assert console == [("batch has no capacity", "warning")]


def test_concurrent_batch_admission_is_atomic_and_second_launches_no_worker(
    monkeypatch,
):
    _small_thresholds(monkeypatch)
    monkeypatch.setattr(
        storage_capacity.os,
        "statvfs",
        lambda _path: _statvfs(available_bytes=300),
    )
    monkeypatch.setattr(
        release_contract,
        "_lane_master_eligibility",
        lambda *_args, **_kwargs: (True, ""),
    )
    recorded: list[tuple[int, str]] = []
    monkeypatch.setattr(
        release_contract,
        "_record_master_failure",
        lambda _db, job_id, lane, **_kwargs: recorded.append((job_id, lane)),
    )
    monkeypatch.setattr(
        release_contract.kernel,
        "parallel_map_in_order",
        lambda items, function, **_kwargs: [function(item) for item in items],
    )
    entered = threading.Event()
    release_first = threading.Event()
    provider_calls: list[int] = []

    class _Built:
        id = 1

    def rebuild(_db, job_id, _lane, **_kwargs):
        provider_calls.append(job_id)
        if job_id == 910001 and provider_calls.count(job_id) == 1:
            entered.set()
            assert release_first.wait(timeout=5)
        return _Built()

    monkeypatch.setattr(release_contract, "rebuild_lane_master", rebuild)
    fake_db = SimpleNamespace(expire_all=lambda: None)
    first_result: dict[str, object] = {}
    first_errors: list[BaseException] = []

    def run_first():
        try:
            first_result.update(release_contract._build_master_siblings(
                fake_db, 910001, 1, owner_sub="local:default",
            ))
        except BaseException as exc:  # pragma: no cover - assertion evidence
            first_errors.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert entered.wait(timeout=5)
    second = release_contract._build_master_siblings(
        fake_db, 910002, 1, owner_sub="local:default",
    )

    assert provider_calls == [910001]
    assert second == {release.LANE_PRE: None, release.LANE_POST: None}
    assert recorded == [
        (910002, release.LANE_PRE),
        (910002, release.LANE_POST),
    ]

    release_first.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_errors == []
    assert provider_calls == [910001, 910001]
    assert all(value is not None for value in first_result.values())


def test_completed_lane_consumption_replaces_its_batch_estimate(monkeypatch):
    _small_thresholds(monkeypatch)
    free = {"bytes": 270}
    monkeypatch.setattr(
        storage_capacity.os,
        "statvfs",
        lambda _path: _statvfs(available_bytes=free["bytes"]),
    )

    with storage_capacity.reserve_master_batch_capacity(
        job_id=910003,
        lanes=(release.LANE_PRE, release.LANE_POST),
    ) as batch:
        with storage_capacity.use_master_batch_lane(
            batch, job_id=910003, lane=release.LANE_PRE,
        ):
            with storage_capacity.reserve_master_capacity(
                job_id=910003, lane=release.LANE_PRE,
            ):
                storage_capacity.require_publication_capacity(90)
        # Pre has consumed 90 real bytes. Its 100-byte estimate is gone, while
        # Post's token remains. Post must see real free space, not subtract the
        # completed Pre lane a second time.
        free["bytes"] = 180
        with storage_capacity.use_master_batch_lane(
            batch, job_id=910003, lane=release.LANE_POST,
        ):
            with storage_capacity.reserve_master_capacity(
                job_id=910003, lane=release.LANE_POST,
            ):
                snapshot = storage_capacity.require_publication_capacity(90)

    assert snapshot.available_bytes == 180


def test_explicit_rebuild_claims_job_lock_but_in_run_sibling_does_not(
    monkeypatch,
):
    job_id = 880003
    calls: list[int] = []
    fake_db = SimpleNamespace(rollback=lambda: None)
    capacity = storage_capacity.CapacitySnapshot(
        path=str(config.DATA_DIR),
        available_bytes=10_000,
        available_inodes=10_000,
    )
    monkeypatch.setattr(
        uploads,
        "get_job",
        lambda *_args, **_kwargs: SimpleNamespace(id=job_id),
    )
    monkeypatch.setattr(
        storage_capacity,
        "reserve_master_capacity",
        lambda **_kwargs: nullcontext(capacity),
    )
    from app.services import assessment_release_run

    monkeypatch.setattr(
        assessment_release_run,
        "run_release_for_job",
        lambda _db, actual_job_id, **_kwargs: calls.append(actual_job_id)
        or SimpleNamespace(id=1),
    )

    with uploads.exclusive_job_operation(job_id):
        with pytest.raises(uploads.JobAlreadyRunningError):
            release_contract.rebuild_lane_master(
                fake_db,
                job_id,
                release.LANE_POST,
                owner_sub="local:default",
                claim_job_lock=True,
            )
        # This is the automatic sibling shape: its parent owns the job lock,
        # so it must not try to acquire the non-reentrant lock again.
        release_contract.rebuild_lane_master(
            fake_db,
            job_id,
            release.LANE_POST,
            owner_sub="local:default",
            claim_job_lock=False,
        )

    assert calls == [job_id]


@pytest.mark.parametrize("lane", [release.LANE_PRE, release.LANE_POST])
def test_explicit_rebuild_preserves_the_lane_concept_authority_and_master_bands(
    db, monkeypatch, lane,
):
    """A retry is Output 01→02 or 03→04, never a new Concept run.

    Capture the same downloadable Concept projection and staged content seal
    that survived ENOSPC, execute the explicit lane-rebuild contract, then
    prove both are unchanged. The rebuilt Master projection must carry every
    shared authored Chapter/Topic/Concept value from that preserved lane.
    The format's deliberate presentation/linkage differences are excluded by
    ``_concept_authority`` above; concept teaching content is not.
    """

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    staged_initial = copy.deepcopy(release.release_payload(job, lane=lane))
    assert staged_initial is not None
    seal_initial = assessment_release_snapshot.source_release_sha256(
        staged_initial
    )
    initial_bridge = assessment_release_snapshot.build(
        db, job, staged_initial,
    )
    run_profile = assessment_profile.resolve_for_metadata(
        None, initial_bridge["metadata"],
    )
    concept_before = release_files.build_release_bulk_import_workbook(
        db, job, lane=lane,
    )
    concept_authority = _concept_authority_by_identity(
        concept_before, profile=run_profile,
    )
    assert concept_authority

    capacity_failure = storage_capacity.StorageCapacityError(
        "insufficient storage for the first attempt",
        phase="master_preflight",
    )
    monkeypatch.setattr(
        storage_capacity,
        "reserve_master_capacity",
        lambda **_kwargs: (_ for _ in ()).throw(capacity_failure),
    )
    with pytest.raises(storage_capacity.StorageCapacityError):
        release_contract.rebuild_lane_master(
            db,
            job.id,
            lane,
            owner_sub=job.owner_sub,
            claim_job_lock=True,
        )

    db.expire_all()
    job = uploads.get_job(
        db,
        job.id,
        owner_sub=job.owner_sub,
        module="build_concepts",
    )
    staged_before = copy.deepcopy(release.release_payload(job, lane=lane))
    assert staged_before is not None
    # The durable failure note may change; the Concept-authority seal may not.
    seal_before = assessment_release_snapshot.source_release_sha256(
        staged_before
    )
    assert seal_before == seal_initial
    assert release.assessment_lane_issue(staged_before) is not None
    assert _concept_authority_by_identity(
        release_files.build_release_bulk_import_workbook(db, job, lane=lane),
        profile=run_profile,
    ) == concept_authority

    from app.services import assessment_release_run
    from app.services import build_concepts

    monkeypatch.setattr(
        build_concepts,
        "generate_post_learning",
        lambda *_args, **_kwargs: pytest.fail(
            "Master recovery reran Concept generation"
        ),
    )
    monkeypatch.setattr(
        release,
        "stage_release",
        lambda *_args, **_kwargs: pytest.fail(
            "Master recovery restaged the Concept lane"
        ),
    )

    runner_calls: list[tuple[int, str]] = []

    def preserved_runner(_db, job_id, *, owner_sub=None, stage_progress=None):
        current_job = uploads.get_job(
            _db,
            job_id,
            owner_sub=owner_sub,
            module="build_concepts",
        )
        current = release.release_payload(current_job, lane=lane)
        assert current == staged_before
        assert assessment_release_snapshot.source_release_sha256(
            current
        ) == seal_before
        runner_calls.append((job_id, lane))
        return SimpleNamespace(id=71 if lane == release.LANE_PRE else 72)

    runner_name = (
        "run_pre_release_for_job"
        if lane == release.LANE_PRE
        else "run_release_for_job"
    )
    monkeypatch.setattr(assessment_release_run, runner_name, preserved_runner)
    capacity = storage_capacity.CapacitySnapshot(
        path=str(config.DATA_DIR),
        available_bytes=10_000_000,
        available_inodes=10_000,
    )
    monkeypatch.setattr(
        storage_capacity,
        "reserve_master_capacity",
        lambda **_kwargs: nullcontext(capacity),
    )

    release_contract.rebuild_lane_master(
        db,
        job.id,
        lane,
        owner_sub=job.owner_sub,
        claim_job_lock=True,
    )

    db.refresh(job)
    staged_after = release.release_payload(job, lane=lane)
    assert staged_after == staged_before
    assert assessment_release_snapshot.source_release_sha256(
        staged_after
    ) == seal_before
    assert runner_calls == [(job.id, lane)]
    assert _concept_authority_by_identity(
        release_files.build_release_bulk_import_workbook(db, job, lane=lane),
        profile=run_profile,
    ) == concept_authority

    bridge = assessment_release_snapshot.build(db, job, staged_after)
    rebuilt_profile = assessment_profile.resolve_for_metadata(
        None, bridge["metadata"],
    )
    assert rebuilt_profile == run_profile
    master_snapshot = copy.deepcopy(bridge["snapshot"])
    master_snapshot["groups"] = []
    master_snapshot["candidates"] = []
    master, issues = assessment_workbook.render_master_file(
        master_snapshot, rebuilt_profile,
    )
    assert issues["unplaced"] == []
    assert _concept_authority_by_identity(
        master, profile=rebuilt_profile,
    ) == concept_authority


@pytest.mark.parametrize(
    "suffix",
    ["", "/pre"],
)
def test_explicit_rebuild_routes_map_raw_enospc_to_507(
    client, monkeypatch, suffix,
):
    claims: list[bool] = []

    def fail(*_args, **kwargs):
        claims.append(bool(kwargs.get("claim_job_lock")))
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(release_contract, "rebuild_lane_master", fail)
    response = client.post(
        f"/build-assessments/releases/from-job/880004{suffix}",
    )

    assert response.status_code == 507
    assert response.json()["detail"]["code"] == "insufficient_storage"
    assert response.json()["detail"]["retryable"] is True
    assert claims == [True]


def test_explicit_rebuild_route_maps_job_overlap_to_409(client, monkeypatch):
    monkeypatch.setattr(
        release_contract,
        "rebuild_lane_master",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            uploads.JobAlreadyRunningError("generation is already running")
        ),
    )

    response = client.post(
        "/build-assessments/releases/from-job/880005",
    )

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


@pytest.mark.parametrize(
    ("builds", "expected_all", "label_fragment"),
    [
        (
            {
                release.LANE_PRE: {"release_id": "REL-pre"},
                release.LANE_POST: {"release_id": "REL-post"},
            },
            True,
            "all four outputs ready",
        ),
        (
            {
                release.LANE_PRE: None,
                release.LANE_POST: {"release_id": "REL-post"},
            },
            False,
            "Master files ready 1/2 (unavailable: Pre)",
        ),
    ],
)
def test_generation_result_and_done_label_report_actual_master_outcomes(
    monkeypatch, builds, expected_all, label_fragment,
):
    monkeypatch.setattr(
        release_contract,
        "_stage_generation_release",
        lambda *_args, **_kwargs: {"status": "released"},
    )
    monkeypatch.setattr(
        release_contract,
        "_build_master_siblings",
        lambda *_args, **_kwargs: builds,
    )
    labels: list[str] = []
    monkeypatch.setattr(
        release_contract.progress,
        "set_progress",
        lambda _value, *, label="": labels.append(label),
    )

    result = release_contract._run_generation_release(
        lambda: None,
        SimpleNamespace(),
        910004,
        1,
        owner_sub="local:default",
    )

    assert result["all_four_outputs_ready"] is expected_all
    assert result["master_outputs"][release.LANE_PRE]["ready"] is (
        builds[release.LANE_PRE] is not None
    )
    assert result["master_outputs"][release.LANE_POST]["ready"] is (
        builds[release.LANE_POST] is not None
    )
    assert len(labels) == 1
    assert label_fragment in labels[0]
    if not expected_all:
        assert "all four outputs ready" not in labels[0]


def test_storage_issue_carries_retry_and_capacity_evidence(db):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    snapshot = storage_capacity.CapacitySnapshot(
        path="/data",
        available_bytes=4,
        available_inodes=2,
    )
    error = storage_capacity.StorageCapacityError(
        "insufficient storage",
        phase="master_preflight",
        snapshot=snapshot,
        required_bytes=100,
        required_inodes=5,
    )

    issue = release.record_assessment_lane_unavailable(
        db,
        job,
        lane=release.LANE_POST,
        error=error,
    )

    assert issue is not None
    assert issue["details"]["failure_code"] == "insufficient_storage"
    assert issue["details"]["retryable"] is True
    assert issue["details"]["capacity"]["available_bytes"] == 4


def test_publication_capacity_refusal_creates_no_staging_tree(db, monkeypatch):
    made, _payload, _label = _fresh_release(db)
    error = storage_capacity.StorageCapacityError(
        "insufficient storage",
        phase="master_publication_preflight",
    )
    monkeypatch.setattr(
        storage_capacity,
        "require_publication_capacity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(storage_capacity.StorageCapacityError):
        release_service.publish_release(db, made)

    target = release_service._version_dir(made)
    assert not target.exists()
    assert not target.with_name(target.name + ".staging").exists()


def test_enospc_during_workbook_write_is_normalized_and_staging_is_removed(
    db, monkeypatch,
):
    made, _payload, _label = _fresh_release(db)
    original_write_bytes = Path.write_bytes

    def write_or_fail(path: Path, data: bytes):
        if path.name == release_service.MASTER_FILENAME:
            raise OSError(errno.ENOSPC, "No space left on device")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_or_fail)

    with pytest.raises(storage_capacity.StorageCapacityError) as caught:
        release_service.publish_release(db, made)

    staging = release_service._version_dir(made).with_name(
        f"v{made.version}.staging",
    )
    assert caught.value.phase == f"writing {release_service.MASTER_FILENAME}"
    assert not staging.exists()


@pytest.mark.parametrize("superseded_before_restart", [False, True])
def test_uncertain_metadata_commit_reconciles_only_the_matching_frozen_row(
    db, tmp_path, monkeypatch, superseded_before_restart,
):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    made, _payload, _label = _fresh_release(db)
    release_id = made.id
    real_commit = db.commit

    def uncertain_commit():
        raise OSError(errno.ENOSPC, "database commit result is uncertain")

    monkeypatch.setattr(db, "commit", uncertain_commit)
    with pytest.raises(storage_capacity.StorageCapacityError) as caught:
        release_service.publish_release(db, made)

    target = release_service._version_dir(made)
    assert caught.value.phase == "committing Master publication metadata"
    assert target.is_dir()
    assert not target.with_name(target.name + ".staging").exists()

    # Simulate the next process: discard uncommitted in-memory mutations and
    # use the real database commit again.
    monkeypatch.setattr(db, "commit", real_commit)
    db.rollback()
    made = db.get(models.AssessmentRelease, release_id)
    successor = None
    if superseded_before_restart:
        made.state = "superseded"
        successor = models.AssessmentRelease(
            release_uid=made.release_uid,
            version=made.version + 1,
            owner_sub=made.owner_sub,
            state="materialized",
            concept_snapshot=dict(made.concept_snapshot or {}),
            concept_snapshot_sha256=made.concept_snapshot_sha256,
        )
        db.add(successor)
        db.commit()

    manifest = release_service._verified_target_manifest(target)
    assert manifest["concept_snapshot_sha256"] == made.concept_snapshot_sha256
    assert made.concept_snapshot_sha256 == assessment_workbook.snapshot_sha256(
        made.concept_snapshot,
    )

    reconciled = release_service.reconcile_complete_publications(db)
    db.refresh(made)

    assert reconciled == [str(target)]
    assert made.publication["directory"] == str(target)
    assert made.publication["reconciled_after_restart"] is True
    assert made.workbook_hashes == manifest["workbook_sha256s"]
    if superseded_before_restart:
        assert made.state == "superseded"
        db.refresh(successor)
        assert successor.state == "materialized"
        assert successor.publication == {}
    else:
        assert made.state in {"ready_for_upload", "validated_with_flags"}


@pytest.mark.parametrize("tamper", ["workbook", "snapshot"])
def test_reconciliation_refuses_tampered_complete_publication(
    db, tmp_path, monkeypatch, tamper,
):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    made, _payload, _label = _fresh_release(db)
    release_id = made.id
    real_commit = db.commit

    monkeypatch.setattr(
        db,
        "commit",
        lambda: (_ for _ in ()).throw(
            OSError(errno.ENOSPC, "database commit result is uncertain")
        ),
    )
    with pytest.raises(storage_capacity.StorageCapacityError):
        release_service.publish_release(db, made)

    target = release_service._version_dir(made)
    monkeypatch.setattr(db, "commit", real_commit)
    db.rollback()
    made = db.get(models.AssessmentRelease, release_id)

    if tamper == "workbook":
        (target / release_service.MASTER_FILENAME).write_bytes(b"tampered")
    else:
        manifest_path = target / release_service.MANIFEST_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["concept_snapshot_sha256"] = "0" * 64
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    assert release_service.reconcile_complete_publications(db) == []
    db.refresh(made)
    assert made.state == "materialized"
    assert made.publication == {}
    assert made.workbook_hashes == {}
    assert target.is_dir()  # preserve the refused evidence for an operator


def test_startup_recovery_removes_nested_staging_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    staging = tmp_path / "assessment_releases" / "REL-crashed" / "v3.staging"
    nested = staging / "nested"
    nested.mkdir(parents=True)
    (nested / "partial.bin").write_bytes(b"partial")
    published = staging.parent / "v2"
    published.mkdir()
    (published / "manifest.json").write_text("{}", encoding="utf-8")

    removed = release_service.recover_incomplete_publications()

    assert removed == [str(staging)]
    assert not staging.exists()
    assert published.exists()


def test_startup_reconciliation_does_not_rehash_historical_publications(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    made, _payload, _label = _fresh_release(db)
    release_service.publish_release(db, made)
    monkeypatch.setattr(
        release_service,
        "_verified_target_manifest",
        lambda _target: pytest.fail("a completed DB row was rehashed"),
    )

    assert release_service.reconcile_complete_publications(db) == []


def test_health_remains_live_when_storage_is_critical(client, monkeypatch):
    monkeypatch.setattr(
        storage_capacity,
        "health_status",
        lambda: {"status": "critical", "available_bytes": 0},
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["storage"]["status"] == "critical"


@pytest.mark.parametrize(
    ("available_bytes", "one_lane_ready", "batch_ready", "status"),
    [
        (150, True, False, "critical"),
        (250, True, True, "ok"),
    ],
)
def test_health_distinguishes_lane_retry_from_normal_two_lane_batch(
    monkeypatch,
    available_bytes,
    one_lane_ready,
    batch_ready,
    status,
):
    _small_thresholds(monkeypatch)
    monkeypatch.setattr(
        storage_capacity.os,
        "statvfs",
        lambda _path: _statvfs(available_bytes=available_bytes),
    )

    health = storage_capacity.health_status()

    assert health["status"] == status
    assert health["one_lane_retry"]["ready"] is one_lane_ready
    assert health["two_lane_batch"]["ready"] is batch_ready
    assert health["master_required_bytes"] == 210
