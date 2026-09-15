# 代理 / TUN / VPN / 虚拟机：状态与尚未完成的兼容性

关联Issue #23。本次PR是**只读诊断改进，不是代理兼容实现**。核心代理传输代码的仓库提交被连接工具安全检查拦截，未进入本PR；不能通过本PR宣称“开代理就一定能采集”。

## 当前能准确看见什么

使用同一解释器只读检查：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe scripts/check_network_environment.py
```

结果只显示Python标准库发现的系统/环境静态代理协议、主机、端口和是否配置认证，不回显用户名/密码、订阅URL或查询参数。

`collector_applies_static_proxy: false`明确表示当前采集器没有使用这些静态代理配置。发现HTTPS_PROXY不等于请求真的走了该代理。系统TUN/VPN仍可能在操作系统层接管已发出的socket，不能从此诊断判定其开启或关闭。

“网络检查”的passed含义不变：只判断这次系统DNS回答是否通过公网检查。它不认证代理可连接、不认证登录，不说明已取得职位正文。198.18.*被当前项目拒绝的兼容问题仍未修复。

虚拟机的127.0.0.1是虚拟机本身，通常不是宿主机。诊断不猜测网关或扫描局域网，不自动开放代理监听，也不修改防火墙。

该命令不进行DNS/TCP/HTTP测试，不修改代理、TUN或VPN。输出含本机Python路径，分享前可遮住用户名。不要提交招聘密码、代理密码或Cookie。

## 尚需实现并验证的路线（Issue #23保持开放）

1. 统一可复用的目标地址校验、解析、代理连接策略，HTTP、浏览器桥和独立案例共用。
2. 系统静态代理读取和显式本机/宿主机HTTP CONNECT、SOCKS5入口；明确代理失效时不偷偷直连。
3. 经用户确认的加密DNS解析，让Fake-IP映射不再被误当最终网站地址，同时保持公网目标与TLS验证。
4. 网络模式的界面设置、路径分阶段检查、可观测失败和有限多地址连接尝试。
5. 保持原网站限频、登录/robots、跳转和内网隔离；不能为了支持代理取消安全模型。
6. 实际代理/TLS请求、TUN条件、VPN/VM网络路径及真实公共数据源的验收。人工注入的网络条件不能冒充测试过所有VPN/虚拟机产品。

上述主体传输提交未成功，本PR中没有可启用的新proxy/DNS模式、网络设置写入口或未连接按钮。工具拒绝不等于技术上做不到，但也不能将本地未提交实验称作用户可用的修复。

## 当前临时做法与边界

仍需在现有安全约束下，让目标域名返回真实公网地址，再使用系统可达路由；配置TUN的域名例外或真实DNS模式可用于排查，但不是本PR已提供的自动解决方案。不能保证断网、代理失效、网站拒绝、验证码或无访问许可时仍取得数据。

参考：[Python静态代理发现](https://docs.python.org/3/library/urllib.request.html#urllib.request.getproxies)、[Mihomo DNS处理模式](https://wiki.metacubex.one/config/dns/)、[Playwright代理支持](https://playwright.dev/python/docs/network)。
