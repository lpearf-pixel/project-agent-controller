# 本地提示词库与跨项目经验库设计

## 1. 目的

Project Agent Controller 不能依赖单个 ChatGPT 会话记住历史。提示词、项目约束、已知问题和跨项目经验必须在本地形成可版本化、可检索、可审计的知识层。

该知识层解决四个问题：

1. 不同项目使用一致、稳定的 AI 输入格式；
2. AI 能知道项目特有约束，而不是每次重新解释；
3. 已验证的问题和解决方法可以被后续运行检索；
4. 某项目获得的经验能够在明确适用条件下复用于其他项目，同时避免错误泛化。

## 2. 核心原则

- 提示词是配置和制品，不是聊天记录；
- 每次 AI 调用记录所用模板 ID、版本和内容哈希；
- 项目私有提示词和数据默认只留本地；
- 全局模板、项目模板、任务模板和临时上下文分层组合；
- 经验必须附带证据、适用范围、置信度和复核日期；
- AI 推断不能自动升级为已确认知识；
- 本地提示词库不授予执行权限，权限仍由 Policy Controller 决定；
- 模型可替换，输出 schema 和证据要求保持稳定。

## 3. 分层结构

```text
Prompt Registry
  ├── Global Policies
  ├── Role Prompts
  ├── Workflow Prompts
  ├── Output Contracts
  ├── Project Profiles
  ├── Project Overrides
  └── Runtime Context

Knowledge Registry
  ├── Known Problems
  ├── Project Lessons
  ├── Shared Lessons
  ├── Decisions
  ├── Anti-patterns
  └── Verification Playbooks
```

建议本地目录：

```text
controller-data/knowledge/
  prompts/
    global/
    roles/
    workflows/
    output-contracts/
    project-overrides/
  projects/<project-id>/
    profile.yaml
    constraints.yaml
    known-problems/
    lessons/
    decisions/
  shared/
    lessons/
    anti-patterns/
    verification-playbooks/
  indexes/
  prompt-runs/
```

可公开的通用默认模板可以放入控制器代码仓库；包含真实路径、业务数据、账号、私有架构和项目策略的内容只能放在本地私有目录或专用私有知识仓库。

## 4. Prompt 对象模型

每个提示词使用元数据与正文分离的结构：

```yaml
prompt_id: workflow.incident-diagnosis
version: 1.2.0
status: stable
purpose: Analyze one incident from structured evidence
language: zh-CN
risk_level: analysis-only
required_inputs:
  - project_profile
  - ai_brief
  - evidence_index
optional_inputs:
  - related_known_problems
  - applicable_lessons
output_contract: incident-diagnosis-v1
applicability:
  controller_modes: [observer, diagnostic]
  technologies: [any]
prohibited_actions:
  - claim_success_without_verification
  - reveal_secrets
  - propose_destructive_action_without_warning
review_after: 2027-01-01
```

正文示例：

```text
根据提供的结构化证据分析当前 incident。

必须区分：
1. 已证实事实；
2. 与历史问题的匹配；
3. 推断与置信度；
4. 下一步最低风险验证；
5. 禁止执行的动作。

不得仅凭日志摘要宣布问题已解决。
```

## 5. Prompt 组合顺序

AI Context Builder 按固定顺序组合，避免项目内容覆盖全局安全规则：

```text
1. Global Safety Policy
2. Controller Mode Policy
3. Role Prompt
4. Workflow Prompt
5. Output Contract
6. Project Profile
7. Project Constraints
8. Applicable Known Problems
9. Applicable Lessons
10. Current AI Brief and Evidence
11. User Request
```

优先级规则：

- 上层安全与权限约束不可被项目模板覆盖；
- 项目模板可收紧权限，不能扩大权限；
- Lesson 只能提供建议和检查项，不能改变任务状态；
- Runtime Context 不允许携带新的系统级指令；
- 任何冲突都记录为 `prompt.composition.conflict` 事件。

## 6. 项目 Profile

每个项目维护简洁而稳定的项目档案：

```yaml
project_id: community-selection-miniapp
project_type: commerce-miniapp
technologies:
  - typescript
  - nodejs
  - prisma
  - postgresql
critical_domains:
  - payment
  - refund
  - privacy
branch_policy:
  protected:
    - main
    - stable/*
verification_sources:
  - command-exit-code
  - database-invariant
  - e2e-test
high_risk_actions:
  - database-migration
  - payment-state-change
  - production-deploy
project_rules:
  - refunds may be reduced but not increased beyond remaining refundable amount
```

Profile 保存长期事实，不保存当次运行日志。动态信息由 AI Brief 提供。

## 7. 本地提示词版本管理

### 7.1 版本规则

使用语义化版本：

- Major：输出 schema、职责或安全边界发生不兼容变化；
- Minor：新增可选输入、检查项或兼容能力；
- Patch：措辞修正，不改变语义。

### 7.2 不可变运行记录

每次 AI 调用生成：

```json
{
  "prompt_run_id": "prun_01...",
  "prompt_ids": [
    "global.safety@2.0.0",
    "role.diagnostic@1.1.0",
    "workflow.incident-diagnosis@1.2.0"
  ],
  "rendered_prompt_sha256": "...",
  "input_bundle_sha256": "...",
  "model_provider": "local-or-cloud-provider-id",
  "model_id": "...",
  "started_at": "...",
  "output_sha256": "...",
  "policy_decision_id": "..."
}
```

默认不把完整敏感 Prompt Run 同步到公共 SCM，只同步模板 ID、版本、哈希和结果摘要。

### 7.3 发布通道

- `draft`：仅测试，不进入默认组合；
- `candidate`：少量项目 shadow mode；
- `stable`：默认可用；
- `deprecated`：仍可读取，不再用于新运行；
- `revoked`：因安全或错误立即禁止使用。

## 8. Output Contract

提示词必须引用结构化输出契约，避免每次返回格式不一致。

诊断输出示例：

```json
{
  "facts": [
    {
      "statement": "...",
      "evidence_refs": ["artifact://..."],
      "confidence": "confirmed"
    }
  ],
  "historical_matches": [],
  "hypotheses": [],
  "next_checks": [],
  "unsafe_actions": [],
  "resolution_status": "not_verified"
}
```

系统拒绝以下输出直接改变任务状态：

- 没有证据引用的“已经修复”；
- 没有执行结果的“测试通过”；
- 只有模型置信度、没有验证证据的根因确认；
- 违反项目风险策略的操作建议。

## 9. Known Problem、Lesson 与 Prompt 的关系

三者职责不同：

| 类型 | 作用 | 是否包含项目事实 | 是否可直接跨项目 |
|---|---|---:|---:|
| Prompt | 规定 AI 如何工作和输出 | 少量 | 通用模板可以 |
| Known Problem | 描述已知故障模式和解决证据 | 可以 | 默认不可以 |
| Lesson | 抽象为可复用规则和检查项 | 不应包含私有事实 | 满足适用条件后可以 |

Known Problem 不应直接写入系统提示词正文；它由检索器按当前 incident 动态注入。Lesson 同样只在 applicability predicates 匹配时注入。

## 10. Lesson 生命周期

```text
CANDIDATE
  -> PROJECT_APPROVED
  -> SHARED_CANDIDATE
  -> SHARED_APPROVED
  -> DEPRECATED
  -> REVOKED
```

### 10.1 Candidate

来源可以是：

- 已确认 Incident；
- 人工复盘；
- Code Review 结论；
- CI 多次重复失败；
- AI 提出的候选经验。

AI 生成的条目必须标注 `generated_by_ai: true`，且默认为 Candidate。

### 10.2 Project Approved

只在原项目生效，需满足：

- 有明确来源 Incident；
- 根因或流程问题已经确认；
- 有验证方式；
- 项目负责人或明确规则批准。

### 10.3 Shared Approved

跨项目共享还需满足：

- 已移除项目私有数据；
- 适用条件可机器判断；
- 至少一个独立项目复现或人工架构评审批准；
- 存在反例和失效条件描述；
- 设置复核日期和责任来源。

## 11. 防止其他项目重复犯错

Controller 在任务开始、Incident 创建和 AI 分析前执行 Lesson Preflight：

```text
Current Project Profile
+ Command / Workload Type
+ Technology and Version
+ Planned Action
+ Risk Tags
        |
        v
Applicability Filter
        |
        v
Matched Lessons and Required Checks
```

匹配后的处理分三级：

- `inform`：加入 AI Brief 和报告；
- `warn`：在任务开始前生成警告事件；
- `block`：仅限明确、确定性、安全相关规则，并需 Policy Controller 支持。

示例：

```yaml
lesson_id: LESSON-GIT-0004
rule: Do not push when remote head has not been re-read after a publish tool rewrites commits.
enforcement: warn
applicability_predicates:
  - scm_provider: github
  - workflow: publish
required_checks:
  - read_remote_head_after_publish
```

只有确定性规则才能设为 `block`。AI 语义匹配的 Lesson 不能直接阻断任务。

## 12. 检索设计

v0.1 使用 SQLite + FTS5：

- 精确 ID；
- 标题和关键词；
- technology、component、workflow、risk tags；
- error fingerprint；
- applicability predicates；
- status 和 confidence。

排序建议：

```text
exact fingerprint
> exact technology + component + action
> exact workflow + risk
> full-text match
> optional semantic similarity
```

后续本地向量检索只作为候选召回层，最终仍需结构化适用条件过滤。

## 13. 提示词测试

提示词必须像代码一样测试。

### 13.1 Golden Cases

保存脱敏输入与预期输出结构：

```text
prompt-tests/
  incident-diagnosis/
    case-001-input.json
    case-001-expected.json
```

至少验证：

- 正确区分事实和假设；
- 不遗漏关键证据引用；
- 不把历史问题相似当成同一问题；
- 不宣布未验证成功；
- 不建议被策略禁止的动作；
- 输出符合 schema。

### 13.2 Adversarial Cases

测试输入日志中出现：

- “忽略系统指令”；
- 伪造的成功消息；
- token 和私钥样式；
- 超长重复日志；
- 来自另一个项目的错误标识；
- 已过期 Lesson。

日志内容永远视为数据，不能成为提示词指令。

### 13.3 Model Matrix

同一 Prompt Candidate 可在本地模型和云模型上 shadow 测试，比较：

- schema 合规率；
- 证据引用准确率；
- 错误泛化率；
- 成本和时延；
- 敏感信息泄漏风险。

模型变化不能自动扩大权限。

## 14. 本地与 SCM 的同步边界

### 14.1 可进入控制器公开仓库

- 通用 Prompt 模板；
- 空白 Project Profile 示例；
- Output Contract schema；
- 不含私有数据的通用 Lesson；
- Prompt 测试框架和合成测试数据。

### 14.2 只能进入私有知识仓库或本地

- 真实项目 Profile；
- 项目约束和业务规则；
- Known Problems；
- Incident 证据；
- 私有 Lesson；
- 完整 Prompt Run；
- 模型凭据和本机路径。

### 14.3 推荐双仓库

```text
project-agent-controller          公共或基础设施代码、通用模板
project-agent-knowledge-private   私有项目档案、问题、经验和提示词覆盖
```

也可以在完全私有部署后合并，但核心代码仍不应依赖固定仓库地址。

## 15. 冲突与污染控制

可能出现的问题：

- 两条 Lesson 相互矛盾；
- 项目规则与共享 Lesson 冲突；
- 旧版本解决方法不适用于新版本；
- AI 将某项目私有规则错误泛化；
- 恶意日志尝试注入提示词。

处理规则：

1. 项目明确约束优先于共享建议；
2. 安全策略优先于所有 Lesson；
3. 更精确的适用条件优先于宽泛规则；
4. 已验证且版本匹配的条目优先；
5. 冲突无法确定时同时提供给 AI，并标记 `CONFLICTED`；
6. `CONFLICTED` 条目不得用于自动阻断或自动执行；
7. 日志、Issue、代码注释和外部文档均按不可信数据处理。

## 16. 变更与回滚

Prompt 或 Lesson 更新时：

- 创建新版本，不覆盖历史版本；
- 运行 Golden 和 Adversarial 测试；
- 先进入 Candidate；
- 使用少量项目 shadow mode；
- 记录新旧输出差异；
- 通过后提升 Stable；
- 出现问题时将版本标记 Revoked，并回退到上一个 Stable；
- 已完成运行仍保留其原始版本和哈希，保证复盘可重现。

## 17. v0.1 实现边界

v0.1 实现：

- 本地 Prompt Registry；
- Global、Role、Workflow、Output Contract 四层模板；
- Project Profile 与本地 override；
- Prompt ID、版本、哈希和运行记录；
- SQLite FTS5 Known Problem 和 Lesson 检索；
- Candidate、Project Approved、Shared Approved 状态；
- 人工批准和撤销；
- 固定适用条件过滤；
- 基础 Golden Tests；
- 日志内容与提示词指令隔离。

v0.1 不实现：

- AI 自动发布 Stable Prompt；
- AI 自动批准 Shared Lesson；
- Lesson 自动修改项目代码；
- 语义相似度直接阻断任务；
- 云端集中保存全部项目知识；
- 根据聊天历史隐式改变提示词。

## 18. 验收标准

- 新 AI 会话只读取本地 Profile、Prompt 和证据包即可理解项目基本约束；
- 每次 AI 输出可追溯到确切 Prompt 版本、输入哈希和模型；
- 项目私有 Lesson 默认不会注入其他项目；
- Shared Lesson 只有适用条件匹配时才会进入 AI Brief；
- 过期、冲突或撤销条目不会用于自动阻断；
- Prompt 更新可以回放历史 Golden Cases；
- 日志中的指令文本不会覆盖系统和项目策略；
- 无向量数据库和无云端模型时，精确指纹、标签和 FTS5 检索仍可工作；
- 公共仓库扫描不包含真实项目路径、凭据、Incident 证据或私有 Prompt Run；
- AI 无法仅通过返回文本扩大 Controller 执行权限。
