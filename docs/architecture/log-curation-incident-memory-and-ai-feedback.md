# 日志精简、问题记忆与 AI 反馈设计

## 1. 目的

多项目常驻观察的难点不是“能否收集日志”，而是：

- 日志量大，不能把所有内容传给 AI 或提交到 Git；
- 不同项目的日志格式不同，容易混在一起；
- 只取最后若干行可能遗漏真正根因；
- AI 需要知道本次运行、上次成功运行和历史同类问题之间的差异；
- 某个项目已经验证过的教训，应在适用范围内帮助其他项目避免重复踩坑；
- 跨项目经验不能无边界传播，否则会把局部结论错误套用到其他技术栈。

本设计引入 Log Curator、Incident Memory、Lesson Library 和 AI Context Builder，形成“原始证据本地保存、结构化信息精简传递、经验有条件复用”的闭环。

## 2. 三种日志处理方案

### 方案 A：上传全部日志

优点是信息完整；缺点是成本高、速度慢、容易泄密，并会造成 Git 历史和 AI 上下文污染。

### 方案 B：只上传最后 N 行

优点是简单；缺点是经常丢失首次错误、环境变化、重试前症状和关键上下文，容易误判。

### 方案 C：分层日志与证据包

原始日志只在本地保存；控制器抽取结构化事件、错误窗口、运行差异和历史问题，生成有限大小的 AI Brief 和脱敏 SCM 检查点。

**采用方案 C。** 日志精简必须是可复核的派生过程，不是删除证据。

## 3. 五层日志模型

```text
L0 Raw Evidence       原始日志与原始命令输出，只留本地或私有对象存储
L1 Normalized Events  解析后的结构化事件，进入 Event Store
L2 Incident Bundle    与某次问题直接相关的最小证据包
L3 AI Brief           给 AI 的结构化上下文和受限片段
L4 SCM Checkpoint     可同步到 GitHub/私有 Git 的脱敏摘要
```

### 3.1 L0 Raw Evidence

保存完整 stdout、stderr、Docker 日志片段、CI 原始结果和文件增量。要求：

- 追加写或内容寻址，不原地篡改；
- 支持轮转、压缩、校验和和保留策略；
- 默认不进入 Git；
- 默认不直接发送给云端模型；
- 记录来源、时间范围、字节偏移和日志编码；
- 大文件采用分块索引，避免重复扫描。

### 3.2 L1 Normalized Events

Watcher 和 Parser 将原始日志转换为统一事件，例如：

```json
{
  "event_id": "evt_01...",
  "project_id": "person-relation-lab",
  "run_id": "run_01...",
  "task_id": "task_01...",
  "attempt_id": "attempt_02",
  "source_id": "docker:app",
  "sequence": 1842,
  "type": "database.import.progress",
  "severity": "info",
  "occurred_at": "2026-07-22T10:20:30+08:00",
  "payload": {
    "source_file": "1600w-1800w.csv",
    "processed_rows": 1200000,
    "total_rows": 2000000
  },
  "evidence_ref": "artifact://sha256/..."
}
```

事件保留证据引用，不能只保留自然语言总结。

### 3.3 L2 Incident Bundle

当出现失败、阻塞、异常重启或人工标记问题时，生成独立证据包：

```text
incidents/<incident-id>/
  manifest.json
  summary.md
  current-run.json
  baseline-run.json
  changes.json
  selected-events.ndjson
  snippets/
  known-problem-matches.json
  verification.json
  evidence-index.json
```

证据包至少包含：

- 当前运行身份、命令、分支、HEAD、环境指纹；
- 第一次异常和最后一次异常，而不只是日志尾部；
- 异常前后可配置上下文窗口；
- 退出码、信号、超时和资源状态；
- 最近一次成功运行或健康检查的基线；
- 从基线到本次运行的代码、配置、依赖、容器和数据变化；
- 历史同类问题匹配结果；
- 已尝试的操作及其结果；
- 原始证据引用和校验和。

### 3.4 L3 AI Brief

AI Brief 是固定 schema 的小型输入包，不是任意拼接的长日志。默认限制建议：

- 总体文本 64 KiB 以内；
- 单一日志片段 200 行以内；
- 每类重复错误最多保留 3 个代表样本；
- 超出预算时保留首次、最具代表性和最后一次事件；
- 所有裁剪都在 manifest 中记录 omitted count 和原始证据引用。

AI Brief 包含：

```json
{
  "schema_version": 1,
  "project": {},
  "current_run": {},
  "last_successful_run": {},
  "change_summary": {},
  "primary_incident": {},
  "related_known_problems": [],
  "applicable_lessons": [],
  "evidence_snippets": [],
  "constraints": [],
  "requested_output": {
    "facts": true,
    "hypotheses": true,
    "next_checks": true,
    "unsafe_actions_forbidden": true
  }
}
```

### 3.5 L4 SCM Checkpoint

SCM 只同步稳定、脱敏和可协作的结论：

- 项目、任务、运行和 incident ID；
- 当前状态及时间；
- 错误指纹和影响范围；
- 代表性脱敏片段；
- 验证命令与退出结果；
- 上次成功运行和本次变化摘要；
- 本地或私有对象存储证据索引；
- 已知问题和经验条目引用。

原始大日志、个人数据、密钥、完整数据库记录和本机绝对路径不得进入公共 SCM。

## 4. 项目如何声明“哪些日志值得看”

每个项目提供版本化的 `observability` 声明。公共仓库可以保存无敏感信息的默认模板；真实路径和私有规则放在本地覆盖配置中。

```yaml
config_version: 1
project_id: person-relation-lab

observability:
  sources:
    - id: importer-log
      kind: file
      path_ref: local-secret://projects/person-relation/importer-log
      parser: postgres-import-v1
      encoding: utf-8
      rotation: auto

    - id: app-container
      kind: docker
      selector:
        compose_project: person-relation-lab
        service: app
      parser: node-json-or-text-v1

  classes:
    critical:
      include_event_types:
        - process.crashed
        - database.import.failed
        - database.integrity.failed
      context_before: 80
      context_after: 120
      always_create_incident: true

    progress:
      include_event_types:
        - database.import.progress
      coalesce_window: 5m
      keep_first: true
      keep_last: true
      keep_percent_steps: 5

    repetitive:
      deduplicate_by: fingerprint
      representative_samples: 3
      count_suppressed: true

    noise:
      exclude_patterns:
        - "health check passed"
      retain_raw_locally: true
      include_in_ai_brief: false

  limits:
    source_bytes_per_hour: 2GiB
    incident_bundle_max: 32MiB
    ai_brief_max: 64KiB
    scm_checkpoint_max: 128KiB

  sync:
    target: private-status-repository
    mode: checkpoint-only
    triggers:
      - incident.created
      - incident.resolved
      - task.finished
    minimum_interval: 10m
```

### 4.1 Fail-closed 原则

- 未声明来源不上传；
- 未分类内容只留本地；
- 脱敏失败时禁止外发；
- 超过大小限制时生成索引，不自动放宽限制；
- Parser 失败时保留原始证据并标记 `UNPARSED`，不得伪造结构化结论。

## 5. 防止不同项目和不同运行混乱

所有数据必须携带稳定关联字段：

```text
project_id
workspace_id
run_id
task_id
attempt_id
source_id
event_sequence
incident_id
problem_id
lesson_id
```

目录按项目与运行隔离：

```text
controller-data/
  projects/<project-id>/
    runs/<run-id>/
      manifest.json
      raw/
      events.ndjson
      ai-brief.json
    incidents/<incident-id>/
    state/current.json
```

规则：

- 不允许跨项目共用同一个可写日志文件；
- 每次进程启动或外部任务重新附着都创建新的 `run_id`；
- 重试创建新的 `attempt_id`，不覆盖旧结果；
- 同一来源的事件使用单调 sequence；
- SCM 检查点路径包含稳定项目 ID 和 incident/run ID；
- 当前状态只是派生视图，历史事件不可被当前状态覆盖。

## 6. 错误指纹与去重

日志精简不能只靠 `ERROR` 关键词。系统对候选问题生成多维指纹：

```text
fingerprint = hash(
  normalized_error_code,
  normalized_message_template,
  top_stack_frames,
  command_template_id,
  component,
  exit_code_or_signal,
  selected_environment_dimensions
)
```

归一化时可移除时间戳、临时路径、PID、UUID 和动态计数，但不能删除具有诊断意义的表名、错误码、依赖版本和业务阶段。

指纹匹配分级：

- `exact`：相同错误码、组件和栈根；
- `strong`：核心模式相同，动态字段不同；
- `possible`：语义相近，需要 AI 或人工确认；
- `none`：新问题。

重复事件聚合为：

```json
{
  "fingerprint": "fp_...",
  "first_seen": "...",
  "last_seen": "...",
  "count": 18244,
  "representative_evidence": ["artifact://..."],
  "suppressed_count": 18241
}
```

## 7. 上次问题、本次问题与基线比较

每个项目维护三类基线：

1. `last_successful_run`：最近一次完整成功运行；
2. `last_comparable_run`：同一命令、数据阶段或环境的最近运行；
3. `last_related_incident`：指纹最接近的历史问题。

本次 AI Brief 必须明确回答：

- 上次成功是什么时间、commit、配置和环境；
- 本次相对上次改变了什么；
- 本次错误是否历史出现过；
- 上次如何处理、是否真正验证；
- 上次处理方案是否仍适用于当前版本；
- 本次是否属于回归、复发或新问题。

Incident 状态：

```text
DETECTED -> TRIAGED -> ROOT_CAUSE_CONFIRMED -> FIX_CANDIDATE
         -> MITIGATED -> VERIFIED_RESOLVED
         -> RECURRED
         -> CLOSED_NOT_REPRODUCIBLE
```

只有存在明确验证证据时才能进入 `VERIFIED_RESOLVED`。

## 8. Known Problem Registry

已知问题不是聊天摘要，而是结构化对象：

```yaml
problem_id: KP-PG-0007
title: PostgreSQL import temp spill exhausts storage
status: confirmed
fingerprints:
  - fp_pattern: postgres-temp-file-no-space-v1
symptoms:
  - temp_written grows rapidly
  - import stalls or reports no space left
root_cause:
  statement: candidate materialization spills excessively to temporary files
  evidence_refs:
    - incident://INC-2026-0041
resolutions:
  - action: change query batching strategy
    verification: import stage completes with bounded temp usage
applicability:
  technologies: [postgresql]
  components: [bulk-import]
  conditions:
    - dataset_rows_gt: 1000000
anti_patterns:
  - increasing retry count without checking storage
last_verified:
  date: 2026-07-20
  software_versions: ["postgresql:16"]
confidence: high
```

Known Problem 记录：症状、根因、解决方法、验证方式、适用范围、反例、版本和证据。未经验证的猜测只能作为 `candidate`，不能作为跨项目规则。

## 9. 跨项目 Lesson Library

某个项目的经验只有满足条件后才能推广：

- 根因已经确认；
- 修复或规避方案有验证证据；
- 结论能抽象成技术或流程规则；
- 适用条件可以明确描述；
- 不包含项目私有数据；
- 经过人工批准，或在多个独立 incident 中重复验证。

Lesson 模型：

```yaml
lesson_id: LESSON-OBS-0012
title: Do not treat log silence as task failure
rule: Long-running imports require an independent heartbeat before idle timeout is enabled.
source_incidents:
  - incident://person-relation/INC-2026-0032
scope:
  technologies: [postgresql, nodejs]
  workload_types: [long-running-import]
applicability_predicates:
  - expected_runtime_gt: 30m
  - progress_log_may_pause: true
required_checks:
  - process_alive
  - database_active_query
  - progress_checkpoint_age
avoid:
  - kill task solely because stdout is quiet
confidence: high
review_after: 2027-01-01
```

### 9.1 防止错误泛化

- 项目级经验默认只在原项目生效；
- 推广到技术栈级需要人工批准或重复验证；
- 每条 Lesson 必须有 applicability predicates；
- 不匹配条件时不注入 AI 上下文；
- 版本过期、依赖变化或出现反例时降级置信度；
- Lesson 不能自动扩大 Controller 权限。

## 10. AI Context Builder

AI 请求不直接读取任意目录，而由 Context Builder 根据任务组装允许输入：

```text
Prompt Template
+ Project Profile
+ Current Run Summary
+ Last Successful Baseline
+ Related Incidents
+ Applicable Known Problems
+ Applicable Lessons
+ Selected Evidence Snippets
+ Safety and Output Contract
```

检索顺序：

1. 精确 ID 和错误指纹；
2. 项目、技术栈、命令和组件标签；
3. SQLite FTS5 关键词检索；
4. 后续版本可增加本地向量检索；
5. AI 语义扩展只提供候选，不直接认定为同一问题。

AI 输出必须分为：

- 已证实事实；
- 与历史问题的匹配程度；
- 推断和置信度；
- 需要继续验证的假设；
- 建议的下一条低风险检查；
- 明确禁止的动作。

## 11. 同步策略

### 11.1 本地保存

长期保存：

- Incident manifest 和摘要；
- Known Problem；
- 已批准 Lesson；
- AI Brief 的 prompt/template 版本和内容哈希；
- 验证结果和证据索引。

按策略轮转：

- 原始日志；
- 无异常的普通运行事件；
- 重复进度样本；
- 临时 AI 上下文缓存。

### 11.2 SCM 同步

默认只同步：

```text
status/<project-id>/current.json
incidents/<project-id>/<incident-id>.md
lessons/<lesson-id>.yaml（仅批准公开或私有共享的条目）
```

建议使用独立私有状态仓库或专用状态分支。业务代码仓库只接收与 PR/Issue 直接相关的验证摘要。

### 11.3 合并与节流

- 同一项目在最小时间窗口内只同步一次当前状态；
- 进度事件合并，保留首个、阶段变化和最后一个；
- 相同错误指纹只更新计数和 last_seen；
- Incident 创建和解决可立即同步；
- 网络离线时本地排队，恢复后按幂等键补同步；
- 同步失败不影响本地观察。

## 12. 数据保留建议

默认值可按项目覆盖：

| 数据 | 默认保留 |
|---|---:|
| 普通原始日志 | 7 天 |
| 长任务原始日志 | 30 天 |
| 失败 Incident 原始证据 | 90 天 |
| Incident 摘要与索引 | 长期 |
| Known Problem | 长期，定期复核 |
| 已批准 Lesson | 长期，带 review date |
| 未确认 AI 推断 | 30 天 |
| SCM 检查点 | 按仓库策略 |

删除原始日志前必须确认仍保留校验和、时间范围和 Incident 所需证据。

## 13. 安全与隐私

上传前执行两阶段检查：

1. 规则脱敏：token、私钥、Authorization header、Cookie、邮箱、手机号、本机路径、数据库连接串；
2. 策略验证：目标仓库、数据分类、大小、允许字段和项目授权。

高敏感项目可配置：

- `local_only`：不生成任何外部同步；
- `private_scm_only`：只允许私有 provider；
- `local_model_only`：AI Brief 只交给本地模型；
- `metadata_only`：只同步状态和证据校验和。

检测到疑似秘密时：

- 阻止该批次同步；
- 生成本地安全 incident；
- 不把疑似秘密本身写入安全报告；
- 不自动继续重试外发。

## 14. v0.1 实现边界

v0.1 实现：

- 原始日志增量读取和本地轮转；
- 来源、run、source、sequence 隔离；
- 基础 Parser 接口；
- 重复指纹聚合；
- 首次错误和上下文窗口；
- Incident Bundle；
- 最近成功基线比较；
- SQLite FTS5 Known Problem 检索；
- 固定 schema AI Brief 生成，但不要求自动调用模型；
- 脱敏 checkpoint 同步到专用私有目标；
- 本地手动批准 Lesson。

v0.1 不实现：

- AI 自动认定根因；
- 自动把项目经验推广为全局 Lesson；
- 云端向量数据库；
- 原始日志自动上传；
- 自动根据 AI 建议执行命令。

## 15. 验收标准

- 10 GiB 级日志增长时不重复扫描完整文件；
- 10000 条相同错误可聚合为少量代表样本和准确计数；
- AI Brief 能同时提供本次运行、最近成功基线和历史同类 Incident；
- 不同项目、运行和 attempt 不会混写；
- 截断后的每个片段都能追溯到原始证据偏移和校验和；
- 未声明或脱敏失败的日志不会同步；
- 相同 checkpoint 重试不会产生重复提交或评论；
- 项目级 Lesson 不会在不满足适用条件的其他项目中注入；
- 删除本地原始日志前仍可复核 Incident 摘要、索引和验证结论；
- 无 AI 模型时系统仍能生成结构化 Incident 和报告。
