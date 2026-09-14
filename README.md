# Vibe Job Radar｜本地招聘要求工作台

将你有权处理的职位文本整理为逐条要求、来源证据、能力汇总、描述模板与缺口清单。Python 3.10+，没有第三方运行依赖。

## 从源码开始

安装 Python 3.10+，下载完整源码 ZIP 并解压。PR 尚未合并时，请先切换到 PR 分支下载。

Windows：双击 `start_windows.bat`，或在源码目录运行：

```powershell
py -3 scripts/start_workbench.py
```

macOS/Linux：

```bash
python3 scripts/start_workbench.py
```

启动器会打开本地浏览器。未自动打开时，将终端里的完整地址复制到同一台电脑的浏览器。保持终端运行，Ctrl+C 停止。默认数据保存在用户主目录下 `.vibe-job-radar`。

## 第一次使用

1. 点击“运行合成演示”熟悉报告；演示数据库与真实数据库隔离。
2. 将获准处理的完整 JD、真实标题和来源填写进表单，确认完整性后保存。片段应标记为摘要。
3. 选择岗位，点击“分析真实数据并生成报告”。
4. 下载 `requirements_zh.csv`、`descriptions.md`、`role_descriptions.md` 和 `evidence_gaps.md`。

粘贴、JSON/JSONL/CSV 文件导入、演示与规则分析无需 API Key。CSV 可用 Excel 打开；当前不是 XLSX 导出。批量导入限制为 UTF-8、1 MB、1000 条，并先校验全部行。

可选的在线搜索发现使用原有 Brave Search API，需要你自己的 `BRAVE_SEARCH_API_KEY`，并在页面确认本次请求预算。搜索结果是摘要线索，不是完整职位正文；工作台显示请求数、线索数与失败状态。环境变量需要在启动前设置，项目不自动加载 `.env`。

当前工作台没有招聘平台自动登录功能，也不需要你提交平台账号资料。平台配置表示域名检索支持，不等于已认证的平台接口或全量数据覆盖。公开正文获取仍使用原 CLI 的显式访问控制入口。

## 报告怎么理解

每次分析保存到独立目录。无正文、岗位未匹配、无已接受正向要求时会明确提示。页面预览最多 100 行要求，下载文件包含全部结果。

通用能力并集不等于本人完全满足所有岗位。没有本人证据时，量化描述保留【待填】。量化指标应有基线、当前值、样本量、比较口径、观测窗口及证据引用。

候选人证据和人工复核仍使用原 CLI；原始完整说明保留在 [README_CLI.md](README_CLI.md)。工作台目前没有这两类编辑表单。详细入门步骤见 [FIRST_RUN.md](docs/FIRST_RUN.md)。

## 开发者

```bash
python -m pip install -e .
vibe-radar-ui --doctor
vibe-radar-ui
vibe-radar --help
python scripts/run_tests.py --report acceptance/results.json
python scripts/run_demo.py
```

`vibe-radar-ui` 是新增入口，原命令兼容保留。源码启动不需要安装包。CI 增加 Windows/Linux Python 3.10、3.11、3.12 和 macOS Python 3.12 验证；通过状态以对应提交的 Actions 为准。

自动测试使用人工编写的数据和模拟搜索响应，并实际执行本地 HTTP、SQLite 与分析管线。这不代表使用真实招聘平台账号或真实搜索密钥完成了线上采集认证。

这是单用户本地应用，只监听 `127.0.0.1`。不要把端口开放到公网。退出后可以备份整个数据目录；本地文件未加密，请保管好系统账户和备份。
