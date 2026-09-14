"""Review regressions: frozen contracts, task-specific credentials, and URL secrets."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vibe_job_radar import collection
from vibe_job_radar.collection import Collector, safe_url
from vibe_job_radar.evidence_ui import EvidenceService
from vibe_job_radar.extract import RuleExtractor
from vibe_job_radar.workspace import InputError, Workspace
import test_advanced as helpers
from test_workbench import capture


class SnapshotRegressions(unittest.TestCase):
    setUp = helpers.EvidenceTests.setUp
    upload = helpers.EvidenceTests.upload
    data = helpers.EvidenceTests.data

    def test_new_current_capability_cannot_be_saved_against_old_report(self):
        self.w.config['capabilities']['new_cap'] = copy.deepcopy(self.w.config['capabilities'][self.row['capability']])
        with self.assertRaises(InputError):
            self.e.save(self.data(capabilities=['new_cap'], requirement_ids=[]))
        self.assertEqual(self.e.state()['revision'], 0)

    def test_catalogue_exposes_frozen_not_current_capability_labels(self):
        cap = self.row['capability']
        before = self.e.catalogue({'run_id': self.run['id']})['capabilities'][cap]
        self.w.config['capabilities'][cap]['label'] = 'CHANGED CURRENT LABEL'
        result = self.e.catalogue({'run_id': self.run['id']})
        self.assertEqual(result['capabilities'][cap], before)

    def test_removed_current_capability_still_validates_against_own_snapshot(self):
        self.w.config['capabilities'].pop(self.row['capability'])
        self.e.save(self.data())
        result = self.e.generate({'run_id': self.run['id'], 'expected_revision': 1})
        self.assertIn('40.00%', result['descriptions'])

    def test_other_report_capabilities_do_not_contaminate_personal_generation(self):
        self.e.save(self.data())
        self.w.config['capabilities']['new_cap'] = copy.deepcopy(self.w.config['capabilities'][self.row['capability']])
        new_run = self.w.analyze({})
        data = self.data(run_id=new_run['id'], project='Other report project', capabilities=['new_cap'], requirement_ids=[])
        self.e.save(data)
        report = self.e.generate({'run_id': self.run['id'], 'expected_revision': 2})
        self.assertNotIn('Other report project', report['descriptions'])
        self.assertIn('Fixture Project', report['descriptions'])
        audit = json.loads(self.w.report_file(report['id'],'evidence_revision.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['excluded_other_report_evidence_count'], 1)

    def test_changed_engine_version_requires_explicit_remapping(self):
        self.e.save(self.data())
        before = set((self.w.root/'reports').iterdir())
        with patch.object(RuleExtractor, 'version', 'rules-new-incompatible'):
            with self.assertRaisesRegex(InputError, '版本'):
                self.e.generate({'run_id': self.run['id'], 'expected_revision': 1})
        self.assertEqual(set((self.w.root/'reports').iterdir()), before)

    def test_unversioned_semantic_drift_cannot_orphan_saved_mappings(self):
        self.e.save(self.data())
        before = set((self.w.root/'reports').iterdir())
        original = RuleExtractor.extract
        def changed(extractor, *args, **kwargs):
            rows = original(extractor, *args, **kwargs)
            for row in rows:
                row.strength = 'preferred' if row.strength != 'preferred' else 'required'
            return rows
        with patch.object(RuleExtractor, 'extract', changed):
            with self.assertRaisesRegex(InputError, '冻结'):
                self.e.generate({'run_id': self.run['id'], 'expected_revision': 1})
        self.assertEqual(set((self.w.root/'reports').iterdir()), before)
        self.assertEqual(self.e.state()['revision'], 1)

    def test_signed_external_evidence_reference_rejected_before_save(self):
        for parameter in ('key','signature','sig','X-Amz-Credential'):
            with self.subTest(parameter=parameter), self.assertRaises(InputError):
                self.e.save(self.data(artifact_id='',external_url=f'https://example.org/proof?{parameter}=SECRET'))
        self.assertEqual(self.e.state()['revision'], 0)


class CredentialRegressions(unittest.TestCase):
    def test_cli_selects_credentials_by_task_mode(self):
        for mode, expected in (('search','BRAVE-ONLY'), ('feed','FEED-ONLY'), ('urls','')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                with patch.object(collection, 'Collector') as factory, patch.dict('os.environ', {
                    'BRAVE_SEARCH_API_KEY':'BRAVE-ONLY', 'RADAR_FEED_TOKEN':'FEED-ONLY'}), patch('builtins.print'):
                    factory.return_value.status.return_value = {'mode':mode}
                    factory.return_value.step.return_value = {'id':'a'*32,'status':'completed','phase':'report',
                                                             'search_requests':0,'detail_attempts':0,'feed_requests':0}
                    self.assertEqual(collection.main(['--workspace',root,'--run-id','a'*32]),0)
                    self.assertEqual(factory.return_value.step.call_args.args[0]['api_key'], expected)

    def test_common_credential_query_names_rejected(self):
        for name in ('key','sig','signature','auth','code','ticket','api_key','X-Amz-Credential','X-Amz-Security-Token','subscriptionKey'):
            with self.subTest(name=name), self.assertRaises(InputError):
                safe_url(f'https://example.org/jobs?{name}=VERY_SECRET')
        self.assertIn('jobId=123', safe_url('https://example.org/jobs?jobId=123&page=2&keyword=architect'))

    def test_credential_endpoint_or_contract_is_never_persisted(self):
        for field in ('endpoint','contract_ref'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                c = Collector(Workspace(root))
                data = {'mode':'feed', 'roles':['architect'],'platforms':['boss'],'rights_note':'test',
                        'consent':True,'endpoint':'https://publisher.example/jobs','contract_ref':'https://publisher.example/docs'}
                data[field] += '?signature=VERY_SECRET'
                with self.assertRaises(InputError): c.start(data)
                self.assertEqual(list(c.root.glob('*.json')), [])


if __name__=='__main__': unittest.main()
