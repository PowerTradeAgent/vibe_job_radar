"""Browser installation facts and safe startup diagnostics, not site connectivity.

Importing this module never launches a browser or installs anything. Production
checks exercise PlaywrightBackend itself on its owning worker thread, with all
HTTP disabled and a blank page only. No accounts, URLs or collection budgets.
"""
from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..utils import utc_now
from .contracts import CrawlError

PLAYWRIGHT_REQUIREMENT = 'playwright>=1.48,<2'
VERSION_CHECK_REQUIREMENT = 'packaging>=24.2'
HEALTH_MESSAGES = {
    'not_checked': '尚未验证浏览器能否启动。包版本不等于就绪；请点“检查浏览器（不采集）”。',
    'browser_ready': '采集浏览器已通过空白页启动检查。只验证本机组件，不代表已登录或网站可采集。',
    'playwright_missing': '当前 Python 缺少 Playwright。请点击安装/修复，或使用下方当前解释器命令安装。',
    'version_validator_missing': '浏览器版本校验组件 packaging 缺失或无法导入。请点安装/修复；基础本地分析不受影响。',
    'playwright_incompatible': '当前 Playwright 版本不满足项目要求（>=1.48,<2）。修复组件后重新检查。',
    'browser_executable_missing': 'Playwright 包存在，但配套 Chromium 可执行文件不存在。请运行当前 Python 的 -m playwright install chromium；pip install Chromium 不会安装该浏览器。',
    'browser_permission_denied': '浏览器或驱动被系统拒绝执行。请检查文件权限和安全软件阻止记录；不要关闭安全防护。',
    'browser_display_unavailable': '有界面浏览器找不到图形桌面。请在本机桌面会话运行；无界面服务器需配置图形环境，不是重新下载就能解决。',
    'playwright_driver_failed': 'Playwright 驱动无法启动。请查看诊断摘要，检查当前环境与驱动文件；不要反复修改招聘账号。',
    'playwright_import_failed': 'Playwright 已有包记录，但导入失败。请检查诊断中的导入位置/异常，修复同一个 Python 环境。',
    'browser_launch_timeout': '浏览器启动超时。检查系统权限、资源和安全软件记录，然后重新检查。',
    'browser_launch_failed': 'Chromium 启动失败；不一定是未安装。请展开诊断查看阶段、异常类型和摘要。',
    'browser_context_failed': '浏览器进程已启动，但页面上下文/拦截器初始化失败。请保留诊断以定位项目或版本兼容问题。',
    'browser_check_failed': '本机浏览器检查失败。请展开诊断查看异常，现有岗位数据不会删除。',
    'dependency_install_failed': '组件安装命令失败；尚未确认浏览器就绪。请看失败阶段和脱敏安装日志。',
    'dependency_install_timeout': '组件下载/安装超时；本次安装已终止。请检查下载网络后手动重试，DNS通过不代表下载源可达。',
}


def safe_text(value: Any, limit: int = 4000) -> str:
    """Redact before truncation; diagnostics may contain authenticated package URLs."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    text = str(value or '')
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'https?://[^\s<>"\']+', '[URL REDACTED]', text, flags=re.I)
    text = re.sub(r'(?im)(authorization|proxy-authorization|cookie|set-cookie)\s*[:=][^\r\n]*',
                  r'\1: [REDACTED]', text)
    text = re.sub(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9+/_.=-]+', r'\1 [REDACTED]', text)
    text = re.sub(r'(?i)([\w-]*(?:token|password|passwd|secret|api[_-]?key)[\w-]*["\']?\s*[:=]\s*)'
                  r'(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;}]+)', r'\1[REDACTED]', text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[-limit:]


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def supported_version(value: str | None) -> bool:
    """Use pip-compatible PEP 440 semantics, including post/local releases.

    packaging belongs to the optional browser extra, not the offline core. A
    missing validator is diagnosed separately and installed by the repair action.
    """
    if not value:
        return False
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version
    try:
        return SpecifierSet(PLAYWRIGHT_REQUIREMENT.removeprefix('playwright')).contains(Version(value), prereleases=False)
    except InvalidVersion:
        return False



def command_help() -> dict:
    # Display-only commands, never parsed or executed from an HTTP parameter.
    exe = sys.executable.replace('\\', '/')
    quoted = '"' + exe.replace('"', '') + '"'
    prefix = '& ' if os.name == 'nt' else ''
    return {'package': f'{quoted} -m pip install "{PLAYWRIGHT_REQUIREMENT}" "{VERSION_CHECK_REQUIREMENT}"',
            'browser': f'{quoted} -m playwright install chromium',
            'powershell_browser': f'{prefix}{quoted} -m playwright install chromium'}


def environment_report() -> dict:
    browser_package = package_version('chromium')
    return {'schema_version': 1, 'checked_at': utc_now(), 'python': sys.executable,
            'python_version': platform.python_version(), 'playwright_version': package_version('playwright'),
            'chromium_python_package': browser_package,
            'version_validator_package': package_version('packaging'),
            'warnings': (['检测到同名 Chromium Python 包；它不是 Playwright 的浏览器，本项目不使用或导入它。']
                         if browser_package else []),
            'browser_cache_override': safe_text(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''), 1000),
            'mode': 'headed', 'stage': 'not_checked', 'code': 'not_checked', 'ready': False,
            'launch_tested': False, 'executable_exists': None, 'executable_path': '',
            'message': HEALTH_MESSAGES['not_checked'], 'commands': command_help(),
            'scope': 'Local component startup only; no job requests, login or platform certification.'}


class BrowserStartupError(CrawlError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__(report['code'])


def failed_report(report: dict, exc: Exception, *, code: str | None = None) -> dict:
    """Only startup errors are classified here, never arbitrary website errors."""
    stage = report.get('stage', 'unknown')
    text = str(exc).lower()
    if code is None:
        if isinstance(exc, PermissionError) or any(s in text for s in ('eacces', 'access is denied', 'permission denied', 'winerror 5')):
            code = 'browser_permission_denied'
        elif 'executable doesn\'t exist' in text or 'executable does not exist' in text:
            code = 'browser_executable_missing'
        elif any(s in text for s in ('missing x server', '$display', 'cannot open display', 'no display server')):
            code = 'browser_display_unavailable'
        elif stage == 'import':
            code = 'playwright_missing' if isinstance(exc, ModuleNotFoundError) and (getattr(exc, 'name', '') or '').startswith('playwright') else 'playwright_import_failed'
        elif stage == 'driver':
            code = 'playwright_driver_failed'
        elif stage == 'context':
            code = 'browser_context_failed'
        elif 'timeout' in type(exc).__name__.lower() or 'timeout' in text:
            code = 'browser_launch_timeout'
        else:
            code = 'browser_launch_failed'
    return {**report, 'ready': False, 'code': code, 'message': HEALTH_MESSAGES.get(code, HEALTH_MESSAGES['browser_check_failed']),
            'error_type': type(exc).__name__, 'error_summary': safe_text(exc)}


class _OfflineTransport:
    """No network even if Chromium attempts a background request during a check."""
    def __init__(self, *args, **kwargs):
        self.blocked: set[str] = set()

    def allowed_resource(self, url):
        return False

    def fetch(self, *args, **kwargs):
        raise CrawlError('diagnostic_network_disabled')

    def reserve(self, *args, **kwargs):
        raise CrawlError('diagnostic_network_disabled')


def probe_browser(*, headless: bool = False, executable_path: str | None = None) -> dict:
    from threading import Event
    from .adapters import builtins
    from .browser import PlaywrightBackend
    backend = None
    try:
        backend = PlaywrightBackend(builtins().get('boss'), None, Event(), headless=headless,
                                    executable_path=executable_path, transport_factory=_OfflineTransport)
        # Actual same page/context initialization as collection, no navigation to a site.
        backend.startup_report['stage'] = 'blank_page'
        backend.page.set_content('<title>Vibe Radar browser check</title><p>本机浏览器检查成功</p>')
        if backend.page.title() != 'Vibe Radar browser check':
            raise RuntimeError('blank page verification failed')
        return {**backend.startup_report, 'stage': 'ready', 'code': 'browser_ready', 'ready': True,
                'message': HEALTH_MESSAGES['browser_ready']}
    except BrowserStartupError as exc:
        return exc.report
    except Exception as exc:
        return failed_report(backend.startup_report if backend is not None else environment_report(),
                             exc, code='browser_check_failed')
    finally:
        if backend:
            backend.close()
