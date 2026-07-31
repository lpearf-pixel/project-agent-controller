from pathlib import Path

from project_agent_controller.settings import Settings


def test_settings_default_to_local_only(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", projects_file=tmp_path / "projects.yaml")

    assert settings.host == "127.0.0.1"
    assert settings.port == 9090
    assert settings.knowledge_dir is None
    assert settings.data_dir == tmp_path / "data"
