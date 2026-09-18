"""Synthetic cookie snapshots only; no recruitment accounts or network calls."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from vibe_job_radar.guided.saved_session import SavedSession, MAX_AGE
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.guided.adapters import Registry
from vibe_job_radar.network_policy import current_policy
from vibe_job_radar.workspace import Workspace, InputError
from test_guided import fixture_adapter

COOKIE = dict(name='fixture_session', value='SYNTHETIC-COOKIE-NOT-A-REAL-SECRET',
              domain='jobs.fixture.test', path='/', expires=-1,
              httpOnly=True, secure=True, sameSite='Lax')


class SavedSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 2000000000

    def lease(self, **kwargs):
        lease = SavedSession(self.root, fixture_adapter(), backend=kwargs.get('backend', 'bridge'),
                             browser='bundled', network='fixture-network', clock=lambda: self.now)
        self.addCleanup(lease.close)
        return lease

    def test_missing_snapshot_is_not_authenticated(self):
        s = self.lease(); self.assertIsNone(s.restore()); self.assertEqual(s.status, 'empty')

    def test_round_trip_closes_and_reopens_without_raw_public_state(self):
        s = self.lease(); s.save([COOKIE]); s.close()
        s = self.lease(); self.assertEqual(s.restore(), {'cookies': [COOKIE], 'origins': []})
        self.assertEqual(s.status, 'restored_unverified')

    def test_other_domains_and_partitioned_cookies_are_not_copied(self):
        s = self.lease()
        s.save([COOKIE, {**COOKIE, 'domain': 'unrelated.test'}, {**COOKIE, 'partitionKey': 'https://unrelated.test'}])
        self.assertEqual(s.restore()['cookies'], [COOKIE])

    def test_malformed_cookie_fails_without_exposing_value(self):
        for bad in ({}, {**COOKIE, 'httpOnly': 'true'}, {**COOKIE, 'expires': float('nan')},
                    {**COOKIE, 'sameSite': 'unknown'}, {**COOKIE, 'path': 'bad'}):
            s = self.lease()
            with self.assertRaises(CrawlError) as exc: s.save([bad])
            self.assertNotIn(COOKIE['value'], str(exc.exception)); s.close()

    def test_expired_snapshot_requires_normal_login(self):
        s = self.lease(); s.save([COOKIE]); self.now += MAX_AGE + 1
        self.assertIsNone(s.restore()); self.assertEqual(s.status, 'expired')

    def test_future_timestamp_is_invalid(self):
        s = self.lease(); s.save([COOKIE]); self.now -= 61
        with self.assertRaises(CrawlError): s.restore()

    def test_backend_mismatch_is_not_silent_anonymous_fallback(self):
        s = self.lease(); s.save([COOKIE]); s.close(); s = self.lease(backend='native')
        with self.assertRaises(CrawlError) as exc: s.restore()
        self.assertEqual(exc.exception.code, 'saved_session_incompatible')

    def test_binding_detects_cross_workspace_copy(self):
        s = self.lease(); s.save([COOKIE]); s.close()
        content = s.path.read_bytes()
        with tempfile.TemporaryDirectory() as other:
            lease = SavedSession(Path(other), fixture_adapter(), backend='bridge',
                                 browser='bundled', network='fixture-network', clock=lambda: self.now)
            try:
                lease.save([COOKIE]); lease.path.write_bytes(content)
                with self.assertRaises(CrawlError): lease.restore()
            finally: lease.close()

    def test_corruption_is_not_returned_or_ignored(self):
        s = self.lease(); s.save([COOKIE]); s.path.write_bytes(b'not-json')
        with self.assertRaises(CrawlError) as exc: s.restore()
        self.assertEqual(exc.exception.code, 'saved_session_unreadable')
        s.forget(); self.assertIsNone(s.restore())

    def test_payload_tampering_is_rejected(self):
        s = self.lease(); s.save([COOKIE]); data = json.loads(s.path.read_bytes())
        data['payload'] = '###invalid###'; s.path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(CrawlError): s.restore()

    def test_size_bound(self):
        s = self.lease(); s.save([COOKIE]); s.path.write_bytes(b' ' * 2000001)
        with self.assertRaises(CrawlError): s.restore()

    def test_no_partial_files_remain(self):
        s = self.lease(); s.save([COOKIE]); s.save([{**COOKIE, 'value': 'updated-synthetic'}])
        self.assertEqual(s.restore()['cookies'][0]['value'], 'updated-synthetic')
        self.assertFalse(list(s.root.glob('.session-*')))

    def test_platform_key_cannot_be_a_path(self):
        adapter = SimpleNamespace(key='../outside')
        with self.assertRaises(CrawlError):
            SavedSession(self.root, adapter, backend='bridge', browser='bundled', network='test')

    def test_close_is_idempotent_and_ends_write_authority(self):
        s = self.lease(); s.close(); s.close()
        with self.assertRaises(CrawlError): s.save([COOKIE])

    def test_permissions_or_dpapi_protect_the_snapshot(self):
        s = self.lease(); s.save([COOKIE])
        if os.name == 'nt':
            envelope = json.loads(s.path.read_bytes())
            self.assertEqual(envelope['protection'], 'dpapi')
            self.assertNotIn(COOKIE['value'].encode(), base64.b64decode(envelope['payload']))
        else:
            self.assertEqual(s.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(s.path.stat().st_mode & 0o777, 0o600)
            s.path.chmod(0o644)
            with self.assertRaises(CrawlError): s.restore()

    def test_links_cannot_replace_private_storage(self):
        s = self.lease(); s.save([COOKIE])
        if os.name != 'nt':
            other = self.root / 'outside'; other.write_text('untouched', encoding='utf-8')
            s.path.unlink(); s.path.symlink_to(other)
            for action in (s.restore, lambda: s.save([COOKIE]), s.forget):
                with self.assertRaises(CrawlError): action()
            self.assertEqual(other.read_text(encoding='utf-8'), 'untouched')
        else:
            # The common reparse-point rejection uses Windows lstat attributes;
            # symlink creation itself may require admin/developer-mode rights.
            from unittest.mock import patch
            from vibe_job_radar.guided.saved_session import _safe_file
            info = SimpleNamespace(st_mode=0o100600, st_file_attributes=1024, st_nlink=1)
            with patch.object(Path, 'lstat', return_value=info), self.assertRaises(CrawlError):
                _safe_file(s.path)

    def test_live_os_lock_and_process_restart(self):
        s = self.lease(); s.save([COOKIE])
        code = '''import sys
from pathlib import Path
from vibe_job_radar.guided.saved_session import SavedSession
from vibe_job_radar.guided.contracts import CrawlError
from test_guided import fixture_adapter
try:
 s=SavedSession(Path(sys.argv[1]),fixture_adapter(),backend='bridge',browser='bundled',network='fixture-network',clock=lambda:2000000000)
except CrawlError as e:
 assert e.code=='saved_session_busy'; print('locked')
else:
 assert len(s.restore()['cookies'])==1; s.close(); print('restored')
'''
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1]/'src'), str(Path(__file__).resolve().parent)]))
        def child():
            return subprocess.run([sys.executable, '-c', code, str(self.root)], env=env,
                                  capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        self.assertEqual(child(), 'locked'); s.close(); self.assertEqual(child(), 'restored')

    def test_private_session_is_not_ingested_as_job_data(self):
        from vibe_job_radar.ingest import iter_items
        s=self.lease(); s.save([COOKIE])
        self.assertIn('private browser state', str(list(iter_items(s.path))[0][1]))
        rows=list(iter_items(self.root))
        self.assertTrue(all('.radar-sessions' not in ref for ref, _ in rows))

    def test_private_state_is_rejected_from_source_packaging(self):
        from vibe_job_radar.qualification import source_files
        private=self.root/'src'/'.radar-sessions'; private.mkdir(parents=True)
        (private/'fixture.json').write_text('SYNTHETIC-ONLY', encoding='utf-8')
        with self.assertRaises(ValueError): source_files(self.root)


class SessionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.adapter = fixture_adapter()
        self.service = GuidedService(self.workspace, registry=Registry([self.adapter]))
        self.service._submit = Mock()
        self.addCleanup(self.service.close)
        self.query = dict(platform='fixture', keyword='算法', roles=['time_series'],
                          consent=True, rights_note='Synthetic test only')

    def state(self, **kwargs):
        job = self.service.create({**self.query, **kwargs})
        return self.service._load(job['id'])

    def backend(self, state):
        backend = SimpleNamespace(adapter=self.adapter, error=None, auth_mode=False,
                                  export_session_cookies=Mock(return_value=[COOKIE]), close=Mock())
        self.service.factory = Mock(return_value=backend)
        self.service._backend(state)
        self.addCleanup(lambda: self.service._close_backend(state['id']))
        return backend

    def test_opt_in_is_strict_and_default_has_no_secret_directory(self):
        state = self.state(); self.backend(state)
        self.assertFalse(state['persist_session'])
        self.assertFalse((self.workspace.root/'.radar-sessions').exists())
        for value in ('true', 1, [], None):
            with self.subTest(value=value), self.assertRaises(InputError): self.state(persist_session=value)

    def test_restore_is_passed_to_factory_before_any_navigation(self):
        state = self.state(persist_session=True)
        lease = self.service._new_session_lease(state); lease.save([COOKIE]); lease.close()
        backend = self.backend(state)
        self.assertEqual(self.service.factory.call_args.kwargs['storage_state']['cookies'], [COOKIE])
        self.assertEqual(state['authentication'], 'restored_session_unverified')
        backend.export_session_cookies.assert_not_called()

    def test_checkpoint_saves_only_after_content_ready(self):
        state = self.state(persist_session=True); backend = self.backend(state)
        state.update(status='waiting_manual', cards=[{'id':'fixture'}]); self.service._checkpoint_session(state)
        backend.export_session_cookies.assert_not_called()
        state['status']='ready'; self.service._checkpoint_session(state)
        self.assertEqual(state['saved_session_status'], 'saved_unverified')
        self.assertNotIn(COOKIE['value'], json.dumps(self.service.state()))
        self.assertNotIn(COOKIE['value'], self.service._path(state['id']).read_text(encoding='utf-8'))

    def test_empty_cards_authentication_errors_and_cancellation_do_not_snapshot(self):
        state = self.state(persist_session=True); backend = self.backend(state)
        state.update(status='ready', cards=[]); self.service._checkpoint_session(state)
        state['cards']=[{'id':'fixture'}]; backend.auth_mode=True; self.service._checkpoint_session(state)
        backend.auth_mode=False; backend.error='http_403'; self.service._checkpoint_session(state)
        backend.error=None; self.service._cancel.set(); self.service._checkpoint_session(state)
        backend.export_session_cookies.assert_not_called()

    def test_snapshot_failure_preserves_successful_report(self):
        state=self.state(persist_session=True); backend=self.backend(state)
        state.update(status='completed', cards=[{'id':'fixture'}], report_id='kept-report')
        backend.export_session_cookies.side_effect=RuntimeError('DO-NOT-EXPOSE')
        self.service._checkpoint_session(state)
        self.assertEqual(state['status'],'completed'); self.assertEqual(state['report_id'],'kept-report')
        self.assertEqual(state['saved_session_status'],'save_failed')
        self.assertNotIn('DO-NOT-EXPOSE', self.service._path(state['id']).read_text(encoding='utf-8'))

    def test_forget_revokes_old_tasks_and_retains_reports(self):
        state=self.state(persist_session=True); backend=self.backend(state)
        state.update(status='ready',cards=[{'id':'fixture'}]); self.service._checkpoint_session(state)
        older=self.state(persist_session=True); self.service._save(older,report_id='historical-report')
        with self.assertRaises(InputError): self.service.action({'id':state['id'],'action':'forget_session'})
        self.service._forget_session(state)
        backend.close.assert_called_once()
        self.assertFalse((self.workspace.root/'.radar-sessions/fixture.json').exists())
        old=self.service._load(older['id']); self.assertFalse(old['persist_session'])
        self.assertEqual(old['report_id'],'historical-report')
        self.assertTrue((self.service.root/'rates.sqlite').exists())

    def test_factory_failure_releases_os_lock(self):
        state=self.state(persist_session=True); self.service.factory=Mock(side_effect=RuntimeError('startup'))
        with self.assertRaises(RuntimeError): self.service._backend(state)
        lease=self.service._new_session_lease(state); lease.close()

    def test_close_backend_releases_lease_even_on_browser_error(self):
        state=self.state(persist_session=True); backend=self.backend(state)
        backend.close.side_effect=RuntimeError('close failed')
        with self.assertRaises(RuntimeError): self.service._close_backend(state['id'])
        lease=self.service._new_session_lease(state); lease.close()
