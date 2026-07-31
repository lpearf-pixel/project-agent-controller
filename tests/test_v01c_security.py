from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from project_agent_controller.observer.contracts import SourceState
from project_agent_controller.observer.git_transport import GitReadTransport
from project_agent_controller.observer.github_transport import (
    GitHubTransportError,
    validate_github_request,
)


def test_git_transport_has_one_fixed_status_template() -> None:
    source = inspect.getsource(GitReadTransport.status)
    assert "shell=False" in source
    assert '"status"' in source
    assert '"--porcelain=v2"' in source
    for forbidden in (
        '"fetch"',
        '"pull"',
        '"push"',
        '"commit"',
        '"reset"',
        '"checkout"',
        '"merge"',
        '"rebase"',
    ):
        assert forbidden not in source


def test_github_transport_rejects_write_and_actions_control_paths() -> None:
    sha = "a" * 40
    good = f"/repos/owner/repo/commits/{sha}/check-runs"
    validate_github_request("GET", good)
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        with pytest.raises(GitHubTransportError):
            validate_github_request(method, good)
    for path in (
        "/repos/owner/repo/actions/runs/1/rerun",
        "/repos/owner/repo/actions/jobs/1/logs",
        f"/repos/owner/repo/commits/{sha}/comments",
    ):
        with pytest.raises(GitHubTransportError):
            validate_github_request("GET", path)


def test_git_and_ci_states_contain_no_credentials_or_absolute_paths() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    states = (
        SourceState(
            project_id="demo",
            source_id="repository",
            source_kind="git",
            sequence=1,
            observed_at=now,
            state={
                "available": True,
                "head_sha": "a" * 40,
                "branch": "main",
                "upstream": "origin/main",
                "remote_tracking_only": True,
            },
        ),
        SourceState(
            project_id="demo",
            source_id="github-ci",
            source_kind="github_ci",
            sequence=1,
            observed_at=now,
            state={
                "available": True,
                "head_sha": "a" * 40,
                "overall": "success",
                "provider_id": "github-cloud",
                "repository": "owner/repo",
            },
        ),
    )
    text = json.dumps([state.model_dump(mode="json") for state in states])
    for forbidden in (
        "Authorization",
        "Bearer ",
        "env://",
        "/Users/",
        str(Path.home()),
        "git@github.com",
        "https://token",
    ):
        assert forbidden not in text
