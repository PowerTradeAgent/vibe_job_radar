# 外部技术依据与本次检索说明

核对日期：2026-09-14。下列是技术接口/格式的第一方参考，不是已采集的真实职位样本。

1. Schema.org JobPosting：职位结构化数据类型；用于独立正文解析，不据此保证各招聘平台都提供完整JSON-LD。
   https://schema.org/JobPosting
2. Python urllib.robotparser：can_fetch、crawl_delay、request_rate；本实现另加“读取失败即停止”的保守访问策略。
   https://docs.python.org/3/library/urllib.robotparser.html
3. Python ssl：TLS默认安全上下文和主机名验证；用于网络实现的证书验证。
   https://docs.python.org/3/library/ssl.html
4. Brave官方搜索API示例与官方MCP仓库：用于搜索端点、count、offset等请求契约。MCP仓库是接口核对材料；本项目直接调用REST API，不依赖或复制MCP服务器。
   https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
   https://github.com/brave/brave-search-mcp-server
5. OpenAI Structured Outputs 与 Chat Completions API：用于可选的严格JSON schema提案接口；不保证用户账户上的所有模型都支持所有参数。
   https://developers.openai.com/api/docs/guides/structured-outputs
   https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/

本次公开网络检索检查了BOSS直聘、猎聘、前程无忧相关结果，观察到摘要提及AI编程工具，但部分结果打开后落到首页/聚合页或无法确定独立职位正文。没有将它们保存为经确认的职位样本，没有用这些摘要计算市场比例，也没有宣称完成三家平台或全市场的完整采集。

本次三个招聘站点robots的浏览核对没有取得可用正文，因此不声称已核实它们当前允许本工具自动抓取。真实执行时按站点当前返回结果和用户明确访问范围决定，不能把本文当成访问授权。

仓库中的jobs.synthetic、candidate.synthetic和演示报告均为测试数据。模型和搜索网络路径采用模拟响应测试；真实账号、预算、模型可用性、区域网络和网站页面适配尚待使用方的授权环境验收。
