# 停止、熔断与恢复规范

## 1. 目的与适用范围

常驻控制器必须优先保证“可停、停得干净、停后可查、重启不误执行”。停止不是单一 `kill` 命令，而是一组分层控制语义。

本规范覆盖：

- 控制器内部任务停止；
- 明确登记为 `owned` 的外部进程停止；
- 项目暂停；
- 全局排空；
- 紧急停止；
- 自动熔断；
- 服务重启后的状态恢复；
- 无法优雅退出时的升级终止；
- 停止期间的证据保存和 SCM 同步策略。

v0.1 不主动启动项目 test、build、导入或部署命令。对于 `borrowed`、`shared` 和 `external` 资源，控制器只能停止 watcher、断开连接或暂停新观察任务，不得直接终止业务进程。

## 2. 停止层级

### 2.1 Task Stop

用途：终止一个控制器内部 task attempt，或终止一个已明确登记为 `owned` 且允许自动停止的外部进程，不影响同项目其他任务。

```text
pac task stop <task-id>
```

语义：

1. Task Engine 校验 ownership 和停止权限；
2. 状态改为 `STOPPING`；
3. 停止接收该任务的新子步骤；
4. 向受管进程组发送协作式取消信号；
5. 刷新 stdout、stderr、事件和游标；
6. 等待宽限期；
7. 未退出则按策略升级终止；
8. 记录最终退出原因；
9. 状态进入 `CANCELLED`，而不是 `FAILED` 或 `SUCCEEDED`。

对非 `owned` 资源，Task Stop 只停止控制器对该资源的跟踪或连接。

### 2.2 Project Pause

用途：暂停一个项目的新控制器任务，并根据策略处理当前受管任务。

```text
pac project pause <project-id> --mode drain
pac project pause <project-id> --mode stop
```

- `drain`：不接收新内部任务，允许当前内部任务结束；
- `stop`：不接收新内部任务，并停止当前可取消任务及 `owned` 资源；
- `borrowed`、`shared` 和 `external` 资源不被终止。

暂停不等于删除注册信息。Watcher 默认继续只读观察；如暂停原因涉及安全或日志泄漏，可以停止相关 watcher。

### 2.3 Controller Drain

用途：控制器维护、升级或关机前排空所有内部任务。

```text
pac controller drain
```

语义：

- 拒绝新任务；
- 保持 API 查询和只读 watcher 工作；
- 等待活动内部任务完成；
- 到达 drain deadline 后按 ownership 和项目策略停止剩余任务；
- 完成数据库 checkpoint 和证据 flush；
- 标记为 `DRAINED`。

### 2.4 Emergency Stop

用途：存在凭据泄漏、误执行、磁盘故障、控制器进程失控或用户明确要求立即停止时使用。

```text
pac controller emergency-stop
```

语义：

- 原子设置全局 `EMERGENCY_STOP`；
- 立即拒绝全部新执行和 SCM 写操作；
- 终止控制器内部执行任务；
- 对已授权自动停止的 `owned` 资源发送终止信号；
- 对其他资源停止 watcher 或断开连接，不批量杀死业务进程；
- 停止自动重试；
- 只保留最小证据写入和只读查询；
- 不自动恢复，必须执行显式解除流程。

紧急停止不自动删除文件、不回滚业务数据、不强制重置 Git。

## 3. 控制状态

控制器状态：

```text
ACTIVE
DRAINING
DRAINED
EMERGENCY_STOP
RECOVERING
DEGRADED
```

项目状态：

```text
ACTIVE
PAUSING
PAUSED
DEGRADED
BLOCKED
```

停止控制以数据库状态为权威来源。可额外生成本地哨兵文件用于运维可见性，但不得只依赖文件存在与否。

```text
controller-data/control/
  controller-state.json
  EMERGENCY_STOP
  projects/<project-id>/PAUSED
```

哨兵文件由控制器写入。人工创建时，控制器将其导入为带 actor、来源和时间戳的控制事件。

## 4. 终止升级顺序

对允许停止的本地进程：

1. 应用级取消通道；
2. 向进程组发送 `SIGINT` 或平台等价信号；
3. 等待 `interrupt_grace_period`；
4. 发送 `SIGTERM`；
5. 等待 `terminate_grace_period`；
6. 发送 `SIGKILL` 或平台等价强制终止；
7. 验证所有已登记子进程是否退出。

对 Docker：

1. 只处理被登记为 `owned` 的容器；
2. 不停止未被任务拥有的共享服务；
3. 先使用容器 stop timeout；
4. 超时后才 kill；
5. 保存容器 ID、镜像、最后日志游标和退出信息。

所有停止都以进程组、容器 ownership 和 task correlation ID 为边界，禁止按模糊名称批量杀进程。

## 5. Ownership 模型

资源登记至少包含：

- task ID 与 attempt；
- PID/进程组 ID；
- 容器 ID；
- 启动时间；
- 来源和登记方式；
- 工作目录；
- ownership 类型；
- 是否允许自动停止；
- 停止策略；
- 登记者和授权记录。

ownership 类型：

- `owned`：由控制器创建，或由用户明确委托控制器管理，可自动停止；
- `borrowed`：任务使用但未创建，只允许断开连接；
- `shared`：多个任务共享，必须引用计数归零且获得项目策略许可后停止；
- `external`：控制器只观察，禁止停止。

PID 必须结合启动时间、可执行文件或容器标识校验，避免 PID 复用导致误停。

## 6. 自动熔断

### 6.1 失败熔断

v0.1 对同一观察源、报告任务或同步任务连续失败进行熔断；v0.2 以后也对同一命令模板进行熔断。

达到阈值时：

- 暂停自动重试；
- 项目标记 `DEGRADED`；
- 生成失败摘要；
- 需要人工或策略明确恢复。

```text
CLOSED -> OPEN -> HALF_OPEN -> CLOSED
```

`HALF_OPEN` 只允许一个探测任务。

### 6.2 资源熔断

触发条件可以包括：

- 控制器数据盘剩余空间低于安全阈值；
- 事件写入持续失败；
- SQLite 无法 checkpoint；
- 系统负载超过项目允许值；
- 单任务日志增长异常；
- 控制器子进程数量超出限制；
- SCM 同步队列持续扩大。

资源熔断优先阻止新内部任务和同步任务，尽量不影响控制器未拥有的业务任务。

### 6.3 安全熔断

以下事件直接禁止 SCM 写入：

- 日志检测到疑似 token、私钥或认证 header；
- 目标仓库与项目声明不一致；
- 分支出现未知远端漂移；
- 当前工作区包含未归属变更；
- provider 认证主体或权限发生变化；
- TLS 校验失败。

## 7. 超时策略

每个内部任务或 v0.2 命令声明：

```yaml
execution:
  startup_timeout: 60s
  idle_timeout: 30m
  max_runtime: 12h
  interrupt_grace_period: 15s
  terminate_grace_period: 30s
```

- `startup_timeout`：任务启动或健康检查超时；
- `idle_timeout`：没有日志、心跳或进度事件的最长时间；
- `max_runtime`：绝对最长运行时间；
- 长导入任务可关闭 idle timeout，但必须提供其他心跳来源；
- 对外部非 `owned` 任务达到超时时，只标记 `BLOCKED` 或停止观察，不直接 kill。

## 8. 恢复模型

### 8.1 启动恢复

控制器启动时进入 `RECOVERING`：

1. 校验数据库和配置版本；
2. 读取上次 controller state；
3. 加载活动 task、attempt、lease 和 ownership；
4. 检查已登记 PID、容器和外部任务；
5. 重新建立 watcher 游标；
6. 识别 orphan；
7. 生成恢复事件；
8. 完成后进入 `ACTIVE`、`DRAINED` 或 `EMERGENCY_STOP`。

### 8.2 Orphan 分类

- `process_alive_and_owned`：进程仍活着，重新附着观察，不自动重启；
- `process_alive_not_owned`：只读重新关联，不取得停止权限；
- `process_missing_with_exit_evidence`：按证据完成状态结算；
- `process_missing_without_evidence`：标记 `UNKNOWN_TERMINATION`；
- `container_alive`：按 ownership 恢复日志游标和健康观察；
- `external_job_alive`：只读重新关联；
- `duplicate_lease`：阻止新执行并要求一致性修复。

### 8.3 自动恢复限制

只有满足以下条件才允许自动继续控制器内部任务；v0.2 以后项目命令还必须被声明为可恢复或幂等：

- task attempt 有稳定 checkpoint；
- 外部副作用可验证；
- 没有未知工作区变化；
- ownership 未发生变化；
- 项目和控制器未处于 stop 状态。

数据库迁移、支付、删除、生产部署和未知脚本不得自动重跑。

## 9. 解除暂停和紧急停止

### 9.1 Resume Project

```text
pac project resume <project-id>
```

恢复前检查：

- 项目配置有效；
- 熔断原因已消失或被明确接受；
- 数据盘和事件库健康；
- 没有冲突 lease；
- ownership 信息有效；
- SCM 写策略没有安全阻断。

### 9.2 Clear Emergency Stop

```text
pac controller emergency-stop clear --reason "..."
```

必须：

- 本机管理员身份；
- 明确原因；
- 生成不可覆盖审计事件；
- 先进入 `RECOVERING`；
- 不自动重跑停止前任务；
- 由用户逐个恢复项目或内部任务。

## 10. 停止期间的报告与 SCM

- Task Stop：允许生成本地最终取消报告；
- Project Pause：允许生成项目状态摘要；
- Controller Drain：允许完成已排队且通过安全检查的 reports-only 同步；
- Emergency Stop：默认禁止所有 SCM 写入；
- 安全熔断：只保留本地报告；
- 网络异常：报告进入离线队列；
- 恢复后必须先验证目标对象状态，再执行幂等补同步。

## 11. 防止误停

- 停止请求必须指定 task ID 或稳定 project ID；
- CLI 展示即将影响的 PID、容器、ownership 和任务数量；
- 非 `owned` 资源默认拒绝终止；
- 紧急停止需要二次确认或受保护本机凭据；
- API 使用 request ID 防止重复提交；
- 远程 SCM 评论不作为 v0.1 的停止控制入口；
- 未来如支持远程停止，必须使用允许名单、签名命令、短期 nonce 和审计。

## 12. 测试矩阵

### 正常停止

- 可响应 SIGINT 的 owned 进程；
- 只响应 SIGTERM 的 owned 进程；
- 忽略信号的 owned 进程；
- 多层子进程；
- Docker owned/shared/external 容器；
- borrowed/external 资源拒绝终止。

### 重启恢复

- 控制器正常退出后重启；
- 控制器被强制 kill；
- SQLite WAL 未 checkpoint；
- 日志发生轮转；
- PID 被系统复用；
- 容器重启后 ID 变化；
- lease 超时和重复实例启动。

### 熔断

- 连续 watcher 失败；
- 连续报告或同步失败；
- 磁盘不足；
- secret 检测命中；
- SCM 断网；
- 工作区未知变更；
- provider 权限降低。

## 13. 验收标准

1. 单任务停止不会误伤同项目其他任务。
2. 项目 `drain` 不接收新内部任务，但允许当前内部任务完成。
3. Emergency Stop 能阻止新的执行和 SCM 写入。
4. 强制终止前至少尝试一次证据 flush。
5. 控制器重启后不会自动启动项目业务命令。
6. orphan 任务被明确标记，不伪装为成功或失败。
7. 所有 stop、resume、clear 操作均带 actor、reason 和时间戳。
8. 重复停止请求具有幂等性。
9. borrowed/shared/external 资源不会被普通 task stop 错误关闭。
10. PID 复用不会导致误停新进程。
11. 所有停止路径均有自动化测试和故障注入测试。
