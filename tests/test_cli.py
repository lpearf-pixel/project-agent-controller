from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import project_agent_controller.cli as cli
from project_agent_controller.cli import app


def test_cli_help_lists_local_observer_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "serve" in result.stdout
    assert "status" in result.stdout
    assert "observe-once" in result.stdout
    assert "incident" in result.stdout
    assert "controller" in result.stdout
    assert "service" in result.stdout
    assert "task" in result.stdout


def test_cli_loads_private_service_environment_before_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    projects_file = tmp_path / "projects.yaml"
    env_file = tmp_path / "pac.env"
    env_file.write_text(
        f"PAC_DATA_DIR={data_dir}\nPAC_PROJECTS_FILE={projects_file}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    monkeypatch.setenv("PAC_ENV_FILE", str(env_file))
    monkeypatch.delenv("PAC_DATA_DIR", raising=False)
    monkeypatch.delenv("PAC_PROJECTS_FILE", raising=False)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    assert (data_dir / "controller.db").exists()


def test_cli_exposes_explicit_recovery_completion() -> None:
    result = CliRunner().invoke(app, ["controller", "--help"])

    assert result.exit_code == 0
    assert "complete-recovery" in result.stdout


def test_cli_exposes_fixed_task_runner() -> None:
    result = CliRunner().invoke(app, ["task", "--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout


def test_cli_returns_nonzero_for_terminal_task_failure(monkeypatch) -> None:
    failed = SimpleNamespace(
        run_id="task-1",
        project_id="demo",
        task_id="verify",
        idempotency_key="request-1",
        state="failed",
        attempt_count=1,
        classification="failed",
        exit_code=1,
        stdout="",
        stderr="failed",
        output_truncated=False,
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
        finished_at=datetime(2026, 7, 31, tzinfo=UTC),
    )
    runtime = SimpleNamespace(
        registry=SimpleNamespace(get=lambda _project_id: object()),
        tasks=SimpleNamespace(run=lambda *_arguments: failed),
    )
    monkeypatch.setattr(cli, "build_runtime", lambda _settings: runtime)

    result = CliRunner().invoke(
        app, ["task", "run", "demo", "verify", "request-1"]
    )

    assert result.exit_code == 1
    assert '"state": "failed"' in result.stdout
