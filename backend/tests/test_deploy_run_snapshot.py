"""Standard-library tests: deployment preflight must also run in the old image."""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy_run_snapshot.py"
_SPEC = importlib.util.spec_from_file_location("deploy_run_snapshot", _PATH)
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


class DeploymentSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / "aegis.db"
        with sqlite3.connect(self.db) as connection:
            connection.executescript("""
                CREATE TABLE upload_jobs (
                    id INTEGER PRIMARY KEY, run_id TEXT, run_state TEXT,
                    question_inventory TEXT, generation_checkpoint TEXT
                );
                CREATE TABLE chapter_batch_tasks (job_id INTEGER, state TEXT);
            """)
        self.add_job(1, status="processing")
        self.add_job(2, status="review")
        self.add_job(3, status="", review="master_building")
        self.add_job(4, status="")
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO chapter_batch_tasks VALUES (4, 'queued')")
        self.revision = "a" * 40

    def add_job(self, job_id, *, status, review=""):
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO upload_jobs VALUES (?, ?, ?, ?, ?)", (
                job_id, f"run-{job_id}", json.dumps({"status": status}),
                json.dumps({"_aegis_concept_review": {"status": review}}),
                json.dumps({"stage": "phase3"}),
            ))

    def manifest(self):
        return json.loads((self.root / "deploy-backups" / f"{self.revision}.json").read_text())

    def migrated(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE run_notifications (id INTEGER)")

    def test_snapshot_tracks_master_and_queued_jobs_without_mutating_live_work(self):
        result = module.snapshot(self.root, self.revision)
        document = self.manifest()
        self.assertEqual(result["active_job_count"], 3)
        self.assertEqual(document["active_job_ids"], [1, 3, 4])
        self.assertEqual(document["active_jobs"]["1"]["run_id"], "run-1")
        with sqlite3.connect(document["backup"]) as backup:
            self.assertEqual(backup.execute("PRAGMA quick_check").fetchone()[0], "ok")
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT run_state FROM upload_jobs WHERE id=1").fetchone()[0], '{"status": "processing"}')

    def test_manifest_reads_only_the_backup_while_live_jobs_advance(self):
        original = module._jobs
        inserted = False
        def advance_then_read(connection):
            nonlocal inserted
            if not inserted:
                self.add_job(5, status="processing")
                inserted = True
            return original(connection)
        with mock.patch.object(module, "_jobs", side_effect=advance_then_read):
            module.snapshot(self.root, self.revision)
        self.assertNotIn(5, self.manifest()["active_job_ids"])
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM upload_jobs").fetchone()[0], 5)

    def test_previous_image_queue_schema_uses_exact_saved_row_binding(self):
        with sqlite3.connect(self.db) as connection:
            connection.executescript("""
                DROP TABLE chapter_batch_tasks;
                CREATE TABLE chapter_batch_rows (id INTEGER PRIMARY KEY, job_id INTEGER);
                CREATE TABLE chapter_batch_tasks (batch_row_id INTEGER, state TEXT);
                INSERT INTO chapter_batch_rows VALUES (40, 4);
                INSERT INTO chapter_batch_tasks VALUES (40, 'queued');
                INSERT INTO chapter_batch_tasks VALUES (999, 'leased');
            """)
        result = module.snapshot(self.root, self.revision)
        self.assertEqual(self.manifest()["active_job_ids"], [1, 3, 4])
        self.assertEqual(result["unbound_live_task_count"], 1)

    def test_retry_retains_verified_backup_and_failed_preflight_keeps_manifest(self):
        module.snapshot(self.root, self.revision)
        first = self.manifest()
        module.snapshot(self.root, self.revision)
        second = self.manifest()
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertTrue(Path(first["backup"]).exists())
        with mock.patch.object(module, "_jobs", side_effect=RuntimeError("snapshot read failed")):
            with self.assertRaisesRegex(RuntimeError, "snapshot read failed"):
                module.snapshot(self.root, self.revision)
        self.assertEqual(self.manifest(), second)
        self.assertEqual(len(list((self.root / "deploy-backups").glob("*.db"))), 2)

    def test_atomic_manifest_failure_keeps_existing_manifest_and_verified_backups(self):
        module.snapshot(self.root, self.revision)
        before = self.manifest()
        with mock.patch.object(module.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                module.snapshot(self.root, self.revision)
        self.assertEqual(self.manifest(), before)
        self.assertTrue(Path(before["backup"]).exists())
        self.assertEqual(list((self.root / "deploy-backups").glob("*.tmp")), [])

    def test_space_check_counts_wal_pages_without_touching_manifest(self):
        with sqlite3.connect(self.db) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE large_payload (payload BLOB)")
            writer.execute("INSERT INTO large_payload VALUES (zeroblob(40 * 1024 * 1024))")
            writer.commit()
            self.assertLess(self.db.stat().st_size, 40 * 1024 * 1024)
            with mock.patch.object(module.shutil, "disk_usage", return_value=mock.Mock(free=70 * 1024 * 1024)):
                with self.assertRaisesRegex(RuntimeError, "free space"):
                    module.snapshot(self.root, self.revision)
        self.assertEqual(list((self.root / "deploy-backups").iterdir()), [])

    def test_after_requires_expected_release_and_completed_schema(self):
        module.snapshot(self.root, self.revision)
        with mock.patch.dict(module.os.environ, {"AEGIS_RELEASE_SHA": "b" * 40}):
            with self.assertRaisesRegex(RuntimeError, "requested release"):
                module.snapshot(self.root, self.revision, after=True)
        with mock.patch.dict(module.os.environ, {"AEGIS_RELEASE_SHA": self.revision}):
            with self.assertRaisesRegex(RuntimeError, "migrations"):
                module.snapshot(self.root, self.revision, after=True)
            self.migrated()
            result = module.snapshot(self.root, self.revision, after=True)
        self.assertTrue(result["release_verified"])
        self.assertEqual(result["preserved_active_job_count"], 3)
        self.assertEqual(result["current_jobs"]["4"]["queue_states"], ["queued"])

    def test_after_fails_changed_stable_run_identity_without_rolling_back(self):
        module.snapshot(self.root, self.revision)
        before = self.manifest()
        self.migrated()
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE upload_jobs SET run_id='replacement' WHERE id=1")
            connection.execute("UPDATE upload_jobs SET question_inventory=? WHERE id=3", (
                json.dumps({"_aegis_run_recovery_request": {"status": "blocked"}}),
            ))
        with mock.patch.dict(module.os.environ, {"AEGIS_RELEASE_SHA": self.revision}):
            with self.assertRaisesRegex(module.DeploymentIdentityError, r"changed run identity: \[1\]") as refused:
                module.snapshot(self.root, self.revision, after=True)
        result = refused.exception.report
        self.assertEqual(result["changed_run_identity_job_ids"], [1])
        self.assertEqual(result["preserved_active_job_count"], 2)
        self.assertEqual(result["current_jobs"]["3"]["recovery_status"], "blocked")
        self.assertEqual(self.manifest(), before)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT run_id FROM upload_jobs WHERE id=1").fetchone()[0], "replacement")
            connection.execute("DELETE FROM upload_jobs WHERE id=1")
        with mock.patch.dict(module.os.environ, {"AEGIS_RELEASE_SHA": self.revision}):
            with self.assertRaisesRegex(RuntimeError, "active jobs missing"):
                module.snapshot(self.root, self.revision, after=True)

    def test_after_allows_legacy_identity_initialization_and_reports_blocked_recovery(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE upload_jobs SET run_id='' WHERE id=1")
        module.snapshot(self.root, self.revision)
        self.migrated()
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE upload_jobs SET run_id='first-stable-id' WHERE id=1")
            connection.execute("UPDATE upload_jobs SET question_inventory=? WHERE id=3", (
                json.dumps({"_aegis_run_recovery_request": {"status": "blocked"}}),
            ))
        with mock.patch.dict(module.os.environ, {"AEGIS_RELEASE_SHA": self.revision}):
            result = module.snapshot(self.root, self.revision, after=True)
        self.assertEqual(result["changed_run_identity_job_ids"], [])
        self.assertEqual(result["current_jobs"]["3"]["recovery_status"], "blocked")

    def test_cli_prints_structured_identity_failure_and_exits_nonzero(self):
        module.snapshot(self.root, self.revision)
        self.migrated()
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE upload_jobs SET run_id='replacement' WHERE id=1")
            connection.execute("UPDATE upload_jobs SET question_inventory=? WHERE id=3", (
                json.dumps({"_aegis_run_recovery_request": {"status": "blocked"}}),
            ))
        output = io.StringIO()
        with mock.patch.dict(module.os.environ, {
            "AEGIS_DATA_DIR": str(self.root), "AEGIS_RELEASE_SHA": self.revision,
        }), mock.patch.object(module, "wait_for_release") as health, mock.patch("sys.stdout", output):
            with self.assertRaises(SystemExit) as stopped:
                module.main([self.revision, "--after"])
        self.assertEqual(stopped.exception.code, 1)
        health.assert_called_once_with(self.revision)
        report = json.loads(output.getvalue())
        self.assertEqual(report["changed_run_identity_job_ids"], [1])
        self.assertEqual(report["preserved_active_job_count"], 2)
        self.assertEqual(report["current_jobs"]["3"]["recovery_status"], "blocked")

    def test_legacy_nullable_json_is_safe_and_invalid_json_stops_preflight(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE upload_jobs SET run_state='null', question_inventory='[]' WHERE id=1")
        module.snapshot(self.root, self.revision)
        before = self.manifest()
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE upload_jobs SET run_state='invalid json' WHERE id=1")
        with self.assertRaises(json.JSONDecodeError):
            module.snapshot(self.root, self.revision)
        self.assertEqual(self.manifest(), before)

    def test_health_gate_checks_application_release_and_waits_for_startup(self):
        old = io.BytesIO(json.dumps({"status": "ok", "release": "old"}).encode())
        ready = io.BytesIO(json.dumps({"status": "ok", "release": self.revision}).encode())
        with mock.patch.object(module.urllib.request, "urlopen", side_effect=[old, ready]) as open_url, mock.patch.object(module.time, "sleep"):
            module.wait_for_release(self.revision)
        self.assertEqual(open_url.call_count, 2)
        with mock.patch.object(module.urllib.request, "urlopen", side_effect=OSError("unavailable")):
            with self.assertRaisesRegex(RuntimeError, "startup"):
                module.wait_for_release(self.revision, timeout=0)


if __name__ == "__main__":
    unittest.main()
