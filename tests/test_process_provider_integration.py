import os

from project_agent_controller.observer.process_provider import PsutilProcessProvider


def test_psutil_provider_observes_current_process_without_cmdline() -> None:
    snapshot = PsutilProcessProvider().snapshot(os.getpid())

    assert snapshot.pid == os.getpid()
    assert snapshot.create_time > 0
    assert snapshot.name
    assert snapshot.rss_bytes > 0
    assert "cmdline" not in snapshot.model_dump()
