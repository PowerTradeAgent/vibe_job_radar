# 浏览器就绪修复的合并前复核

PR #18 / 维护 Issue #19。合并后统一从 main 下载，不需要保留修复分支。

## 用户可见变化

不兼容的 Playwright 先按包元数据判断，再决定是否导入；因此“先检查、再修复”不会先把不兼容的客户端留在 Python 模块缓存里。手工更换已经运行中的 SDK 时仍应重启工作台。

版本比较使用 PyPA `packaging` 的 PEP 440 规则，不再使用只认识三段数字的正则。`1.57.0.post1`、`1.57.0+conda`等满足声明约束的版本不会被反复要求修复。`packaging>=24.2`只加入可选浏览器依赖；基础粘贴、存储和规则分析仍可在没有这些可选包时运行。安装/修复按钮会一并安装版本校验组件，缺失时会明确提示，不误报Chromium缺失。

浏览器文件检查使用 `stat()`，路径无权访问时保留权限错误，不编造为文件不存在。如果浏览器已经启动，但空白页内容或标题验证失败，仍保留实际可执行路径、存在状态和 `launch_tested`，阶段显示 `blank_page`，不会倒退成“尚未检查”。

## 当前操作

打开main的新手向导，先点“检查浏览器（不采集）”；缺少控制库、浏览器或版本校验组件时，再点“安装 / 修复采集浏览器”。不需要更换你已有的Anaconda解释器。完整流程见[BROWSER_SETUP](BROWSER_SETUP.md)。

手动安装浏览器可选依赖时使用当前Python：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe -m pip install "playwright>=1.48,<2" "packaging>=24.2"
/d/code_environment/anaconda_all_css/py312/python.exe -m playwright install chromium
/d/code_environment/anaconda_all_css/py312/python.exe scripts/check_browser.py
```

正常情况下优先用界面；不要求每次启动都重新安装。通过空白页检查仍不等于招聘网站已登录或获准采集。

## 回归与依据

四项审查各有回归：导入次序及同进程修复重检、权限与真实缺文件区分、内容/标题验证失败保留启动事实、PEP 440 post/local/epoch/pre/dev/边界语义。另以`python -S`确认基础功能不导入可选依赖。

- PyPA SpecifierSet：https://packaging.pypa.io/en/stable/specifiers.html
- Python Path.stat / Path.is_file：https://docs.python.org/3/library/pathlib.html

清理脚本仅适用于本次用户授权的main基线、精确分支、merge父提交和标题；删除前校验全部提交已纳入main，不是永久自动删分支策略。最终测试与清理产物在Issue #19记录。
