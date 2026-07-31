# 升级、兼容与回滚策略

## 1. 目的

Project Agent Controller 是常驻基础设施。升级失败可能导致任务重复、日志丢失、错误停止或错误同步，因此升级必须是可预检、可排空、可备份、可验证和可回滚的受控流程。

本策略适用于：

- Controller 应用版本升级；
- 数据库 schema 迁移；
- 事件 schema 演进；
- 项目配置升级；
- SCM adapter 升级；
- 单机向多节点升级；
- GitHub.com 向私有 Git provider 迁移；
- 本地模型或模型网关切换。

## 2. 版本维度

控制器同时维护多个独立版本：

```text
application_version
config_schema_version
database_schema_version
event_schema_version
plugin_api_version
provider_adapter_version
report_schema_version
```

这些版本不得被单一应用版本号隐式替代。应用升级时必须声明支持范围。

```yaml
compatibility:
  config_schema: [1, 2]
  database_schema: [3]
  event_schema_read: [1, 2]
  event_schema_write: 2
  plugin_api: 1
  report_schema: [1]
```

## 3. 兼容性原则

### 3.1 配置兼容

- 配置文件必须包含 `config_version`；
- 新字段默认可选，并有安全默认值；
- 删除字段前至少经历一个弃用周期；
- 未知高风险字段不能被静默忽略；
- 迁移工具只生成新文件或备份后替换；
- secrets 引用迁移时不得展开凭据值。

### 3.2 数据库兼容

- 迁移脚本按序执行且记录 checksum；
- 默认只允许向前迁移；
- 每个不可逆迁移必须声明原因和恢复方式；
- 大表迁移需要预估磁盘和锁时间；
- 升级前必须完成 SQLite/WAL checkpoint；
- 升级过程中禁止新任务和 SCM 写入。

### 3.3 事件兼容

事件是长期审计记录，遵循：

- 已写事件不可原地改写；
- 新字段必须可忽略；
- 字段语义不得复用；
- 破坏性变化创建新的事件类型或 schema version；
- Reader 至少支持当前版本和前一个主版本；
- Reporter 读取旧事件时标注数据完整性和降级情况。

### 3.4 Plugin 与 Adapter 兼容

插件启动时握手：

- plugin API version；
- provider type；
- capabilities；
- required permissions；
- adapter build/version；
- server compatibility；
- health probe。

不兼容插件不得加载为“部分成功”。控制器记录原因并保持核心观察功能运行。

## 4. 发布通道

### stable

用于日常常驻运行，只接收通过完整回归和升级测试的版本。

### candidate

用于一个或少量非关键项目试运行，不得接管全部项目。

### development

用于开发和契约测试，不使用真实高权限凭据，不运行关键长任务。

常驻实例不得自动跟随 `latest` 标签。

## 5. 升级前 Preflight

升级工具必须检查：

- 当前版本和目标版本支持升级路径；
- 控制器未处于未确认的 Emergency Stop；
- 活动任务数量、ownership 和类型；
- 数据盘剩余空间；
- 数据库完整性和 WAL 状态；
- 配置语法和版本；
- provider 连接与权限；
- 插件兼容性；
- 当前备份是否可用；
- 是否存在未同步的安全报告；
- 是否存在未知 orphan 或冲突 lease。

Preflight 失败时默认停止升级，不允许使用通用 `--force` 绕过。特定检查只能通过具名豁免和审计原因绕过。

## 6. 标准单机升级流程

1. 发布升级通知事件；
2. 进入 `DRAINING`；
3. 拒绝新任务；
4. 等待可完成的控制器内部任务结束；
5. 按 ownership 停止剩余可取消任务；
6. 暂停 SCM 写入；
7. flush watcher 游标和事件；
8. 完成 WAL checkpoint；
9. 创建一致性备份；
10. 验证备份可读取；
11. 安装 candidate 版本；
12. 运行配置 dry-run；
13. 运行数据库迁移；
14. 启动为 `RECOVERING`；
15. 执行与目标版本权限边界一致的 smoke test；
16. 恢复只读 watcher；
17. 恢复该版本允许的低风险内部任务；
18. 恢复 SCM 写入；
19. 进入 `ACTIVE`；
20. 保留回滚窗口和升级证据包。

v0.1 升级过程不得运行项目 test、lint、build 等验证命令；v0.2 以后才能在白名单和策略允许下恢复低风险项目验证。

## 7. 备份设计

升级备份至少包含：

```text
backup-manifest.json
controller.db
controller.db-wal（如仍存在）
controller.db-shm（如需要）
config/
project-registry/
watcher-cursors/
control-state/
report-templates/
provider-metadata/
artifact-index/
```

大型原始日志不必每次完整复制，但 manifest 必须记录：

- 路径或对象存储引用；
- 大小；
- 校验和；
- 时间范围；
- 保留策略；
- 是否已脱敏。

备份必须加密并与运行数据分离。备份成功不等于可恢复，需定期执行恢复演练。

## 8. 数据库迁移策略

### 8.1 Expand / Migrate / Contract

优先采用三阶段：

1. `expand`：增加新表或可空字段，旧代码仍可运行；
2. `migrate`：后台或升级窗口回填数据；
3. `contract`：在后续版本删除旧字段或旧路径。

### 8.2 长迁移

当 SQLite 迁移时间不可接受时：

- 先复制到新数据库文件；
- 在副本上迁移和校验；
- 原子切换数据库文件；
- 保留旧文件到回滚窗口结束；
- 不在原数据库上执行无法中断的大规模重写。

### 8.3 迁移校验

至少检查：

- 表和索引存在；
- row count 或逻辑计数；
- 关键外键或引用一致性；
- 活动 task/lease 数量；
- 事件最大 sequence；
- 随机样本反序列化；
- 报告生成 smoke test。

## 9. 回滚等级

### 9.1 应用回滚

适用：应用代码故障，但数据库 schema 仍被旧版本支持。

- drain；
- 停止新版本；
- 启动旧二进制或镜像；
- 使用现有数据库；
- 执行 smoke test；
- 恢复服务。

### 9.2 数据库回滚

适用：迁移破坏兼容性或数据错误。

- 进入 Emergency Stop 或维护模式；
- 禁止全部写入；
- 保存失败版本数据库副本；
- 恢复升级前一致性备份；
- 启动旧版本；
- 对升级窗口事件进行差异清单；
- 不自动合并失败版本产生的新数据。

### 9.3 Provider 回滚

适用：SCM adapter 或私有 Git 切换失败。

- SCM write drain；
- primary 指回原 provider；
- 对目标 provider 新对象建立清单；
- 禁止自动双向同步；
- 人工确认需要回迁的 commits 或评论；
- 恢复原 provider 后重新验证幂等键。

### 9.4 配置回滚

配置更新采用版本目录和原子软链接或指针切换：

```text
config-revisions/
  rev-0001/
  rev-0002/
current -> rev-0002
```

回滚切换到已验证 revision，并生成审计事件。

## 10. 升级后 Smoke Test

所有版本必须验证：

- API 健康；
- 数据库 schema 版本；
- 可读取旧事件；
- 项目注册数量；
- watcher 可建立游标；
- task 状态未丢失；
- stop API 可用；
- Emergency Stop 状态被正确继承；
- provider 只读能力；
- 控制器内部只读探针；
- 报告生成；
- SCM 幂等 dry-run。

v0.2 以后可额外运行一个明确白名单的低风险项目验证命令。Smoke test 未通过时不恢复 SCM 写入和自动调度。

## 11. Canary 升级

当进入多节点或多实例阶段：

- 先升级一个 controller candidate 或一个 worker；
- 只分配非关键项目；
- 比较事件、资源和任务完成率；
- 验证旧、新节点协议兼容；
- 保持数据库写者单一或使用明确 leader；
- 通过观察窗口后逐步扩大；
- 任一安全指标异常即停止扩容。

## 12. 单机到多节点的升级路径

避免直接把 SQLite 共享给多个节点。推荐顺序：

1. 单机 Controller + SQLite；
2. Controller 与本机 Worker 进程分离；
3. 引入远程 Worker，但 Controller 仍单实例；
4. Artifact Store 外置；
5. Event Store 从 SQLite 迁移到 PostgreSQL；
6. 引入消息队列或可靠任务分发；
7. 最后考虑 Controller 高可用。

每一步都保留单机回退路径。

## 13. 模型 Provider 升级

AI 模型不是事实来源。切换或升级模型时：

- 固定诊断输入证据包；
- 保存 prompt/template 版本；
- 对比结论、证据引用和成本；
- 先运行 shadow mode，不影响任务状态；
- 本地模型与云模型使用同一领域输出 schema；
- 模型失败时退化为规则报告；
- 不因模型升级自动扩大执行权限。

## 14. 弃用策略

- 配置字段、API 和插件接口弃用必须提前声明；
- 至少提供一个稳定版本的兼容窗口；
- 启动时提示弃用，不立即失败；
- 到达移除版本前提供自动迁移工具；
- 涉及安全的弃用可缩短周期，但必须给出明确阻断原因。

## 15. 升级证据包

每次升级生成：

```text
upgrade-id
source-version
target-version
started-at / finished-at
preflight-result
active-task-summary
backup-manifest
migration-list
migration-checksums
smoke-test-result
rollback-deadline
operator / actor
exceptions and waivers
```

证据包默认保存在本地；同步到 SCM 时只发布脱敏摘要。

## 16. 验收标准

1. 从任一受支持 stable 版本可按声明路径升级。
2. 升级前无法通过 preflight 时不会开始迁移。
3. 升级期间不会接收新任务或执行 SCM 写入。
4. 备份可在隔离目录完成恢复演练。
5. 数据库迁移具有 checksum 和执行记录。
6. 新版本可读取旧事件并生成报告。
7. Smoke test 未通过时自动保持维护或降级状态。
8. 应用回滚和数据库回滚流程均有自动化测试。
9. Provider 切换失败可通过配置回到原 provider。
10. 回滚不会自动覆盖未知 commits 或丢弃升级窗口证据。
11. v0.1 升级流程不会运行任何项目业务命令。
