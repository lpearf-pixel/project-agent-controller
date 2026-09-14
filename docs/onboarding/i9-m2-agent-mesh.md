# i9 + M2 Agent Mesh (opt-in)

This standalone CLI lives in the same GitHub project as PAC, without changing PAC observers or its controlled runner. Python 3.11+ standard library, Git, SSH and an existing M2 Ollama are sufficient. Only i9 creates worktrees or invokes Codex. M2 serves its existing local Ollama; it never needs to expose port 11434 on the LAN. No token or credentials are stored in Git.

## M2 (one time)

Clone this same repository on M2, then:

```bash
cd project-agent-controller
bash scripts/setup-m2-agent.sh
```

If the model is missing, run `ollama pull qwen2.5-coder:7b` (or export MESH_MODEL with a locally installed text model), then rerun setup. Enable macOS **System Settings > General > Sharing > Remote Login** and allow the i9 user. This is the only machine-specific SSH configuration.

## i9 (one time)

```bash
git clone https://github.com/lpearf-pixel/project-agent-controller.git
cd project-agent-controller
git switch codex/i9-m2-agent-mesh
bash scripts/setup-i9-agent.sh
export MESH_M2_SSH='your-user@your-M2-LAN-IP'
bash scripts/connect-m2-agent.sh
python3 dual_agent.py doctor
python3 dual_agent.py smoke
```

Until the branch is merged, M2 also needs `git switch codex/i9-m2-agent-mesh` after clone. The SSH tunnel may ask for the M2 account password or host-key verification; it forwards i9 localhost:11435 to M2 localhost:11434. To reconnect after reboot, rerun `bash scripts/connect-m2-agent.sh`. If port 11435 is occupied, export MESH_TUNNEL_PORT and MESH_OLLAMA_URL=http://127.0.0.1:PORT using the same PORT.

If i9 has no Codex executable in PATH, install it using the [official CLI installer](https://github.com/openai/codex#installing-and-running-codex-cli) and log in once on i9. Running without `--code` does not require Codex.

## Real task

Copy `config/dual-agent-task.example.json` to a private file outside Git; set `repo` to the actual i9 project directory and `base` to a locally existing ref. Read the project's AGENTS.md and status docs before selecting the scope. A task never fetches or pushes.

```bash
python3 dual_agent.py run /path/to/task.json
python3 dual_agent.py run /path/to/coding-task.json --code
```

The first command does local research, executes only explicitly listed test argv arrays, and writes a bounded JSON report. `--code` additionally calls `codex exec --sandbox workspace-write` inside a new detached Git worktree, then reviews the Git diff with local Qwen. Task IDs are single use. Each test runs in the worktree without a shell. Exit status is nonzero on failures. Reports are under `~/.local/share/project-agent-mesh/reports`; worktrees remain for human review. Inspect untracked files as well as the diff. Commit or push manually after inspection. Cloud token counts are deliberately recorded as unknown; Ollama input/output use API counts. No unverifiable “saved token” figure is shown.

## Verification sequence

1. M2 setup checks local Ollama and the selected model.
2. i9 setup executes deterministic unit tests; `bash -n` is also checked in CI.
3. Tunnel + `doctor` confirm the actual i9-to-M2 API/model path.
4. `smoke` creates a temporary Git repository, calls real M2 inference, creates a detached worktree and runs a real Git test.
5. A real task checks repo-specific tests and reports. A `--code` task requires a working authenticated Codex CLI and is the final end-to-end acceptance check.

If any step fails, keep its diagnostic output and the report; do not mark the machines validated until the real commands succeed on both.
