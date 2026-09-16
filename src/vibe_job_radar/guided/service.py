"""Local guided task service. All browser calls stay on one owning thread.

UI requests enqueue actions and return immediately. Jobs and quotas survive process
restarts; live sessions/passwords do not. Dependency injection enables offline tests.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import queue
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from ..collection import writer_lock
from ..models import JobRecord
from ..store import Store
from ..utils import atomic_json, utc_now
from ..workspace import InputError, text_field
from .adapters import Registry, builtins
from .browser import PlaywrightBackend
from .contracts import CrawlError
from .rate import RateLedger, RateLimit
from .transport import diagnose_host
from .browser_health import (BrowserStartupError, HEALTH_MESSAGES, environment_report,
                             failed_report, probe_browser, safe_text)
from .browser_install import install_commands, run_command

MESSAGES = {
    'login_rate_limited': '打开登录的频次已达到限制：至少间隔5分钟，滚动24小时最多3次。请使用已经打开的登录窗口或等待，不要反复新建任务。',
    'list_page_limit': '本任务列表页数已达到上限。新任务仍受站点共享配额限制。',
    'operation_error': '操作未完成，已有结果保留。请检查环境与页面。',
    'new': '任务已保存。正在准备采集浏览器。',
    'browser_missing': '浏览器组件未就绪。请先点击“安装/修复采集浏览器”，完成后再打开任务。',
    'opening': '正在打开站内搜索；浏览器可能弹出。无需复制职位链接。',
    'reading': '正在读取当前列表并识别具体岗位链接。',
    'ready': '岗位已列出。勾选要研究的岗位，再点击“采集所选并生成报告”。',
    'empty_list': '没有识别到岗位链接。请在采集浏览器完成搜索/登录，再点“读取当前列表”。',
    'manual_required': '需要你操作采集浏览器：完成登录/验证或打开搜索结果，然后回这里读取当前列表。',
    'manual_browser_open': '已打开平台登录页面。请在采集浏览器完成密码/扫码/验证码登录，然后读取当前列表。',
    'collecting': '正在按强制频次依次打开选中岗位，真实详情链接会自动保留。',
    'completed': '本批次已结束。请查看每条结果和报告；有报告不等于所有岗位均采集成功。',
    'paused': '已请求暂停；当前网络调用结束后停止。没有完成的岗位保留，可继续。',
    'stopped': '任务已停止，采集浏览器已关闭。已保存结果保留。',
    'interrupted': '程序曾退出；登录状态未保留。请重新打开/登录，已完成岗位不会重复采集。',
    'non_public_address': '域名解析到了非公网地址（可能为 198.18.* Fake-IP）。点击“网络检查”查看地址；先修正代理/DNS，不要重复增加采集预算。',
    'dns_error': '域名解析失败。检查本机 DNS/网络后重试；此错误与账号密码无关。',
    'robots_denied': '当前站点 robots 规则不允许此自动访问路径；本程序已停止。可使用获准接口或回基础页粘贴有权处理的正文。',
    'robots_unavailable': '无法确认 robots 规则；已停止自动采集。不是填写错误。',
    'publisher_delay_exceeds_policy': '站点要求比当前策略更长的抓取间隔，已停止；需适配站点延时策略，不能忽略其限制。',
    'http_401': '站点拒绝此请求（401）；任务已暂停，请检查登录或权限。冷却期内不会反复请求。',
    'http_403': '站点拒绝此请求（403）；不代表单纯缺少密码。请核对访问条件，程序不会换身份继续访问。',
    'http_429': '站点限流（429），已停止并记录至少5分钟冷却。服务要求更久时遵循其 Retry-After。',
    'cooldown': '该站点仍处于冷却期。新建任务也不能绕过，请稍后重新操作。',
    'hourly_limit': '本工作区该站点的小时配额已用完。请稍后再操作，不要新建任务试图提速。',
    'daily_limit': '本工作区该站点的每日配额已用完。已有数据仍可分析。',
    'publisher_wait': '该来源要求降低访问频率；正在等待，已有进度保留，可以暂停或停止。',
    'rate_storage_error': '限频记录无法安全读取或写入，已停止联网；不会重置配额后继续。',
    'publisher_policy_invalid': '发布方限频规则无法安全处理，已保留进度并停止自动访问。',
    'rate_wait': '正在等待安全间隔；不是卡死。可以暂停或停止。',
    'clock_rollback': '系统时钟回拨，限频保护暂停了请求。请先校准本机时间。',
    'structure_changed': '页面没有可确认的完整职位容器；未把整页/推荐职位冒充正文。可在基础页粘贴获准正文。',
    'page_not_ready': '页面没有在限定时间内就绪。可到采集浏览器手动操作，然后读取当前列表。',
    'browser_closed': '采集浏览器已被关闭。点击重新搜索或打开登录以新建会话。',
    'network_error': '网络连接未完成。已有结果保留；检查网络后手动继续，不做无限重试。',
    'resource_domain_blocked': '页面需要适配器尚未允许的资源域名，已阻止；需要审核站点适配，不能任意放行。',
    'write_not_allowed': '该页面需要未开放的写入请求。采集模式只读，不代投简历或发消息。',
    'redirect_requires_attention': '目标重定向无法安全处理，请到采集浏览器确认页面。',
    'login_origin_changed': '页面离开了该平台允许的登录域名，未填入账号密码。请人工确认。',
    'not_job_url': '当前页面不是适配器识别的职位详情；没有保存为完整 JD。',
    'wrong_platform': '页面不属于所选平台，请在同一平台重新搜索。',
    'credential_url': '链接含疑似凭据参数，未保存。请使用无登录凭据的稳定职位链接。',
    'dependency_install_failed': '浏览器组件安装失败。检查网络/磁盘权限后重试，原本地分析仍可使用。',
}

MESSAGES.update(HEALTH_MESSAGES)
MESSAGES.update({
    'local_proxy_configuration_invalid': '本机HTTP代理配置不符合要求。仅接受明确的 http://127.0.0.1:端口 或 http://[::1]:端口；本版本不接受账号、远程代理或SOCKS。',
    'local_proxy_connection_failed': '已选择本机代理，但代理连接或CONNECT隧道失败。程序没有改走直连；请核对实际HTTP代理端口及代理是否运行。',
    'tls_verification_failed': 'TLS证书验证失败，已停止。检查系统时间、证书与网络环境，不要关闭TLS校验。',
    'tls_handshake_failed': 'TLS握手失败，已停止；这不是缺少招聘账号或浏览器安装问题。',
})


class GuidedService:
    def __init__(self, workspace, *, registry: Registry | None = None, backend_factory=PlaywrightBackend,
                 ledger: RateLedger | None = None, health_probe=probe_browser, installer=run_command):
        self.workspace = workspace
        self.root = workspace.root / 'guided'
        self.root.mkdir(exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise InputError('向导目录不能使用符号链接。')
        self.registry, self.factory = registry or builtins(), backend_factory
        self.ledger = ledger or RateLedger(self.root / 'rates.sqlite')
        self._lock = threading.RLock()
        self._queue = queue.Queue(maxsize=1)
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._busy = False
        self._active = None
        self._stop_ident = None
        self._backends = {}
        self._thread = None
        self._last_install = 'not_started'
        self._health_probe, self._installer = health_probe, installer
        self._browser_health = environment_report()
        self._setup = {'stage': 'idle', 'message': '请先检查浏览器；DNS检查与浏览器检查是两回事。', 'steps': []}

    def _path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise InputError('无效任务编号。')
        p = self.root / f'{ident}.json'
        if p.is_symlink():
            raise InputError('任务文件不能使用符号链接。')
        return p

    def _load(self, ident):
        try:
            return json.loads(self._path(ident).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise InputError('任务不存在或文件损坏。') from exc

    def _save(self, state, code=None, **changes):
        with self._lock:
            if code:
                state['code'] = code
            state.update(changes)
            state['message'] = MESSAGES.get(state.get('code'), '请查看当前状态或回基础工作台处理数据。')
            state['updated_at'] = utc_now()
            with writer_lock(self.workspace.root):
                atomic_json(self._path(state['id']), state)

    def _spawn(self):
        if not self._thread:
            self._thread = threading.Thread(target=self._worker, name='radar-guided-browser', daemon=True)
            self._thread.start()

    def _submit(self, action, ident=None, secret=None):
        with self._lock:
            if self._busy:
                raise InputError('已有浏览器动作正在运行；请等待或暂停它。')
            self._busy, self._active = True, ident
            self._cancel.clear()
            self._spawn()
            self._queue.put_nowait((action, ident, secret))

    def state(self, data=None):
        with self._lock:
            jobs = []
            for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
                if path.is_symlink():
                    continue
                try:
                    item = self._load(path.stem)
                except InputError:
                    continue
                if item['status'] in {'queued', 'running'} and item['id'] != self._active:
                    item.update(status='interrupted', code='interrupted', message=MESSAGES['interrupted'])
                item['browser_open'] = item['id'] in self._backends
                item['automatic_resume_available'] = (item.get('auto_resume', False)
                                                      and item['browser_open']
                                                      and item['status'] == 'waiting_rate')
                jobs.append(item)
            return {'jobs': jobs, 'busy': self._busy, 'active': self._active,
                    'sites': self.registry.describe(), 'limits': asdict(self.ledger.limits),
                    'browser_package': self._package(), 'installation': self._last_install,
                    'browser_health': copy.deepcopy(self._browser_health), 'setup': copy.deepcopy(self._setup),
                    'python': sys.executable, 'roles': {k: v['label'] for k,v in self.workspace.config['roles'].items()},
                    'sessions_persisted': False, 'external_site_certification': False}

    @staticmethod
    def _package():
        try:
            return importlib.metadata.version('playwright')
        except importlib.metadata.PackageNotFoundError:
            return None

    def create(self, data):
        adapter = self.registry.get(data.get('platform'))
        keyword = text_field(data, 'keyword', required=True, limit=100).strip()
        roles = data.get('roles', ['time_series'])
        if not isinstance(roles, list) or not roles or any(not isinstance(r,str) or r not in self.workspace.config['roles'] for r in roles):
            raise InputError('请选择已配置的目标岗位。')
        max_pages, max_jobs = data.get('max_pages', 1), data.get('max_jobs', 5)
        if type(max_pages) is not int or not 1 <= max_pages <= 5 or type(max_jobs) is not int or not 1 <= max_jobs <= 20:
            raise InputError('首次建议1页/5个岗位；最多5页/20个岗位。')
        if data.get('consent') is not True:
            raise InputError('请确认本次正常访问与数据使用范围。')
        rights = text_field(data, 'rights_note', required=True, limit=2000)
        seed = text_field(data, 'list_url', limit=2048).strip()
        if seed:
            seed = adapter.accept_url(seed)
        state = {'id': uuid.uuid4().hex, 'schema_version': 1, 'platform': adapter.key,
                 'keyword': keyword, 'roles': roles, 'max_pages': max_pages, 'max_jobs': max_jobs,
                 'rights_note': rights, 'search_url': seed or adapter.search_url(keyword),
                 'status': 'queued', 'code': 'new', 'cards': [], 'pages_seen': [], 'report_id': '',
                 'phase': 'search', 'selection': [], 'created_at': utc_now(), 'updated_at': utc_now(),
                 'authentication': 'not_checked', 'certification': 'not_live_verified'}
        with self._lock:
            if self._busy:
                raise InputError('已有任务运行，请先暂停。')
            self._save(state)
            self._submit('search', state['id'])
        return {'id': state['id'], 'queued': True}

    def action(self, data):
        ident, action = data.get('id'), data.get('action')
        state = self._load(ident)
        if any(k in data for k in ('username','password','cookie','credential_consent')):
            raise InputError('本向导不接收账号密码；请在平台原生浏览器页面登录。')
        permitted = {'search', 'login', 'capture', 'more', 'collect', 'pause', 'stop', 'resume'}
        if action not in permitted:
            raise InputError('未知操作。')
        if action in {'pause','stop'}:
            with self._lock:
                if self._busy and self._active is None:
                    raise InputError('浏览器安装正在运行；此任务按钮不能取消安装。')
                if self._active and self._active != ident:
                    raise InputError('另一个任务正在运行。')
                self._cancel.set()
                if action == 'stop' and self._busy:
                    self._stop_ident = ident
                elif self._busy:
                    return {'id': ident, 'message': MESSAGES['paused']}
                else:
                    self._submit('close' if action == 'stop' else 'pause_idle', ident)
            return {'id': ident, 'message': MESSAGES['paused' if action=='pause' else 'stopped']}
        if action == 'resume' and state['status'] in {'completed', 'stopped'}:
            return {'id': ident, 'message': '该任务已结束；已有报告保留。重新打开搜索请使用搜索按钮。'}
        secret = None
        if action == 'collect':
            ids = data.get('selected')
            known = {r['id'] for r in state['cards']}
            if (not isinstance(ids,list) or not ids or len(ids)>state['max_jobs']
                    or any(not isinstance(i,str) or i not in known for i in ids) or len(set(ids))!=len(ids)):
                raise InputError('请选择列表中的岗位，不能超过本批数量上限。')
            state['selection'] = ids
            state['report_id'] = ''
            state['phase'] = 'collect'
        with self._lock:
            if self._busy:
                raise InputError('当前动作尚未结束，请先暂停或等待。')
            self._save(state, 'opening' if action in {'search','login'} else state['code'],
                       status='queued', auto_resume=False, next_allowed_at=None)
            self._submit(action, ident, secret)
        return {'id': ident, 'queued': True}

    def install(self, data):
        if data.get('consent') is not True:
            raise InputError('安装会用当前Python下载Playwright和Chromium；请先确认。')
        self._submit_setup('install')
        return {'queued': True}

    def _submit_setup(self, action):
        with self._lock:
            if self._busy:
                raise InputError('已有动作正在运行，请结束后再检查或安装。')
            if self._backends:
                raise InputError('请先点“停止并关闭登录会话”，再检查或安装。不会擅自关闭你的登录浏览器。')
            self._browser_health = environment_report()
            self._setup = {'stage': 'queued', 'message': '已排队准备浏览器组件检查。', 'steps': []}
            self._submit(action)

    def check_browser(self, data):
        if data:
            raise InputError('检查不接受网址、命令或浏览器路径参数。')
        self._submit_setup('check_browser')
        return {'queued': True, 'network_scope': 'blank local page only; no job requests'}

    def _check_browser(self):
        with self._lock:
            self._setup.update(stage='launch_check', message='正在实际打开并关闭空白采集浏览器，不访问招聘网站。')
        report = self._health_probe()
        with self._lock:
            self._browser_health = report
            self._setup.update(stage='ready' if report['ready'] else 'failed', message=report['message'])
        return report

    def _install_browser(self):
        self._last_install = 'installing'
        labels = {'package_install': '正在安装/检查 Playwright Python 包。',
                  'browser_download': '正在通过 Playwright 下载配套 Chromium，不是 pip install Chromium。'}
        for stage, command in install_commands():
            step = {'stage': stage, 'log': '', 'returncode': None}
            with self._lock:
                self._setup.update(stage=stage, message=labels[stage])
                self._setup['steps'].append(step)
            def progress(text):
                with self._lock:
                    step['log'] = safe_text(text, 12000)
            result = self._installer(command, cancel=self._shutdown, progress=progress)
            with self._lock:
                step.update(returncode=result.returncode, log=safe_text(result.output, 12000),
                            timed_out=result.timed_out, cancelled=result.cancelled)
            if result.returncode or result.timed_out or result.cancelled:
                code = 'dependency_install_timeout' if result.timed_out else 'dependency_install_failed'
                self._last_install = code
                with self._lock:
                    self._browser_health = failed_report({**environment_report(), 'stage': stage}, RuntimeError(result.output), code=code)
                    self._setup.update(stage='failed', message=HEALTH_MESSAGES[code])
                return
        # Zero exit codes are not proof of a usable browser. Verify the real
        # headed backend on the same owner thread before reporting installed.
        report = self._check_browser()
        self._last_install = 'installed' if report['ready'] else 'installed_not_ready'

    def diagnose(self, data):
        adapter = self.registry.get(data.get('platform'))
        return diagnose_host(urlsplit(adapter.search_url('test')).hostname)

    def export(self, data):
        state = self._load(data.get('id'))
        return {'id': state['id'], 'urls': [c.get('resolved_url') or c['url'] for c in state['cards']],
                'source': 'observed links; not a proof of complete market coverage'}

    def _backend(self, state):
        ident = state['id']
        previous = self._backends.get(ident)
        if previous and hasattr(previous, 'alive') and not previous.alive():
            self._backends.pop(ident).close()
        if ident not in self._backends:
            for old in list(self._backends):
                self._backends.pop(old).close()
            def progress(code, seconds):
                self._save(state, code, wait_seconds=seconds)
            self._backends[ident] = self.factory(self.registry.get(state['platform']), self.ledger, self._cancel, progress)
            health = getattr(self._backends[ident], 'startup_report', None)
            if health:
                with self._lock:
                    self._browser_health = dict(health)
        return self._backends[ident]

    def _gather(self, state, backend, adapter, *, navigate=False, more=False):
        if navigate:
            backend.open(state['search_url'])
        if hasattr(backend, 'collection_mode'):
            backend.collection_mode()
        if more and len(state['pages_seen']) >= state['max_pages']:
            raise CrawlError('list_page_limit')
        if more and not backend.next_page():
            self._save(state, 'ready' if state['cards'] else 'empty_list', status='ready',
                       list_end='no_next_button')
            return
        pages = len(state['pages_seen'])
        while pages < state['max_pages']:
            if self._cancel.is_set():
                raise CrawlError('paused')
            page = backend.snapshot()
            if hasattr(backend, 'wire'):
                backend.wire.ensure_robots(page.url)
            cards = adapter.cards(page)
            signature = hashlib.sha256('\n'.join(c.id for c in cards).encode()).hexdigest()
            if signature in state['pages_seen']:
                break
            if cards:
                state['pages_seen'].append(signature)
            pages += 1
            existing = {r['id'] for r in state['cards']}
            for card in cards:
                if card.id not in existing and len(state['cards']) < 100:
                    state['cards'].append({**asdict(card), 'status': 'discovered', 'record_id': '', 'resolved_url': ''})
                    existing.add(card.id)
            self._save(state, 'reading', status='running', last_list_url=page.url)
            if not cards or pages >= state['max_pages'] or not backend.next_page():
                break
        self._save(state, 'ready' if state['cards'] else 'empty_list', status='ready' if state['cards'] else 'waiting_manual', phase='select')

    def _collect(self, state, backend, adapter):
        self._save(state, 'collecting', status='running', phase='collect')
        selected = set(state['selection'])
        try:
            for row in state['cards']:
                if row['id'] not in selected or row['status'] == 'ok':
                    continue
                if self._cancel.is_set():
                    raise CrawlError('paused')
                row['status'] = 'opening'
                self._save(state)
                try:
                    page = backend.open(row['url'])
                    final_url = adapter.accept_url(page.url, detail=True)
                    parsed = adapter.detail(page)
                    record = JobRecord(**parsed, url=final_url, platform=adapter.key,
                        source_mode='browser_fetch', rights_note=state['rights_note'],
                        source_ref=f"guided:{state['id']}:{row['id']}",
                        raw_sha256=hashlib.sha256(page.html.encode()).hexdigest())
                    with writer_lock(self.workspace.root), Store(self.workspace.db) as store:
                        store.add(record)
                    row.update(status='ok', record_id=record.record_id, resolved_url=final_url, title=record.title)
                except CrawlError as exc:
                    row['status'] = exc.code
                    self._save(state)
                    if exc.code not in {'structure_changed','not_job_url'}:
                        raise
                self._save(state)
        finally:
            # Keep a usable batch report even if a later selected job blocks.
            self._finalize_report(state, adapter)
        self._save(state, 'completed', status='completed', phase='report')

    def _finalize_report(self, state, adapter):
        selected = set(state['selection'])
        if any(c['status']=='ok' and c['id'] in selected for c in state['cards']):
            from ..pipeline import analyze
            with writer_lock(self.workspace.root):
                report_id = uuid.uuid4().hex
                selected_records = {c['record_id'] for c in state['cards'] if c['id'] in selected and c['status']=='ok'}
                with Store(self.workspace.db) as store:
                    records = [r for r in store.records(latest_only=False) if r.record_id in selected_records]
                with tempfile.TemporaryDirectory(prefix='.batch-',dir=self.root) as tmp:
                    batch_db = Path(tmp)/'batch.sqlite'
                    with Store(batch_db) as batch:
                        for record in records:
                            batch.add(record)
                    # A plugin need not edit the global platform catalogue. Preserve
                    # its metadata in this report's own effective configuration.
                    config = copy.deepcopy(self.workspace.config)
                    config['platforms'].setdefault(adapter.key,
                        {'label': adapter.label, 'domains': list(adapter.domains)})
                    analyze(batch_db, self.workspace.root/'reports'/report_id,
                            config=config, role_filter=state['roles'], platform_filter=[adapter.key])
            self._save(state, report_id=report_id,
                       report_scope='exact successful selected records in this batch')

    def _run(self, action, state, secret):
        if action == 'close':
            backend = self._backends.pop(state['id'],None)
            if backend: backend.close()
            self._save(state,'stopped',status='stopped'); return
        if action == 'pause_idle':
            self._cancel.set()
            self._save(state,'paused',status='paused'); return
        adapter = self.registry.get(state['platform'])
        if action == 'login':
            try:
                self.ledger.reserve(adapter.key, 'login')
            except RateLimit as exc:
                self._save(state, wait_seconds=round(exc.wait, 1))
                raise CrawlError('login_rate_limited') from exc
        backend = self._backend(state)
        self._save(state, 'opening', status='running')
        if action == 'login':
            backend.open(adapter.login_url, authentication=True)
            self._save(state, 'manual_browser_open', status='waiting_manual', authentication='manual_pending')
        elif action in {'capture','more','search'}:
            self._gather(state,backend,adapter,navigate=action=='search',more=action=='more')
        elif action in {'collect','resume'}:
            if state['phase'] == 'collect':
                self._collect(state,backend,adapter)
            else:
                self._gather(state,backend,adapter,navigate=action=='resume')

    def _resume_due(self):
        """Resume only safe read actions in a still-owned browser session.

        Restarted processes preserve the due time and selection, but require one
        user resume/login because browser credentials intentionally aren't saved.
        Never replay login POSTs or repeat a pagination click automatically.
        """
        with self._lock:
            if self._busy or self._shutdown.is_set():
                return
            for ident in list(self._backends):
                try:
                    state = self._load(ident)
                    due = state.get('next_allowed_at')
                    action = state.get('retry_action')
                    if (state['status'] == 'waiting_rate' and state.get('auto_resume')
                            and action in {'search', 'capture', 'collect', 'resume'}
                            and isinstance(due, (int, float)) and math.isfinite(due)
                            and due <= self.ledger.clock()):
                        self._save(state, status='queued', auto_resume=False, next_allowed_at=None)
                        self._submit(action, ident)
                        return
                except (InputError, OSError, ValueError):
                    continue

    def _worker(self):
        while not self._shutdown.is_set():
            try:
                action, ident, secret = self._queue.get(timeout=0.1)
            except queue.Empty:
                self._resume_due()
                for backend in list(self._backends.values()):
                    try: backend.pump()
                    except Exception: pass
                continue
            try:
                if action == 'install':
                    self._install_browser()
                    continue
                if action == 'check_browser':
                    self._check_browser()
                    continue
                state = self._load(ident)
                self._run(action,state,secret)
                if state['status'] in {'completed','stopped','paused','ready'}:
                    self._cancel.set()
            except Exception as exc:
                code = exc.code if isinstance(exc,CrawlError) else 'operation_error'
                if action in {'install', 'check_browser'}:
                    code = 'dependency_install_failed' if action == 'install' else 'browser_check_failed'
                    if action == 'install':
                        self._last_install = code
                    with self._lock:
                        self._browser_health = failed_report(environment_report(), exc, code=code)
                        self._setup.update(stage='failed', message=self._browser_health['message'])
                else:
                    try:
                        current = self._load(ident)
                        stopping = self._stop_ident == ident
                        if stopping:
                            backend = self._backends.pop(ident,None)
                            if backend: backend.close()
                            self._save(state,'stopped',status='stopped')
                        else:
                            if isinstance(exc, BrowserStartupError):
                                self._browser_health = exc.report
                                state['startup_diagnostic'] = exc.report
                            if isinstance(exc, RateLimit) and exc.next_allowed_at is not None:
                                backend = self._backends.get(ident)
                                safe = (action in {'search', 'capture', 'collect', 'resume'}
                                        and not getattr(backend, 'auth_mode', False)
                                        and code != 'clock_rollback')
                                self._save(state, code, status='waiting_rate',
                                           wait_seconds=round(exc.wait, 1),
                                           next_allowed_at=exc.next_allowed_at,
                                           retry_action=action, auto_resume=safe)
                                self._cancel.set()  # No background browser requests during deferral.
                            else:
                                self._save(state,code,status='paused' if code=='paused' else 'waiting_manual',
                                           auto_resume=False, next_allowed_at=None)
                    except Exception:
                        pass
            finally:
                secret = None
                if ident and self._stop_ident == ident:
                    self._stop_ident = None
                    backend = self._backends.pop(ident, None)
                    if backend:
                        backend.close()
                    self._save(self._load(ident), 'stopped', status='stopped')
                with self._lock:
                    self._busy, self._active = False, None
                self._queue.task_done()
        for backend in list(self._backends.values()):
            try: backend.close()
            except Exception: pass
        self._backends.clear()

    def close(self):
        self._cancel.set(); self._shutdown.set()
        if self._thread:
            self._thread.join(timeout=5)
