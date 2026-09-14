"""Real browser resume regressions; supplier responses and accounts are artificial."""
from __future__ import annotations
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.workbench import LocalServer


def main():
    from playwright.sync_api import sync_playwright, expect
    output = ROOT/'browser-acceptance'/'resume-review'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'checks': [], 'page_errors': [], 'external_browser_requests': [],
              'scope': 'Artificial provider responses, real application/browser; not live certification.'}
    with tempfile.TemporaryDirectory() as tmp:
        server = LocalServer(Workspace(tmp))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                def local_only(route):
                    if route.request.url.startswith(server.origin+'/'):
                        route.continue_()
                    else:
                        result['external_browser_requests'].append(route.request.url)
                        route.abort()
                context.route('**/*', local_only)
                page = context.new_page()
                page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
                try:
                    page.goto(server.entry_url)
                    expect(page.locator('#counts')).to_contain_text('真实记录 0')
                    page.get_by_role('link', name='进入自动采集 / 原文复核 / 附件与量化指标工作台').click()
                    expect(page.locator('#revision')).to_contain_text('版本 0')
                    form = page.locator('#collect-form')
                    base = {'roles': ['time_series'], 'platforms': ['boss'], 'consent': True,
                            'rights_note': 'Artificial browser regression, not market data'}
                    feed = server.collector.start({**base, 'mode': 'feed', 'feed_budget': 1,
                        'endpoint': 'https://www.liepin.com/fixture-feed',
                        'contract_ref': 'https://www.liepin.com/fixture-contract'})
                    page.get_by_role('button', name='我有职位链接：套用 URL 入门参数').click()
                    page.locator('#collect-refresh').click()
                    expect(page.locator('#collect-resume')).to_be_enabled()
                    page.locator('#collect-history').select_option(feed['id'])
                    with patch('vibe_job_radar.collection.SafeHTTP') as source:
                        source.return_value.json.return_value = {'jobs': [], 'next_cursor': ''}
                        page.locator('#collect-resume').click()
                        expect(page.locator('#notice')).to_contain_text('已切换到数据源任务')
                        assert source.return_value.json.call_count == 0
                        assert server.collector.status({'id': feed['id']})['feed_requests'] == 0
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.locator('#collect-resume').click()
                        expect(page.locator('#notice')).to_contain_text('已取消继续')
                        assert source.return_value.json.call_count == 0
                        form.locator('[name=api_key]').fill('ARTIFICIAL-FEED-TOKEN')
                        page.locator('#collect-resume').click()
                        expect(page.locator('#collect-progress')).to_contain_text('empty', timeout=30000)
                        expect(page.locator('#collect-resume')).to_be_enabled()
                        assert source.return_value.json.call_count == 1
                        assert source.return_value.json.call_args.kwargs['headers'] == {'Authorization': 'Bearer ARTIFICIAL-FEED-TOKEN'}
                    result['checks'].append('cross-mode feed resume pauses; cancel uses zero budget; entered feed token is preserved')
                    for phase in ('detail', 'report'):
                        task = server.collector.start({**base, 'mode': 'search', 'search_storage_rights': True,
                            'search_budget': 1, 'detail_budget': 0})
                        saved = server.collector._load(task['id']); saved['phase'] = phase
                        for query in saved['tasks']:
                            query['status'] = 'provider_stopped'
                        server.collector._save(saved)
                        page.locator('#collect-refresh').click()
                        expect(page.locator('#collect-resume')).to_be_enabled()
                        page.locator('#collect-history').select_option(task['id'])
                        with patch('vibe_job_radar.collection.SafeHTTP') as no_search:
                            page.locator('#collect-resume').click()
                            expect(page.locator('#collect-progress')).to_contain_text('empty', timeout=30000)
                            expect(page.locator('#collect-resume')).to_be_enabled()
                            assert no_search.return_value.json.call_count == 0
                        result['checks'].append(f'search-origin {phase} phase completes without Brave Key or search request')
                    assert not result['page_errors'] and not result['external_browser_requests']
                    result['success'] = True
                finally:
                    page.screenshot(path=str(output/'resume-state.png'), full_page=True)
                    browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            (output/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
