# 本机 SOCKS5：第二阶段增量（父需求 #23，子任务 #25）

## 先区分main与本PR

PR #24 已合并，main@a4f8e009含本机匿名HTTP CONNECT与多公网地址容错。本次新PR在该main上增加本机匿名SOCKS5，合并前只能在本PR源码里使用。旧文档中的“PR24仍草稿”是历史记录，不能用来判断当前仓库状态。

本阶段没有系统代理自动采用、SOCKS认证、HTTP认证、PAC、Fake-IP远程DNS、宿主机LAN入口或网页网络配置。它不是完整网络兼容完成。只解决已有本机匿名SOCKS5入口却无法被项目使用这一项。

## 什么条件可以使用

目标DNS已经通过公网检查，你的代理软件有真实运行的本机SOCKS5端口，而且不要求用户名密码。127.0.0.1和::1指运行Python的这台机器；在虚拟机内它们不指宿主机。

从代理软件的设置页查看SOCKS5或支持SOCKS5的mixed端口。端口不是订阅链接、不是控制接口。下面1080仅作格式示例，要换成实际端口，不扫描或猜测。

停止旧工作台，在本PR源码目录的Git Bash运行：

```bash
VIBE_RADAR_HTTP_PROXY= VIBE_RADAR_SOCKS_PROXY=socks5://127.0.0.1:1080 /d/code_environment/anaconda_all_css/py312/python.exe scripts/start_workbench.py
```

前面的HTTP变量清空仅对这次进程生效，避免继承旧HTTP配置产生歧义。两个入口同时有值会明确拒绝，不偷偷选一种。

原有真实公开案例使用同样前缀，把最后的脚本换成`scripts/run_real_example.py`；程序仍要求你确认后才发请求。这个命令是操作方法，不保证当前网络或来源一定可达。

PowerShell：

```powershell
$env:VIBE_RADAR_HTTP_PROXY = ""
$env:VIBE_RADAR_SOCKS_PROXY = "socks5://127.0.0.1:1080"
& "D:\code_environment\anaconda_all_css\py312\python.exe" scripts/start_workbench.py
```

PowerShell变量会留在当前终端；恢复系统路由可清空这两个变量或新开终端。不会改动Windows代理、DNS、VPN、防火墙或工作区数据库。

## 程序实际做什么

读取显式本机入口→继续原有域名/HTTPS/完整公网DNS检查→发送SOCKS5匿名协商→用已验证的IPv4/IPv6数字地址和443端口建立CONNECT→按原网站名完成SNI和TLS→发送原HTTP请求。

不向代理发送域名解析请求，不提供socks5h/BIND/UDP功能；代理返回的bound地址只按协议消费，不拿它继续连接。分片回复按长度读取，整个握手使用同一时限。代理拒绝、要求认证或格式异常时关闭socket，不直连、不换身份、不重放HTTP。源站401/403/429与robots限制保留。

原HTTP、搜索/公开案例、Chromium Python桥都由同一PinnedHTTPSConnection选择该连接，不需要给每个入口另装插件。

## 错误如何看

| 提示 | 处理 |
|---|---|
| local_proxy_configuration_conflict | HTTP与SOCKS变量同时有值，只选一种 |
| local_socks_configuration_invalid | 检查socks5协议、本机地址与实际端口，不能携带凭据或路径 |
| local_socks_auth_unsupported | 代理要求认证；该项仍未实现，不要把招聘密码填进去 |
| local_socks_protocol_error | 可能填错协议端口，或代理响应不兼容 |
| local_socks_truncated_reply / local_socks_timeout | 本机代理握手中断或超时，不是网站登录错误 |
| local_socks_request_rejected | 代理拒绝此目标，不绕过规则或直连重试 |
| non_public_address | 目标DNS仍返回非公网/Fake-IP；本阶段未解决，不能加入白名单 |
| tls_verification_failed | 原目标证书不通过，仍须停止，不能关闭验证 |

## 可复用设计与验证

LoopbackSocks5继承既有不可变本机端点验证，提供相同open_tunnel接口；select_loopback_proxy只读取显式配置，不执行PAC、不改变进程环境。原连接层统一处理目标TLS和请求，目标校验不重复定义另一套规则。

新增28项测试，含真实本机SOCKS5+TLS服务的数据交换、浏览器桥、IPv6、分片、失败不回退、POST只发一次、超时与错误证书。人工代理将测试公网符号映射到本机TLS站；生产无此映射或信任开关。全量本地427项通过，不代表远端CI或实际VPN产品认证。

协议依据：RFC1928：https://www.rfc-editor.org/rfc/rfc1928.html 。此处实现的是匿名CONNECT子集，不应标成认证/PAC/Fake-IP全支持。
