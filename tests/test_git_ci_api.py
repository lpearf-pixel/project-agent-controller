from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from typer.testing import CliRunner

from project_agent_controller.api.routes import list_source_states
from project_agent_controller.cli import app
from project_agent_controller.observer.contracts import SourceState


class Registry:
    def get(self, project_id):
        if project_id != "demo":
            raise KeyError(project_id)
        return object()


class States:
    def __init__(self):
        self.values = (
            SourceState(
                project_id="demo",
                source_id="repository",
                source_kind="git",
                sequence=1,
                observed_at=datetime(2026, 7, 22, tzinfo=UTC),
                state={
                    "available": True,
                    "head_sha": "a" * 40,
                    "branch": "main",
                    "remote_tracking_only": True,
                },
            ),
            SourceState(
                project_id="demo",
                source_id="github-ci",
                source_kind="github_ci",
                sequence=1,
                observed_at=datetime(2026, 7, 22, tzinfo=UTC),
                state={
                    "available": True,
                    "head_sha": "a" * 40,
                    "overall": "success",
                },
            ),
        )

    def list(self, project_id):
        return self.values if project_id == "demo" else ()


class Runtime:
    registry = Registry()
    source_states = States()


def request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=Runtime())))


def test_api_filters_source_kind_and_contains_no_sensitive_fields() -> None:
    payload = list_source_states("demo", request(), kind="git")
    assert len(payload) == 1
    text = json.dumps(payload)
    assert '"source_kind": "git"' in text
    for forbidden in ("Authorization", "Bearer ", "/Users/", "git@github.com", "https://token"):
        assert forbidden not in text


def test_cli_filters_source_kind(monkeypatch) -> None:
    import project_agent_controller.cli as cli

    monkeypatch.setattr(cli, "build_runtime", lambda _settings: Runtime())
    result = CliRunner().invoke(app, ["sources", "demo", "--kind", "github_ci"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [item["source_kind"] for item in payload] == ["github_ci"]


def test_cli_rejects_unknown_kind(monkeypatch) -> None:
    import project_agent_controller.cli as cli

    monkeypatch.setattr(cli, "build_runtime", lambda _settings: Runtime())
    result = CliRunner().invoke(app, ["sources", "demo", "--kind", "unknown"])
    assert result.exit_code == 2
