"""Direct/system-route regression; no proxy transport or alternate DNS is added.

Real socket/TLS tests route test-only public-IP symbols to a loopback TLS server
using a test mock. Production rejects loopback/private/Fake-IP targets unchanged.
"""
from __future__ import annotations

import errno
import http.server
import ipaddress
import socket
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibe_job_radar.network import (FetchError, PinnedHTTPSConnection, SafeHTTP,
                                    _connection_candidates, validate_public_url)
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.rate import Limits, RateLedger
from vibe_job_radar.guided.transport import PinnedTransport

IP4 = '8.8.8.8'
IP4_B = '1.1.1.1'
IP6 = '2606:4700:4700::1111'
HOST = 'network-fixture.invalid'


def answers(*ips):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM,
             socket.IPPROTO_TCP, '', (ip, 443, 0, 0) if ':' in ip else (ip, 443)) for ip in ips]


class ResolutionTests(unittest.TestCase):
    def test_legacy_single_ip_contract_remains(self):
        with patch('socket.getaddrinfo', return_value=answers(IP6, IP4)) as resolve:
            self.assertEqual(validate_public_url('https://example.com/a', {'example.com'}),
                             ('example.com', IP6, '/a'))
        resolve.assert_called_once()

    def test_network_callers_can_retain_the_whole_snapshot(self):
        with patch('socket.getaddrinfo', return_value=answers(IP6, IP4, IP4)) as resolve:
            result = validate_public_url('https://example.com/a', {'example.com'}, all_addresses=True)
        self.assertEqual(result, ('example.com', (IP6, IP4), '/a'))
        resolve.assert_called_once()

    def test_mixed_private_result_is_not_filtered_away(self):
        for ip in ('127.0.0.1', '10.0.0.1', '198.18.0.92', '::1'):
            with self.subTest(ip=ip), patch('socket.getaddrinfo', return_value=answers(IP4, ip)):
                with self.assertRaises(FetchError) as error:
                    validate_public_url('https://example.com/', {'example.com'}, all_addresses=True)
                self.assertEqual(error.exception.code, 'non_public_address')

    def test_candidate_family_interleaving_and_cap(self):
        ips = (IP6, '2606:4700:4700::1001', '2001:4860:4860::8888', IP4, IP4_B, '9.9.9.9')
        self.assertEqual(_connection_candidates(ips), (IP6, IP4, '2606:4700:4700::1001', IP4_B))

    def test_invalid_candidate_beyond_limit_still_rejected(self):
        with self.assertRaises(FetchError):
            _connection_candidates((IP4, IP4_B, '9.9.9.9', '8.8.4.4', '127.0.0.1'))


class ConnectionUnitTests(unittest.TestCase):
    def connection(self, ips=(IP6, IP4), timeout=20):
        conn = PinnedHTTPSConnection(HOST, ips, timeout)
        conn._context = MagicMock()
        return conn

    def test_unreachable_ipv6_uses_already_validated_ipv4(self):
        conn = self.connection()
        plain = MagicMock()
        with patch('socket.create_connection', side_effect=[OSError(errno.ENETUNREACH, 'no IPv6'), plain]) as dial:
            conn.connect()
        self.assertEqual([call.args[0][0] for call in dial.call_args_list], [IP6, IP4])
        self.assertEqual(conn.connected_ip, IP4)
        conn._context.wrap_socket.assert_called_once_with(plain, server_hostname=HOST)
        self.assertEqual(conn.connection_attempts[-1]['outcome'], 'connected')

    def test_same_family_second_ip_can_succeed(self):
        conn = self.connection((IP4, IP4_B))
        with patch('socket.create_connection', side_effect=[ConnectionRefusedError(), MagicMock()]):
            conn.connect()
        self.assertEqual(conn.connected_ip, IP4_B)

    def test_first_success_does_not_dial_other_candidates(self):
        conn = self.connection()
        with patch('socket.create_connection', return_value=MagicMock()) as dial:
            conn.connect()
        dial.assert_called_once()

    def test_certificate_failure_closes_socket_and_does_not_try_next_ip(self):
        conn = self.connection()
        plain = MagicMock()
        conn._context.wrap_socket.side_effect = ssl.SSLCertVerificationError(1, 'bad certificate')
        with patch('socket.create_connection', return_value=plain) as dial:
            with self.assertRaises(ssl.SSLCertVerificationError):
                conn.connect()
        dial.assert_called_once(); plain.close.assert_called_once()
        self.assertIsNone(conn.sock)

    def test_tls_protocol_error_is_not_downgraded_to_routing_failure(self):
        conn = self.connection()
        conn._context.wrap_socket.side_effect = ssl.SSLError(1, 'TLS handshake failed')
        with patch('socket.create_connection', return_value=MagicMock()) as dial:
            with self.assertRaises(ssl.SSLError):
                conn.connect()
        dial.assert_called_once()

    def test_os_permission_refusal_does_not_try_another_ip(self):
        conn = self.connection()
        with patch('socket.create_connection', side_effect=PermissionError(errno.EACCES, 'blocked')) as dial:
            with self.assertRaises(PermissionError):
                conn.connect()
        dial.assert_called_once()

    def test_connect_timeout_shares_one_budget(self):
        conn = self.connection((IP6, IP4, IP4_B, '9.9.9.9'), timeout=20)
        now = [100.0]
        budgets = []
        def timeout(endpoint, timeout, source):
            budgets.append(timeout)
            now[0] += timeout
            raise TimeoutError('test timeout')
        with patch('time.monotonic', side_effect=lambda: now[0]), patch('socket.create_connection', side_effect=timeout):
            with self.assertRaises(TimeoutError):
                conn.connect()
        self.assertEqual(len(budgets), 4)
        self.assertLessEqual(sum(budgets), 20.00001)

    def test_failed_handshake_socket_is_closed_before_pre_request_retry(self):
        conn = self.connection()
        one, two = MagicMock(), MagicMock()
        conn._context.wrap_socket.side_effect = [TimeoutError(), MagicMock()]
        with patch('socket.create_connection', side_effect=[one, two]):
            conn.connect()
        one.close.assert_called_once()
        self.assertEqual(conn.connected_ip, IP4)

    def test_all_tcp_failures_are_bounded(self):
        conn = self.connection((IP4, IP4_B, '9.9.9.9', '8.8.4.4', IP6))
        with patch('socket.create_connection', side_effect=ConnectionRefusedError()) as dial:
            with self.assertRaises(ConnectionRefusedError):
                conn.connect()
        self.assertEqual(dial.call_count, 4)
        self.assertIsNone(conn.sock)

    def test_nonfinite_timeout_and_unvalidated_inputs_rejected(self):
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                PinnedHTTPSConnection(HOST, IP4, value)
        for value in ((), '127.0.0.1', 'example.com', '198.18.0.92'):
            with self.subTest(ip=value), self.assertRaises(FetchError):
                PinnedHTTPSConnection(HOST, value, 20)


class RealTLSRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.received = []
        received = self.received
        self.response_status = 200
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                received.append((self.command, self.path, body))
                value = b'{"fixture":"real-tls","ok":true}'
                self.send_response(owner.response_status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(value)))
                self.end_headers(); self.wfile.write(value)
            do_GET = do_POST = handle_request
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        pem = Path(__file__).with_name('fixtures') / 'connection_test_only.pem'
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(pem)
        self.server.socket = server_context.wrap_socket(self.server.socket, server_side=True)
        self.client_context = ssl.create_default_context(cafile=str(pem))
        self.client_context.check_hostname = True
        self.assertEqual(self.client_context.verify_mode, ssl.CERT_REQUIRED)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.original_dial = socket.create_connection
        self.dials = []

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.tmp.cleanup()

    def local_test_dial(self, endpoint, timeout=None, source=None):
        ip, port = endpoint
        self.assertEqual(port, 443)
        self.assertTrue(ipaddress.ip_address(ip).is_global)
        self.dials.append(ip)
        if ip == IP6:
            raise OSError(errno.ENETUNREACH, 'controlled absent IPv6 route')
        self.assertIn(ip, (IP4, IP4_B))
        # Sole test seam: a checked public-IP symbol is sent to a local test
        # server. No production API/setting can request this mapping.
        return self.original_dial(self.server.server_address, timeout, source)

    def round_trip(self, operation, *, hostname=HOST):
        with patch('socket.getaddrinfo', return_value=answers(IP6, IP4, IP4_B)), \
             patch('vibe_job_radar.network.ssl.create_default_context', return_value=self.client_context), \
             patch('vibe_job_radar.network.socket.create_connection', side_effect=self.local_test_dial):
            # Numeric loopback dialing must use its real resolver rather than
            # the fixture DNS snapshot for the external hostname.
            real_gai = self.real_resolver
            with patch('socket.getaddrinfo', side_effect=lambda host, *a, **kw:
                       answers(IP6, IP4, IP4_B) if host == hostname else real_gai(host, *a, **kw)):
                return operation()

    real_resolver = staticmethod(socket.getaddrinfo)

    def test_http_get_reaches_real_tls_server_after_first_ip_failure(self):
        value = self.round_trip(lambda: SafeHTTP({HOST}, interval=0).json(f'https://{HOST}/jobs'))
        self.assertEqual(value['fixture'], 'real-tls')
        self.assertEqual(self.dials, [IP6, IP4])
        self.assertEqual(len(self.received), 1)

    def test_bridge_shares_the_same_candidate_selection(self):
        adapter = MagicMock(); adapter.key = 'fixture'; adapter.domains = (HOST,); adapter.resource_domains = ()
        ledger = RateLedger(Path(self.tmp.name)/'rate.sqlite', Limits(request_interval=0))
        wire = PinnedTransport(adapter, ledger, threading.Event())
        value = self.round_trip(lambda: wire.fetch(f'https://{HOST}/detail'))
        self.assertEqual(value.status, 200)
        self.assertEqual(self.dials, [IP6, IP4])
        self.assertEqual(ledger.summary('fixture')['request']['day'], 1)
        self.assertEqual(len(self.received), 1)

    def test_post_body_is_sent_once_not_once_per_candidate(self):
        body = b'test payload, no credentials'
        self.round_trip(lambda: SafeHTTP({HOST}, interval=0).request(f'https://{HOST}/api', method='POST', body=body))
        self.assertEqual(self.received, [('POST', '/api', body)])
        self.assertEqual(self.dials, [IP6, IP4])

    def test_http_403_is_not_retried_at_another_address(self):
        self.response_status = 403
        with self.assertRaises(FetchError) as error:
            self.round_trip(lambda: SafeHTTP({HOST}, interval=0).request(f'https://{HOST}/denied'))
        self.assertEqual(error.exception.code, 'http_403')
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.dials, [IP6, IP4])

    def test_wrong_hostname_certificate_is_rejected_without_http(self):
        wrong = 'wrong-host.invalid'
        with self.assertRaises(FetchError) as error:
            self.round_trip(lambda: SafeHTTP({wrong}, interval=0).request(f'https://{wrong}/'), hostname=wrong)
        self.assertEqual(error.exception.code, 'tls_verification_failed')
        self.assertEqual(self.received, [])
        self.assertEqual(self.dials, [IP6, IP4])

    def test_broken_response_is_not_replayed(self):
        class BrokenResponse(SafeHTTP):
            pass
        with patch('http.client.HTTPResponse.read', side_effect=ConnectionResetError('test read failure')):
            with self.assertRaises(FetchError):
                self.round_trip(lambda: BrokenResponse({HOST}, interval=0).request(f'https://{HOST}/once'))
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.dials, [IP6, IP4])


if __name__ == '__main__':
    unittest.main()
