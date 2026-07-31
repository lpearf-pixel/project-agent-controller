from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from project_agent_controller.control.service import ControlService
from project_agent_controller.domain.models import ProjectConfig, TaskTemplateConfig
from project_agent_controller.runner.executor import TaskExecutor
from project_agent_controller.runner.service import TaskRunnerService
from project_agent_controller.storage.database import Database


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True, stdin=subprocess.DEVNULL)


with tempfile.TemporaryDirectory(prefix="pac-v02-preflight-") as temporary:
    root = Path(temporary)
    repositories = root / "repos"
    repository = repositories / "fixture"
    repository.mkdir(parents=True)
    run("git", "init", "-q", str(repository))
    run("git", "-C", str(repository), "config", "user.name", "PAC Preflight")
    run(
        "git",
        "-C",
        str(repository),
        "config",
        "user.email",
        "pac@example.invalid",
    )
    (repository / "tracked.txt").write_text("committed\n", encoding="utf-8")
    run("git", "-C", str(repository), "add", "tracked.txt")
    run("git", "-C", str(repository), "commit", "-qm", "fixture")
    (repository / "untracked-secret.txt").write_text("must-not-copy\n", encoding="utf-8")

    python = shutil.which("python3")
    if python is None:
        raise SystemExit("v0.2 preflight requires python3")
    project = ProjectConfig(
        project_id="fixture",
        display_name="Fixture",
        tasks=(
            TaskTemplateConfig(
                task_id="verify",
                repository_ref="local://fixture",
                executable=Path(python).name,
                arguments=(
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('tracked.txt').read_text() == 'committed\\n'; "
                    "assert not Path('untracked-secret.txt').exists(); "
                    "Path('generated.txt').write_text('isolated'); print('verified')",
                ),
            ),
        ),
    )
    database = Database(root / "data" / "controller.db")
    database.initialize()
    service = TaskRunnerService(
        database,
        ControlService(database),
        TaskExecutor(repositories, root / "data" / "runner-workspaces"),
    )

    first = service.run(project, "verify", "preflight-1")
    repeated = service.run(project, "verify", "preflight-1")

    assert first.state == "success"
    assert repeated.run_id == first.run_id
    assert database.count_task_attempts(first.run_id) == 1
    assert not (repository / "generated.txt").exists()
    assert (repository / "untracked-secret.txt").read_text() == "must-not-copy\n"

print("v0.2 isolated idempotent task preflight passed")
