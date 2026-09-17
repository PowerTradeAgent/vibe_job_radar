"""Actual local Edge UI, simulated fixed package install; no source/site access."""
from __future__ import annotations
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from vibe_job_radar import tls_context
from vibe_job_radar.guided.browser_install import CommandResult
from vibe_job_radar.guided.browser_choice import BrowserChoice
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.utils import utc_now
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.workbench import LocalServer


def main():
    if sys.platform!='win32':raise RuntimeError('Actual Windows required')
    from playwright.sync_api import sync_playwright,expect
    out=ROOT/'native-tls-evidence';out.mkdir(exist_ok=True)
    result={'success':False,'checks':[],'page_errors':[],'external_ui_requests':[],
            'scope':'Real local UI and service restart; installation and prior browser-ready record are artificial. No root/browser install, job or remote diagnosis.'}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Workspace(tmp)
            # Explicit test history, not an actual collector launch claim.
            BrowserChoice(workspace.root).record({'checked_at':utc_now(),'ready':True,'code':'browser_ready','playwright_version':'1.63.0'},'msedge',select=True)
            server=LocalServer(workspace)
            worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);worker.start()
            installer=Mock(return_value=CommandResult(0,'fixture install result'))
            server.guided._installer=installer
            dialogs=[];allow=[False]
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1280,'height':960})
                        def route(r):
                            if r.request.url.startswith(server.origin+'/'):r.continue_()
                            else:result['external_ui_requests'].append(r.request.url);r.abort()
                        context.route('**/*',route)
                        page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                        def dialog(d):
                            dialogs.append(d.message)
                            d.accept() if allow[0] else d.dismiss()
                        page.on('dialog',dialog)
                        with patch.object(tls_context,'_version',return_value=None):
                            response=page.goto(server.entry_url)
                            assert response and "'unsafe-eval'" not in response.headers.get('content-security-policy','')
                            page.locator('a[href="/guided"]').click()
                            expect(page.locator('#tls-repair')).to_be_visible()
                            page.locator('#tls-repair summary').click()
                            expect(page.locator('#tls-environment')).to_contain_text('native_component_missing')
                            expect(page.locator('#browser-selected')).to_contain_text('本机 Microsoft Edge')
                            page.locator('#repair-tls').click()
                            installer.assert_not_called()
                            result['checks'].append('component repair is visible on Windows, states missing add-on rather than missing browser; dismissed consent does nothing')
                            allow[0]=True;page.locator('#repair-tls').click()
                            expect(page.locator('#browser-summary')).to_contain_text('安装成功不代表 TLS 已通过',timeout=15000)
                            expect(page.locator('#repair-tls')).to_be_disabled()
                            installer.assert_called_once()
                            assert installer.call_args.args[0]==[sys.executable,'-m','pip','install',tls_context.TRUSTSTORE_REQUIREMENT]
                            assert '证书服务请求' in dialogs[-1]
                            page.locator('#network').click()
                            expect(page.locator('#diagnostic')).to_contain_text('tls_component_restart_required')
                            assert server.guided._selected_browser=='msedge'
                            result['checks'].append('confirmed action calls only fixed truststore install; no browser/root changes; network diagnosis stops until restart')
                            page.screenshot(path=str(out/'tls-repair-restart.png'),full_page=True)
                        server.guided.close();server.guided=GuidedService(workspace)
                        page.reload()
                        expect(page.locator('#tls-environment')).to_contain_text('windows_cryptoapi')
                        page.locator('#tls-repair summary').click()
                        expect(page.locator('#repair-tls')).to_be_disabled()
                        expect(page.locator('#browser-selected')).to_contain_text('本机 Microsoft Edge')
                        assert not server.guided.state()['tls_environment']['tls_tested']
                        assert not server.guided.state()['tls_environment']['restart_required']
                        assert server.guided.ledger.summary('boss')['request']['day']==0
                        assert not workspace.db.exists()
                        page.set_viewport_size({'width':390,'height':844})
                        assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                        page.screenshot(path=str(out/'tls-native-mobile.png'),full_page=True)
                        result['checks'].append('restart detects actual installed native component and preserves saved Edge; no false TLS/site success, quota use or layout overflow')
                        assert not result['page_errors'] and not result['external_ui_requests']
                        result['success']=True
                    finally:browser.close()
            finally:server.shutdown();server.server_close();worker.join(5)
    finally:(out/'ui-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
