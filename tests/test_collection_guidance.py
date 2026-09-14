"""No-network, no-write preflight regression tests with artificial inputs."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from vibe_job_radar.collection import Collector
from vibe_job_radar.collection_guidance import preview
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.workbench import LocalServer


def values(**overrides):
    return {"mode": "urls", "roles": ["time_series"], "platforms": ["boss"],
            "permit_platforms": ["boss"], "consent": True, "rights_note": "Unit test only, not market data",
            "urls": "https://www.zhipin.com/job_detail/artificial-case.html", "detail_budget": 1, **overrides}


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(self.tmp.name); self.service = Collector(self.workspace)

    def test_preview_is_network_free_and_does_not_write_any_file(self):
        before = {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        with patch('socket.create_connection', side_effect=AssertionError('forbidden network')):
            result = preview(self.workspace, values())
        after = {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(result['ready']); self.assertEqual(result['external_network_requests'], 0)
        self.assertFalse(result['task_created']); self.assertFalse(self.workspace.db.exists())

    def test_url_mode_needs_no_key_or_feed_fields(self):
        with patch.dict(os.environ, {}, clear=True):
            result = preview(self.workspace, values(api_key='', endpoint='bad', contract_ref='bad', search_budget=None))
        self.assertTrue(result['ready']); self.assertFalse(result['credential_configured'])
        self.assertNotIn('search_budget', result['budgets'])

    def test_missing_fields_return_multiple_actionable_errors(self):
        result = preview(self.workspace, values(urls='', rights_note='', consent=False, roles=[]))
        self.assertFalse(result['ready'])
        self.assertTrue({'urls', 'rights_note', 'consent', 'roles'}.issubset({e['field'] for e in result['errors']}))
        self.assertTrue(all(e['label'] and e['message'] for e in result['errors']))

    def test_placeholder_homepage_localhost_and_plaintext_are_rejected(self):
        for url in ['https://www.zhipin.com/job_detail/REPLACE_WITH_REAL_JOB_ID.html',
                    'https://www.zhipin.com/', 'http://127.0.0.1:53892/advanced',
                    'https://www.zhipin.com/web/geek/jobs', 'https://careers.example.com/test',
                    'https://api.example.invalid/jobs', 'D:/jobs.txt', 'some copied app token']:
            with self.subTest(url=url):
                result = preview(self.workspace, values(urls=url))
                self.assertFalse(result['ready']); self.assertIn('urls', {e['field'] for e in result['errors']})

    def test_detected_platform_does_not_grant_permission(self):
        result = preview(self.workspace, values(platforms=['liepin'], permit_platforms=[]))
        self.assertEqual(result['detected_platforms'], ['boss']); self.assertFalse(result['ready'])
        result = preview(self.workspace, values(permit_platforms=[]))
        self.assertIn('permit_platforms', {e['field'] for e in result['errors']})
        self.assertEqual(self.service.list()['runs'], [])

    def test_duplicate_urls_do_not_inflate_count(self):
        data = values(); data['urls'] += '\n' + data['urls']
        result = preview(self.workspace, data)
        self.assertEqual(result['unique_url_count'], 1)
        self.assertEqual(len(result['url_rows']), 2); self.assertTrue(result['url_rows'][1]['duplicate'])

    def test_search_plan_can_be_previewed_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = preview(self.workspace, values(mode='search', api_key='', search_storage_rights=True, detail_budget=0))
        self.assertFalse(result['ready']); self.assertGreater(result['query_count'], 0)
        self.assertTrue(result['query_preview']); self.assertEqual(result['errors'][0]['field'], 'api_key')

    def test_search_only_zero_details_is_valid_without_permits(self):
        result = preview(self.workspace, values(mode='search', api_key='TEST-NOT-REAL-SECRET',
            search_storage_rights=True, permit_platforms=[], detail_budget=0, search_budget=1, pages=1))
        self.assertTrue(result['ready']); self.assertTrue(result['credential_configured'])
        self.assertFalse(result['credential_verified'])
        self.assertTrue(any('只搜索' in w for w in result['warnings']))
        self.assertNotIn('TEST-NOT-REAL-SECRET', json.dumps(result))

    def test_environment_key_presence_not_returned_or_verified(self):
        secret = 'ENV-SECRET-FOR-TEST-ONLY'
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': secret}):
            result = preview(self.workspace, values(mode='search', search_storage_rights=True, detail_budget=0))
        self.assertTrue(result['ready']); self.assertNotIn(secret, json.dumps(result))
        self.assertFalse(result['credential_verified'])

    def test_search_permissions_not_inferred_from_key_presence(self):
        result = preview(self.workspace, values(mode='search', api_key='TEST', detail_budget=0))
        self.assertIn('search_storage_rights', {e['field'] for e in result['errors']})

    def test_feed_missing_supplier_data_is_explained(self):
        result = preview(self.workspace, values(mode='feed'))
        fields = {e['field'] for e in result['errors']}
        self.assertTrue({'endpoint', 'contract_ref'}.issubset(fields)); self.assertFalse(result['ready'])

    def test_invalid_and_empty_budgets_rejected(self):
        for budget in [None, True, -1, '1', 0]:
            with self.subTest(budget=budget):
                self.assertFalse(preview(self.workspace, values(detail_budget=budget))['ready'])

    def test_query_credential_does_not_leak_through_preview(self):
        result = preview(self.workspace, values(urls='https://www.zhipin.com/job_detail/a.html?token=TOP-SECRET'))
        self.assertFalse(result['ready']); self.assertNotIn('TOP-SECRET', json.dumps(result))

    def test_invalid_types_return_errors_not_exceptions(self):
        for data in [values(mode=[]), values(platforms=None), values(permit_platforms='boss'), values(rights_note={}), values(urls=33)]:
            with self.subTest(data=data): self.assertFalse(preview(self.workspace, data)['ready'])


class GuidanceHTTPTests(unittest.TestCase):
    def test_http_route_is_authenticated_and_side_effect_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = LocalServer(Workspace(tmp)); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                for authorized, expected in [(False, 403), (True, 200)]:
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
                    headers = {'Content-Type': 'application/json'}
                    if authorized: headers['X-Radar-Token'] = server.token
                    conn.request('POST', '/api/collection/preview', json.dumps(values()), headers)
                    response = conn.getresponse(); body = json.loads(response.read()); conn.close()
                    self.assertEqual(response.status, expected)
                    if authorized: self.assertFalse(body['task_created'])
                self.assertEqual(server.collector.list()['runs'], [])
            finally:
                server.shutdown(); server.server_close(); thread.join()
