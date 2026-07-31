from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

_LABEL = "com.openai.project-agent-controller"


@dataclass(frozen=True, slots=True)
class ServiceRenderInput:
    executable: Path
    working_directory: Path
    env_file: Path
    log_directory: Path

    def __post_init__(self) -> None:
        for name, path in (
            ("executable", self.executable),
            ("working_directory", self.working_directory),
            ("env_file", self.env_file),
            ("log_directory", self.log_directory),
        ):
            _validate_absolute_path(name, path)
        if not self.executable.is_file():
            raise ValueError("executable must reference a regular file")
        if not self.working_directory.is_dir():
            raise ValueError("working_directory must reference a directory")
        if not self.env_file.is_file():
            raise ValueError("env_file must reference a regular file")
        if self.log_directory.exists() and not self.log_directory.is_dir():
            raise ValueError("log_directory must reference a directory")


def render_launchd(config: ServiceRenderInput) -> bytes:
    document = {
        "EnvironmentVariables": {"PAC_ENV_FILE": str(config.env_file)},
        "KeepAlive": {"SuccessfulExit": False},
        "Label": _LABEL,
        "ProcessType": "Background",
        "ProgramArguments": [str(config.executable), "serve"],
        "RunAtLoad": True,
        "StandardErrorPath": str(config.log_directory / "controller.stderr.log"),
        "StandardOutPath": str(config.log_directory / "controller.stdout.log"),
        "ThrottleInterval": 30,
        "WorkingDirectory": str(config.working_directory),
    }
    return plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True)


def render_systemd(config: ServiceRenderInput) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Project Agent Controller",
            "After=network-online.target",
            "Wants=network-online.target",
            "StartLimitIntervalSec=300",
            "StartLimitBurst=3",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_systemd_quote(config.working_directory)}",
            f'Environment="PAC_ENV_FILE={_systemd_escape(config.env_file)}"',
            f"ExecStart={_systemd_quote(config.executable)} serve",
            "Restart=on-failure",
            "RestartSec=30",
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def write_service_definition(
    platform: str,
    config: ServiceRenderInput,
    output_directory: Path,
) -> Path:
    _validate_absolute_path("output_directory", output_directory)
    if not output_directory.is_dir():
        raise ValueError("output_directory must reference an existing directory")
    config.log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    if platform == "launchd":
        filename = f"{_LABEL}.plist"
        payload = render_launchd(config)
    elif platform == "systemd":
        filename = "project-agent-controller.service"
        payload = render_systemd(config).encode("utf-8")
    else:
        raise ValueError("platform must be launchd or systemd")

    target = output_directory / filename
    if target.is_symlink():
        raise ValueError("service definition target must not be a symlink")
    target.write_bytes(payload)
    target.chmod(0o600)
    return target


def _validate_absolute_path(name: str, path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    value = str(path)
    if "\0" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{name} contains unsupported control characters")


def _systemd_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def _systemd_quote(path: Path) -> str:
    return f'"{_systemd_escape(path)}"'
