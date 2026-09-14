"""What this process is holding, and what it is allowed to hold.

Two of this repository's worst production days were memory days. Job 139's
Step 2 died after ~1h39m behind a bare 502 because the reviewed-file reader
materialised a 0.48 MB workbook twice at 306 MB a time on a 2048 MB machine
(register Q57), and the cure — a streaming reader and a 4096 MB machine — was
found by measuring RSS offline, because the running machine reported nothing
at all. Q73 now widens a cohort to six concurrent chapter runs on that same
machine on the strength of a default nobody has measured.

So this module measures. It is stdlib-only, allocation-light and reads
``/proc`` once per sample, because instrumentation that costs memory to
observe memory is not instrumentation.

Nothing here judges anything. It parses two files and reports two numbers and
a ceiling — mechanics in the sense Rule 1 allows, with no bearing on what any
run produces. Read the numbers, then decide; do not wire a threshold in here
and let it decide for you.

**The logger is deliberately its own.** ``uvicorn``'s shipped ``LOGGING_CONFIG``
configures the ``uvicorn*`` loggers and leaves the root logger exactly as it
found it, so every ``logging.getLogger(__name__)`` line this codebase writes at
INFO is discarded in production. The obvious repair — ``basicConfig(INFO)`` on
root — switches on ``httpx``'s per-request line and ``openai``'s retry chatter
for all 48 concurrent provider calls, which is how you turn a memory
investigation into a log-volume incident. ``aegis.memory`` carries its own
handler and does not propagate, so exactly one family of lines is turned on.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass

#: Monkeypatchable so the parsers can be tested against a real container's
#: files without being run inside one.
_PROC_STATUS = "/proc/self/status"
_PROC_MEMINFO = "/proc/meminfo"
_PROC_CGROUP = "/proc/self/cgroup"
_CGROUP_ROOT = "/sys/fs/cgroup"

_KIB = 1024

#: The kernel's own spelling of "no limit", as a number. cgroup v1 writes
#: ``PAGE_COUNTER_MAX`` — 2**63-1 rounded down to a page — into
#: ``memory.limit_in_bytes``, and a v2 parent can carry something of the same
#: order. The ``MemTotal`` comparison in ``_resolve_limit`` already discards
#: these, but only on a machine whose ``/proc/meminfo`` can be read: without
#: this floor, a machine that answers the cgroup files and not that one prints
#: an 8 EiB ceiling on every line, which reads as limitless headroom and is
#: simply the absence of a limit. No machine has exabytes of RAM, so a
#: candidate up here is not a ceiling however it is spelled. Two machine
#: numbers compared; nothing about content is judged.
_NO_LIMIT_FLOOR = 1 << 62

_LIMIT_LOCK = threading.Lock()
_LIMIT: "MemoryLimit | None" = None

_MEMORY_LOG = logging.getLogger("aegis.memory")
_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
#: Marks the handler this module owns, so a second install is a no-op and
#: ``_reset_for_tests`` removes ours without touching anyone else's.
_HANDLER_TAG = "aegis-memory"


@dataclass(frozen=True)
class MemoryLimit:
    """The ceiling this process is measured against, and where it came from.

    ``source`` is not decoration. A limit read from ``meminfo`` on a container
    means the container has no memory cgroup and the number is the HOST's RAM
    — a reading that looks reassuring and is worthless. Naming the source is
    what lets a person tell that apart from a real 4 GiB ceiling.
    """

    limit_bytes: int | None
    source: str

    def as_dict(self) -> dict[str, int | str | None]:
        return {"limit_bytes": self.limit_bytes, "source": self.source}


@dataclass(frozen=True)
class MemorySample:
    """One reading of this process's resident set and its high-water mark.

    ``hwm_bytes`` is the field that matters after the fact: RSS at the moment
    of sampling can miss the peak entirely, and the peak is what the OOM
    killer acts on.
    """

    rss_bytes: int
    hwm_bytes: int
    limit: MemoryLimit

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "rss_bytes": self.rss_bytes,
            "hwm_bytes": self.hwm_bytes,
            **self.limit.as_dict(),
        }


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:  # noqa: BLE001 — a reading must never break its caller
        return None


def _status_field(text: str, name: str) -> int | None:
    """Pull one ``VmXxx:\t   1952 kB`` line out of an already-read status."""
    prefix = f"{name}:"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line[len(prefix):].split()
        if not parts:
            return None
        try:
            value = int(parts[0])
        except ValueError:
            return None
        unit = parts[1].lower() if len(parts) > 1 else "kb"
        if unit == "kb":
            return value * _KIB
        if unit == "mb":
            return value * _KIB * _KIB
        if unit == "b":
            return value
        return None
    return None


def sample() -> MemorySample | None:
    """This process's RSS and peak RSS, or ``None`` if they cannot be read.

    One ``open()``. ``/proc/self/status`` is generated on read, so opening it
    twice to pull two adjacent fields would both cost twice and risk reporting
    an RSS and a high-water mark from different instants — a pair that can
    contradict each other.

    Never raises: a machine without ``/proc`` (a developer's Mac, a future
    runtime) must lose its memory line and nothing else.
    """
    text = _read_text(_PROC_STATUS)
    if text is None:
        return None
    rss = _status_field(text, "VmRSS")
    hwm = _status_field(text, "VmHWM")
    if rss is None or hwm is None:
        return None
    return MemorySample(rss_bytes=rss, hwm_bytes=hwm, limit=limit())


# ---------------------------------------------------------------------------
# The ceiling
# ---------------------------------------------------------------------------

def _read_int_file(path: str) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    raw = text.strip()
    # cgroup v2 writes the literal ``max`` for "no limit"; v1 writes a
    # sentinel near 2**63, which ``_NO_LIMIT_FLOOR`` discards below. ``max``
    # is not an integer at all, so it has to be dropped here.
    try:
        return int(raw)
    except ValueError:
        return None


def mem_total_bytes() -> int | None:
    text = _read_text(_PROC_MEMINFO)
    if text is None:
        return None
    return _status_field(text, "MemTotal")


def _cgroup_paths() -> tuple[str | None, str | None]:
    """``(v2 path, v1 memory path)`` from ``/proc/self/cgroup``.

    The v1 field is a COMMA SET of co-mounted subsystems, not one name: a
    hierarchy mounted ``memory,hugetlb`` is a memory hierarchy, and matching
    the field exactly misses it. Missing it is not a harmless miss — the
    resolution below then falls through to ``meminfo`` and reports the host's
    physical RAM as though it were the container's ceiling.
    """
    text = _read_text(_PROC_CGROUP)
    if text is None:
        return None, None
    v2: str | None = None
    v1: str | None = None
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        _hierarchy, subsystems, path = parts
        if not subsystems:
            if v2 is None:
                v2 = path
            continue
        if "memory" in subsystems.split(",") and v1 is None:
            v1 = path
    return v2, v1


def _under_root(*parts: str) -> str:
    joined = _CGROUP_ROOT
    for part in parts:
        joined = os.path.join(joined, part.lstrip("/"))
    return joined


def _candidates() -> list[tuple[int | None, str]]:
    """Every ceiling this machine might be under, best evidence first.

    Namespaced first because a container normally sees its own cgroup mounted
    at the root; the ``/proc/self/cgroup`` path is the fallback for a host
    process, or for a container that mounted the whole hierarchy.
    """
    v2_path, v1_path = _cgroup_paths()
    out: list[tuple[int | None, str]] = [
        (_read_int_file(_under_root("memory.max")), "cgroup.v2"),
    ]
    if v2_path:
        out.append((
            _read_int_file(_under_root(v2_path, "memory.max")),
            "cgroup.v2.mapped",
        ))
    out.append((
        _read_int_file(_under_root("memory", "memory.limit_in_bytes")),
        "cgroup.v1",
    ))
    if v1_path:
        out.append((
            _read_int_file(
                _under_root("memory", v1_path, "memory.limit_in_bytes")),
            "cgroup.v1.mapped",
        ))
    out.append((mem_total_bytes(), "meminfo"))
    return out


def _resolve_limit() -> MemoryLimit:
    total = mem_total_bytes()
    for value, source in _candidates():
        if value is None or value <= 0:
            continue
        if value >= _NO_LIMIT_FLOOR:
            # "No limit", written as a number. Discarded without consulting
            # MemTotal, because this is the one case where MemTotal being
            # unreadable would otherwise promote the sentinel to a ceiling.
            continue
        if total is not None and value > total:
            # "No limit" is written as a number larger than the machine —
            # 2**63-4096 under v1, and under v2 a parent's limit can exceed
            # this machine's RAM. Either way it is not a ceiling; keep looking.
            continue
        return MemoryLimit(limit_bytes=value, source=source)
    return MemoryLimit(limit_bytes=None, source="unknown")


def limit() -> MemoryLimit:
    """The ceiling, resolved once per process.

    Once, because a cgroup limit does not move under a running container and
    the resolution opens up to five files. The cached answer is what every
    heartbeat line carries.
    """
    global _LIMIT

    with _LIMIT_LOCK:
        if _LIMIT is None:
            _LIMIT = _resolve_limit()
        return _LIMIT


def human_bytes(value: int | None) -> str:
    """A ceiling a person can read at a glance, for the one line per boot."""
    if value is None:
        return "unknown"
    step = float(_KIB)
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < step:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= step
    return f"{size:.1f} TiB"


# ---------------------------------------------------------------------------
# The logger
# ---------------------------------------------------------------------------

def _configured_level() -> int | None:
    """``None`` means off. Anything unreadable means the INFO default."""
    raw = os.environ.get("AEGIS_MEMORY_LOG_LEVEL", "").strip()
    if not raw:
        return logging.INFO
    if raw.lower() == "off":
        return None
    resolved = logging.getLevelName(raw.upper())
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def logger() -> logging.Logger:
    return _MEMORY_LOG


def install_logging() -> logging.Logger:
    """Give ``aegis.memory`` a handler of its own, and nobody else one.

    Idempotent, and deliberately narrow. It does not call ``basicConfig``, does
    not add a root handler and does not change any other logger's level —
    under ``uvicorn``'s shipped config root stays at WARNING with no handler,
    which is exactly what keeps ``httpx`` and ``openai`` quiet while these
    lines are on.
    """
    global _INSTALLED

    with _INSTALL_LOCK:
        level = _configured_level()
        if level is None:
            # Off: emit nothing, and still refuse to propagate, so turning the
            # beat off can never be the thing that turns root logging on.
            _MEMORY_LOG.setLevel(logging.CRITICAL + 1)
            _MEMORY_LOG.propagate = False
            _INSTALLED = True
            return _MEMORY_LOG
        _MEMORY_LOG.setLevel(level)
        _MEMORY_LOG.propagate = False
        if not _INSTALLED or not any(
            getattr(handler, "_aegis_tag", "") == _HANDLER_TAG
            for handler in _MEMORY_LOG.handlers
        ):
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"))
            handler.setLevel(level)
            handler._aegis_tag = _HANDLER_TAG  # type: ignore[attr-defined]
            _MEMORY_LOG.addHandler(handler)
        else:
            for handler in _MEMORY_LOG.handlers:
                if getattr(handler, "_aegis_tag", "") == _HANDLER_TAG:
                    handler.setLevel(level)
        _INSTALLED = True
        return _MEMORY_LOG


def _reset_for_tests() -> None:
    """Forget the resolved ceiling and uninstall the handler this module owns."""
    global _LIMIT, _INSTALLED

    with _LIMIT_LOCK:
        _LIMIT = None
    with _INSTALL_LOCK:
        for handler in list(_MEMORY_LOG.handlers):
            if getattr(handler, "_aegis_tag", "") == _HANDLER_TAG:
                _MEMORY_LOG.removeHandler(handler)
        _MEMORY_LOG.setLevel(logging.NOTSET)
        _MEMORY_LOG.propagate = True
        _INSTALLED = False
