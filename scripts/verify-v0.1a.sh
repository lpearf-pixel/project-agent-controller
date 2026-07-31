#!/usr/bin/env bash
set -euo pipefail

MODE="${PAC_VERIFY_MODE:-full}"

if [[ "$MODE" == "offline" ]]; then
  export PYTHONPATH="${PYTHONPATH:-src}"
  python3 -m pytest -q
  python3 -m compileall -q src
  python3 - <<'PY'
from typer.testing import CliRunner
from project_agent_controller.cli import app

result = CliRunner().invoke(app, ["--help"])
if result.exit_code != 0:
    raise SystemExit(result.exit_code)
PY
  echo "v0.1A offline verification passed"
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

echo "v0.1A full verification passed"
