"""Trusted-code native access contracts. Web forms cannot add hosts or operations.

The bootstrap Liepin contract deliberately has no guessed business API or login
POST. Unreviewed operations are visible failures, not empty search results.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import quote, unquote, urlsplit

from .contracts import CrawlError
from ..network import USER_AGENT


@dataclass(frozen=True)
class NativeRule:
    key: str
    host: str
    path: str
    methods: tuple[str, ...] = ('GET',)
    resources: tuple[str, ...] = ('Fetch', 'XHR')
    role: str = 'business'
    authentication: bool = False

    def __post_init__(self):
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}', self.key):
            raise ValueError('invalid native operation identifier')
        if self.role not in {'document', 'business', 'asset', 'login'}:
            raise ValueError('invalid native operation role')
        if not self.methods or any(m not in {'GET', 'HEAD', 'POST', 'OPTIONS'} for m in self.methods):
            raise ValueError('invalid native operation method')
        if not re.fullmatch(r'[a-z0-9.-]{1,253}', self.host) or len(self.path) > 512:
            raise ValueError('invalid native operation target')
        re.compile(self.path)


@dataclass(frozen=True)
class NativeContract:
    key: str
    hosts: tuple[str, ...]
    rules: tuple[NativeRule, ...]
    bootstrap_only: bool = True

    def __post_init__(self):
        if (not self.hosts or len(self.hosts) > 16 or len(self.rules) > 64
                or len(set(self.hosts)) != len(self.hosts)):
            raise ValueError('invalid native contract size')
        for host in self.hosts:
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host):
                raise ValueError('invalid exact hostname')
        if any(r.host not in self.hosts for r in self.rules):
            raise ValueError('rule host not declared')

    def target(self, url):
        if (not isinstance(url, str) or len(url) > 8192 or '\\' in url
                or any(ord(c) < 32 or ord(c) == 127 for c in url)):
            raise CrawlError('invalid_url')
        try:
            p = urlsplit(url)
            if (p.scheme != 'https' or p.hostname not in self.hosts
                    or p.port not in (None, 443) or p.username or p.password):
                raise CrawlError('resource_domain_blocked')
            # No path-smuggling interpretation differences in code-owned rules.
            path = unquote(p.path, errors='strict')
            if ('\\' in path or '%' in path or any(ord(c) < 32 for c in path)
                    or any(s in {'.', '..'} for s in path.split('/'))):
                raise CrawlError('invalid_url')
            return p, path
        except (ValueError, UnicodeError) as exc:
            raise CrawlError('invalid_url') from exc

    def match(self, url, method, resource, *, authentication=False):
        p, path = self.target(url)
        for rule in self.rules:
            if (rule.host == p.hostname and method in rule.methods and resource in rule.resources
                    and (not rule.authentication or authentication) and re.fullmatch(rule.path, path)):
                return rule
        raise CrawlError('native_operation_unreviewed')

    @property
    def rule_origins(self):
        return tuple(dict.fromkeys('https://' + r.host for r in self.rules if r.role != 'asset'))


def liepin_bootstrap():
    # These are existing application entrypoints, NOT a live-certified data API.
    # Dependencies are exact hosts already named by that entrypoint; unknown CDN
    # hosts stay blocked until their actual purpose is reviewed in the site PR.
    host = 'www.liepin.com'
    return NativeContract('liepin_bootstrap_v1', (host,), (
        NativeRule('liepin_navigation', host, r'(?:/|/zhaopin/|/job/[^/]+\.(?:shtml|html)|/a/[0-9]+\.shtml|/lptjob/[0-9]+)',
                   resources=('Document',), role='document'),
        NativeRule('liepin_same_host_assets', host, r'.+\.(?:js|css|png|jpg|jpeg|gif|webp|svg|ico|woff2?|ttf)',
                   resources=('Script','Stylesheet','Image','Font'), role='asset'),
    ))


def contract_for(adapter):
    contract = getattr(adapter, 'native_contract', None)
    if contract is not None:
        if not isinstance(contract, NativeContract):
            raise CrawlError('native_contract_invalid')
        return contract
    if adapter.key == 'liepin':
        return liepin_bootstrap()
    raise CrawlError('native_contract_unavailable')


def capability(adapter):
    try:
        contract = contract_for(adapter)
        return {'available': True, 'contract': contract.key, 'bootstrap_only': contract.bootstrap_only,
                'certification': 'not_live_verified'}
    except CrawlError:
        return {'available': False, 'certification': 'not_live_verified'}


def _octets(value):
    """RFC9309 comparison: UTF-8 octets, unreserved %-escapes decoded only."""
    def escape(m):
        c = chr(int(m[0][1:], 16))
        return c if c.isascii() and (c.isalnum() or c in '-._~') else m[0].upper()
    return re.sub(r'%[0-9A-Fa-f]{2}', escape, quote(value, safe="/%*?$&=:+,;@!'-._~()"))


def _glob_matches(pattern, target, anchored):
    """Literal chunks only: publisher wildcards cannot cause regex backtracking."""
    chunks = pattern.split('*')
    if not target.startswith(chunks[0]):
        return False
    offset = len(chunks[0])
    if len(chunks) == 1:
        return not anchored or offset == len(target)
    for chunk in chunks[1:-1]:
        found = target.find(chunk, offset)
        if found < 0:
            return False
        offset = found + len(chunk)
    last = chunks[-1]
    if anchored:
        return target.endswith(last) and len(target) - len(last) >= offset
    return target.find(last, offset) >= 0


class NativeRobots:
    """Bounded explicit rules, with conservative unavailable/invalid handling.

    Non-200/HTML/invalid bodies are not converted to permission. Crawl-delay and
    Request-rate are supported publisher extensions; never reduce ledger policy.
    """
    def __init__(self, status, content_type, body):
        if (status != 200 or content_type.split(';')[0].strip().lower() != 'text/plain'
                or len(body) > 512 * 1024):
            raise CrawlError('robots_unavailable')
        try:
            text = body.decode('utf-8-sig')
        except UnicodeError as exc:
            raise CrawlError('robots_unavailable') from exc
        if re.search(r'<\s*(?:!doctype|html|script|body)\b', text, re.I):
            raise CrawlError('robots_unavailable')
        groups, agents, entries = [], [], []
        for raw in text.splitlines():
            line = raw.split('#', 1)[0].strip()
            if not line or ':' not in line:
                continue
            k, value = (x.strip() for x in line.split(':', 1)); k = k.lower()
            if k == 'user-agent':
                if entries:
                    groups.append((agents, entries)); agents, entries = [], []
                agents.append(value.lower())
            elif agents and k in {'allow','disallow','crawl-delay','request-rate'}:
                if len(value) > 2048:
                    raise CrawlError('robots_unavailable')
                entries.append((k, value))
        if agents:
            groups.append((agents, entries))
        if not groups:
            raise CrawlError('robots_unavailable')
        token = USER_AGENT.split('/')[0].lower()
        selected = [v for a,v in groups if token in a]
        if not selected:
            selected = [v for a,v in groups if '*' in a]
        self.rules, self.delay, self.windows = [], 0.0, []
        for group in selected:
            for key, value in group:
                if key in {'allow','disallow'} and value:
                    if not value.startswith('/'):
                        raise CrawlError('robots_unavailable')
                    value = _octets(value)
                    end = value.endswith('$')
                    pattern = value[:-1] if end else value
                    # Literal chunk matching bounds work even for hostile wildcards.
                    if pattern.count('*') > 32:
                        raise CrawlError('robots_unavailable')
                    pattern = re.sub(r'\*+', '*', pattern)
                    self.rules.append((len(value.replace('*','').rstrip('$')), key == 'allow', pattern, end))
                elif key == 'crawl-delay':
                    if not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,3})?', value):
                        raise CrawlError('robots_unavailable')
                    self.delay = max(self.delay, float(value))
                elif key == 'request-rate':
                    m = re.fullmatch(r'([1-9][0-9]{0,6})\s*/\s*([1-9][0-9]{0,7})', value)
                    if not m:
                        raise CrawlError('robots_unavailable')
                    self.windows.append(tuple(map(int, m.groups())))
        if len(self.rules) > 2000:
            raise CrawlError('robots_unavailable')

    def allowed(self, url):
        p = urlsplit(url)
        target = _octets(p.path or '/') + (('?' + _octets(p.query)) if p.query else '')
        matches = [(n, allow) for n,allow,pattern,end in self.rules if _glob_matches(pattern,target,end)]
        return max(matches, default=(0, True))[1]
