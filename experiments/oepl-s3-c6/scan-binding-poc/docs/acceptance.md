# 扫码绑定 PoC 验收门槛

## 阶段一：离线 Mock
- [ ] API 测试完整运行 PASS：MAC 格式、商品条码、数据库事务、重复绑定幂等、冲突换绑确认、价格更新生成模拟任务。
- [ ] Angular 18 编译 PASS。
- [ ] API /health 返回 `SIMULATED` 和 `ap_writes_enabled=false`。
- [ ] 本地手工/扫码枪注册 16 位 OEPL MAC；错误的条码格式被拒绝。
- [ ] 商品首次扫描走新建流程，已存在条码走回显流程。
- [ ] 用户显式同意换绑前不能覆盖原绑定。
- [ ] 每个模拟任务均显示 `SIMULATED`，SVG 预览 296×128，无真实 AP HTTP POST。
- [ ] 数据库重启后数据持久，原始 MAC/价格不上传外部服务。

## 阶段二：手机测试（需要独立 HTTPS 部署）
- [ ] 受信任 HTTPS + 访问鉴权 + 局域网可达。
- [ ] 摄像头权限、安卓/iOS 条码识别、拒绝权限时手输回退。
- [ ] 同一扫码动作不会产生多条相同业务任务。
- [ ] 手机页面显示结果不等同于 RF 更新完成。

## 阶段三：真实 AP 更新（尚未授权、**禁止执行**）
- [ ] 再次确认用户授权具体 MAC、图片内容和最大尝试次数。
- [ ] AP 只读 GET 识别精确 `hwType`、几何尺寸、LittleFS 类型定义和在线状态。
- [ ] 生成尺寸正确的 JPEG/raw，逐阶段记录 accepted/queued/transferred/display_seen。
- [ ] 失败不自动无限重试，不重启或刷写 AP/标签。
- [ ] 用户实机目视确认屏幕，才可标注 DISPLAY_UPDATE_PASS。

本阶段任何 CI 通过都不得标记 REAL_RF_PASS。
