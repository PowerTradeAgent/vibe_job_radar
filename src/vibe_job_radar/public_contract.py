"""Credential-free query/result contract for an optional reviewed public service.

This is a data protocol, not an arbitrary URL fetcher. Source registration is an
operator decision; a server response cannot add sources or widen a user's scope.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qsl

from .models import JobRecord
from .utils import canonical_url, parse_time
from .url_safety import credential_query_key

ID = re.compile(r'[a-zA-Z0-9_-]{1,100}')


class ContractError(ValueError):
    def __init__(self, code='public_contract_invalid'):
        self.code = code
        super().__init__(code)  # Never echo free text, credentials or remote URLs.


def text(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or ('\x00' in value)
            or (not empty and not value.strip())):
        raise ContractError()
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ContractError() from exc
    return value


@dataclass(frozen=True)
class PublicQuery:
    query: str
    source_scope: tuple[str, ...]
    region: str = ''
    remote: bool | None = None
    limit: int = 20
    cursor: str = ''

    def __post_init__(self):
        text(self.query, 200); text(self.region, 100, empty=True)
        if (not isinstance(self.source_scope, tuple) or not 1 <= len(self.source_scope) <= 10
                or any(not isinstance(s, str) or not ID.fullmatch(s) for s in self.source_scope)
                or len(set(self.source_scope)) != len(self.source_scope)
                or type(self.limit) is not int or not 1 <= self.limit <= 50
                or (self.remote is not None and type(self.remote) is not bool)):
            raise ContractError()
        text(self.cursor, 1024, empty=True)
        if self.cursor and not re.fullmatch(r'[0-9a-zA-Z_.-]+', self.cursor):
            raise ContractError('public_cursor_invalid')

    @classmethod
    def from_dict(cls, value):
        fields = {'query', 'source_scope', 'region', 'remote', 'limit', 'cursor'}
        if (not isinstance(value, dict) or set(value)-fields or not {'query','source_scope'} <= set(value)
                or not isinstance(value['source_scope'], list)):
            raise ContractError()
        return cls(**{**value, 'source_scope': tuple(value['source_scope'])})

    def payload(self):
        return {'query': self.query, 'source_scope': list(self.source_scope),
                'region': self.region, 'remote': self.remote, 'limit': self.limit, 'cursor': self.cursor}

    def digest(self, *, include_cursor=True):
        value = self.payload()
        if not include_cursor:
            value['cursor'] = ''
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PublicSource:
    key: str
    label: str
    domains: tuple[str, ...]
    contract: str
    distribution_approved: bool = False

    def __post_init__(self):
        if not isinstance(self.key, str) or not ID.fullmatch(self.key):
            raise ContractError('public_source_invalid')
        text(self.label, 200); text(self.contract, 2000)
        if not isinstance(self.domains, tuple) or not self.domains or type(self.distribution_approved) is not bool:
            raise ContractError('public_source_invalid')
        for host in self.domains:
            if (not isinstance(host, str) or len(host) > 253 or '.' not in host
                    or not re.fullmatch(r'[a-z0-9.-]+',host) or '..' in host
                    or host.startswith(('.', '-')) or host.endswith(('.', '-'))):
                raise ContractError('public_source_invalid')
            try:
                if not ipaddress.ip_address(host).is_global:
                    raise ContractError('public_source_invalid')
            except ValueError as exc:
                if isinstance(exc, ContractError):
                    raise

    def accepts(self, url):
        try:
            text(url, 2048)
            p = urlsplit(url)
            if (p.scheme != 'https' or p.hostname not in self.domains or p.username is not None or p.password is not None
                    or p.port not in (None,443) or p.fragment or any(ord(c)<33 for c in url)
                    or any(credential_query_key(k) for k,_ in parse_qsl(p.query,keep_blank_values=True))):
                raise ContractError('public_source_mismatch')
            # Reject signed/session-bearing URLs before any persistence. Merely
            # validating a URL here never causes it to be fetched.
            canonical_url(url)
        except (ValueError, TypeError) as exc:
            raise ContractError('public_source_mismatch') from exc


def validate_batch(value, query: PublicQuery, registry: dict[str, PublicSource]):
    if (not isinstance(value, dict) or set(value) != {'schema_version','jobs','next_cursor','generated_at'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['jobs'], list) or len(value['jobs']) > query.limit):
        raise ContractError()
    for source in query.source_scope:
        if source not in registry or not registry[source].distribution_approved:
            raise ContractError('public_source_unapproved')
    text(value['next_cursor'],1024,empty=True)
    if value['next_cursor'] and not re.fullmatch(r'[0-9a-zA-Z_.-]+',value['next_cursor']):
        raise ContractError('public_cursor_invalid')
    try:
        parse_time(text(value['generated_at'],60))
    except (ValueError, TypeError) as exc:
        raise ContractError() from exc
    result, identities = [], set()
    required = {'id','source','title','company','location','remote','text','url','final_url',
                'collected_at','completeness','adapter_version'}
    for raw in value['jobs']:
        if not isinstance(raw,dict) or set(raw) != required:
            raise ContractError()
        if (not isinstance(raw['id'],str) or not ID.fullmatch(raw['id'])
                or not isinstance(raw['source'],str) or raw['source'] not in query.source_scope
                or not isinstance(raw['completeness'],str) or raw['completeness'] not in {'full_text','snippet'}
                or (raw['remote'] is not None and type(raw['remote']) is not bool)):
            raise ContractError('public_source_mismatch')
        identity=(raw['source'],raw['id'])
        if identity in identities:
            raise ContractError('public_duplicate_result')
        identities.add(identity)
        source=registry[raw['source']]
        source.accepts(raw['url']); source.accepts(raw['final_url'])
        text(raw['title'],2000); text(raw['company'],2000,empty=True)
        text(raw['location'],2000,empty=True); text(raw['text'],150000)
        text(raw['adapter_version'],100)
        if raw['completeness']=='full_text' and len(raw['text'].strip())<100:
            raise ContractError('public_content_incomplete')
        try:
            parse_time(text(raw['collected_at'],60))
        except (ValueError,TypeError) as exc:
            raise ContractError() from exc
        if parse_time(raw['collected_at']) > parse_time(value['generated_at']):
            raise ContractError('public_timestamp_invalid')
        if query.region and query.region.casefold() not in raw['location'].casefold():
            raise ContractError('public_filter_mismatch')
        if query.remote is not None and raw['remote'] is not query.remote:
            raise ContractError('public_filter_mismatch')
        result.append(dict(raw))
    # Validate the entire response before accepting ANY row. Completeness and
    # cache freshness are independent; never upgrade snippets on cache reads.
    return {**value,'jobs':result}


def as_record(job: dict, source: PublicSource) -> JobRecord:
    """Only call after batch validation. Source identities remain in local audit."""
    return JobRecord(title=job['title'],company=job['company'],location=job['location'],
        text=job['text'],url=job['final_url'],platform=source.key,source_mode='authorized_feed',
        evidence_level=job['completeness'],collected_at=job['collected_at'],
        source_ref=source.key+':'+job['id'],rights_note=source.contract,
        parser=job['adapter_version'],is_synthetic=False)


def checked_registry(value):
    if (not isinstance(value,dict) or len(value)>1000
            or any(not isinstance(v,PublicSource) or k!=v.key for k,v in value.items())):
        raise ContractError('public_registry_invalid')
    return dict(value)
