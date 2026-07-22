from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
import re
from typing import Annotated, Any, Literal, TypeAlias

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
        by_compose = (
            self.compose_project is not None and self.compose_service is not None
        )
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


SourceConfig: TypeAlias = Annotated[
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

    @model_validator(mode="after")
    def validate_ci_dependencies(self) -> ProjectConfig:
        git_ids = {
            source.source_id
            for source in self.sources
            if isinstance(source, GitSourceConfig)
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
