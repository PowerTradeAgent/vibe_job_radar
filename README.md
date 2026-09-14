# Vibe Job Radar｜招聘要求与个人证据工作台

**已打开高级页但不知道怎么填？先看 [URL 与搜索 API 逐格填写案例](docs/COLLECTION_FORM_CASES.md)，新版页面也提供就地提示和不采集预检。**

将获准处理的招聘文本变成逐条要求，再与自己的项目、附件和量化指标关联，生成描述、覆盖矩阵与缺口清单。

**先看这里：下载完整 main 源码并解压，双击 [START_HERE.html](START_HERE.html) 阅读浏览器版入门指南；完整步骤见 [零基础操作指南](docs/FIRST_RUN.md)。无需学习 Git、JSON、Docker 或数据库。仍需安装 Python，并非免安装 EXE。**

## 最短启动路线

电脑已有 Python 3.10+ 时：Windows 双击 `start_windows.bat`；macOS/Linux 在源码目录运行：

```bash
python3 scripts/start_workbench.py
```

Windows 也可在源码目录执行：

```powershell
py -3 scripts/start_workbench.py
```

浏览器未自动打开时，复制终端中的完整本地地址（包含 `#token`）。不要分享地址；保持终端运行，Ctrl+C 停止。源码运行无需 pip 安装，核心程序无第三方运行依赖。当前 CI 覆盖 Windows/Linux Python 3.10～3.12、macOS Python 3.12，其他组合不由这组 CI 保证。

## 第一个实际结果

先点“运行合成演示”认识报告；再将一条你有权处理的**真实完整 JD**填进基础工作台，保存、选择岗位、分析。下载 `requirements_zh.csv` 核对逐条原文。演示数据与真实库隔离，摘要不能标成正文，真实模式也不等于来源已经第三方认证。

点击“进入自动采集 / 原文复核 / 附件与量化指标工作台”，可以创建采集任务、复核要求、上传个人证据、填写指标并生成个人报告。这些本地操作不需要模型 Key；搜索路线另需自己的 Brave Key 和相应使用权限。

## 当前到底能采集什么

**准确定位：已实现搜索线索发现、有限受控的公开 HTML 正文抓取、授权 JSON 数据源接入，以及自动入库分析。不是“各大招聘网站已全面实站接通的专用爬虫”。**

| 路线 | 需要的输入 | 已实现 | 不代表什么 |
|---|---|---|---|
| 搜索 | 平台、岗位、Brave Key、预算 | 搜索引擎分页发现，再尝试获准正文 | 不是直接登录各站搜索；摘要不是 JD |
| URL | 实际职位链接、访问范围 | HTTPS 获取、解析、去重和分析 | 不执行网页 JavaScript，不共享你的浏览器登录态 |
| 数据源 | 实际授权 JSON API、契约、必要的 Token | 按约定字段和游标分页入库 | 任意招聘网址不能当 API；不是已认证官方平台适配器 |

8 个平台是**域名配置**，不是 8 项实站认证。页面需登录、验证码、robots 拒绝、动态渲染或未知结构时，现有 HTTP 抓取可能失败。自动测试用模拟上游，不能当作实际网站成功率。详情见 [采集能力说明](docs/ACQUISITION_CAPABILITIES.md)。

## 按目标阅读

| 目标 | 文档 |
|---|---|
| 下载、安装、启动、第一条真实 JD、升级与备份 | [FIRST_RUN.md](docs/FIRST_RUN.md) |
| 自动采集的字段、任务状态；原文、附件、指标和个人报告 | [WORKFLOWS.md](docs/WORKFLOWS.md) |
| 现有能力、缺少什么、下一阶段逐站爬虫及验收 | [ACQUISITION_CAPABILITIES.md](docs/ACQUISITION_CAPABILITIES.md) |
| 旧版 CLI、更多文件格式、自定义规则 | [README_CLI.md](README_CLI.md) |
| 证据约束与规则升级保护 | [EVIDENCE_CONTRACT.md](docs/EVIDENCE_CONTRACT.md)、[REVIEW_GUARDRAILS.md](docs/REVIEW_GUARDRAILS.md) |

默认数据目录是用户主目录下 `.vibe-job-radar`。导出的证据包含个人数据；哈希只证明字节一致，不证明经历真实。服务只监听本机，不能直接当多人网站部署。不要把密码、Cookie、Token 放进 JD、截图、Issue 或仓库。

## 开发验收

```bash
python scripts/run_tests.py --report acceptance/results.json
python scripts/start_workbench.py --doctor
python scripts/run_demo.py
```

安装为命令行工具是可选项：`python -m pip install -e .` 后可用 `vibe-radar-ui`、`vibe-radar`、`vibe-radar-collect`。Playwright 仅用于独立浏览器验收脚本，不是当前采集器的浏览器后端。以具体 commit 的 CI 结果为准，不将历史通过状态用于新版本。
