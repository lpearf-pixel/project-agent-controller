# Project Agent Controller

面向个人多项目研发环境的本地优先常驻控制平面，用于增量观察项目日志、保存可审计事件、聚合重复问题、生成受限 AI Brief，并读取私有 Prompt / Known Problem / Lesson 仓库。

> 当前实现阶段：**v0.1A File Observer（Draft）**。
>
> v0.1A 不执行项目命令、不调用 AI、不写入业务仓库，也不上传原始日志。

## v0.1A 已实现

- 声明式项目和文件日志源注册；
- `local://` 路径引用与根目录越界防护；
- SQLite WAL 追加事件、日志游标和控制状态；
- 完整行增量读取、半行等待、日志截断和轮转识别；
- 缺失日志首次告警、持续缺失合并、恢复事件；
- FastAPI 生命周期驱动的常驻轮询；
- 后台轮询与手动观察的进程内互斥；
- `ACTIVE / DRAINING / DRAINED / EMERGENCY_STOP / RECOVERING / DEGRADED`；
- 重复错误指纹、Incident 聚合和最多三个代表样本；
- Authorization、Token、邮箱、手机号和用户目录脱敏；
- NUL、替换字符和异常控制字符触发 fail-closed；
- 最大字节数受限、可复现 JSON 的 AI Brief；
- 私有知识仓库 YAML / Markdown Prompt 索引；
- 项目 Lesson 隔离、共享 Lesson 审批条件和技术栈过滤；
- 精确错误指纹优先于 FTS5 全文匹配；
- 事件、游标和 Incident 在同一事务提交或回滚；
- 本地 FastAPI 查询接口和 Typer CLI。

## 明确未实现

- Docker、进程、Git 和 CI watcher；
- 自动运行 test、lint、build 或任意 shell；
- 模型调用和 AI 自动诊断；
- 原始日志上传；
- Git commit、push、PR、Issue 或评论写入；
- 自动修改代码、合并、部署或数据库破坏性操作。

这些能力分别属于后续 v0.1B、v0.1C、v0.2、v0.3 和 v0.4，不得提前穿透权限边界。

## 环境

- Python 3.12 或 3.13；
- 推荐使用 `uv`；
- HTTP 服务默认且仅允许绑定 `127.0.0.1`、`::1` 或 `localhost`。

## 配置

复制示例：

```bash
cp config/projects.example.yaml ~/.config/project-agent-controller/projects.yaml
```

项目文件使用稳定 ID 和 `local://` 引用：

```yaml
config_version: 1
projects:
  - project_id: example-project
    display_name: Example Project
    technologies: [python]
    sources:
      - source_id: application-log
        kind: file
        path_ref: local://example/application.log
        parser: text-v1
```

本机真实目录通过环境变量提供，不进入项目配置或 Git：

```bash
export PAC_PROJECTS_FILE="$HOME/.config/project-agent-controller/projects.yaml"
export PAC_LOCAL_SOURCES_ROOT="$HOME/project-agent-sources"
export PAC_DATA_DIR="$HOME/.local/share/project-agent-controller"
export PAC_KNOWLEDGE_DIR="$HOME/dev/project-agent-knowledge-private"
```

上述示例日志对应：

```text
$PAC_LOCAL_SOURCES_ROOT/example/application.log
```

## 启动常驻服务

联网环境安装依赖：

```bash
uv sync
uv run pac serve
```

服务启动后，FastAPI lifespan 会自动轮询所有已注册文件源。

查询状态：

```bash
curl http://127.0.0.1:9090/health
curl http://127.0.0.1:9090/v1/projects
```

## CLI

```bash
uv run pac status
uv run pac observe-once example-project
uv run pac incident show <incident-id>
uv run pac controller drain --actor local-admin --reason maintenance
uv run pac controller emergency-stop --actor local-admin --reason "unexpected activity"
uv run pac controller clear-emergency-stop --actor local-admin --reason "risk removed"
```

解除 Emergency Stop 只进入 `RECOVERING`，不会自动重跑停止前任务。

## 验证

当前离线环境可执行：

```bash
PAC_VERIFY_MODE=offline ./scripts/verify-v0.1a.sh
```

离线模式运行 pytest、Python 编译检查和 CLI help。完整发布门禁：

```bash
./scripts/verify-v0.1a.sh
```

完整模式会 fail-closed，并要求：

- 已生成并审核的 `uv.lock`；
- `uv sync --frozen`；
- Ruff；
- mypy strict；
- pytest；
- CLI smoke test。

## 私有知识仓库

真实项目 Profile、Prompt、Known Problem 和 Lesson 存放于私有仓库 `project-agent-knowledge-private`。原始大日志仍只保存在本地证据库，不进入控制器仓库或知识仓库。

Controller 只索引：

```text
prompts/**/*.{yaml,yml,md}
projects/*/known-problems/**/*.{yaml,yml,md}
projects/*/lessons/**/*.{yaml,yml,md}
shared/lessons/**/*.{yaml,yml,md}
```

## 设计文档

- [总体架构设计](docs/superpowers/specs/2026-07-22-project-agent-controller-design.md)
- [v0.1A 实施计划](docs/superpowers/plans/2026-07-22-v0.1a-file-observer-foundation.md)
- [日志精简、问题记忆与 AI 反馈设计](docs/architecture/log-curation-incident-memory-and-ai-feedback.md)
- [本地提示词库与跨项目经验库设计](docs/architecture/local-prompt-and-lesson-library.md)
- [SCM 可迁移与私有 Git 设计](docs/architecture/scm-portability-and-private-git.md)
- [停止、熔断与恢复规范](docs/operations/stop-circuit-breaker-and-recovery.md)
- [版本演进路线图](docs/roadmap/evolution-roadmap.md)
- [升级、兼容与回滚策略](docs/roadmap/upgrade-compatibility-and-rollback.md)
