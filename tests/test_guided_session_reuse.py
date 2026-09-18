"""Explicit same-process browser continuity; no real platform credentials."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
from types import SimpleNamespace

from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.guided.adapters import Registry
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.session_reuse import reuse_current_session
from vibe_job_radar.workspace import Workspace, InputError
from vibe_job_radar.network_policy import current_policy
from test_guided import fixture_adapter


class SessionReuseTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.adapter = fixture_adapter()
        self.service = GuidedService(Workspace(Path(self.tmp.name)),
                                     registry=Registry([self.adapter]))
        self.addCleanup(self.service.close)
        self.service._submit = Mock()
        self.query = dict(platform='fixture', keyword='算法', roles=['time_series'],
                          consent=True, rights_note='人工受控测试')
        old = self.service.create(self.query)
        self.old = self.service._load(old['id'])
        self.service._save(self.old, status='completed', report_id='old-report')
        self.wire = SimpleNamespace(network_policy=current_policy(), progress=Mock())
        self.backend = Mock(adapter=self.adapter, wire=self.wire, error=None,
                            wait_error=None, auth_mode=False,
                            startup_report={'browser_channel': 'bundled'})
        self.backend.alive.return_value = True
        self.service._backends[self.old['id']] = self.backend

    def new(self, **changes):
        item = self.service.create({**self.query, 'reuse_current_session': True, **changes})
        return self.service._load(item['id'])

    def test_old_tasks_do_not_opt_in_implicitly(self):
        item = self.service.create(self.query)
        state = self.service._load(item['id'])
        self.assertFalse(state['reuse_current_session'])
        self.assertFalse(reuse_current_session(self.service, state))
        self.assertIn(self.old['id'], self.service._backends)

    def test_true_boolean_required(self):
        for value in ('yes', 1, None, []):
            with self.subTest(value=value), self.assertRaises(InputError):
                self.new(reuse_current_session=value)

    def test_new_query_keeps_browser_without_reading_or_copying_secrets(self):
        state = self.new(keyword='架构师')
        self.assertTrue(reuse_current_session(self.service, state))
        self.assertIs(self.service._backends[state['id']], self.backend)
        self.assertNotIn(self.old['id'], self.service._backends)
        self.backend.open.assert_not_called(); self.backend.close.assert_not_called()
        self.backend.context.cookies.assert_not_called()
        self.backend.context.storage_state.assert_not_called()
        self.assertEqual(state['authentication'], 'reused_session_unverified')
        self.assertEqual(self.service._load(self.old['id'])['report_id'], 'old-report')
        self.assertEqual(state['cards'], [])

    def test_progress_belongs_to_new_task_not_previous_report(self):
        state = self.new(); reuse_current_session(self.service, state)
        self.wire.progress('rate_wait', 3)
        self.assertEqual(self.service._load(state['id'])['wait_seconds'], 3)
        self.assertNotIn('wait_seconds', self.service._load(self.old['id']))

    def test_service_backend_reuses_and_rebinds_without_factory_call(self):
        state = self.new(); self.service.factory = Mock()
        self.assertIs(self.service._backend(state), self.backend)
        self.service.factory.assert_not_called()
        self.backend.bind_diagnostics.assert_called_once_with(None)

    def test_no_existing_browser_starts_normal_fresh_path(self):
        self.service._backends.clear()
        self.assertFalse(reuse_current_session(self.service, self.new()))

    def test_denied_challenged_and_waiting_sessions_do_not_reopen(self):
        for status in ('waiting_manual', 'waiting_rate', 'paused', 'running', 'stopped'):
            with self.subTest(status=status):
                self.service._save(self.old, status=status)
                state = self.new()
                with self.assertRaises(CrawlError) as raised:
                    reuse_current_session(self.service, state)
                self.assertEqual(raised.exception.code, 'session_reuse_unavailable')
                self.assertIn(self.old['id'], self.service._backends)
                self.backend.close.assert_not_called()

    def test_active_login_is_not_stolen(self):
        self.backend.auth_mode = True
        with self.assertRaises(CrawlError): reuse_current_session(self.service, self.new())

    def test_platform_and_backend_must_match(self):
        state = self.new()
        for key, value in (('platform', 'different'), ('backend', 'native')):
            with self.subTest(key=key), self.assertRaises(CrawlError) as raised:
                reuse_current_session(self.service, {**state, key:value})
            self.assertEqual(raised.exception.code, 'session_reuse_incompatible')

    def test_browser_or_network_change_does_not_share_identity(self):
        state = self.new()
        self.backend.startup_report['browser_channel'] = 'msedge'
        with self.assertRaises(CrawlError): reuse_current_session(self.service, state)
        self.backend.startup_report['browser_channel'] = 'bundled'
        self.wire.network_policy = SimpleNamespace(fingerprint='different')
        with self.assertRaises(CrawlError): reuse_current_session(self.service, state)

    def test_unknown_or_dead_browser_is_not_claimed_reusable(self):
        self.backend.alive.return_value = False
        with self.assertRaises(CrawlError): reuse_current_session(self.service, self.new())

    def test_terminal_errors_cannot_be_cleared_by_creating_new_query(self):
        for code in ('http_403', 'http_429', 'native_surface_unsupported', 'manual_required'):
            self.backend.error = code
            with self.subTest(code=code), self.assertRaises(CrawlError):
                reuse_current_session(self.service, self.new())

    def test_idle_pause_can_reuse_without_resetting_shared_ledger(self):
        state = self.new(); ledger = self.service.ledger
        self.backend.error = 'paused'
        self.assertTrue(reuse_current_session(self.service, state))
        self.assertIs(self.service.ledger, ledger)
        self.assertEqual(self.backend.error, 'paused')  # normal open() owns reset

    def test_existing_and_partially_collected_tasks_are_not_grafted(self):
        state = self.new()
        for changes in ({'phase':'collect'}, {'cards':[{'id':'saved'}]}, {'pages_seen':['p1']}):
            self.assertFalse(reuse_current_session(self.service, {**state, **changes}))

    def test_new_owner_stop_closes_actual_browser_old_task_does_not(self):
        state = self.new(); reuse_current_session(self.service, state)
        self.service._run('close', self.old, None)
        self.backend.close.assert_not_called()
        self.service._run('close', state, None)
        self.backend.close.assert_called_once()

    def test_another_workspace_has_no_access_to_live_browser(self):
        with TemporaryDirectory() as directory:
            other = GuidedService(Workspace(Path(directory)), registry=Registry([self.adapter]))
            try:
                other._submit = Mock()
                item = other.create({**self.query,'reuse_current_session':True})
                self.assertFalse(reuse_current_session(other,other._load(item['id'])))
            finally: other.close()

    def test_task_display_counts_reset_but_ledger_does_not(self):
        self.backend.native_counts = {'document': 4, 'business': 6}
        state = self.new()
        before = self.service.ledger.path.read_bytes()
        reuse_current_session(self.service, state)
        self.assertEqual(self.backend.native_counts, {'document':0, 'business':0})
        self.assertEqual(self.service.ledger.path.read_bytes(), before)
