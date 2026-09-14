"""Real Chromium pagination regression at the actual 15-second production default.

The application backend and RateLedger are real; only supplier HTTP is an
in-memory fixture. No external sites, accounts or credentials are used.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vibe_job_radar.guided.adapters import DOMAdapter
from vibe_job_radar.guided.browser import PlaywrightBackend
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.rate import Limits, RateLedger
from vibe_job_radar.guided.transport import PinnedTransport, WireResponse


class ReviewWire(PinnedTransport):
    """Fixed fixture pages; reuses production reserve()/cancel/throttle behavior."""
    def ensure_robots(self, url):
        pass  # robots-denial and public-IP tests are independent unit tests.

    def fetch(self, url, method='GET', headers=None, body=None, *, required=True):
        self.reserve('request')
        path = urlsplit(url).path
        if path == '/optional.png':
            raise CrawlError('http_403')
        if path == '/page/one':
            text = '<h1>Page one</h1><img src="/optional.png"><a rel="next" href="/page/two">下一页</a>'
        elif path == '/page/two':
            text = '<h1>Page two</h1><img src="/optional.png">'
        else:
            text = '''<h1 id="title">SPA one</h1><button id="next" onclick="document.getElementById('title').textContent='SPA two'">下一页</button>'''
        return WireResponse(200, {'content-type': 'text/html; charset=utf-8'}, text.encode())


def main():
    output = ROOT/'browser-acceptance'/'guided-review'
    output.mkdir(parents=True, exist_ok=True)
    results = {'success': False, 'checks': [], 'scope': 'Real Chromium/backend/default navigation interval; artificial HTTP only.'}
    adapter = DOMAdapter('fixture', 'Test fixture', ('jobs.fixture.test',),
        'https://jobs.fixture.test/page/one', 'q', r'^/job/\d+$',
        'https://jobs.fixture.test/login', ('jobs.fixture.test',), next_selectors=('a[rel="next"]', '#next'))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RateLedger(Path(tmp)/'default.sqlite')
            browser = PlaywrightBackend(adapter, ledger, threading.Event(), headless=True,
                executable_path=os.environ.get('RADAR_TEST_CHROMIUM'), transport_factory=ReviewWire)
            try:
                started = time.monotonic()
                browser.open('https://jobs.fixture.test/page/one')
                assert browser.error is None
                assert browser.next_page()
                assert 'Page two' in browser.snapshot().html
                assert ledger.summary('fixture')['page']['day'] == 2
                elapsed = time.monotonic()-started
                assert elapsed >= 15
                results['production_page_interval'] = ledger.limits.page_interval
                results['elapsed_seconds'] = round(elapsed, 2)
                results['checks'].append('normal document next-page at real 15s limit completes and consumes exactly one additional navigation')
                results['checks'].append('optional image 403 does not poison an otherwise usable document')
            finally:
                browser.close()
            ledger = RateLedger(Path(tmp)/'spa.sqlite', Limits(page_interval=.1, request_interval=0))
            browser = PlaywrightBackend(adapter, ledger, threading.Event(), headless=True,
                executable_path=os.environ.get('RADAR_TEST_CHROMIUM'), transport_factory=ReviewWire)
            try:
                browser.open('https://jobs.fixture.test/spa')
                assert browser.next_page()
                assert 'SPA two' in browser.page.locator('h1').inner_text()
                assert ledger.summary('fixture')['page']['day'] == 2
                results['checks'].append('SPA pagination also counts exactly once without document navigation')
            finally:
                browser.close()
        results['success'] = True
    finally:
        (output/'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
