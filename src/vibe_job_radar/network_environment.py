"""Read-only, redacted visibility into proxy configuration.

This module does NOT install a proxy transport, change DNS, test connectivity,
forward requests, or relax the collector's public-address restrictions. Its
purpose is to expose the outstanding compatibility gap instead of misleading
users into thinking detected OS settings are already applied by the collector.
"""
from __future__ import annotations

import os
import platform
import sys
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass_environment


def _describe_proxy(value: object) -> dict:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return {'valid_endpoint_shape': False, 'message': '无法识别代理配置；原值不回显，以免泄漏凭据。'}
    try:
        parsed = urlsplit(value if '://' in value else 'http://' + value)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme.lower()
        if not host or scheme not in {'http', 'https', 'socks', 'socks4', 'socks5', 'socks5h'}:
            raise ValueError('unsupported shape')
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('not an endpoint')
        safe_host = '[' + host + ']' if ':' in host else host
        return {'valid_endpoint_shape': True,
                'endpoint': f'{scheme}://{safe_host}' + (f':{port}' if port else ''),
                'credentials_present': parsed.username is not None or parsed.password is not None,
                'application_support': 'not_implemented',
                'connectivity_tested': False}
    except (ValueError, UnicodeError):
        return {'valid_endpoint_shape': False, 'message': '配置不是可识别的静态代理入口；未记录原值。'}


def inspect_environment(host: str = 'www.zhipin.com', *, discover=None) -> dict:
    """Pure configuration read. The caller selects a known host; no DNS/TCP here.

    `discover` is dependency injection for tests, not exposed to HTTP callers.
    Never return arbitrary environment variables or unredacted proxy URLs.
    """
    discovery = discover or getproxies
    try:
        proxies = discovery()
        if not isinstance(proxies, dict):
            raise TypeError('proxy discovery did not return a mapping')
        endpoints = {name: _describe_proxy(proxies[name]) for name in ('https', 'all', 'http') if proxies.get(name)}
        bypassed = proxy_bypass_environment(host, proxies) if host else None
        error = ''
    except Exception as exc:
        endpoints, bypassed = {}, None
        error = type(exc).__name__
    relevant = 'https' if 'https' in endpoints else ('all' if 'all' in endpoints else None)
    found = relevant is not None
    if error:
        message = '系统代理配置读取失败；不是已证明无代理。诊断只记录异常类型。'
    elif found:
        message = ('检测到适用于HTTPS的代理配置，但当前项目的自建采集连接尚未使用该配置。'
                   'TUN/VPN仍可能在系统层接管流量，不能据此判断已绕过或已启用代理。')
    else:
        message = ('没有发现适用于HTTPS的静态代理配置。此结果不能证明未使用TUN/VPN；'
                   'TUN/VPN系统路由和PAC动态代理不由此检查认证。')
    from .loopback_proxy import LoopbackProxy, LocalProxyError
    from .loopback_socks import LoopbackSocks5, select_loopback_proxy
    try:
        selected = select_loopback_proxy()
        explicit = {'enabled': selected is not None, 'valid': True,
                    'host': selected.host if selected else None,
                    'port': selected.port if selected else None,
                    'target_dns': 'local_public_only', 'connectivity_tested': False,
                    'protocol': 'socks5' if isinstance(selected, LoopbackSocks5) else 'http' if selected else None}
    except LocalProxyError as exc:
        explicit = {'enabled': False, 'valid': False, 'error': exc.code,
                    'connectivity_tested': False}
    if explicit['enabled']:
        message += ' 已明确配置本程序专用的本机代理；最终目标仍须通过公网DNS检查，未在本次诊断中测试连接。'
    elif not explicit['valid']:
        message += ' 本程序专用代理配置无效，实际请求会报错而非静默直连。'
    return {'schema_version': 1, 'python': sys.executable, 'os': platform.system(),
            'host_for_config_check': host, 'proxy_configuration_detected': found,
            'configuration_read_error_type': error, 'proxy_candidates': endpoints,
            'https_candidate_key': relevant, 'standard_no_proxy_match': bypassed,
            'collector_applies_static_proxy': False,
            'explicit_loopback_proxy': explicit,
            'tun_or_vpn_detected': None, 'virtual_machine_detected': None,
            'vm_note': '虚拟机的127.0.0.1是该虚拟机本身；诊断不猜宿主机地址、不扫描网关。',
            'message': message,
            'scope': 'Read-only configuration, no DNS or connections; NOT a proxy compatibility implementation.'}
