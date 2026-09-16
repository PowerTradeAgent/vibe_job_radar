# 合并 main 后的第一轮：SOCKS5 与统一策略整合

关联 #3 / #23 / #25 / PR #26；延续已合并 PR #31，不复制 PR #29 的发布工具。

## 基线及原分支保全

本轮父提交为用户实际合并的 `bc243f67e03d47b08ffe33ed84c63e6cc561ed41`。Fork/main 使用非强制快进同步，再从该SHA创建 `feat/network-policy-socks-integration`。上游main不直接提交，本轮不继续修改已合并的 `feat/hybrid-core-phase1`。

源码基线使用已附完整源码恢复文件权限后重建Git树，得到 `52d1dbef733b21c1bd105e882f67cd6606ec8db2`，与GitHub返回的main树完全一致；没有用文件名猜测源码版本。本地直接clone受执行环境DNS影响未成功，不将该失败说成仓库权限故障。

PR #26与#29的原分支/原SHA继续保留。本次将#26的协议能力适配到当前main，不直接把旧network.py覆盖回去。新整合通过并合并后，才按代码覆盖和验收结果处理旧#26的替代关系；现在不关闭它，不丢弃历史证据。

| PR #26 内容 | 本次处理 |
|---|---|
| loopback_socks.py握手 | 保留数字公网目标、单一时限、分片、拒绝/截断清理；新增不读环境的from_url解析。 |
| select_loopback_proxy | 保留旧显式API；作为NetworkPolicy的专用覆盖分支，不取代自动策略。 |
| network.py接线 | 使用现有NetworkPolicy选择；正确回显SOCKS协议，不拆出第二条TLS路径。 |
| network_environment.py | 自动与专用配置都识别SOCKS；仍区分发现、选择与真实连接。 |
| 高级表单/浏览器错误 | 补齐SOCKS、冲突与拒绝解释，不将失败改为直连或登录错误。 |
| tests/test_loopback_socks.py | 原28测试完整保留，blob `6ee7e126ac50eca8bb0535d0ef181043e0e920ff`。 |
| 旧网络状态/操作说明 | 更新成新main+自动选择，不将旧显式端口命令作为普通用户默认操作。 |

## 自动选择与不可混淆的含义

系统/环境设置由Python `urllib.request.getproxies()`发现；其优先级和平台能力受标准库实际返回内容限制。明确 `socks5://` 可用；`socks://`、`socks4://`、`socks5h://` 或没有版本的专用socks项不能靠猜测升级成SOCKS5。无配置时不扫描端口，也不声称VPN关闭。

NO_PROXY只对自动模式生效；协议加入策略指纹，避免同一个host:port的HTTP与SOCKS策略被误认为同一快照。新会话使用新策略，已有会话不因环境改变而换协议或出口。

原来两个“SOCKS不支持”断言只将对象更新为仍不支持的socks5h；没有删除拒绝断言。原28项SOCKS测试没有删减。新增测试包含真实本机SOCKS+TLS的GET/POST、浏览器桥、拒绝不直连、原域名SNI，以及配置优先级、凭据不回显、指纹与NO_PROXY边界。

## Fake-IP下一阶段的具体接口与验收方向（未交付）

本次确认：仅加入SOCKS5仍不能消除 `validate_public_url` 在实际连接前拒绝映射地址的问题。下一阶段需要独立的“允许目标→带来源/TTL的解析快照→实际连接”接口，不允许把198.18地址段全局当公网。

优先评估受控加密解析后连接已验证真实公网IP的路径，以及原生浏览器执行器的独立信任模型。解析的提供方、bootstrap、超时/缓存、NO_PROXY与所选出口、凭据隔离、混合公网/私网返回、CNAME/IPv6以及用户知情必须一起测试。DoH的JSON格式没有正式RFC且供应商有变更记录，稳定接口宜评估标准wireformat；不能把添加公共解析器等同于系统网络已兼容。

当前本分支未改变解析来源、未加入第三方DNS查询。上述是下一阶段工程方向，不是已完成承诺。服务化/自建Linux仍后置，不拿生产地址缺失作本地开发阻断。

## 依赖与回滚

不改数据库结构、不改本地报告或任务、无新增运行依赖。回退代码不删工作区与限频数据；回到未识别SOCKS的旧版可能改变自动路线支持范围，应停止运行中的任务后按旧版能力确认网络，不能声称回退后仍提供相同SOCKS能力。

PR #29需在最新main单独整合版本/源码验证/打包与组合回归；网络功能不依赖提前部署服务器。#23的Fake-IP仍为主要未闭合目标，不能因SOCKS回归通过而关闭整个需求。

## 官方协议资料

- https://www.rfc-editor.org/rfc/rfc1928
- https://docs.python.org/3.10/library/urllib.request.html#urllib.request.getproxies
- https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/
- https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/
- https://developers.cloudflare.com/changelog/product/1.1.1.1/
