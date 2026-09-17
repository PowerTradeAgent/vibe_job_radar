# 本机取数前置故障：浏览器崩溃与 Fake-IP 诊断分开解决

## 此次现场证据与不能推断的内容

用户在同一个 Python 3.12 环境中已安装 Playwright 1.57.0，配套 chromium-1200/chrome-win64/chrome.exe 存在。启动日志有进程 PID，随后以 3221226356 = 0xC0000374 退出。微软将此码定义为 STATUS_HEAP_CORRUPTION；这不是“没有安装”、缺 API Key 或猎聘拒绝。

退出状态不确定具体损坏模块。不能据此断言同名 chromium Python 包、VPN、显卡、杀毒软件或物理内存是根因。taskkill 找不到进程是退出后的清理现象。没有读取用户的转储文件或 Windows 事件，不能声称已定位原生缺陷。

原“安装 / 修复”只运行 pip install 版本范围与 playwright install chromium；已有安装可能直接满足条件、复用缓存，不等于重新下载或升级。本轮保留普通安装，并增加两条明确的修复动作，不改系统安全策略。

## 最少操作

停止现有采集会话后，在向导点“重新下载并修复浏览器”：仅用当前解释器执行 `-m playwright install --force chromium`，保留 SDK 版本和工作区；然后用相同后端实际检查空白页。退出码0不算浏览器就绪。

若仍以相同原生异常失败，可明确选择“更新 Playwright 与配套浏览器”。它会执行限定版本范围内的 pip --upgrade 和配套浏览器 --force 下载，影响共用该 Python 的项目，必须确认。更新完成或部分执行后，旧进程可能已加载旧SDK：退出工作台，用原解释器重新启动，再检查。程序不会谎报在同进程里修复成功，也不自动重启正在运行的其他项目。升级前核对实际操作系统是否满足当前 Playwright 的支持条件。

不卸载无关的同名包、不删除所有浏览器缓存、不下载第三方DLL、不关闭杀毒/沙箱、不更改注册表、不以管理员方式绕过拒绝。一个同版本重下载和一次经确认的更新仍未解决时，停止重复安装；用 Windows 事件查看器的应用程序错误核对本次 chrome.exe 的异常码、故障模块名称及版本即可。完整转储可能含敏感数据，不默认收集或公开上传。

## DNS按钮现在检查什么

仅用户点击后检查适配器固定的目标域名，不接受任意URL。结果分别给出：

- system_dns：操作系统原始回答，保留198.18.*的事实；这不意味着VPN故障。
- effective_resolution：当前工作区策略实际解析结果。未启用时不擅自发第三方查询，而是提示到现有“网络自动适配”阅读说明、同意并保存。
- 用户已同意且为纯198.18/15映射时，复用原workspace PublicResolver。它仍检查原策略、再次系统解析、许可撤销、TTL、缓存和限频。正常公网无需第三方查询；私网/混合/解析失败不放行。

通过只代表DNS解析通过。没有连接目标网站、验证目标TLS、登录或抓取职位，也不能让崩溃的浏览器变成ready。现有会话保持原策略，不悄悄变更；检查给出会话范围提醒。无需先关闭正常VPN/TUN。

## 验收与剩余工作

新增测试使用去除用户路径的人工原生日志、人工DNS和安装命令；不冒充在用户电脑复现或修好0xC0000374。既有可见浏览器专项继续覆盖Windows Playwright1.57和Linux；新增浏览器界面测试覆盖重下载、更新后重启门槛、无同意不发DoH、系统与应用解析并列呈现。真实用户的启动与采集仍需更新后核验。

这一轮修的是已提交现场日志对应的主线阻断，不继续扩外围功能。没有修改高级collection.py的历史未交付接线、没有其他来源或站点访问替代。

参考：
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/596a1078-e883-4972-9bbc-49e60bebca55
- https://playwright.dev/python/docs/browsers
- https://playwright.dev/python/docs/intro
- https://learn.microsoft.com/en-us/troubleshoot/windows-server/performance/troubleshoot-application-service-crashing-behavior
