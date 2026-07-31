from pathlib import Path

import pytest

from project_agent_controller.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        projects_file=tmp_path / "projects.yaml",
        knowledge_dir=tmp_path / "knowledge",
    )
