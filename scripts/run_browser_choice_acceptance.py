"""Windows Edge: real headed collector, local UI and restart persistence.

The initial bundled crash is a fixture. Edge is actually launched, not installed
by this script. No real account, profile, site navigation or automated fallback.
"""
from __future__ import annotations
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vibe_job_radar.guided.browser_health import environment_report, failed_report, probe_browser
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.workbench import LocalServer


def main():
    from playwright.sync_api import sync_playwright, expect
    out=ROOT/'browser-acceptance'/'browser-choice';out.mkdir(parents=True,exist_ok=True)
    result={'success':False,'checks':[],'page_errors':[], 'external_ui_requests':[],
            'scope':'Real installed Edge headed collector and local UI; bundled native exception is artificial. Not user-PC crash reproduction or site certification.'}
    fixture=failed_report({**environment_report(),'stage':'launch'},RuntimeError(
        '<launched> pid=123\n[pid=123] <process did exit: exitCode=3221226356, signal=null>'))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Workspace(tmp);server=LocalServer(workspace)
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
            def probe(**kw):
                return probe_browser(**kw) if kw.get('channel')=='msedge' else fixture
            server.guided._health_probe=probe
            server.guided._installer=lambda *a,**k:(_ for _ in ()).throw(AssertionError('unexpected install'))
            try:
                with sync_playwright() as pw:
                    # The workbench UI browser is separate from the headed collector.
                    browser=pw.chromium.launch(channel='msedge',headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1280,'height':960})
                        def route(r):
                            if r.request.url.startswith(server.origin+'/'):r.continue_()
                            else:result['external_ui_requests'].append(r.request.url);r.abort()
                        context.route('**/*',route)
                        page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                        page.on('dialog',lambda d:d.accept())
                        page.goto(server.entry_url);page.locator('a[href="/guided"]').click()
                        expect(page.locator('#environment')).to_contain_text('不表示缺少组件')
                        page.locator('#check-browser').click()
                        expect(page.locator('#browser-summary')).to_contain_text('停止循环重装')
                        expect(page.locator('#browser-history')).to_contain_text('0xC0000374')
                        result['checks'].append('operation log is not installation detection; failed check records minimal history')
                        page.locator('#browser-alternative summary').click()
                        page.locator('#browser-choice').select_option('msedge')
                        page.locator('#use-browser-choice').click()
                        expect(page.locator('#browser-selected')).to_contain_text('当前采集浏览器：本机 Microsoft Edge',timeout=45000)
                        expect(page.locator('#browser-summary')).to_contain_text('已通过空白页启动检查')
                        health=server.guided.state()['browser_health']
                        assert health['selection_applied'] and health['mode']=='headed'
                        assert health['browser_channel']=='msedge' and health['launch_tested']
                        result['edge_version']=health['browser_version']
                        result['playwright_version']=health['playwright_version']
                        assert server.guided.state()['jobs']==[]
                        assert server.guided.ledger.summary('liepin')['request']['day']==0
                        result['checks'].append('explicit Edge choice runs the real headed collector on a blank page before persisting; no install, jobs or source quota')
                        page.screenshot(path=str(out/'edge-ready.png'),full_page=True)
                        # Service restart exercises the persisted product state, not a browser reload.
                        server.guided.close()
                        from vibe_job_radar.guided.service import GuidedService
                        server.guided=GuidedService(workspace)
                        page.reload()
                        expect(page.locator('#browser-selected')).to_contain_text('本机 Microsoft Edge')
                        expect(page.locator('#browser-choice')).to_have_value('msedge')
                        expect(page.locator('#browser-summary')).to_contain_text('尚未验证')
                        expect(page.locator('#browser-history')).to_contain_text('历史，不代表本次就绪')
                        assert not server.guided.state()['browser_health']['ready']
                        result['checks'].append('restart keeps the selected channel and history but never calls a historical green check current readiness')
                        page.locator('#check-browser').click()
                        expect(page.locator('#browser-summary')).to_contain_text('已通过空白页启动检查',timeout=45000)
                        assert server.guided.state()['browser_health']['browser_channel']=='msedge'
                        page.set_viewport_size({'width':390,'height':844})
                        assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                        page.screenshot(path=str(out/'edge-mobile.png'),full_page=True)
                        assert not result['page_errors'] and not result['external_ui_requests']
                        assert not workspace.db.exists()
                        result['checks'].append('regular recheck uses the saved channel, no source requests or JS errors; narrow layout fits')
                        result['success']=True
                    finally:browser.close()
            finally:server.shutdown();server.server_close();thread.join(timeout=5)
    finally:(out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
