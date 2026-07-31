# Project Agent Controller

面向个人多项目研发环境的本地优先常驻控制平面，用于增量观察日志、进程、Docker、Git 工作树和 CI 检查，保存可审计事件，聚合重复问题，生成受限 AI Brief，并读取私有 Prompt / Known Problem / Lesson 仓库。

> 当前实现阶段：**v0.2 Controlled Runner（Draft）**。
>
> Observer 仍然只读；Runner 只执行固定模板，并在 committed `HEAD` 的临时归档副本中运行。它不修改业务工作树、不重跑 CI、不调用 AI，也不上传原始日志。

## 已实现

### v0.1A 文件与问题记忆

- 声明式项目和文件日志源注册；
- `local://` 路径引用与根目录越界防护；
- SQLite WAL 事件、游标、Incident 和控制状态；
- 完整行增量读取、半行等待、截断、轮转和缺失恢复；
- FastAPI lifespan 常驻轮询和手动观察互斥；
- 重复错误指纹、最多三个代表样本和 suppressed count；
- 脱敏、fail-closed 与有字节上限的 AI Brief；
- 私有 Prompt / Known Problem / Lesson 索引；
- 事件、游标和 Incident 原子提交。

### v0.1B 进程与 Docker

- Process/Docker/File 判别式 Source 配置；
- PID 文件定位与 `PID + create_time` 身份识别；
- 进程状态、CPU、RSS、子进程数和 heartbeat；
- CPU/RSS 阈值跨越与 90% 滞回恢复；
- Docker Compose label 或精确容器名 selector；
- Docker state、health、restart、exit、OOM 和内存快照；
- GET-only Docker Engine Unix Socket transport；
- 有时间戳、去重和单轮上限的 Docker stdout/stderr 日志；
- 高频快照、低频 transition/threshold/heartbeat 事件；
- 通用 `source_states` 当前快照表；
- Event、SourceState 和 Incident 原子提交；
- `GET /v1/projects/{project_id}/sources` 与 `pac sources`；
- Emergency Stop 在 Provider 调用前阻断全部观察。

### v0.1C Git 与 CI

- 固定参数、`shell=False` 的 Git porcelain v2 读取；
- 本地 HEAD、branch、upstream、ahead/behind、dirty、conflict 和 detached 状态；
- ahead/behind 明确标注为本地 tracking ref 证据，不执行 fetch；
- GitHub/GHES check-runs 与 combined commit status GET-only 查询；
- Token 仅使用 `env://` 引用，API/事件不返回 Authorization 或绝对仓库路径；
- ETag/304、rate-limit/auth/network 退避和失败去重；
- CI 错误按 HEAD SHA + check name + conclusion 建立身份并进入 Incident；
- `GET /v1/projects/{project_id}/sources?kind=git|github_ci`；
- `pac sources <project-id> --kind git|github_ci`。

### v0.1D 宿主常驻与社区甄选接入

- 私有、0600、64 KiB 上限的 `PAC_ENV_FILE`，只接受 PAC 与代理键；
- launchd/systemd 用户服务的确定性 render-only 生成；
- 异常重启限速，不自动清除 drain、急停或恢复状态；
- 社区甄选生产 `api`、`edge`、`postgres`、Git 与 CI 的统一只读模板；
- `clear-emergency-stop` 后必须显式 `complete-recovery`；
- Ubuntu Docker GET-only 与 macOS LaunchAgent 公共 Runner 门禁。

### v0.2 受控验证执行器

- 项目级固定 executable、argv、环境和资源上限模板；
- `git archive HEAD` 临时工作区，不复制未跟踪文件、忽略文件或 `.git`；
- `shell=False`、临时 HOME、凭据环境隔离、输出限额与脱敏；
- 超时终止完整进程组，最多三次 attempt；
- SQLite 幂等请求和有界 attempt 审计；
- 连续失败熔断、冷却后单探针与成功自动闭合；
- 全局 drain、急停、恢复和 degraded 状态在进程启动前 fail-closed；
- 社区甄选首个模板仅允许隔离执行无依赖安装的 lint 扫描入口。

## 明确未实现

- 任意参数命令、shell 或容器 exec；
- kill、terminate、stop、restart 或自动修复；
- 模型调用；
- 原始日志上传；
- Git fetch/pull/push/commit/reset/checkout/merge/rebase；
- CI 重跑、完整 job log 下载、PR/Issue/评论或 status 写入；
- 自动部署或数据库破坏性操作。

## 环境与本机配置

- Python 3.12 或 3.13；
- HTTP 仅允许 `127.0.0.1`、`::1` 或 `localhost`；
- 推荐 `uv`；
- Docker 推荐 rootless Socket 或 GET-only Socket Proxy。

```bash
export PAC_PROJECTS_FILE="$HOME/.config/project-agent-controller/projects.yaml"
export PAC_LOCAL_SOURCES_ROOT="$HOME/project-agent-sources"
export PAC_DATA_DIR="$HOME/.local/share/project-agent-controller"
export PAC_KNOWLEDGE_DIR="$HOME/dev/project-agent-knowledge-private"
export PAC_DOCKER_SOCKET="$HOME/.docker/run/docker.sock"
export PAC_LOCAL_REPOS_ROOT="$HOME/dev"
export PAC_GIT_EXECUTABLE="$(command -v git)"
export PAC_SCM_PROVIDERS_FILE="$HOME/.config/project-agent-controller/scm-providers.yaml"
export PAC_GITHUB_TOKEN="<read-only token>"
```

真实绝对路径、Docker Socket 和凭据不得写入项目 YAML 或 Git。

配置参考：

- `config/projects.example.yaml`：文件日志源；
- `config/projects.system-observers.example.yaml`：Process/Docker 首批项目模板；
- `docs/onboarding/v0.1b-first-projects.md`：Process/Docker 本机预检；
- `config/projects.git-ci.example.yaml`：Git/CI Source 模板；
- `config/scm-providers.example.yaml`：GitHub/GHES Provider 模板；
- `docs/onboarding/v0.1c-git-ci-projects.md`：Git freshness 与 CI 预检。

## 启动和查询

```bash
uv sync
uv run pac serve
```

```bash
curl http://127.0.0.1:9090/health
curl http://127.0.0.1:9090/v1/projects
curl 'http://127.0.0.1:9090/v1/projects/chan-shuo/sources?kind=git'
curl 'http://127.0.0.1:9090/v1/projects/chan-shuo/sources?kind=github_ci'
uv run pac sources chan-shuo --kind git
uv run pac sources chan-shuo --kind github_ci
uv run pac task run community-selection-miniapp lint manual-20260731-001
```

## 停止

```bash
uv run pac controller drain local-admin maintenance
uv run pac controller emergency-stop local-admin 'unexpected activity'
uv run pac controller clear-emergency-stop local-admin 'risk removed'
uv run pac controller complete-recovery local-admin 'preflight passed'
```

解除 Emergency Stop 只进入 `RECOVERING`，不会自动恢复观察或重跑任务。

## 验证

离线门禁：

```bash
PAC_VERIFY_MODE=offline ./scripts/verify-v0.1c.sh
```

完整门禁：

```bash
./scripts/verify-v0.1c.sh
```

完整模式 fail-closed，要求 `uv.lock`、Ruff、mypy strict、pytest、真实临时 Git、隔离任务、GitHub GET-only、Docker GET-only 和 Emergency Stop 门禁。

## 设计文档

- [总体架构设计](docs/superpowers/specs/2026-07-22-project-agent-controller-design.md)
- [v0.1A 实施计划](docs/superpowers/plans/2026-07-22-v0.1a-file-observer-foundation.md)
- [v0.1B System Observers 设计](docs/superpowers/specs/2026-07-22-v0.1b-system-observers-design.md)
- [v0.1B 实施计划](docs/superpowers/plans/2026-07-22-v0.1b-system-observers.md)
- [v0.1C Git/CI 设计](docs/superpowers/specs/2026-07-22-v0.1c-git-ci-observers-design.md)
- [v0.1C 实施计划](docs/superpowers/plans/2026-07-22-v0.1c-git-ci-observers.md)
- [日志精简、问题记忆与 AI 反馈设计](docs/architecture/log-curation-incident-memory-and-ai-feedback.md)
- [本地提示词库与跨项目经验库设计](docs/architecture/local-prompt-and-lesson-library.md)
- [SCM 可迁移与私有 Git 设计](docs/architecture/scm-portability-and-private-git.md)
- [停止、熔断与恢复规范](docs/operations/stop-circuit-breaker-and-recovery.md)
- [版本演进路线图](docs/roadmap/evolution-roadmap.md)
- [社区甄选宿主常驻手册](docs/onboarding/community-selection-host-service.md)
- [v0.2 Controlled Runner 设计](docs/superpowers/specs/2026-07-31-v0.2-controlled-runner-design.md)
- [v0.2 实施计划](docs/superpowers/plans/2026-07-31-v0.2-controlled-runner.md)
