"""Opt-in continuation when a normal login returns to the original job list.

Observe only the application's current DOM, never cookies/passwords or new
network requests. A readable matching list proves availability of that list,
not that a particular account has authenticated. No login is submitted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
from urllib.parse import parse_qsl, urlsplit

from .contracts import CrawlError


def matching_list_signature(adapter, expected_url, page):
    """Reject unrelated queries, detail/recommendation pages and empty lists."""
    expected = urlsplit(adapter.accept_url(expected_url))
    actual = urlsplit(adapter.accept_url(page.url))
    if (expected.scheme, expected.netloc, expected.path.rstrip('/')) != (
            actual.scheme, actual.netloc, actual.path.rstrip('/')):
        return None
    # accept_url removes only the adapter's known tracking parameters. All
    # remaining filters, including repeated keys, must match. Never log values.
    wanted = parse_qsl(expected.query, keep_blank_values=True)
    present = parse_qsl(actual.query, keep_blank_values=True)
    if sorted(wanted) != sorted(present):
        return None
    cards = adapter.cards(page)
    if not cards:
        return None
    return hashlib.sha256('\n'.join(card.id for card in cards).encode()).hexdigest()


@dataclass
class _Watch:
    backend: object
    expected_url: str
    expires: float
    next_check: float
    signature: str | None = None


class LoginReturnManager:
    """Owner-worker-only watcher; bounded, cancellable and never restarted on boot."""
    def __init__(self, *, clock=time.monotonic, interval=1.0, timeout=600.0):
        self.clock, self.interval, self.timeout = clock, interval, timeout
        self._watches: dict[str, _Watch] = {}

    def arm(self, state, backend):
        self._watches.clear()  # The service owns one active browser session.
        if state.get('auto_continue_after_login') is not True:
            return False
        now = self.clock()
        self._watches[state['id']] = _Watch(backend, state['search_url'], now+self.timeout, now)
        return True

    def disarm(self, ident):
        return self._watches.pop(ident, None) is not None

    def _attention(self, service, ident, watch):
        # DOM access can yield to pause or a new action; never overwrite its
        # newer state when reporting an observation error.
        with service._lock:
            if self._watches.get(ident) is not watch:
                return
            self.disarm(ident)
            if service._busy or service._cancel.is_set() or service._shutdown.is_set():
                return
            state = service._load(ident)
            if state.get('status') == 'waiting_manual':
                service._save(state, login_continuation='needs_attention')

    def tick(self, service):
        """Called on the same worker as Playwright, after pumping page events."""
        for ident, watch in tuple(self._watches.items()):
            with service._lock:
                if service._busy or service._shutdown.is_set():
                    return
                now = self.clock()
                if now < watch.next_check:
                    continue
                watch.next_check = now + self.interval
                try:
                    state = service._load(ident)
                except Exception:
                    self.disarm(ident)
                    continue
                if (service._cancel.is_set() or service._backends.get(ident) is not watch.backend
                        or state.get('authentication') != 'manual_pending'
                        or state.get('status') != 'waiting_manual'):
                    self.disarm(ident)
                    service._save(state, login_continuation='cancelled')
                    continue
                if now >= watch.expires:
                    self.disarm(ident)
                    service._save(state, login_continuation='timed_out')
                    continue
            try:
                # No service lock during DOM access: pause/stop can win while
                # Playwright yields. Recheck ownership before submitting below.
                page = watch.backend.snapshot()
                signature = matching_list_signature(
                    service.registry.get(state['platform']), watch.expected_url, page)
            except CrawlError as exc:
                if exc.code in {'manual_required', 'not_job_list', 'wrong_platform', 'invalid_url'}:
                    watch.signature = None
                    continue
                self._attention(service, ident, watch)
                continue
            except Exception:
                self._attention(service, ident, watch)
                continue
            with service._lock:
                if (self._watches.get(ident) is not watch or service._busy
                        or service._cancel.is_set() or service._shutdown.is_set()
                        or service._backends.get(ident) is not watch.backend):
                    self.disarm(ident)
                    continue
                if signature is None or signature != watch.signature:
                    watch.signature = signature
                    continue
                # Two stable local observations; remove before enqueueing so
                # reentrant events cannot submit the action twice.
                self.disarm(ident)
                action = 'resume' if state.get('phase') == 'collect' else 'capture'
                service._save(state, status='queued', login_continuation='resumed')
                service._submit(action, ident)
                return
