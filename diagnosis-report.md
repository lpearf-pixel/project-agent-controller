# OpenEPaperLink S3/C6 实机诊断报告

更新时间：2026-10-17
范围：只读网络、USB 元数据和源代码检查；没有打开 UART、修改 GPIO、刷写、重启、主动无线自检或修改防火墙。

## 结论摘要

当前故障分类为：

- Ubuntu 到 `192.168.2.192:80` 的历史 AP 地址没有得到 HTTP 响应；必须用 `time_connect` 和 `time_starttransfer` 字段区分 TCP 建连失败与 TCP 已建立但 HTTP 首字节超时。
- Codex 沙箱无法看到 `/dev`，但宿主侧只读探针可以看到 USB 设备。因此 USB 设备可见性是 Codex 文件系统沙箱限制，不是 Ubuntu 设备枚举失败。
- Ubuntu 的 `10.211.55.4/24` 通过 `10.211.55.1` 的默认路由访问 `192.168.2.192`，没有直连 `192.168.2.0/24` 路由。这与 Parallels NAT/共享网络路径一致，但不能单独证明 AP 不在线或地址已改变。
- 真实 Mac 对比已完成：Mac 可访问 AP，而 Shared/NAT 下的 Ubuntu 不可访问；因此 Shared/NAT 路径是当前网络故障边界。不要由 Codex 自动切换网络模式；若要恢复 Ubuntu 直达 AP，下一步应由用户在 Parallels UI 评估桥接网络。

## 网络故障分类

`host-readonly-probe.sh` 和 `mac-readonly-network-probe.sh` 都使用固定目标、禁用代理的只读 `curl` GET，并记录：

- `tcp_connect_failed`：连接阶段返回失败；
- `tcp_connect_timeout`：连接计时接近 4 秒，未进入 HTTP 首字节阶段；
- `http_first_byte_timeout`：TCP 已建立，但 8 秒总超时前没有 HTTP 首字节；
- `http_no_response`：对端建立连接后返回空响应；
- `http_response`：收到 HTTP 状态码，包括非 2xx；
- `http_transport_error`：其他 curl 传输错误。

脚本不保存页面、响应体、响应头、Cookie 或凭据。代理只输出是否设置，不输出代理值。Ubuntu 侧额外记录接口、路由、邻居表和有限的 UFW 状态摘要；Mac 侧记录 `route -n get`、接口、目标 ARP 和系统代理开关。

## 当前 Ubuntu 证据

已知只读状态：


- 地址：`enp0s5=10.211.55.4/24`；
- 默认网关：`10.211.55.1`；
- 到 AP 的路由：经 `10.211.55.1 dev enp0s5`；
- 未观察到 `192.168.2.0/24` 直连路由；
- 历史 `curl` 请求返回 `HTTP=000` 并超时；
- 最新 Ubuntu 分阶段探针：`classification=http_first_byte_timeout`、`curl_exit=28`、`time_connect=0.000282`、`time_starttransfer=0.000000`、`time_total=8.003076`。这表示 TCP 已快速进入连接路径，但在总超时内没有收到 HTTP 首字节；它不是 TCP 建连超时的证据；
- Parallels 虚拟接口 `enp0s5` 为 UP；
- Ubuntu 环境代理变量均存在，但探针使用 `--noproxy '*'`，且输出不包含代理值；UFW 状态不可用，未修改防火墙；
- 只读探针可枚举 `/dev/ttyACM1` 和 `/dev/ttyACM2`，Codex 沙箱内两者均不存在。

真实 Mac 证据已排除 AP HTTP 服务不可用和历史 IP 完全错误这两个主要分支：Mac 在 `en0` 上对同一地址收到 HTTP 200。Ubuntu 失败因此不再是 Codex 网络沙箱本身，也不是 AP 已离线；当前最强根因是 Parallels Shared/NAT 没有把 Ubuntu 的 `10.211.55.0/24` 访达路径转发到 Mac 所在的 `192.168.2.0/24` LAN。

## USB 身份核验

不能依赖 `ttyACM1`/`ttyACM2`，因为编号会随插拔顺序变化。Ubuntu 探针现在同时读取 `/dev/serial/by-id` 和 udev 元数据：

- 稳定身份由 `ID_SERIAL_SHORT` 与 by-id 名称派生 SHA-256；原始序列号不写入输出；
- 输出 VID/PID、厂商、型号、`cdc_acm` 驱动和当前 devnode 作为交叉证据；
- 不打开设备节点，因此不改变 DTR/RTS，也不能证明 UART 通信。

最新 Ubuntu 只读采集实际得到三个 by-id 身份：两个 Espressif `303a:1001 USB_JTAG_serial_debug_unit`，当前 devnode 分别为 `ttyACM1`、`ttyACM0`；一个 QinHeng `1a86:55d3 USB_Single_Serial`，当前 devnode 为 `ttyACM2`。因此此前“ACM2 为 Espressif”与本次“ACM2 为 QinHeng”最可能是 tty 编号重排或多设备并存，而不是同一稳定身份改变。必须比较 `stable_id_sha256` 和 VID/PID/型号，不能仅比较 ACM 编号。稳定哈希不同表示设备/USB 接口身份不同；哈希相同而模型文本变化时，应复核 udev 数据源。

## Mac 对比步骤

先前导入的 `mac-network-probe.json` 不能作为 Mac 证据：其中出现 Linux 的 `enp0s5`、Linux `ifconfig` 输出和 `10.211.55.4`，而 `route -n get` 与 ARP 字段为空，说明脚本实际在 Ubuntu 上执行。随后从 `/media/psf/chatgpt/oepl-s3-c6-stage/mac-network-probe.json` 读取到真实 Mac 结果：目标路由接口为 `en0`，Mac 侧 HTTP `200`，`time_connect=0.052597`、`time_starttransfer=0.105093`、`time_total=0.116871`。更新后的 Mac 脚本会先检查 `uname -s`；非 Darwin 环境只输出 `platform_mismatch`，不再发起网络请求。

在 Mac 宿主机执行：

```bash
bash mac-readonly-network-probe.sh > mac-network-probe.json
```

应使用更新后的脚本在真实 Mac 上执行，然后将 `mac-network-probe.json` 复制到 PAC 目录。该脚本只做目标路由、接口、ARP、代理状态和禁用代理的 HTTP GET，不修改网络。当前 PAC 中的旧 JSON 未纳入提交。

比较规则及本次结果：

| Mac 结果 | Ubuntu 结果 | 判断 |
|---|---|---|
| TCP/HTTP 成功 | `http_first_byte_timeout` | **本次结果**：优先检查 Parallels Shared/NAT 到 `192.168.2.0/24` 的转发路径；不自动切换模式 |
| Mac 与 Ubuntu 都失败 | 失败 | 优先怀疑 AP 历史 IP、AP 服务或物理网络路径，不改 Parallels 模式 |
| Mac 失败，Ubuntu 成功 | 成功 | AP 可达性存在时序或接口差异，先保存两端证据，不改硬件 |
| 两端都成功 | 成功 | 网络故障解除；仍不能推断 UART 成功 |

## 尚未解决的 UART 问题

UART 仍为 `NOT_TESTED`。当前证据只能证明源配置审计、USB 枚举、稳定身份和网络可达性类别；UART 连通性 **not proven**，不能证明：

- S3/C6 物理 TX/RX/GND 连接正确；
- 正确固件已经刷入两块板；
- S3 能从 C6 获得新鲜 ACK/心跳；
- ACM 设备的打开不会触发板级复位或 DTR/RTS 副作用。

下一步最小操作是：由用户在 Parallels UI 评估临时桥接到 Mac 当前网络接口，或添加第二个桥接适配器保留 Shared/NAT；切换后只重新读取 Ubuntu 路由并运行同一只读 HTTP 探针。不得由 Codex 自动切换桥接，不得打开串口，不得执行 `/radio_selftest`、刷写或重启。

## M3 进度

M3 的详细报告见 [`experiments/oepl-s3-c6/m3-acceptance-report.md`](experiments/oepl-s3-c6/m3-acceptance-report.md)。接触恢复已记录为 Issue #7 的根因，但 30 分钟稳定性、历史 Panic 是否复现、受控 reset 和真实首枚价签 RF 验收仍未通过；不得将 Dashboard Online 误写成这些项目的 PASS。

## Ubuntu C6 HTTP 永久修复

已确认原先的“TCP 成功但 HTTP 超时”来自两层本机策略：sing-box `tun-in` 的 `auto_redirect` 把 C6 请求送入 `mac-http`，而运行态 `codex_guard` 的 C6 allow 曾被追加在物理接口 DROP 之后。修复后的持久化源为：

- `/etc/sing-box/config.json` 的 `tun-in.route_exclude_address=["192.168.2.192/32"]`；
- `/etc/nftables-codex.nft` 中 C6:80 allow 位于 `enp0s5` DROP 之前；
- `codex-guard.service` 启动时重新加载上述 nftables 源，sing-box 启动时读取上述 JSON。

运行态临时规则用于立即恢复访问；持久化配置源已修正，后续服务重载/启动不会再把 C6 allow 放到 DROP 后面。`apply-c6-direct-fix.sh` 仅针对该 /32、保留代理隔离、保留时间戳备份且不删除文件。
