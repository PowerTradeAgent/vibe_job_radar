"""Read three fixed public robots responses once, without changing access policy.

Explicit opt-in. Do not follow redirects, load a job page, authenticate, execute
site scripts, change trust, retry refusals, or import a site's certificates.
The result is diagnostic evidence, not site-availability certification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vibe_job_radar.network import SafeHTTP, FetchError
from vibe_job_radar.network_policy import NetworkPolicy
from vibe_job_radar.tls_context import status as tls_status
from vibe_job_radar.utils import utc_now

HOSTS = ('www.liepin.com', 'we.51job.com', 'www.zhipin.com')


def observe():
    result = {'observed_at': utc_now(), 'scope': 'Three fixed anonymous robots GETs only; no follow, no job request, no access-policy change.',
              'tls_environment': tls_status(), 'observations': []}
    for host in HOSTS:
        row = {'host': host, 'job_requested': False}
        transport = SafeHTTP({host}, timeout=15, max_bytes=512000, network_policy=NetworkPolicy())
        try:
            response = transport.public_get('https://' + host + '/robots.txt')
            mime = response.headers.get('content-type', '').split(';', 1)[0].lower().strip()
            if not re.fullmatch(r'[a-z0-9.+-]{1,40}/[a-z0-9.+-]{1,40}', mime): mime = '[unknown]'
            row.update(http_status=response.status, content_type=mime,
                       bytes=len(response.body), sha256=hashlib.sha256(response.body).hexdigest())
            text = response.body.decode('utf-8-sig', errors='replace')
            row['has_user_agent_line'] = bool(re.search(r'(?im)^\s*user-agent\s*:', text))
            row['looks_like_html'] = bool(re.search(r'<\s*(?:!doctype|html|head|body|script|form)\b', text, re.I))
            # Only record the redirect category; never export its query/path.
            if 300 <= response.status < 400:
                row['redirect_present'] = bool(response.headers.get('location'))
            row['outcome'] = 'response_observed_not_access_authorization'
        except FetchError as exc:
            row.update(outcome=exc.code, error_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
        result['observations'].append(row)
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--live', action='store_true'); args = parser.parse_args()
    if not args.live: raise SystemExit('Pass --live to explicitly request three robots responses; nothing fetched by default.')
    report = observe()
    out = ROOT / 'site-observations'; out.mkdir(exist_ok=True)
    (out / 'robots-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__': main()
