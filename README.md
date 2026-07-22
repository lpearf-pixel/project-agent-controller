# Project Agent Controller

面向个人多项目研发环境的本地优先常驻控制平面，用于增量观察日志、进程和 Docker 容器，保存可审计事件，聚合重复问题，生成受限 AI Brief，并读取私有 Prompt / Known Problem / Lesson 仓库。

> 当前实现阶段：**v0.1B System Observers（Draft）**。
>
> v0.1B 仍然只观察：不执行项目命令、不停止或重启进程/容器、不调用 AI、不写业务仓库，也不上传原始日志。

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

## 明确未实现

- Git 和 CI watcher；
- test、lint、build、shell 或容器 exec；
- kill、terminate、stop、restart 或自动修复；
- 模型调用；
- 原始日志上传；
- Git commit、push、PR、Issue 或评论写入；
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
```

真实绝对路径、Docker Socket 和凭据不得写入项目 YAML 或 Git。

配置参考：

- `config/projects.example.yaml`：文件日志源；
- `config/projects.system-observers.example.yaml`：Process/Docker 首批项目模板；
- `docs/onboarding/v0.1b-first-projects.md`：本机预检步骤。

## 启动和查询

```bash
uv sync
uv run pac serve
```

```bash
curl http://127.0.0.1:9090/health
curl http://127.0.0.1:9090/v1/projects
curl http://127.0.0.1:9090/v1/projects/person-relation-lab/sources
uv run pac sources person-relation-lab
```

## 停止

```bash
uv run pac controller drain --actor local-admin --reason maintenance
uv run pac controller emergency-stop --actor local-admin --reason 'unexpected activity'
uv run pac controller clear-emergency-stop --actor local-admin --reason 'risk removed'
```

解除 Emergency Stop 只进入 `RECOVERING`，不会自动恢复观察或重跑任务。

## 验证

离线门禁：

```bash
PAC_VERIFY_MODE=offline ./scripts/verify-v0.1b.sh
```

完整门禁：

```bash
./scripts/verify-v0.1b.sh
```

完整模式 fail-closed，要求 `uv.lock`、`uv sync --frozen`、Ruff、mypy strict、pytest、CLI smoke test 和 Docker Provider 合约测试。

## 设计文档

- [总体架构设计](docs/superpowers/specs/2026-07-22-project-agent-controller-design.md)
- [v0.1A 实施计划](docs/superpowers/plans/2026-07-22-v0.1a-file-observer-foundation.md)
- [v0.1B System Observers 设计](docs/superpowers/specs/2026-07-22-v0.1b-system-observers-design.md)
- [v0.1B 实施计划](docs/superpowers/plans/2026-07-22-v0.1b-system-observers.md)
- [日志精简、问题记忆与 AI 反馈设计](docs/architecture/log-curation-incident-memory-and-ai-feedback.md)
- [本地提示词库与跨项目经验库设计](docs/architecture/local-prompt-and-lesson-library.md)
- [SCM 可迁移与私有 Git 设计](docs/architecture/scm-portability-and-private-git.md)
- [停止、熔断与恢复规范](docs/operations/stop-circuit-breaker-and-recovery.md)
- [版本演进路线图](docs/roadmap/evolution-roadmap.md)
