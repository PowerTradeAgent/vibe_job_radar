# 猎聘当前搜索页依赖验证（2026-09-23，PR62）

关联 #45、#49、#50。目标仍是原关键词的一页真实列表、一个完整真实 JD 和同批报告。本记录不能作为该目标已通过的证据。

## 判断与改动

当前公开 `/zhaopin/` 使用异步加载的界面。实际原生浏览器观察到，营销和统计 XHR 被当作未知业务请求后会中止整个搜索任务；只有允许的静态脚本仍不足以完成初始化。

- 精确识别 `api-wanda.liepin.com` 的营销展示/统计，以及 `statistic.liepin.com/statisticPlatform/standardFLog.json`。这些请求在本机直接终止，域名不进入隧道许可，不发请求；诊断标记为可选依赖，不把它们当作正文失败。
- 补充 `feim.liepin.com/lp-manifest.json` 的 GET、其公开加载器使用的 `/lp-manifest.js`，以及 `concat.lietou-static.com/fe-im-pc/v6/` 静态资源。它们是搜索页使用的共享界面依赖，不赋予聊天、消息或任意接口访问权限。
- 补充已观察到的 `/api/com.liepin.searchfront4c.pc-search-job-cond-init` POST/OPTIONS，只读取筛选项。沿用搜索 API 的固定页面 Origin、预检检查、robots 和配额；它的响应不能成为岗位卡片。

未知业务请求仍停止。平台拒绝、验证码、登录错误不作为可选依赖忽略。未修改浏览器身份、网络出口或平台脚本。

## 公开依据

2026-09-23 读取官方页面引用的脚本，并由本项目原生 Edge 独立观察请求。只在本地临时目录检查公开代码，不将第三方原始代码、会话、请求头或响应正文提交到仓库。

- `https://concat.lietou-static.com/fe-www-pc/v6/js/pages/search-jobs.b09b5b41.js`：共享容器清单和静态资源加载地址。
- 同站 `common.0081c3a9.js`：筛选初始化和营销操作的函数定义。
- 同站 `src_pages_search-jobs_bootstrap_js-node_modules_moment_locale_sync_recursive_-src_components_-146d1f.bf7780c3.js`：搜索条件初始化、页面内 `history.replaceState` 和只读搜索调用。

脚本表明正常页面交互可能无需对含参数的搜索 URL 再次 GET，但**目前还没有实站验证完成此路径**。不能由此删除主站明确的 robots 查询限制，也不能把默认推荐结果当成原关键词结果。后续需要区分实际文档请求与页面内历史地址，并验证请求/响应和原筛选条件一致。

## 验证与尚未完成

新增回归覆盖：营销/统计请求无网络发送、未知路径仍停止、取消优先、可选失败不污染正文诊断、精确静态资源及筛选初始化边界。现有人工 HTTPS 原生搜索流程增加可选请求同时发生的场景，完整列表、正文和报告断言保留。

真实验证使用用户已经保存的网络配置、同一共享频率账本和新建的应用浏览器上下文，没有读取日常浏览器会话或提交账号。当前已观察到真实搜索页产生筛选初始化及搜索预检；完整实站结果、最终提交的 CI 和剩余故障在关联 Issue/PR 中分别记录，不能将受控通过等同于实站完成。

出现过一次首个请求的 `local_proxy_connection_failed`，以及脚本加载阶段的 `Invalid InterceptionId`，后者仍需定位。默认关键词流程仍由带参数 URL 导航开始，不能仅靠本增量宣称自动搜索已经打通。#49 / #50 保持开放。
