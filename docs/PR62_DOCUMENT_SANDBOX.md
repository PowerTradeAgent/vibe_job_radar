# 原生跨域采集：先限制辅助窗口，再执行网页

关联 #49 / #50 / #46，仍在 PR62 内；不需要用户提前陪测或提供登录资料。

## 真实失败而非推测

读取到中断前新增提交 `bfec5fe45a4db47c2d8ea79bf5fd632ff8280c4a` 的 Windows headed 原生工件：匿名 API 搜索、完整 JD 到报告、自动选择采集和明确零结果均通过。但随后的弹窗反例记录了 `/apply` 的首个 HTTP GET，且没有应用标识，最终检查失败。这里的 `/apply` 是人工服务器的无副作用测试路径，绝非真实投递。

不能以先前某轮绿色宣称拒绝弹窗总能及时生效。公开 page/target 事件与临时 Fetch 隔离存在时序竞争，必须在网页执行之前限制其创建辅助窗口。

## 修正及明确的架构取舍

只对启用了已声明 CORS 业务契约的原生上下文，在既有响应检查通过后，为 Document 响应追加一条独立的 enforcing CSP：

`sandbox allow-scripts allow-same-origin allow-forms`

由浏览器在文档执行前实施，不授权 popups/popups-to-escape-sandbox、下载或其他额外能力。脚本、原站点 origin、Cookie 和同窗口的正常表单仍可运行，所有实际请求仍须经过原角色/域名/方法/robots/配额/取消检查。没有伪造接口、自动填写密码或放开跨源 SSO。

这是对既有“完全不改响应头”保证的一处明确收窄：响应正文不读取重建；原头的顺序、重复值、大小写全部保留，只增加更严格的 CSP。每一条源站 CSP 都仍参与限制，不替换/删改它；不修改任何 CORS、Cookie、压缩字段，预检及业务响应完全原样，源站拒绝仍阻止 POST。TLS 仍由浏览器端到端验证。不能将其描述为所有响应头逐字未变。

不使用 JavaScript 重写 window.open，不依靠延迟关闭，不通过 Playwright 全局路由合成 OPTIONS，不启用 bypass_csp 或 ignore_https_errors。非 CORS 原生上下文及旧 bridge 不受此次文档策略影响。协议调用失败继续停止，绝不降级为不加限制后继续。

**兼容边界：** 需要新窗口的登录或业务在该模式仍不支持。此前这些目标就不在支持范围；不能为了通过测试将其宣称已接通。使用同一受控页面的正常登录衔接继续保留。隐藏登录模板误判、真实平台表单/SSO 适配和第一条真实 JD 连续验收仍未完成，本变更不重交此前被阻断的登录判断修正。

## 开发验证

单元测试覆盖新增限制与原 CSP 并存、重复 Set-Cookie 和压缩头保留、真实 CORS 响应不变、robots/错误文档、拒绝响应及协议失败不降级。

四组生产原生浏览器保留原全部正常和反例路径，增加页面最初内联脚本、普通/无 opener 的 window.open、target=_blank 链接和 POST 表单的多次尝试；服务器必须零收到 /apply，主岗位页和已取得搜索数据仍可用。人工源自己明确允许 popups，证明是额外限制生效；原有正常 CORS 预检/POST、自动查询到报告、来源拒绝不发 POST 保持。

这些是人工源测试，不等于实际猎聘、用户账号、当前网络或所有第三方站点认证。当前 PR 按已知功能问题和实际验证决定是否可合并，不只看测试数量。

## 公开依据

- W3C CSP3 sandbox 与多策略并行：https://www.w3.org/TR/CSP3/#directive-sandbox ，https://www.w3.org/TR/CSP3/#multiple-policies
- Chrome DevTools Protocol Fetch.continueResponse：https://chromedevtools.github.io/devtools-protocol/tot/Fetch/#method-continueResponse
- Playwright 新 page 事件可能在首次请求响应开始后才触发：https://playwright.dev/python/docs/api/class-browsercontext#browser-context-event-page
