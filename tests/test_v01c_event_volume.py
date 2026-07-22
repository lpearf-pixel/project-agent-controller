from datetime import UTC, datetime, timedelta

from project_agent_controller.domain.models import GitHubCISourceConfig, GitSourceConfig
from project_agent_controller.observer.contracts import SourceState
from project_agent_controller.observer.git_provider import GitSnapshot
from project_agent_controller.observer.git_source import GitSourceObserver
from project_agent_controller.observer.github_ci_provider import CISnapshot
from project_agent_controller.observer.github_ci_source import GitHubCISourceObserver


class StableGitProvider:
    def snapshot(self, _source: GitSourceConfig) -> GitSnapshot:
        return GitSnapshot(
            head_sha="a" * 40,
            branch="main",
            upstream="origin/main",
            change_fingerprint="sha256:stable",
        )


class StableCIProvider:
    def snapshot(self, _repository, sha, *, previous, max_check_runs, max_failed_checks):
        del max_check_runs, max_failed_checks
        return CISnapshot(
            head_sha=sha,
            overall="success",
            total_checks=1,
            success_count=1,
            pending_count=0,
            failure_count=0,
            cancelled_count=0,
            neutral_count=0,
            failed_checks=(),
            legacy_status_state="success",
            check_summary={
                "total": 1,
                "success": 1,
                "pending": 0,
                "failure": 0,
                "cancelled": 0,
                "neutral": 0,
                "failed_checks": [],
            },
            legacy_summary={"state": "success", "total": 0},
            etag_check_runs='"checks"',
            etag_status='"status"',
            rate_limit_remaining=100,
            rate_limit_reset=None,
            not_modified=previous is not None,
        )


def test_500_stable_git_cycles_do_not_create_event_storm() -> None:
    observer = GitSourceObserver(StableGitProvider())
    source = GitSourceConfig(
        source_id="repository",
        path_ref="local://demo",
        heartbeat_seconds=900,
    )
    now = datetime(2026, 7, 22, tzinfo=UTC)
    state = None
    events = []
    for index in range(500):
        result = observer.observe(
            "demo", "run-1", source, state, now=now + timedelta(seconds=index)
        )
        state = result.state
        events.extend(result.events)

    assert [event.event_type for event in events] == ["git.available"]


def test_500_stable_ci_cycles_do_not_create_event_storm() -> None:
    observer = GitHubCISourceObserver(StableCIProvider())
    source = GitHubCISourceConfig(
        source_id="github-ci",
        provider_id="github-cloud",
        repository="owner/repo",
        git_source_id="repository",
        heartbeat_seconds=900,
    )
    now = datetime(2026, 7, 22, tzinfo=UTC)
    git_state = SourceState(
        project_id="demo",
        source_id="repository",
        source_kind="git",
        sequence=1,
        observed_at=now,
        state={"available": True, "head_sha": "a" * 40},
    )
    state = None
    events = []
    for index in range(500):
        result = observer.observe(
            "demo",
            "run-1",
            source,
            git_state,
            state,
            now=now + timedelta(seconds=index),
        )
        state = result.state
        events.extend(result.events)

    assert [event.event_type for event in events] == ["ci.available"]
