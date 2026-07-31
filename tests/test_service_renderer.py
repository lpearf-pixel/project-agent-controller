import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from project_agent_controller.service_renderer import (
    ServiceRenderInput,
    render_launchd,
    render_systemd,
    write_service_definition,
)


def make_input(tmp_path: Path) -> ServiceRenderInput:
    executable = tmp_path / "pac"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o700)
    working_directory = tmp_path / "controller"
    working_directory.mkdir()
    env_file = tmp_path / "pac.env"
    env_file.write_text("PAC_GITHUB_TOKEN=must-not-render\n", encoding="utf-8")
    env_file.chmod(0o600)
    log_directory = tmp_path / "logs"
    return ServiceRenderInput(
        executable=executable,
        working_directory=working_directory,
        env_file=env_file,
        log_directory=log_directory,
    )


def test_launchd_renderer_uses_fixed_argv_restart_throttle_and_env_file_only(
    tmp_path: Path,
) -> None:
    config = make_input(tmp_path)

    payload = render_launchd(config)
    document = plistlib.loads(payload)

    assert document["Label"] == "com.openai.project-agent-controller"
    assert document["ProgramArguments"] == [str(config.executable), "serve"]
    assert document["WorkingDirectory"] == str(config.working_directory)
    assert document["EnvironmentVariables"] == {"PAC_ENV_FILE": str(config.env_file)}
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] == {"SuccessfulExit": False}
    assert document["ThrottleInterval"] == 30
    assert document["StandardOutPath"].endswith("/controller.stdout.log")
    assert document["StandardErrorPath"].endswith("/controller.stderr.log")
    assert b"must-not-render" not in payload


def test_systemd_renderer_is_failure_only_rate_limited_and_contains_no_secret(
    tmp_path: Path,
) -> None:
    config = make_input(tmp_path)

    document = render_systemd(config)

    assert "StartLimitIntervalSec=300" in document
    assert "StartLimitBurst=3" in document
    assert "Type=simple" in document
    assert f'WorkingDirectory="{config.working_directory}"' in document
    assert f'Environment="PAC_ENV_FILE={config.env_file}"' in document
    assert f'ExecStart="{config.executable}" serve' in document
    assert "Restart=on-failure" in document
    assert "RestartSec=30" in document
    assert "UMask=0077" in document
    assert "must-not-render" not in document


@pytest.mark.parametrize("field", ["executable", "working_directory", "env_file", "log_directory"])
def test_renderer_rejects_relative_paths(tmp_path: Path, field: str) -> None:
    config = make_input(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        replace(config, **{field: Path("relative")})


def test_write_service_definition_creates_only_expected_private_file(tmp_path: Path) -> None:
    config = make_input(tmp_path)
    output_directory = tmp_path / "output"
    output_directory.mkdir()

    launchd_path = write_service_definition("launchd", config, output_directory)

    assert launchd_path == output_directory / "com.openai.project-agent-controller.plist"
    assert launchd_path.stat().st_mode & 0o777 == 0o600
    assert [path.name for path in output_directory.iterdir()] == [launchd_path.name]


def test_write_service_definition_rejects_unknown_platform(tmp_path: Path) -> None:
    config = make_input(tmp_path)
    output_directory = tmp_path / "output"
    output_directory.mkdir()

    with pytest.raises(ValueError, match="platform"):
        write_service_definition("windows", config, output_directory)
