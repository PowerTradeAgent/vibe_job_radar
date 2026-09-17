"""Certificate engine selection and repair: no external requests or trusted roots."""
from __future__ import annotations

import json
import os
import ssl
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

from vibe_job_radar import tls_context
from vibe_job_radar.network import PinnedHTTPSConnection
from vibe_job_radar.network_policy import NetworkPolicy
from vibe_job_radar.tls_diagnostic import failure_details
from vibe_job_radar.guided.browser_install import install_commands, CommandResult
from vibe_job_radar.guided.service import GuidedService
from vibe_job_radar.workspace import Workspace, InputError


class TLSContextTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'SSL_CERT_FILE': '', 'SSL_CERT_DIR': ''})
        self.env.start(); self.addCleanup(self.env.stop)
        self.win = patch.object(tls_context, 'is_windows', return_value=True)
        self.win.start(); self.addCleanup(self.win.stop)
        self.version = patch.object(tls_context, '_version', return_value='0.10.4')
        self.version.start(); self.addCleanup(self.version.stop)

    def native(self):
        factory = Mock(side_effect=lambda p: ssl.SSLContext(p))
        module = types.SimpleNamespace(SSLContext=factory, __version__='0.10.4')
        return module, factory

    def test_native_context_requires_chain_hostname_and_modern_tls(self):
        module, factory = self.native()
        original = ssl.SSLContext
        with patch.object(tls_context, '_load_native', return_value=module):
            ctx = tls_context.create_client_context()
        factory.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(ctx.radar_tls_engine, 'windows_cryptoapi')
        self.assertIs(ssl.SSLContext, original)

    def test_no_global_injection_or_ca_import(self):
        module, _ = self.native()
        module.inject_into_ssl = Mock(side_effect=AssertionError('global modification'))
        with patch.object(tls_context, '_load_native', return_value=module):
            ctx = tls_context.create_client_context()
        module.inject_into_ssl.assert_not_called()
        self.assertEqual(ctx.cert_store_stats()['x509'], 0)  # fake engine, no peer/root loaded

    def test_missing_optional_component_preserves_verified_legacy_path(self):
        with patch.object(tls_context, '_version', return_value=None), patch.object(tls_context, '_load_native') as load:
            ctx = tls_context.create_client_context()
        self.assertTrue(ctx.check_hostname); self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(ctx.radar_tls_engine, 'openssl_default'); load.assert_not_called()

    def test_native_import_failure_does_not_silently_choose_openssl(self):
        with patch.object(tls_context, '_load_native', side_effect=ImportError('PRIVATE')):
            with self.assertRaises(tls_context.TLSConfigurationError) as e:
                tls_context.create_client_context()
        self.assertNotIn('PRIVATE', str(e.exception))

    def test_incompatible_version_fails_before_import_or_connection(self):
        for version in ('0.10.3', '0.11.0', 'not-a-version', '0.10.4rc1'):
            with self.subTest(version=version), patch.object(tls_context, '_version', return_value=version), patch.object(tls_context, '_load_native') as load:
                with self.assertRaises(tls_context.TLSConfigurationError):
                    tls_context.create_client_context()
                load.assert_not_called()

    def test_imported_old_module_requires_restart(self):
        module, factory = self.native(); module.__version__ = '0.10.3'
        with patch.object(tls_context, '_load_native', return_value=module):
            with self.assertRaisesRegex(tls_context.TLSConfigurationError, 'restart_required'):
                tls_context.create_client_context()
        factory.assert_not_called()

    def test_explicit_ca_settings_are_not_ignored_or_exported(self):
        for name in ('SSL_CERT_FILE', 'SSL_CERT_DIR'):
            with patch.dict(os.environ, {name: 'PRIVATE-path'}), patch.object(tls_context, '_load_native') as load, patch('ssl.create_default_context', return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as legacy:
                ctx = tls_context.create_client_context(); report = tls_context.status()
                self.assertEqual(report['reason'], 'explicit_ca_environment')
                self.assertFalse(report['repair_available']); self.assertNotIn('PRIVATE', json.dumps(report))
                self.assertEqual(ctx.radar_tls_engine, 'openssl_default')
                legacy.assert_called_once(); load.assert_not_called()

    def test_non_windows_preserves_default_tls_without_optional_import(self):
        with patch.object(tls_context, 'is_windows', return_value=False), patch.object(tls_context, '_load_native') as load:
            ctx = tls_context.create_client_context()
        self.assertEqual(ctx.radar_tls_engine, 'openssl_default'); load.assert_not_called()

    def test_status_never_claims_connection_success(self):
        with patch('socket.create_connection') as dial:
            result = tls_context.status()
        self.assertFalse(result['tls_tested']); self.assertFalse(result['certificate_imported'])
        self.assertEqual(result['certificate_retrieval'], 'windows_managed')
        dial.assert_not_called()

    def test_pinned_connection_consumes_factory_before_dial(self):
        module, factory = self.native()
        with patch.object(tls_context, '_load_native', return_value=module), patch('socket.create_connection') as dial:
            conn = PinnedHTTPSConnection('cloudflare-dns.com', ('1.1.1.1', '1.0.0.1'), 2, network_policy=NetworkPolicy())
        self.assertEqual(conn._context.radar_tls_engine, 'windows_cryptoapi')
        factory.assert_called_once(); dial.assert_not_called(); conn.close()

    def test_native_certificate_rejection_does_not_retry_second_ip_or_validator(self):
        context = Mock(); context.wrap_socket.side_effect = ssl.SSLCertVerificationError('untrusted')
        with patch('vibe_job_radar.network.create_client_context', return_value=context) as make, patch('socket.create_connection') as dial:
            conn = PinnedHTTPSConnection('cloudflare-dns.com', ('1.1.1.1','1.0.0.1'), 10, network_policy=NetworkPolicy())
            with self.assertRaises(ssl.SSLCertVerificationError): conn.connect()
        make.assert_called_once(); dial.assert_called_once()
        self.assertEqual(conn.connection_attempts[0]['outcome'], 'tls_rejected')
        conn.close()

    def test_windows_policy_error_number_survives_without_private_text(self):
        for code, reason in [(0x800B010A, 'issuer_unavailable'), (0x800B0109,'untrusted_root'),
                             (0x800B010F,'hostname_mismatch'),(0x800B0101,'expired_or_not_yet_valid'),
                             (0x800B010C,'revoked')]:
            exc = ssl.SSLCertVerificationError('PRIVATE issuer'); exc.verify_code = code
            exc.verify_message = 'PRIVATE hostname'
            detail = failure_details(exc, phase='tls_handshake')
            self.assertEqual(detail['verify_code'], code)
            self.assertEqual(detail['verify_code_namespace'], 'windows_chain_policy')
            self.assertEqual(detail['verification_reason'], reason)
            self.assertNotIn('PRIVATE', json.dumps(detail))

    def test_native_store_enumeration_unavailable_is_not_zero_cas(self):
        class NativeLike(ssl.SSLContext):
            def cert_store_stats(self): raise NotImplementedError
        ctx = NativeLike(ssl.PROTOCOL_TLS_CLIENT); ctx.radar_tls_engine = 'windows_cryptoapi'
        conn = types.SimpleNamespace(_context=ctx)
        detail = failure_details(ssl.SSLCertVerificationError('private'), phase='tls_handshake', connection=conn)
        self.assertIsNone(detail['trust_store_counts'])
        self.assertEqual(detail['trust_engine'], 'windows_cryptoapi')
        self.assertTrue(detail['tls_policy']['check_hostname'])

    def test_native_initialization_error_classified_separately(self):
        detail = failure_details(tls_context.TLSConfigurationError(), phase='tls_context')
        self.assertEqual(detail['category'], 'configuration')
        self.assertFalse(detail['tls_handshake_completed'])


class TLSRepairServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.service = GuidedService(Workspace(self.tmp.name))
        self.addCleanup(self.service.close)

    def test_fixed_install_does_not_upgrade_browser_or_import_certificate(self):
        commands = install_commands('tls')
        self.assertEqual(commands, (('tls_component', [sys.executable, '-m', 'pip', 'install', tls_context.TRUSTSTORE_REQUIREMENT]),))
        self.assertNotIn('--trusted-host', str(commands)); self.assertNotIn('chromium', str(commands))

    def test_explicit_confirmation_and_no_extra_fields(self):
        for value in ({'mode':'tls'}, {'mode':'tls','consent':False},
                      {'mode':'tls','consent':True,'certificate':'UNTRUSTED'},
                      {'mode':'tls','consent':True,'command':'something'}):
            with self.assertRaises(InputError): self.service.install(value)

    def test_install_not_offered_on_non_windows(self):
        with patch.object(tls_context, 'is_windows', return_value=False):
            with self.assertRaises(InputError): self.service.install({'mode':'tls','consent':True})

    def test_explicit_ca_configuration_cannot_be_overwritten(self):
        with patch.object(tls_context, 'is_windows', return_value=True), patch.dict(os.environ, {'SSL_CERT_FILE':'PRIVATE-root.pem'}):
            with self.assertRaises(InputError): self.service.install({'mode':'tls','consent':True})

    def test_existing_sessions_must_be_stopped_first(self):
        self.service._backends['test'] = Mock()
        with patch.object(tls_context, 'status', return_value={'windows':True,'explicit_ca_environment':False}):
            with self.assertRaises(InputError): self.service.install({'mode':'tls','consent':True})

    def test_repair_preserves_browser_and_requires_restart_without_network(self):
        self.service._selected_browser = 'msedge'
        self.service._installer = Mock(return_value=CommandResult(0,'installed'))
        with patch('socket.create_connection') as dial, patch.object(self.service, '_health_probe') as probe:
            self.service._install_browser('tls')
            result = self.service.diagnose({'platform':'boss'})
        self.assertEqual(self.service._selected_browser, 'msedge')
        self.assertTrue(self.service._tls_restart_required)
        self.assertFalse(self.service._browser_health['ready'])
        self.assertEqual(result['code'], 'tls_component_restart_required')
        self.assertFalse(result['passed']); dial.assert_not_called(); probe.assert_not_called()
        self.assertFalse(self.service.workspace.db.exists())

    def test_partial_install_never_claims_ready_or_runs_browser(self):
        self.service._installer = Mock(return_value=CommandResult(1,'install failed'))
        self.service._install_browser('tls')
        self.assertEqual(self.service._last_install, 'dependency_install_failed')
        self.assertTrue(self.service._tls_restart_required)
        self.assertFalse(self.service._browser_health['ready'])
        self.assertEqual(self.service._setup['steps'][0]['returncode'], 1)

    def test_state_describes_tls_without_initializing_or_installing(self):
        with patch.object(self.service, '_installer') as install, patch.object(tls_context, '_load_native') as load:
            result = self.service.state()
        self.assertIn('tls_environment', result)
        self.assertFalse(result['tls_environment']['tls_tested'])
        install.assert_not_called(); load.assert_not_called()


if __name__ == '__main__': unittest.main()
