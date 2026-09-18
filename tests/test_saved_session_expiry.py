"""Expired synthetic cookies never become a claimed restored login.

No real accounts or network: exercise the on-disk lease and its actual service
factory handoff. Browser and platform validity remain separate from expiry.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from vibe_job_radar.guided.adapters import Registry
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.saved_session import SavedSession, _dpapi
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.workspace import Workspace
from test_guided import fixture_adapter

COOKIE = {
    'name': 'expiry_fixture', 'value': 'SYNTHETIC-NOT-A-REAL-LOGIN',
    'domain': 'jobs.fixture.test', 'path': '/', 'expires': -1,
    'httpOnly': True, 'secure': True, 'sameSite': 'Lax',
}


class CookieExpiryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 2_000_000_000
        self.adapter = fixture_adapter()

    def lease(self):
        lease = SavedSession(self.root, self.adapter, backend='bridge',
                             browser='bundled', network='synthetic-policy',
                             clock=lambda: self.now)
        self.addCleanup(lease.close)
        return lease

    def test_restore_filters_elapsed_cookie_without_extending_survivor(self):
        lease = self.lease()
        short = {**COOKIE, 'name': 'short', 'expires': self.now + 5}
        long = {**COOKIE, 'name': 'long', 'expires': self.now + 500}
        lease.save([short, long, COOKIE])
        lease.close()
        self.now += 10
        restored = self.lease().restore()
        self.assertEqual(restored, {'cookies': [long, COOKIE], 'origins': []})

    def test_all_cookies_expired_removes_snapshot_before_restarting_browser(self):
        lease = self.lease()
        lease.save([{**COOKIE, 'expires': self.now + 5}])
        self.now += 5  # Expiration at exactly now is already expired.
        self.assertIsNone(lease.restore())
        self.assertEqual(lease.status, 'empty')
        self.assertFalse(lease.path.exists())
        self.assertTrue((lease.root / 'fixture.lock').exists())

    def test_empty_browser_snapshot_does_not_keep_previous_login(self):
        lease = self.lease()
        lease.save([COOKIE])
        lease.save([])
        self.assertEqual(lease.status, 'empty')
        self.assertFalse(lease.path.exists())
        self.assertIsNone(lease.restore())

    def test_filtering_all_ineligible_domains_removes_previous_snapshot(self):
        lease = self.lease()
        lease.save([COOKIE])
        lease.save([{**COOKIE, 'domain': 'unrelated.test'}])
        self.assertIsNone(lease.restore())
        self.assertEqual(lease.status, 'empty')

    def test_save_filters_expired_cookie_and_retains_session_cookie(self):
        lease = self.lease()
        lease.save([{**COOKIE, 'name': 'old', 'expires': self.now - 1}, COOKIE])
        self.assertEqual(lease.restore()['cookies'], [COOKIE])

    def test_saving_only_expired_cookies_does_not_create_snapshot(self):
        lease = self.lease()
        lease.save([{**COOKIE, 'expires': 0}])
        self.assertFalse(lease.path.exists())
        self.assertEqual(lease.status, 'empty')

    def test_malformed_negative_expiry_preserves_previous_snapshot(self):
        lease = self.lease()
        lease.save([COOKIE])
        original = lease.path.read_bytes()
        for expiry in (-2, -0.5):
            with self.subTest(expiry=expiry), self.assertRaises(CrawlError):
                lease.save([{**COOKIE, 'expires': expiry}])
            self.assertEqual(lease.path.read_bytes(), original)

    def test_new_normal_login_can_replace_an_expired_snapshot(self):
        lease = self.lease()
        lease.save([{**COOKIE, 'expires': self.now + 1}])
        self.now += 2
        self.assertIsNone(lease.restore())
        renewed = {**COOKIE, 'value': 'RENEWED-SYNTHETIC-ONLY', 'expires': self.now + 60}
        lease.save([renewed])
        self.assertEqual(lease.restore()['cookies'], [renewed])
        self.assertEqual(lease.status, 'restored_unverified')

    def test_empty_legacy_snapshot_is_read_compatibly_but_not_restored(self):
        lease = self.lease()
        lease.save([COOKIE])
        envelope = json.loads(lease.path.read_bytes())
        data = b'[]'
        if envelope['protection'] == 'dpapi':
            data = _dpapi(data, lease.entropy)
        envelope['payload'] = base64.b64encode(data).decode('ascii')
        lease.path.write_text(json.dumps(envelope), encoding='utf-8')
        self.assertIsNone(lease.restore())
        self.assertEqual(lease.status, 'empty')

    def test_cleanup_affects_only_this_platform_snapshot(self):
        lease = self.lease()
        report = self.root / 'report.json'
        report.write_text('{"saved": 1}', encoding='utf-8')
        other = lease.root / 'another.json'
        other.write_text('SYNTHETIC-OTHER-PLATFORM', encoding='utf-8')
        lease.save([{**COOKIE, 'expires': self.now + 1}])
        self.now += 2
        self.assertIsNone(lease.restore())
        self.assertEqual(json.loads(report.read_text(encoding='utf-8')), {'saved': 1})
        self.assertEqual(other.read_text(encoding='utf-8'), 'SYNTHETIC-OTHER-PLATFORM')

    def test_empty_session_does_not_disable_explicit_persistence_preference(self):
        workspace = Workspace(self.root)
        service = GuidedService(workspace, registry=Registry([self.adapter]))
        service._submit = Mock()
        self.addCleanup(service.close)
        task = service.create(dict(platform='fixture', keyword='算法', roles=['time_series'],
                                   consent=True, rights_note='Synthetic only', persist_session=True))
        state = service._load(task['id'])
        lease = service._new_session_lease(state)
        lease.clock = lambda: self.now
        lease.save([{**COOKIE, 'expires': self.now + 1}])
        self.now += 2
        lease.close()
        new_lease = service._new_session_lease
        def lease_at_test_time(task_state):
            current = new_lease(task_state)
            current.clock = lambda: self.now
            return current
        service._new_session_lease = lease_at_test_time
        backend = Mock(startup_report=None)
        service.factory = Mock(return_value=backend)
        service._backend(state)
        try:
            self.assertNotIn('storage_state', service.factory.call_args.kwargs)
            self.assertEqual(state['authentication'], 'not_checked')
            self.assertEqual(state['saved_session_status'], 'empty')
            self.assertTrue(state['persist_session'])
            self.assertNotIn(COOKIE['value'], json.dumps(service.state()))
        finally:
            service._close_backend(state['id'])
