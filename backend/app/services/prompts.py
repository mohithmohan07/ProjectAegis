"""Central registry of every editable GPT prompt in the tool.

Each prompt has a stable ``key``, a built-in ``default`` (the text the code
shipped with), and an optional user ``override`` persisted to
``DATA_DIR/prompt_overrides.json``. The Admin tab edits overrides; every run of
any function reads its prompt through :func:`get_text` / :func:`render`, so an
edit takes effect on the very next generation — no restart needed.

Variable substitution uses ``{{name}}`` tokens (double braces) on purpose: the
prompts embed literal single-brace ``{ ... }`` JSON examples, so ``str.format``
is unusable. :func:`render` only replaces the explicit ``{{name}}`` tokens.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .. import config


@dataclass(frozen=True)
class PromptSpec:
    key: str
    label: str
    category: str
    default: str
    description: str = ""
    variables: tuple[str, ...] = ()


_REGISTRY: dict[str, PromptSpec] = {}
_lock = threading.RLock()
_overrides_cache: dict[str, str] | None = None


def _overrides_path() -> Path:
    return config.DATA_DIR / "prompt_overrides.json"


def _load_overrides() -> dict[str, str]:
    global _overrides_cache
    if _overrides_cache is not None:
        return _overrides_cache
    path = _overrides_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _overrides_cache = {str(k): str(v) for k, v in data.items()}
            else:
                _overrides_cache = {}
        except (json.JSONDecodeError, OSError):
            _overrides_cache = {}
    else:
        _overrides_cache = {}
    return _overrides_cache


def _save_overrides(data: dict[str, str]) -> None:
    global _overrides_cache
    _overrides_cache = dict(data)
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def register(
    key: str, *, label: str, category: str, default: str,
    description: str = "", variables: tuple[str, ...] = (),
) -> str:
    """Register a prompt's default. Returns the same default for convenient use
    at module import time (``X = register("key", default=...)``)."""
    with _lock:
        _REGISTRY[key] = PromptSpec(
            key=key, label=label, category=category, default=default,
            description=description, variables=tuple(variables),
        )
    return default


def get_text(key: str) -> str:
    """Return the override if set, else the registered default."""
    ov = _load_overrides()
    if key in ov:
        return ov[key]
    spec = _REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"unknown prompt key: {key!r}")
    return spec.default


def render(key: str, **variables: object) -> str:
    """Return the prompt text with ``{{name}}`` tokens substituted."""
    text = get_text(key)
    for name, value in variables.items():
        text = text.replace("{{" + name + "}}", str(value))
    return text


def is_overridden(key: str) -> bool:
    return key in _load_overrides()


def set_override(key: str, text: str) -> None:
    if key not in _REGISTRY:
        raise KeyError(f"unknown prompt key: {key!r}")
    with _lock:
        data = dict(_load_overrides())
        data[key] = text
        _save_overrides(data)


def reset(key: str) -> None:
    with _lock:
        data = dict(_load_overrides())
        if key in data:
            del data[key]
            _save_overrides(data)


def reset_all() -> None:
    with _lock:
        _save_overrides({})


def specs() -> list[PromptSpec]:
    """All registered prompts, sorted by category then key."""
    return sorted(_REGISTRY.values(), key=lambda s: (s.category, s.key))


def categories() -> list[str]:
    seen: list[str] = []
    for s in specs():
        if s.category not in seen:
            seen.append(s.category)
    return seen


def describe(key: str) -> dict:
    spec = _REGISTRY[key]
    return {
        "key": spec.key,
        "label": spec.label,
        "category": spec.category,
        "description": spec.description,
        "variables": list(spec.variables),
        "default": spec.default,
        "current": get_text(spec.key),
        "overridden": is_overridden(spec.key),
    }


def export_all() -> list[dict]:
    return [describe(s.key) for s in specs()]


def _invalidate_cache() -> None:
    """Test hook: force a reload of overrides from disk."""
    global _overrides_cache
    _overrides_cache = None
