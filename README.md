# Vibe Job Radar｜招聘要求与个人证据工作台

**先拿到真实数据：** 运行 `scripts/run_real_example.py`，输入 y，即可从官方公开接口取得一条 Anthropic 架构师岗位并打开本地报告。无需URL、Key、账号或浏览器组件；这不是BOSS数据或合成演示。详见[第一份真实结果](docs/FIRST_REAL_RESULT.md)。

**0.2.0 新手入口：在首页点击“新手推荐：选平台 → 找岗位 → 勾选采集”，无需先整理职位 URL。** 详见[按按钮操作指南](docs/GUIDED_COLLECTION.md)。新增可选真实浏览器后端，平台原生页面人工登录后继续自动采集；三站页面配置尚未实站认证。原高级表单和本地分析继续保留。

**当前代码与说明统一从 main 获取。** 0.2.0 为源码版本，尚未创建正式 GitHub Release/tag；不是包含 Python 和 Chromium 的免安装 EXE。

## 已经打开过工作台

先停止旧终端并备份工作区，完整解压新版本源码，进入新目录，沿用已成功的解释器启动。例如：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe scripts/start_workbench.py
```

不需要更换 Anaconda 环境。打开本次终端给出的完整地址，从首页进入 `/guided` 新手向导。只刷新旧网页不会更新运行中的代码。完整更新与首次安装说明见 [FIRST_RUN.md](docs/FIRST_RUN.md)，或双击源码中的 [START_HERE.html](START_HERE.html)。

## 想让程序自己找岗位

在新手向导中先安装可选采集浏览器，再选择平台、关键词、1页/5条和实际访问范围。点击“打开搜索并读取岗位”。需要登录时，操作程序弹出的独立采集浏览器，在平台原生页正常登录，然后回向导读取当前列表。勾选岗位，点击“采集所选并生成报告”。

程序负责汇总列表观察到的链接、打开详情、保存最终地址和独立正文、生成本批报告。你不必填写 API endpoint、分页游标、选择器，也不必逐条复制详情 URL。没有稳定链接或结构不兼容的页面需要逐站维护，不会编造结果。

**本次不接收或自动填写账号密码。** 平台原生页面的人工登录已经接入，完成后复用当前内存会话继续采集；退出服务不保存 Cookie。三站均为 `not_live_verified`，没有你的现场授权与真实样本不能宣称全部实测成功。

## 不需要浏览器采集，也能使用

本地粘贴、导入和规则分析不需要任何 Key 或额外运行依赖。先点击“运行合成演示”认识报告，再粘贴你有权处理的真实完整 JD，保存并分析。下载 `requirements_zh.csv` 核对逐条原文。

原 `/advanced` 保留 URL、Brave Search API 和授权 JSON 数据源路线。已有链接走 URL；想用搜索服务找链接，需要自己的 Brave Key；没有数据供应方就不选 JSON 接口。见 [逐格填写案例](docs/COLLECTION_FORM_CASES.md)。旧 HTTP 路线不共享新向导的浏览器会话。

高级页同时提供原文复核、个人项目、附件和指标、精确要求映射、个人报告与证据包。没有本人证据的数字保持待填，能力覆盖率不是胜任度或录用概率。

## 强制频次与运行边界

新向导默认页面导航至少15秒、60次/小时、200次/滚动24小时；经桥接的HTTP请求至少0.5秒、600次/小时、3000次/滚动24小时。同一工作区和站点的任务共享持久化配额，失败不退回，限流触发冷却，界面不能提高上限。

新向导的当前批次由后端线程执行，关工作台网页仍继续；暂停/停止控制任务，关闭终端退出服务。旧高级页仍由网页驱动后续步骤。这不是开机自启的长期调度服务。

浏览器请求保留公网DNS/IP绑定、TLS和域名边界。Fake-IP导致的 `non_public_address` 现在可以点击网络检查查看地址，但不会通过关闭安全检查来修复。验证码、robots拒绝、资源域名或页面结构变化仍可能中止采集。

## 使用说明与开发契约

| 目标 | 文档 |
|---|---|
| 安装、更新、启动和第一份结果 | [零基础指南](docs/FIRST_RUN.md) |
| 不整理URL，按按钮找岗位 | [浏览器采集向导](docs/GUIDED_COLLECTION.md) |
| 原有URL/Search/JSON表单 | [字段来源和填写案例](docs/COLLECTION_FORM_CASES.md) |
| 采集和本人证据操作 | [操作手册](docs/WORKFLOWS.md) |
| 已实现和未认证的边界 | [采集能力](docs/ACQUISITION_CAPABILITIES.md) |
| Pythonic 插件与接口 | [插件契约](docs/BROWSER_PLUGIN_CONTRACT.md) |
| 升级和源码合并说明 | [0.2.0 说明](docs/RELEASE_0_2_0.md) |

## 启动与验收

Windows可双击 `start_windows.bat` 或执行 `py -3 scripts/start_workbench.py`；macOS/Linux执行 `python3 scripts/start_workbench.py`。Python需3.10+。默认数据保存在用户主目录 `.vibe-job-radar`，停止服务后备份整个目录，升级时不要清空。

```bash
python scripts/run_tests.py --report acceptance/results.json
python scripts/start_workbench.py --doctor
python scripts/run_demo.py
# 已安装可选浏览器依赖后，运行受控双浏览器测试
python scripts/run_guided_browser.py
```

安装CLI是可选项：`python -m pip install -e .`。Playwright只在启用浏览器功能时需要，可在向导里安装。自动测试使用人工职位与模拟上游，不能替代真实招聘网站认证；以对应提交的CI为准。

旧CLI文档完整保留在 [README_CLI.md](README_CLI.md)。服务仅监听本机，不适用于公网多人部署。升级新增的 `browser_fetch` 来源不保证旧版本认识，回退时恢复对应的完整工作区备份。

### 已装Playwright仍提示浏览器未就绪？

先读[浏览器安装与启动修复](docs/BROWSER_SETUP.md)。`pip install Chromium`装的是同名Python包；浏览器本体要通过当前Python的`-m playwright install chromium`下载。新手页可点“检查浏览器（不采集）”，区分包、可执行文件与真实空白页启动；安装失败阶段和脱敏输出直接可见。
