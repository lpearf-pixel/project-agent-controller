from dataclasses import dataclass
from uuid import uuid4

from project_agent_controller.config.loader import load_projects
from project_agent_controller.control.service import ControlService
from project_agent_controller.curation.briefs import BriefBuilder
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.domain.models import (
    DockerSourceConfig,
    ProcessSourceConfig,
    ProjectsConfig,
)
from project_agent_controller.knowledge.index import KnowledgeIndex
from project_agent_controller.observer.daemon import ObserverDaemon
from project_agent_controller.observer.docker_provider import (
    DockerEngineProvider,
    UnavailableDockerProvider,
)
from project_agent_controller.observer.docker_source import DockerSourceObserver
from project_agent_controller.observer.docker_transport import UnixSocketDockerTransport
from project_agent_controller.observer.process_provider import PsutilProcessProvider
from project_agent_controller.observer.process_source import ProcessSourceObserver
from project_agent_controller.observer.runner import ObserverRunner
from project_agent_controller.observer.state_store import SourceStateStore
from project_agent_controller.registry.service import ProjectRegistry
from project_agent_controller.settings import Settings
from project_agent_controller.storage.database import Database


@dataclass(frozen=True, slots=True)
class Runtime:
    settings: Settings
    database: Database
    registry: ProjectRegistry
    control: ControlService
    observer: ObserverRunner
    source_states: SourceStateStore
    daemon: ObserverDaemon
    brief_builder: BriefBuilder
    knowledge_index: KnowledgeIndex


def build_runtime(settings: Settings) -> Runtime:
    database = Database(settings.database_path)
    database.initialize()
    projects = (
        load_projects(settings.projects_file)
        if settings.projects_file.exists()
        else ProjectsConfig(config_version=1, projects=())
    )
    registry = ProjectRegistry(projects)
    control = ControlService(database)
    incident_service = IncidentService(database)
    all_sources = tuple(source for project in projects.projects for source in project.sources)
    process_observer = None
    if any(isinstance(source, ProcessSourceConfig) for source in all_sources):
        process_observer = ProcessSourceObserver(
            settings.local_sources_root,
            PsutilProcessProvider(),
        )
    docker_observer = None
    if any(isinstance(source, DockerSourceConfig) for source in all_sources):
        if settings.docker_socket is None:
            docker_provider = UnavailableDockerProvider()
        else:
            docker_provider = DockerEngineProvider(
                UnixSocketDockerTransport(settings.docker_socket)
            )
        docker_observer = DockerSourceObserver(docker_provider)
    source_states = SourceStateStore(database)
    observer = ObserverRunner(
        database,
        control,
        local_root=settings.local_sources_root,
        run_id=f"run-{uuid4()}",
        incident_service=incident_service,
        process_observer=process_observer,
        docker_observer=docker_observer,
        source_states=source_states,
    )
    daemon = ObserverDaemon(
        registry,
        observer,
        control,
        poll_interval_seconds=settings.poll_interval_seconds,
    )
    brief_builder = BriefBuilder(database, Redactor())
    knowledge_index = KnowledgeIndex(database)
    if settings.knowledge_dir is not None and settings.knowledge_dir.exists():
        knowledge_index.rebuild(settings.knowledge_dir)
    return Runtime(
        settings=settings,
        database=database,
        registry=registry,
        control=control,
        observer=observer,
        source_states=source_states,
        daemon=daemon,
        brief_builder=brief_builder,
        knowledge_index=knowledge_index,
    )
