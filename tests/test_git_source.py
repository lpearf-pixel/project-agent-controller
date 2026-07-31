from __future__ import annotations

from datetime import UTC, datetime, timedelta

from project_agent_controller.domain.models import GitSourceConfig
from project_agent_controller.observer.git_provider import GitSnapshot
from project_agent_controller.observer.git_source import GitSourceObserver


def snap(**overrides) -> GitSnapshot:
    data = {
        "head_sha": "0" * 40,
        "branch": "main",
        "detached": False,
        "upstream": "origin/main",
        "ahead": 0,
        "behind": 0,
        "staged_count": 0,
        "unstaged_count": 0,
        "untracked_count": 0,
        "conflict_count": 0,
        "dirty": False,
        "change_fingerprint": "sha256:base",
        "remote_tracking_only": True,
        "remote_freshness": "unknown",
    }
    data.update(overrides)
    return GitSnapshot.model_validate(data)


class Provider:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.calls = 0

    def snapshot(self, source):
        self.calls += 1
        value = next(self.snapshots)
        if isinstance(value, Exception):
            raise value
        return value


def source() -> GitSourceConfig:
    return GitSourceConfig(source_id="repository", path_ref="local://repos/demo")


def test_available_dirty_head_and_clean_transitions() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = Provider(
        [
            snap(),
            snap(dirty=True, unstaged_count=1, change_fingerprint="sha256:dirty"),
            snap(
                head_sha="1" * 40,
                dirty=False,
                unstaged_count=0,
                change_fingerprint="sha256:new-head",
            ),
        ]
    )
    observer = GitSourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), None, now=now)
    second = observer.observe(
        "demo", "run-1", source(), first.state, now=now + timedelta(seconds=1)
    )
    third = observer.observe(
        "demo", "run-1", source(), second.state, now=now + timedelta(seconds=2)
    )

    assert [event.event_type for event in first.events] == ["git.available"]
    assert [event.event_type for event in second.events] == ["git.dirty.entered"]
    assert [event.event_type for event in third.events] == ["git.head.changed", "git.dirty.cleared"]


def test_conflict_creates_one_incident_and_recovery_event() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    conflict = snap(
        dirty=True,
        conflict_count=1,
        change_fingerprint="sha256:conflict",
    )
    provider = Provider([snap(), conflict, conflict, snap()])
    observer = GitSourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), None, now=now)
    entered = observer.observe(
        "demo", "run-1", source(), first.state, now=now + timedelta(seconds=1)
    )
    stable = observer.observe(
        "demo", "run-1", source(), entered.state, now=now + timedelta(seconds=2)
    )
    cleared = observer.observe(
        "demo", "run-1", source(), stable.state, now=now + timedelta(seconds=3)
    )

    assert [event.event_type for event in entered.events] == [
        "git.dirty.entered",
        "git.conflict.entered",
    ]
    assert len(entered.incident_candidates) == 1
    assert stable.events == () and stable.incident_candidates == ()
    assert [event.event_type for event in cleared.events] == [
        "git.dirty.cleared",
        "git.conflict.cleared",
    ]


def test_provider_error_is_coalesced_and_recovers() -> None:
    from project_agent_controller.observer.git_transport import GitTransportError

    now = datetime(2026, 7, 22, tzinfo=UTC)
    error = GitTransportError("not a repository")
    provider = Provider([error, error, snap()])
    observer = GitSourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), None, now=now)
    second = observer.observe(
        "demo", "run-1", source(), first.state, now=now + timedelta(seconds=1)
    )
    third = observer.observe(
        "demo", "run-1", source(), second.state, now=now + timedelta(seconds=2)
    )

    assert [event.event_type for event in first.events] == ["git.provider.unavailable"]
    assert second.events == ()
    assert [event.event_type for event in third.events] == ["git.recovered"]


def test_detached_and_ahead_behind_transitions() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = Provider(
        [
            snap(),
            snap(detached=True, branch=None, ahead=2, behind=1, change_fingerprint="sha256:d"),
        ]
    )
    observer = GitSourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), None, now=now)
    second = observer.observe(
        "demo", "run-1", source(), first.state, now=now + timedelta(seconds=1)
    )
    assert [event.event_type for event in second.events] == [
        "git.branch.changed",
        "git.detached.entered",
        "git.ahead.changed",
        "git.behind.changed",
    ]


def test_500_stable_cycles_only_emit_available_and_heartbeat() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = Provider([snap()] * 502)
    observer = GitSourceObserver(provider)
    state = None
    events = []
    for index in range(500):
        observation = observer.observe(
            "demo", "run-1", source(), state, now=now + timedelta(seconds=index)
        )
        state = observation.state
        events.extend(observation.events)
    assert [event.event_type for event in events] == ["git.available"]

    observation = observer.observe(
        "demo", "run-1", source(), state, now=now + timedelta(seconds=901)
    )
    assert [event.event_type for event in observation.events] == ["git.heartbeat"]
