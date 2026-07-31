from dataclasses import dataclass
from uuid import uuid4

from project_agent_controller.config.loader import load_projects
from project_agent_controller.config.scm_loader import load_scm_providers
from project_agent_controller.control.service import ControlService
from project_agent_controller.curation.briefs import BriefBuilder
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.domain.models import (
    DockerSourceConfig,
    GitHubCISourceConfig,
    GitSourceConfig,
    ProcessSourceConfig,
    ProjectsConfig,
    SCMProvidersConfig,
)
from project_agent_controller.knowledge.index import KnowledgeIndex
from project_agent_controller.observer.daemon import ObserverDaemon
from project_agent_controller.observer.docker_provider import (
    DockerEngineProvider,
    UnavailableDockerProvider,
)
from project_agent_controller.observer.docker_source import DockerSourceObserver
from project_agent_controller.observer.docker_transport import UnixSocketDockerTransport
from project_agent_controller.observer.git_provider import (
    GitRepositoryProvider,
    UnavailableGitProvider,
)
from project_agent_controller.observer.git_source import GitSnapshotProvider, GitSourceObserver
from project_agent_controller.observer.git_transport import GitReadTransport
from project_agent_controller.observer.github_ci_provider import (
    GitHubCIProvider,
    UnavailableCIProvider,
)
from project_agent_controller.observer.github_ci_source import CIProvider, GitHubCISourceObserver
from project_agent_controller.observer.github_transport import GitHubReadTransport
from project_agent_controller.observer.process_provider import PsutilProcessProvider
from project_agent_controller.observer.process_source import ProcessSourceObserver
from project_agent_controller.observer.runner import CIObserver, ObserverRunner
from project_agent_controller.observer.state_store import SourceStateStore
from project_agent_controller.registry.service import ProjectRegistry
from project_agent_controller.runner.executor import TaskExecutor
from project_agent_controller.runner.service import TaskRunnerService
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
    tasks: TaskRunnerService


def build_runtime(settings: Settings) -> Runtime:
    database = Database(settings.database_path)
    database.initialize()
    projects = (
        load_projects(settings.projects_file)
        if settings.projects_file.exists()
        else ProjectsConfig(config_version=1, projects=())
    )
    providers = (
        load_scm_providers(settings.scm_providers_file)
        if settings.scm_providers_file.exists()
        else SCMProvidersConfig(config_version=1, providers=())
    )
    provider_map = {provider.provider_id: provider for provider in providers.providers}
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
        docker_provider = (
            UnavailableDockerProvider()
            if settings.docker_socket is None
            else DockerEngineProvider(UnixSocketDockerTransport(settings.docker_socket))
        )
        docker_observer = DockerSourceObserver(docker_provider)

    git_observer = None
    if any(isinstance(source, GitSourceConfig) for source in all_sources):
        executable = settings.git_executable
        if executable is None or not executable.exists():
            git_provider: GitSnapshotProvider = UnavailableGitProvider()
        else:
            git_provider = GitRepositoryProvider(
                settings.local_repos_root,
                GitReadTransport(executable.resolve()),
            )
        git_observer = GitSourceObserver(git_provider)

    ci_observers: dict[str, CIObserver] = {}
    ci_provider_ids = {
        source.provider_id for source in all_sources if isinstance(source, GitHubCISourceConfig)
    }
    for provider_id in sorted(ci_provider_ids):
        config = provider_map.get(provider_id)
        if config is None:
            ci_provider: CIProvider = UnavailableCIProvider(
                f"SCM provider is not configured: {provider_id}"
            )
        else:
            ci_provider = GitHubCIProvider(
                GitHubReadTransport(
                    str(config.api_base_url),
                    api_version=config.api_version,
                    credential_ref=config.credential_ref,
                    timeout_seconds=config.timeout_seconds,
                )
            )
        ci_observers[provider_id] = GitHubCISourceObserver(ci_provider)

    source_states = SourceStateStore(database)
    observer = ObserverRunner(
        database,
        control,
        local_root=settings.local_sources_root,
        run_id=f"run-{uuid4()}",
        incident_service=incident_service,
        process_observer=process_observer,
        docker_observer=docker_observer,
        git_observer=git_observer,
        ci_observers=ci_observers,
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
    tasks = TaskRunnerService(
        database,
        control,
        TaskExecutor(
            settings.local_repos_root,
            settings.data_dir / "runner-workspaces",
            settings.git_executable,
            lambda: control.get_state().value == "ACTIVE",
        ),
    )
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
        tasks=tasks,
    )
