"""Workspace-wide durable limits. No task, UI tab or process can reset a quota."""
from __future__ import annotations

import sqlite3
import math
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .contracts import CrawlError


@dataclass(frozen=True)
class Limits:
    page_interval: float = 15
    pages_hour: int = 60
    pages_day: int = 200
    request_interval: float = 0.5
    requests_hour: int = 600
    requests_day: int = 3000
    login_interval: float = 300
    logins_day: int = 3


class RateLimit(CrawlError):
    def __init__(self, wait: float, code: str = 'rate_wait'):
        self.wait = max(0.0, wait)
        super().__init__(code)


class RateLedger:
    def __init__(self, path: Path, limits: Limits | None = None, clock=time.time):
        self.path, self.limits, self.clock = path, limits or Limits(), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise CrawlError('unsafe_workspace')
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS visits (site TEXT, kind TEXT, ts REAL)')
            conn.execute('CREATE INDEX IF NOT EXISTS visits_scope ON visits(site,kind,ts)')
            conn.execute('CREATE TABLE IF NOT EXISTS cooldown (site TEXT PRIMARY KEY, until REAL)')
            conn.commit()

    def reserve(self, site: str, kind: str) -> None:
        if kind not in {'page', 'request', 'login'}:
            raise ValueError('unknown budget kind')
        now, p = self.clock(), self.limits
        interval, hourly, daily = {
            'page': (p.page_interval, p.pages_hour, p.pages_day),
            'request': (p.request_interval, p.requests_hour, p.requests_day),
            'login': (p.login_interval, p.logins_day, p.logins_day)}[kind]
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            cool = conn.execute('SELECT until FROM cooldown WHERE site=?', (site,)).fetchone()
            if cool and cool[0] > now:
                raise RateLimit(cool[0] - now, 'cooldown')
            rows = [r[0] for r in conn.execute(
                'SELECT ts FROM visits WHERE site=? AND kind=? AND ts>? ORDER BY ts',
                (site, kind, now - 86400))]
            if rows and rows[-1] > now + 1:
                raise RateLimit(rows[-1] - now + interval, 'clock_rollback')
            recent = [t for t in rows if t > now - 3600]
            if len(rows) >= daily:
                raise RateLimit(rows[0] + 86400 - now, 'daily_limit')
            if len(recent) >= hourly:
                raise RateLimit(recent[0] + 3600 - now, 'hourly_limit')
            if rows and now - rows[-1] < interval:
                raise RateLimit(interval - (now - rows[-1]))
            conn.execute('INSERT INTO visits VALUES (?,?,?)', (site, kind, now))
            conn.execute('DELETE FROM visits WHERE ts<?', (now - 172800,))
            conn.commit()

    def cool(self, site: str, seconds: float = 300) -> None:
        if not math.isfinite(seconds):
            seconds = 86400
        until = self.clock() + max(300, seconds)
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute('INSERT INTO cooldown VALUES (?,?) ON CONFLICT(site) DO UPDATE SET until=MAX(until,excluded.until)', (site, until))
            conn.commit()

    def summary(self, site: str) -> dict:
        now = self.clock()
        with closing(sqlite3.connect(self.path)) as conn:
            return {k: {'hour': conn.execute('SELECT COUNT(*) FROM visits WHERE site=? AND kind=? AND ts>?', (site, k, now-3600)).fetchone()[0],
                        'day': conn.execute('SELECT COUNT(*) FROM visits WHERE site=? AND kind=? AND ts>?', (site, k, now-86400)).fetchone()[0]}
                    for k in ('page', 'request', 'login')}
