from datetime import UTC, datetime, timedelta
from pathlib import Path

from project_agent_controller.domain.models import ProcessSourceConfig
from project_agent_controller.observer.contracts import SourceState
from project_agent_controller.observer.process_provider import (
    ProcessSnapshot,
    ProcessUnavailable,
    ProcessUnavailableKind,
)
from project_agent_controller.observer.process_source import ProcessSourceObserver


class FakeProcessProvider:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def snapshot(self, pid: int) -> ProcessSnapshot:
        self.calls += 1
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        assert result.pid == pid
        return result


def snapshot(*, pid=42, create_time=100.0, status="running", cpu=1.0, rss=100):
    return ProcessSnapshot(
        pid=pid,
        create_time=create_time,
        status=status,
        name="python",
        executable="python3",
        cpu_seconds=cpu,
        rss_bytes=rss,
        child_count=0,
    )


def test_missing_is_emitted_once_then_available(tmp_path: Path) -> None:
    source = ProcessSourceConfig(source_id="worker", pid_file_ref="local://demo/worker.pid")
    provider = FakeProcessProvider([snapshot()])
    now = datetime(2026, 7, 22, tzinfo=UTC)
    observer = ProcessSourceObserver(tmp_path, provider, clock=lambda: now)

    first = observer.observe("demo", "run-1", source, None)
    second = observer.observe("demo", "run-1", source, first.state)

    pid_file = tmp_path / "demo/worker.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("42\n", encoding="utf-8")
    recovered = observer.observe("demo", "run-1", source, second.state)

    assert [event.event_type for event in first.events] == ["process.missing"]
    assert second.events == ()
    assert [event.event_type for event in recovered.events] == ["process.available"]


def test_pid_reuse_emits_restart(tmp_path: Path) -> None:
    pid_file = tmp_path / "demo/worker.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("42\n", encoding="utf-8")
    source = ProcessSourceConfig(source_id="worker", pid_file_ref="local://demo/worker.pid")
    provider = FakeProcessProvider([snapshot(create_time=200.0)])
    now = datetime(2026, 7, 22, tzinfo=UTC)
    observer = ProcessSourceObserver(tmp_path, provider, clock=lambda: now)
    previous = SourceState(
        project_id="demo",
        source_id="worker",
        source_kind="process",
        sequence=1,
        observed_at=now - timedelta(seconds=10),
        state={
            "presence": "available",
            "pid": 42,
            "create_time": 100.0,
            "status": "running",
            "cpu_seconds": 1.0,
            "cpu_high": False,
            "rss_high": False,
            "last_heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
        },
    )

    result = observer.observe("demo", "run-1", source, previous)

    assert [event.event_type for event in result.events] == ["process.restarted"]


def test_resource_threshold_crossing_and_recovery(tmp_path: Path) -> None:
    pid_file = tmp_path / "demo/worker.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("42\n", encoding="utf-8")
    source = ProcessSourceConfig(
        source_id="worker",
        pid_file_ref="local://demo/worker.pid",
        cpu_warning_percent=50,
        rss_warning_bytes=1000,
    )
    current = [datetime(2026, 7, 22, tzinfo=UTC)]
    provider = FakeProcessProvider([
        snapshot(cpu=7.0, rss=2000),
        snapshot(cpu=7.1, rss=500),
    ])
    observer = ProcessSourceObserver(tmp_path, provider, clock=lambda: current[0])
    previous = SourceState(
        project_id="demo",
        source_id="worker",
        source_kind="process",
        sequence=1,
        observed_at=current[0] - timedelta(seconds=10),
        state={
            "presence": "available",
            "pid": 42,
            "create_time": 100.0,
            "status": "running",
            "cpu_seconds": 1.0,
            "cpu_high": False,
            "rss_high": False,
            "last_heartbeat_at": (current[0] - timedelta(seconds=10)).isoformat(),
        },
    )

    high = observer.observe("demo", "run-1", source, previous)
    current[0] += timedelta(seconds=10)
    recovered = observer.observe("demo", "run-1", source, high.state)

    assert [event.event_type for event in high.events] == ["process.resource.threshold"]
    assert set(high.events[0].payload["crossed"]) == {"cpu", "rss"}
    assert [event.event_type for event in recovered.events] == ["process.resource.recovered"]


def test_access_denied_is_coalesced_and_command_line_is_not_exposed(tmp_path: Path) -> None:
    pid_file = tmp_path / "demo/worker.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("42\n", encoding="utf-8")
    source = ProcessSourceConfig(source_id="worker", pid_file_ref="local://demo/worker.pid")
    provider = FakeProcessProvider([
        ProcessUnavailable(ProcessUnavailableKind.ACCESS_DENIED, 42),
        ProcessUnavailable(ProcessUnavailableKind.ACCESS_DENIED, 42),
    ])
    observer = ProcessSourceObserver(
        tmp_path, provider, clock=lambda: datetime(2026, 7, 22, tzinfo=UTC)
    )

    first = observer.observe("demo", "run-1", source, None)
    second = observer.observe("demo", "run-1", source, first.state)

    assert [event.event_type for event in first.events] == ["process.access.denied"]
    assert second.events == ()
    assert "cmdline" not in first.state.state
