# OEPL S3+C6 M3 实机稳定性与首枚价签验收

更新时间：2026-10-17
范围：只读观察、源代码审查和验收准备；没有重启、刷写、GPIO 操作、串口打开、主动无线自检或网络配置修改。

## 输出结论

- `UART_LINK_STATUS=ONLINE_OBSERVED_NOT_UART_PROVEN`：用户报告 S3 Dashboard 已显示 Online，且 AP 报告 ESP32-C6 / fw 0021；本轮没有打开 UART，故不宣称 UART 已完成验收。
- `RESET_WIRING_SAFETY=UNVERIFIED`：本地项目标注为 N16-M，但核对到的官方 DevKitM-1 资料不等同于该精确变体；GPIO47 由 S3 代码以推挽方式 LOW 约 50 ms 后 HIGH，可能与 C6 板载 EN 上拉、RC 和 USB-UART 自动下载电路形成冲突。未执行 reset。
- `30MIN_STABILITY=PASS_WITH_LIMITS`：Ubuntu 本机完成 1800 秒请求窗口，60 个 30 秒样本全部 TCP 可达且 `/sysinfo` 成功；这是 HTTP/API 稳定性证据，不等同于实时 radio 或 UART 验收。
- `NEW_PANIC_COUNT=0_OBSERVED`：60 个新鲜 `/sysinfo` 样本均为 `crashed=false`，reset reason 均为 `Software`；这不覆盖采样窗口之外的历史 Panic。
- `RADIO_DISCONNECT_COUNT=UNVERIFIED`：HTTP `/sysinfo` 不暴露实时 WebSocket `apstate`，API 超时单独统计，不冒充 radio disconnect。
- `C6_FIRMWARE_VERSION=0021 (0x0021)`：来自既有 Dashboard 报告及 C6 源码版本常量；本次新鲜 `/sysinfo` 只报告 AP 类型/版本字段，不证明 C6 Flash 镜像身份。
- `FIRST_TAG_TEST_READY=PREPARED_NO_TAG`：当前 0 tags，没有真实 RF PASS。

## A. EN/RESET 安全审查

S3 工程的 `resetRadio()` 将 reset 引脚设为 OUTPUT，拉低约 50 ms，再拉高；当前环境将 `FLASHER_AP_RESET` 配为 GPIO47。C6 固件的 UART profile 为 TX GPIO3、RX GPIO2，C6 固件版本常量为 `0x0021`。

官方 ESP32-C6-DevKitM-1 资料显示 EN/CHIP_PU 节点与上拉/RC、复位按键以及 CP2102N 的 DTR/RTS 自动下载电路相连；同时官方说明 USB 供电选项不能与外部 5V/3V3 供电混用。由于项目注明的是 N16-M，不能将 M-1 schematic 当成精确板级证明。因此直接 GPIO47→EN 是否会与该板的自动复位晶体管冲突、以及两块板分别 USB 供电时是否经 EN/保护路径反向供电，均保持 `UNVERIFIED`。

在精确 N16-M 原理图和电源路径确认前，不进行新的 reset 或单板断电状态下的带电试验。建议后续硬件审查优先确认隔离/开漏或串联限流方案；本报告不把 GPIO47 补接归因为通信恢复的唯一原因。

参考： [Espressif ESP32-C6-DevKitM-1 用户指南](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c6/esp32-c6-devkitm-1/user_guide.html)、[官方 schematic PDF](https://dl.espressif.com/dl/schematics/esp32-c6-devkitm-1-schematics.pdf)。

## B. 稳定性采集器

`m3_stability.py` 每个样本分别记录 TCP 状态和 `/sysinfo` API 状态，保存脱敏 JSONL 和 summary JSON。它只执行指定私网地址的 HTTP GET `/sysinfo`，不读取页面、Cookie、响应头或凭据，不调用 `/radio_selftest`，不打开 TTY。

Mac 可达时运行：

```bash
python3 experiments/oepl-s3-c6/m3_stability.py \
  --ap-ip 192.168.2.192 --duration 1800 --interval 30 \
  --output m3-stability-20261017.jsonl
```

`/sysinfo` 可观察固件/类型/版本/重启原因/崩溃字段，但不提供实时 `apstate` 和 uptime；因此脚本不会伪造 `radio_disconnect_count` 或 uptime。API 失败与 radio disconnect 明确分开。

本轮实际在 Ubuntu 执行：

```text
m3-stability-ubuntu-20261018.jsonl
samples=60, TCP_REACHABLE=60, HTTP_INFO_OBSERVED=60,
api_failure_count=0, radio_none_count=0, panic_observation_count=0,
ap_typename=ESP32-C6, ap_version=33, hasC6=1, channel=11
```

原始 JSONL 与 summary 保留在 PAC 工作区，未加入 Git；其中没有响应正文、Cookie、凭据或 USB 数据。

## C. Issue #7 根因记录

- 原因：面包板插针未完全插入，造成 UART 接触不良。
- 修复：重新插紧后，Dashboard 识别 ESP32-C6 fw 0021，状态 Online。
- 该证据支持“接触恢复”结论，但不证明 30 分钟稳定性、受控复位或真实价签 RF 已通过。
- `uart_printf()` 越界问题继续作为独立软件缺陷跟踪。

## D. 首枚价签最小验收矩阵

| 项目 | 状态 | 通过证据 |
|---|---|---|
| 标签首次入网 | NOT TESTED | 当前 0 tags |
| 获取标签 MAC、类型、信号 | NOT TESTED | 无真实标签样本 |
| 小图片下发 | NOT TESTED | 未执行 RF 操作 |
| 显示内容更新 | NOT TESTED | 未执行 |
| 第二次内容更新 | NOT TESTED | 未执行 |
| 通信中断与重试记录 | NOT TESTED | 未执行 |

下一步只能在真实价签到位且用户明确授权设备状态改变后进行；不能用模拟数据写成 RF PASS。

## E. 本轮 CI 与稳定性证据

- `ruff check .`：PASS，输出 `All checks passed!`。
- `mypy src`：PASS，48 个源文件无问题。
- PAC 全量 pytest：PASS，`176 passed, 1 warning, 16 subtests passed`；在沙箱外普通用户环境执行，避免 Codex 沙箱对 FastAPI TestClient 线程的限制。
- OEPL Offline Safety Gate：PASS，36 项 fixture 测试通过，并完成 probe 编译和 shell 语法检查。
- v0.2 Controlled Runner Release Gate：PASS（远端 SHA `6a30d0f`；Ubuntu/Docker 与 macOS/LaunchAgent 两个 job 均通过）。本机没有 Docker daemon，但 GitHub Actions 已完成 Docker fixture 验证。
- 本轮 Ubuntu 产生了新鲜 AP 30 分钟 JSONL/summary：60/60 TCP 可达、60/60 `HTTP_INFO_OBSERVED`、0 API 失败、0 `radio_none`、0 个样本内 Panic 观测。由于 `/sysinfo` 不暴露实时 `apstate` 和 uptime，`RADIO_DISCONNECT_COUNT=UNVERIFIED` 仍然保留；Dashboard Online/fw 0021 也不被改写为精确 Flash 身份证明。

本轮软件修复只涉及 Ruff 兼容性、UART 测试隔离/真实 C 边界 harness、README 路径和报告；没有刷写、串口打开、复位、GPIO、无线自检或网络配置变更。
