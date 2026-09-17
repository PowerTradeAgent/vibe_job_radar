"""The opt-in observer cannot turn inconclusive robots into access permission."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from vibe_job_radar.network import FetchError, Response

SPEC = importlib.util.spec_from_file_location('radar_site_observer', Path(__file__).resolve().parents[1]/'scripts'/'observe_site_robots.py')
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


class SiteObservationTests(unittest.TestCase):
    def perform(self, robots, page=None):
        transport = Mock()
        values = [robots] if page is None else [robots, page]
        transport.public_get.side_effect = values
        with patch.object(observer, 'HOSTS', ('www.liepin.com',)), \
             patch.object(observer, 'PLATFORMS', ('liepin',)), \
             patch.object(observer, 'SafeHTTP', return_value=transport), \
             patch.object(observer.time, 'sleep') as sleep:
            result = observer.observe()['observations'][0]
        return result, transport, sleep

    @staticmethod
    def response(body=b'User-agent: *\nAllow: /\n', status=200, mime='text/plain', **headers):
        return Response(status, {'content-type':mime, **headers}, body, 'https://www.liepin.com/robots.txt')

    def test_valid_simple_rule_permits_one_list_and_records_origins_only(self):
        page = self.response(b'<link rel="stylesheet" href="https://static.example.org/private.css?token=secret"><script src="/app.js"></script>', mime='text/html')
        result, wire, sleep = self.perform(self.response(), page)
        self.assertTrue(result['list_requested']); self.assertFalse(result['job_requested'])
        self.assertEqual(wire.public_get.call_count, 2)
        self.assertIn({'kind':'stylesheet','host':'static.example.org'},result['resource_origins'])
        self.assertNotIn('secret', json.dumps(result)); self.assertNotIn('private.css',json.dumps(result))
        sleep.assert_called_once_with(2)

    def test_html_empty_and_undecodable_rules_never_trigger_search(self):
        for body, mime in [(b'<html><body>User-agent: *</body>', 'text/html'),
                           (b'', 'text/plain'), (b'User-agent: *\nAllow: /\n\xff', 'text/plain'),
                           (b'<script>ignored</script>\nUser-agent: *\nAllow: /', 'text/plain')]:
            with self.subTest(body=body):
                result, wire, sleep = self.perform(self.response(body, mime=mime))
                self.assertFalse(result['list_requested']); wire.public_get.assert_called_once()
                sleep.assert_not_called()

    def test_redirect_and_non_success_do_not_request_another_url(self):
        for status in (301, 302, 404, 500):
            with self.subTest(status=status):
                result, wire, _ = self.perform(self.response(status=status, location='https://other.example.org/login?password=secret'))
                self.assertFalse(result['list_requested']); wire.public_get.assert_called_once()
                self.assertNotIn('password', json.dumps(result))

    def test_explicit_disallow_does_not_request_search(self):
        result, wire, sleep = self.perform(self.response(b'User-agent: *\nDisallow: /\n'))
        self.assertFalse(result['existing_parser_allows_search'])
        self.assertFalse(result['list_requested']); wire.public_get.assert_called_once(); sleep.assert_not_called()

    def test_extended_syntax_requires_review_instead_of_guessing_allow(self):
        for rule in ('Disallow: /private*', 'Disallow: /private$'):
            with self.subTest(rule=rule):
                result, wire, _ = self.perform(self.response(('User-agent: *\n'+rule+'\n').encode()))
                self.assertTrue(result['extended_rules_require_separate_review'])
                self.assertFalse(result['list_requested']); wire.public_get.assert_called_once()

    def test_stricter_wait_is_respected_or_deferred_without_source_request(self):
        page = self.response(b'<html></html>', mime='text/html')
        result, wire, sleep = self.perform(self.response(b'User-agent: *\nAllow: /\nCrawl-delay: 5\n'), page)
        self.assertTrue(result['list_requested']); sleep.assert_called_once_with(5)
        result, wire, sleep = self.perform(self.response(b'User-agent: *\nAllow: /\nCrawl-delay: 60\n'))
        self.assertFalse(result['list_requested']); self.assertEqual(result['list_observation'],'deferred_publisher_wait')
        wire.public_get.assert_called_once(); sleep.assert_not_called()

    def test_refusal_has_no_retry_and_does_not_leak_exception_text(self):
        result, wire, _ = self.perform(FetchError('http_403', 'private diagnostic'))
        self.assertEqual(result['outcome'], 'http_403'); wire.public_get.assert_called_once()
        self.assertNotIn('private diagnostic', json.dumps(result))

    def test_resource_inventory_is_bounded_passive_and_credential_free(self):
        parser = observer.ResourceOrigins('https://www.liepin.com/search')
        parser.feed('<script src="https://user:secret@private.example.org/x"></script><script src="http://other.example.org/x"></script><script src="https://other.example.org:8888/x"></script>')
        self.assertEqual(parser.values,set())
        parser.feed(''.join(f'<img src="https://site{i}.example.org/secret?token=x">' for i in range(100)))
        self.assertEqual(len(parser.values),40)
        self.assertNotIn('secret',json.dumps(sorted(parser.values)))


if __name__ == '__main__': unittest.main()
