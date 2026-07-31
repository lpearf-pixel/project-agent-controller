from datetime import UTC, datetime

import pytest

from project_agent_controller.control.service import ControllerState
from project_agent_controller.domain.models import ProjectConfig
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.runner import ObservationBlocked, ObserverRunner


class Control:
    def __init__(self, state=ControllerState.ACTIVE):
        self.state = state

    def get_state(self):
        return self.state


class Store:
    def __init__(self):
        self.items = {}

    def get(self, project_id, source_id):
        return self.items.get((project_id, source_id))

    def append(self, observation):
        state = observation.state
        self.items[(state.project_id, state.source_id)] = state


class GitObserver:
    def __init__(self, calls):
        self.calls = calls

    def observe(self, project_id, run_id, source, previous):
        self.calls.append("git")
        return SourceObservation(
            events=(),
            state=SourceState(
                project_id=project_id,
                source_id=source.source_id,
                source_kind="git",
                sequence=0,
                observed_at=datetime(2026, 7, 22, tzinfo=UTC),
                state={"available": True, "head_sha": "a" * 40},
            ),
        )


class CIObserver:
    def __init__(self, calls):
        self.calls = calls

    def observe(self, project_id, run_id, source, git_state, previous):
        self.calls.append("ci")
        assert git_state is not None
        assert git_state.state["head_sha"] == "a" * 40
        return SourceObservation(
            events=(),
            state=SourceState(
                project_id=project_id,
                source_id=source.source_id,
                source_kind="github_ci",
                sequence=0,
                observed_at=datetime(2026, 7, 22, tzinfo=UTC),
                state={"available": True, "head_sha": "a" * 40, "overall": "success"},
            ),
        )


def project() -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project_id": "demo",
            "display_name": "Demo",
            "sources": [
                {
                    "source_id": "github-ci",
                    "kind": "github_ci",
                    "provider_id": "github-cloud",
                    "repository": "owner/repo",
                    "git_source_id": "repository",
                },
                {
                    "source_id": "repository",
                    "kind": "git",
                    "path_ref": "local://repos/demo",
                },
            ],
        }
    )


def test_runner_observes_git_before_ci_even_if_config_order_is_reversed(tmp_path) -> None:
    calls = []
    store = Store()
    runner = ObserverRunner(
        object(),
        Control(),
        local_root=tmp_path,
        run_id="run-1",
        git_observer=GitObserver(calls),
        ci_observers={"github-cloud": CIObserver(calls)},
        source_states=store,
    )
    assert runner.observe_once(project()) == 0
    assert calls == ["git", "ci"]


def test_emergency_stop_blocks_git_and_ci_before_provider_calls(tmp_path) -> None:
    calls = []
    runner = ObserverRunner(
        object(),
        Control(ControllerState.EMERGENCY_STOP),
        local_root=tmp_path,
        run_id="run-1",
        git_observer=GitObserver(calls),
        ci_observers={"github-cloud": CIObserver(calls)},
        source_states=Store(),
    )
    with pytest.raises(ObservationBlocked):
        runner.observe_once(project())
    assert calls == []
