# 社区甄选 Project Agent Controller 宿主常驻手册

本手册把 v0.1D 以只读 Observer 方式运行在 macOS 或 Linux 宿主。控制器不执行社区甄选项目命令，不修改 Git，不重跑 CI，不停止业务容器，也不写 PostgreSQL。

## 1. 准备私有配置

先在控制器仓库完成冻结安装：

```bash
export PAC_REPO_DIR="/absolute/path/to/project-agent-controller"
cd "$PAC_REPO_DIR"
uv sync --frozen
```

创建不进入 Git 的配置目录：

```bash
export PAC_CONFIG_DIR="$HOME/.config/project-agent-controller"
export PAC_ENV_FILE="$PAC_CONFIG_DIR/controller.env"
mkdir -p "$PAC_CONFIG_DIR"
cp config/project-agent-controller.env.example "$PAC_ENV_FILE"
cp config/projects.community-selection.example.yaml "$PAC_CONFIG_DIR/projects.yaml"
cp config/scm-providers.example.yaml "$PAC_CONFIG_DIR/scm-providers.yaml"
chmod 600 "$PAC_ENV_FILE" "$PAC_CONFIG_DIR/projects.yaml" "$PAC_CONFIG_DIR/scm-providers.yaml"
```

编辑 `controller.env`，把全部 `replace-with-*` 替换为当前宿主的绝对路径。`PAC_LOCAL_REPOS_ROOT` 必须是包含 `community-selection-miniapp/` 的父目录；项目 YAML 继续使用 `local://community-selection-miniapp`。PostgreSQL 地址、账号与密码不放入 Controller 配置。

公共仓库可先删除 `PAC_GITHUB_TOKEN` 行；需要提高 GitHub API 限额时，只使用 read-only Token。代理只能写入私有环境文件。若代理位于另一台局域网主机，应同时设置大小写 `HTTP_PROXY`/`HTTPS_PROXY`，不要只设置 `ALL_PROXY`。

Docker Desktop 常见 Socket 为 `$HOME/.docker/run/docker.sock`，Colima 常见为 `$HOME/.colima/default/docker.sock`。把实际存在的绝对路径写入 `PAC_DOCKER_SOCKET`，不要复制到项目 YAML 或 Git。

先执行只读预检：

```bash
PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" status
PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" observe-once community-selection-miniapp
```

## 2. macOS i9 主机：LaunchAgent

Controller 原生运行在 macOS，才能正确观察宿主进程；不要把它放进 Docker Desktop 或 Colima 容器。
下面通过 `pac service render` 生成固定参数的 LaunchAgent 定义，不直接安装其他系统文件。

```bash
export PAC_DATA_DIR="$HOME/.local/share/project-agent-controller"
export PAC_LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$PAC_DATA_DIR/logs" "$PAC_LAUNCH_AGENTS"

PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" service render \
  --platform launchd \
  --env-file "$PAC_ENV_FILE" \
  --executable "$PAC_REPO_DIR/.venv/bin/pac" \
  --working-directory "$PAC_REPO_DIR" \
  --log-directory "$PAC_DATA_DIR/logs" \
  --output-dir "$PAC_LAUNCH_AGENTS"

launchctl bootstrap "gui/$(id -u)" \
  "$PAC_LAUNCH_AGENTS/com.openai.project-agent-controller.plist"
launchctl print "gui/$(id -u)/com.openai.project-agent-controller"
```

LaunchAgent 只在异常退出后重启，最短间隔 30 秒。它不会清除数据库中的 `DRAINED`、`EMERGENCY_STOP` 或 `RECOVERING` 状态。

查看日志：

```bash
tail -F "$PAC_DATA_DIR/logs/controller.stdout.log" \
  "$PAC_DATA_DIR/logs/controller.stderr.log"
```

## 3. Linux 主机：systemd user service

```bash
export PAC_DATA_DIR="$HOME/.local/share/project-agent-controller"
export PAC_SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$PAC_DATA_DIR/logs" "$PAC_SYSTEMD_DIR"

PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" service render \
  --platform systemd \
  --env-file "$PAC_ENV_FILE" \
  --executable "$PAC_REPO_DIR/.venv/bin/pac" \
  --working-directory "$PAC_REPO_DIR" \
  --log-directory "$PAC_DATA_DIR/logs" \
  --output-dir "$PAC_SYSTEMD_DIR"

systemctl --user daemon-reload
systemctl --user enable --now project-agent-controller.service
systemctl --user status project-agent-controller.service
journalctl --user-unit project-agent-controller.service -f
```

systemd 在五分钟内最多连续启动三次，异常退出后等待 30 秒。用户退出后仍需常驻时，由管理员为该用户启用 linger。

## 4. 健康和两周期验收

```bash
curl -fsS http://127.0.0.1:9090/health
curl -fsS http://127.0.0.1:9090/v1/projects
curl -fsS http://127.0.0.1:9090/v1/projects/community-selection-miniapp/sources
```

等待两个 `PAC_POLL_INTERVAL_SECONDS` 周期后再次查询。无状态变化时只能看到快照更新，不应出现线性增长的 lifecycle 事件。确认 API、edge、postgres selector 均唯一；若使用的 Compose project 名不是 `community-selection-miniapp`，先修私有项目 YAML，不能放宽成模糊匹配。

## 5. 排空、急停与显式恢复

维护前排空：

```bash
PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" \
  controller drain local-admin "planned maintenance"
```

发现误观察、凭据风险或异常活动时执行 Emergency Stop：

```bash
PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" \
  controller emergency-stop local-admin "unexpected activity"
```

急停状态保存在 `controller.db`，重启服务不会绕过。排除风险后分两步恢复：

```bash
PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" \
  controller clear-emergency-stop local-admin "risk removed"

# 此时必须先检查 status、配置、磁盘和 Source；状态仍为 RECOVERING。
PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" status

PAC_ENV_FILE="$PAC_ENV_FILE" "$PAC_REPO_DIR/.venv/bin/pac" \
  controller complete-recovery local-admin "preflight passed"
```

## 6. 停止与卸载

macOS 停止并取消注册：

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.openai.project-agent-controller.plist"
```

Linux 停止并取消开机启动：

```bash
systemctl --user disable --now project-agent-controller.service
systemctl --user daemon-reload
```

卸载服务不会删除 `controller.db`、私有项目配置、知识库或本地证据。确认不再需要历史后，应另行备份并由人工明确处理；服务脚本绝不自动删除这些数据。
