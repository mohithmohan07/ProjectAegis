"""Freeze the reviewed-file workflow on new uploads; historical runs keep theirs.

Two recorded versions exist and both remain valid for replay:

* ``V1`` (Q49): Step 1 stops before Pre question generation; Step 2 reads the
  reviewed files independently. Step 1 still ran the question-polishing pass.
* ``V2`` (Q51): Step 1 additionally extracts every question AS IS — the
  question-polishing pass no longer runs before the Concept files are staged.
  Polishing runs in Step 2 over the reviewed Post questions, before the
  Master is authored.

New uploads mint ``VERSION`` (the current version). A saved record, envelope,
release payload or checkpoint carrying an older version keeps that version's
behaviour: ``fields`` echoes the recorded version, never the current one.
"""
from contextlib import contextmanager
from contextvars import ContextVar

KEY = "reviewed_file_workflow_policy"
V1 = "concepts-then-reviewed-masters-2026-09-11-v1"
V2 = "concepts-then-reviewed-masters-2026-09-11-v2"
VERSIONS = (V1, V2)
VERSION = V2
_bound = ContextVar(KEY, default=None)


def version_of(value):
    """The recorded workflow version on a payload/metadata mapping, or None."""
    if not isinstance(value, dict):
        return None
    recorded = value.get(KEY)
    if recorded in VERSIONS:
        return recorded
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and metadata.get(KEY) in VERSIONS:
        return metadata[KEY]
    return None


def active(value):
    """Whether the value records any reviewed-file workflow version."""
    return version_of(value) is not None


def defers_polishing(value):
    """Q51: Step 1 extracts questions as is; polishing belongs to Step 2."""
    return version_of(value) == V2


def fields(value):
    """Echo the recorded version so stamps never upgrade a historical run."""
    recorded = version_of(value)
    return {KEY: recorded} if recorded is not None else {}


def bound_version():
    return _bound.get()


def run_fields():
    bound = _bound.get()
    return {KEY: bound} if bound in VERSIONS else {}


def run_defers_polishing():
    """Whether the bound run (or explicit metadata) defers polishing to Step 2."""
    return _bound.get() == V2


@contextmanager
def bind_run(version):
    if version is not None and version not in VERSIONS:
        raise ValueError("Unknown saved reviewed-file workflow policy")
    token = _bound.set(version)
    try:
        yield
    finally:
        _bound.reset(token)
