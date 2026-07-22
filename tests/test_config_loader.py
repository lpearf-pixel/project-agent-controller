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
