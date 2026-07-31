#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-src}"

python3 - <<'PY'
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess

from project_agent_controller.domain.models import GitSourceConfig
from project_agent_controller.observer.git_provider import GitRepositoryProvider
from project_agent_controller.observer.git_source import GitSourceObserver
from project_agent_controller.observer.git_transport import GitReadTransport

executable = shutil.which("git")
if not executable:
    raise SystemExit("git executable is unavailable")
with TemporaryDirectory() as directory:
    root = Path(directory)
    repo = root / "demo"
    repo.mkdir()
    subprocess.run([executable, "init", "-q", str(repo)], check=True)
    subprocess.run([executable, "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run([executable, "-C", str(repo), "config", "user.name", "Preflight"], check=True)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([executable, "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run([executable, "-C", str(repo), "commit", "-qm", "initial"], check=True)

    observer = GitSourceObserver(
        GitRepositoryProvider(root, GitReadTransport(Path(executable)))
    )
    source = GitSourceConfig(source_id="repository", path_ref="local://demo")
    now = datetime.now(UTC)
    first = observer.observe("preflight", "run-1", source, None, now=now)
    second = observer.observe(
        "preflight", "run-1", source, first.state, now=now + timedelta(seconds=1)
    )
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    dirty = observer.observe(
        "preflight", "run-1", source, second.state, now=now + timedelta(seconds=2)
    )
    assert [event.event_type for event in first.events] == ["git.available"]
    assert second.events == ()
    assert [event.event_type for event in dirty.events] == ["git.dirty.entered"]
    assert dirty.state.state["head_sha"] and len(dirty.state.state["head_sha"]) == 40
    assert dirty.state.state["remote_tracking_only"] is True
print("v0.1C real temporary Git preflight passed")
PY

if [[ "${PAC_PREFLIGHT_GITHUB:-0}" == "1" ]]; then
  : "${PAC_V01C_PUBLIC_REPOSITORY:?PAC_V01C_PUBLIC_REPOSITORY is required}"
  : "${PAC_V01C_PUBLIC_SHA:?PAC_V01C_PUBLIC_SHA is required}"
  python3 - <<'PY'
import os

from project_agent_controller.observer.github_ci_provider import GitHubCIProvider
from project_agent_controller.observer.github_transport import GitHubReadTransport

repository = os.environ["PAC_V01C_PUBLIC_REPOSITORY"]
sha = os.environ["PAC_V01C_PUBLIC_SHA"]
provider = GitHubCIProvider(
    GitHubReadTransport(
        "https://api.github.com",
        credential_ref="env://PAC_GITHUB_TOKEN" if os.getenv("PAC_GITHUB_TOKEN") else None,
    )
)
from project_agent_controller.observer.github_transport import GitHubTransportError

try:
    snapshot = provider.snapshot(
        repository,
        sha,
        previous=None,
        max_check_runs=100,
        max_failed_checks=20,
    )
except GitHubTransportError as error:
    raise SystemExit(
        f"v0.1C GitHub preflight blocked: {error.kind}: {error}"
    ) from None
assert snapshot.head_sha == sha
print(
    "v0.1C GitHub GET-only preflight passed:",
    repository,
    snapshot.overall,
    snapshot.total_checks,
)
PY
fi
