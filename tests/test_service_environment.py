from pathlib import Path

import pytest

from project_agent_controller.service_environment import load_service_environment


def write_private_env(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_loader_accepts_private_pac_and_proxy_values_without_overriding_environment(
    tmp_path: Path,
) -> None:
    env_file = write_private_env(
        tmp_path / "pac.env",
        "PAC_PORT=9191\nPAC_GITHUB_TOKEN=secret\nHTTPS_PROXY=http://127.0.0.1:8001\n",
    )
    environ = {"PAC_ENV_FILE": str(env_file), "PAC_PORT": "9090"}

    assert load_service_environment(environ) == env_file
    assert environ == {
        "PAC_ENV_FILE": str(env_file),
        "PAC_PORT": "9090",
        "PAC_GITHUB_TOKEN": "secret",
        "HTTPS_PROXY": "http://127.0.0.1:8001",
    }


def test_loader_returns_none_when_env_file_is_not_configured() -> None:
    environ: dict[str, str] = {}

    assert load_service_environment(environ) is None
    assert environ == {}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("UNKNOWN_KEY=value\n", "unsupported key"),
        ("PAC_PORT\n", "has no string value"),
    ],
)
def test_loader_rejects_unsupported_or_valueless_entries(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    env_file = write_private_env(tmp_path / "pac.env", content)

    with pytest.raises(ValueError, match=message):
        load_service_environment({"PAC_ENV_FILE": str(env_file)})


def test_loader_rejects_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_private_env(tmp_path / "pac.env", "PAC_PORT=9191\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        load_service_environment({"PAC_ENV_FILE": "pac.env"})


def test_loader_rejects_group_or_world_access(tmp_path: Path) -> None:
    env_file = write_private_env(tmp_path / "pac.env", "PAC_PORT=9191\n")
    env_file.chmod(0o640)

    with pytest.raises(ValueError, match="0600"):
        load_service_environment({"PAC_ENV_FILE": str(env_file)})


def test_loader_rejects_files_larger_than_64_kib(tmp_path: Path) -> None:
    env_file = write_private_env(tmp_path / "pac.env", "PAC_VALUE=" + "x" * 65_536)

    with pytest.raises(ValueError, match="65536"):
        load_service_environment({"PAC_ENV_FILE": str(env_file)})
