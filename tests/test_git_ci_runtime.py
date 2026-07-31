from pathlib import Path

from project_agent_controller.observer.git_provider import UnavailableGitProvider
from project_agent_controller.observer.github_ci_provider import UnavailableCIProvider
from project_agent_controller.runtime import build_runtime
from project_agent_controller.settings import Settings


def write(path: Path, text: str) -> None:
    path.write_text(text.strip(), encoding="utf-8")


def make_settings(tmp_path: Path) -> Settings:
    projects = tmp_path / "projects.yaml"
    providers = tmp_path / "providers.yaml"
    write(
        projects,
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: repository
        kind: git
        path_ref: local://demo
      - source_id: github-ci
        kind: github_ci
        provider_id: github-cloud
        repository: owner/repo
        git_source_id: repository
""",
    )
    write(
        providers,
        """
config_version: 1
providers:
  - provider_id: github-cloud
    kind: github
    api_base_url: https://api.github.com
    credential_ref: env://PAC_TEST_TOKEN
""",
    )
    return Settings(
        data_dir=tmp_path / "data",
        projects_file=projects,
        scm_providers_file=providers,
        local_sources_root=tmp_path / "sources",
        local_repos_root=tmp_path / "repos",
        knowledge_dir=None,
        git_executable=Path("/usr/bin/git"),
    )


def test_runtime_wires_git_and_ci_observers(tmp_path: Path) -> None:
    runtime = build_runtime(make_settings(tmp_path))
    assert runtime.observer.git_observer is not None
    assert "github-cloud" in runtime.observer.ci_observers


def test_runtime_uses_unavailable_providers_without_startup_failure(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "git_executable": Path("/missing/git"),
            "scm_providers_file": tmp_path / "missing-providers.yaml",
        }
    )
    runtime = build_runtime(settings)
    assert isinstance(runtime.observer.git_observer.provider, UnavailableGitProvider)
    assert isinstance(
        runtime.observer.ci_observers["github-cloud"].provider,
        UnavailableCIProvider,
    )
