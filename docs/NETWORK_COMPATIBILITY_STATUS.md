# 网络兼容修复进度：显式本机HTTP代理 + 公网多地址容错

对应Issue #23、PR #24。当前仍是草稿，不是完整代理/TUN/VPN/虚拟机兼容发布。main没有被此PR修改。之前只有静态代理诊断；现在增加了一个范围明确、真实连接的本机HTTP CONNECT路径，不能再把当前PR称为“只有诊断”。

## 本次可用范围

1. 默认系统路由：保留一次DNS查询的全部已验证公网地址，IPv4/IPv6交错，最多4个候选，共享连接超时。DNS首个地址不可达时不再立即判整个目标不可达。
2. 显式本机HTTP代理：启动程序前设置`VIBE_RADAR_HTTP_PROXY`，只接受`http://127.0.0.1:实际端口`、`http://localhost:实际端口`或`http://[::1]:实际端口`，不含账号密码。原HTTP、搜索、公开案例和Chromium的Python网络桥共用这一连接类。
3. 代理以HTTP CONNECT连接已经通过原公网检查的**数字IP**，之后仍按原网站域名做SNI和TLS证书验证。不是让代理另外解析域名、不是把Fake-IP当公网。
4. 代理失效或拒绝CONNECT，程序报`local_proxy_connection_failed`并停止，绝不自动切换到直连。网站403/429、证书失败或HTTP发送/读取失败不重放。

这是对此前大范围未提交方案的明确收窄：不包含远程DNS、加密DNS、任意LAN代理入口、SOCKS或代理身份认证，不削弱公网目标检查。不能以这一较小的已实现路径宣布原主体问题全部解决。

## 你怎样使用

首先确认：目标域名的DNS检查是`passed: true`。还返回198.18.*时，这个模式仍不能取数；本次没有解决Fake-IP。

打开你已有的代理软件，在其设置中找到**实际HTTP代理或mixed端口**。不是订阅地址、控制接口端口、SOCKS专用端口，也不要把端口想当然写成7890。下面仅以你的实际端口恰好为7890举例。

停止旧工作台，在修复源码目录的Git Bash中执行一行：

```bash
VIBE_RADAR_HTTP_PROXY=http://127.0.0.1:7890 /d/code_environment/anaconda_all_css/py312/python.exe scripts/start_workbench.py
```

这个前缀仅为本次启动进程设置代理，不修改Windows、代理软件、VPN或其他程序。代理保持开启。正常进入工作台后沿用原操作，HTTP与浏览器桥都会使用同一明确选定的代理。

先验证已有公开案例也可以：

```bash
VIBE_RADAR_HTTP_PROXY=http://127.0.0.1:7890 /d/code_environment/anaconda_all_css/py312/python.exe scripts/run_real_example.py
```

正常输入y确认。案例是公开接口，不是BOSS或大陆站点验收。如果网站、代理或当前网络不可达，仍明确报失败；不以模拟数据代替。

PowerShell等价形式（仅当前PowerShell进程及其子进程）：

```powershell
$env:VIBE_RADAR_HTTP_PROXY = "http://127.0.0.1:7890"
& "D:\code_environment\anaconda_all_css\py312\python.exe" scripts/start_workbench.py
```

回到默认系统路由：停止服务，Git Bash执行`unset VIBE_RADAR_HTTP_PROXY`，PowerShell执行`Remove-Item Env:VIBE_RADAR_HTTP_PROXY -ErrorAction SilentlyContinue`，再正常启动。系统路由仍可能经过你的TUN/VPN；不等于程序要求关闭它们。

不需要重装Python、修改工作区数据或增加抓取预算。使用的代理由你已有的软件提供，本项目不会自动创建代理、监听端口或扫描局域网。

## 如何看设置

使用同样的环境前缀运行`scripts/check_network_environment.py`，新增`explicit_loopback_proxy`会显示是否启用、配置是否有效、本机主机/端口及`target_dns: local_public_only`。该检查不联网，`connectivity_tested`仍为false。

原`collector_applies_static_proxy=false`专指HTTP(S)_PROXY/ALL_PROXY/系统设置不会被自动采用；本程序专用VIBE_RADAR_HTTP_PROXY与它是不同配置。不能把“检测到系统代理”当“自动使用系统代理”。本次专用显式设置也不读取NO_PROXY；它是你为本程序整次运行选择的路径。

## 仍然未解决

| 环境/需求 | 当前结果 |
|---|---|
| 正常系统路由，公网DNS，首IPv6坏但其他IP可达 | 已提供有限预请求容错 |
| 公网DNS，本机无认证HTTP代理可用 | 本次显式CONNECT路径可用；需实际可用端口 |
| TUN/VPN，DNS为真实公网 | 可沿系统路由或明确选定的本机代理连接；未认证具体软件产品 |
| Fake-IP/混合公网私网/DNS失败 | 仍拒绝或报告DNS失败，不能自动处理 |
| SOCKS、代理账号密码、PAC、自动系统代理 | 尚未实现；不会降级假装成功 |
| 虚拟机需连接宿主机LAN地址上的代理 | 此路径尚不支持，127.0.0.1只指虚拟机本身 |
| 网页内选择/保存代理 | 尚未实现，当前为进程级显式设置 |
| 代理失效、网站拒绝、无访问权限 | 如实停止，不能保证无条件取数 |

## 测试证据与边界

本机真实TCP HTTP CONNECT代理与真实TLS服务组成受控测试：代理看到的是数字公网地址符号，只有测试代理将它映射到测试服务器；生产没有该映射。测试覆盖HTTP与Chromium桥、TLS/SNI、POST仅一次、403不重放、代理407不直连、错误证书拒绝、IPv6 CONNECT格式以及私网/Fake-IP不发请求。

这些不是用户本机代理软件的实站测试，也不是境外/大陆招聘网站认证。远端CI之前没有runner和执行步骤，不把本地通过改写为CI通过。每次结果见PR最终提交与验收记录。

新连接层不发送招聘消息、不自动填写账号、不改变robots、站点配额或验证规则。Issue #23继续保留完整主体目标，PR未具备全部验收条件前保持草稿。
