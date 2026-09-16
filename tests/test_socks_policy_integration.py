"""Merged-main + PR26 semantic integration. Local fixtures, no Internet calls."""
import json
import os
import socket
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_loopback_socks as fixture
from vibe_job_radar.loopback_proxy import LocalProxyError, LoopbackProxy
from vibe_job_radar.loopback_socks import LoopbackSocks5
from vibe_job_radar.network import FetchError, PinnedHTTPSConnection, SafeHTTP
from vibe_job_radar.network_environment import inspect_environment
from vibe_job_radar.network_policy import NetworkPolicy, _bypasses, use_policy
from vibe_job_radar.guided.rate import Limits, RateLedger
from vibe_job_radar.guided.transport import PinnedTransport


class SocksPolicyTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start(); self.addCleanup(env.stop)

    def capture(self, **values):
        return NetworkPolicy.capture(discover=lambda: values)

    def test_explicit_socks_does_not_discover_or_obey_ambient_no_proxy(self):
        with patch.dict(os.environ, {fixture.ENV:'socks5://localhost:8111'}):
            def forbidden(): raise AssertionError('explicit policy must not discover')
            policy = NetworkPolicy.capture(discover=forbidden)
        self.assertIsInstance(policy.for_host('example.com'), LoopbackSocks5)
        self.assertEqual(policy.source, 'explicit_application')
        self.assertEqual(policy.proxy.port, 8111)

    def test_explicit_socks_beats_environment_http_and_bypass(self):
        with patch.dict(os.environ, {fixture.ENV:'socks5://localhost:8111'}):
            policy = self.capture(https='http://localhost:8222', no='*')
        self.assertIsInstance(policy.for_host('example.com'), LoopbackSocks5)

    def test_explicit_conflict_is_not_silently_resolved_by_no_proxy(self):
        with patch.dict(os.environ, {fixture.ENV:'socks5://localhost:8111',
                                    fixture.HTTP_ENV:'http://localhost:8222'}):
            policy = self.capture(no='*')
        with self.assertRaises(LocalProxyError) as error: policy.for_host('example.com')
        self.assertEqual(error.exception.code, 'local_proxy_configuration_conflict')

    def test_automatic_https_socks_before_all_http(self):
        p = self.capture(https='socks5://localhost:8111', all='http://localhost:8222')
        self.assertIsInstance(p.proxy, LoopbackSocks5)
        self.assertEqual(p.source, 'automatic_static')
        self.assertEqual(p.describe()['transport'], 'loopback_socks5_proxy')

    def test_automatic_all_socks_and_explicit_versioned_socks_key(self):
        for values in ({'all':'socks5://localhost:8111'}, {'socks':'socks5://localhost:8111'}):
            with self.subTest(values=values):
                self.assertIsInstance(self.capture(**values).for_host('x'), LoopbackSocks5)

    def test_system_bare_socks_cannot_invent_protocol_version(self):
        for raw in ('localhost:8111', 'socks://localhost:8111', 'socks4://localhost:8111'):
            with self.subTest(raw=raw), self.assertRaises(LocalProxyError):
                self.capture(socks=raw).for_host('example.com')

    def test_http_precedence_over_dedicated_socks(self):
        p = self.capture(https='http://localhost:8222', socks='socks5://localhost:8111')
        self.assertIs(type(p.proxy), LoopbackProxy)

    def test_protocol_participates_in_policy_fingerprint(self):
        http = self.capture(https='http://localhost:8111')
        socks = self.capture(https='socks5://localhost:8111')
        self.assertNotEqual(http.fingerprint, socks.fingerprint)
        self.assertEqual(socks.fingerprint, self.capture(https='socks5://127.0.0.1:8111').fingerprint)

    def test_socks_parser_does_not_accept_http_or_remote_dns(self):
        for raw in (None, 1, 'http://localhost:8111', 'socks5h://localhost:8111',
                    'socks5://localhost:8111\x7f', 'socks5://[::1%lo]:8111'):
            with self.subTest(raw=raw), self.assertRaises(LocalProxyError):
                LoopbackSocks5.from_url(raw)

    def test_socks_credentials_never_echoed_from_policy_or_diagnostics(self):
        secret='USER-PRIVATE-TOKEN'
        values={'all':f'socks5://name:{secret}@localhost:8111'}
        policy=self.capture(**values)
        with self.assertRaises(LocalProxyError) as error:policy.for_host('example.com')
        report=inspect_environment(discover=lambda:values)
        self.assertNotIn(secret, repr(policy)+str(error.exception)+json.dumps(report))
        self.assertEqual(report['selected_policy']['transport'], 'unavailable')

    def test_diagnostics_report_actual_socks_capability_not_certification(self):
        report=inspect_environment(discover=lambda:{'all':'socks5://localhost:8111'})
        self.assertTrue(report['collector_applies_static_proxy'])
        self.assertTrue(report['selected_policy']['automatic_static_socks5'])
        self.assertFalse(report['selected_policy']['fake_ip_supported'])
        self.assertFalse(report['selected_policy']['network_tested'])
        self.assertFalse(report['explicit_loopback_proxy']['enabled'])

    def test_explicit_diagnostics_and_conflict_use_same_selector(self):
        with patch.dict(os.environ,{fixture.ENV:'socks5://localhost:8111'}):
            report=inspect_environment(discover=lambda:{})
            self.assertTrue(report['explicit_loopback_proxy']['enabled'])
            self.assertEqual(report['explicit_loopback_proxy']['protocol'], 'loopback_socks5_proxy')
            with patch.dict(os.environ,{fixture.HTTP_ENV:'http://localhost:8222'}):
                bad=inspect_environment(discover=lambda:{})
        self.assertFalse(bad['explicit_loopback_proxy']['valid'])
        self.assertEqual(bad['explicit_loopback_proxy']['error'], 'local_proxy_configuration_conflict')

    def test_no_proxy_is_host_specific_not_global_socks_disable(self):
        p=self.capture(all='socks5://localhost:8111', no='.example.com:443')
        self.assertIsNone(p.for_host('a.example.com'))
        self.assertIsInstance(p.for_host('notexample.com'), LoopbackSocks5)
        self.assertEqual(p.describe('a.example.com')['transport'], 'system_route')

    def test_ipv6_literal_ending_443_is_not_a_port_suffix(self):
        host='2001:4860:4860::8844'
        self.assertFalse(_bypasses(host, (host+':443',)))
        self.assertTrue(_bypasses(host, ('['+host+']:443',)))
        self.assertTrue(_bypasses(host+':443', (host+':443',)))
        self.assertFalse(_bypasses(host, ('['+host+']:80',)))

    def test_numeric_no_proxy_rule_does_not_match_domain_suffix(self):
        self.assertFalse(_bypasses('evil.8.8.8.8', ('8.8.8.8',)))
        self.assertFalse(_bypasses('evil.8.8.8.8', ('8.8.8.8:443',)))
        self.assertTrue(_bypasses('8.8.8.8', ('8.8.8.8:443',)))
        self.assertTrue(_bypasses('8.8.8.8', ('8.8.8.8',)))

    def test_snapshot_survives_environment_change_without_protocol_switch(self):
        with patch.dict(os.environ,{fixture.ENV:'socks5://localhost:8111'}):
            client=SafeHTTP({'example.com'})
        with patch.dict(os.environ,{fixture.HTTP_ENV:'http://localhost:8222'}):
            with use_policy(client.network_policy):
                connection=PinnedHTTPSConnection('example.com','8.8.8.8',1)
        self.assertEqual(connection.network_mode,'loopback_socks5_proxy')
        connection.close()


class AutomaticSocksRoundTripTests(unittest.TestCase):
    setUp=fixture.RealSocksTests.setUp
    tearDown=fixture.RealSocksTests.tearDown
    perform=fixture.RealSocksTests.perform

    def auto(self, call):
        def invoke():
            with patch.dict(os.environ,{fixture.ENV:'',fixture.HTTP_ENV:''}), \
                 patch('urllib.request.getproxies',return_value={'all':self.setting}):
                return call()
        return self.perform(invoke)

    def test_standard_all_proxy_reaches_real_socks_and_verified_tls(self):
        result=self.auto(lambda:SafeHTTP({fixture.HOST},interval=0).json(f'https://{fixture.HOST}/jobs'))
        self.assertTrue(result['ok'])
        self.assertEqual(self.connects, [(fixture.IP4,443,1)])
        self.assertEqual(self.sni, [fixture.HOST])
        self.assertEqual(self.dials, [self.proxy.server_address])

    def test_browser_bridge_consumes_automatic_socks_policy(self):
        ledger=RateLedger(Path(self.tmp.name)/'rate.sqlite', Limits(request_interval=0))
        wire=PinnedTransport(SimpleNamespace(key='fixture',domains=(fixture.HOST,),resource_domains=()),
                             ledger,threading.Event())
        self.assertEqual(self.auto(lambda:wire.fetch(f'https://{fixture.HOST}/job')).status,200)
        self.assertEqual(wire.network_policy.describe()['transport'],'loopback_socks5_proxy')
        self.assertEqual(ledger.summary('fixture')['request']['day'],1)

    def test_automatic_auth_refusal_sends_no_origin_request_or_direct_retry(self):
        self.method=2
        with self.assertRaises(FetchError) as error:
            self.auto(lambda:SafeHTTP({fixture.HOST},interval=0).json(f'https://{fixture.HOST}/jobs'))
        self.assertEqual(error.exception.code,'local_socks_auth_unsupported')
        self.assertFalse(self.requests)
        self.assertEqual(self.dials,[self.proxy.server_address])

    def test_automatic_post_is_not_replayed_after_origin_403(self):
        self.http_status=403
        with self.assertRaises(FetchError) as error:
            self.auto(lambda:SafeHTTP({fixture.HOST},interval=0).request(f'https://{fixture.HOST}/jobs',method='POST',body=b'once'))
        self.assertEqual(error.exception.code,'http_403')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.requests[0][1],b'once')
        self.assertEqual(self.dials,[self.proxy.server_address])

    def test_automatic_fake_ip_cannot_sneak_into_numeric_socks_connect(self):
        self.symbol='198.18.0.5'
        with self.assertRaises(FetchError) as error:
            self.auto(lambda:SafeHTTP({fixture.HOST},interval=0).json(f'https://{fixture.HOST}/jobs'))
        self.assertEqual(error.exception.code,'non_public_address')
        self.assertFalse(self.dials)
