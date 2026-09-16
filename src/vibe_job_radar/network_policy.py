"""Immutable route selection shared by HTTP, CLI and the browser request bridge.

This phase consumes static loopback HTTP proxy settings. It intentionally does
not claim PAC, proxy credentials, SOCKS or Fake-IP/remote-DNS certification.
No settings are written to the OS, no endpoints scanned, no TLS checks removed.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .loopback_proxy import LocalProxyError, LoopbackProxy

_ACTIVE: ContextVar = ContextVar('radar_network_policy', default=None)


def _bypasses(host: str, rules: tuple[str, ...]) -> bool:
    """NO_PROXY matching without resolving targets or contacting a PAC server."""
    host = host.lower().rstrip('.')
    for rule in rules:
        rule = rule.strip().lower()
        if rule == '*':
            return True
        if not rule:
            continue
        try:
            if ipaddress.ip_address(host) in ipaddress.ip_network(rule, strict=False):
                return True
        except ValueError:
            pass
        if rule.endswith(':443'):
            rule = rule[:-4]
        if rule.startswith('[') and rule.endswith(']'):
            rule = rule[1:-1]
        rule = rule.removeprefix('*.').lstrip('.').rstrip('.')
        if host == rule or host.endswith('.' + rule):
            return True
    return False


@dataclass(frozen=True)
class NetworkPolicy:
    source: str = 'system_route'
    proxy: LoopbackProxy | None = field(default=None, repr=False)
    bypass: tuple[str, ...] = field(default=(), repr=False)
    error: str | None = None

    @classmethod
    def capture(cls, *, discover=None) -> NetworkPolicy:
        explicit = os.environ.get('VIBE_RADAR_HTTP_PROXY', '')
        if explicit.strip():
            # An explicit application route is authoritative; ambient NO_PROXY
            # must not silently override it. The legacy parser remains strict.
            try:
                return cls('explicit_application', LoopbackProxy.from_url(explicit))
            except LocalProxyError:
                return cls('explicit_application', error='local_proxy_configuration_invalid')
        rules = ()
        try:
            values = (discover or urllib.request.getproxies)()
            if not isinstance(values, dict):
                raise ValueError
            raw = values.get('https') or values.get('all')
            exclusions = values.get('no', '')
            if not isinstance(exclusions, str) or len(exclusions) > 32768:
                raise ValueError
            rules = tuple(exclusions.split(','))
            if not raw:
                return cls(bypass=rules)
            if not isinstance(raw, str):
                raise ValueError
            if '://' not in raw:
                raw = 'http://' + raw
            proxy = LoopbackProxy.from_url(raw)
            return cls('automatic_static', proxy, rules)
        except Exception:
            # Keep only a fixed code, never a proxy URL containing credentials.
            return cls('automatic_static', bypass=rules, error='local_proxy_configuration_invalid')

    def for_host(self, host: str) -> LoopbackProxy | None:
        if self.source != 'explicit_application' and _bypasses(host, self.bypass):
            return None
        if self.error:
            raise LocalProxyError(self.error)
        return self.proxy

    @property
    def fingerprint(self) -> str:
        value = [self.source, self.proxy.host if self.proxy else None,
                 self.proxy.port if self.proxy else None, self.bypass, self.error]
        return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()[:16]

    def describe(self, host: str | None = None) -> dict:
        result = {'mode': 'auto', 'source': self.source, 'policy_id': self.fingerprint,
                  'resolution': 'local_validated_public_ip', 'direct_fallback': False,
                  'automatic_static_http': True, 'pac_supported': False,
                  'socks_supported': False, 'fake_ip_supported': False,
                  'proxy_credentials_supported': False,
                  'network_tested': False}
        try:
            selected = self.for_host(host or '')
            result.update(transport='loopback_http_proxy' if selected else 'system_route', code='policy_selected')
        except LocalProxyError:
            result.update(transport='unavailable', code=self.error)
        return result


def current_policy() -> NetworkPolicy:
    """Capture once per client/session, unless its owner supplied a snapshot."""
    return _ACTIVE.get() or NetworkPolicy.capture()


@contextmanager
def use_policy(policy: NetworkPolicy):
    if not isinstance(policy, NetworkPolicy):
        raise TypeError('expected a network policy snapshot')
    token = _ACTIVE.set(policy)
    try:
        yield policy
    finally:
        _ACTIVE.reset(token)
