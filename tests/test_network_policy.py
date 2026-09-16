"""Selection and actual loopback CONNECT/TLS; no public-site certification."""
import json
import os
import threading
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import test_loopback_proxy as proxy_fixtures
import test_guided_merge_review as browser_fixtures
from vibe_job_radar.network_policy import NetworkPolicy, current_policy, use_policy, _bypasses
from vibe_job_radar.loopback_proxy import LocalProxyError, LoopbackProxy
from vibe_job_radar.network import SafeHTTP, PinnedHTTPSConnection, FetchError
from vibe_job_radar.network_environment import inspect_environment
from vibe_job_radar.guided.transport import PinnedTransport
from vibe_job_radar.guided.rate import RateLimit
from vibe_job_radar.guided.contracts import CrawlError


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start(); self.addCleanup(self.environment.stop)

    def capture(self, values):
        return NetworkPolicy.capture(discover=lambda: values)

    def test_automatic_https_before_all(self):
        p = self.capture({'https':'http://localhost:8001','all':'http://127.0.0.1:8002'})
        self.assertEqual(p.for_host('example.com'), LoopbackProxy('127.0.0.1',8001))
        self.assertEqual(p.source, 'automatic_static')

    def test_automatic_all_and_system_bare_host_port(self):
        self.assertEqual(self.capture({'all':'127.0.0.1:8002'}).for_host('x').port, 8002)

    def test_environment_lowercase_precedence_comes_from_stdlib(self):
        with patch.dict(os.environ, {'HTTPS_PROXY':'http://127.0.0.1:8001',
                                     'https_proxy':'http://127.0.0.1:8002'}):
            self.assertEqual(NetworkPolicy.capture().for_host('x').port,8002)

    def test_http_only_not_used_for_https(self):
        self.assertIsNone(self.capture({'http':'http://127.0.0.1:8001'}).for_host('x'))

    def test_explicit_application_overrides_system_and_no_proxy(self):
        with patch.dict(os.environ, {'VIBE_RADAR_HTTP_PROXY':'http://127.0.0.1:8003'}):
            p = self.capture({'https':'http://127.0.0.1:8001','no':'*'})
        self.assertEqual(p.for_host('x').port,8003)
        self.assertEqual(p.source,'explicit_application')

    def test_no_proxy_suffix_boundary_and_https_port(self):
        p = self.capture({'https':'http://127.0.0.1:8001','no':'.example.com:443'})
        for h in ('example.com','a.example.com','A.EXAMPLE.COM.'):
            self.assertIsNone(p.for_host(h))
        for h in ('notexample.com','example.com.evil.invalid'):
            self.assertIsNotNone(p.for_host(h))
        self.assertIsNotNone(self.capture({'all':'http://127.0.0.1:8001', 'no':'example.com:80'}).for_host('example.com'))

    def test_no_proxy_ip_and_cidr_without_dns(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')):
            self.assertTrue(_bypasses('8.8.8.8',('8.8.0.0/16',)))
            self.assertFalse(_bypasses('8.9.8.8',('8.8.0.0/16',)))
            self.assertTrue(_bypasses('2606:4700:4700::1111',('[2606:4700:4700::1111]:443',)))
            self.assertFalse(_bypasses('example.com',('8.8.0.0/16',)))

    def test_configured_no_proxy_can_exclude_unsupported_proxy(self):
        p = self.capture({'https':'socks5://127.0.0.1:8001','no':'example.com'})
        self.assertIsNone(p.for_host('example.com'))
        with self.assertRaises(LocalProxyError): p.for_host('other.com')

    def test_unknown_proxy_or_credentials_fails_without_direct_fallback(self):
        for raw in ('socks5://127.0.0.1:8001','http://10.0.0.1:8001',
                    'http://secret:DO-NOT-ECHO@127.0.0.1:8001','http://127.0.0.1:8001/path'):
            p = self.capture({'https':raw})
            with self.subTest(raw=raw), self.assertRaises(FetchError), patch('socket.create_connection') as dial:
                PinnedHTTPSConnection('example.com','8.8.8.8',1,network_policy=p)
            dial.assert_not_called()
            self.assertNotIn('DO-NOT-ECHO', repr(p)+json.dumps(p.describe()))

    def test_discovery_failure_is_not_no_proxy(self):
        def failed(): raise RuntimeError('credential=SECRET')
        p = NetworkPolicy.capture(discover=failed)
        with self.assertRaises(LocalProxyError) as error: p.for_host('x')
        self.assertNotIn('SECRET', str(error.exception)+repr(p))

    def test_capture_neither_changes_settings_nor_connects(self):
        before=dict(os.environ)
        with patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')), patch('socket.create_connection',side_effect=AssertionError('connection forbidden')):
            self.capture({'https':'http://127.0.0.1:8001'})
        self.assertEqual(before,dict(os.environ))

    def test_existing_client_keeps_immutable_snapshot(self):
        with patch('urllib.request.getproxies',return_value={'https':'http://127.0.0.1:8001'}):
            client=SafeHTTP({'example.com'})
        with patch('urllib.request.getproxies',return_value={'https':'http://127.0.0.1:8002'}):
            other=SafeHTTP({'example.com'})
            self.assertEqual(client.network_policy.for_host('x').port,8001)
            self.assertEqual(other.network_policy.for_host('x').port,8002)
        with self.assertRaises(FrozenInstanceError): client.network_policy.source='other'

    def test_nested_context_resets_even_after_exception(self):
        one=self.capture({'https':'http://127.0.0.1:8001'}); two=self.capture({})
        with use_policy(one):
            self.assertIs(current_policy(),one)
            with self.assertRaises(RuntimeError):
                with use_policy(two):
                    self.assertIs(current_policy(),two)
                    raise RuntimeError('test')
            self.assertIs(current_policy(),one)

    def test_thread_policy_does_not_leak(self):
        one=self.capture({'https':'http://127.0.0.1:8001'}); two=self.capture({'https':'http://127.0.0.1:8002'})
        found=[]; barrier=threading.Barrier(2)
        def worker(p):
            with use_policy(p):
                barrier.wait(timeout=3)
                found.append(current_policy().for_host('x').port)
        workers=[threading.Thread(target=worker,args=(p,)) for p in (one,two)]
        for t in workers:t.start()
        for t in workers:t.join(timeout=4)
        self.assertCountEqual(found,[8001,8002])

    def test_diagnostic_does_not_claim_fake_ip_supported(self):
        result=inspect_environment(discover=lambda:{'https':'http://127.0.0.1:8001'})
        self.assertTrue(result['collector_applies_static_proxy'])
        self.assertFalse(result['selected_policy']['fake_ip_supported'])
        self.assertFalse(result['selected_policy']['network_tested'])


class AutomaticProxyRoundTripTests(unittest.TestCase):
    setUp=proxy_fixtures.RealProxyTests.setUp
    tearDown=proxy_fixtures.RealProxyTests.tearDown
    perform=proxy_fixtures.RealProxyTests.perform

    def auto(self, call):
        # Reuse actual loopback HTTP CONNECT + TLS fixture, replacing ONLY its
        # explicit app setting with stdlib static-proxy discovery.
        def automatic():
            with patch.dict(os.environ,{'VIBE_RADAR_HTTP_PROXY':''}), patch('urllib.request.getproxies',return_value={'https':self.setting}):
                return call()
        return self.perform(automatic)

    def test_standard_proxy_actually_transports_http_without_application_env(self):
        value=self.auto(lambda:SafeHTTP({proxy_fixtures.HOST},interval=0).json(f'https://{proxy_fixtures.HOST}/jobs'))
        self.assertTrue(value['ok']); self.assertEqual(len(self.connects),1)
        self.assertEqual(self.dials,[self.proxy.server_address])

    def test_browser_bridge_uses_same_automatic_policy(self):
        from types import SimpleNamespace
        from pathlib import Path
        from vibe_job_radar.guided.rate import RateLedger, Limits
        wire=PinnedTransport(SimpleNamespace(key='fixture', domains=(proxy_fixtures.HOST,),resource_domains=()),
                             RateLedger(Path(self.tmp.name)/'rate.sqlite',Limits(request_interval=0)),threading.Event())
        self.assertEqual(self.auto(lambda:wire.fetch(f'https://{proxy_fixtures.HOST}/jobs')).status,200)
        self.assertEqual(wire.network_policy.source,'automatic_static')
        self.assertEqual(len(self.connects),1)

    def test_automatic_proxy_refusal_does_not_direct_fallback(self):
        self.proxy_status=407
        with self.assertRaises(FetchError) as error:
            self.auto(lambda:SafeHTTP({proxy_fixtures.HOST},interval=0).json(f'https://{proxy_fixtures.HOST}/jobs'))
        self.assertEqual(error.exception.code,'local_proxy_connection_failed')
        self.assertEqual(self.dials,[self.proxy.server_address]); self.assertFalse(self.requests)

    def test_automatic_policy_still_rejects_fake_ip(self):
        self.symbol='198.18.0.5'
        with self.assertRaises(FetchError) as error:
            self.auto(lambda:SafeHTTP({proxy_fixtures.HOST},interval=0).json(f'https://{proxy_fixtures.HOST}/jobs'))
        self.assertEqual(error.exception.code,'non_public_address')
        self.assertFalse(self.connects)


class RecoveryPriorityTests(unittest.TestCase):
    def test_permission_error_wins_over_earlier_or_later_wait(self):
        for errors in ((RateLimit(60,'publisher_wait',next_allowed_at=123), CrawlError('http_403')),
                       (CrawlError('http_403'), RateLimit(60,'publisher_wait',next_allowed_at=123))):
            browser=browser_fixtures.BrowserReviewTests().backend()
            route=Mock(); route.request.url='https://jobs.fixture.test/job/1'
            route.request.resource_type='document'; route.request.method='GET'
            browser.wire.fetch.side_effect=errors
            browser._route(route); browser._route(route)
            with self.assertRaises(CrawlError) as error:browser.snapshot()
            self.assertEqual(error.exception.code,'http_403')
            self.assertNotIsInstance(error.exception,RateLimit)

    def test_nonfinite_retry_after_does_not_persist_infinite_timestamp(self):
        for value in ('inf','Infinity','NaN'):
            self.assertLessEqual(PinnedTransport._retry_seconds(value),86400)
