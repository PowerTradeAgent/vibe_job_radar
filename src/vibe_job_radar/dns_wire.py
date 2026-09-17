"""Bounded DNS wire codec for A/AAAA over an authenticated DoH connection.

Not a general recursive resolver or a DNSSEC verifier. Only addresses belonging
 to the requested name's CNAME chain can be returned. Additional data is never a
connection target. RFC 1035 / 8484; no external package or network in this module.
"""
from __future__ import annotations

import ipaddress
import math
import re
import struct
from dataclasses import dataclass


class ResolutionError(RuntimeError):
    def __init__(self, code: str, *, diagnostic: dict | None = None):
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(code)


def invalid():
    raise ResolutionError('encrypted_dns_invalid_response')


def hostname(value: str) -> str:
    if not isinstance(value, str):
        invalid()
    try:
        value = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        invalid()
    if (not 1 <= len(value) <= 253 or '.' not in value
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p)
                   for p in value.split('.'))):
        invalid()
    return value


def query(host: str, kind: int) -> bytes:
    if kind not in (1, 28):
        invalid()
    name = hostname(host)
    labels = b''.join(bytes([len(p)]) + p.encode('ascii') for p in name.split('.')) + b'\x00'
    # ID=0 (HTTP correlates messages), recursion desired, no ECS or client ID.
    return struct.pack('!6H', 0, 0x0100, 1, 0, 0, 0) + labels + struct.pack('!2H', kind, 1)


def _name(data: bytes, pos: int) -> tuple[str, int]:
    labels, seen, end, size = [], set(), None, 1
    while True:
        if not 0 <= pos < len(data) or pos in seen or len(seen) > 128:
            invalid()
        seen.add(pos)
        length = data[pos]
        if length & 0xc0 == 0xc0:
            if pos + 1 >= len(data):
                invalid()
            target = ((length & 0x3f) << 8) | data[pos + 1]
            if target >= pos or target < 12:
                invalid()
            end = pos + 2 if end is None else end
            pos = target
            continue
        if length & 0xc0 or pos + 1 + length > len(data):
            invalid()
        pos += 1
        if not length:
            return '.'.join(labels).lower(), end if end is not None else pos
        try:
            label = data[pos:pos+length].decode('ascii')
        except UnicodeError:
            invalid()
        if not re.fullmatch(r'[A-Za-z0-9_-]+', label):
            invalid()
        labels.append(label)
        size += length + 1
        if size > 255:
            invalid()
        pos += length


@dataclass(frozen=True)
class Answer:
    addresses: tuple[str, ...]
    ttl: float
    canonical: str
    # Cache policy may set ttl=0 while the answer's positive validity continues.
    # None preserves the original three-argument internal/testing constructor.
    valid_for: float | None = None


def parse_answer(data: bytes, host: str, kind: int, *, age: int = 0,
                 elapsed: float = 0.0) -> Answer:
    if (not isinstance(data, bytes) or not 12 <= len(data) <= 65535
            or kind not in (1, 28) or type(age) is not int or age < 0
            or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
        invalid()
    expected = hostname(host)
    ident, flags, qd, an, ns, ar = struct.unpack_from('!6H', data)
    # Response, QUERY opcode, untruncated, reserved Z=0, CD=0. AD is not
    # treated as local DNSSEC verification; trust is authenticated HTTPS.
    if ident != 0 or not flags & 0x8000 or flags & (0x7800 | 0x0200 | 0x0040 | 0x0010):
        invalid()
    if qd != 1 or an + ns + ar > 256:
        invalid()
    name, pos = _name(data, 12)
    if pos + 4 > len(data) or name != expected or struct.unpack_from('!2H', data, pos) != (kind, 1):
        invalid()
    pos += 4
    rcode = flags & 15
    if rcode not in (0, 3):
        raise ResolutionError('encrypted_dns_refused')
    aliases, addresses, negative_ttls = {}, [], []
    for index in range(an + ns + ar):
        owner, pos = _name(data, pos)
        if pos + 10 > len(data):
            invalid()
        rtype, rclass, ttl, length = struct.unpack_from('!HHIH', data, pos)
        pos += 10
        # RFC 2181 section 8: high-bit TTLs are zero, not multi-decade leases.
        # OPT overloads this field; retain it for the EDNS checks below.
        if rtype != 41 and ttl & 0x80000000:
            ttl = 0
        end = pos + length
        if end > len(data):
            invalid()
        if rtype in (1, 28):
            if rclass != 1 or length != (4 if rtype == 1 else 16):
                invalid()
            address = ipaddress.ip_address(data[pos:end])
            if not address.is_global:
                raise ResolutionError('encrypted_dns_non_public_answer')
            if index < an:
                if rtype != kind:
                    invalid()
                addresses.append((owner, str(address), ttl))
        elif rtype == 5:
            canonical, used = _name(data, pos)
            if rclass != 1 or used != end:
                invalid()
            canonical = hostname(canonical)
            if index < an:
                if owner in aliases and aliases[owner][0] != canonical:
                    invalid()
                aliases[owner] = (canonical, min(ttl, aliases.get(owner, ('', ttl))[1]))
        elif rtype == 6:  # RFC 2308 negative cache bound from SOA.
            _, used = _name(data, pos)
            _, used = _name(data, used)
            if rclass != 1 or used + 20 != end:
                invalid()
            minimum = struct.unpack_from('!5I', data, used)[4]
            if an <= index < an + ns:
                negative_ttls.append(min(ttl, minimum))
        elif rtype == 41:
            if index < an + ns or owner or ttl >> 16:
                invalid()  # No extended error or unknown EDNS version.
        elif index < an:
            invalid()  # DNAME and other answer types are not silently widened.
        pos = end
    if pos != len(data):
        invalid()
    if rcode == 3:
        if addresses:
            invalid()
        raise ResolutionError('encrypted_dns_name_not_found')
    canonical, visited, ttls = expected, set(), []
    while canonical in aliases:
        if canonical in visited or len(visited) >= 8:
            invalid()
        visited.add(canonical)
        canonical, ttl = aliases[canonical]
        ttls.append(ttl)
    if set(aliases) != visited or any(owner != canonical for owner, _, _ in addresses):
        invalid()
    if not addresses:
        ttls.append(min(negative_ttls, default=0))
    ttls.extend(ttl for _, _, ttl in addresses)
    lifetime = min(ttls, default=0)
    spent = age + elapsed
    # A genuinely zero-TTL answer may serve this transaction, never the cache.
    # A formerly positive TTL exhausted by transit/Age is *not* that exception.
    if (lifetime > 0 and spent >= lifetime) or (lifetime == 0 and age > 0):
        raise ResolutionError('encrypted_dns_expired_answer')
    remaining = float(max(0, lifetime - spent))
    return Answer(tuple(dict.fromkeys(ip for _, ip, _ in addresses)),
                  remaining, canonical, valid_for=remaining)
