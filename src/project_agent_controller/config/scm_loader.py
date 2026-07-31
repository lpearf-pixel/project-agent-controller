from pathlib import Path

import yaml

from project_agent_controller.domain.models import SCMProvidersConfig


def load_scm_providers(path: Path) -> SCMProvidersConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SCMProvidersConfig.model_validate(raw)
