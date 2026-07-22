from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SOURCE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,63}$"


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
    def validate_mode(self) -> "DockerSelector":
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


SourceConfig: TypeAlias = Annotated[
    FileSourceConfig | ProcessSourceConfig | DockerSourceConfig,
    Field(discriminator="kind"),
]


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(pattern=SOURCE_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    technologies: tuple[str, ...] = ()
    sources: tuple[SourceConfig, ...] = ()


class ProjectsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    config_version: Literal[1]
    projects: tuple[ProjectConfig, ...]


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
