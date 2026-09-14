"""Artificial fixtures, real storage/pipeline/HTTP; remote providers are mocked."""
import base64
import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from vibe_job_radar.collection import Collector, TERMINAL, safe_url, writer_lock
from vibe_job_radar.evidence_ui import Conflict, EvidenceService
from vibe_job_radar.models import JobRecord
from vibe_job_radar.network import FetchError, Response
from vibe_job_radar.store import Store
from vibe_job_radar.synthesis import load_candidate
from vibe_job_radar.workspace import InputError, Workspace
import test_workbench as legacy
from test_workbench import capture


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = Workspace(self.tmp.name)
        self.w.add_job(capture())
        self.run = self.w.analyze({})
        self.e = EvidenceService(self.w)
        self.rows = self.e.catalogue({'run_id':self.run['id']})['rows']
        self.row = next(r for r in self.rows if r['review_status']=='rule_accepted' and r['strength'] not in {'prohibited','not_required'})

    def upload(self, content=b'test proof'):
        return self.e.upload({'name':'../../proof.txt','content_base64':base64.b64encode(content).decode(),'rights_confirmed':True})['artifact_id']

    def data(self, **kw):
        return {'expected_revision':self.e.state()['revision'],'name':'TEST PERSON NOT REAL','project':'Fixture Project',
            'contribution':'仅人工测试，负责规格、实现与回归验证。','scope':'offline','run_id':self.run['id'],
            'artifact_id':self.upload(),'capabilities':[self.row['capability']],'requirement_ids':[self.row['requirement_id']],
            'metrics':[{'metric_id':'cycle_time_hours','current':6,'baseline':10,'sample_size':20,'baseline_sample_size':20,
                        'window':'fixture after','baseline_window':'fixture before','comparison_basis':'same artificial tasks'}],
            'review_status':'approved','reviewer':'Fixture reviewer','attested':True,**kw}

    def test_review_and_personal_report(self):
        state = self.e.save(self.data())
        report = self.e.generate({'run_id':self.run['id'],'expected_revision':state['revision']})
        self.assertIn('40.00%', report['descriptions'])
        self.assertIn('本人负责范围', report['descriptions'])
        self.assertIn('fixture before', report['descriptions'])
        self.assertTrue(any(r['status']=='user_attested_exact' for r in report['matrix']))
        self.assertIn('candidate.effective.json', report['files'])
        self.assertIn('reviews.effective.json', report['files'])
        for name, digest in report['manifest']['output_files_sha256'].items():
            self.assertEqual(hashlib.sha256(self.w.report_file(report['id'],name).read_bytes()).hexdigest(),digest)

    def test_source_snapshot_not_mutated_or_mixed_with_later_jobs(self):
        original = self.w.report_file(self.run['id'],'run_manifest.json').read_bytes()
        self.e.save(self.data())
        self.w.add_job(capture(url='https://www.zhipin.com/job_detail/later.html',text='使用 Codex 完成代码审查。'))
        report = self.e.generate({'run_id':self.run['id'],'expected_revision':1})
        self.assertEqual(report['manifest']['stats']['full_text_job_groups'],1)
        self.assertEqual(self.w.report_file(self.run['id'],'run_manifest.json').read_bytes(),original)

    def test_optimistic_concurrency_and_restart(self):
        data=self.data();state=self.e.save(data)
        with self.assertRaises(Conflict): self.e.save(data)
        fresh=EvidenceService(self.w).state()
        self.assertEqual(fresh['revision'],state['revision'])
        self.assertEqual(len(fresh['evidence']),1)

    def test_edit_and_remove_have_revision_history(self):
        state=self.e.save(self.data());eid=state['evidence'][0]['evidence_id']
        state=self.e.save(self.data(evidence_id=eid,review_status='draft',attested=False))
        self.assertEqual(len(state['evidence']),1)
        self.assertEqual(state['evidence'][0]['review_status'],'draft')
        state=self.e.remove({'expected_revision':2,'evidence_id':eid})
        self.assertEqual(state['evidence'],[])
        self.assertEqual(len(state['history']),3)

    def test_portable_export_preserves_file_hash(self):
        self.e.save(self.data())
        with tempfile.TemporaryDirectory() as target:
            archive=zipfile.ZipFile(io.BytesIO(self.e.export()))
            archive.extractall(target)
            candidate=load_candidate(Path(target)/'candidate.json',self.w.config)
            self.assertEqual(candidate['evidence'][0]['integrity_status'],'file_hash_verified_content_not_audited')

    def test_attachment_tampering_rejected(self):
        data=self.data();(self.e.artifacts/data['artifact_id']).write_bytes(b'changed')
        with self.assertRaises(InputError):self.e.save(data)

    def test_no_arbitrary_local_file_reference(self):
        with self.assertRaises(InputError):self.e.save(self.data(evidence_ref='/etc/passwd'))
        with self.assertRaises(InputError):self.e._artifact_path('../secret')

    def test_upload_consent_size_and_encoding(self):
        for d in ({'rights_confirmed':False},{'content_base64':'@@'}, {'content_base64':base64.b64encode(b'x'*1000001).decode()}):
            data={'name':'x','content_base64':'eA==','rights_confirmed':True,**d}
            with self.subTest(data=list(d)),self.assertRaises(InputError):self.e.upload(data)

    def test_approved_needs_attestation_and_reference(self):
        with self.assertRaises(InputError):self.e.save(self.data(attested=False))
        with self.assertRaises(InputError):self.e.save(self.data(artifact_id=''))

    def test_external_reference_is_not_fetched(self):
        with patch('socket.create_connection',side_effect=AssertionError('network forbidden')):
            self.e.save(self.data(artifact_id='',external_url='https://example.org/proof'))
            report=self.e.generate({'run_id':self.run['id'],'expected_revision':1})
        self.assertIn('external_reference_not_fetched',report['descriptions'])

    def test_credential_urls_rejected(self):
        for url in ('https://u:p@example.org/proof','https://example.org/proof?token=x','file:///etc/passwd'):
            with self.subTest(url=url),self.assertRaises(ValueError):self.e.save(self.data(artifact_id='',external_url=url))

    def test_invalid_metrics_never_saved(self):
        for metrics in ([{'metric_id':'test_pass_rate','current':101,'sample_size':2,'window':'test','comparison_basis':'test'}],
                        [{'metric_id':'cycle_time_hours','current':True,'sample_size':2,'window':'test','comparison_basis':'test'}]):
            with self.assertRaises(InputError):self.e.save(self.data(metrics=metrics))
        self.assertEqual(self.e.state()['revision'],0)

    def test_zero_baseline_and_percentage_points(self):
        base={'metric_id':'cycle_time_hours','current':1,'baseline':0,'sample_size':3,'baseline_sample_size':3,
              'window':'after','baseline_window':'before','comparison_basis':'same'}
        self.assertIsNone(self.e.metric(base)['metric']['relative_reduction_pct'])
        result=self.e.metric({**base,'metric_id':'test_pass_rate','baseline':80,'current':90})
        self.assertEqual(result['metric']['percentage_point_change'],10)

    def test_synthetic_source_and_unknown_requirement_rejected(self):
        demo=self.w.analyze({'dataset':'demo'})
        with self.assertRaises(InputError):self.e.catalogue({'run_id':demo['id']})
        with self.assertRaises(InputError):self.e.save(self.data(requirement_ids=['unknown']))

    def test_source_integrity_is_checked(self):
        self.w.report_file(self.run['id'],'requirements.jsonl').write_text('{}\n',encoding='utf-8')
        with self.assertRaises(InputError):self.e.catalogue({'run_id':self.run['id']})

    def test_review_reject_and_pending_preserve_history(self):
        d={'run_id':self.run['id'],'requirement_id':self.row['requirement_id'],'reviewer':'test','reason':'fixture review'}
        self.e.review({**d,'expected_revision':0,'decision':'reject'})
        report=self.e.generate({'run_id':self.run['id'],'expected_revision':1})
        row=next(r for r in report['requirements'] if r['requirement_id']==self.row['requirement_id'])
        self.assertEqual(row['review_status'],'rejected')
        self.e.review({**d,'expected_revision':1,'decision':'pending'})
        self.assertEqual(self.e.state()['reviews'][self.row['requirement_id']]['decision'],'pending')

    def test_snippet_review_never_promotes_to_full_text(self):
        w=Workspace(Path(self.tmp.name)/'snippets');w.add_job(capture(evidence_level='snippet',full_text_confirmed=False));run=w.analyze({});e=EvidenceService(w)
        row=e.catalogue({'run_id':run['id']})['rows'][0]
        e.review({'run_id':run['id'],'requirement_id':row['requirement_id'],'reviewer':'test','reason':'test','decision':'approve','expected_revision':0})
        report=e.generate({'run_id':run['id'],'expected_revision':1})
        self.assertEqual(report['manifest']['stats']['full_text_job_groups'],0)
        self.assertEqual(report['manifest']['stats']['accepted_positive_requirement_rows'],0)

    def test_catalogue_paginates_beyond_preview_limit(self):
        for i in range(65):self.w.add_job(capture(company=f'Fixture {i}',url=f'https://www.zhipin.com/job_detail/fixture-{i}.html'))
        run=self.w.analyze({});pages=[];page=0
        while True:
            result=self.e.catalogue({'run_id':run['id'],'page':page});pages.extend(result['rows']);page+=1
            if page*50>=result['total']:break
        self.assertGreater(len(pages),100)
        self.assertEqual(len(pages),len({r['requirement_id'] for r in pages}))
        self.assertTrue(all('source_text' in r for r in pages))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name);self.c=Collector(self.w)

    def start(self,**kw):
        return self.c.start({'mode':'urls','roles':['architect'],'platforms':['boss'],'permit_platforms':['boss'],
            'urls':'https://www.zhipin.com/job_detail/fixture.html','consent':True,'rights_note':'artificial fixture only',**kw})

    def finish(self,s,key=''):
        for _ in range(500):
            s=self.c.step({'id':s['id'],'api_key':key})
            if s['status'] in TERMINAL:return s
        self.fail('run did not terminate')

    def html(self):
        return Response(200,{'content-type':'text/html'},'<h1>架构师</h1><div class="job-sec-text">熟练使用 Cursor 进行 AI 辅助编程，编写单元测试并审查代码。</div>'.encode(),'https://www.zhipin.com/job_detail/fixture.html')

    def test_url_route_to_audited_report(self):
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            factory.return_value.fetch.return_value=self.html()
            s=self.finish(self.start())
        self.assertEqual(s['status'],'completed');self.assertEqual(s['detail_attempts'],1)
        self.assertEqual(s['details'][0]['status'],'ok')
        self.assertIn('collection_manifest.json',self.w.report(s['report_id'])['files'])
        self.assertEqual(self.w.status()['counts']['full_text'],1)

    def test_permissions_required_before_any_detail_network(self):
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            s=self.finish(self.start(permit_platforms=[]))
            factory.assert_not_called()
        self.assertEqual(s['details'][0]['status'],'permission_required')
        self.assertEqual(s['status'],'needs_attention')

    def test_zero_detail_budget(self):
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            s=self.finish(self.start(detail_budget=0));factory.assert_not_called()
        self.assertEqual(s['detail_attempts'],0);self.assertEqual(s['details'][0]['status'],'budget_skipped')

    def test_duplicate_seeds_fresh_cache_and_restart(self):
        url='https://www.zhipin.com/job_detail/fixture.html'
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            factory.return_value.fetch.return_value=self.html()
            first=self.finish(self.start(urls=url+'\n'+url))
        self.assertEqual(len(first['details']),1)
        self.c=Collector(self.w)
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            second=self.finish(self.start());factory.assert_not_called()
        self.assertEqual(second['details'][0]['status'],'fresh_reused')

    def test_failure_status_and_host_stop(self):
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            factory.return_value.fetch.side_effect=FetchError('login_or_challenge')
            s=self.finish(self.start(urls='https://www.zhipin.com/job_detail/a.html\nhttps://www.zhipin.com/job_detail/b.html'))
            self.assertEqual(factory.return_value.fetch.call_count,1)
        self.assertEqual([r['status'] for r in s['details']],['login_or_challenge','host_stopped'])

    def test_robots_error_keeps_explicit_reason(self):
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            factory.return_value.fetch.side_effect=FetchError('robots_denied')
            s=self.finish(self.start())
        self.assertEqual(s['details'][0]['status'],'robots_denied')

    def test_all_configured_platforms_interleaved(self):
        s=self.start(mode='search',platforms=list(self.w.config['platforms']),permit_platforms=[],search_storage_rights=True)
        self.assertEqual(set(t['platform'] for t in s['tasks'][:8]),set(self.w.config['platforms']))

    def test_missing_key_and_storage_consent(self):
        with self.assertRaises(InputError):self.start(mode='search')
        s=self.start(mode='search',search_storage_rights=True)
        with patch.dict('os.environ',{},clear=True),self.assertRaises(InputError):self.c.step({'id':s['id']})
        self.assertEqual(self.c.status({'id':s['id']})['search_requests'],0)

    def test_search_page_offsets_fair_budget_and_dedupe(self):
        s=self.start(mode='search',pages=2,search_budget=12,detail_budget=0,search_storage_rights=True)
        response={'web':{'results':[{'title':'架构师','description':'使用 Cursor 编程','url':'https://www.zhipin.com/job_detail/fixture.html'}]},'query':{'more_results_available':True}}
        with patch('vibe_job_radar.collection.SafeHTTP') as factory:
            factory.return_value.json.return_value=response
            s=self.finish(s,key='fixture-not-a-real-secret')
            urls=[c.args[0] for c in factory.return_value.json.call_args_list]
        self.assertEqual(s['search_requests'],12)
        self.assertTrue(any('offset=1' in u for u in urls));self.assertFalse(any('offset=20' in u for u in urls))
        self.assertEqual(len(s['details']),1);self.assertEqual(self.w.status()['counts']['snippet'],1)
        self.assertEqual(self.w.status()['counts']['full_text'],0)

    def test_provider_denial_stops_without_secret_leak(self):
        for code in ('http_401','http_403','http_429'):
            s=self.start(mode='search',search_storage_rights=True)
            with patch('vibe_job_radar.collection.SafeHTTP') as factory:
                factory.return_value.json.side_effect=FetchError(code,'FAKE-SECRET')
                result=self.finish(s,key='FAKE-SECRET')
                self.assertEqual(factory.return_value.json.call_count,1)
            self.assertNotIn('FAKE-SECRET',json.dumps(result))
            self.assertNotIn('FAKE-SECRET',self.c._path(s['id']).read_text(encoding='utf-8'))

    def test_empty_results_not_market_conclusion(self):
        s=self.start(mode='search',search_storage_rights=True,search_budget=10)
        with patch('vibe_job_radar.collection.SafeHTTP') as factory:
            factory.return_value.json.return_value={'web':{'results':[]}}
            s=self.finish(s,key='not-real')
        self.assertEqual(s['status'],'empty');self.assertFalse(s['complete_market_coverage'])

    def test_interrupted_request_reserves_budget_and_is_not_retried(self):
        s=self.start();s['in_flight']={'queue':'details','index':0};s['detail_attempts']=1;self.c._save(s)
        self.c=Collector(self.w)
        with patch('vibe_job_radar.collection.SiteFetcher') as factory:
            s=self.finish(s);factory.assert_not_called()
        self.assertEqual(s['details'][0]['status'],'interrupted_uncertain');self.assertEqual(s['detail_attempts'],1)

    def test_cross_process_writer_lock(self):
        with writer_lock(self.c.root):
            with self.assertRaises(InputError):self.start()

    def test_url_validation(self):
        for url in ('http://example.org','https://example.org:3000/x','https://user:password@example.org','https://example.org/x?token=x'):
            with self.subTest(url=url),self.assertRaises(ValueError):safe_url(url)
        with self.assertRaises(InputError):self.start(urls='https://unselected.example/job')

    def test_custom_platform_registration_survives_restart(self):
        self.c.register({'key':'example_jobs','label':'Test publisher','domains':'careers.example.org'})
        fresh = Workspace(self.tmp.name)
        collector = Collector(fresh)
        self.assertIn('example_jobs',fresh.config['platforms'])
        s = collector.start({'mode':'urls','roles':['architect'],'platforms':['example_jobs'],
            'urls':'https://careers.example.org/job/1','consent':True,'rights_note':'fixture'})
        self.assertEqual(s['details'][0]['platform'],'example_jobs')

    def test_platform_cannot_override_or_overlap_existing_source(self):
        for data in ({'key':'boss','label':'wrong','domains':'evil.example'},
                     {'key':'new_jobs','label':'wrong','domains':'job.zhipin.com'}):
            with self.assertRaises(InputError):self.c.register(data)

    def test_platform_rejects_invalid_domain(self):
        with self.assertRaises(ValueError):self.c.register({'key':'new_jobs','label':'x','domains':'https://example.org/path'})

    def feed(self,**kw):
        return self.start(mode='feed',endpoint='https://publisher.example/jobs',contract_ref='https://publisher.example/docs',**kw)

    def test_authorized_feed_explicit_fulltext_and_pagination(self):
        record={'title':'架构师','text':'使用 Cursor 开发并进行代码审查。','url':'https://www.zhipin.com/job_detail/feed.html','evidence_level':'full_text'}
        with patch('vibe_job_radar.collection.SafeHTTP') as factory:
            factory.return_value.json.side_effect=[{'jobs':[record],'next_cursor':'page2'},{'jobs':[],'next_cursor':None}]
            s=self.finish(self.feed(),key='FAKE-TOKEN')
            self.assertIn('cursor=page2',factory.return_value.json.call_args_list[1].args[0])
            self.assertEqual(factory.return_value.json.call_args_list[0].kwargs['headers']['Authorization'],'Bearer FAKE-TOKEN')
        self.assertEqual(s['feed_requests'],2);self.assertEqual(self.w.status()['counts']['full_text'],1)
        self.assertNotIn('FAKE-TOKEN',json.dumps(s))

    def test_feed_without_level_stays_snippet(self):
        record={'title':'架构师','text':'使用 Cursor 编程。','url':'https://www.zhipin.com/job_detail/feed.html'}
        with patch('vibe_job_radar.collection.SafeHTTP') as factory:
            factory.return_value.json.return_value={'jobs':[record]};s=self.finish(self.feed())
        self.assertEqual(self.w.status()['counts']['snippet'],1)
        self.assertEqual(self.w.status()['counts']['full_text'],0)

    def test_feed_rejects_synthetic_and_repeated_cursor(self):
        for extra in ({'is_synthetic':True},{'source_mode':'synthetic'}):
            with patch('vibe_job_radar.collection.SafeHTTP') as factory:
                factory.return_value.json.return_value={'jobs':[{'title':'架构师','text':'使用 Cursor','url':'https://www.zhipin.com/job_detail/a.html',**extra}]}
                s=self.finish(self.feed())
            self.assertEqual(s['feed_outcomes'][0]['status'],'invalid_feed_contract')
        with patch('vibe_job_radar.collection.SafeHTTP') as factory:
            factory.return_value.json.return_value={'jobs':[],'next_cursor':'repeat'}
            s=self.finish(self.feed())
        self.assertEqual(s['feed_requests'],2)
        self.assertEqual(s['feed_outcomes'][-1]['status'],'invalid_feed_contract')

    def test_feed_interruption_stops_page(self):
        s=self.feed();s['in_flight']={'queue':'feed_outcomes','index':0};s['feed_outcomes']=[{'status':'requesting'}];s['feed_requests']=1;self.c._save(s)
        self.c=Collector(self.w)
        with patch('vibe_job_radar.collection.SafeHTTP') as factory:
            s=self.finish(s);factory.assert_not_called()
        self.assertEqual(s['feed_requests'],1)


class AdvancedHTTPTests(unittest.TestCase):
    setUp = legacy.HTTPTests.setUp
    tearDown = legacy.HTTPTests.tearDown
    call = legacy.HTTPTests.call
    def test_evidence_endpoints_require_token(self):
        self.assertEqual(self.call('/advanced',authorized=False)[0],200)
        self.assertEqual(self.call('/api/evidence/state',{},authorized=False)[0],403)
        self.assertEqual(self.call('/api/evidence/export',authorized=False)[0],403)
        self.assertEqual(self.call('/api/evidence/state',{})[0],200)

    def test_evidence_and_collection_through_actual_http(self):
        self.assertEqual(self.call('/api/job',capture())[0],200)
        _,_,body=self.call('/api/analyze',{})
        run=json.loads(body)
        _,_,body=self.call('/api/evidence/catalogue',{'run_id':run['id']})
        self.assertTrue(json.loads(body)['rows'])
        code,_,_=self.call('/api/evidence/generate',{'run_id':run['id'],'expected_revision':99})
        self.assertEqual(code,409)
        code,headers,body=self.call('/api/evidence/export')
        self.assertEqual(code,200);self.assertIn('candidate.json',zipfile.ZipFile(io.BytesIO(body)).namelist())
        code,_,body=self.call('/api/collection/start',{'mode':'urls','platforms':['boss'],'roles':['architect'],'urls':'https://www.zhipin.com/job_detail/fixture.html','consent':True,'rights_note':'fixture','permit_platforms':[]})
        self.assertEqual(code,200)
        ident=json.loads(body)['id']
        self.assertEqual(self.call('/api/collection/step',{'id':ident})[0],200)
        self.assertEqual(self.call('/api/collection/status',{'id':'../../secret'})[0],400)
