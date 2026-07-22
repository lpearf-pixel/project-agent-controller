from pathlib import Path

import yaml

from project_agent_controller.domain.models import ProjectsConfig


def load_projects(path: Path) -> ProjectsConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = ProjectsConfig.model_validate(raw)

    project_ids = [project.project_id for project in config.projects]
    for project_id in project_ids:
        if project_ids.count(project_id) > 1:
            raise ValueError(f"duplicate project_id: {project_id}")

    for project in config.projects:
        source_ids = [source.source_id for source in project.sources]
        for source_id in source_ids:
            if source_ids.count(source_id) > 1:
                raise ValueError(f"duplicate source_id in {project.project_id}: {source_id}")

    return config
