"""Raw TLS wire assertions, plus header validation before any DNS or quota use."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_loopback_proxy as proxy_fixture
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.rate import RateLedger, Limits
from vibe_job_radar.guided.transport import PinnedTransport
from vibe_job_radar.guided.request_headers import browser_headers
from vibe_job_radar.network import USER_AGENT

HOST = proxy_fixture.HOST


class BrowserRequestHeaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = proxy_fixture.RealProxyTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.observed = []
        original = self.fixture.target.RequestHandlerClass.do_GET
        def receive(handler):
            self.observed.append({name: handler.headers.get_all(name, []) for name in
                ('User-Agent', 'Accept-Encoding', 'Cookie', 'Proxy-Authorization',
                 'Connection', 'X-Fixture-Hop', 'TE', 'Trailer', 'Upgrade',
                 'Accept-Language', 'Content-Length', 'Transfer-Encoding', 'Host')})
            original(handler)
        self.fixture.target.RequestHandlerClass.do_GET = receive
        self.fixture.target.RequestHandlerClass.do_POST = receive

    def fetch(self, headers, body=None):
        adapter = SimpleNamespace(key='fixture', domains=(HOST,), resource_domains=())
        with tempfile.TemporaryDirectory() as folder:
            wire = PinnedTransport(adapter, RateLedger(Path(folder)/'rates.sqlite',
                Limits(request_interval=0)), threading.Event())
            response = self.fixture.perform(lambda: wire.fetch('https://'+HOST+'/fixture',
                method='POST' if body is not None else 'GET', headers=headers, body=body))
            self.assertEqual(response.status, 200)
        return self.observed[-1]

    def test_real_browser_lowercase_header_is_not_sent_twice(self):
        h = self.fetch({'user-agent':'Fixture Browser/1.0'})
        self.assertEqual(h['User-Agent'], ['Fixture Browser/1.0 '+USER_AGENT])

    def test_uppercase_header_keeps_explicit_crawler_identity(self):
        h = self.fetch({'User-Agent':'Fixture Browser/1.0'})
        self.assertEqual(h['User-Agent'], ['Fixture Browser/1.0 '+USER_AGENT])

    def test_both_case_spellings_are_canonicalized_without_losing_identity(self):
        h = self.fetch({'User-Agent':'Fixture Browser/1.0','user-agent':'Fixture Browser/1.0'})
        self.assertEqual(h['User-Agent'], ['Fixture Browser/1.0 '+USER_AGENT])

    def test_existing_product_token_is_not_duplicated(self):
        h = self.fetch({'user-agent':'Fixture Browser/1.0 '+USER_AGENT})
        self.assertEqual(h['User-Agent'], ['Fixture Browser/1.0 '+USER_AGENT])

    def test_default_agent_and_encoding_boundary_remain(self):
        h = self.fetch({'accept-encoding':'br','Proxy-Authorization':'private-proxy-token'})
        self.assertEqual(h['User-Agent'], [USER_AGENT])
        self.assertEqual(h['Accept-Encoding'], ['identity'])
        self.assertEqual(h['Proxy-Authorization'], [])

    def test_session_cookie_unchanged_in_same_host_request(self):
        h = self.fetch({'user-agent':'Fixture Browser/1.0','Cookie':'fixture-session=artificial'})
        self.assertEqual(h['Cookie'], ['fixture-session=artificial'])
        self.assertEqual(len(self.fixture.requests), 1)
        self.assertEqual(len(self.fixture.connects), 1)

    def test_connection_nominated_fields_are_not_forwarded(self):
        h = self.fetch({'Connection':'keep-alive, X-Fixture-Hop', 'X-Fixture-Hop':'secret',
            'TE':'trailers', 'Trailer':'X-Fixture-Trailer', 'Upgrade':'websocket'})
        for name in ('Connection','X-Fixture-Hop','TE','Trailer','Upgrade'):
            self.assertEqual(h[name], [], name)

    def test_browser_languages_and_opaque_cookies_are_preserved(self):
        h = self.fetch({'Accept-Language':'zh-CN,zh;q=0.9', 'cookie':'a=1; b=%2Fabc=='})
        self.assertEqual(h['Accept-Language'], ['zh-CN,zh;q=0.9'])
        self.assertEqual(h['Cookie'], ['a=1; b=%2Fabc=='])

    def test_post_sent_once_with_recomputed_length_and_target_host(self):
        body = b'fixture=only'
        h = self.fetch({'content-length':'999','Host':'wrong.invalid','Transfer-Encoding':'chunked'}, body)
        self.assertEqual(h['Content-Length'], [str(len(body))])
        self.assertEqual(h['Host'], [HOST])
        self.assertEqual(h['Transfer-Encoding'], [])
        self.assertEqual(len(self.fixture.requests), 1)
        self.assertEqual(self.fixture.requests[0]['body'], body)


class HeaderValidationTests(unittest.TestCase):
    def test_none_produces_one_explicit_agent(self):
        self.assertEqual(browser_headers(None), {'User-Agent':USER_AGENT,'Accept-Encoding':'identity'})

    def test_empty_or_whitespace_agent_uses_project_identity(self):
        for value in ('','  '):
            self.assertEqual(browser_headers({'user-agent':value})['User-Agent'], USER_AGENT)

    def test_input_mapping_not_mutated(self):
        source = {'user-agent':'Browser/1','Cookie':'private-test-only'}
        before = dict(source);browser_headers(source)
        self.assertEqual(source, before)

    def test_identical_case_duplicates_are_one_field(self):
        self.assertEqual(browser_headers({'Cookie':'a=1','cookie':'a=1'})['cookie'], 'a=1')

    def test_conflicting_duplicates_are_rejected_without_values(self):
        for name in ('Cookie','User-Agent','Authorization','Connection'):
            with self.subTest(name=name), self.assertRaises(CrawlError) as e:
                browser_headers({name:'PRIVATE-one',name.lower():'PRIVATE-two'})
            self.assertEqual(e.exception.code, 'request_headers_conflict')
            self.assertNotIn('PRIVATE', str(e.exception))

    def test_illegal_names_and_header_injection_are_rejected(self):
        cases = [ {'X Test':'v'}, {'X:Bad':'v'}, {'X\r\nBad':'v'},
            {'X-Test':'one\r\nCookie: PRIVATE'}, {'X-Test':'bad\0value'},
            {'X-Test':'bad\x7fvalue'}, {'X-Test':'bad\x01value'},
            {'X-Test':'\u4e2d\u6587'}, {'X-Test':None}, {7:'v'} ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(CrawlError):
                browser_headers(value)

    def test_size_and_field_count_are_bounded(self):
        for value in ({'x':'a'*65536}, {f'X-{i}':'a' for i in range(129)}, ['bad']):
            with self.assertRaises(CrawlError):browser_headers(value)

    def test_invalid_connection_options_rejected(self):
        with self.assertRaises(CrawlError):browser_headers({'connection':'upgrade, bad option'})

    def test_normalization_is_idempotent(self):
        value = {'user-agent':'Browser/1', 'Cookie':'opaque', 'accept-encoding':'gzip'}
        self.assertEqual(browser_headers(browser_headers(value)), browser_headers(value))

    def test_project_substring_does_not_replace_explicit_token(self):
        h=browser_headers({'user-agent':'Not'+USER_AGENT+'suffix'})
        self.assertTrue(h['User-Agent'].endswith(' '+USER_AGENT))

    def test_invalid_fields_do_not_resolve_dial_or_consume_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=RateLedger(Path(tmp)/'rates.sqlite',Limits(request_interval=0))
            wire=PinnedTransport(SimpleNamespace(key='fixture',domains=(HOST,),resource_domains=()),
                                 ledger,threading.Event())
            with patch('socket.getaddrinfo') as dns, patch('socket.create_connection') as dial:
                with self.assertRaises(CrawlError):
                    wire.fetch('https://'+HOST+'/',headers={'Cookie':'a','cookie':'b'})
                dns.assert_not_called();dial.assert_not_called()
            self.assertEqual(ledger.summary('fixture')['request']['day'],0)


if __name__=='__main__':unittest.main()
