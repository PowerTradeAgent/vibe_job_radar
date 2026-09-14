"""Local preflight regressions for PR #11 review."""
import os
import tempfile
import unittest
from unittest.mock import patch
from vibe_job_radar.collection import Collector
from vibe_job_radar.collection_guidance import preview
from vibe_job_radar.workspace import Workspace

def values(**overrides):
    return {"mode":"feed", "roles":["time_series"], "platforms":["boss"], "permit_platforms":[], "consent":True, "rights_note":"Artificial unit test", **overrides}

class GuidanceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.workspace=Workspace(self.tmp.name); self.service=Collector(self.workspace)

    def test_non_global_literal_feed_ips_are_rejected_without_network(self):
        for host in ('10.0.0.1', '192.168.1.5', '169.254.169.254', '0.0.0.0', '[::1]', '[fc00::1]', '[fe80::1]'):
            with self.subTest(host=host), patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')):
                result = preview(self.workspace, values(mode='feed', endpoint=f'https://{host}/jobs', contract_ref='https://www.liepin.com/contract'))
                self.assertFalse(result['ready'])
                self.assertIn('endpoint', {e['field'] for e in result['errors']})
        self.assertEqual(self.service.list()['runs'], [])

    def test_public_literal_ip_is_not_rejected_by_local_ip_policy(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            result = preview(self.workspace, values(mode='feed', endpoint='https://8.8.8.8/jobs', contract_ref='https://www.liepin.com/contract'))
        self.assertTrue(result['ready'])  # Structure only: no claim this IP serves jobs.
        self.assertFalse(result['credential_verified'])

    def test_feed_cursor_is_rejected_before_task_creation(self):
        for suffix in ('?cursor=page2', '?cursor=', '?%63ursor=page2', '?page=1&cursor=page2'):
            with self.subTest(suffix=suffix):
                result = preview(self.workspace, values(mode='feed', endpoint='https://www.liepin.com/jobs'+suffix, contract_ref='https://www.liepin.com/contract'))
                self.assertFalse(result['ready'])
                self.assertTrue(any(e['field']=='endpoint' and 'cursor' in e['message'] for e in result['errors']))
        self.assertEqual(self.service.list()['runs'], [])

    def test_feed_contract_cursor_is_only_a_reference(self):
        result = preview(self.workspace, values(mode='feed', endpoint='https://www.liepin.com/jobs?city=shanghai', contract_ref='https://www.liepin.com/contract?cursor=section2'))
        self.assertTrue(result['ready'])
