from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller import __version__
from project_agent_controller.curation.briefs import BriefExportBlocked
from project_agent_controller.observer.runner import ObservationBlocked

if TYPE_CHECKING:
    from project_agent_controller.runtime import Runtime

router = APIRouter()


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)


def runtime_from(request: Request) -> "Runtime":
    return request.app.state.runtime


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    runtime = runtime_from(request)
    return {
        "status": "ok",
        "application_version": __version__,
        "controller_state": runtime.control.get_state().value,
    }


@router.get("/v1/projects")
def list_projects(request: Request) -> list[dict[str, object]]:
    runtime = runtime_from(request)
    return [
        {
            "project_id": project.project_id,
            "display_name": project.display_name,
            "technologies": list(project.technologies),
            "sources": [
                {"source_id": source.source_id, "kind": source.kind}
                for source in project.sources
            ],
        }
        for project in runtime.registry.list()
    ]


@router.get("/v1/projects/{project_id}/sources")
def list_source_states(project_id: str, request: Request) -> list[dict[str, Any]]:
    runtime = runtime_from(request)
    try:
        runtime.registry.get(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [
        state.model_dump(mode="json")
        for state in runtime.source_states.list(project_id)
    ]


@router.get("/v1/projects/{project_id}/events")
def list_events(
    project_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, object]]:
    runtime = runtime_from(request)
    try:
        runtime.registry.get(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return [
        event.model_dump(mode="json")
        for event in runtime.database.list_events(project_id, limit=limit)
    ]


@router.post("/v1/projects/{project_id}/observe-once")
def observe_once(project_id: str, request: Request) -> dict[str, object]:
    runtime = runtime_from(request)
    try:
        project = runtime.registry.get(project_id)
        emitted = runtime.observer.observe_once(project)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ObservationBlocked as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"project_id": project_id, "emitted_events": emitted}


@router.get("/v1/incidents/{incident_id}/brief")
def incident_brief(
    incident_id: str,
    request: Request,
    max_bytes: Annotated[int, Query(ge=512, le=1_048_576)] = 65_536,
) -> dict[str, object]:
    runtime = runtime_from(request)
    try:
        brief = runtime.brief_builder.build(incident_id, max_bytes=max_bytes)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BriefExportBlocked as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return brief.model_dump(mode="json")


@router.post("/v1/controller/drain")
def drain(payload: ControlRequest, request: Request) -> dict[str, str]:
    state = runtime_from(request).control.drain(actor=payload.actor, reason=payload.reason)
    return {"state": state.value}


@router.post("/v1/controller/emergency-stop")
def emergency_stop(payload: ControlRequest, request: Request) -> dict[str, str]:
    state = runtime_from(request).control.emergency_stop(
        actor=payload.actor,
        reason=payload.reason,
    )
    return {"state": state.value}


@router.post("/v1/controller/emergency-stop/clear")
def clear_emergency_stop(payload: ControlRequest, request: Request) -> dict[str, str]:
    try:
        state = runtime_from(request).control.clear_emergency_stop(
            actor=payload.actor,
            reason=payload.reason,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"state": state.value}
