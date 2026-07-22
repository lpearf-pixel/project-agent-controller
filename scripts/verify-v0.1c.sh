#!/usr/bin/env bash
set -euo pipefail

MODE="${PAC_VERIFY_MODE:-full}"
export PYTHONPATH="${PYTHONPATH:-src}"

python3 - <<'PY'
from pathlib import Path

from project_agent_controller.observer.github_transport import (
    GitHubTransportError,
    validate_github_request,
)

text = Path("src/project_agent_controller/observer/git_transport.py").read_text(encoding="utf-8")
assert "shell=False" in text
for forbidden in ('"fetch"', '"pull"', '"push"', '"commit"', '"reset"', '"checkout"', '"merge"', '"rebase"'):
    assert forbidden not in text
sha = "a" * 40
good = f"/repos/owner/repo/commits/{sha}/check-runs"
validate_github_request("GET", good)
for method in ("POST", "PATCH", "PUT", "DELETE"):
    try:
        validate_github_request(method, good)
    except GitHubTransportError:
        pass
    else:
        raise AssertionError(f"write method was accepted: {method}")
for path in (
    "/repos/owner/repo/actions/runs/1/rerun",
    "/repos/owner/repo/actions/jobs/1/logs",
):
    try:
        validate_github_request("GET", path)
    except GitHubTransportError:
        pass
    else:
        raise AssertionError(f"control/log path was accepted: {path}")
print("v0.1C read-only capability scan passed")
PY

if [[ "$MODE" == "offline" ]]; then
  python3 -m pytest -q
  python3 -m compileall -q src
  python3 - <<'PY'
from typer.testing import CliRunner
from project_agent_controller.cli import app
result = CliRunner().invoke(app, ["--help"])
if result.exit_code != 0:
    raise SystemExit(result.exit_code)
PY
  ./scripts/preflight-v0.1c.sh
  echo "v0.1C offline verification passed"
  exit 0
fi

if [[ "$MODE" != "full" ]]; then
  echo "PAC_VERIFY_MODE must be 'full' or 'offline'" >&2
  exit 2
fi

command -v uv >/dev/null 2>&1 || {
  echo "uv is required for full verification" >&2
  exit 1
}
[[ -f uv.lock ]] || {
  echo "uv.lock is required for full verification" >&2
  exit 1
}

uv sync --frozen
uv run ruff check .
uv run mypy src
uv run pytest -q
uv run pac --help >/dev/null
if [[ "${PAC_PREFLIGHT_GITHUB:-0}" != "1" ]]; then
  echo "PAC_PREFLIGHT_GITHUB=1 is required for full v0.1C verification" >&2
  exit 1
fi
./scripts/preflight-v0.1c.sh

echo "v0.1C full verification passed"
