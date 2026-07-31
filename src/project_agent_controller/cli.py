import json
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from project_agent_controller.app import create_app
from project_agent_controller.curation.briefs import BriefExportBlocked
from project_agent_controller.observer.runner import ObservationBlocked
from project_agent_controller.runtime import build_runtime
from project_agent_controller.service_environment import load_service_environment
from project_agent_controller.service_renderer import ServiceRenderInput, write_service_definition
from project_agent_controller.settings import Settings

app = typer.Typer(no_args_is_help=True, help="Local Project Agent Controller")
incident_app = typer.Typer(no_args_is_help=True, help="Inspect local incidents")
controller_app = typer.Typer(no_args_is_help=True, help="Control local observer state")
service_app = typer.Typer(no_args_is_help=True, help="Render host service definitions")
app.add_typer(incident_app, name="incident")
app.add_typer(controller_app, name="controller")
app.add_typer(service_app, name="service")
_SOURCE_KINDS = frozenset({"process", "docker", "git", "github_ci"})


@app.callback()
def bootstrap() -> None:
    load_service_environment()


@app.command()
def serve() -> None:
    settings = Settings()
    application = create_app(settings)
    uvicorn.run(application, host=settings.host, port=settings.port)


@app.command()
def status() -> None:
    runtime = build_runtime(Settings())
    typer.echo(
        json.dumps(
            {
                "state": runtime.control.get_state().value,
                "projects": [project.project_id for project in runtime.registry.list()],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@app.command("sources")
def sources(
    project_id: str,
    kind: str | None = typer.Option(None, "--kind"),
) -> None:
    """Show sanitized current source states, optionally filtered by kind."""
    if kind is not None and kind not in _SOURCE_KINDS:
        typer.echo(f"unsupported source kind: {kind}", err=True)
        raise typer.Exit(code=2)
    runtime = build_runtime(Settings())
    try:
        runtime.registry.get(project_id)
    except KeyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    payload = [
        state.model_dump(mode="json")
        for state in runtime.source_states.list(project_id)
        if kind is None or state.source_kind == kind
    ]
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


@app.command("observe-once")
def observe_once(project_id: str) -> None:
    runtime = build_runtime(Settings())
    try:
        emitted = runtime.observer.observe_once(runtime.registry.get(project_id))
    except (KeyError, ObservationBlocked) as error:
        raise typer.Exit(code=2) from error
    typer.echo(json.dumps({"project_id": project_id, "emitted_events": emitted}))


@incident_app.command("show")
def incident_show(incident_id: str, max_bytes: int = 65_536) -> None:
    runtime = build_runtime(Settings())
    try:
        brief = runtime.brief_builder.build(incident_id, max_bytes=max_bytes)
    except (KeyError, BriefExportBlocked) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(brief.to_json_bytes().decode("utf-8"))


@controller_app.command("drain")
def controller_drain(actor: str, reason: str) -> None:
    runtime = build_runtime(Settings())
    state = runtime.control.drain(actor=actor, reason=reason)
    typer.echo(state.value)


@controller_app.command("emergency-stop")
def controller_emergency_stop(actor: str, reason: str) -> None:
    runtime = build_runtime(Settings())
    state = runtime.control.emergency_stop(actor=actor, reason=reason)
    typer.echo(state.value)


@controller_app.command("clear-emergency-stop")
def controller_clear_emergency_stop(actor: str, reason: str) -> None:
    runtime = build_runtime(Settings())
    try:
        state = runtime.control.clear_emergency_stop(actor=actor, reason=reason)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(state.value)


@controller_app.command("complete-recovery")
def controller_complete_recovery(actor: str, reason: str) -> None:
    runtime = build_runtime(Settings())
    try:
        state = runtime.control.complete_recovery(actor=actor, reason=reason)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(state.value)


@service_app.command("render")
def service_render(
    platform: Annotated[str, typer.Option("--platform")],
    env_file: Annotated[Path, typer.Option("--env-file")],
    executable: Annotated[Path, typer.Option("--executable")],
    working_directory: Annotated[Path, typer.Option("--working-directory")],
    log_directory: Annotated[Path, typer.Option("--log-directory")],
    output_directory: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    try:
        target = write_service_definition(
            platform,
            ServiceRenderInput(
                executable=executable,
                working_directory=working_directory,
                env_file=env_file,
                log_directory=log_directory,
            ),
            output_directory,
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(str(target))
