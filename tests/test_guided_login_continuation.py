"""Mainline login/list regressions with artificial pages, never live credentials."""
from __future__ import annotations

import dataclasses
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_guided import FakeBackend, detail, fixture_adapter, listing
from vibe_job_radar.guided.adapters import Registry, builtins
from vibe_job_radar.guided.browser import PlaywrightBackend
from vibe_job_radar.guided.contracts import CrawlError, PageSnapshot
from vibe_job_radar.guided.rate import RateLedger, Limits
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.workspace import Workspace


class ListSurfaceTests(unittest.TestCase):
    def test_each_builtin_rejects_detail_recommendations_as_search_results(self):
        for key, suffix in [('boss','/job_detail/fixture.html'), ('liepin','/job/123.shtml'), ('51job','/pc/jobdetail')]:
            adapter = builtins().get(key)
            host = {'boss':'www.zhipin.com','liepin':'www.liepin.com','51job':'we.51job.com'}[key]
            page = PageSnapshot('https://' + host + suffix,
                                '<h1>正在查看的职位</h1><aside><a href="' + suffix + '">猜你喜欢职位</a></aside>')
            with self.subTest(key=key), self.assertRaises(CrawlError) as error:
                adapter.cards(page)
            self.assertEqual(error.exception.code, 'not_job_list')

    def test_homepage_recommendations_are_not_query_results(self):
        a = fixture_adapter()
        with self.assertRaises(CrawlError) as error:
            a.cards(PageSnapshot('https://jobs.fixture.test/', listing().html))
        self.assertEqual(error.exception.code, 'not_job_list')

    def test_login_form_with_recommendations_is_not_query_results(self):
        a = fixture_adapter()
        with self.assertRaises(CrawlError) as error:
            a.cards(PageSnapshot(a.login_url, '<input type="password">' + listing().html))
        self.assertEqual(error.exception.code, 'not_job_list')

    def test_challenged_search_does_not_supply_hidden_links(self):
        a = fixture_adapter()
        with self.assertRaises(CrawlError) as error:
            a.cards(PageSnapshot(a.search_url('x'), '<h1>登录后查看职位</h1>' + listing().html))
        self.assertEqual(error.exception.code, 'manual_required')

    def test_liepin_blank_challenge_route_is_not_empty_search(self):
        a = builtins().get('liepin')
        with self.assertRaises(CrawlError) as error:
            a.cards(PageSnapshot('https://safe.liepin.com/page/liepin/captchaPage_ip_PC', ''))
        self.assertEqual(error.exception.code, 'manual_required')

    def test_challenge_word_in_query_does_not_change_page_classification(self):
        a = fixture_adapter()
        self.assertFalse(a.challenged('正常搜索结果', a.search_url('/captcha/')))

    def test_legitimate_search_dedup_and_scope_unchanged(self):
        a = fixture_adapter()
        self.assertEqual(len(a.cards(listing())), 1)
        a = dataclasses.replace(a, card_selector='#jobs a[href]')
        p = PageSnapshot(a.search_url('x'), '<main id="jobs">' + listing().html + '</main>' +
                         '<aside><a href="/job/99">广告</a></aside>')
        self.assertEqual([c.url for c in a.cards(p)], ['https://jobs.fixture.test/job/1'])

    def test_matching_detail_text_without_company_keeps_distinct_job_urls(self):
        from vibe_job_radar.models import JobRecord
        a = builtins().get('liepin')
        markup = ('<h1>时间序列算法工程师</h1><div class="job-description">'
                  '使用 Cursor 进行 AI 辅助编程，编写单元测试和代码审查；负责时间序列预测。人工测试正文。</div>')
        records = []
        for ident in (1, 2):
            url = f'https://www.liepin.com/job/{ident}.shtml'
            data = a.detail(PageSnapshot(url, markup))
            record = JobRecord(**data, url=url, platform='liepin', source_mode='browser_fetch',
                               rights_note='人工测试输入')
            self.assertEqual(record.company, '')
            records.append(record)
        self.assertEqual(records[0].text, records[1].text)
        self.assertNotEqual(records[0].fingerprint, records[1].fingerprint)

    def test_site_explicitly_using_root_as_search_is_supported(self):
        a = dataclasses.replace(fixture_adapter(), search_base='https://jobs.fixture.test/')
        self.assertEqual(len(a.cards(PageSnapshot(a.search_url('x'), listing().html))), 1)


class PageStub:
    def __init__(self, context, url):
        self.context, self.url, self.closed, self.events = context, url, False, {}
        context.pages.append(self)
    def is_closed(self): return self.closed
    def on(self, event, callback): self.events[event] = callback
    def close(self):
        self.closed = True
        self.context.pages.remove(self)
        callback = self.events.get('close')
        if callback: callback(self)


class PopupContinuationTests(unittest.TestCase):
    def backend(self):
        b = object.__new__(PlaywrightBackend)
        b.adapter, b.context = fixture_adapter(), SimpleNamespace(pages=[])
        b.browser = SimpleNamespace(is_connected=lambda: True)
        b.page = None
        b.error, b.wait_error, b.auth_mode = None, None, True
        b.cancelled = threading.Event()
        b._pagination_page = None
        parent = PageStub(b.context, b.adapter.search_url('x'))
        b._bind_page(parent)
        return b, parent

    def test_closing_login_popup_restores_parent_without_losing_context(self):
        b, parent = self.backend()
        original = b.context
        popup = PageStub(b.context, b.adapter.login_url)
        b._bind_page(popup)
        popup.close()
        self.assertIs(b.page, parent)
        self.assertIs(b.context, original)
        self.assertTrue(b.alive())

    def test_closing_background_parent_does_not_replace_active_popup(self):
        b, parent = self.backend()
        popup = PageStub(b.context, b.adapter.login_url)
        b._bind_page(popup)
        parent.close()
        self.assertIs(b.page, popup)
        self.assertTrue(b.alive())

    def test_no_remaining_pages_is_not_a_live_session(self):
        b, parent = self.backend()
        parent.close()
        self.assertFalse(b.alive())

    def test_fallback_does_not_pick_external_or_credential_tab(self):
        b, parent = self.backend()
        for url in ['https://outside.invalid/', 'https://jobs.fixture.test/search?token=secret', 'about:blank']:
            PageStub(b.context, url)
        popup = PageStub(b.context, b.adapter.login_url)
        b._bind_page(popup)
        popup.close()
        self.assertIs(b.page, parent)

    def test_only_untrusted_pages_left_never_becomes_live(self):
        b, parent = self.backend()
        PageStub(b.context, 'https://outside.invalid/')
        parent.close()
        self.assertFalse(b.alive())

    def test_popup_close_does_not_clear_a_denial(self):
        b, parent = self.backend()
        popup = PageStub(b.context, b.adapter.login_url)
        b._bind_page(popup)
        b.error = 'http_403'
        popup.close()
        self.assertEqual(b.error, 'http_403')
        self.assertIs(b.page, parent)

    def test_capture_mode_cannot_erase_hard_errors(self):
        for code in ['http_401','http_403','tls_verification_failed','site_stopped','network_error']:
            b, _ = self.backend()
            b.error = code
            b.collection_mode()
            self.assertFalse(b.auth_mode)
            self.assertEqual(b.error, code)

    def test_transient_wait_reset_retains_existing_behavior(self):
        b, _ = self.backend()
        b.error = 'publisher_wait'
        b.collection_mode()
        self.assertIsNone(b.error)


class NativeLoginBackend(FakeBackend):
    def __init__(self, *args):
        super().__init__(*args)
        self.logged_in, self.login_challenge = True, False
        self.auth_calls = []
        self.collection_mode = Mock()
        self.block_detail = None
    def open(self, url, authentication=False):
        self.auth_calls.append(authentication)
        if authentication:
            self.opens.append(url)
            self.page = PageSnapshot(url, '<h1>人工平台登录</h1>')
            if self.login_challenge: raise CrawlError('manual_required')
            return self.page
        if not self.logged_in or url == self.block_detail:
            self.opens.append(url)
            self.page = PageSnapshot(url, '<h1>登录后查看职位</h1>')
            raise CrawlError('manual_required')
        return super().open(url, authentication=authentication)


class GuidedContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.tmp.name))
        self.service = GuidedService(self.workspace, registry=Registry([fixture_adapter()]),
            backend_factory=NativeLoginBackend,
            ledger=RateLedger(Path(self.tmp.name)/'guided'/'rates.sqlite', Limits(login_interval=0)))
    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()
    def wait(self):
        end = time.monotonic()+10
        while self.service.state()['busy'] and time.monotonic()<end: time.sleep(.01)
        self.assertFalse(self.service.state()['busy'])
    def create(self, **kw):
        values=dict(platform='fixture', keyword='时间序列 & 原条件', roles=['time_series'], consent=True,
                    rights_note='人工测试范围', max_pages=1, max_jobs=5)
        values.update(kw)
        ident=self.service.create(values)['id']; self.wait()
        return ident
    def action(self, ident, action, **kw):
        self.service.action(dict(id=ident, action=action, **kw)); self.wait()
        return self.service._load(ident)

    def test_capture_checks_current_page_even_after_one_page_limit(self):
        ident=self.create()
        self.service._backends[ident].page = detail('https://jobs.fixture.test/job/1')
        state=self.action(ident, 'capture')
        self.assertEqual(state['code'], 'not_job_list')
        self.assertEqual(state['status'], 'waiting_manual')
        self.assertEqual(len(state['cards']), 1)

    def test_new_unique_page_at_limit_does_not_silently_report_ready(self):
        ident=self.create()
        self.service._backends[ident].page = listing(2)
        state=self.action(ident, 'capture')
        self.assertEqual(state['code'], 'list_page_limit')
        self.assertEqual(len(state['cards']), 1)
        self.assertEqual(len(state['pages_seen']), 1)

    def test_same_page_at_limit_still_ready_without_new_budget(self):
        ident=self.create()
        before=self.service._load(ident)
        state=self.action(ident, 'capture')
        self.assertEqual(state['status'], 'ready')
        self.assertEqual(state['pages_seen'], before['pages_seen'])
        self.assertEqual(state['cards'], before['cards'])

    def test_empty_current_page_not_reported_as_new_ready_result(self):
        ident=self.create()
        self.service._backends[ident].page=PageSnapshot(fixture_adapter().search_url('x'), '<h1>空列表</h1>')
        state=self.action(ident, 'capture')
        self.assertEqual(state['code'], 'empty_list')
        self.assertEqual(state['status'], 'waiting_manual')
        self.assertEqual(len(state['cards']), 1)

    def test_login_pending_saved_even_if_login_page_requests_manual_work(self):
        ident=self.create()
        self.service._backends[ident].login_challenge=True
        state=self.action(ident, 'login')
        self.assertEqual(state['authentication'], 'manual_pending')
        self.assertEqual(state['status'], 'waiting_manual')

    def test_login_home_does_not_replace_search_and_resume_uses_exact_target(self):
        ident=self.create()
        original=self.service._load(ident)
        state=self.action(ident, 'login')
        backend=self.service._backends[ident]
        state=self.action(ident, 'capture')
        self.assertEqual(state['code'], 'not_job_list')
        state=self.action(ident, 'resume')
        self.assertEqual(backend.opens[-1], original['search_url'])
        for key in ('id','keyword','roles','rights_note','selection','max_pages','max_jobs'):
            self.assertEqual(state[key], original[key])
        self.assertEqual(state['status'], 'ready')
        self.assertEqual(state['authentication'], 'user_resumed')

    def test_login_during_details_resumes_remaining_selection_not_search(self):
        ident=self.create(max_pages=2)
        before=self.service._load(ident)
        backend=self.service._backends[ident]
        backend.block_detail=before['cards'][1]['url']
        ids=[c['id'] for c in before['cards']]
        partial=self.action(ident, 'collect', selected=ids)
        self.assertEqual(partial['status'], 'waiting_manual')
        old_report=partial['report_id']; self.assertTrue(old_report)
        self.action(ident, 'login')
        backend.block_detail=None
        resumed=self.action(ident, 'resume')
        self.assertEqual(resumed['status'], 'completed')
        self.assertEqual(resumed['selection'], ids)
        self.assertEqual(backend.opens.count(before['cards'][0]['url']), 1)
        self.assertEqual(backend.opens.count(before['cards'][1]['url']), 2)
        self.assertEqual(resumed['authentication'], 'user_resumed')
        self.assertTrue(self.workspace.report_file(old_report,'run_manifest.json').is_file())

    def test_unsuccessful_resume_does_not_claim_login_verified(self):
        ident=self.create(); self.action(ident, 'login')
        self.service._backends[ident].logged_in=False
        state=self.action(ident, 'resume')
        self.assertEqual(state['status'],'waiting_manual')
        self.assertEqual(state['authentication'],'manual_pending')
        self.assertFalse(state['certification']=='live_verified')


if __name__=='__main__': unittest.main()
