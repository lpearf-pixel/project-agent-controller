from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller.domain.models import EventRecord


class SourceState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    source_id: str
    source_kind: Literal["process", "docker"]
    sequence: int = Field(ge=0)
    observed_at: datetime
    state: dict[str, Any]


class SourceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[EventRecord, ...]
    state: SourceState
    incident_candidates: tuple[tuple[str, EventRecord], ...] = ()
