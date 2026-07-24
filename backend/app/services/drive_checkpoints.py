"""Optional non-blocking Google Drive backups for concept checkpoints.

The module deliberately imports Google authentication packages only when the
feature is enabled.  Generation code calls :func:`schedule_checkpoint_backup`
after its database commit; all serialization, authentication, retry, and
network work then happens on a daemon thread.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from .. import models
from . import checkpoints


_LOGGER = logging.getLogger(__name__)


ENABLED_ENV = "AEGIS_DRIVE_CHECKPOINT_BACKUP_ENABLED"
FOLDER_ID_ENV = "AEGIS_DRIVE_CHECKPOINT_FOLDER_ID"
CREDENTIAL_JSON_ENV = "AEGIS_DRIVE_SERVICE_ACCOUNT_JSON"
CREDENTIAL_JSON_B64_ENV = "AEGIS_DRIVE_SERVICE_ACCOUNT_JSON_B64"
IMPERSONATE_USER_ENV = "AEGIS_DRIVE_IMPERSONATE_USER"
NAMESPACE_ENV = "AEGIS_DRIVE_CHECKPOINT_NAMESPACE"

# Full Drive scope is intentional for a service identity addressing a
# pre-existing Shared Drive folder. The service account should be restricted
# through Shared Drive membership. Domain-wide delegation is optional below.
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

MAX_CREDENTIAL_CHARS = 128_000
MAX_PENDING_JOBS = 1_000
MAX_BACKUP_FILENAME_CHARS = 180
_FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


@dataclass(frozen=True)
class DriveCheckpointSettings:
    enabled: bool = False
    folder_id: str = ""
    namespace: str = "projectaegis"
    credential_json: str = field(default="", repr=False)
    impersonate_user: str = field(default="", repr=False)
    configuration_error: str = ""

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and not self.configuration_error
            and self.folder_id
            and self.credential_json
        )

    @property
    def auth_mode(self) -> str:
        return (
            "domain_delegation"
            if self.impersonate_user
            else "service_account_shared_drive"
        )

    @property
    def configuration_notice(self) -> str:
        if self.impersonate_user:
            return (
                "Domain-wide delegation is configured for a Workspace user; "
                "credentials and folder access are verified on backup."
            )
        return (
            "Plain service-account mode is configured for a Google Shared "
            "Drive folder; credentials and folder access are verified on "
            "backup, and a My Drive folder is not supported."
        )

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None,
    ) -> "DriveCheckpointSettings":
        values = os.environ if environ is None else environ
        enabled = _truthy(values.get(ENABLED_ENV, ""))
        if not enabled:
            return cls(enabled=False)

        folder_id = str(values.get(FOLDER_ID_ENV, "") or "").strip()
        namespace = str(
            values.get(NAMESPACE_ENV, "projectaegis") or "projectaegis"
        ).strip()
        raw_json = str(values.get(CREDENTIAL_JSON_ENV, "") or "").strip()
        encoded_json = str(
            values.get(CREDENTIAL_JSON_B64_ENV, "") or "").strip()
        impersonate_user = str(
            values.get(IMPERSONATE_USER_ENV, "") or "").strip()

        if not folder_id:
            return cls(
                enabled=True,
                configuration_error="Google Drive checkpoint folder ID is missing.",
            )
        if not _FOLDER_ID_RE.fullmatch(folder_id):
            return cls(
                enabled=True,
                configuration_error="Google Drive checkpoint folder ID is invalid.",
            )
        if not _NAMESPACE_RE.fullmatch(namespace):
            return cls(
                enabled=True,
                configuration_error="Google Drive checkpoint namespace is invalid.",
            )
        if impersonate_user and (
            len(impersonate_user) > 254
            or not _EMAIL_RE.fullmatch(impersonate_user)
        ):
            return cls(
                enabled=True,
                folder_id=folder_id,
                namespace=namespace,
                configuration_error=(
                    "Google Drive impersonation user is invalid."
                ),
            )
        if raw_json and encoded_json:
            return cls(
                enabled=True,
                folder_id=folder_id,
                namespace=namespace,
                impersonate_user=impersonate_user,
                configuration_error=(
                    "Set either raw or base64 service-account JSON, not both."
                ),
            )
        if not raw_json and not encoded_json:
            return cls(
                enabled=True,
                folder_id=folder_id,
                namespace=namespace,
                impersonate_user=impersonate_user,
                configuration_error=(
                    "Google Drive service-account credentials are missing."
                ),
            )
        if encoded_json:
            if len(encoded_json) > MAX_CREDENTIAL_CHARS:
                return cls(
                    enabled=True,
                    folder_id=folder_id,
                    namespace=namespace,
                    impersonate_user=impersonate_user,
                    configuration_error=(
                        "Base64 service-account credentials exceed the size limit."
                    ),
                )
            try:
                raw_json = base64.b64decode(
                    encoded_json, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                del exc
                return cls(
                    enabled=True,
                    folder_id=folder_id,
                    namespace=namespace,
                    impersonate_user=impersonate_user,
                    configuration_error=(
                        "Base64 service-account credentials are invalid."
                    ),
                )
        if len(raw_json) > MAX_CREDENTIAL_CHARS:
            return cls(
                enabled=True,
                folder_id=folder_id,
                namespace=namespace,
                impersonate_user=impersonate_user,
                configuration_error=(
                    "Service-account credentials exceed the size limit."
                ),
            )
        credential_error = _credential_error(raw_json)
        return cls(
            enabled=True,
            folder_id=folder_id,
            namespace=namespace,
            credential_json=raw_json if not credential_error else "",
            impersonate_user=impersonate_user,
            configuration_error=credential_error,
        )

    def credential_info(self) -> dict[str, Any]:
        """Return parsed credentials internally; never include this in status."""
        return json.loads(self.credential_json)

    def secret_fragments(self) -> list[str]:
        fragments = [self.credential_json, self.impersonate_user]
        try:
            info = self.credential_info()
        except (TypeError, ValueError, json.JSONDecodeError):
            return [fragment for fragment in fragments if fragment]
        for key in (
            "private_key", "private_key_id", "client_email",
            "client_id", "project_id",
        ):
            value = info.get(key)
            if isinstance(value, str) and len(value) >= 4:
                fragments.append(value)
        return [fragment for fragment in fragments if fragment]


@dataclass(frozen=True)
class BackupArtifact:
    job_id: int
    filename: str
    content: bytes


@dataclass(frozen=True)
class RemoteCheckpoint:
    file_id: str
    filename: str
    action: str


@dataclass(frozen=True)
class BackupScheduleResult:
    accepted: bool
    state: str
    job_id: int | None = None
    coalesced: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupStatus:
    enabled: bool
    configured: bool
    state: str
    auth_mode: str = ""
    notice: str = ""
    pending_jobs: int = 0
    in_flight_job_id: int | None = None
    last_job_id: int | None = None
    last_file_id: str = ""
    last_filename: str = ""
    last_action: str = ""
    last_attempt_at: str = ""
    last_success_at: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointStore(Protocol):
    def upsert(self, artifact: BackupArtifact) -> RemoteCheckpoint: ...


class DriveRestCheckpointStore:
    """Small Drive v3 REST adapter backed by google-auth AuthorizedSession."""

    def __init__(
        self,
        session: Any,
        *,
        folder_id: str,
        namespace: str,
        require_shared_drive: bool = True,
        request_timeout: tuple[float, float] = (5.0, 30.0),
    ) -> None:
        self._session = session
        self._folder_id = folder_id
        self._namespace = namespace
        self._require_shared_drive = require_shared_drive
        self._request_timeout = request_timeout
        self._folder_validated = False
        self._shared_drive_id = ""

    def upsert(self, artifact: BackupArtifact) -> RemoteCheckpoint:
        self._validate_folder()
        files = self._find_files(artifact.job_id)
        metadata = {
            "name": artifact.filename,
            "mimeType": "application/json",
            "appProperties": {
                "aegis_kind": "concept_checkpoint",
                "aegis_namespace": self._namespace,
                "aegis_job_id": str(artifact.job_id),
            },
        }
        if files:
            file_id = files[0]["id"]
            self._request_json(
                "PATCH",
                f"{DRIVE_FILES_URL}/{file_id}",
                operation="metadata update",
                params={
                    "fields": "id,name",
                    "supportsAllDrives": "true",
                },
                json=metadata,
            )
            action = "updated"
        else:
            created = self._request_json(
                "POST",
                DRIVE_FILES_URL,
                operation="file create",
                params={
                    "fields": "id,name",
                    "supportsAllDrives": "true",
                },
                json={**metadata, "parents": [self._folder_id]},
            )
            file_id = str(created.get("id") or "")
            if not file_id:
                raise RuntimeError(
                    "Google Drive file create returned no file ID")
            action = "created"

        uploaded = self._request_json(
            "PATCH",
            f"{DRIVE_UPLOAD_URL}/{file_id}",
            operation="content upload",
            params={
                "uploadType": "media",
                "fields": "id,name,modifiedTime",
                "supportsAllDrives": "true",
            },
            data=artifact.content,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        name = str(uploaded.get("name") or artifact.filename)

        # A prior multi-worker race may have made duplicates. Only files tagged
        # by this service with the same namespace/job are recoverably trashed.
        for duplicate in files[1:]:
            try:
                self._request_json(
                    "PATCH",
                    f"{DRIVE_FILES_URL}/{duplicate['id']}",
                    operation="duplicate cleanup",
                    params={"supportsAllDrives": "true"},
                    json={"trashed": True},
                )
            except Exception:
                pass
        return RemoteCheckpoint(
            file_id=str(uploaded.get("id") or file_id),
            filename=name,
            action=action,
        )

    def _validate_folder(self) -> None:
        if self._folder_validated:
            return
        metadata = self._request_json(
            "GET",
            f"{DRIVE_FILES_URL}/{self._folder_id}",
            operation="folder validation",
            params={
                "fields": "id,name,mimeType,driveId,capabilities(canAddChildren)",
                "supportsAllDrives": "true",
            },
        )
        if metadata.get("mimeType") != "application/vnd.google-apps.folder":
            raise RuntimeError(
                "Configured Google Drive checkpoint target is not a folder")
        if self._require_shared_drive and not metadata.get("driveId"):
            raise RuntimeError(
                "Configured checkpoint folder is in My Drive; plain "
                "service-account mode requires a Google Shared Drive folder"
            )
        capabilities = metadata.get("capabilities")
        if (
            isinstance(capabilities, dict)
            and capabilities.get("canAddChildren") is False
        ):
            raise RuntimeError(
                "Google Drive identity cannot add files to the checkpoint folder"
            )
        self._shared_drive_id = str(metadata.get("driveId") or "")
        self._folder_validated = True

    def _find_files(self, job_id: int) -> list[dict[str, str]]:
        folder = _drive_query_value(self._folder_id)
        namespace = _drive_query_value(self._namespace)
        job = _drive_query_value(str(job_id))
        params: dict[str, Any] = {
            "q": (
                f"trashed = false and '{folder}' in parents and "
                "appProperties has { key='aegis_kind' and "
                "value='concept_checkpoint' } and "
                "appProperties has { key='aegis_namespace' and "
                f"value='{namespace}' }} and "
                "appProperties has { key='aegis_job_id' and "
                f"value='{job}' }}"
            ),
            "spaces": "drive",
            "orderBy": "modifiedTime desc",
            "pageSize": 100,
            "fields": "files(id,name,modifiedTime)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if self._shared_drive_id:
            params.update({
                "corpora": "drive",
                "driveId": self._shared_drive_id,
            })
        result = self._request_json(
            "GET",
            DRIVE_FILES_URL,
            operation="file lookup",
            params=params,
        )
        raw_files = result.get("files")
        if not isinstance(raw_files, list):
            raise RuntimeError("Google Drive file lookup returned invalid data")
        files: list[dict[str, str]] = []
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("id") or "")
            if file_id:
                files.append({
                    "id": file_id,
                    "name": str(item.get("name") or ""),
                })
        return files

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            url,
            timeout=self._request_timeout,
            **kwargs,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(
                f"Google Drive {operation} returned HTTP {status_code or 'unknown'}"
            )
        try:
            result = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Google Drive {operation} returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Google Drive {operation} returned invalid data")
        return result


class DriveCheckpointBackup:
    """A latest-wins queue with one bounded-retry daemon worker."""

    def __init__(
        self,
        settings: DriveCheckpointSettings,
        *,
        exporter: Callable[[int], tuple[str, bytes]],
        existing_job_ids: Callable[[], Iterable[int]] | None = None,
        store_factory: Callable[
            [DriveCheckpointSettings], CheckpointStore
        ] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
        initial_backoff_seconds: float = 0.5,
        maximum_backoff_seconds: float = 4.0,
    ) -> None:
        self.settings = settings
        self._exporter = exporter
        self._existing_job_ids = existing_job_ids
        self._store_factory = store_factory or _default_store_factory
        self._sleep = sleep
        self._max_attempts = max(1, min(int(max_attempts), 5))
        self._initial_backoff = max(0.0, float(initial_backoff_seconds))
        self._maximum_backoff = max(
            self._initial_backoff, float(maximum_backoff_seconds))

        self._condition = threading.Condition()
        self._pending: dict[int, None] = {}
        self._in_flight: int | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._store: CheckpointStore | None = None
        self._state = (
            "disabled"
            if not settings.enabled
            else "misconfigured"
            if not settings.configured
            else "idle"
        )
        self._last_job_id: int | None = None
        self._last_file_id = ""
        self._last_filename = ""
        self._last_action = ""
        self._last_attempt_at = ""
        self._last_success_at = ""
        self._error = settings.configuration_error

    def schedule(self, job_id: int) -> BackupScheduleResult:
        """Enqueue and return immediately; this method never raises."""
        try:
            if isinstance(job_id, bool) or int(job_id) < 1:
                return BackupScheduleResult(
                    accepted=False,
                    state="rejected",
                    error="Checkpoint backup requires a positive job ID.",
                )
            normalized_job_id = int(job_id)
            with self._condition:
                if not self.settings.enabled:
                    return BackupScheduleResult(
                        accepted=False,
                        state="disabled",
                        job_id=normalized_job_id,
                        error="Google Drive checkpoint backup is disabled.",
                    )
                if not self.settings.configured:
                    return BackupScheduleResult(
                        accepted=False,
                        state="misconfigured",
                        job_id=normalized_job_id,
                        error=self.settings.configuration_error,
                    )
                if self._stopping:
                    return BackupScheduleResult(
                        accepted=False,
                        state="stopped",
                        job_id=normalized_job_id,
                        error="Google Drive checkpoint backup is stopped.",
                    )
                coalesced = (
                    normalized_job_id in self._pending
                    or normalized_job_id == self._in_flight
                )
                if (
                    normalized_job_id not in self._pending
                    and len(self._pending) >= MAX_PENDING_JOBS
                ):
                    return BackupScheduleResult(
                        accepted=False,
                        state="busy",
                        job_id=normalized_job_id,
                        error="Google Drive checkpoint backup queue is full.",
                    )
                # Reassigning preserves one pending entry per job. If a job is
                # currently uploading this creates exactly one follow-up read
                # of the latest committed database state.
                self._pending[normalized_job_id] = None
                self._state = "pending"
                self._ensure_worker_locked()
                self._condition.notify_all()
                return BackupScheduleResult(
                    accepted=True,
                    state="coalesced" if coalesced else "queued",
                    job_id=normalized_job_id,
                    coalesced=coalesced,
                )
        except Exception as exc:
            return BackupScheduleResult(
                accepted=False,
                state="failed",
                error=_redacted_error(exc, self.settings),
            )

    def reconcile(self) -> int:
        """Queue locally saved checkpoints at startup without contacting Drive."""
        if not self.settings.configured or self._existing_job_ids is None:
            return 0
        try:
            job_ids = {
                int(job_id)
                for job_id in self._existing_job_ids()
                if not isinstance(job_id, bool) and int(job_id) > 0
            }
        except Exception as exc:
            with self._condition:
                self._state = "failed"
                self._error = _redacted_error(exc, self.settings)
            return 0
        return sum(1 for job_id in job_ids if self.schedule(job_id).accepted)

    def status(self) -> BackupStatus:
        with self._condition:
            return BackupStatus(
                enabled=self.settings.enabled,
                configured=self.settings.configured,
                state=self._state,
                auth_mode=self.settings.auth_mode,
                notice=self.settings.configuration_notice,
                pending_jobs=len(self._pending),
                in_flight_job_id=self._in_flight,
                last_job_id=self._last_job_id,
                last_file_id=self._last_file_id,
                last_filename=self._last_filename,
                last_action=self._last_action,
                last_attempt_at=self._last_attempt_at,
                last_success_at=self._last_success_at,
                error=self._error,
            )

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._pending or self._in_flight is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(remaining, 0.1))
            return True

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        with self._condition:
            self._stopping = True
            self._pending.clear()
            self._condition.notify_all()
            thread = self._thread
        if wait and thread is not None:
            thread.join(timeout=max(0.0, timeout))
        with self._condition:
            self._state = "stopped"

    def _ensure_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker,
            name="aegis-drive-checkpoint-backup",
            daemon=True,
        )
        self._thread.start()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    self._in_flight = None
                    self._condition.notify_all()
                    return
                job_id = next(iter(self._pending))
                self._pending.pop(job_id, None)
                self._in_flight = job_id
                self._state = "uploading"
                self._last_attempt_at = _now()
                self._error = ""

            remote: RemoteCheckpoint | None = None
            error = ""
            try:
                filename, content = self._exporter(job_id)
                artifact = BackupArtifact(
                    job_id=job_id,
                    filename=sanitize_checkpoint_filename(job_id, filename),
                    content=bytes(content),
                )
                remote = self._upload_with_retry(artifact)
            except Exception as exc:
                error = _redacted_error(exc, self.settings)

            with self._condition:
                self._last_job_id = job_id
                if remote is not None:
                    self._state = "succeeded"
                    self._last_file_id = remote.file_id
                    self._last_filename = remote.filename
                    self._last_action = remote.action
                    self._last_success_at = _now()
                    self._error = ""
                    _LOGGER.info(
                        "Google Drive checkpoint backup succeeded "
                        "(job_id=%s, action=%s, filename=%s).",
                        job_id,
                        remote.action,
                        remote.filename,
                    )
                else:
                    self._state = "failed"
                    self._error = error or "Google Drive checkpoint backup failed."
                    _LOGGER.warning(
                        "Google Drive checkpoint backup failed "
                        "(job_id=%s): %s",
                        job_id,
                        self._error,
                    )
                self._in_flight = None
                self._condition.notify_all()

    def _upload_with_retry(
        self, artifact: BackupArtifact,
    ) -> RemoteCheckpoint:
        delay = self._initial_backoff
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                if self._store is None:
                    self._store = self._store_factory(self.settings)
                return self._store.upsert(artifact)
            except Exception as exc:
                last_error = exc
                # Rebuild authentication/session on the next attempt.
                self._store = None
                if attempt < self._max_attempts and delay > 0:
                    self._sleep(delay)
                    delay = min(
                        max(delay * 2, self._initial_backoff),
                        self._maximum_backoff,
                    )
        assert last_error is not None
        raise last_error


def sanitize_checkpoint_filename(job_id: int, original: str) -> str:
    name = Path(str(original or "")).name
    suffix = ".aegis-checkpoint.json"
    stem = name[:-len(suffix)] if name.lower().endswith(suffix) else Path(name).stem
    safe_stem = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in stem
    )
    safe_stem = re.sub(r"-+", "-", safe_stem).strip("-_")[:100]
    if not safe_stem:
        safe_stem = "checkpoint"
    prefix = f"aegis-job-{int(job_id)}-{safe_stem}"
    return prefix[:MAX_BACKUP_FILENAME_CHARS - len(suffix)] + suffix


def make_database_exporter(
    session_factory: Callable[[], Session],
) -> Callable[[int], tuple[str, bytes]]:
    def export(job_id: int) -> tuple[str, bytes]:
        db = session_factory()
        try:
            return checkpoints.export_bundle_for_internal_backup(db, job_id)
        finally:
            db.close()
    return export


def make_existing_checkpoint_provider(
    session_factory: Callable[[], Session],
) -> Callable[[], list[int]]:
    def job_ids() -> list[int]:
        db = session_factory()
        try:
            rows = db.query(models.UploadJob).filter(
                models.UploadJob.module == "build_concepts"
            ).all()
            return [
                row.id
                for row in rows
                if isinstance(row.generation_checkpoint, dict)
                and row.generation_checkpoint.get("stage")
                and (row.mmd_text or "").strip()
            ]
        finally:
            db.close()
    return job_ids


_default_lock = threading.Lock()
_default_backup: DriveCheckpointBackup | None = None


def initialize_checkpoint_backup(
    session_factory: Callable[[], Session],
    *,
    environ: Mapping[str, str] | None = None,
    store_factory: Callable[
        [DriveCheckpointSettings], CheckpointStore
    ] | None = None,
    reconcile_existing: bool = True,
) -> BackupStatus:
    """Startup hook: configure the singleton and optionally queue saved jobs."""
    global _default_backup
    settings = DriveCheckpointSettings.from_env(environ)
    if settings.enabled and not settings.configured:
        _LOGGER.warning(
            "Google Drive checkpoint backup is misconfigured: %s",
            settings.configuration_error,
        )
    backup = DriveCheckpointBackup(
        settings,
        exporter=make_database_exporter(session_factory),
        existing_job_ids=make_existing_checkpoint_provider(session_factory),
        store_factory=store_factory,
    )
    with _default_lock:
        previous, _default_backup = _default_backup, backup
    if previous is not None:
        previous.stop(wait=False)
    if reconcile_existing:
        backup.reconcile()
    return backup.status()


def schedule_checkpoint_backup(job_id: int) -> BackupScheduleResult:
    """Post-commit hook. Safe to call even before initialization."""
    with _default_lock:
        backup = _default_backup
    if backup is None:
        result = BackupScheduleResult(
            accepted=False,
            state="disabled",
            job_id=job_id if isinstance(job_id, int) else None,
            error="Google Drive checkpoint backup is not initialized.",
        )
        _LOGGER.warning(
            "Google Drive checkpoint backup schedule rejected "
            "(job_id=%s, state=%s): %s",
            result.job_id,
            result.state,
            result.error,
        )
        return result
    result = backup.schedule(job_id)
    if not result.accepted and result.state != "disabled":
        _LOGGER.warning(
            "Google Drive checkpoint backup schedule rejected "
            "(job_id=%s, state=%s): %s",
            result.job_id,
            result.state,
            _redacted_error(RuntimeError(result.error), backup.settings),
        )
    return result


def checkpoint_backup_status() -> BackupStatus:
    with _default_lock:
        backup = _default_backup
    if backup is None:
        return BackupStatus(
            enabled=False,
            configured=False,
            state="disabled",
            auth_mode="",
            notice=(
                "Plain service-account mode requires a Google Shared Drive "
                "folder unless Workspace domain-wide delegation is configured."
            ),
            error="Google Drive checkpoint backup is not initialized.",
        )
    return backup.status()


def shutdown_checkpoint_backup(
    *, wait: bool = True, timeout: float = 5.0,
) -> None:
    global _default_backup
    with _default_lock:
        backup, _default_backup = _default_backup, None
    if backup is not None:
        backup.stop(wait=wait, timeout=timeout)


def _default_store_factory(
    settings: DriveCheckpointSettings,
) -> CheckpointStore:
    """Lazy import keeps disabled/local operation dependency-free."""
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive checkpoint dependencies are not installed"
        ) from exc
    credentials = Credentials.from_service_account_info(
        settings.credential_info(),
        scopes=[DRIVE_SCOPE],
    )
    if settings.impersonate_user:
        credentials = credentials.with_subject(settings.impersonate_user)
    session = AuthorizedSession(credentials)
    return DriveRestCheckpointStore(
        session,
        folder_id=settings.folder_id,
        namespace=settings.namespace,
        require_shared_drive=not bool(settings.impersonate_user),
    )


def _credential_error(raw_json: str) -> str:
    try:
        info = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "Service-account credentials are not valid JSON."
    if not isinstance(info, dict) or info.get("type") != "service_account":
        return "Service-account credentials have an invalid type."
    missing = [
        key for key in ("client_email", "private_key", "token_uri")
        if not isinstance(info.get(key), str) or not info[key].strip()
    ]
    if missing:
        return "Service-account credentials are missing required fields."
    return ""


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _drive_query_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _redacted_error(
    error: Exception,
    settings: DriveCheckpointSettings,
) -> str:
    message = str(error) or error.__class__.__name__
    for fragment in sorted(
        settings.secret_fragments(), key=len, reverse=True,
    ):
        message = message.replace(fragment, "[REDACTED]")
    message = re.sub(
        r"-----BEGIN [^-]+-----.*?-----END [^-]+-----",
        "[REDACTED]",
        message,
        flags=re.DOTALL,
    )
    message = re.sub(
        r"(?i)\b(?:bearer|token|private[_ -]?key)\s*[:=]\s*\S+",
        "[REDACTED]",
        message,
    )
    message = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED-EMAIL]",
        message,
    )
    return f"{error.__class__.__name__}: {message}"[:600]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
