"""Read-only diagnosis regressions; no proxy/DNS/HTTP behavior is changed."""
import json
import os
import unittest
from unittest.mock import patch
from vibe_job_radar.network_environment import inspect_environment


class EnvironmentTests(unittest.TestCase):
    def test_https_proxy_is_detected_but_not_claimed_applied(self):
        r=inspect_environment(discover=lambda:{'https':'http://127.0.0.1:7890'})
        self.assertTrue(r['proxy_configuration_detected'])
        self.assertFalse(r['collector_applies_static_proxy'])
        self.assertEqual(r['https_candidate_key'],'https')
    def test_all_proxy_is_detected(self):
        r=inspect_environment(discover=lambda:{'all':'socks5://127.0.0.1:1080'})
        self.assertEqual(r['https_candidate_key'],'all')
    def test_http_only_proxy_does_not_claim_https_route(self):
        r=inspect_environment(discover=lambda:{'http':'http://127.0.0.1:7890'})
        self.assertFalse(r['proxy_configuration_detected'])
        self.assertIn('http',r['proxy_candidates'])
    def test_credentials_removed_from_endpoint_and_result(self):
        r=inspect_environment(discover=lambda:{'https':'http://privateuser:secret-password@127.0.0.1:7890'})
        self.assertTrue(r['proxy_candidates']['https']['credentials_present'])
        self.assertEqual(r['proxy_candidates']['https']['endpoint'],'http://127.0.0.1:7890')
        self.assertNotIn('privateuser',json.dumps(r));self.assertNotIn('secret-password',json.dumps(r))
    def test_query_credentials_are_not_echoed(self):
        r=inspect_environment(discover=lambda:{'https':'http://host:7890/path?token=SECRET'})
        self.assertNotIn('SECRET',json.dumps(r))
        self.assertFalse(r['proxy_candidates']['https']['valid_endpoint_shape'])
    def test_ipv6_endpoint_formats_correctly(self):
        r=inspect_environment(discover=lambda:{'https':'http://[::1]:7890'})
        self.assertEqual(r['proxy_candidates']['https']['endpoint'],'http://[::1]:7890')
    def test_no_proxy_is_observed_not_executed(self):
        r=inspect_environment(discover=lambda:{'https':'http://127.0.0.1:7890','no':'.zhipin.com'})
        self.assertTrue(r['standard_no_proxy_match'])
        self.assertFalse(r['collector_applies_static_proxy'])
    def test_no_proxies_does_not_assert_vpn_off(self):
        r=inspect_environment(discover=lambda:{})
        self.assertIsNone(r['tun_or_vpn_detected']);self.assertIsNone(r['virtual_machine_detected'])
    def test_discovery_failure_not_reported_as_no_proxy(self):
        def failed():raise RuntimeError('http://user:secret@proxy')
        r=inspect_environment(discover=failed)
        self.assertEqual(r['configuration_read_error_type'],'RuntimeError')
        self.assertNotIn('secret',json.dumps(r))
    def test_malformed_shape_is_safe(self):
        r=inspect_environment(discover=lambda:{'https':'http://[bad'})
        self.assertFalse(r['proxy_candidates']['https']['valid_endpoint_shape'])
    def test_does_not_connect_resolve_or_change_env(self):
        before=dict(os.environ)
        with patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')) as dns,patch('socket.create_connection',side_effect=AssertionError('network forbidden')) as conn:
            inspect_environment(discover=lambda:{'https':'socks5://127.0.0.1:1080'})
        self.assertEqual(before,dict(os.environ));dns.assert_not_called();conn.assert_not_called()
    def test_empty_and_overlong_proxy_safe(self):
        for value in ('x'*3000,'http://bad\nhost:7890',123):
            r=inspect_environment(discover=lambda:{'https':value})
            self.assertFalse(r['proxy_candidates']['https']['valid_endpoint_shape'])
    def test_non_mapping_configuration_is_reported(self):
        r=inspect_environment(discover=lambda:['unexpected'])
        self.assertEqual(r['configuration_read_error_type'],'TypeError')
    def test_unrelated_environment_values_are_not_returned(self):
        with patch.dict(os.environ,{'UNRELATED_SECRET':'NOT-FOR-OUTPUT'}):
            r=inspect_environment(discover=lambda:{})
        self.assertNotIn('NOT-FOR-OUTPUT',json.dumps(r))


if __name__=='__main__':unittest.main()
