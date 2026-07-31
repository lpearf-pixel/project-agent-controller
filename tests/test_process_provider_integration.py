import os
from pathlib import Path

from project_agent_controller.observer.process_provider import PsutilProcessProvider


def test_psutil_provider_observes_current_process_without_cmdline() -> None:
    pid = os.getpid()
    proc_self = Path("/proc/self")
    if proc_self.exists():
        resolved_pid = proc_self.resolve().name
        if resolved_pid.isdecimal():
            pid = int(resolved_pid)

    snapshot = PsutilProcessProvider().snapshot(pid)

    assert snapshot.pid == pid
    assert snapshot.create_time > 0
    assert snapshot.name
    assert snapshot.rss_bytes > 0
    assert "cmdline" not in snapshot.model_dump()
