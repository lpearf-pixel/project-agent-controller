# OEPL M4 — 首枚电子价签与 RF 验收准备

状态：软件准备完成；已完成首次入网和一次受控图片请求，完整 RF 验收尚未完成。

## 结论字段

```text
M4_SOFTWARE_READY=READY
RADIO_STATUS_OBSERVABILITY=PASSIVE_WS_AVAILABLE
TAG_COMPATIBILITY=PARTIAL_SOURCE_SUPPORT_EXACT_TAG_UNKNOWN
FIRST_TAG_AVAILABLE=YES
FIRST_TAG_JOIN=OBSERVED
FIRST_TAG_RF_STATUS=BLOCKED_MISSING_TAGTYPE_01
CI_OEPL=PASS (run 39120160234, commit 995863e)
CI_PAC_RELEASE=PASS (run 39120160207, commit 995863e)
COMMIT_SHA=995863e1a6ce0aac1a841e3fb428de74fae3e73d (report-only follow-up commit may differ)
NEXT_HARDWARE_ACTION=USER_VISUALLY_CONFIRM_DISPLAY_BEFORE_ANY_SECOND_UPDATE
```

## 1. 无线协议兼容性矩阵

| 目标/族 | 源码证据 | 无线路径 | 结论 | 额外条件 |
|---|---|---|---|---|
| ESP32-C6 AP | `ARM_Tag_FW/OpenEPaperLink_esp32_C6_AP/main/radio.c`: `esp_ieee802154_*` | 片上 IEEE 802.15.4，源码默认启动 `curChannel=25`；允许的 AP 信道列表为 11/15/20/25/26/27 | SOURCE_CONFIRMED | 运行中的 AP 状态曾报告 channel 11；这不是 Flash 镜像证明 |
| OEPL 2.4 GHz 协议 | `proto.h`: `PROTO_PAN_ID=0x4447`；`radio_init()` 设置 PAN、非 promiscuous、非 coordinator | IEEE 802.15.4 PAN `0x4447` | SOURCE_CONFIRMED | 标签必须使用兼容的 OEPL 固件/协议与信道 |
| 可选 CC1101 Sub-GHz | `SubGigRadio.h`, `radio.c`, `CONFIG_OEPL_SUBGIG_SUPPORT` | 外置 CC1101；866 MHz 通道 100–105，915 MHz 通道 200–205；`PROTO_PAN_ID_SUBGHZ=0x1337` | CONDITIONAL | 需要编译启用、CC1101 硬件和对应 Sub-GHz 标签族；不是 C6 片上 2.4 GHz 的替代品 |
| ZBS243 标签 | `Tag_Flasher/README.md`、flasher `-z/--zbs243` | 需 OEPL 兼容标签固件 | SUPPORTED_FAMILY_WITH_FIRMWARE_GATE | 具体型号、固件和标签类型字段仍需现场确认 |
| Nordic nRF52811 标签 | `Tag_Flasher/README.md`、flasher `-n/--nrf82511` | 需 OEPL 兼容 nRF 标签固件 | SUPPORTED_FAMILY_WITH_FIRMWARE_GATE | 需要具体标签身份及对应 type definition |
| 88MZ100 / CC1110 标签 | `Tag_Flasher/README.md` | 专用标签族；CC1110 为 Sub-GHz 方向 | CONDITIONAL | 需要对应硬件/固件和专用刷写路径；不能由尺寸推断兼容 |
| Hanshow Nebular type `0x60/0x61/0x62` | `oepl-definitions.h`；资源中存在 `resources/tagtypes/61.json` | 源码有类型定义 | TYPE_DEFINITION_ONLY | 不等于任意二手 Hanshow/Hanshuo 2.66 英寸标签可直接入网 |
| Gicisky/PICKSMART BLE `0xB0–0xBF` | `oepl-definitions.h` | BLE 类型定义 | NOT_PROVEN_FOR_C6_802154 | 需要具体 AP/radio 支持与固件证据，不能按 802.15.4 直接判定 |
| “汉朔二手 2.66 英寸”未给型号/固件 | 用户描述，无可核对的具体料号、协议或固件 | UNKNOWN | UNKNOWN | 先提供背标/型号、主控/无线芯片、当前固件及 type 字段；不得先配对或刷写 |

源码只证明存在协议路径、标签类型定义或刷写工具支持，不证明未识别的二手价签可用。首次 RF 验收前不需要专用刷写器；若标签原厂固件不兼容，才进入经授权的标签固件改造/专用刷写评估，当前不执行。

## 2. 被动 Dashboard 状态观察

源码审查确认 WebSocket 路径为 `/ws`（`ESP32_AP-Flasher/src/web.cpp` 与 `wwwroot/main.js`）。服务端周期性广播只读状态对象 `sys`，字段包括：`apstate`、`runstate`、`uptime`、Wi-Fi 状态/RSSI、记录数和资源统计；AP 信道被维护为变量 `ap_ch`。还存在 `tags`、`logMsg`、`errMsg` 等广播。

可设计的被动采集只建立 WebSocket、接收广播、保存脱敏后的 `sys` 状态变化，并计算：

- AP Online/Offline：由 `sys.apstate`/`runstate` 的时间序列判断；
- 无线版本/信道：版本来自现有 AP 信息接口，信道来自 `ap_ch`；
- heartbeat/超时：按 `sys` 广播间隔和连续缺失计算；
- `radio_disconnect_count`：当前接口未提供可信的独立计数，标记 `UNVERIFIED`；
- 重启：`uptime` 回退或 `errMsg=REBOOTING` 可观察，但不能把缺失广播自动算作 Panic。

本轮建立了 15 秒只读 WebSocket 订阅，仅发送标准握手、不发送应用控制帧；观察到 `apstate=1`、`runstate=2`、`recordcount=1` 和递增 uptime，未观察到传输错误事件。原始日志未读取，MAC、别名和凭据未进入采集物。真实 tag 传输重试和断连仍为 `UNVERIFIED`。

## 3. 首枚价签可重复验收流程

### 阶段门槛

- `MOCK_PASS`：仅离线验证报告/事件解析和字段脱敏；不接触 AP 或价签。
- `READY`：已确认价签精确型号、无线芯片、现有固件和 OEPL type definition；用户明确授权一次受控 RF 操作。
- `REAL_RF_PASS`：新鲜现场证据完整，包括入网、型号/MAC（脱敏保存）、信号、两次显示更新、耗时/重试和中断恢复。

### 实施步骤（需用户逐项授权后才执行）

1. 拍摄并记录价签背标/型号，确认协议族、无线芯片和当前固件；不先刷写。
2. 在 AP 只读状态界面确认测试前时间、AP 在线、信道和已有 tag 数；不得发起自检。
3. 将单枚已确认兼容的价签置于正常 RF 范围，记录首次入网事件、脱敏 MAC、type、信号质量和协议字段。
4. 使用已准备的小尺寸单色 fixture，执行一次受控内容更新，记录传输耗时、重试、失败原因和显示结果。
5. 内容改变后执行第二次更新，确认不是旧画面缓存或偶然成功。
6. 仅在授权的中断场景下记录一次通信中断与自动重试；不人为断电、不复位、不改变接线。
7. 只有所有证据一致时标记 `REAL_RF_PASS`；否则标记 `REAL_RF_FAIL` 或 `UNVERIFIED`，并保留原因。

当前已有真实入网记录；一次图片请求已被 AP 接受，但 AP 缺少 `01.json` 标签类型定义，未形成传输队列，因此 `FIRST_TAG_RF_STATUS=BLOCKED_MISSING_TAGTYPE_01`，不是 PASS。

## 4. 新鲜只读入网证据

通过 AP `GET /current/tagDB.json` 获取并在本地脱敏解析，未输出 MAC、别名、文件名或图像数据：

```text
record_count=1
hwType=0x01
LQI=50
RSSI=-39
channel=11
version=41
updatecount=0
pending=0
```

源码定义中 `hwType=0x01` 对应 `SOLUM_29_SSD1619` 类型名；这仍是 AP 记录的类型字段，不替代标签背标/固件身份确认。在获得明确授权后执行了一次 `/imgupload` 小图片 POST：响应为 `HTTP=200`、正文为 `Ok, saved`。标签随后自然检查-in，`lastseen` 更新，读取到 `LQI=61`、`RSSI=-37`；但 `pending=0`、`updatecount=0`、`updatelast=0`，没有可验证的显示完成计数或屏幕视觉证据。因此当前结论是“图片请求已被 AP 接受，显示更新未验证”，不是 `REAL_RF_PASS`。

进一步只读检查 AP `GET /tagtypes/01.json` 返回 `HTTP=404`。源码 `contentmanager.cpp::drawNew()` 在 `getHwType(taginfo->hwType)` 得不到定义（`bpp == 0`）时只记录“无定义”、将 `nextupdate` 推迟约 300 秒并返回，不创建图片传输队列。这解释了 `pending=0`、`updatecount=0` 及约 5 分钟重试时间；当前最可能的直接阻断是 AP LittleFS 缺少 `01.json` 标签类型资源。此结论来自源码和 HTTP 404 证据，尚未修改 AP 文件系统。

## 5. 当前限制与下一步

- C6 精确 Flash 镜像身份仍为 `UNKNOWN`；AP 报告的 `ESP32-C6`/版本字段不能替代镜像哈希。
- GPIO47→EN 仍未通过硬件安全审查；M4 不使用它，也不执行复位。
- 不再执行第二次图片上传；不执行刷写、配对、串口打开、无线自检或设备重启。
- 最小下一步：在备份并核对版本后，由用户明确授权一次 AP LittleFS 标签类型资源补齐/恢复操作；之后再重新执行一次受控图片更新。当前保持 `BLOCKED_MISSING_TAGTYPE_01`，不宣称 `REAL_RF_PASS`。

## M4.2 后续状态更正（只读证据）

上述结论记录的是修复前状态。M4.2 已在用户单次授权范围内补齐 `/tagtypes/01.json`，并验证固定提交源文件的字节、MD5 与 SHA-256 一致。随后标签自然传输完成：`pending=0`、`updatecount=1`、`updatelast` 非零；这证明缺失类型定义已解除且协议层传输完成，但不等于屏幕视觉 PASS。

屏幕仍未变化的当前最强证据是 AP 保存的 `.raw` 文件头为 `480×320`、2bpp，而 `hwType=0x01` 的定义要求 `296×128`、2bpp。OEPL `makeimage.cpp::jpg2buffer()` 直接使用上传 JPEG 的原始尺寸，当前这次测试图因此没有生成标签尺寸的有效 raw。后续必须先准备严格 `296×128` 的测试图或修复编码路径，再由用户明确授权一次新的单图更新；在此之前保持 `DISPLAY_UPDATE=UNVERIFIED`，不重复上传。
