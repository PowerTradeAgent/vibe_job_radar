"""A second click/rate limit/restart must not turn a hard failure into success."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_local_public as fixtures
from vibe_job_radar.local_public import LocalPublicDataClient
from vibe_job_radar.network import FetchError
from vibe_job_radar.public_cache_guard import CacheFailureGuard
from vibe_job_radar.public_contract import ContractError
from vibe_job_radar.workspace import Workspace, InputError


class LocalFailureReviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.workspace = Workspace(self.folder.name)
        self.now = [time.time()]
        self.transport = Mock()
        self.transport.json.return_value = fixtures.payload()
        self.client = LocalPublicDataClient(self.workspace, transport=self.transport, clock=lambda: self.now[0])
        self.original = self.client.search(fixtures.query(limit=1), consent=True)
        self.old_bytes = self.client.path.read_bytes()
        self.now[0] += 601

    def fail(self, code='encrypted_dns_tls_failed'):
        self.transport.json.side_effect = FetchError(code, 'DO-NOT-PERSIST')
        with self.assertRaisesRegex(FetchError, '^' + code):
            self.client.search(fixtures.query(), consent=True)

    def test_second_click_rate_limit_does_not_mask_tls_failure(self):
        self.fail()
        with self.assertRaisesRegex(FetchError, 'encrypted_dns_tls_failed'):
            self.client.search(fixtures.query(), consent=True)
        self.assertEqual(self.transport.json.call_count, 2)
        self.assertEqual(self.client.path.read_bytes(), self.old_bytes)

    def test_restart_does_not_reset_failure_into_cached_success(self):
        self.fail()
        transport = Mock()
        client = LocalPublicDataClient(Workspace(self.folder.name), transport=transport, clock=lambda: self.now[0])
        with self.assertRaisesRegex(FetchError, 'encrypted_dns_tls_failed'):
            client.search(fixtures.query(), consent=True)
        transport.json.assert_not_called()

    def test_other_query_and_pagination_do_not_hide_same_source_failure(self):
        self.fail('encrypted_dns_non_public_answer')
        for query in (fixtures.query(query='Engineer'), fixtures.query(limit=1, cursor=self.original['response']['next_cursor'])):
            with self.subTest(query=query), self.assertRaisesRegex(FetchError, 'encrypted_dns_non_public_answer'):
                self.client.search(query, consent=True)
        self.assertEqual(self.transport.json.call_count, 2)

    def test_later_temporary_error_cannot_replace_unresolved_hard_failure(self):
        self.fail()
        self.now[0] += 31
        self.transport.json.side_effect = FetchError('network_error')
        with self.assertRaisesRegex(FetchError, 'encrypted_dns_tls_failed'):
            self.client.search(fixtures.query(), consent=True)

    def test_successful_validated_refresh_clears_guard_without_deleting_history(self):
        self.fail()
        self.now[0] += 31
        self.transport.json.side_effect = None
        value = self.client.search(fixtures.query(), consent=True)
        self.assertFalse(value['cache_reused'])
        self.assertIsNone(value['refresh_error'])
        self.assertFalse(self.client.failure_guard.path.exists())
        self.assertTrue(self.client.search(fixtures.query(), consent=True)['cache_reused'])

    def test_invalid_new_body_also_blocks_rate_limit_cache_fallback(self):
        self.transport.json.return_value = {'jobs': [{'id': 1}], 'meta': {'total': 1}}
        with self.assertRaises(ContractError):
            self.client.search(fixtures.query(), consent=True)
        with self.assertRaises(FetchError):
            self.client.search(fixtures.query(), consent=True)
        self.assertEqual(self.client.path.read_bytes(), self.old_bytes)

    def test_explicit_offline_history_keeps_error_and_original_timestamp(self):
        self.fail()
        cached = self.client.cached(fixtures.query())
        self.assertEqual(cached['refresh_error'], 'encrypted_dns_tls_failed')
        self.assertEqual(cached['observed_at'], self.original['observed_at'])
        self.assertTrue(cached['stale'])
        self.assertEqual(self.transport.json.call_count, 2)

    def test_only_fixed_error_code_saved_no_headers_messages_or_query(self):
        self.fail()
        data = json.loads(self.client.failure_guard.path.read_text(encoding='utf-8'))
        self.assertEqual(set(data), {'schema_version', 'code', 'observed_at'})
        self.assertNotIn('DO-NOT-PERSIST', json.dumps(data))
        self.assertNotIn('Architect', json.dumps(data))

    def test_temporary_failure_without_hard_history_still_uses_labelled_cache(self):
        self.transport.json.side_effect = FetchError('encrypted_dns_timeout')
        result = self.client.search(fixtures.query(), consent=True)
        self.assertTrue(result['stale'])
        self.assertEqual(result['refresh_error'], 'encrypted_dns_timeout')
        self.assertFalse(self.client.failure_guard.path.exists())


class FailureGuardStorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.guard = CacheFailureGuard(self.root, 'source')

    def test_constructor_and_absent_read_are_offline_and_side_effect_free(self):
        self.assertIsNone(self.guard.read(100))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_sources_do_not_share_failure_state(self):
        self.guard.record('tls_verification_failed', 100)
        self.assertIsNone(CacheFailureGuard(self.root, 'another').read(100))

    def test_invalid_future_or_secret_state_fails_closed(self):
        for state in ('{bad', json.dumps({'schema_version': 1, 'code': 'http_403', 'observed_at': 101}),
                      json.dumps({'schema_version': True, 'code': 'http_403', 'observed_at': 100}),
                      json.dumps({'schema_version': 1, 'code': 'secret:example', 'observed_at': 100}),
                      'x' * 1025):
            self.guard.path.write_text(state, encoding='utf-8')
            with self.subTest(state=state), self.assertRaises(InputError):
                self.guard.read(100)

    def test_unknown_error_text_is_not_persisted_as_a_code(self):
        self.guard.record('secret-url-password=DO-NOT-PERSIST', 100)
        self.assertEqual(self.guard.read(100), 'public_response_invalid')
        self.assertNotIn('DO-NOT-PERSIST', self.guard.path.read_text(encoding='utf-8'))


class RemoteFailureReviewTests(unittest.TestCase):
    def setUp(self):
        import test_public_hybrid as remote
        from vibe_job_radar.public_data import PublicDataClient
        self.remote = remote
        self.factory = PublicDataClient
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.now = [time.time()]
        self.transport = Mock()
        self.transport.json.return_value = {'schema_version': 1, 'jobs': [remote.job()],
            'next_cursor': '', 'generated_at': '2026-01-02T00:00:00Z'}
        self.client = self.factory(self.root, 'https://service.fixture.test', remote.REGISTRY,
                                   transport=self.transport, clock=lambda: self.now[0])
        self.client.search(remote.query(), consent=True)
        self.now[0] += 601

    def test_remote_hard_failure_survives_rate_limit_and_restart(self):
        self.transport.json.side_effect = FetchError('http_403')
        with self.assertRaisesRegex(FetchError, 'http_403'):
            self.client.search(self.remote.query(), consent=True)
        other_transport = Mock()
        other = self.factory(self.root, 'https://service.fixture.test', self.remote.REGISTRY,
                             transport=other_transport, clock=lambda: self.now[0])
        with self.assertRaisesRegex(FetchError, 'http_403'):
            other.search(self.remote.query(), consent=True)
        other_transport.json.assert_not_called()

    def test_remote_invalid_payload_cannot_become_cached_success(self):
        self.transport.json.return_value = {'secret': 'DO-NOT-PERSIST'}
        with self.assertRaises(ContractError):
            self.client.search(self.remote.query(), consent=True)
        with self.assertRaises(FetchError):
            self.client.search(self.remote.query(), consent=True)
        self.assertNotIn('DO-NOT-PERSIST', self.client.failure_guard.path.read_text(encoding='utf-8'))

    def test_remote_cached_history_exposes_unresolved_failure(self):
        self.transport.json.side_effect = FetchError('tls_verification_failed')
        with self.assertRaises(FetchError):
            self.client.search(self.remote.query(), consent=True)
        self.assertEqual(self.client.cached(self.remote.query())['refresh_error'], 'tls_verification_failed')


if __name__ == '__main__':
    unittest.main()
