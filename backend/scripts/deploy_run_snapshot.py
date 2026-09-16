"""Online SQLite backup and run-identity check around an in-place Fly update.

Standard library only: the preflight runs inside the previous runtime image.
No active run is cancelled and no provider requests are made by this script.
This preserves database identities; an unsaved in-flight response is not a
checkpoint and cannot be verified or recovered by a database backup.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import uuid


_ACTIVE_STATUSES = {"processing", "master", "waiting"}
_LIVE_TASK_STATES = {"queued", "leased", "blocked"}


class DeploymentIdentityError(RuntimeError):
    """Fail verification while retaining the complete observational report."""

    def __init__(self, report: dict) -> None:
        self.report = report
        super().__init__(
            "Previously active jobs changed run identity: "
            + str(report["changed_run_identity_job_ids"])
        )


def _mapping(raw) -> dict:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _tables(db) -> set:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _live_tasks(db) -> dict:
    tables = _tables(db)
    if "chapter_batch_tasks" not in tables:
        return {}
    columns = {row[1] for row in db.execute("PRAGMA table_info(chapter_batch_tasks)")}
    if "job_id" in columns:
        query = "SELECT job_id, state FROM chapter_batch_tasks"
    elif "batch_row_id" in columns and "chapter_batch_rows" in tables:
        # The preceding image stores the source binding on its chapter row.
        # Its live-task invariant prevents replacing that row's binding. Only
        # use this exact foreign-key join for that historical schema.
        query = ("SELECT r.job_id, t.state FROM chapter_batch_tasks t "
                 "LEFT JOIN chapter_batch_rows r ON r.id = t.batch_row_id")
    else:
        raise RuntimeError("Cannot identify active tasks in the installed queue schema")
    tasks = {}
    for job_id, state in db.execute(query):
        if state in _LIVE_TASK_STATES:
            tasks.setdefault(job_id, []).append(state)
    return tasks


def _jobs(db) -> dict:
    # These fields already exist in the image preceding this deployment.
    columns = {row[1] for row in db.execute("PRAGMA table_info(upload_jobs)")}
    fields = ["id", "run_state", "question_inventory"]
    fields += [name for name in ("run_id", "generation_checkpoint") if name in columns]
    jobs = {}
    for row in db.execute("SELECT " + ", ".join(fields) + " FROM upload_jobs"):
        raw = dict(row)
        state = _mapping(raw["run_state"])
        inventory = _mapping(raw["question_inventory"])
        review = inventory.get("_aegis_concept_review") or {}
        recovery = inventory.get("_aegis_run_recovery_request") or {}
        jobs[raw["id"]] = {
            "run_id": raw.get("run_id") or state.get("run_id") or "",
            "status": state.get("status", ""),
            "review_status": review.get("status", "") if isinstance(review, dict) else "",
            "recovery_status": recovery.get("status", "") if isinstance(recovery, dict) else "",
            "checkpoint_stage": _mapping(raw.get("generation_checkpoint")).get("stage", ""),
        }
    return jobs


def _atomic_manifest(path: Path, document: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x") as output:
            json.dump(document, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        # The manifest must survive the same machine replacement as its backup.
        descriptor = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def wait_for_release(revision: str, *, timeout: float = 45) -> None:
    """Verify that application startup/recovery finished in the expected image."""
    deadline = time.monotonic() + timeout
    address = "http://127.0.0.1:" + os.environ.get("PORT", "8000") + "/health"
    while True:
        try:
            with urllib.request.urlopen(address, timeout=2) as response:
                health = json.load(response)
            if isinstance(health, dict) and health.get("status") == "ok" and health.get("release") == revision:
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Application startup did not become healthy at the requested release revision")
        time.sleep(.5)


def snapshot(root: Path, revision: str, *, after: bool = False) -> dict:
    if not revision or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("a hexadecimal commit revision is required")
    database = root / "aegis.db"
    if not database.is_file():
        raise RuntimeError("Expected production SQLite database is missing")
    folder = root / "deploy-backups"
    folder.mkdir(exist_ok=True)
    manifest = folder / f"{revision}.json"
    # mode=ro prevents this observational script from creating/replacing a DB.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        if after:
            if os.environ.get("AEGIS_RELEASE_SHA") != revision:
                raise RuntimeError("The running image does not report the requested release revision")
            prior = json.loads(manifest.read_text())
            if prior.get("revision") != revision:
                raise RuntimeError("Deployment manifest belongs to another release revision")
            if not Path(prior["backup"]).is_file():
                raise RuntimeError("The verified pre-deployment backup is missing")
            missing_tables = {"run_notifications", "chapter_batch_tasks"} - _tables(db)
            if missing_tables:
                raise RuntimeError(f"Deployment migrations are missing: {sorted(missing_tables)}")
            # Hold one read snapshot across identities and task states. The
            # queue may advance naturally while the health check is running.
            db.execute("BEGIN")
            jobs = _jobs(db)
            missing = set(prior["active_job_ids"]) - jobs.keys()
            if missing:
                raise RuntimeError(f"Previously active jobs missing: {sorted(missing)}")
            tasks = _live_tasks(db)
            counts = dict(db.execute("SELECT state, count(*) FROM chapter_batch_tasks GROUP BY state"))
            current = {str(job_id): jobs[job_id] | {"queue_states": sorted(tasks.get(job_id, []))}
                       for job_id in prior["active_job_ids"]}
            changed = [job_id for job_id in prior["active_job_ids"]
                       if prior.get("active_jobs", {}).get(str(job_id), {}).get("run_id")
                       and prior["active_jobs"][str(job_id)]["run_id"] != jobs[job_id]["run_id"]]
            report = {"phase": "after", "revision": revision, "release_verified": True,
                      "preserved_active_job_count": len(prior["active_job_ids"]) - len(changed),
                      "changed_run_identity_job_ids": changed, "current_jobs": current,
                      "queue_states": counts,
                      "email_sender_configured": bool(os.environ.get("AEGIS_SMTP_HOST") and os.environ.get("AEGIS_NOTIFICATION_FROM"))}
            if changed:
                # Reaching the new image is not proof that its migration and
                # recovery preserved an existing run. Never roll back live work
                # here; fail the workflow and retain the diagnostic for review.
                raise DeploymentIdentityError(report)
            return report
        # page_count includes committed WAL pages not reflected in the main
        # file's stat size, so a busy WAL database cannot understate the budget.
        size = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
        minimum = max(size * 2, 64 * 1024 * 1024)
        if shutil.disk_usage(root).free < minimum:
            raise RuntimeError("Not enough free space for an online database backup")
        # A retry of one revision retains the previous verified backup. Only
        # publish its replacement manifest once the new snapshot is verified.
        backup = folder / f"{revision}-{uuid.uuid4().hex}.db"
        verified = False
        try:
            with sqlite3.connect(str(backup)) as target:
                db.backup(target, pages=256, sleep=.05)
            with sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True) as check:
                check.row_factory = sqlite3.Row
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Database backup did not pass quick_check")
                # Read identities from the backup, never the advancing live DB.
                jobs = _jobs(check)
                tasks = _live_tasks(check)
                active = sorted(job_id for job_id, job in jobs.items()
                                if job["status"] in _ACTIVE_STATUSES
                                or job["review_status"] == "master_building"
                                or job_id in tasks)
            verified = True
            document = {"version": 1, "revision": revision, "created_at": time.time(),
                        "active_job_ids": active,
                        "active_jobs": {str(job_id): jobs[job_id] for job_id in active},
                        "unbound_live_task_count": len(tasks.get(None, [])),
                        "backup": str(backup.resolve())}
            _atomic_manifest(manifest, document)
        except BaseException:
            # Do not leave an unverified partial backup or replace the last
            # successful manifest when preflight fails.
            if not verified:
                backup.unlink(missing_ok=True)
            raise
        return {"phase": "before", "revision": revision, "active_job_count": len(active),
                "unbound_live_task_count": len(tasks.get(None, [])), "backup_verified": True}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("revision")
    parser.add_argument("--after", action="store_true")
    args = parser.parse_args(argv)
    if args.after:
        wait_for_release(args.revision)
    try:
        report = snapshot(Path(os.environ.get("AEGIS_DATA_DIR", "/data")), args.revision, after=args.after)
    except DeploymentIdentityError as exc:
        # Actions needs both a failed gate and the recovery observations that
        # explain it. Printing the report does not convert the refusal to success.
        print(json.dumps(exc.report, sort_keys=True), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
