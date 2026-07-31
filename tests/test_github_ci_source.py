from __future__ import annotations

from datetime import UTC, datetime, timedelta

from project_agent_controller.domain.models import GitHubCISourceConfig
from project_agent_controller.observer.contracts import SourceState
from project_agent_controller.observer.github_ci_provider import CISnapshot, FailedCheck
from project_agent_controller.observer.github_ci_source import GitHubCISourceObserver
from project_agent_controller.observer.github_transport import GitHubTransportError


def git_state(sha: str = "a" * 40) -> SourceState:
    return SourceState(
        project_id="demo",
        source_id="repository",
        source_kind="git",
        sequence=1,
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        state={"available": True, "head_sha": sha},
    )


def source() -> GitHubCISourceConfig:
    return GitHubCISourceConfig(
        source_id="github-ci",
        provider_id="github-cloud",
        repository="owner/repo",
        git_source_id="repository",
        heartbeat_seconds=900,
    )


def snapshot(
    *,
    sha: str = "a" * 40,
    overall: str = "success",
    failed: tuple[FailedCheck, ...] = (),
    not_modified: bool = False,
) -> CISnapshot:
    failure_count = len(failed)
    total = max(1, failure_count) if overall != "no_checks" else 0
    success_count = total if overall == "success" else 0
    pending_count = total if overall == "pending" else 0
    return CISnapshot(
        head_sha=sha,
        overall=overall,
        total_checks=total,
        success_count=success_count,
        pending_count=pending_count,
        failure_count=failure_count,
        cancelled_count=0,
        neutral_count=0,
        failed_checks=failed,
        legacy_status_state="success" if total else None,
        check_summary={
            "total": total,
            "success": success_count,
            "pending": pending_count,
            "failure": failure_count,
            "cancelled": 0,
            "neutral": 0,
            "failed_checks": [item.model_dump(mode="json") for item in failed],
        },
        legacy_summary={"state": "success", "total": 0},
        etag_check_runs='"c"',
        etag_status='"s"',
        rate_limit_remaining=100,
        rate_limit_reset=None,
        not_modified=not_modified,
    )


class Provider:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    def snapshot(self, *_args, **_kwargs):
        self.calls += 1
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def test_missing_git_state_is_blocked_and_coalesced() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = Provider([])
    observer = GitHubCISourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), None, None, now=now)
    second = observer.observe(
        "demo", "run-1", source(), None, first.state, now=now + timedelta(seconds=1)
    )
    assert [event.event_type for event in first.events] == ["ci.blocked.missing_git_state"]
    assert second.events == ()
    assert provider.calls == 0


def test_new_failed_check_creates_one_incident_and_recovers() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    failed = FailedCheck(
        name="tests",
        conclusion="failure",
        details_url="https://github.com/owner/repo/actions/runs/1",
        summary="one test failed",
        provider_object_id="10",
    )
    provider = Provider(
        [
            snapshot(),
            snapshot(overall="failure", failed=(failed,)),
            snapshot(overall="failure", failed=(failed,)),
            snapshot(),
        ]
    )
    observer = GitHubCISourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), git_state(), None, now=now)
    entered = observer.observe(
        "demo", "run-1", source(), git_state(), first.state, now=now + timedelta(seconds=1)
    )
    stable = observer.observe(
        "demo", "run-1", source(), git_state(), entered.state, now=now + timedelta(seconds=2)
    )
    cleared = observer.observe(
        "demo", "run-1", source(), git_state(), stable.state, now=now + timedelta(seconds=3)
    )

    assert [event.event_type for event in first.events] == ["ci.available"]
    assert [event.event_type for event in entered.events] == [
        "ci.status.changed",
        "ci.check.failed",
    ]
    assert len(entered.incident_candidates) == 1
    assert stable.events == () and stable.incident_candidates == ()
    assert [event.event_type for event in cleared.events] == [
        "ci.status.changed",
        "ci.check.recovered",
    ]


def test_head_change_and_no_checks_transitions() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = Provider([snapshot(), snapshot(sha="b" * 40, overall="no_checks")])
    observer = GitHubCISourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), git_state(), None, now=now)
    second = observer.observe(
        "demo",
        "run-1",
        source(),
        git_state("b" * 40),
        first.state,
        now=now + timedelta(seconds=1),
    )
    assert [event.event_type for event in second.events] == [
        "ci.head.changed",
        "ci.status.changed",
        "ci.no_checks",
    ]


def test_rate_limit_backoff_skips_provider_until_reset() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    reset_epoch = int((now + timedelta(seconds=120)).timestamp())
    error = GitHubTransportError(
        "limited",
        kind="rate_limited",
        status_code=403,
        rate_limit_remaining=0,
        rate_limit_reset=reset_epoch,
    )
    provider = Provider([error, snapshot()])
    observer = GitHubCISourceObserver(provider)
    first = observer.observe("demo", "run-1", source(), git_state(), None, now=now)
    skipped = observer.observe(
        "demo", "run-1", source(), git_state(), first.state, now=now + timedelta(seconds=30)
    )
    recovered = observer.observe(
        "demo", "run-1", source(), git_state(), skipped.state, now=now + timedelta(seconds=121)
    )
    assert [event.event_type for event in first.events] == ["ci.rate_limited"]
    assert skipped.events == ()
    assert provider.calls == 2
    assert [event.event_type for event in recovered.events] == ["ci.recovered"]


def test_500_not_modified_cycles_only_emit_available_and_heartbeat() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    provider = Provider([snapshot()] + [snapshot(not_modified=True)] * 501)
    observer = GitHubCISourceObserver(provider)
    state = None
    events = []
    for index in range(500):
        observation = observer.observe(
            "demo",
            "run-1",
            source(),
            git_state(),
            state,
            now=now + timedelta(seconds=index),
        )
        state = observation.state
        events.extend(observation.events)
    assert [event.event_type for event in events] == ["ci.available"]
    heartbeat = observer.observe(
        "demo",
        "run-1",
        source(),
        git_state(),
        state,
        now=now + timedelta(seconds=901),
    )
    assert [event.event_type for event in heartbeat.events] == ["ci.heartbeat"]
