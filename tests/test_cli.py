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
