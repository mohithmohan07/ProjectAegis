"""Freeze the two-step workflow on new uploads; historical runs keep theirs."""
from contextlib import contextmanager
from contextvars import ContextVar

KEY = "reviewed_file_workflow_policy"
VERSION = "concepts-then-reviewed-masters-2026-09-11-v1"
_bound = ContextVar(KEY, default=None)

def active(value):
    return isinstance(value, dict) and (value.get(KEY) == VERSION or
        isinstance(value.get("metadata"), dict) and value["metadata"].get(KEY) == VERSION)

def fields(value):
    return {KEY: VERSION} if active(value) else {}

def run_fields():
    return {KEY: VERSION} if _bound.get() == VERSION else {}

@contextmanager
def bind_run(version):
    if version not in (None, VERSION):
        raise ValueError("Unknown saved reviewed-file workflow policy")
    token = _bound.set(version)
    try:
        yield
    finally:
        _bound.reset(token)
