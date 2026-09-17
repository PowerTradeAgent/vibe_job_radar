# 首站页面加载：先修真实 HTTP 请求，再验证真实岗位

主线 #3 / #9，P0 子项 #45，实现沿用 PR #44。
开发基线为 main@79bdc6e450b06b9b86c5a3e238991d174e41f593（已合并 #43）。

## 已确认事实与尚未定位部分

用户的 Edge 启动、Windows 原生证书链、明确同意的 Fake-IP 解析已有成功反馈；
这不等于所有网站、资源或登录都通过。猎聘无样式 HTML、51job robots_unavailable、
BOSS 加载壳及 network_error 是三个不同故障，不能全部归因为同一个代理问题。

先前固定 robots 单次观察（PR44初始53dcaa3、run35229838077）：
猎聘200/text/plain/178字节；we.51job.com200/text/html/7919字节且无User-agent行；
BOSS200/text/plain/1040字节。环境差异和时间差异仍存在，不是用户抓包。
主域有效 robots 不保证嵌入页面来源的规则可用，HTML 不能当成有效空规则。

## 本轮实际修复：重复请求头

Playwright all_headers()返回小写字段，原HTTP桥使用大小写敏感字典补入User-Agent，
原始网络请求中会出现两条同名字段。本轮真实本机TLS服务先复现两条，再验证一条。

独立request_headers.browser_headers在DNS、额度与拨号前验证、归一化字段名称：
相同重复值去重，矛盾重复值或非法/过大输入拒绝且不发请求。保留不透明Cookie值，
不拆解/重建用户登录资料。HTTP层自己产生Host及正文长度，不透传代理认证、
Connection及其指定的逐跳字段，Accept-Encoding仍固定identity。

User-Agent保留实际浏览器兼容信息并带原有VibeJobRadar/0.1标识；不轮换身份、
不假扮其他爬虫、不在拒绝后改身份重试。robots仍按同一项目标识判断。
这是协议构造修复，不是已证明某个平台失败的唯一根因。

## 页面与输入验证

新增真实TLS测试覆盖实际线上的字段个数、Cookie/POST不变、代理认证隔离、
字段冲突/控制字符/大小上限、不拨号不扣额。旧完整回归保留。
现有Linux Chromium与Windows Edge集成脚本增加真实CSS计算样式断言及
实际HTTP头检查：生产GuidedService→Playwright回调→原PinnedTransport→
受控TLS→robots/样式/脚本/动态列表→所选正文→原研究报告。
原Fake-IP共享策略/撤销/未同意工作区测试全部保留。人工页面不代替猎聘实站。

## 受限现场观察（不会改变采集决定）

observe_site_robots.py默认零请求，--live只检查三家固定robots。
仅当200、text/plain、UTF-8、存在User-agent、非HTML且原简单规则明确允许时，
可读取该站一次固定搜索HTML；复杂通配/末尾匹配规则跳过等待审核。
更严格延迟或Request-rate遵守；超过观察等待预算则不请求列表。
拒绝/重定向/HTML/异常不当作允许，无重试或跳转。

搜索HTML仅提取最多40项声明的资源类型和域名，不导出路径、查询参数或正文；
不请求资源、不执行JS、不登录、不打开岗位详情，也不自动将域名加入允许范围。
远端观察只用于下一次逐站审核，不宣称规则、访问许可或页面稳定性已认证。

## 没有包含的修改

此前更广的robots访问语义、响应解压和阶段诊断改动未获工具写入，本PR没有重交，
也不把这些本地实验视为已实现。仍按原代码处理robots不明、TLS错误和压缩响应；
不借主域规则放行其他来源、不降低域名/公网地址/限频或身份保护。

## 用户审查合并后

保留工作区、Edge、truststore与网络偏好；停止旧服务、更新合并后的main再启动，
不用安装新组件。只验证一个猎聘任务（1页、最多5条），记录第一个真实失败阶段；
没有正文不算研究成功，已有原文则核对来源及完整性再进入本人证据。
若还是robots_unavailable/加载壳，不重复重装；按具体来源与失败响应继续#45。
51job和BOSS仍分别追踪，不承诺本PR已解决三站所有问题。

参考（协议/API）：
- https://playwright.dev/python/docs/api/class-request#request-all-headers
- https://www.rfc-editor.org/rfc/rfc9110#section-5.1
- https://www.rfc-editor.org/rfc/rfc9309.html
