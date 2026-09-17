"""Observe fixed robots and permitted search HTML without changing access policy.

Explicit opt-in. No redirects, login, script execution, resource/detail requests,
trust changes or retries. Parsed HTML contributes only bounded resource origins,
not content, URL paths, query parameters or permissions for those resources.
"""
from __future__ import annotations
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vibe_job_radar.network import SafeHTTP, FetchError, USER_AGENT
from vibe_job_radar.guided.adapters import builtins
from vibe_job_radar.network_policy import NetworkPolicy
from vibe_job_radar.tls_context import status as tls_status
from vibe_job_radar.utils import utc_now

HOSTS = ('www.liepin.com', 'we.51job.com', 'www.zhipin.com')
PLATFORMS = ('liepin', '51job', 'boss')


class ResourceOrigins(HTMLParser):
    """Observe declared references only; never request them or expand allowlists."""
    def __init__(self, base):
        super().__init__(); self.base = base; self.values = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get('src') if tag in {'script', 'iframe', 'img'} else attrs.get('href') if tag == 'link' else None
        if not value or len(self.values) >= 40:
            return
        try:
            parsed = urlsplit(urljoin(self.base, value))
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.port not in (None, 443)
                    or not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', parsed.hostname)):
                return
        except ValueError:
            return
        kind = 'stylesheet' if tag == 'link' and 'stylesheet' in (attrs.get('rel') or '').split() else tag
        self.values.add((kind, parsed.hostname.lower()))


def observe():
    result = {'observed_at': utc_now(),
              'scope': 'Fixed anonymous robots and at most one permitted search HTML per source; no redirects, resources, details, login, script execution or access-policy change.',
              'tls_environment': tls_status(), 'observations': []}
    for host, platform in zip(HOSTS, PLATFORMS):
        row = {'host': host, 'job_requested': False, 'list_requested': False}
        transport = SafeHTTP({host}, timeout=15, max_bytes=512000, network_policy=NetworkPolicy())
        phase = 'robots'
        try:
            response = transport.public_get('https://' + host + '/robots.txt')
            mime = response.headers.get('content-type', '').split(';', 1)[0].lower().strip()
            if not re.fullmatch(r'[a-z0-9.+-]{1,40}/[a-z0-9.+-]{1,40}', mime):
                mime = '[unknown]'
            row.update(http_status=response.status, content_type=mime,
                       bytes=len(response.body), sha256=hashlib.sha256(response.body).hexdigest())
            try:
                text = response.body.decode('utf-8-sig'); row['valid_utf8'] = True
            except UnicodeError:
                text = ''; row['valid_utf8'] = False
            row['has_user_agent_line'] = bool(re.search(r'(?im)^\s*user-agent\s*:', text))
            row['looks_like_html'] = bool(re.search(r'<\s*(?:!doctype|html|head|body|script|form)\b', text, re.I))
            if 300 <= response.status < 400:
                row['redirect_present'] = bool(response.headers.get('location'))
            row['outcome'] = 'response_observed_not_access_authorization'
            if response.status == 200 and mime == 'text/plain' and row['has_user_agent_line'] and not row['looks_like_html']:
                target = builtins().get(platform).search_url('时间序列算法工程师')
                rules = RobotFileParser(); rules.parse(text.splitlines())
                complex_rules = any(re.search(r'(?i)^\s*(?:allow|disallow)\s*:.*[\*$]', line.split('#', 1)[0]) for line in text.splitlines())
                row['existing_parser_allows_search'] = rules.can_fetch(USER_AGENT, target)
                row['extended_rules_require_separate_review'] = complex_rules
                if row['existing_parser_allows_search'] and not complex_rules:
                    # Only the existing simple parser's positive decision is used.
                    # Inconclusive/complex rules are NOT made permissive here.
                    delay = rules.crawl_delay(USER_AGENT) or 0
                    rate = rules.request_rate(USER_AGENT)
                    wait = max(2, delay, rate.seconds if rate else 0)
                    if wait > 30:
                        row['list_observation'] = 'deferred_publisher_wait'
                    else:
                        time.sleep(wait)
                        row['list_requested'] = True
                        phase = 'search_html'
                        page = transport.public_get(target)
                        row.update(list_http_status=page.status, list_bytes=len(page.body),
                                   list_sha256=hashlib.sha256(page.body).hexdigest())
                        if page.status == 200 and 'html' in page.headers.get('content-type', '').lower():
                            resources = ResourceOrigins(target)
                            resources.feed(page.body.decode('utf-8', errors='replace'))
                            row['resource_origins'] = [{'kind':k, 'host':h} for k,h in sorted(resources.values)]
        except FetchError as exc:
            row.update(outcome=exc.code, phase=phase,
                       error_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
        result['observations'].append(row)
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--live', action='store_true'); args = parser.parse_args()
    if not args.live:
        raise SystemExit('Pass --live to request fixed robots and at most one permitted search HTML; nothing fetched by default.')
    report = observe()
    out = ROOT / 'site-observations'; out.mkdir(exist_ok=True)
    (out / 'robots-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__': main()
