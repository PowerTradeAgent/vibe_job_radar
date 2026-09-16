# 混合模式第一批：发布方限频与可恢复等待

Refs saksim/vibe_job_radar#3、#9、#27。混合服务另由 #30 跟踪。

## 实际运行路径

`robots → RateLedger.set_publisher → 同一 SQLite 事务检查站点配额和来源限频 → PinnedTransport → Chromium → GuidedService → guided.js`。

允许路径的 Crawl-delay 不再因为超过默认 15 秒就被拒绝。发布方间隔和 Request-rate 精确滑动窗口分别保存，规则变更保守取更严格约束，不自动降低旧限制。所有请求（包括获取 robots）记录来源；来源限频按 HTTPS origin 隔离，站点小时/每日配额仍跨来源共享。失败请求不退款。

短等待最多使用当前调用的 30 秒预算，可暂停/停止。长等待返回 next_allowed_at，不创建网络连接、不消费请求配额，任务进入 waiting_rate；保留选择、已成功条目和部分报告。同一进程的存活会话可在到期后自动继续安全读取；登录动作和分页点击不会自动重放，时钟回拨不会自动恢复。重启后截止时间和选择仍在，但登录会话没有持久化，需一次继续或登录，不声称完整持久调度已交付。

429 首次收到时不重试，保存至少 300 秒或更长 Retry-After；到期后允许正常恢复。401/403 保持访问拒绝，不能因计时结束自动换身份。公网目标、TLS、robots 拒绝、只读请求和配额保护保留。

## 数据库与回滚

原 visits/cooldown 表和记录保留，新增 publisher_policy、publisher_windows、publisher_visits、clock_seen。配额与来源请求的预留在同一个 BEGIN IMMEDIATE 事务内完成。损坏、丢失或不可写的数据库导致停止，不创建空配额替代。支持的发布方窗口最多 366 天，超范围规则明确拒绝而非截断。

新规则不自动过期/放宽；需要未来显式规则更新机制。回滚应用前应停止任务并保留数据库备份；旧版不认识新增规则，不能继续采集并宣称仍遵守新规则。不要通过删除数据库恢复速度。跨设备/不同工作区共享配额、后台系统服务与持久登录仍待实现。

## 本轮证据与限制

基线恢复后 Git tree 精确等于 main a4f8e009 的 0849a37d39fcf1b780644add9ce627fff673381f，基线 399 项离线测试通过。本次 22 项新测试加完整回归为 421 项，0 失败/错误/跳过；node --check guided.js 通过。测试使用真实 SQLite/并发与受控传输、任务夹具，不是用户 VPN 或招聘站点测试。

实际运行 scripts/run_guided_browser.py 时，Chromium 访问本机工作台被环境策略 ERR_BLOCKED_BY_ADMINISTRATOR 阻止，尚未完成浏览器用户流程，没有移除策略或声称通过。远端 CI 与真实站点认证另验，本文件不代替这些证据。不关闭 #27、不合并 main、不创建正式发行版。
