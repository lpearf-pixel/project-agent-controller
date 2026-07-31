from pathlib import Path

import pytest

from project_agent_controller.config.loader import load_projects


def test_load_projects_requires_stable_unique_ids(tmp_path: Path) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: app-log
        kind: file
        path_ref: local://demo.log
  - project_id: demo
    display_name: Duplicate
    sources: []
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate project_id: demo"):
        load_projects(path)


def test_load_projects_rejects_literal_absolute_paths(tmp_path: Path) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: app-log
        kind: file
        path_ref: /Users/example/private.log
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="path_ref must use local://"):
        load_projects(path)


def test_load_projects_accepts_fixed_verification_task(tmp_path: Path) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    tasks:
      - task_id: verify
        repository_ref: local://demo
        executable: uv
        arguments: [run, pytest, -q]
        environment:
          CI: "1"
        timeout_seconds: 300
        max_attempts: 2
""".strip(),
        encoding="utf-8",
    )

    config = load_projects(path)

    task = config.projects[0].tasks[0]
    assert task.task_id == "verify"
    assert task.arguments == ("run", "pytest", "-q")


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        ("repository_ref: /tmp/demo", "repository_ref must use local://"),
        ("repository_ref: local://../escape", "repository_ref must stay inside"),
        ("working_directory: ../escape", "working_directory must stay inside"),
        ("executable: /bin/sh", "executable must be a bare command name"),
        ("arguments: [\"a\\u0000b\"]", "arguments must not contain NUL"),
        ("environment: {GITHUB_TOKEN: secret}", "credential-shaped environment key"),
        ("environment: {PATH: /tmp/bin}", "runner-controlled environment key"),
    ],
)
def test_load_projects_rejects_unsafe_task_templates(
    tmp_path: Path, fragment: str, message: str
) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        f"""
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    tasks:
      - task_id: verify
        repository_ref: local://demo
        executable: uv
        arguments: [run, pytest]
        {fragment}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_projects(path)


def test_load_projects_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    tasks:
      - task_id: verify
        repository_ref: local://demo
        executable: uv
      - task_id: verify
        repository_ref: local://demo
        executable: python3
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate task_id"):
        load_projects(path)
