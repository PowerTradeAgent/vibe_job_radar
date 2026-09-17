"""Artificial TLS errors through the real diagnostic facade, with no remote access."""
from __future__ import annotations

import json
import socket
import ssl
import tempfile
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vibe_job_radar.dns_wire import ResolutionError
from vibe_job_radar.encrypted_dns import PublicResolver, BOOTSTRAP
from vibe_job_radar.network_policy import NetworkPolicy
from vibe_job_radar.tls_diagnostic import failure_details
from vibe_job_radar.workspace import Workspace
from vibe_job_radar.guided.service import GuidedService
from test_encrypted_dns import fake_answers, HOST


def certificate_error(code=20):
    exc = ssl.SSLCertVerificationError(1, 'PRIVATE exception /username Cookie:SECRET')
    exc.verify_code = code
    exc.verify_message = 'PRIVATE hostname and path must not be copied'
    exc.reason = 'CERTIFICATE_VERIFY_FAILED'
    exc.library = 'SSL'
    return exc


class TLSFactsTests(unittest.TestCase):
    def test_certificate_error_keeps_number_but_not_raw_message(self):
        d = failure_details(certificate_error(), phase='tls_handshake')
        self.assertEqual(d['category'], 'certificate_verification')
        self.assertEqual(d['verify_code'], 20)
        self.assertEqual(d['verification_reason'], 'issuer_unavailable')
        self.assertEqual(d['ssl_reason'], 'CERTIFICATE_VERIFY_FAILED')
        self.assertFalse(d['cause_confirmed'])
        self.assertFalse(d['dns_request_attempted'])
        self.assertNotIn('PRIVATE', json.dumps(d))
        self.assertNotIn('SECRET', json.dumps(d))

    def test_expired_hostname_self_signed_and_unknown_not_conflated(self):
        for code, reason in [(10,'expired'), (9,'not_yet_valid'), (62,'hostname_mismatch'),
                             (18,'self_signed_leaf'), (19,'self_signed_chain'), (4000,'other_certificate_error')]:
            self.assertEqual(failure_details(certificate_error(code), phase='tls_handshake')['verification_reason'], reason)

    def test_eof_is_not_reported_as_certificate_validation(self):
        d = failure_details(ssl.SSLEOFError(8,'PRIVATE'), phase='tls_handshake')
        self.assertEqual(d['category'],'peer_closed')
        self.assertNotIn('verify_code',d)

    def test_generic_eof_reason_and_clean_close_are_peer_closure(self):
        e=ssl.SSLError(1,'hidden'); e.reason='UNEXPECTED_EOF_WHILE_READING'
        for exc in (e,ssl.SSLZeroReturnError(6,'hidden')):
            self.assertEqual(failure_details(exc,phase='response_body')['category'],'peer_closed')

    def test_wrong_protocol_does_not_say_ca_missing(self):
        e=ssl.SSLError(1,'hidden');e.reason='WRONG_VERSION_NUMBER'
        self.assertEqual(failure_details(e,phase='tls_handshake')['category'],'protocol_mismatch')

    def test_other_handshake_reason_stays_unspecified(self):
        e=ssl.SSLError(1,'hidden');e.reason='TLSV1_ALERT_INTERNAL_ERROR'
        d=failure_details(e,phase='tls_handshake')
        self.assertEqual(d['category'],'tls_protocol');self.assertEqual(d['ssl_reason'],e.reason)

    def test_malformed_symbols_and_numeric_values_are_not_exported(self):
        e=certificate_error();e.reason='Cookie: secret';e.library='C:\\Users\\secret';e.verify_code=True
        d=failure_details(e,phase='unsafe arbitrary phase')
        self.assertNotIn('ssl_reason',d);self.assertNotIn('ssl_library',d);self.assertNotIn('verify_code',d)
        self.assertEqual(d['phase'],'unknown')

    def test_ca_environment_paths_are_only_presence_flags(self):
        with patch.dict('os.environ', {'SSL_CERT_FILE':'C:/private/user.pem','SSL_CERT_DIR':'secret-dir'}):
            d=failure_details(certificate_error(),phase='tls_context')
        self.assertTrue(d['ssl_cert_file_configured']);self.assertTrue(d['ssl_cert_dir_configured'])
        self.assertNotIn('private',json.dumps(d));self.assertNotIn('secret-dir',json.dumps(d))

    def test_context_summary_is_read_only_and_dials_are_fixed_bootstrap_only(self):
        context=ssl.create_default_context()
        conn=SimpleNamespace(_context=context,connection_attempts=[
            {'ip':BOOTSTRAP[0],'phase':'tls','outcome':'tls_rejected','header':'SECRET'},
            {'ip':'10.0.0.1','phase':'tls','outcome':'tls_rejected'}])
        old=(context.check_hostname,context.verify_mode,context.verify_flags)
        d=failure_details(certificate_error(),phase='tls_handshake',connection=conn,bootstrap=BOOTSTRAP)
        self.assertEqual(d['tls_policy']['verify_mode'],'CERT_REQUIRED')
        self.assertTrue(d['tls_policy']['check_hostname'])
        self.assertEqual(d['trust_store_counts'],context.cert_store_stats())
        self.assertEqual((context.check_hostname,context.verify_mode,context.verify_flags),old)
        self.assertEqual(len(d['connection_attempts']),1)
        self.assertNotIn('SECRET',json.dumps(d));self.assertNotIn('10.0.0.1',json.dumps(d))

    def test_error_after_handshake_does_not_say_no_dns_attempt(self):
        d=failure_details(ssl.SSLEOFError(8,'hidden'),phase='response_headers')
        self.assertTrue(d['tls_handshake_completed']);self.assertTrue(d['dns_request_attempted'])

    def test_no_detail_for_non_tls_error(self):
        with self.assertRaises(TypeError):failure_details(OSError('hidden'),phase='tls_handshake')


class ResolverTLSPropagationTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.0
        self.resolver=PublicResolver(clock=lambda:self.clock)
        self.policy=NetworkPolicy(encrypted_dns=True,resolver=self.resolver)
        self.conn=Mock();self.conn.connection_attempts=[]
        self.conn.connect.side_effect=certificate_error()
        self.factory=patch('vibe_job_radar.network.PinnedHTTPSConnection',return_value=self.conn).start()
        self.dns=patch('socket.getaddrinfo',return_value=fake_answers('198.18.1.244')).start()
        self.addCleanup(patch.stopall)

    def fail(self, policy=None):
        with self.assertRaises(ResolutionError) as caught:
            self.resolver.resolve(HOST,policy or self.policy)
        return caught.exception

    def test_original_exchange_exports_hard_failure_and_no_post_or_fallback(self):
        e=self.fail()
        self.assertEqual(e.code,'encrypted_dns_tls_failed')
        self.assertEqual(e.diagnostic['verify_code'],20)
        self.assertEqual(e.diagnostic['endpoint_host'],'cloudflare-dns.com')
        self.assertFalse(e.diagnostic['dns_request_attempted'])
        self.conn.request.assert_not_called();self.conn.close.assert_called_once()
        self.factory.assert_called_once();self.assertEqual(self.resolver._cache,{})

    def test_repeated_click_keeps_original_evidence_and_budget_without_reconnect(self):
        first=self.fail();second=self.fail()
        self.assertEqual(first.diagnostic['observed_at'],second.diagnostic['observed_at'])
        self.assertFalse(first.diagnostic['reused_failure']);self.assertTrue(second.diagnostic['reused_failure'])
        self.assertTrue(second.diagnostic['matches_current_policy'])
        self.assertEqual(second.code,'encrypted_dns_tls_failed')
        self.factory.assert_called_once();self.assertEqual(len(self.resolver._requests),1)

    def test_mutating_one_failure_does_not_corrupt_saved_evidence(self):
        e=self.fail();e.diagnostic['verify_code']=999
        self.assertEqual(self.fail().diagnostic['verify_code'],20)

    def test_changed_policy_marks_previous_failure_instead_of_fake_new_probe(self):
        self.fail()
        policy=replace(self.policy,source='explicit_application')
        self.assertNotEqual(policy.fingerprint,self.policy.fingerprint)
        e=self.fail(policy)
        self.assertTrue(e.diagnostic['reused_failure']);self.assertFalse(e.diagnostic['matches_current_policy'])
        self.factory.assert_called_once()

    def test_clear_drops_details_but_does_not_reset_limit_or_hard_reason(self):
        self.fail();self.resolver.clear();e=self.fail()
        self.assertIsNone(e.diagnostic);self.assertEqual(e.code,'encrypted_dns_tls_failed')
        self.factory.assert_called_once();self.assertEqual(len(self.resolver._requests),1)

    def test_after_existing_cooldown_next_confirmed_check_gets_new_error(self):
        self.fail();self.clock+=31;self.conn.connect.side_effect=ssl.SSLEOFError(8,'hidden')
        e=self.fail();self.assertEqual(e.diagnostic['category'],'peer_closed')
        self.assertFalse(e.diagnostic['reused_failure']);self.assertEqual(self.factory.call_count,2)

    def test_revocation_and_cancellation_never_query_or_export_old_tls(self):
        self.fail();self.resolver.permission=lambda:False
        e=self.fail();self.assertEqual(e.code,'encrypted_dns_disabled');self.assertIsNone(e.diagnostic)
        cancelled=threading.Event();cancelled.set()
        with self.assertRaisesRegex(ResolutionError,'paused'):self.resolver.resolve(HOST,self.policy,cancelled=cancelled)
        self.factory.assert_called_once()

    def test_context_creation_and_response_read_failures_preserve_phase(self):
        self.factory.side_effect=certificate_error()
        e=self.fail();self.assertEqual(e.diagnostic['phase'],'tls_context')
        self.clock+=31;self.factory.side_effect=None;self.conn.connect.side_effect=None
        self.conn.getresponse.side_effect=ssl.SSLEOFError(8,'hidden')
        e=self.fail();self.assertEqual(e.diagnostic['phase'],'response_headers')
        self.assertTrue(e.diagnostic['dns_request_attempted'])


class WorkspaceDiagnosticTests(unittest.TestCase):
    def test_existing_endpoint_facade_returns_details_without_target_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workspace(tmp);s=GuidedService(w)
            try:
                w.network_preferences({'mode':'fake_ip_doh','revision':0,'consent':True})
                conn=Mock();conn.connection_attempts=[];conn.connect.side_effect=certificate_error()
                with patch('socket.getaddrinfo',return_value=fake_answers('198.18.1.244')), \
                     patch('vibe_job_radar.network_policy.NetworkPolicy.capture',return_value=NetworkPolicy()), \
                     patch('vibe_job_radar.network.PinnedHTTPSConnection',return_value=conn) as factory:
                    d=s.diagnose({'platform':'boss'});again=s.diagnose({'platform':'boss'})
                self.assertFalse(d['passed']);self.assertFalse(d['target_connection_tested'])
                self.assertFalse(d['browser_tested']);self.assertTrue(d['encrypted_dns_enabled'])
                self.assertEqual(d['effective_resolution']['tls_diagnostic']['verify_code'],20)
                self.assertIn('证书验证',d['message'])
                self.assertIn('上次失败',again['message']);factory.assert_called_once()
                self.assertEqual(s.ledger.summary('boss')['request']['day'],0)
                conn.request.assert_not_called();self.assertFalse(w.db.exists())
                self.assertNotIn('PRIVATE',json.dumps(d))
            finally:s.close()

    def test_no_consent_no_connection_or_tls_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=GuidedService(Workspace(tmp))
            try:
                with patch('socket.getaddrinfo',return_value=fake_answers('198.18.1.244')), \
                     patch('vibe_job_radar.network.PinnedHTTPSConnection') as factory:
                    d=s.diagnose({'platform':'boss'})
                self.assertEqual(d['code'],'encrypted_dns_consent_required')
                self.assertNotIn('tls_diagnostic',d['effective_resolution']);factory.assert_not_called()
            finally:s.close()

    def test_route_abort_preserves_original_failure_not_site_http_rejection(self):
        from vibe_job_radar.guided.browser import PlaywrightBackend
        from vibe_job_radar.guided.contracts import CrawlError
        b=object.__new__(PlaywrightBackend)
        b.adapter=Mock();b.cancelled=threading.Event();b.auth_mode=False
        b.error=None;b.wait_error=None;b._pagination_page=None;b.resource_denials=set()
        b.wire=Mock();b.wire.allowed_resource.return_value=True
        b.wire.ensure_robots.side_effect=CrawlError('encrypted_dns_tls_failed')
        route=Mock();route.request.url='https://www.zhipin.com/web/geek/job'
        route.request.method='GET';route.request.resource_type='document';route.request.post_data_buffer=None
        b._route(route)
        route.abort.assert_called_once_with('blockedbyclient')
        self.assertEqual(b.error,'encrypted_dns_tls_failed');b.wire.fetch.assert_not_called()
        route.fulfill.assert_not_called()


class ActualTLSMetadataTests(unittest.TestCase):
    def test_real_local_tls_rejection_preserves_validation_and_sends_no_post(self):
        # Reuse the existing loopback-only TLS fixture, not a new trust path.
        from test_encrypted_dns_transport import EncryptedRoundTripTests
        fixture=EncryptedRoundTripTests('test_bad_dns_tls_never_reaches_target')
        fixture.setUp();self.addCleanup(fixture.tearDown)
        with self.assertRaises(ResolutionError) as caught:
            fixture.perform(lambda:fixture.resolver.resolve(HOST,fixture.policy),trust=False)
        d=caught.exception.diagnostic
        self.assertEqual(caught.exception.code,'encrypted_dns_tls_failed')
        self.assertEqual(d['category'],'certificate_verification')
        self.assertIsInstance(d['verify_code'],int)
        self.assertEqual(d['tls_policy']['verify_mode'],'CERT_REQUIRED')
        self.assertTrue(d['tls_policy']['check_hostname'])
        self.assertEqual(d['connection_attempts'][0]['phase'],'tls')
        self.assertEqual(fixture.sni,['cloudflare-dns.com'])
        self.assertEqual(fixture.posts,[]);self.assertEqual(fixture.targets,[])
        self.assertEqual(len(fixture.dials),1)


if __name__=='__main__':unittest.main()
