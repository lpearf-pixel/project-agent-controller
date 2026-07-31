from pathlib import Path

from dotenv import dotenv_values

from project_agent_controller.config.loader import load_projects
from project_agent_controller.domain.models import DockerSourceConfig, GitHubCISourceConfig

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_EXAMPLE = ROOT / "config/projects.community-selection.example.yaml"
ENV_EXAMPLE = ROOT / "config/project-agent-controller.env.example"


def test_community_selection_example_combines_production_git_ci_and_docker_sources() -> None:
    config = load_projects(PROJECTS_EXAMPLE)

    assert len(config.projects) == 1
    project = config.projects[0]
    assert project.project_id == "community-selection-miniapp"
    assert len(project.tasks) == 1
    task = project.tasks[0]
    assert task.task_id == "lint"
    assert task.executable == "node"
    assert task.arguments == ("scripts/lint-placeholder.js",)
    assert task.repository_ref == "local://community-selection-miniapp"
    assert len({source.source_id for source in project.sources}) == len(project.sources)

    ci_source = next(
        source for source in project.sources if isinstance(source, GitHubCISourceConfig)
    )
    assert ci_source.repository == "lpearf-pixel/community-selection-miniapp"
    assert ci_source.git_source_id == "repository"

    docker_sources = [
        source for source in project.sources if isinstance(source, DockerSourceConfig)
    ]
    assert {
        (source.selector.compose_project, source.selector.compose_service)
        for source in docker_sources
    } == {
        ("community-selection-miniapp", "api"),
        ("community-selection-miniapp", "edge"),
        ("community-selection-miniapp", "postgres"),
    }
    assert all(source.selector.compose_service != "admin" for source in docker_sources)


def test_community_selection_example_contains_no_host_path_dsn_or_literal_secret() -> None:
    text = PROJECTS_EXAMPLE.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "local://community-selection-miniapp" in text
    assert "/users/" not in lowered
    assert "/home/" not in lowered
    assert "database_url" not in lowered
    assert "postgresql://" not in lowered
    assert "github_token" not in lowered
    assert "password" not in lowered


def test_service_environment_example_uses_only_replaceable_placeholders() -> None:
    values = dotenv_values(ENV_EXAMPLE, interpolate=False)

    assert values["PAC_GITHUB_TOKEN"] == "replace-with-read-only-token"
    assert values["PAC_PROJECTS_FILE"] == "replace-with-absolute-projects-file"
    assert values["PAC_LOCAL_REPOS_ROOT"] == "replace-with-absolute-repositories-root"
    assert values["PAC_DATA_DIR"] == "replace-with-absolute-data-directory"
    assert all("lpearf" not in (value or "").lower() for value in values.values())
