"""Core research outcome tests; every JD and personal observation is artificial."""
from __future__ import annotations
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from vibe_job_radar.workspace import Workspace
from vibe_job_radar.evidence_ui import EvidenceService
from vibe_job_radar.research_brief import build_brief, brief_markdown


def capture(**overrides):
    item = dict(title='时间序列算法工程师', company='研究流程人工夹具', platform='manual',
                source_ref='research-fixture:one', url='', location='',
                text='要求熟练使用 Cursor 进行 AI 辅助编程，编写单元测试并完成代码审查。',
                rights_note='人工测试材料，不是真实招聘或本人经历。',
                evidence_level='full_text', full_text_confirmed=True)
    item.update(overrides)
    return item


class ResearchBriefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, data=None, **job):
        self.workspace.add_job(capture(**job))
        return self.workspace.analyze(data or {})

    def test_real_input_report_contains_actionable_brief_and_exact_spans(self):
        report = self.report()
        b = report['manifest']['research_brief']
        self.assertEqual(b['status'], 'research_ready')
        self.assertEqual(b['mode'], 'real_sample')
        self.assertEqual(b['counts']['full_text_job_groups'], 1)
        rows = {r['requirement_id']: r for r in report['requirements']}
        for cap in b['capabilities']:
            for example in cap['examples']:
                self.assertEqual(example['quote_preview'], rows[example['requirement_id']]['quote'])
                self.assertFalse(example['quote_is_excerpt'])
        self.assertTrue(any(c['evidence_to_check'] for c in b['capabilities']))
        self.assertIn('不等于本人不具备', b['evidence_note'])

    def test_single_job_does_not_establish_common_baseline(self):
        report = self.report()
        self.assertEqual(report['manifest']['research_brief']['common_capability_ids'], [])
        self.assertIn('单岗位要求仍见', report['descriptions'])

    def test_two_deduplicated_jobs_can_form_only_sample_common_items(self):
        self.workspace.add_job(capture(title='时间序列预测算法工程师', company='另一个人工公司', source_ref='fixture:two'))
        b = self.report()['manifest']['research_brief']
        self.assertEqual(b['counts']['full_text_job_groups'], 2)
        self.assertTrue(b['common_capability_ids'])
        self.assertIn('不是全市场', b['scope_note'])

    def test_unmatched_real_job_explains_exclusion_not_market_absence(self):
        b = self.report(title='收银员', text='负责收款。')['manifest']['research_brief']
        self.assertEqual(b['status'], 'no_full_text')
        self.assertEqual(b['capabilities'], [])
        self.assertEqual(b['exclusions'][0]['reason'], 'role_unmatched')
        self.assertIn('暂不能', b['conclusion'])

    def test_snippet_does_not_generate_accepted_capabilities(self):
        b = self.report(evidence_level='snippet', full_text_confirmed=False)['manifest']['research_brief']
        self.assertEqual(b['status'], 'no_full_text')
        self.assertEqual(b['capabilities'], [])

    def test_negative_and_ordinary_skills_do_not_become_positive_ai_claims(self):
        b = self.report(text='不要求使用 Cursor。熟悉时间序列预测和滚动回测。')['manifest']['research_brief']
        self.assertEqual(b['status'], 'no_ai_evidence')
        self.assertEqual(b['capabilities'], [])
        self.assertIn('不表示招聘方没有', b['next_step'])

    def test_role_filter_kept_no_other_role_requirements_borrowed(self):
        b = self.report({'roles':['time_series']})['manifest']['research_brief']
        self.assertEqual([r['id'] for r in b['roles']], ['time_series'])

    def test_default_other_role_sections_explicitly_empty(self):
        b = self.report()['manifest']['research_brief']
        architecture = next(r for r in b['roles'] if r['id'] == 'architect')
        self.assertEqual(architecture['capabilities'], [])

    def test_brief_files_registered_in_manifest(self):
        r = self.report()
        for file in ['research_brief.json','research_brief.md']:
            raw = self.workspace.report_file(r['id'], file).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), r['manifest']['output_files_sha256'][file])
        saved = json.loads(self.workspace.report_file(r['id'], 'research_brief.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, r['manifest']['research_brief'])

    def test_demo_explicitly_not_real_and_does_not_populate_real_db(self):
        r = self.workspace.analyze({'dataset':'demo'})
        b = r['manifest']['research_brief']
        self.assertEqual(b['mode'], 'synthetic_demo')
        self.assertIn('合成演示', brief_markdown(b))
        self.assertFalse(self.workspace.db.exists())

    def test_html_markdown_from_source_is_not_active_markup(self):
        b = self.report(company='<img src=x onerror=alert(1)>')['manifest']['research_brief']
        b['source_labels'] = ['<script>alert(1)</script>[bad](javascript:evil)']
        s = brief_markdown(b)
        self.assertNotIn('<script>', s)
        self.assertNotIn('[bad](javascript:evil)', s)
        self.assertIn('待填', s)

    def test_incomplete_analysis_never_becomes_ready(self):
        r = self.report(); m = copy.deepcopy(r['manifest']); m['status']='incomplete'
        b = build_brief(m, [], [], [], [], self.workspace.config)
        self.assertEqual(b['status'], 'analysis_incomplete')
        self.assertIn('analysis_errors.csv', b['next_step'])

    def test_new_report_brief_not_affected_by_later_database_writes(self):
        old = self.report(); before = self.workspace.report_file(old['id'], 'research_brief.json').read_bytes()
        self.workspace.add_job(capture(title='架构师', source_ref='fixture:later', company='后来的公司'))
        self.workspace.analyze({})
        self.assertEqual(before, self.workspace.report_file(old['id'], 'research_brief.json').read_bytes())

    def test_personal_generation_reuses_exact_source_not_newest_report(self):
        original = self.report({'roles':['time_series']})
        self.report({'roles':['architect']}, title='架构师', source_ref='fixture:decoy')
        service = EvidenceService(self.workspace)
        before = service.state()['revision']
        c = service.catalogue({'run_id':original['id']})
        self.assertEqual({r['title'] for r in c['rows']}, {'时间序列算法工程师'})
        result = service.generate({'run_id':original['id'], 'expected_revision':before})
        self.assertIn('尚未提供', result['descriptions'])
        brief = self.workspace.report(result['id'])['manifest']['research_brief']
        self.assertEqual([r['id'] for r in brief['roles']], ['time_series'])
        self.assertEqual(service.state()['revision'], before)

    def test_legacy_reports_remain_readable_without_new_brief(self):
        report = self.report()
        manifest_path = self.workspace.report_file(report['id'], 'run_manifest.json')
        m = report['manifest']; del m['research_brief']
        manifest_path.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
        self.assertNotIn('research_brief', self.workspace.report(report['id'])['manifest'])

    def test_pending_is_counted_but_not_used_as_accepted_fact(self):
        r=self.report()
        from vibe_job_radar.models import Requirement
        req=Requirement(**r['requirements'][0]);req.review_status='needs_review'
        from vibe_job_radar.synthesis import aggregate, evidence_matrix
        b=build_brief(r['manifest'],aggregate([req],self.workspace.config),[req],
                      evidence_matrix([req],{},self.workspace.config,demo_mode=False),[],self.workspace.config)
        self.assertEqual(b['capabilities'],[])
        self.assertEqual(b['evidence_to_check'],0)


if __name__=='__main__':
    unittest.main()
