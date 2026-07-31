from pathlib import Path

from typer.testing import CliRunner

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
