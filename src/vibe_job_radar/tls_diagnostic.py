"""Bounded metadata from an already-failed TLS operation; never another probe.

No exception strings, peer certificates, paths, query names, headers or secrets.
The original TLS verification, route and retry decisions stay authoritative.
"""
from __future__ import annotations

import os
import re
import ssl

from .utils import utc_now

VERIFY_REASONS = {
    2: 'issuer_unavailable', 9: 'not_yet_valid', 10: 'expired',
    18: 'self_signed_leaf', 19: 'self_signed_chain', 20: 'issuer_unavailable',
    21: 'chain_unverifiable', 23: 'revoked', 27: 'untrusted',
    62: 'hostname_mismatch', 64: 'ip_address_mismatch',
}
HINTS = {
    'certificate_verification': '证书验证未通过。按 verify_code 核对时间、证书主机名及可信链；不要关闭校验或自动信任收到的证书。',
    'peer_closed': 'TLS 连接在完整握手或响应前结束，尚不能认定为根证书缺失。请核对当前允许的网络路线及其中间设备，不要靠重装浏览器修复。',
    'protocol_mismatch': '收到的协议数据不符合 TLS。请核对现有路线或代理协议是否与实际服务一致，不能据此断言某个软件有问题。',
    'tls_protocol': 'TLS 协商或读取失败。请根据 ssl_reason 核对当前允许的路线和 TLS 环境；不能把所有此类错误当作证书缺失。',
}


def _symbol(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Z0-9_]{1,96}', value) else None


def failure_details(exc: ssl.SSLError, *, phase: str, connection=None,
                    bootstrap=(), policy_id='') -> dict:
    """Extract only allowlisted fields; no str(exc) or additional network calls."""
    if not isinstance(exc, ssl.SSLError):
        raise TypeError('TLS exception required')
    reason = _symbol(getattr(exc, 'reason', None))
    if isinstance(exc, ssl.SSLCertVerificationError):
        category, error_type = 'certificate_verification', 'SSLCertVerificationError'
    elif isinstance(exc, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        category, error_type = 'peer_closed', ('SSLEOFError' if isinstance(exc, ssl.SSLEOFError) else 'SSLZeroReturnError')
    elif reason in {'UNEXPECTED_EOF_WHILE_READING'}:
        category, error_type = 'peer_closed', 'SSLError'
    elif reason in {'WRONG_VERSION_NUMBER', 'UNKNOWN_PROTOCOL', 'HTTP_REQUEST', 'HTTPS_PROXY_REQUEST'}:
        category, error_type = 'protocol_mismatch', 'SSLError'
    else:
        category, error_type = 'tls_protocol', 'SSLError'
    phases = {'tls_context', 'tls_handshake', 'dns_request', 'response_headers', 'response_body'}
    result = {
        'schema_version': 1, 'observed_at': utc_now(), 'category': category,
        'error_type': error_type, 'phase': phase if phase in phases else 'unknown',
        'endpoint_host': 'cloudflare-dns.com', 'endpoint_port': 443,
        'policy_id': policy_id, 'reused_failure': False,
        'cause_confirmed': False, 'next_action': HINTS[category],
        'tls_handshake_completed': phase in {'dns_request', 'response_headers', 'response_body'},
        'dns_request_attempted': phase in {'dns_request', 'response_headers', 'response_body'},
        'ssl_cert_file_configured': bool(os.environ.get('SSL_CERT_FILE')),
        'ssl_cert_dir_configured': bool(os.environ.get('SSL_CERT_DIR')),
    }
    if reason:
        result['ssl_reason'] = reason
    library = _symbol(getattr(exc, 'library', None))
    if library:
        result['ssl_library'] = library
    code = getattr(exc, 'verify_code', None)
    if isinstance(exc, ssl.SSLCertVerificationError) and type(code) is int and 0 <= code < 65536:
        result['verify_code'] = code
        # Python's raw verify_message can contain a hostname or arbitrary data.
        result['verification_reason'] = VERIFY_REASONS.get(code, 'other_certificate_error')
    if isinstance(ssl.OPENSSL_VERSION, str) and len(ssl.OPENSSL_VERSION) < 150:
        result['openssl_version'] = ssl.OPENSSL_VERSION
    attempts = getattr(connection, 'connection_attempts', None)
    if isinstance(attempts, list):
        safe = []
        for attempt in attempts[:4]:
            if (isinstance(attempt, dict) and attempt.get('ip') in bootstrap
                    and attempt.get('phase') in {'tcp', 'proxy_connect', 'tls'}
                    and attempt.get('outcome') in {'pending', 'connected', 'proxy_failed', 'tls_rejected', 'connect_failed'}):
                safe.append({k: attempt[k] for k in ('ip', 'phase', 'outcome')})
        result['connection_attempts'] = safe
    context = getattr(connection, '_context', None)
    if isinstance(context, ssl.SSLContext):
        result['tls_policy'] = {'check_hostname': context.check_hostname,
                                'verify_mode': context.verify_mode.name,
                                'verify_flags': int(context.verify_flags)}
        result['trust_store_counts'] = context.cert_store_stats()
    return result
