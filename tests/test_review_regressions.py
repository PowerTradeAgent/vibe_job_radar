import json
import tempfile
import unittest
from unittest.mock import patch
from vibe_job_radar.workspace import Workspace, _redact

class ReviewRegressions(unittest.TestCase):
    def test_same_url_less_file_has_stable_identity(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            data = {'filename': 'jobs.json', 'content': json.dumps({'title':'架构师', 'text':'使用 Cursor 编程。'}),
                    'rights_note':'人工测试数据', 'full_text_confirmed':True}
            self.assertEqual(workspace.import_file(data)['new_snapshots'], 1)
            self.assertEqual(workspace.import_file(data)['new_snapshots'], 0)
            self.assertEqual(workspace.status()['counts']['records'], 1)
            self.assertEqual(workspace.analyze({})['manifest']['stats']['deduplicated_groups'], 1)

    def test_redaction_preserves_keys(self):
        self.assertEqual(_redact({'tasks': [{'error':'tasks'}]}, 'tasks'), {'tasks':[{'error':'[REDACTED]'}]})

    def test_placeholder_keys_keep_provider_failure_observable(self):
        for key in ('a','tasks','status','error'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                with patch('vibe_job_radar.discovery.SafeHTTP') as transport:
                    transport.return_value.json.side_effect = ValueError('provider echoed: ' + key)
                    result = Workspace(root).discover({'roles':['architect'], 'platforms':['boss'],
                        'api_key':key, 'consent_search':True, 'max_requests':1})
                self.assertEqual(result['task_statuses']['error'], 1)
                self.assertEqual(len(result['errors']), 1)
                self.assertIn('[REDACTED]',result['errors'][0])
