from pathlib import Path

import pytest

from project_agent_controller.config.loader import load_projects
from project_agent_controller.config.scm_loader import load_scm_providers


def write(path: Path, text: str) -> Path:
    path.write_text(text.strip(), encoding="utf-8")
    return path


def test_git_and_ci_sources_load_with_dependency(tmp_path: Path) -> None:
    path = write(
        tmp_path / "projects.yaml",
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: repository
        kind: git
        path_ref: local://repos/demo
      - source_id: github-ci
        kind: github_ci
        provider_id: github-cloud
        repository: owner/demo
        git_source_id: repository
""",
    )
    config = load_projects(path)
    project = config.projects[0]
    assert [source.kind for source in project.sources] == ["git", "github_ci"]


def test_ci_source_requires_existing_git_source(tmp_path: Path) -> None:
    path = write(
        tmp_path / "projects.yaml",
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: github-ci
        kind: github_ci
        provider_id: github-cloud
        repository: owner/demo
        git_source_id: missing
""",
    )
    with pytest.raises(ValueError, match="git_source_id"):
        load_projects(path)


def test_provider_loader_accepts_env_credential_ref(tmp_path: Path) -> None:
    path = write(
        tmp_path / "scm.yaml",
        """
config_version: 1
providers:
  - provider_id: github-cloud
    kind: github
    api_base_url: https://api.github.com
    api_version: 2022-11-28
    credential_ref: env://PAC_GITHUB_TOKEN
""",
    )
    config = load_scm_providers(path)
    assert config.providers[0].provider_id == "github-cloud"


def test_provider_loader_rejects_literal_token(tmp_path: Path) -> None:
    path = write(
        tmp_path / "scm.yaml",
        """
config_version: 1
providers:
  - provider_id: github-cloud
    kind: github
    api_base_url: https://api.github.com
    credential_ref: ghp_secret
""",
    )
    with pytest.raises(ValueError, match="credential_ref"):
        load_scm_providers(path)
