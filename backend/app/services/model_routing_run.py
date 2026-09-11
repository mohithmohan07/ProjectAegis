"""Freeze transport settings per uploaded source before the first request.

The record lives beside the source artifact directory, outside checkpoints which the
pipeline replaces as it advances. It contains model IDs and efforts, never
credentials. Existing converted/checkpointed sources without a record retain
the historical routing path; replacing the uploaded file gets a new identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager

from .. import config
from . import canonical_source_contract, model_provider, generation_quality_policy as quality


def _record_path(job):
    identity = str(job.upload_storage_key or job.filename or "")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return canonical_source_contract._artifact_directory(job.id).parent / (
        f"source.model-routing.{digest}.json"
    )


def recorded_profile_for_job(job):
    """Read portable provenance without minting a record during export."""
    path = _record_path(job)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("version") != 1 or "profile" not in record:
        raise ValueError("invalid saved model routing record")
    profile = record["profile"]
    return None if profile is None else model_provider.validate_profile(profile)


def save_profile_for_job(job, profile, *, quality_version=None):
    """Persist an explicit run profile, including None for historical restore."""
    profile = None if profile is None else model_provider.validate_profile(profile)
    path = _record_path(job)
    record = {"version": 1, "profile": profile}
    if quality_version is not None:
        if quality_version != quality.VERSION:
            raise ValueError("Unknown saved generation quality policy")
        record[quality.KEY] = quality_version
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return profile


def profile_for_job(job):
    """Read or atomically mint one profile under the upload operation lock."""
    if _record_path(job).exists():
        return recorded_profile_for_job(job)
    historical = bool(
        job.mmd_text or job.generation_checkpoint or job.question_inventory
        or (job.openai_usage or {}).get("request_count")
    )
    return save_profile_for_job(
        job, None if historical else model_provider.new_profile(),
        quality_version=None if historical else quality.VERSION,
    )


@contextmanager
def bind_job(job, *, require_pre: bool = False):
    profile = profile_for_job(job)
    record = json.loads(_record_path(job).read_text(encoding="utf-8"))
    with model_provider.bind_profile(profile), quality.bind_run(record.get(quality.KEY)):
        if profile is not None and not config.allow_dry() and not config._live_disabled():
            providers = {
                route["provider"] for name, route in profile["routes"].items()
                if require_pre or name != "pre_question_author"
            }
            missing = []
            if "openai" in providers and not os.environ.get("OPENAI_API_KEY"):
                missing.append("OPENAI_API_KEY")
            if "gemini" in providers and not os.environ.get("GEMINI_API_KEY"):
                missing.append("GEMINI_API_KEY")
            if missing:
                raise config.LiveRequiredError(
                    "This run requires " + " and ".join(missing)
                    + ". Configure the missing server credential before starting. "
                    + ("Its recorded v1 policy uses Gemini 3.8 Flash for Pre question authoring."
                       if "gemini" in providers else "All stages in this run use OpenAI GPT-5.4 mini.")
                )
        yield profile
