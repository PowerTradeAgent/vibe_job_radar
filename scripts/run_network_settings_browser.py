"""Real Chromium local consent UI; no upstream resolution or source request."""
from __future__ import annotations
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from playwright.sync_api import sync_playwright
from vibe_job_radar.workbench import LocalServer
from vibe_job_radar.workspace import Workspace


def main():
    output=ROOT/'browser-acceptance';output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        workspace=Workspace(folder);server=LocalServer(workspace,public_client=None)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);thread.start()
        try:
            with sync_playwright() as runtime:
                browser=runtime.chromium.launch(headless=True)
                page=browser.new_page();external=[]
                def permitted(route):
                    if not route.request.url.startswith(server.origin+'/'):
                        external.append(route.request.url);route.abort()
                    else:route.continue_()
                page.route('**/*',permitted)
                page.goto(server.entry_url)
                page.locator('#network-preferences summary').click()
                page.wait_for_function("document.querySelector('#save-network-preferences').disabled === false")
                assert not page.locator('#encrypted-dns-consent').is_checked()
                page.locator('#encrypted-dns-consent').check()
                with page.expect_response(lambda r:'/api/network/preferences' in r.url) as saved:
                    page.locator('#save-network-preferences').click()
                assert saved.value.status==200
                page.wait_for_function("document.querySelector('#network-preferences [role=status]').textContent.includes('已保存')")
                assert workspace.network_policy().encrypted_dns
                for path in ('/guided','/advanced'):
                    page.goto(server.origin+path)
                    page.locator('#network-preferences summary').click()
                    page.wait_for_function("document.querySelector('#encrypted-dns-consent').checked === true")
                page.locator('#encrypted-dns-consent').uncheck()
                with page.expect_response(lambda r:'/api/network/preferences' in r.url) as revoked:
                    page.locator('#save-network-preferences').click()
                assert revoked.value.status==200
                assert not workspace.network_policy().encrypted_dns
                page.reload()
                page.wait_for_function("document.querySelector('#save-network-preferences').disabled === false")
                assert not page.locator('#encrypted-dns-consent').is_checked()
                assert not external
                page.screenshot(path=str(output/'network-consent.png'),full_page=True)
                browser.close()
            value={'success':True,'real_chromium':True,'external_requests':0,
                   'checks':['default_off','explicit_consent','cross_page_persistence','revoke','reload'],
                   'scope':'Local UI only; upstream resolver and VPN/TUN are separate tests.'}
            (output/'network-consent.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
    return 0

if __name__=='__main__':raise SystemExit(main())
