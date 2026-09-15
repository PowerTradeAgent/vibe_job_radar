"""Actual browser verifies existing report and improved failure UI; fixture upstream.

Real public GET is separately exercised by check_live_public_example.py --live.
The main HTTP server is unchanged: no new password or remote-target endpoints.
"""
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vibe_job_radar.workbench import LocalServer
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.public_example import JOB_ID, SOURCE_URL
from vibe_job_radar.network import SafeHTTP


def main():
    from playwright.sync_api import sync_playwright, expect
    out=ROOT/'browser-acceptance'/'recovery';out.mkdir(parents=True,exist_ok=True)
    result={'success':False,'checks':[],'page_errors':[],
            'scope':'Artificial upstream for UI/command only; actual public GET acceptance is separate.'}
    payload={'id':JOB_ID,'absolute_url':SOURCE_URL,'title':'Technical Architect',
             'content':'<p>ARTIFICIAL UI FIXTURE — NOT MARKET DATA.</p><p>Use Claude Code for AI-assisted coding and review generated code. Build dependable systems with comprehensive tests.</p>',
             'location':{'name':'TEST ONLY'}}
    with tempfile.TemporaryDirectory() as tmp:
        command=runpy.run_path(str(ROOT/'scripts/run_real_example.py'))['main']
        with patch.object(SafeHTTP,'json',return_value=payload) as source,contextlib.redirect_stdout(io.StringIO()):
            assert command(['--yes','--no-browser','--workspace',tmp])==0
            assert command(['--yes','--no-browser','--workspace',tmp])==0
            assert source.call_count==1
        result['checks'].append('user command saves one full record/report; repeat command visibly caches without extra upstream GET')
        server=LocalServer(Workspace(tmp))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        try:
            task=server.collector.start({'mode':'urls','platforms':['boss'],'permit_platforms':['boss'],
                'roles':['architect'],'urls':'https://www.zhipin.com/job_detail/test-fixture-a.html\nhttps://www.zhipin.com/job_detail/test-fixture-b.html',
                'detail_budget':1,'rights_note':'UI人工测试','consent':True})
            task['details'][0]['status']='redirect_not_followed';task['details'][1]['status']='budget_skipped'
            task.update(status='needs_attention',phase='report',detail_attempts=1)
            server.collector._save(task)
            with sync_playwright() as pw:
                options={'headless':True}
                if os.environ.get('RADAR_TEST_CHROMIUM'):options['executable_path']=os.environ['RADAR_TEST_CHROMIUM']
                browser=pw.chromium.launch(**options)
                context=browser.new_context(viewport={'width':1360,'height':1000},accept_downloads=True)
                context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(server.origin+'/') else r.abort())
                page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                page.goto(server.entry_url)
                expect(page.locator('#counts')).to_contain_text('真实记录 1')
                page.locator('#runs button').first.click()
                expect(page.locator('#requirements')).to_contain_text('Claude Code')
                with page.expect_download() as pending:
                    page.get_by_role('button',name='requirements_zh.csv',exact=True).click()
                pending.value.save_as(out/'fixture-requirements.csv')
                assert 'Claude Code' in (out/'fixture-requirements.csv').read_text(encoding='utf-8-sig')
                result['checks'].append('saved public-case report is available through unchanged local UI and authenticated CSV download')
                page.screenshot(path=str(out/'example-report.png'),full_page=True)
                page.goto(server.origin+'/advanced')
                expect(page.locator('#collect-history')).to_contain_text('needs_attention')
                page.locator('#collect-load').click()
                expect(page.locator('#collect-result')).to_contain_text('未执行：本批正文尝试预算已用完')
                expect(page.locator('#collect-result')).to_contain_text('历史日志不能判断是否需要登录')
                expect(page.locator('#collect-progress')).to_contain_text('无浏览器登录会话')
                result['checks'].append('historical unknown redirect is distinguished from an unexecuted budget item')
                page.screenshot(path=str(out/'redirect-guidance.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                assert not result['page_errors']
                result['checks'].append('no page-level overflow or JavaScript errors')
                result['success']=True;browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
            (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
