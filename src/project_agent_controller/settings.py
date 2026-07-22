from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAC_", extra="forbid")

    data_dir: Path = Field(default=Path.home() / ".local/share/project-agent-controller")
    projects_file: Path = Field(
        default=Path.home() / ".config/project-agent-controller/projects.yaml"
    )
    knowledge_dir: Path | None = None
    host: str = "127.0.0.1"
    port: int = 9090
    poll_interval_seconds: float = 1.0

    @property
    def database_path(self) -> Path:
        return self.data_dir / "controller.db"
