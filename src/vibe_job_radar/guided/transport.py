"""Bridge browser HTTP through public-IP-pinned TLS, never arbitrary direct egress.

The browser receives fulfilled responses only: no route.continue_(), no automatic
HTTP redirects, no proxy fallback and no weakening DNS checks for Fake-IP.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from ..network import FetchError, PinnedHTTPSConnection, USER_AGENT, validate_public_url
from ..utils import domain_matches
from .contracts import CrawlError
from .rate import RateLedger, RateLimit


@dataclass(frozen=True)
class WireResponse:
    status: int
    headers: dict[str, str]
    body: bytes = field(repr=False)
    cookies: tuple[str, ...] = field(default=(), repr=False)


def diagnose_host(host: str) -> dict:
    """DNS only; fixed adapter host chosen server-side, no caller-supplied URLs."""
    try:
        values = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        addresses = [{'ip': v, 'public': ipaddress.ip_address(v).is_global,
                      'fake_ip_range': ipaddress.ip_address(v).version == 4
                          and ipaddress.ip_address(v) in ipaddress.ip_network('198.18.0.0/15')} for v in values]
        passed = bool(addresses) and all(a['public'] for a in addresses)
        return {'host': host, 'addresses': addresses, 'passed': passed,
                'code': 'dns_ok' if passed else 'non_public_address',
                'network_scope': 'DNS only; no job requests; not login or collection certification'}
    except (OSError, ValueError):
        return {'host': host, 'addresses': [], 'passed': False, 'code': 'dns_error'}


class PinnedTransport:
    """One instance per browser session; ledger limits are shared across sessions."""
    def __init__(self, adapter, ledger: RateLedger, cancelled: threading.Event,
                 progress=lambda *_: None):
        self.adapter, self.ledger, self.cancelled, self.progress = adapter, ledger, cancelled, progress
        self.domains = set((*adapter.domains, *adapter.resource_domains))
        self.robots = {}
        self.blocked = set()

    def reserve(self, kind: str) -> None:
        while not self.cancelled.is_set():
            try:
                self.ledger.reserve(self.adapter.key, kind)
                return
            except RateLimit as exc:
                if exc.code != 'rate_wait' or exc.wait > 60:
                    raise CrawlError(exc.code) from exc
                self.progress('rate_wait', round(exc.wait, 1))
                if self.cancelled.wait(min(exc.wait, 1)):
                    break
        raise CrawlError('paused')

    def fetch(self, url: str, method='GET', headers=None, body=None) -> WireResponse:
        try:
            host, ip, target = validate_public_url(url, self.domains)
        except FetchError as exc:
            raise CrawlError(exc.code) from exc
        if host in self.blocked:
            raise CrawlError('site_stopped')
        self.reserve('request')
        if body and len(body) > 1_000_000:
            raise CrawlError('request_too_large')
        conn = PinnedHTTPSConnection(host, ip, 20)
        # Cookies are supplied by the browser cookie jar. Never persisted or logged.
        hdr = {k: v for k, v in (headers or {}).items()
               if k.lower() not in {'host', 'connection', 'content-length', 'accept-encoding',
                                    'proxy-authorization', 'proxy-connection', 'transfer-encoding'}}
        hdr['Accept-Encoding'] = 'identity'
        hdr.setdefault('User-Agent', USER_AGENT)
        try:
            conn.request(method, target, body=body, headers=hdr)
            response = conn.getresponse()
            pairs = response.getheaders()
            metadata = {k.lower(): v for k, v in pairs if k.lower() != 'set-cookie'}
            if response.status in {401, 403, 429}:
                self.blocked.add(host)
                self.ledger.cool(self.adapter.key, self._retry_seconds(metadata.get('retry-after', '')))
                raise CrawlError(f'http_{response.status}')
            content = response.read(5_000_001)
            if len(content) > 5_000_000:
                raise CrawlError('response_too_large')
            if metadata.get('content-encoding', 'identity').lower() != 'identity':
                raise CrawlError('unexpected_compression')
            return WireResponse(response.status, metadata, content,
                                tuple(v for k, v in pairs if k.lower() == 'set-cookie'))
        except (OSError, http.client.HTTPException) as exc:
            raise CrawlError('network_error') from exc
        finally:
            conn.close()

    @staticmethod
    def _retry_seconds(value: str) -> float:
        try:
            return max(300, float(value))
        except ValueError:
            try:
                return max(300, parsedate_to_datetime(value).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                return 300

    def ensure_robots(self, url: str) -> None:
        p = urlsplit(url)
        origin = f'https://{p.netloc}'
        if origin not in self.robots:
            result = self.fetch(origin + '/robots.txt')
            if result.status != 200 or 'html' in result.headers.get('content-type', '').lower():
                raise CrawlError('robots_unavailable')
            parser = RobotFileParser()
            try:
                parser.parse(result.body.decode('utf-8-sig').splitlines())
            except UnicodeError as exc:
                raise CrawlError('robots_unavailable') from exc
            self.robots[origin] = parser
        parser = self.robots[origin]
        if not parser.can_fetch(USER_AGENT, url):
            raise CrawlError('robots_denied')
        # Our hard default is already conservative; stricter publisher delays win.
        delay = parser.crawl_delay(USER_AGENT) or 0
        rate = parser.request_rate(USER_AGENT)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > self.ledger.limits.page_interval:
            # Fail closed rather than silently ignoring a stricter policy.
            raise CrawlError('publisher_delay_exceeds_policy')

    def allowed_resource(self, url: str) -> bool:
        p = urlsplit(url)
        return p.scheme == 'https' and bool(p.hostname) and any(domain_matches(p.hostname, d) for d in self.domains)
