import base64
import json
import threading

import pytest

from app.services import drive_checkpoints as drive


def _credential_json(private_key="very-secret-private-key"):
    return json.dumps({
        "type": "service_account",
        "project_id": "aegis-tests",
        "private_key_id": "secret-key-id",
        "private_key": private_key,
        "client_email": "checkpoint-bot@example.test",
        "client_id": "123456789",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def _settings():
    return drive.DriveCheckpointSettings.from_env({
        drive.ENABLED_ENV: "true",
        drive.FOLDER_ID_ENV: "folder_123456",
        drive.CREDENTIAL_JSON_ENV: _credential_json(),
        drive.NAMESPACE_ENV: "aegis-tests",
    })


class _Response:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class _RecordingStore:
    def __init__(self):
        self.calls = []

    def upsert(self, artifact):
        self.calls.append(artifact)
        return drive.RemoteCheckpoint(
            file_id=f"file-{artifact.job_id}",
            filename=artifact.filename,
            action="updated",
        )


def test_settings_are_disabled_without_credentials_and_accept_base64():
    disabled = drive.DriveCheckpointSettings.from_env({})

    assert disabled.enabled is False
    assert disabled.configured is False
    assert disabled.credential_json == ""

    raw = _credential_json()
    encoded = base64.b64encode(raw.encode()).decode()
    enabled = drive.DriveCheckpointSettings.from_env({
        drive.ENABLED_ENV: "1",
        drive.FOLDER_ID_ENV: "folder_123456",
        drive.CREDENTIAL_JSON_B64_ENV: encoded,
    })

    assert enabled.configured is True
    assert enabled.credential_json == raw
    assert "very-secret-private-key" not in repr(enabled)
    assert enabled.auth_mode == "service_account_shared_drive"
    assert "Shared Drive" in enabled.configuration_notice

    delegated = drive.DriveCheckpointSettings.from_env({
        drive.ENABLED_ENV: "1",
        drive.FOLDER_ID_ENV: "folder_123456",
        drive.CREDENTIAL_JSON_ENV: raw,
        drive.IMPERSONATE_USER_ENV: "drive-owner@example.test",
    })
    assert delegated.configured is True
    assert delegated.auth_mode == "domain_delegation"
    assert "drive-owner@example.test" not in repr(delegated)


def test_invalid_base64_reports_only_redacted_configuration_error():
    invalid = "not-base64-and-private-key=do-not-show"

    settings = drive.DriveCheckpointSettings.from_env({
        drive.ENABLED_ENV: "true",
        drive.FOLDER_ID_ENV: "folder_123456",
        drive.CREDENTIAL_JSON_B64_ENV: invalid,
    })

    assert settings.configured is False
    assert settings.configuration_error == (
        "Base64 service-account credentials are invalid."
    )
    assert invalid not in settings.configuration_error
    assert settings.credential_json == ""


def test_disabled_schedule_never_constructs_drive_client():
    called = False

    def unexpected_factory(_settings):
        nonlocal called
        called = True
        raise AssertionError("Drive factory must stay lazy")

    backup = drive.DriveCheckpointBackup(
        drive.DriveCheckpointSettings(),
        exporter=lambda _job_id: ("unused.json", b"{}"),
        store_factory=unexpected_factory,
    )

    result = backup.schedule(7)

    assert result.accepted is False
    assert result.state == "disabled"
    assert called is False
    assert backup.status().state == "disabled"


def test_rest_store_creates_then_uploads_one_checkpoint_file():
    session = _Session([
        _Response({
            "id": "folder_123456",
            "mimeType": "application/vnd.google-apps.folder",
            "driveId": "shared-drive-1",
            "capabilities": {"canAddChildren": True},
        }),
        _Response({"files": []}),
        _Response({"id": "drive-file-1"}),
        _Response({"id": "drive-file-1", "name": "saved.json"}),
    ])
    store = drive.DriveRestCheckpointStore(
        session,
        folder_id="folder_123456",
        namespace="aegis-tests",
    )
    artifact = drive.BackupArtifact(
        job_id=42,
        filename="saved.aegis-checkpoint.json",
        content=b'{"checkpoint":true}',
    )

    result = store.upsert(artifact)

    assert result.action == "created"
    assert result.file_id == "drive-file-1"
    assert [call[0] for call in session.calls] == [
        "GET", "GET", "POST", "PATCH",
    ]
    assert session.calls[0][1].endswith("/folder_123456")
    lookup = session.calls[1][2]["params"]["q"]
    assert "aegis_job_id" in lookup
    assert "value='42'" in lookup
    create_json = session.calls[2][2]["json"]
    assert create_json["parents"] == ["folder_123456"]
    assert create_json["appProperties"]["aegis_namespace"] == "aegis-tests"
    upload = session.calls[3]
    assert upload[2]["data"] == artifact.content
    assert upload[2]["params"]["uploadType"] == "media"


def test_rest_store_updates_existing_and_recoverably_trashes_duplicate():
    session = _Session([
        _Response({
            "id": "folder_123456",
            "mimeType": "application/vnd.google-apps.folder",
            "driveId": "shared-drive-1",
            "capabilities": {"canAddChildren": True},
        }),
        _Response({"files": [
            {"id": "newest", "name": "old.json"},
            {"id": "duplicate", "name": "duplicate.json"},
        ]}),
        _Response({"id": "newest"}),
        _Response({"id": "newest", "name": "fresh.json"}),
        _Response({"id": "duplicate", "trashed": True}),
    ])
    store = drive.DriveRestCheckpointStore(
        session,
        folder_id="folder_123456",
        namespace="aegis-tests",
    )

    result = store.upsert(drive.BackupArtifact(
        job_id=9,
        filename="fresh.aegis-checkpoint.json",
        content=b"latest",
    ))

    assert result.action == "updated"
    assert result.file_id == "newest"
    assert [call[0] for call in session.calls] == [
        "GET", "GET", "PATCH", "PATCH", "PATCH",
    ]
    assert session.calls[-1][1].endswith("/duplicate")
    assert session.calls[-1][2]["json"] == {"trashed": True}


def test_plain_service_account_mode_rejects_my_drive_folder():
    session = _Session([_Response({
        "id": "folder_123456",
        "mimeType": "application/vnd.google-apps.folder",
        "capabilities": {"canAddChildren": True},
    })])
    store = drive.DriveRestCheckpointStore(
        session,
        folder_id="folder_123456",
        namespace="aegis-tests",
        require_shared_drive=True,
    )

    with pytest.raises(RuntimeError, match="requires a Google Shared Drive"):
        store.upsert(drive.BackupArtifact(
            job_id=1,
            filename="one.aegis-checkpoint.json",
            content=b"{}",
        ))


def test_background_upload_coalesces_and_latest_committed_state_wins():
    settings = _settings()
    first_started = threading.Event()
    release_first = threading.Event()
    version = {"content": b"version-1"}

    class BlockingStore(_RecordingStore):
        def upsert(self, artifact):
            self.calls.append(artifact)
            if len(self.calls) == 1:
                first_started.set()
                assert release_first.wait(timeout=2)
            return drive.RemoteCheckpoint(
                file_id="remote",
                filename=artifact.filename,
                action="updated",
            )

    store = BlockingStore()
    backup = drive.DriveCheckpointBackup(
        settings,
        exporter=lambda job_id: (
            f"job-{job_id}.aegis-checkpoint.json",
            version["content"],
        ),
        store_factory=lambda _settings: store,
        max_attempts=1,
    )

    assert backup.schedule(5).accepted is True
    assert first_started.wait(timeout=2)
    version["content"] = b"version-2"
    assert backup.schedule(5).coalesced is True
    version["content"] = b"version-3"
    assert backup.schedule(5).coalesced is True
    release_first.set()

    assert backup.wait_until_idle(timeout=3)
    assert [item.content for item in store.calls] == [
        b"version-1", b"version-3",
    ]
    assert backup.status().state == "succeeded"
    backup.stop()


def test_drive_failures_retry_in_background_and_redact_credentials():
    settings = _settings()
    attempts = []
    delays = []
    secret = settings.credential_info()["private_key"]
    email = settings.credential_info()["client_email"]

    class FailingStore:
        def upsert(self, _artifact):
            attempts.append(1)
            raise RuntimeError(f"provider rejected {secret} for {email}")

    backup = drive.DriveCheckpointBackup(
        settings,
        exporter=lambda _job_id: ("safe.json", b"checkpoint"),
        store_factory=lambda _settings: FailingStore(),
        sleep=delays.append,
        max_attempts=3,
        initial_backoff_seconds=0.25,
    )

    result = backup.schedule(11)

    assert result.accepted is True
    assert backup.wait_until_idle(timeout=3)
    status = backup.status()
    assert len(attempts) == 3
    assert delays == [0.25, 0.5]
    assert status.state == "failed"
    assert secret not in status.error
    assert email not in status.error
    assert "[REDACTED]" in status.error
    backup.stop()


def test_startup_reconciliation_deduplicates_job_ids_and_stays_nonblocking():
    settings = _settings()
    store = _RecordingStore()
    backup = drive.DriveCheckpointBackup(
        settings,
        exporter=lambda job_id: (
            f"job-{job_id}.json", f"checkpoint-{job_id}".encode()),
        existing_job_ids=lambda: [4, 4, 8],
        store_factory=lambda _settings: store,
        max_attempts=1,
    )

    queued = backup.reconcile()

    assert queued == 2
    assert backup.wait_until_idle(timeout=3)
    assert {artifact.job_id for artifact in store.calls} == {4, 8}
    backup.stop()


def test_filename_sanitization_is_stable_bounded_and_has_one_suffix():
    filename = drive.sanitize_checkpoint_filename(
        31,
        "../../My unsafe   PDF?.aegis-checkpoint.json",
    )

    assert filename.startswith("aegis-job-31-")
    assert filename.endswith(".aegis-checkpoint.json")
    assert filename.count(".aegis-checkpoint.json") == 1
    assert "/" not in filename
    assert len(filename) <= drive.MAX_BACKUP_FILENAME_CHARS
