# OEPL 扫码绑定 PoC（模拟模式）

这是一个与现有 S3+C6 AP **隔离**的本地业务侧实验。支持通过手机摄像头、USB 二维码扫码枪或手工输入，完成**价签 MAC → 商品条码/SKU → 价格**的登记与绑定。

**默认且当前唯一模式：SIMULATED。** 不访问、不上传、不重启真实 OpenEPaperLink AP，不发送无线命令，不改固件、LittleFS 或 GPIO。本项目仅在本机 SQLite 中创建模拟任务。不能把模拟任务当成真实墨水屏刷新。

## 功能边界

- Angular 18 手机响应式界面，`@zxing/browser` 摄像头识别二维码、EAN/UPC 等条形码；扫码枪键盘输入可直接粘贴到输入框并回车。
- Express 5 HTTP API + Node 24 内置 `node:sqlite`（SQLite WAL 本地数据库）。
- 登记价签（`verified=0` 表示**尚未确认在线**）；商品 SKU/条形码/价格录入；允许多个价签绑定同一个 SKU。
- 绑定事务与幂等：重复绑定同一商品不重复创建任务，换绑到其他 SKU 必须额外确认。
- 修改已绑定商品的名称/价格，记录 `SIMULATED` 任务；不存在真实 AP 图片传输状态。
- `GET /api/preview/<MAC>.svg` 输出对应商品的 **296×128 SVG 预览**（不是固件用的 raw/JPEG，未无线下发）。
- 不支持账号权限、商品批量导入、POS 库存同步、离线冲突合并、实机 RF 验收；以上是生产化前的独立任务。

## 在 Ubuntu / Mac 运行

要求 Node.js **24+**；AP 可以保持正常运行，本实验**不需要**与 AP 建立连接。

终端 A：

```sh
cd experiments/oepl-s3-c6/scan-binding-poc/api
npm install
npm test
npm start
```

API 默认监听 **127.0.0.1:3788**，SQLite 默认保存在 `api/data/scan-binding.sqlite`。

终端 B：

```sh
cd experiments/oepl-s3-c6/scan-binding-poc/web
npm install
npm start
```

浏览器访问 **http://127.0.0.1:4200**。Angular 开发代理将 `/api` 转发到本机 3788。

首次录入时，先输入价签上的 16 位 OEPL MAC（如 `OEPL:1234567890ABCDEF`），再输入或扫描商品条码；如没有找到商品，则先录入 SKU、名称与价格，保存后确认绑定。软件不会假定扫码得到的价签一定在 AP 在线。

> 扫码二维码的载荷必须是实际 OEPL 16 位 MAC 或 `OEPL:<MAC>`，不是标签厂商贴纸上的任意序列号。真实 MAC 必须与 AP 标签数据库匹配。示例 MAC 不得作为真实设备身份使用。

## 手机摄像头与访问安全

手机摄像头扫码需要**可信 HTTPS 安全上下文**（通常 `http://127.0.0.1` 仅对同一设备被视为安全，电脑本机的 localhost 不等于手机的 localhost）。不要在手机上直接访问局域网 HTTP 并期望摄像头工作。

本 PoC 的 API **只允许监听 loopback**，没有登录鉴权，拒绝 `HOST=0.0.0.0` 等非本机绑定。实际手机访问需要在同一主机上部署**受信任的 HTTPS 反向代理 + 登录鉴权/访问控制**，通过 `/api` 转发至 `127.0.0.1:3788`，并托管编译后的前端，完成后才可用手机实测扫码。

目前不得直接把 3788/4200 转发至公网或无鉴权局域网，也不得把 AP 配置文件和真实标签数据库同步到 GitHub。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | `mode=SIMULATED` |
| POST | `/api/tags` | 手工/扫码登记已知 OEPL MAC，标记未验证 |
| GET | `/api/tags` | 本地价签登记表 |
| POST | `/api/products` | 创建或更新商品，价格为整数分 |
| GET | `/api/products/by-barcode/:barcode` | 查找商品 |
| POST | `/api/bindings` | MAC 与 barcode 绑定；换绑需 `replace:true` |
| GET | `/api/bindings` | 绑定明细 |
| GET | `/api/jobs` | 模拟业务任务 |
| GET | `/api/preview/:mac.svg` | 296×128 SVG 画面预览 |

`POST /api/bindings` 示例：

```json
{"mac":"1234567890ABCDEF","barcode":"6901234567892","replace":false}
```

只允许在用户确认、画面参数核实并**再次单独授权**后开发实机 AP 写入适配器。真实模式至少要求类型定义存在、图片宽高符合硬件、请求幂等、发送与完成状态分离、失败重试限流及操作审计。

## 安全与验收

```sh
cd api && npm test
cd ../web && npm run build
```

必须先通过 HTTP 正/负例、绑定冲突、重放幂等、价格更新及 UI 编译。**即使通过，也只能标注 `MOCK_PASS`，不能标注 `REAL_RF_PASS`。**

真实 S3+C6 的 M4.2 报告仍在隔离主线上，本 PoC 不修改已有报告，也不改变 PR #8 的状态。
