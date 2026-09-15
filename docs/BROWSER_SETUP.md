# DNS 已通过，但“浏览器组件未就绪”：逐步修复

## 先分清三件事

DNS通过，只说明招聘域名解析地址通过检查，与Chromium是否安装无关。

`pip install playwright` 安装的是Python控制库。`python -m playwright install chromium` 才会为这个Playwright版本下载配套浏览器。**`pip install Chromium` 安装的是PyPI同名示例包，不是浏览器；其0.0.0版本和2.4kB安装包不能使采集器就绪。** 本项目不需要也不会导入该同名包。你电脑已有日常Chrome，也不代表Playwright需要的浏览器版本存在。

参考：[Playwright官方浏览器安装](https://playwright.dev/python/docs/browsers)、[PyPI chromium项目](https://pypi.org/project/chromium/)。

## 已使用指定Anaconda解释器的Windows用户

先在运行工作台的终端按Ctrl+C停止旧进程。不要重新安装Anaconda，不用再改DNS。

在Git Bash执行这一整行（终端里执行，不要填进网页）：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe -m playwright install chromium
```

用户示例已有Playwright 1.57.0，符合项目要求；不需要先升级才能执行上面的下载。命令应出现浏览器下载/解压或已经存在的结果，而不只是`Requirement already satisfied: playwright`或`Successfully installed Chromium-0.0.0`。下载大小和版本随Playwright改变，不要把固定大小当成功标准。

手动安装后，进入这份源码目录，再执行：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe scripts/check_browser.py
```

该检查实际打开再关闭一个空白采集浏览器，不打开招聘网站、不登录、不产生采集配额。看到`ready: true`、`code: browser_ready`、`mode: headed`后，再运行原命令：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe scripts/start_workbench.py
```

`check_browser.py`是本次修复新增的文件；还没更新源码时，可先用`python -m playwright open --browser chromium about:blank`测试是否能手工打开空白浏览器。你的完整命令为：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe -m playwright open --browser chromium about:blank
```

看到空白窗口后手动关闭。这条官方CLI冒烟仅验证基础启动，不替代新版对项目拦截器和上下文初始化的检查。

PowerShell的路径写法不同，使用`&`运行：

```powershell
& "D:\code_environment\anaconda_all_css\py312\python.exe" -m playwright install chromium
```

误装的Python示例包不需要反复安装。可选清理：确认没有别的项目使用该Python包后，执行同一解释器的`-m pip uninstall Chromium`并确认；这不是让你卸载系统Chrome。

## 不想敲命令：从界面操作

完整取得含本次修复的源码并重启，进入首页的新手采集向导 `/guided`：

1. 点 **“检查浏览器（不采集）”**。不需要先创建采集任务，也不需要网站账号。检查会短暂打开一个空白窗口。
2. 如果提示缺少Playwright或浏览器文件，点 **“安装 / 修复采集浏览器”**并确认。程序使用当前Python，先安装/检查控制库，再下载配套Chromium，最后实际启动验证。
3. 展开 **“浏览器诊断、修复命令与安装日志”**。查看Python完整路径、Playwright版本、配套可执行文件路径、存在状态、失败阶段与错误摘要。安装日志保留脱敏的有限尾部，不再被丢弃。
4. 只有空白页启动检查通过，才显示浏览器就绪。已有采集浏览器会话时，先点“停止并关闭登录会话”，不会偷偷关闭你的登录窗口再安装。
5. 就绪后再搜索/采集。旧失败任务的历史状态不会自动重写，也不会自动重试网站；在原任务点击“登录后重新搜索”或新建任务即可。

页面的包版本、安装操作状态和最近一次启动检查是三种不同信息。刷新网页不会自动下载或打开浏览器。服务重启后需要重新检查，就绪不是永久认证。

## 各种结果怎样处理

| 结果 | 实际含义 | 下一步 |
|---|---|---|
| playwright_missing | 当前解释器无法导入Playwright | 安装/修复；不要在另一个Python环境安装 |
| playwright_incompatible | 版本不在>=1.48,<2 | 按诊断给出的当前解释器命令修复，再下载匹配浏览器 |
| browser_executable_missing | 包存在，但预期的浏览器文件不存在 | `同一个python -m playwright install chromium` |
| playwright_import_failed / playwright_driver_failed | 导入依赖或driver启动失败 | 复制诊断，检查文件/环境；不是修改招聘URL或密码 |
| browser_permission_denied | 操作系统拒绝执行 | 检查账户权限与安全软件记录；不要直接关闭防护 |
| browser_display_unavailable | 有界面浏览器没有可用图形桌面 | 在本机桌面运行；Linux服务器配置合适图形环境 |
| browser_launch_failed / browser_launch_timeout | 文件可能已存在，但启动失败/超时 | 按原始异常摘要处理系统资源/兼容/权限，不反复假装重装就成功 |
| browser_context_failed | 进程启动后，项目上下文或路由初始化失败 | 提交脱敏诊断供代码维护定位；不谎报缺Chromium |
| dependency_install_failed / dependency_install_timeout | 下载/安装命令失败或超时 | 查看失败的package_install/browser_download阶段与日志 |

Playwright默认Windows浏览器目录通常是`%USERPROFILE%\AppData\Local\ms-playwright`；以诊断中实际的`executable_path`为准。安装和运行必须使用同一个系统用户和相同`PLAYWRIGHT_BROWSERS_PATH`。升级控制库后可能需要重新下载浏览器；只装headless shell不够，本应用需要可见Chromium窗口完成人工登录。

DNS通过并不能证明浏览器下载源可访问。下载网络/代理/证书错误按官方说明处理，不关闭TLS校验，也不改项目公网检查。安装命令日志已脱敏URL和常见凭据，但可能仍显示本机路径，分享前可遮住用户名；不要上传账号密码或Cookie。

## 给维护人员的可观测性说明

浏览器初始化按import→driver→executable→launch→context→ready分阶段记录。缺文件和实际launch被分别标记；原有browser_missing历史记录不迁移，新的启动失败保存startup_diagnostic。

检查与安装排入原后端线程，实际使用相同PlaywrightBackend初始化；用空白页和拒绝网络的transport，不访问任何招聘URL。不接受用户从HTTP指定命令、任意可执行文件或测试网址。安装使用固定subprocess参数、shell=False，日志有限且脱敏；超时或退出服务终止本次安装进程树，不删除用户工作区。

单元测试覆盖错误分类和失败路径；专门的Windows/Linux浏览器CI分别使用用户版本1.57.0和当前兼容版本验证真实有界面启动。测试不需要招聘账号，也不能证明招聘平台可用性。
