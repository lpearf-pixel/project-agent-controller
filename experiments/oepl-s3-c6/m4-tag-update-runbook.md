# OEPL M4 首枚价签更新 Runbook

本手册记录 S3+C6 AP 与首枚 `hwType=0x01` 价签的可重复更新流程。
后续 agent 必须先阅读本文件，再执行任何 AP 写入。它适用于本实验分支，
不是对任意 OEPL 标签的通用兼容性承诺。

## 当前已验证基线

- AP：`http://192.168.2.192`
- AP 身份：S3+C6，`ap_type=198`，C6 在线，信道 11
- 标签：`hwType=0x01`，信号约 RSSI `-31`、LQI `65`
- 固定标签定义来源：`85f92f3839da2c5a7adb4be95c31f9a3fc1cd2ed:resources/tagtypes/01.json`
- 标签定义：`296×128`、2bpp、`rotatebuffer=1`、G5 compression `29`
- 当前实测：`pending=0`、`updatecount=2`，用户已确认屏幕发生变化
- 当前结论：类型资源已恢复，尺寸有效的单图更新已成功；完整 RF 中断/重试验收仍未完成

当前分支的 CI 注意事项：`9713646` 修复了 fixture 生成器的 Ruff import 检查，
但 GitHub 尚未为该 SHA 创建新的 Actions run；不要把旧 SHA 的 PASS 复制给当前 HEAD。

## 两个关键问题点

### 1. `tagtypes/01.json` 缺失会阻止任务生成

当 `/tagtypes/01.json` 返回 404 或 `check_file` 报 `filesize=0` 时，AP
无法取得 `hwType=0x01` 的 `bpp`，`drawNew()` 会推迟约 300 秒并直接返回。
此时 `/imgupload` 返回 HTTP 200 也只表示上传请求被接受，不表示图片已排队。

修复方式是用户明确授权后，仅补齐这一个 LittleFS 文件；必须先备份并校验，
一次 POST 后只读验证，不能重试或扩大写入范围。固定源文件必须来自上面指定的 OEPL 提交。

### 2. 上传 JPEG 的尺寸必须匹配标签定义

AP 当前 `makeimage.cpp::jpg2buffer()` 直接以输入 JPEG 的宽高创建 sprite，
不会自动把任意图片缩放到标签尺寸。此前发送的 `480×320` 图片造成了：

```text
协议层：updatecount 增加，传输完成
显示层：屏幕没有变化
原因：raw 头尺寸不是 hwType=0x01 所需的 296×128
```

对 `rotatebuffer=1` 的标签，raw 头可能显示为 `128×296`；只要尺寸对是
`(296,128)` 或 `(128,296)` 即可，不能把旋转方向误判为错误。

## 标准操作顺序

### A. 只读门禁

在 Ubuntu 可访问 AP 的执行环境运行，所有请求都绕过代理：

```bash
AP_IP=192.168.2.192
curl --noproxy '*' --connect-timeout 4 --max-time 8 -sS \
  "http://${AP_IP}/sysinfo" -o /tmp/oepl-sysinfo.json
curl --noproxy '*' --connect-timeout 4 --max-time 8 -sS \
  "http://${AP_IP}/current/tagDB.json" -o /tmp/oepl-tagdb.json
```

只输出脱敏字段：`ap_type`、`ap_typename`、`hasC6`、`channel`、`crashed`、
标签 `hwType`、`pending`、`updatecount`、`lastseen`、`LQI`、`RSSI`。
不要把 MAC、alias、Wi-Fi 配置、原始数据库或 Cookie 发到聊天或 GitHub。

确认类型文件：

```bash
curl --noproxy '*' --connect-timeout 4 --max-time 8 -sS -o /tmp/tagtype-01.json \
  "http://${AP_IP}/tagtypes/01.json"
curl --noproxy '*' --connect-timeout 4 --max-time 8 -sS \
  "http://${AP_IP}/check_file?path=/tagtypes/01.json"
```

若文件存在，必须校验 JSON、字节数和 MD5；内容正确时输出
`ALREADY_PRESENT`，禁止重新上传。若存在但哈希冲突，停止并输出
`EXISTING_FILE_CONFLICT`。

### B. 生成并验证图片

当前仓库包含确定性 fixture 生成器：

```bash
python3 experiments/oepl-s3-c6/generate_296x128_fixture.py
python3 - <<'PY'
from PIL import Image
with Image.open('/tmp/oepl-m4-296x128.jpg') as image:
    assert image.size == (296, 128)
    print(image.format, image.size, image.mode)
PY
```

如果标签不是 `hwType=0x01`，不要复用这个 fixture；应先读取精确的
`tagtypes/<hwType>.json` 定义并生成对应尺寸。图片验证失败时禁止 POST。

### C. 一次性单图更新

只有用户明确授权本次单图更新，且写入前 `pending=0` 时，才允许执行一次：

```bash
MAC="$(jq -r '.[0][0].mac' /tmp/oepl-tagdb.json)"
curl --noproxy '*' --connect-timeout 4 --max-time 20 --max-redirs 0 \
  -sS -o /tmp/oepl-upload.response \
  -w 'UPLOAD_HTTP=%{http_code} TIME=%{time_total}\n' \
  -F "mac=${MAC}" \
  -F 'file=@/tmp/oepl-m4-296x128.jpg;filename=oepl-m4-296x128.jpg;type=image/jpeg' \
  "http://${AP_IP}/imgupload"
```

该请求只能执行一次。超时、断开或非 200 后不得直接重试，先只读检查
`tagDB`、`check_file` 和 WebSocket/状态事件。不要调用 `/reboot`、
`/radio_selftest`、`/update_c6`，不要打开 `/dev/ttyACM*`，不要驱动 GPIO。

### D. 自然 Check-in 与验收

每 10–30 秒只读轮询，最长按本轮授权的观察窗口等待自然 Check-in：

```text
上传前：pending=0, updatecount=N
自然 Check-in：pending 可变为 1
完成后：pending=0, updatecount=N+1, updatelast 更新
```

随后只读下载当前 raw 文件，检查 `check_file` 和前 6 字节：

```text
header_size=6
bpp=2
(width,height) ∈ {(296,128),(128,296)}
```

只有以下条件全部成立才记录 `TRANSFER_COMPLETION_OBSERVED`：

1. AP 仍在线且 `crashed=false`；
2. `pending` 清零；
3. `updatecount` 确实增加；
4. raw 文件 HTTP 200 且尺寸/格式有效；
5. 用户实际看到屏幕内容变化，才能标记 `DISPLAY_UPDATE_PASS`。

协议完成或 HTTP 200 本身都不能代替第 5 项。

## 故障分类

| 现象 | 结论 | 下一步 |
|---|---|---|
| `/tagtypes/01.json` 404、`filesize=0` | 类型定义缺失 | 授权后补齐单文件，不重传图片 |
| `/imgupload` 200，但 `pending` 一直 0 | 可能尚未 Check-in 或内容任务未生成 | 只读等待、检查 `nextcheckin`；不重传 |
| `pending=1→0`、`updatecount` 增加，但 raw 尺寸错误 | 图片几何错误 | 修正 fixture/编码后重新申请一次授权 |
| `pending=1→0`、`updatecount` 增加、raw 尺寸正确，但屏幕不变 | 标签端固件/显示驱动或面板问题 | 停止 AP 写入，另行授权硬件诊断 |
| HTTP 超时或 AP `crashed=true` | AP/网络故障 | 分离网络、AP 重启与 UART 结论；不刷机 |

## 安全边界

- 不自动二次上传，不根据 HTTP 200 猜测显示成功。
- 不刷写、擦除、重启 S3/C6/AP，不改变 GPIO47→EN 接线。
- 不打开 UART/USB CDC，不改变 DTR/RTS，不执行无线自检。
- 不上传原始 tagDB、MAC、Wi-Fi 配置、Cookie 或串口日志。
- 当前成功只证明这枚已入网标签的一次受控更新；不证明其他型号兼容。

## 仍需单独授权的硬件项目

正式 UART 字节握手、受控通信中断/重试、读取 C6 精确 Flash 身份以及
GPIO47→EN 电气安全验证都没有在本轮自动执行。它们会打开设备接口或改变
设备状态，必须由用户分别授权并具备匹配的硬件/ELF/原理图证据；在此之前保持
`UNVERIFIED` 或 `UNKNOWN`，不要伪造完成。
