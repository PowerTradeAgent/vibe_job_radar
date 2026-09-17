"""Desktop failure regressions. Artificial lifecycle logs and DNS, no real accounts."""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from vibe_job_radar.guided.browser_health import (environment_report, failed_report,
                                                process_exit_facts, BrowserStartupError)
from vibe_job_radar.guided.browser_install import install_commands, CommandResult
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.workspace import Workspace, InputError
from vibe_job_radar.network_policy import NetworkPolicy
from vibe_job_radar.dns_wire import Answer, ResolutionError


def lifecycle(code=3221226356, pid=73):
    return (f'BrowserType.launch: Target page, context or browser has been closed\n'
            f'<launched> pid={pid}\n  - [pid={pid}] <gracefully close start>\n'
            f'  - [pid={pid}] taskkill stderr: process not found\n'
            f'  - [pid={pid}] <process did exit: exitCode={code}, signal=null>\n')


def dns(*ips):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in ips]


class NativeCrashTests(unittest.TestCase):
    def report(self, log):
        return failed_report({**environment_report(), 'stage':'launch', 'launch_tested':True,
                              'executable_exists':True}, RuntimeError(log))

    def test_user_crash_is_heap_corruption_not_missing_executable(self):
        r=self.report(lifecycle())
        self.assertEqual(r['code'],'browser_native_heap_corruption')
        self.assertEqual(r['process_exit_hex'],'0xC0000374')
        self.assertEqual(r['process_status'],'STATUS_HEAP_CORRUPTION')
        self.assertTrue(r['process_started']); self.assertTrue(r['executable_exists'])
        self.assertFalse(r['ready']); self.assertFalse(r['cause_confirmed'])

    def test_signed_decimal_and_hex_status_match_unsigned(self):
        for code in (-1073740940, '0xc0000374', 3221226356):
            with self.subTest(code=code):
                self.assertEqual(self.report(lifecycle(code))['code'],'browser_native_heap_corruption')

    def test_extract_before_truncation_and_preserve_redaction(self):
        r=self.report(lifecycle()+'\n'+'x'*7000+'\nCookie: fake-session-value\nhttps://user:password@host/\n')
        self.assertEqual(r['process_exit_hex'],'0xC0000374')
        self.assertLessEqual(len(r['error_summary']),4000)
        self.assertNotIn('fake-session-value',r['error_summary'])
        self.assertNotIn('user:password',json.dumps(r))

    def test_taskkill_missing_process_is_cleanup_not_cause(self):
        r=self.report(lifecycle())
        self.assertNotEqual(r['code'],'browser_executable_missing')
        self.assertEqual(r['process_exit_code'],3221226356)

    def test_normal_zero_exit_is_not_heap_corruption(self):
        r=self.report(lifecycle(0))
        self.assertEqual(r['code'],'browser_launch_failed'); self.assertFalse(r['ready'])

    def test_targetclosed_without_native_evidence_is_not_guessed(self):
        r=self.report('TargetClosedError: Target page closed')
        self.assertNotIn('process_status',r);self.assertEqual(r['code'],'browser_launch_failed')

    def test_unrelated_process_or_command_argument_is_not_a_crash(self):
        logs=['--exitCode=3221226356', '<launched> pid=99\n[pid=73] <process did exit: exitCode=3221226356, signal=null>']
        for log in logs:
            self.assertNotIn('process_exit_code',process_exit_facts(log))

    def test_duplicate_call_log_ok_but_conflicting_exits_not_guessed(self):
        self.assertEqual(self.report(lifecycle()*2)['process_exit_hex'],'0xC0000374')
        self.assertNotIn('process_status',self.report(lifecycle()+lifecycle(1)))

    def test_permissions_keep_their_classification_and_no_repair_bypass(self):
        r=failed_report({'stage':'launch'},PermissionError('access is denied '+lifecycle()))
        self.assertEqual(r['code'],'browser_permission_denied')

    def test_os_metadata_has_no_host_name_or_environment_dump(self):
        r=environment_report()
        self.assertEqual(set(r['os']),{'system','release','version','machine'})
        self.assertIn('--force chromium',r['commands']['repair_browser'])


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
        self.probe=MagicMock(return_value={**environment_report(),'ready':True,'message':'checked'})
        self.installer=MagicMock(return_value=CommandResult(0,''))
        self.s=GuidedService(self.w,health_probe=self.probe,installer=self.installer)
        self.addCleanup(self.s.close)

    def wait(self):
        deadline=time.monotonic()+5
        while self.s.state()['busy'] and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(self.s.state()['busy'])

    def test_matching_repair_forces_download_without_upgrading_shared_sdk(self):
        commands=install_commands('reinstall')
        self.assertEqual(commands,(('browser_download',[sys.executable,'-m','playwright','install','--force','chromium']),))
        self.s.install({'consent':True,'mode':'reinstall'});self.wait()
        self.installer.assert_called_once();self.probe.assert_called_once()
        self.assertTrue(self.s.state()['browser_health']['ready'])

    def test_upgrade_is_explicit_and_installs_matched_browser(self):
        c=install_commands('upgrade')
        self.assertIn('--upgrade',c[0][1]);self.assertIn('--force',c[1][1])
        self.assertTrue(all(item[1][0]==sys.executable for item in c))

    def test_upgrade_requires_restart_not_stale_same_process_probe(self):
        self.s.install({'consent':True,'mode':'upgrade'});self.wait()
        r=self.s.state();self.assertEqual(r['installation'],'restart_required')
        self.assertFalse(r['browser_health']['ready']);self.probe.assert_not_called()
        self.s.check_browser({});self.wait();self.probe.assert_not_called()
        self.s.factory=MagicMock()
        with self.assertRaises(BrowserStartupError):self.s._backend({'id':'a'*32})
        self.s.factory.assert_not_called()
        self.assertFalse(self.w.db.exists())

    def test_failed_upgrade_never_claims_ready_or_downloads_browser(self):
        self.installer.return_value=CommandResult(1,'simulated package failure')
        self.s.install({'consent':True,'mode':'upgrade'});self.wait()
        self.installer.assert_called_once();self.probe.assert_not_called()
        self.assertFalse(self.s.state()['browser_health']['ready'])
        self.s.check_browser({});self.wait()
        self.assertEqual(self.s.state()['browser_health']['code'],'browser_restart_required')

    def test_reinstall_zero_exit_still_requires_successful_launch(self):
        self.probe.return_value=failed_report({**environment_report(),'stage':'launch'},RuntimeError(lifecycle()))
        self.s.install({'consent':True,'mode':'reinstall'});self.wait()
        self.assertEqual(self.s.state()['installation'],'installed_not_ready')
        self.assertEqual(self.s.state()['browser_health']['code'],'browser_native_heap_corruption')

    def test_unknown_mode_or_unconfirmed_change_rejected(self):
        for d in ({'consent':False,'mode':'upgrade'},{'consent':True,'mode':'shell'},
                  {'consent':True,'mode':['upgrade']},{'consent':True,'path':'C:/secret'},
                  {'consent':True,'mode':'upgrade','version':'1.57.0'},None):
            with self.subTest(d=d), self.assertRaises(InputError):self.s.install(d)
        self.installer.assert_not_called()

    def test_active_session_is_not_closed_by_repair_or_upgrade(self):
        backend=MagicMock();self.s._backends['a']=backend
        for mode in ('reinstall','upgrade'):
            with self.assertRaises(InputError):self.s.install({'consent':True,'mode':mode})
        backend.close.assert_not_called();self.s._backends.clear()

    def test_ensure_mode_still_does_not_implicitly_upgrade(self):
        self.assertNotIn('--upgrade',install_commands()[0][1])
        self.assertNotIn('--force',install_commands()[1][1])

    def test_reinstall_failure_stops_without_probe(self):
        self.installer.return_value=CommandResult(-1,'failed',timed_out=True)
        self.s.install({'consent':True,'mode':'reinstall'});self.wait()
        self.probe.assert_not_called()
        self.assertEqual(self.s.state()['installation'],'dependency_install_timeout')


class EffectiveDNSTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name);self.s=GuidedService(self.w);self.addCleanup(self.s.close)
        self.p=patch('vibe_job_radar.network_policy.NetworkPolicy.capture',return_value=NetworkPolicy());self.p.start();self.addCleanup(self.p.stop)
        self.net=patch('socket.getaddrinfo',return_value=dns('198.18.1.251'));self.net.start();self.addCleanup(self.net.stop)
        ex=patch.object(self.w.dns_resolver,'_exchange',side_effect=lambda host,kind,*a,**kw: Answer(('93.184.216.34',) if kind==1 else (),60,host))
        self.exchange=ex.start();self.addCleanup(ex.stop)

    def enable(self):
        self.w.network_preferences({'mode':'fake_ip_doh','revision':0,'consent':True})

    def test_fake_ip_without_consent_is_actionable_not_broken_vpn(self):
        r=self.s.diagnose({'platform':'liepin'})
        self.assertEqual(r['code'],'encrypted_dns_consent_required');self.assertFalse(r['passed'])
        self.assertFalse(r['effective_resolution']['tested']);self.exchange.assert_not_called()
        self.assertIn('保存',r['message']);self.assertFalse(self.w.db.exists())

    def test_opted_in_check_uses_actual_workspace_resolver_and_reports_both(self):
        self.enable();r=self.s.diagnose({'platform':'liepin'})
        self.assertTrue(r['passed']);self.assertFalse(r['system_dns']['passed'])
        self.assertEqual(r['addresses'][0]['ip'],'198.18.1.251')
        self.assertEqual(r['effective_resolution']['addresses'],['93.184.216.34'])
        self.assertEqual(self.exchange.call_count,2)
        self.assertFalse(r['target_connection_tested']);self.assertFalse(r['browser_tested'])
        self.assertEqual(self.s.ledger.summary('liepin')['request']['day'],0)

    def test_repeat_does_not_reset_shared_resolver_budget_and_uses_cache(self):
        self.enable();self.s.diagnose({'platform':'liepin'});r=self.s.diagnose({'platform':'liepin'})
        self.assertTrue(r['effective_resolution']['cache_reused']);self.assertEqual(self.exchange.call_count,2)
        self.assertEqual(len(self.w.dns_resolver._requests),1)

    def test_hard_tls_failure_is_visible_and_not_a_successful_dns_check(self):
        self.enable();self.exchange.side_effect=ResolutionError('encrypted_dns_tls_failed')
        r=self.s.diagnose({'platform':'liepin'})
        self.assertEqual(r['code'],'encrypted_dns_tls_failed');self.assertFalse(r['passed'])
        self.assertFalse(r['effective_resolution']['passed']);self.assertEqual(self.exchange.call_count,1)

    def test_private_and_mixed_answers_never_start_doh(self):
        self.enable()
        for ips in (('10.0.0.1',),('198.18.1.251','93.184.216.34'),('198.18.1.251','10.0.0.1')):
            with patch('socket.getaddrinfo',return_value=dns(*ips)):
                r=self.s.diagnose({'platform':'liepin'})
            self.assertFalse(r['passed']);self.assertEqual(r['code'],'non_public_address')
        self.exchange.assert_not_called()

    def test_normal_public_dns_needs_no_third_party_query(self):
        self.enable()
        with patch('socket.getaddrinfo',return_value=dns('93.184.216.34')):
            r=self.s.diagnose({'platform':'liepin'})
        self.assertTrue(r['passed']);self.assertEqual(r['effective_resolution']['source'],'system_dns')
        self.exchange.assert_not_called()

    def test_dns_failure_is_not_repaired_as_fake_ip(self):
        self.enable()
        with patch('socket.getaddrinfo',side_effect=socket.gaierror):r=self.s.diagnose({'platform':'liepin'})
        self.assertEqual(r['code'],'dns_error');self.exchange.assert_not_called()

    def test_revoked_consent_before_second_family_returns_failure(self):
        self.enable()
        def revoke(host,*a,**kw):
            self.w.dns_resolver.permission=lambda:False
            return Answer(('93.184.216.34',),60,host)
        self.exchange.side_effect=revoke
        r=self.s.diagnose({'platform':'liepin'})
        self.assertFalse(r['passed']);self.assertEqual(r['code'],'encrypted_dns_disabled')

    def test_active_session_is_not_changed_by_new_policy_check(self):
        self.enable();backend=MagicMock();self.s._backends['a']=backend
        r=self.s.diagnose({'platform':'liepin'})
        self.assertTrue(r['existing_sessions_may_use_previous_policy']);backend.close.assert_not_called()
        self.s._backends.clear()

    def test_bad_proxy_blocks_dns_before_query(self):
        self.p.stop()
        with patch('vibe_job_radar.network_policy.NetworkPolicy.capture',return_value=NetworkPolicy(error='local_proxy_configuration_invalid')),patch('socket.getaddrinfo') as d:
            r=self.s.diagnose({'platform':'liepin'})
        self.assertFalse(r['passed']);d.assert_not_called();self.exchange.assert_not_called()


if __name__=='__main__':unittest.main()
