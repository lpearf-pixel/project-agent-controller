from project_agent_controller.domain.models import ProjectConfig, ProjectsConfig


class ProjectRegistry:
    def __init__(self, config: ProjectsConfig) -> None:
        self.config = config
        self._projects = {project.project_id: project for project in config.projects}

    def list(self) -> tuple[ProjectConfig, ...]:
        return self.config.projects

    def get(self, project_id: str) -> ProjectConfig:
        try:
            return self._projects[project_id]
        except KeyError as error:
            raise KeyError(f"project not found: {project_id}") from error
