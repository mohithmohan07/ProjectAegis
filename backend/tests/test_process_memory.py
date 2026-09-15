"""Reading this process's memory, and the ceiling it is measured against.

Two production incidents make this worth a test module rather than a glance.
Job 139's Step 2 died behind a bare 502 because the box ran out of memory and
said nothing about it (register Q57), and Q73 has since widened a cohort to six
concurrent chapter runs on the same box on the strength of an unmeasured
default. The numbers this module reports are what turn that default into a
measurement, so the parsers had better be right about a real container — and
right in the direction of admitting they do not know, rather than reporting a
comfortable number that is not the ceiling at all.

Nothing here judges content. These are file parsers and a logging handler.
"""
from __future__ import annotations

import io
import logging
import sys

import pytest

from app.services import process_memory


@pytest.fixture(autouse=True)
def clean_memory_module():
    """Every test starts with no cached ceiling and no installed handler."""
    process_memory._reset_for_tests()
    yield
    process_memory._reset_for_tests()


def _write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


_STATUS = """Name:\tpython3
State:\tS (sleeping)
Threads:\t9
VmPeak:\t 1200000 kB
VmSize:\t 1100000 kB
VmHWM:\t  412528 kB
VmRSS:\t  309112 kB
VmData:\t  200000 kB
"""


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #

def test_a_sample_reports_rss_and_the_high_water_mark_in_bytes(tmp_path, monkeypatch):
    """The peak is the number that matters after the fact: RSS at the moment
    of sampling routinely misses the spike the OOM killer acted on."""
    monkeypatch.setattr(process_memory, "_PROC_STATUS",
                        _write(tmp_path, "status", _STATUS))
    monkeypatch.setattr(process_memory, "_PROC_MEMINFO",
                        _write(tmp_path, "meminfo", "MemTotal: 4194304 kB\n"))
    monkeypatch.setattr(process_memory, "_PROC_CGROUP",
                        _write(tmp_path, "cgroup", "0::/\n"))
    monkeypatch.setattr(process_memory, "_CGROUP_ROOT", str(tmp_path / "nope"))

    reading = process_memory.sample()
    assert reading is not None
    assert reading.rss_bytes == 309112 * 1024
    assert reading.hwm_bytes == 412528 * 1024
    assert reading.as_dict()["rss_bytes"] == 309112 * 1024


def test_the_status_file_is_opened_exactly_once_per_sample(tmp_path, monkeypatch):
    """``/proc/self/status`` is generated on read. Opening it twice to pull two
    adjacent fields costs twice AND can return an RSS and a high-water mark
    from different instants — a pair that can contradict each other."""
    path = _write(tmp_path, "status", _STATUS)
    monkeypatch.setattr(process_memory, "_PROC_STATUS", path)
    monkeypatch.setattr(process_memory, "_PROC_MEMINFO",
                        _write(tmp_path, "meminfo", "MemTotal: 4194304 kB\n"))
    monkeypatch.setattr(process_memory, "_PROC_CGROUP",
                        _write(tmp_path, "cgroup", "0::/\n"))
    monkeypatch.setattr(process_memory, "_CGROUP_ROOT", str(tmp_path / "nope"))

    opened: list[str] = []
    real_open = io.open

    def counting_open(file, *args, **kwargs):
        if file == path:
            opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)
    process_memory.sample()
    assert opened == [path]


def test_an_unreadable_status_returns_none_and_never_raises(monkeypatch):
    """A machine with no ``/proc`` — a developer's Mac, a future runtime —
    loses its memory line and nothing else. Instrumentation that can take
    down the heartbeat thread is worse than no instrumentation."""
    monkeypatch.setattr(process_memory, "_PROC_STATUS", "/does/not/exist")
    assert process_memory.sample() is None


def test_a_status_without_the_fields_is_not_half_reported(tmp_path, monkeypatch):
    """Half a reading is a wrong reading: a zero RSS beside a real peak would
    read as a process that just released a gigabyte."""
    monkeypatch.setattr(process_memory, "_PROC_STATUS",
                        _write(tmp_path, "status", "Name:\tpython3\nVmRSS:\t 10 kB\n"))
    assert process_memory.sample() is None


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #

def _fake_machine(tmp_path, monkeypatch, *, cgroup: str, mem_total_kb: int = 16000000):
    monkeypatch.setattr(process_memory, "_PROC_STATUS",
                        _write(tmp_path, "status", _STATUS))
    monkeypatch.setattr(process_memory, "_PROC_MEMINFO",
                        _write(tmp_path, "meminfo", f"MemTotal: {mem_total_kb} kB\n"))
    monkeypatch.setattr(process_memory, "_PROC_CGROUP",
                        _write(tmp_path, "cgroup", cgroup))
    root = tmp_path / "cgroupfs"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(process_memory, "_CGROUP_ROOT", str(root))
    return root


def test_a_namespaced_v2_limit_is_the_first_answer(tmp_path, monkeypatch):
    root = _fake_machine(tmp_path, monkeypatch, cgroup="0::/\n")
    (root / "memory.max").write_text("4294967296\n")
    bound = process_memory.limit()
    assert bound.limit_bytes == 4294967296
    assert bound.source == "cgroup.v2"


def test_a_v1_hierarchy_co_mounted_with_another_subsystem_is_still_found(
    tmp_path, monkeypatch,
):
    """``/proc/self/cgroup`` writes the v1 subsystem field as a COMMA SET, and
    a kernel that co-mounted ``memory,hugetlb`` writes exactly that. Matching
    the field for equality misses it — and the miss is not harmless: the
    resolution then falls through to ``MemTotal`` and reports the HOST's RAM
    as though it were this container's ceiling, which is the one wrong answer
    that looks reassuring."""
    root = _fake_machine(
        tmp_path, monkeypatch,
        cgroup="9:name=systemd:/\n4:memory,hugetlb:/aegis/app\n0::/\n",
    )
    limits = root / "memory" / "aegis" / "app"
    limits.mkdir(parents=True)
    (limits / "memory.limit_in_bytes").write_text("4294967296\n")

    bound = process_memory.limit()
    assert bound.limit_bytes == 4294967296
    assert bound.source == "cgroup.v1.mapped"


def test_a_v2_path_from_proc_is_used_when_the_root_is_not_namespaced(
    tmp_path, monkeypatch,
):
    root = _fake_machine(tmp_path, monkeypatch, cgroup="0::/aegis/app\n")
    limits = root / "aegis" / "app"
    limits.mkdir(parents=True)
    (limits / "memory.max").write_text("2147483648\n")

    bound = process_memory.limit()
    assert bound.limit_bytes == 2147483648
    assert bound.source == "cgroup.v2.mapped"


def test_the_literal_max_is_not_a_ceiling(tmp_path, monkeypatch):
    """cgroup v2 writes ``max`` for "no limit". Reported as a number it would
    be a ceiling of zero or a crash; skipped, the answer falls to MemTotal."""
    root = _fake_machine(tmp_path, monkeypatch, cgroup="0::/\n", mem_total_kb=4000000)
    (root / "memory.max").write_text("max\n")

    bound = process_memory.limit()
    assert bound.limit_bytes == 4000000 * 1024
    assert bound.source == "meminfo"


def test_a_limit_larger_than_the_machine_is_discarded(tmp_path, monkeypatch):
    """cgroup v1 writes a sentinel near 2**63 for "no limit", and a v2 parent
    can carry a limit larger than this machine's RAM. Neither is a ceiling
    this process can actually reach, and reporting one would make every
    heartbeat line say there is headroom that does not exist."""
    root = _fake_machine(tmp_path, monkeypatch, cgroup="4:memory:/\n",
                         mem_total_kb=4000000)
    memory = root / "memory"
    memory.mkdir()
    (memory / "memory.limit_in_bytes").write_text("9223372036854771712\n")

    bound = process_memory.limit()
    assert bound.source == "meminfo"
    assert bound.limit_bytes == 4000000 * 1024


def test_the_no_limit_sentinel_is_discarded_even_with_no_meminfo(
    tmp_path, monkeypatch,
):
    """The MemTotal comparison above is the usual guard, and it is only as
    available as ``/proc/meminfo``. A machine that answers the cgroup files and
    not that one would otherwise accept the v1 sentinel and print an 8 EiB
    ceiling on every heartbeat line — a number that reads as limitless
    headroom and is simply the kernel's word for "no limit at all"."""
    monkeypatch.setattr(process_memory, "_PROC_MEMINFO", "/does/not/exist")
    monkeypatch.setattr(process_memory, "_PROC_CGROUP",
                        _write(tmp_path, "cgroup", "4:memory:/\n"))
    root = tmp_path / "cgroupfs"
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "memory.limit_in_bytes").write_text(
        "9223372036854771712\n")
    monkeypatch.setattr(process_memory, "_CGROUP_ROOT", str(root))

    bound = process_memory.limit()
    assert bound.limit_bytes is None, process_memory.human_bytes(
        bound.limit_bytes)
    assert bound.source == "unknown"


def test_a_non_positive_limit_is_discarded(tmp_path, monkeypatch):
    root = _fake_machine(tmp_path, monkeypatch, cgroup="0::/\n", mem_total_kb=4000000)
    (root / "memory.max").write_text("0\n")
    assert process_memory.limit().source == "meminfo"


def test_a_machine_that_answers_nothing_says_unknown(tmp_path, monkeypatch):
    """An honest "I do not know" beats a number nobody can act on. ``source``
    is what tells the two apart on the boot line."""
    monkeypatch.setattr(process_memory, "_PROC_MEMINFO", "/does/not/exist")
    monkeypatch.setattr(process_memory, "_PROC_CGROUP", "/does/not/exist")
    monkeypatch.setattr(process_memory, "_CGROUP_ROOT", str(tmp_path / "nope"))

    bound = process_memory.limit()
    assert bound.limit_bytes is None
    assert bound.source == "unknown"
    assert process_memory.human_bytes(None) == "unknown"


def test_the_ceiling_is_resolved_once_per_process(tmp_path, monkeypatch):
    """Up to five file reads. A cgroup limit does not move under a running
    container, and the heartbeat carries this on every line."""
    root = _fake_machine(tmp_path, monkeypatch, cgroup="0::/\n")
    (root / "memory.max").write_text("4294967296\n")
    first = process_memory.limit()

    (root / "memory.max").write_text("1\n")
    assert process_memory.limit() is first
    process_memory._reset_for_tests()
    assert process_memory.limit().limit_bytes != 4294967296


def test_human_bytes_is_readable_at_the_scales_this_machine_runs_at():
    assert process_memory.human_bytes(4 * 1024 ** 3) == "4.0 GiB"
    assert process_memory.human_bytes(2048 * 1024 ** 2) == "2.0 GiB"
    assert process_memory.human_bytes(512) == "512 B"
    assert process_memory.human_bytes(1024) == "1.0 KiB"
    # The top of the ladder has no next unit to promote to, so it must carry
    # everything above it rather than fall off the end unformatted.
    assert process_memory.human_bytes(3 * 1024 ** 4) == "3.0 TiB"
    assert process_memory.human_bytes(4096 * 1024 ** 4) == "4096.0 TiB"


# --------------------------------------------------------------------------- #
# The logger
# --------------------------------------------------------------------------- #

def test_the_installer_gives_aegis_memory_its_own_stderr_handler():
    process_memory.install_logging()
    memory_log = process_memory.logger()
    handlers = [h for h in memory_log.handlers
                if getattr(h, "_aegis_tag", "") == process_memory._HANDLER_TAG]
    assert len(handlers) == 1
    assert handlers[0].stream is sys.stderr
    assert memory_log.level == logging.INFO
    assert memory_log.propagate is False, (
        "propagating would put these lines through root as well, which is the "
        "configuration this handler exists to avoid needing"
    )


def test_installing_twice_does_not_double_every_line():
    """The lifespan can run more than once in a test process, and a second
    handler would print every memory line twice — which reads as two beats."""
    process_memory.install_logging()
    process_memory.install_logging()
    assert len([h for h in process_memory.logger().handlers
                if getattr(h, "_aegis_tag", "") == process_memory._HANDLER_TAG]) == 1


def test_the_installer_touches_neither_root_nor_the_provider_libraries():
    """The obvious repair for uvicorn's silent root logger is
    ``basicConfig(INFO)``. It also switches on ``httpx``'s per-request line
    and ``openai``'s retry chatter for all 48 concurrent provider calls,
    turning a memory investigation into a log-volume incident. This pins that
    the narrow fix stayed narrow."""
    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_root_level = root.level
    before_httpx = logging.getLogger("httpx").getEffectiveLevel()
    before_openai = logging.getLogger("openai").getEffectiveLevel()

    process_memory.install_logging()

    assert list(root.handlers) == before_handlers
    assert root.level == before_root_level
    assert logging.getLogger("httpx").getEffectiveLevel() == before_httpx
    assert logging.getLogger("openai").getEffectiveLevel() == before_openai


def test_the_lines_carry_a_timestamp_and_the_logger_name():
    """These lines are read in ``fly logs`` against a crash. Without a
    timestamp there is no way to line a peak up with the request that caused
    it, and uvicorn's own formatter — which prints neither — is not on this
    logger."""
    process_memory.install_logging()
    handler = next(h for h in process_memory.logger().handlers
                   if getattr(h, "_aegis_tag", "") == process_memory._HANDLER_TAG)
    handler.stream = io.StringIO()
    process_memory.logger().info("chapter queue: memory limit 4.0 GiB")
    text = handler.stream.getvalue()
    assert "chapter queue: memory limit 4.0 GiB" in text
    import re

    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO aegis\.memory ", text,
    ), text


def test_the_level_knob_can_turn_the_lines_off_entirely(monkeypatch):
    """``off`` is a deployment's escape hatch if a beat every fifteen seconds
    ever costs more than it is worth. It must not be the thing that turns
    root logging back on."""
    monkeypatch.setenv("AEGIS_MEMORY_LOG_LEVEL", "off")
    process_memory.install_logging()
    memory_log = process_memory.logger()
    assert memory_log.isEnabledFor(logging.INFO) is False
    assert memory_log.propagate is False
    assert [h for h in memory_log.handlers
            if getattr(h, "_aegis_tag", "") == process_memory._HANDLER_TAG] == []


def test_an_unreadable_level_falls_back_to_info_rather_than_silence(monkeypatch):
    monkeypatch.setenv("AEGIS_MEMORY_LOG_LEVEL", "banana")
    process_memory.install_logging()
    assert process_memory.logger().level == logging.INFO


def test_a_named_level_is_honoured(monkeypatch):
    monkeypatch.setenv("AEGIS_MEMORY_LOG_LEVEL", "warning")
    process_memory.install_logging()
    assert process_memory.logger().level == logging.WARNING
