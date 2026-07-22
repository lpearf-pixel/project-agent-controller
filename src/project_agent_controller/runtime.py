from dataclasses import dataclass
from uuid import uuid4

from project_agent_controller.config.loader import load_projects
from project_agent_controller.control.service import ControlService
from project_agent_controller.curation.briefs import BriefBuilder
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.domain.models import ProjectsConfig
from project_agent_controller.knowledge.index import KnowledgeIndex
from project_agent_controller.observer.daemon import ObserverDaemon
from project_agent_controller.observer.runner import ObserverRunner
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
    daemon: ObserverDaemon
    brief_builder: BriefBuilder
    knowledge_index: KnowledgeIndex


def build_runtime(settings: Settings) -> Runtime:
    database = Database(settings.database_path)
    database.initialize()
    if settings.projects_file.exists():
        projects = load_projects(settings.projects_file)
    else:
        projects = ProjectsConfig(config_version=1, projects=())
    registry = ProjectRegistry(projects)
    control = ControlService(database)
    incident_service = IncidentService(database)
    observer = ObserverRunner(
        database,
        control,
        local_root=settings.local_sources_root,
        run_id=f"run-{uuid4()}",
        incident_service=incident_service,
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
        daemon=daemon,
        brief_builder=brief_builder,
        knowledge_index=knowledge_index,
    )
