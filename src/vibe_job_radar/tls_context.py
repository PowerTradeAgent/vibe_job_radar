"""Verified, per-connection TLS contexts; Windows uses its native chain engine.

A Windows CA *snapshot* in OpenSSL is not Windows chain construction. Truststore
uses CryptoAPI to build and verify chains against the OS trust policy, including
missing intermediates. Never inject globally, trust peer certificates, or retry a
rejected connection with a different validator. Explicit CA environment overrides
keep the existing OpenSSL path. No package is installed while making a request.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import ssl
import sys

TRUSTSTORE_REQUIREMENT = 'truststore>=0.10.4,<0.11'
NATIVE_DISCLOSURE = (
    'Windows 系统证书验证使用系统已有的信任策略，不导入或自动信任网站证书。'
    'Windows 可按自身策略下载缺少的中间证书；这些证书服务请求由系统路由管理，'
    '不由应用的岗位代理或采集配额管理。证书失败仍停止，不重试其他验证器。'
)


class TLSConfigurationError(ssl.SSLError):
    """A selected native validator could not initialize. Never fall back."""
    def __init__(self, code='tls_native_backend_unavailable'):
        self.code = code
        super().__init__(code)


def is_windows() -> bool:
    return sys.platform == 'win32'


def _version() -> str | None:
    try:
        return importlib.metadata.version('truststore')
    except importlib.metadata.PackageNotFoundError:
        return None


def _supported(version) -> bool:
    match = re.fullmatch(r'0\.10\.([0-9]{1,6})', version or '')
    return bool(match and int(match[1]) >= 4)


def status() -> dict:
    """Component/configuration facts only: never imply a successful handshake."""
    windows = is_windows()
    override = bool(os.environ.get('SSL_CERT_FILE') or os.environ.get('SSL_CERT_DIR'))
    version = _version() if windows else None
    if not windows:
        engine, reason = 'openssl_default', 'non_windows'
    elif override:
        engine, reason = 'openssl_default', 'explicit_ca_environment'
    elif version is None:
        engine, reason = 'openssl_default', 'native_component_missing'
    elif not _supported(version):
        engine, reason = 'unavailable', 'native_component_incompatible'
    else:
        engine, reason = 'windows_cryptoapi', 'native_system_policy'
    return {
        'engine': engine, 'reason': reason, 'windows': windows,
        'truststore_version': version if version and re.fullmatch(r'[0-9.a-z+-]{1,40}', version) else None,
        'explicit_ca_environment': override,
        'repair_available': windows and not override and reason == 'native_component_missing',
        'tls_tested': False, 'certificate_imported': False,
        'certificate_retrieval': 'windows_managed' if engine == 'windows_cryptoapi' else 'not_app_managed',
        'disclosure': NATIVE_DISCLOSURE,
    }


def _load_native():
    return importlib.import_module('truststore')


def create_client_context() -> ssl.SSLContext:
    """Select before dialing; a failed verifier never causes retry/fallback.

    A missing optional component retains the old, fully verified OpenSSL path.
    An installed-but-broken/incompatible native component is an error, not a
    reason to silently change certificate policy. Restart after component repair.
    """
    selected = status()
    if selected['engine'] == 'unavailable':
        raise TLSConfigurationError('tls_native_backend_incompatible')
    if selected['engine'] == 'windows_cryptoapi':
        try:
            module = _load_native()
            if getattr(module, '__version__', None) != selected['truststore_version']:
                raise TLSConfigurationError('tls_native_backend_restart_required')
            context = module.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        except TLSConfigurationError:
            raise
        except Exception as exc:
            raise TLSConfigurationError() from exc
    else:
        context = ssl.create_default_context()
    # ssl.SSLContext permits application metadata. It contains no host/path/CA.
    context.radar_tls_engine = selected['engine']
    context.radar_tls_reason = selected['reason']
    return context
