from datetime import UTC, datetime, timedelta

from project_agent_controller.domain.models import DockerSourceConfig
from project_agent_controller.observer.contracts import SourceState
from project_agent_controller.observer.docker_provider import (
    DockerContainer,
    DockerLogBatch,
    DockerLogCursor,
    DockerLogLine,
    DockerSelectorAmbiguous,
    DockerSnapshot,
)
from project_agent_controller.observer.docker_source import DockerSourceObserver


class FakeDockerProvider:
    def __init__(self, containers, snapshots=(), log_batches=()):
        self.containers = iter(containers)
        self.snapshots = iter(snapshots)
        self.log_batches = iter(log_batches)
        self.find_calls = 0

    def find_container(self, selector):
        self.find_calls += 1
        result = next(self.containers)
        if isinstance(result, Exception):
            raise result
        return result

    def inspect(self, container_id):
        return next(self.snapshots)

    def logs(self, container_id, cursor, *, limit, tail):
        return next(self.log_batches)


def container(container_id="abc"):
    return DockerContainer(container_id=container_id, name="demo-db-1", image="postgres:16")


def snapshot(
    *, container_id="abc", state="running", health="healthy", restarts=0,
    exit_code=0, oom=False, memory=100,
):
    return DockerSnapshot(
        container_id=container_id,
        name="demo-db-1",
        image="postgres:16",
        state=state,
        status=state,
        health=health,
        restart_count=restarts,
        exit_code=exit_code,
        oom_killed=oom,
        started_at="2026-07-22T00:00:00Z",
        finished_at=None,
        memory_bytes=memory,
    )


def source(**kwargs):
    return DockerSourceConfig(
        source_id="db",
        selector={"compose_project": "demo", "compose_service": "db"},
        **kwargs,
    )


def test_missing_is_coalesced_then_available() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = FakeDockerProvider([None, None, container()], [snapshot()])
    observer = DockerSourceObserver(provider, clock=lambda: now)

    first = observer.observe("demo", "run-1", source(), None)
    second = observer.observe("demo", "run-1", source(), first.state)
    recovered = observer.observe("demo", "run-1", source(), second.state)

    assert [event.event_type for event in first.events] == ["docker.container.missing"]
    assert second.events == ()
    assert [event.event_type for event in recovered.events] == ["docker.container.available"]


def test_ambiguous_selector_is_coalesced() -> None:
    error = DockerSelectorAmbiguous("docker selector matched 2 containers")
    provider = FakeDockerProvider([error, error])
    observer = DockerSourceObserver(
        provider, clock=lambda: datetime(2026, 7, 22, tzinfo=UTC)
    )

    first = observer.observe("demo", "run-1", source(), None)
    second = observer.observe("demo", "run-1", source(), first.state)

    assert [event.event_type for event in first.events] == ["docker.selector.ambiguous"]
    assert second.events == ()


def test_restart_health_exit_and_oom_transitions() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = FakeDockerProvider(
        [container()],
        [snapshot(state="exited", health="unhealthy", restarts=2, exit_code=137, oom=True)],
    )
    observer = DockerSourceObserver(provider, clock=lambda: now)
    previous = SourceState(
        project_id="demo",
        source_id="db",
        source_kind="docker",
        sequence=1,
        observed_at=now - timedelta(seconds=10),
        state={
            "presence": "available",
            "container_id": "abc",
            "state": "running",
            "health": "healthy",
            "restart_count": 1,
            "oom_killed": False,
            "memory_high": False,
            "last_heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
        },
    )

    result = observer.observe("demo", "run-1", source(), previous)
    types = [event.event_type for event in result.events]

    assert "docker.container.restarted" in types
    assert "docker.state.changed" in types
    assert "docker.health.changed" in types
    assert "docker.container.exited" in types
    assert "docker.container.oom_killed" in types


def test_memory_threshold_and_recovery() -> None:
    current = [datetime(2026, 7, 22, tzinfo=UTC)]
    provider = FakeDockerProvider(
        [container(), container()],
        [snapshot(memory=2000), snapshot(memory=500)],
    )
    observer = DockerSourceObserver(provider, clock=lambda: current[0])
    configured = source(memory_warning_bytes=1000)
    previous = SourceState(
        project_id="demo",
        source_id="db",
        source_kind="docker",
        sequence=1,
        observed_at=current[0] - timedelta(seconds=10),
        state={
            "presence": "available",
            "container_id": "abc",
            "state": "running",
            "health": "healthy",
            "restart_count": 0,
            "oom_killed": False,
            "memory_high": False,
            "last_heartbeat_at": (current[0] - timedelta(seconds=10)).isoformat(),
        },
    )

    high = observer.observe("demo", "run-1", configured, previous)
    current[0] += timedelta(seconds=10)
    recovered = observer.observe("demo", "run-1", configured, high.state)

    assert "docker.resource.threshold" in [event.event_type for event in high.events]
    assert "docker.resource.recovered" in [event.event_type for event in recovered.events]


def test_error_log_is_bounded_and_becomes_incident_candidate() -> None:
    log = DockerLogLine(
        timestamp="2026-07-22T00:00:01Z",
        stream="stderr",
        line="ERROR connection refused",
        content_hash="hash-1",
    )
    batch = DockerLogBatch(
        lines=(log,),
        cursor=DockerLogCursor(
            last_timestamp=log.timestamp,
            recent_hashes=(log.content_hash,),
        ),
    )
    provider = FakeDockerProvider([container()], [snapshot()], [batch])
    observer = DockerSourceObserver(
        provider, clock=lambda: datetime(2026, 7, 22, tzinfo=UTC)
    )

    result = observer.observe("demo", "run-1", source(include_logs=True), None)

    log_events = [event for event in result.events if event.event_type == "docker.log.line"]
    assert len(log_events) == 1
    assert log_events[0].severity.value == "error"
    assert len(result.incident_candidates) == 1
    assert result.state.state["log_cursor"]["last_timestamp"] == log.timestamp
