from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FileSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    kind: Literal["file"] = "file"
    path_ref: str
    encoding: str = "utf-8"
    parser: str = "text-v1"

    @field_validator("path_ref")
    @classmethod
    def validate_path_ref(cls, value: str) -> str:
        if not value.startswith("local://"):
            raise ValueError("path_ref must use local://")
        if value == "local://":
            raise ValueError("path_ref must not be empty")
        return value


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    technologies: tuple[str, ...] = ()
    sources: tuple[FileSourceConfig, ...] = ()


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
