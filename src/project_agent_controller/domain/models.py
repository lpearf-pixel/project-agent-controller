from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SOURCE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,63}$"
PROVIDER_ID_PATTERN = SOURCE_ID_PATTERN
REPOSITORY_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


def _validate_local_ref(value: str, field_name: str) -> str:
    if not value.startswith("local://"):
        raise ValueError(f"{field_name} must use local://")
    if value == "local://":
        raise ValueError(f"{field_name} must not be empty")
    return value


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FileSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    kind: Literal["file"] = "file"
    path_ref: str
    encoding: str = "utf-8"
    parser: str = "text-v1"

    @field_validator("path_ref")
    @classmethod
    def validate_path_ref(cls, value: str) -> str:
        return _validate_local_ref(value, "path_ref")


class ProcessSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    kind: Literal["process"] = "process"
    pid_file_ref: str
    heartbeat_seconds: int = Field(default=300, ge=30, le=86400)
    cpu_warning_percent: float | None = Field(default=None, gt=0, le=10000)
    rss_warning_bytes: int | None = Field(default=None, gt=0)
    include_children: bool = True

    @field_validator("pid_file_ref")
    @classmethod
    def validate_pid_file_ref(cls, value: str) -> str:
        return _validate_local_ref(value, "pid_file_ref")


class DockerSelector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    container_name: str | None = None
    compose_project: str | None = None
    compose_service: str | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> DockerSelector:
        by_name = self.container_name is not None
        by_compose = self.compose_project is not None and self.compose_service is not None
        partial_compose = (self.compose_project is None) != (self.compose_service is None)
        if partial_compose or by_name == by_compose:
            raise ValueError("docker selector must use exactly one selector mode")
        return self


class DockerSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    kind: Literal["docker"] = "docker"
    selector: DockerSelector
    include_logs: bool = False
    log_tail_on_attach: int = Field(default=100, ge=0, le=5000)
    log_max_lines_per_cycle: int = Field(default=500, ge=1, le=5000)
    heartbeat_seconds: int = Field(default=300, ge=30, le=86400)
    memory_warning_bytes: int | None = Field(default=None, gt=0)


class GitSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    kind: Literal["git"] = "git"
    path_ref: str
    remote: str = Field(default="origin", min_length=1, max_length=120)
    include_untracked: bool = True
    heartbeat_seconds: int = Field(default=900, ge=60, le=86400)
    max_changed_paths: int = Field(default=0, ge=0, le=100)

    @field_validator("path_ref")
    @classmethod
    def validate_path_ref(cls, value: str) -> str:
        return _validate_local_ref(value, "path_ref")


class GitHubCISourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    kind: Literal["github_ci"] = "github_ci"
    provider_id: str = Field(pattern=PROVIDER_ID_PATTERN)
    repository: str = Field(pattern=REPOSITORY_PATTERN)
    git_source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    heartbeat_seconds: int = Field(default=900, ge=60, le=86400)
    max_check_runs: int = Field(default=100, ge=1, le=100)
    max_failed_checks: int = Field(default=20, ge=1, le=50)


class TaskTemplateConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_id: str = Field(pattern=SOURCE_ID_PATTERN)
    repository_ref: str
    working_directory: str = "."
    executable: str = Field(min_length=1, max_length=120)
    arguments: tuple[str, ...] = ()
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=900, ge=1, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=3)
    output_max_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=10)
    circuit_cooldown_seconds: int = Field(default=300, ge=30, le=86400)

    @field_validator("repository_ref")
    @classmethod
    def validate_repository_ref(cls, value: str) -> str:
        checked = _validate_local_ref(value, "repository_ref")
        relative = checked.removeprefix("local://")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or "\x00" in relative:
            raise ValueError("repository_ref must stay inside the local repository root")
        return checked

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
            raise ValueError("working_directory must stay inside the archived repository")
        return value

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}", value) is None:
            raise ValueError("executable must be a bare command name")
        return value

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 64:
            raise ValueError("arguments must contain at most 64 values")
        if any("\x00" in argument or len(argument) > 512 for argument in value):
            raise ValueError("arguments must not contain NUL or exceed 512 characters")
        return value

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("environment must contain at most 32 entries")
        for key, item in value.items():
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is None:
                raise ValueError("environment keys must be upper case names")
            if re.search(r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|API_KEY)", key):
                raise ValueError("credential-shaped environment key is forbidden")
            if key in {"HOME", "PATH", "SHELL", "TMPDIR", "XDG_CONFIG_HOME"}:
                raise ValueError("runner-controlled environment key is forbidden")
            if "\x00" in item or len(item) > 512:
                raise ValueError("environment values must not contain NUL or exceed 512 characters")
        return value


type SourceConfig = Annotated[
    FileSourceConfig
    | ProcessSourceConfig
    | DockerSourceConfig
    | GitSourceConfig
    | GitHubCISourceConfig,
    Field(discriminator="kind"),
]


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str = Field(pattern=SOURCE_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    technologies: tuple[str, ...] = ()
    sources: tuple[SourceConfig, ...] = ()
    tasks: tuple[TaskTemplateConfig, ...] = ()

    @model_validator(mode="after")
    def validate_ci_dependencies(self) -> ProjectConfig:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("duplicate task_id")
        git_ids = {
            source.source_id for source in self.sources if isinstance(source, GitSourceConfig)
        }
        for source in self.sources:
            if isinstance(source, GitHubCISourceConfig) and source.git_source_id not in git_ids:
                raise ValueError(
                    f"git_source_id {source.git_source_id!r} must reference a git source"
                )
        return self


class ProjectsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    config_version: Literal[1]
    projects: tuple[ProjectConfig, ...]


class GitHubProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider_id: str = Field(pattern=PROVIDER_ID_PATTERN)
    kind: Literal["github"] = "github"
    api_base_url: AnyHttpUrl
    api_version: str = Field(default="2022-11-28", pattern=r"^\d{4}-\d{2}-\d{2}$")
    credential_ref: str | None = None
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @field_validator("api_version", mode="before")
    @classmethod
    def normalize_api_version(cls, value: object) -> object:
        if isinstance(value, date):
            return value.isoformat()
        return value

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if re.fullmatch(r"env://[A-Z][A-Z0-9_]+", value) is None:
            raise ValueError("credential_ref must use env://UPPER_CASE_NAME")
        return value


class SCMProvidersConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    config_version: Literal[1]
    providers: tuple[GitHubProviderConfig, ...]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> SCMProvidersConfig:
        ids = [provider.provider_id for provider in self.providers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate provider_id")
        return self


class EventRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str
    project_id: str
    run_id: str
    source_id: str
    sequence: int = Field(ge=1)
    event_type: str
    severity: Severity
    occurred_at: datetime
    payload: dict[str, Any]
    evidence_ref: str
