"""Two real browsers: native popup login -> original search -> interrupted details.

Supplier HTTP is a fixed artificial fixture. No platform accounts, live job
pages or user credentials are used; production network policy is not altered.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.workbench import LocalServer
from vibe_job_radar.guided.adapters import Registry, builtins
from vibe_job_radar.guided.browser import PlaywrightBackend
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.rate import Limits, RateLedger
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.guided.transport import WireResponse

MANUAL_LOGIN = threading.Event()
CALLS = []
SEARCH_QUERIES = []
LOGIN_COUNT = 0
BACKENDS = []


class NativeWire:
    """Fixtures only; no code path dispatches a live upstream request."""
    def __init__(self, adapter, ledger, cancelled, progress):
        self.ledger, self.cancelled, self.blocked = ledger, cancelled, set()
    def allowed_resource(self, url): return url.startswith('https://jobs.fixture.test/')
    def reserve(self, kind):
        if self.cancelled.is_set(): raise CrawlError('paused')
        self.ledger.reserve('liepin', kind)
    def ensure_robots(self, url): pass  # Denials are independently covered in production transport tests.
    def fetch(self, url, method='GET', headers=None, body=None, *, required=True):
        global LOGIN_COUNT
        self.reserve('request')
        p=urlsplit(url); CALLS.append((method,p.path))
        logged='native_fixture=yes' in (headers or {}).get('cookie','')
        response_headers={'content-type':'text/html; charset=utf-8'}
        if p.path=='/native-login' and method=='POST':
            LOGIN_COUNT += 1
            return WireResponse(200,response_headers,
                b'<h1>Artificial login complete</h1><script>window.close()</script>',
                ('native_fixture=yes; Path=/; Secure; HttpOnly',))
        if p.path=='/native-login':
            markup='<h1>人工登录窗口</h1><form method="post" action="/native-login"><button type="submit">人工确认登录</button></form>'
        elif p.path=='/':
            markup='<h1>人工平台首页</h1><a id="native-open" href="/native-login" target="_blank">打开登录窗口</a><aside><a href="/job/900.shtml">不属于原搜索的推荐岗位</a></aside>'
        elif p.path=='/zhaopin/':
            SEARCH_QUERIES.append(parse_qs(p.query).get('key',[''])[0])
            markup=('<h1>登录后查看职位</h1>' if not logged else '<h1>原搜索结果</h1><a href="/job/1.shtml">时间序列算法工程师一</a><a href="/job/2.shtml">时间序列算法工程师二</a>')
        elif p.path.startswith('/job/'):
            if not logged or (p.path=='/job/2.shtml' and LOGIN_COUNT < 2):
                markup='<h1>登录后查看职位</h1>'
            else:
                markup='<h1>时间序列算法工程师</h1><div class="job-description">使用 Cursor 进行 AI 辅助编程，编写单元测试和代码审查；负责时间序列预测。人工测试正文。</div><aside><a href="/job/900.shtml">猜你喜欢</a></aside>'
        else:
            raise CrawlError('unknown_fixture_route')
        return WireResponse(200,response_headers,markup.encode())


class HumanClickBackend(PlaywrightBackend):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        BACKENDS.append(self)
    def pump(self):
        if MANUAL_LOGIN.is_set():
            MANUAL_LOGIN.clear()
            # A developer fixture simulates the human's two clicks on the owner
            # thread. This is not a production login automation capability.
            with self.page.expect_popup() as popup:
                self.page.locator('#native-open').click()
            popup.value.locator('button[type="submit"]').click()
        super().pump()


def main():
    from playwright.sync_api import expect, sync_playwright
    out=ROOT/'browser-acceptance'/'native-login'; out.mkdir(parents=True,exist_ok=True)
    result={'success':False,'checks':[],'page_errors':[],
            'scope':'Real UI and collection browsers; artificial supplier/login/JDs only. No live site or VPN certification.'}
    executable=os.environ.get('RADAR_TEST_CHROMIUM')
    options={'headless':True}
    if executable: options['executable_path']=executable
    try:
        with tempfile.TemporaryDirectory(prefix='native-login-') as tmp:
            workspace=Workspace(tmp)
            # Preserve built-in Liepin URL patterns, but no requests to Liepin.
            adapter=dataclasses.replace(builtins().get('liepin'),
                domains=('jobs.fixture.test',), resource_domains=(),
                search_base='https://jobs.fixture.test/zhaopin/',
                login_url='https://jobs.fixture.test/', login_hosts=('jobs.fixture.test',))
            server=LocalServer(workspace); server.guided.close()
            ledger=RateLedger(Path(tmp)/'guided'/'rates.sqlite',Limits(page_interval=0,request_interval=0,login_interval=0))
            server.guided=GuidedService(workspace,registry=Registry([adapter]),ledger=ledger,
                backend_factory=lambda a,l,c,p:HumanClickBackend(a,l,c,p,headless=True,
                    executable_path=executable,transport_factory=NativeWire))
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(**options)
                    try:
                        context=browser.new_context(viewport={'width':1280,'height':960})
                        context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(server.origin+'/') else r.abort())
                        page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                        page.goto(server.entry_url)
                        page.locator('a[href="/guided"]').click()
                        form=page.locator('#search-form')
                        expect(page.locator('#site')).to_have_value('liepin')
                        form.locator('[name=keyword]').fill('时间序列 & 原条件')
                        form.locator('[name=rights_note]').fill('人工测试范围，不是实站授权')
                        form.locator('[name=consent]').check()
                        page.locator('#find').click()
                        expect(page.locator('#task-status')).to_contain_text('需要你操作',timeout=30000)
                        original=server.guided.state()['jobs'][0]
                        result['checks'].append('search login wall preserves original query and does not create fake results')

                        def login():
                            page.locator('#login').click()
                            expect(page.locator('#task-status')).to_contain_text('已打开平台登录页面',timeout=30000)
                            expect(page.locator('#resume')).to_have_text('登录完成，继续原任务')
                            expected=LOGIN_COUNT+1; MANUAL_LOGIN.set()
                            end=time.monotonic()+20
                            while LOGIN_COUNT < expected and time.monotonic()<end: page.wait_for_timeout(100)
                            assert LOGIN_COUNT == expected
                            # The native popup closes itself; event handling runs on
                            # the collection thread, not via the UI/browser context.
                            page.wait_for_timeout(1000)
                            assert len(BACKENDS)==1, 'must keep original context and its in-memory cookies'

                        login()
                        page.locator('#capture').click()
                        expect(page.locator('#task-status')).to_contain_text('不是本次搜索列表',timeout=30000)
                        expect(page.locator('#cards .card')).to_have_count(0)
                        result['checks'].append('logged-in homepage recommendations not mistaken for target search')
                        page.locator('#resume').click()
                        expect(page.locator('#cards .card')).to_have_count(2,timeout=30000)
                        assert SEARCH_QUERIES == ['时间序列 & 原条件']*2
                        assert len(BACKENDS)==1
                        result['checks'].append('closed login popup returns to same context; one click resumes exact original search')
                        page.locator('#select-all').click();page.locator('#collect').click()
                        expect(page.locator('#task-status')).to_contain_text('需要你操作',timeout=30000)
                        partial=server.guided.state()['jobs'][0]
                        assert partial['cards'][0]['status']=='ok' and partial['report_id']
                        first_report=partial['report_id']; first_record=partial['cards'][0]['record_id']
                        assert len(partial['selection'])==2
                        login()
                        page.locator('#resume').click()
                        expect(page.locator('#task-status')).to_contain_text('本批次已结束',timeout=30000)
                        finished=server.guided.state()['jobs'][0]
                        assert all(c['status']=='ok' for c in finished['cards'])
                        assert finished['cards'][0]['record_id']==first_record
                        assert finished['id']==original['id'] and finished['search_url']==original['search_url']
                        assert finished['roles']==original['roles'] and finished['rights_note']==original['rights_note']
                        assert CALLS.count(('GET','/job/1.shtml'))==1 and CALLS.count(('GET','/job/2.shtml'))==2
                        assert CALLS.count(('POST','/native-login'))==2
                        assert workspace.report_file(first_report,'run_manifest.json').is_file()
                        assert workspace.report(finished['report_id'])['manifest']['stats']['full_text_job_groups']==1  # identical artificial JDs deduplicate
                        result['checks'].append('login during details resumes only failed selection, preserves first body and partial report')
                        page.locator('a[href="/#report='+finished['report_id']+'"]').click()
                        expect(page.locator('#brief-conclusion')).to_contain_text('已形成本批')
                        result['checks'].append('successful selected bodies feed real mainline research report')
                        page.screenshot(path=str(out/'research-after-native-login.png'),full_page=True)
                        assert not result['page_errors']
                        result['success']=True
                    finally: browser.close()
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
    finally:
        (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
