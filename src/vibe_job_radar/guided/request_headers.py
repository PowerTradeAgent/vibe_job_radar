"""Serialize browser request fields once without changing crawler identity.

No network requests or access decisions here. Only validated field names/values
are accepted; ambiguous case-duplicate fields are rejected rather than guessed.
Connection-specific fields cannot control the separate pinned HTTP connection.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from ..network import USER_AGENT
from .contracts import CrawlError

_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_REMOVE = frozenset({
    'host', 'connection', 'content-length', 'accept-encoding',
    'proxy-authorization', 'proxy-authenticate', 'proxy-connection',
    'transfer-encoding', 'keep-alive', 'te', 'trailer', 'upgrade',
})


def browser_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Normalize only field names. Cookie/body values are never reconstructed.

    A browser UA is preserved, with the same explicit crawler product token used
    by our robots parser. This is not UA rotation or a response-triggered retry.
    """
    if headers is None:
        headers = {}
    if not isinstance(headers, Mapping) or len(headers) > 128:
        raise CrawlError('request_headers_invalid')
    normalized: dict[str, str] = {}
    size = 0
    for name, value in headers.items():
        if (not isinstance(name, str) or not _NAME.fullmatch(name)
                or not isinstance(value, str)
                or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in value)):
            raise CrawlError('request_headers_invalid')
        try:
            size += len(name.encode('ascii')) + len(value.encode('latin-1')) + 4
        except UnicodeError:
            raise CrawlError('request_headers_invalid') from None
        if size > 65536:
            raise CrawlError('request_headers_invalid')
        key = name.lower()
        if key in normalized and normalized[key] != value:
            raise CrawlError('request_headers_conflict')
        normalized[key] = value
    nominated = set()
    for raw in normalized.get('connection', '').split(','):
        key = raw.strip().lower()
        if key and not _NAME.fullmatch(key):
            raise CrawlError('request_headers_invalid')
        if key:
            nominated.add(key)
    result = {k: v for k, v in normalized.items() if k not in _REMOVE | nominated}
    agent = result.pop('user-agent', '').strip()
    if USER_AGENT not in agent.split():
        agent = (agent + ' ' + USER_AGENT).strip()
    result['User-Agent'] = agent
    result['Accept-Encoding'] = 'identity'
    return result
