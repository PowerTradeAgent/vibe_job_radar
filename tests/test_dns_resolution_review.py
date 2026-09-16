"""Regression review of DoH lifecycle and cache provenance. No public traffic."""
from __future__ import annotations

import ipaddress
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import test_encrypted_dns as fixture
from vibe_job_radar.dns_wire import Answer, ResolutionError, parse_answer
from vibe_job_radar.encrypted_dns import PublicResolver
from vibe_job_radar.network_policy import NetworkPolicy

HOST, IP4, IP6 = fixture.HOST, fixture.IP4, fixture.IP6


class ResolverLifecycleReviewTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.allowed = True
        self.resolver = PublicResolver(clock=lambda: self.now[0], permission=lambda: self.allowed)
        self.policy = NetworkPolicy(encrypted_dns=True, resolver=self.resolver)
        self.cancelled = threading.Event()
        self.dns = patch('socket.getaddrinfo', return_value=fixture.fake_answers('198.18.0.42'))
        self.dns.start()
        self.addCleanup(self.dns.stop)

    def answer(self, host, kind, *args, **kwargs):
        return Answer((IP4 if kind == 1 else IP6,), 60, host)

    def resolve(self):
        return self.resolver.resolve(HOST, self.policy, cancelled=self.cancelled)

    def test_cancelled_literal_or_completed_system_dns_does_not_return_target(self):
        self.cancelled.set()
        with self.assertRaisesRegex(ResolutionError, 'paused'):
            self.resolver.resolve(IP4, self.policy, cancelled=self.cancelled)
        self.cancelled.clear()
        def system_dns(*args, **kwargs):
            self.cancelled.set()
            return fixture.fake_answers(IP4)
        with patch('socket.getaddrinfo', side_effect=system_dns):
            with self.assertRaisesRegex(ResolutionError, 'paused'):
                self.resolve()

    def test_cancellation_during_last_family_never_returns_or_caches(self):
        def exchange(host, kind, *args, **kwargs):
            if kind == 28:
                self.cancelled.set()
            return self.answer(host, kind)
        with patch.object(self.resolver, '_exchange', side_effect=exchange):
            with self.assertRaisesRegex(ResolutionError, 'paused'):
                self.resolve()
        self.assertEqual(self.resolver._cache, {})

    def test_revocation_during_last_family_never_returns_or_caches(self):
        def exchange(host, kind, *args, **kwargs):
            if kind == 28:
                self.allowed = False
            return self.answer(host, kind)
        with patch.object(self.resolver, '_exchange', side_effect=exchange):
            with self.assertRaisesRegex(ResolutionError, 'encrypted_dns_disabled'):
                self.resolve()
        self.assertEqual(self.resolver._cache, {})

    def test_waiting_lookup_rechecks_permission_before_cached_result(self):
        with patch.object(self.resolver, '_exchange', side_effect=self.answer) as exchange:
            self.resolve()
            checked = threading.Event()
            outcome = []
            def permission():
                checked.set()
                return self.allowed
            self.resolver.permission = permission
            def worker():
                try:
                    outcome.append(self.resolve())
                except ResolutionError as exc:
                    outcome.append(exc.code)
            self.resolver._lock.acquire()
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(checked.wait(2), 'worker did not reach permission check')
                self.allowed = False
            finally:
                self.resolver._lock.release()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome, ['encrypted_dns_disabled'])
            self.assertEqual(exchange.call_count, 2)

    def test_hard_failure_is_not_relabelled_temporary_on_next_attempt(self):
        for code in ('encrypted_dns_tls_failed', 'encrypted_dns_non_public_answer',
                     'encrypted_dns_invalid_response', 'encrypted_dns_http_rejected',
                     'encrypted_dns_route_failed', 'encrypted_dns_refused'):
            with self.subTest(code=code):
                self.resolver = PublicResolver(clock=lambda: self.now[0], permission=lambda: True)
                self.policy = replace(self.policy, resolver=self.resolver)
                with patch.object(self.resolver, '_exchange', side_effect=ResolutionError(code)) as exchange:
                    for _ in range(2):
                        with self.assertRaisesRegex(ResolutionError, '^' + code + '$'):
                            self.resolve()
                    self.resolver.clear()
                    with self.assertRaisesRegex(ResolutionError, '^' + code + '$'):
                        self.resolve()
                    self.assertEqual(exchange.call_count, 1)

    def test_temporary_outage_remains_cooldown_and_can_recover(self):
        with patch.object(self.resolver, '_exchange', side_effect=ResolutionError('encrypted_dns_unavailable')) as exchange:
            with self.assertRaisesRegex(ResolutionError, 'unavailable'):
                self.resolve()
            with self.assertRaisesRegex(ResolutionError, 'cooldown'):
                self.resolve()
            self.assertEqual(exchange.call_count, 1)
        self.now[0] += 31
        with patch.object(self.resolver, '_exchange', side_effect=self.answer):
            self.assertEqual(self.resolve().addresses, (IP4, IP6))

    def test_expired_negative_family_is_not_silently_accepted(self):
        def exchange(host, kind, *args, **kwargs):
            if kind == 1:
                return Answer((), 1, host)
            self.now[0] += 2
            return Answer((IP6,), 60, host)
        with patch.object(self.resolver, '_exchange', side_effect=exchange):
            with self.assertRaisesRegex(ResolutionError, 'expired_answer'):
                self.resolve()
        self.assertEqual(self.resolver._cache, {})

    def test_noncacheable_answer_still_has_a_validity_deadline(self):
        def exchange(host, kind, *args, **kwargs):
            if kind == 1:
                return Answer((IP4,), 0, host, valid_for=1)
            self.now[0] += 2
            return Answer((IP6,), 60, host)
        with patch.object(self.resolver, '_exchange', side_effect=exchange):
            with self.assertRaisesRegex(ResolutionError, 'expired_answer'):
                self.resolve()
        self.assertEqual(self.resolver._cache, {})

    def test_private_special_names_do_not_leak_to_system_or_doh(self):
        with patch('socket.getaddrinfo') as dns, patch.object(self.resolver, '_exchange') as exchange:
            for host in ('some-service.onion', 'host.localhost', 'printer.local', 'host.internal',
                         'home.arpa', 'device.home.arpa', 'host.test', 'host.invalid', 'host.example'):
                with self.subTest(host=host), self.assertRaisesRegex(ResolutionError, 'non_public_address'):
                    self.resolver.resolve(host, self.policy)
            dns.assert_not_called()
            exchange.assert_not_called()


class AnswerFreshnessReviewTests(unittest.TestCase):
    def test_http_age_cannot_turn_expired_positive_ttl_into_usable_zero(self):
        for age in (60, 61, 100):
            with self.subTest(age=age), self.assertRaisesRegex(ResolutionError, 'expired_answer'):
                parse_answer(fixture.wire(), HOST, 1, age=age)

    def test_shortest_cname_ttl_is_also_expiry_not_only_cache_hint(self):
        rows = [fixture.record(HOST, 5, fixture.encoded_name('edge.example.org'), ttl=1),
                fixture.record('edge.example.org', 1, ipaddress.ip_address(IP4).packed, ttl=60)]
        with self.assertRaisesRegex(ResolutionError, 'expired_answer'):
            parse_answer(fixture.wire(records=rows), HOST, 1, age=2)

    def test_true_zero_ttl_is_usable_for_current_transaction_only(self):
        raw = fixture.wire(records=[fixture.record(HOST, 1, ipaddress.ip_address(IP4).packed, ttl=0)])
        result = parse_answer(raw, HOST, 1)
        self.assertEqual(result.addresses, (IP4,))
        self.assertEqual(result.ttl, 0)
        with self.assertRaisesRegex(ResolutionError, 'expired_answer'):
            parse_answer(raw, HOST, 1, age=1)

    def test_high_bit_ttl_is_zero_not_a_long_cache_lease(self):
        raw = fixture.wire(records=[fixture.record(HOST, 1, ipaddress.ip_address(IP4).packed, ttl=2**31)])
        self.assertEqual(parse_answer(raw, HOST, 1).ttl, 0)

    def test_transfer_age_inputs_must_be_finite_and_nonnegative(self):
        for elapsed in (-1, True, float('nan'), float('inf')):
            with self.subTest(elapsed=elapsed), self.assertRaises(ResolutionError):
                parse_answer(fixture.wire(), HOST, 1, elapsed=elapsed)


class ExchangeReviewTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.allowed = True
        self.cancelled = threading.Event()
        self.resolver = PublicResolver(clock=lambda: self.now[0], permission=lambda: self.allowed)
        self.policy = NetworkPolicy(encrypted_dns=True, resolver=self.resolver)
        self.conn = Mock()
        self.response = self.conn.getresponse.return_value
        self.response.status = 200
        self.headers = [('Content-Type', 'application/dns-message'), ('Age', '0')]
        self.response.getheaders.side_effect = lambda: self.headers
        self.response.read1.side_effect = [fixture.wire(), b'']

    def exchange(self):
        # Exercise the real HTTP framing/parser without opening an external socket.
        with patch('vibe_job_radar.network.PinnedHTTPSConnection', return_value=self.conn):
            return self.resolver._exchange(HOST, 1, self.policy, 110, cancelled=self.cancelled)

    def test_permission_revoked_while_connecting_does_not_send_dns_body(self):
        self.conn.connect.side_effect = lambda: setattr(self, 'allowed', False)
        with self.assertRaisesRegex(ResolutionError, 'disabled'):
            self.exchange()
        self.conn.request.assert_not_called()
        self.conn.close.assert_called_once()

    def test_cancelled_while_connecting_does_not_send_dns_body(self):
        self.conn.connect.side_effect = self.cancelled.set
        with self.assertRaisesRegex(ResolutionError, 'paused'):
            self.exchange()
        self.conn.request.assert_not_called()
        self.conn.close.assert_called_once()

    def test_transfer_duration_reduces_validity_and_cache_ttl(self):
        def response():
            self.now[0] += 2
            return self.response
        self.conn.getresponse.side_effect = response
        result = self.exchange()
        self.assertEqual(result.ttl, 58)
        self.assertEqual(result.valid_for, 58)

    def test_short_positive_ttl_expiring_in_transit_is_rejected(self):
        self.response.read1.side_effect = [fixture.wire(records=[
            fixture.record(HOST, 1, ipaddress.ip_address(IP4).packed, ttl=1)]), b'']
        def response():
            self.now[0] += 2
            return self.response
        self.conn.getresponse.side_effect = response
        with self.assertRaisesRegex(ResolutionError, 'expired_answer'):
            self.exchange()

    def test_no_store_zeroes_cache_not_validity(self):
        self.headers.append(('Cache-Control', 'no-store'))
        result = self.exchange()
        self.assertEqual(result.ttl, 0)
        self.assertEqual(result.valid_for, 60)

    def test_duplicate_age_headers_cannot_hide_old_answer(self):
        self.headers += [('Age', '100')]
        with self.assertRaisesRegex(ResolutionError, 'invalid_response'):
            self.exchange()

    def test_cache_control_multiple_fields_keep_no_store(self):
        self.headers += [('Cache-Control', 'no-store'), ('Cache-Control', 'max-age=60')]
        self.assertEqual(self.exchange().ttl, 0)

    def test_expired_http_max_age_is_not_a_fresh_zero_ttl(self):
        self.headers = [('Content-Type', 'application/dns-message'), ('Age', '10'),
                        ('Cache-Control', 'max-age=5')]
        with self.assertRaisesRegex(ResolutionError, 'expired_answer'):
            self.exchange()

    def test_conflicting_or_malformed_max_age_fails_closed(self):
        for cache in ('max-age=30, max-age=60', 'max-age=garbage', 'max-age=30junk',
                      'max-age=-1', 'max-age'):
            with self.subTest(cache=cache):
                self.headers = [('Content-Type', 'application/dns-message'), ('Cache-Control', cache)]
                self.response.read1.side_effect = [fixture.wire(), b'']
                with self.assertRaisesRegex(ResolutionError, 'invalid_response'):
                    self.exchange()

    def test_max_age_zero_allows_current_answer_but_not_reuse(self):
        self.headers.append(('Cache-Control', 'max-age=0'))
        result = self.exchange()
        self.assertEqual(result.ttl, 0)
        self.assertEqual(result.valid_for, 60)

    def test_cancellation_during_last_body_read_discards_result(self):
        def read(size):
            self.cancelled.set()
            return fixture.wire()
        self.response.read1.side_effect = read
        with self.assertRaisesRegex(ResolutionError, 'paused'):
            self.exchange()
        self.conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
