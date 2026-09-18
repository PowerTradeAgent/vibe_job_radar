"""Synthetic representations only. No live Liepin request or account is used."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from vibe_job_radar.guided.adapters import DOMAdapter, Registry, builtins
from vibe_job_radar.guided.contracts import CrawlError, PageSnapshot
from vibe_job_radar.guided.liepin import semantic_detail
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.store import Store
from vibe_job_radar.workspace import Workspace

URL = 'https://www.liepin.com/job/123.shtml'
SEARCH = 'https://www.liepin.com/zhaopin/?key=test'
BODY = '岗位职责：负责时间序列预测模型研发。任职要求：使用 Cursor 进行 AI 辅助编程，编写单元测试，进行代码审查与验证。'


def page_markup(body=BODY):
    return '<h1>时间序列算法工程师</h1><dl><dt>职位介绍</dt><dd><p>' + body + '</p></dd></dl>'


class SemanticDetailTests(unittest.TestCase):
    def test_explicit_definition_list(self):
        parsed = semantic_detail(page_markup())
        self.assertEqual(parsed['text'], BODY)
        self.assertEqual(parsed['parser'], 'liepin:semantic_intro:v1')

    def test_section_intro_and_later_heading_boundary(self):
        html = '<h1>职位标题</h1><section><h2>职位介绍</h2><div>' + BODY + '</div><h2>公司信息</h2><p>别的正文</p></section>'
        self.assertEqual(semantic_detail(html)['text'], BODY)

    def test_never_include_recommendations_and_footer(self):
        html = page_markup() + '<aside><h2>职位介绍</h2>推荐职位</aside><footer>公司信息</footer>'
        self.assertEqual(semantic_detail(html)['text'], BODY)

    def test_hidden_heading_is_not_duplicate(self):
        html = page_markup() + '<section hidden><h1>隐藏标题</h1><h2>职位介绍</h2><p>摘要</p></section>'
        self.assertEqual(semantic_detail(html)['text'], BODY)

    def test_style_and_aria_hidden_excluded(self):
        for attribute in ['aria-hidden="true"', 'style="display: none !important;"', 'style="visibility:hidden"']:
            with self.subTest(attribute=attribute):
                self.assertEqual(semantic_detail(page_markup() + '<div ' + attribute + '><h1>隐藏</h1></div>')['text'], BODY)

    def test_two_visible_titles_fail(self):
        with self.assertRaises(CrawlError):
            semantic_detail(page_markup() + '<h1>另一个岗位</h1>')

    def test_two_equal_introductions_fail(self):
        with self.assertRaises(CrawlError):
            semantic_detail(page_markup() + '<dl><dt>职位介绍</dt><dd>' + BODY + '</dd></dl>')

    def test_body_level_heading_is_not_a_dedicated_container(self):
        with self.assertRaises(CrawlError):
            semantic_detail('<h1>职位</h1><h2>职位介绍</h2><p>' + BODY + '</p>')

    def test_no_heading_does_not_fall_back_to_full_body(self):
        with self.assertRaises(CrawlError):
            semantic_detail('<h1>职位</h1><main>' + BODY + '</main>')

    def test_nested_company_section_fails(self):
        with self.assertRaises(CrawlError):
            semantic_detail('<h1>职位</h1><section><h2>职位介绍</h2><div>' + BODY + '<h3>公司信息</h3></div></section>')

    def test_recommendation_text_inside_candidate_fails(self):
        with self.assertRaises(CrawlError):
            semantic_detail(page_markup(BODY + '推荐职位：另一份工作'))

    def test_unexpanded_content_remains_incomplete(self):
        for prompt in ['展开全部', '展开更多', '登录后查看完整职位', '查看完整职位']:
            with self.subTest(prompt=prompt), self.assertRaises(CrawlError) as error:
                semantic_detail(page_markup(BODY + prompt))
            self.assertEqual(error.exception.code, 'jd_incomplete')

    def test_long_marketing_copy_not_a_job(self):
        with self.assertRaises(CrawlError):
            semantic_detail(page_markup('我们是一个很好的团队。' * 30))

    def test_no_truncation_of_long_valid_jd(self):
        text = BODY + ('这是职责说明。' * 100)
        self.assertEqual(semantic_detail(page_markup(text))['text'], text)

    def test_zero_ai_requirements_keeps_actual_text(self):
        text = '岗位职责：负责时间序列预测与模型评估。任职要求：熟悉Python、统计学、回归分析，具备良好的沟通能力。'
        self.assertEqual(semantic_detail(page_markup(text))['text'], text)

    def test_payload_limit(self):
        with self.assertRaises(CrawlError) as error:
            semantic_detail('x' * 5_000_001)
        self.assertEqual(error.exception.code, 'response_too_large')


class LiepinIdentityTests(unittest.TestCase):
    def setUp(self):
        self.adapter = builtins().get('liepin')

    def test_tracker_queries_do_not_change_entity(self):
        self.assertEqual(self.adapter.job_identity(URL + '?d_sfrom=one'), self.adapter.job_identity(URL + '?d_sfrom=two'))

    def test_url_families_are_not_assumed_aliases(self):
        values = [self.adapter.job_identity('https://www.liepin.com' + suffix) for suffix in ['/job/123.shtml', '/a/123.shtml', '/lptjob/123']]
        self.assertEqual(len(set(values)), 3)

    def test_final_url_must_match_selected_job(self):
        with self.assertRaises(CrawlError) as error:
            self.adapter.validate_detail_identity(URL, PageSnapshot(URL.replace('123', '456'), page_markup()))
        self.assertEqual(error.exception.code, 'job_identity_mismatch')

    def test_conflicting_canonical_is_rejected(self):
        with self.assertRaises(CrawlError) as error:
            self.adapter.validate_detail_identity(URL, PageSnapshot(URL, '<link rel="canonical" href="/job/456.shtml">' + page_markup()))
        self.assertEqual(error.exception.code, 'job_identity_mismatch')

    def test_same_canonical_with_different_tracking_is_accepted(self):
        self.adapter.validate_detail_identity(URL + '?d_sfrom=a', PageSnapshot(URL + '?d_sfrom=b', '<link rel="canonical" href="/job/123.shtml">' + page_markup()))

    def test_canonical_cannot_grant_external_access(self):
        with self.assertRaises(CrawlError):
            self.adapter.detail(PageSnapshot(URL, '<link rel="canonical" href="https://evil.invalid/job/123.shtml">' + page_markup()))

    def test_list_deduplicates_tracking_variants(self):
        html = '<a href="/job/123.shtml?d_sfrom=a">时间序列</a><a href="/job/123.shtml?d_sfrom=b">时间序列</a>'
        self.assertEqual(len(self.adapter.cards(PageSnapshot(SEARCH, html))), 1)

    def test_existing_task_card_ids_are_not_migrated(self):
        page = PageSnapshot(SEARCH, '<a href="/job/123.shtml?d_sfrom=a">时间序列</a>')
        legacy = DOMAdapter.cards(self.adapter, page)
        self.assertEqual(self.adapter.cards(page), legacy)

    def test_non_search_page_does_not_supply_cards(self):
        with self.assertRaises(CrawlError):
            self.adapter.cards(PageSnapshot('https://www.liepin.com/company/1/', '<a href="/job/123.shtml">推荐</a>'))

    def test_semantic_fallback_used_by_actual_adapter(self):
        self.assertEqual(self.adapter.detail(PageSnapshot(URL, page_markup()))['text'], BODY)

    def test_existing_isolated_dom_is_preserved(self):
        p = PageSnapshot(URL, '<h1>职位标题</h1><div class="job-description">' + BODY + '</div>')
        self.assertEqual(self.adapter.detail(p)['parser'], 'dom:job-description')

    def test_structured_identity_conflict_cannot_be_hidden_by_fallback(self):
        structured = {'@type':'JobPosting', 'url':URL.replace('123', '456'), 'title':'另一个岗位', 'description':BODY}
        html = '<script type="application/ld+json">' + json.dumps(structured) + '</script>' + page_markup()
        with self.assertRaises(CrawlError):
            self.adapter.detail(PageSnapshot(URL, html))

    def test_existing_json_ld_is_preserved(self):
        structured = {'@type':'JobPosting','url':URL,'title':'时间序列算法工程师','description':BODY}
        html = '<script type="application/ld+json">' + json.dumps(structured) + '</script>'
        self.assertEqual(self.adapter.detail(PageSnapshot(URL, html))['parser'], 'json_ld_jobposting')

    def test_login_or_challenge_page_never_is_full_jd(self):
        with self.assertRaises(CrawlError) as error:
            self.adapter.detail(PageSnapshot(URL, '<h1>登录后查看职位</h1>' + page_markup()))
        self.assertEqual(error.exception.code, 'manual_required')


class ReturnedPageBackend:
    """Test-local pages only; does not create a browser or make network requests."""
    final_url = URL
    markup = page_markup()

    def __init__(self, adapter, ledger, cancelled, progress):
        self.page = None
    def open(self, url, authentication=False):
        self.page = PageSnapshot(url, '<a href="/job/123.shtml">时间序列算法工程师</a>') if '/zhaopin/' in url else PageSnapshot(self.final_url, self.markup)
        return self.page
    def snapshot(self): return self.page
    def next_page(self): return False
    def pump(self): pass
    def close(self): pass


class LiepinPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.tmp.name))
        self.backend_type = type('IsolatedReturnedPages', (ReturnedPageBackend,), {})
        self.service = GuidedService(self.workspace, registry=Registry([builtins().get('liepin')]), backend_factory=self.backend_type)
    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()
    def wait(self):
        until = time.monotonic() + 10
        while self.service.state()['busy'] and time.monotonic() < until:
            time.sleep(.01)
        self.assertFalse(self.service.state()['busy'])
    def run_task(self):
        ident = self.service.create({'platform':'liepin','keyword':'时间序列算法工程师','roles':['time_series'],
                                     'consent':True,'rights_note':'仅测试合成页面，不是猎聘实站数据', 'max_pages':1,'max_jobs':1})['id']
        self.wait()
        state = self.service._load(ident)
        self.service.action({'id':ident,'action':'collect','selected':[state['cards'][0]['id']]})
        self.wait()
        return self.service._load(ident)

    def test_semantic_body_to_existing_report_and_audit(self):
        state = self.run_task()
        self.assertEqual(state['cards'][0]['status'], 'ok')
        self.assertTrue(state['report_id'])
        with Store(self.workspace.db) as store:
            records = store.records()
        self.assertEqual(records[0].text, BODY)
        row = state['cards'][0]
        self.assertEqual(row['body_sha256'], hashlib.sha256(BODY.encode()).hexdigest())
        self.assertTrue(row['platform_job_id'].endswith(':job:123'))
        audit = json.loads(self.workspace.report_file(state['report_id'], 'guided_acquisition.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['items'][0]['body_sha256'], row['body_sha256'])
        self.assertEqual(state['certification'], 'not_live_verified')

    def test_wrong_final_job_is_never_persisted(self):
        self.backend_type.final_url = URL.replace('123', '456')
        state = self.run_task()
        self.assertEqual(state['cards'][0]['status'], 'job_identity_mismatch')
        self.assertFalse(state['report_id'])
        with Store(self.workspace.db) as store:
            self.assertEqual(len(store.records()), 0)

    def test_incomplete_body_never_counts_as_success(self):
        self.backend_type.markup = page_markup(BODY + '展开全部')
        state = self.run_task()
        self.assertEqual(state['cards'][0]['status'], 'jd_incomplete')
        self.assertEqual(state['outcome']['saved'], 0)
        self.assertFalse(state['report_id'])


if __name__ == '__main__':
    unittest.main()
