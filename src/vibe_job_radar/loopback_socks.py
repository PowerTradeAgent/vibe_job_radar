"""SOCKS5 CONNECT to verified numeric public targets via an explicit local peer.

This is deliberately NOT socks5h, remote DNS, PAC, authentication, a LAN proxy
or a change to target validation. Final TLS remains in PinnedHTTPSConnection.
"""
from __future__ import annotations

import ipaddress
import math
import os
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .loopback_proxy import LocalProxyError, LoopbackProxy


@dataclass(frozen=True)
class LoopbackSocks5(LoopbackProxy):
    """Immutable local endpoint; inherits loopback and port validation."""

    @classmethod
    def from_environment(cls) -> LoopbackSocks5 | None:
        raw = os.environ.get('VIBE_RADAR_SOCKS_PROXY', '')
        if not raw.strip():
            return None
        try:
            if len(raw) > 2048 or any(ord(c) < 32 for c in raw):
                raise ValueError
            p = urlsplit(raw.strip())
            host = '127.0.0.1' if p.hostname == 'localhost' else p.hostname
            if (p.scheme != 'socks5' or not host or p.port is None
                    or p.username is not None or p.password is not None
                    or p.path not in ('', '/') or p.query or p.fragment):
                raise ValueError
            return cls(host, p.port)
        except (ValueError, TypeError, LocalProxyError) as exc:
            raise LocalProxyError('local_socks_configuration_invalid') from exc

    def open_tunnel(self, public_ip: str, timeout: float, source_address=None) -> socket.socket:
        try:
            address = ipaddress.ip_address(public_ip)
        except (ValueError, TypeError) as exc:
            raise LocalProxyError('non_public_address') from exc
        if not address.is_global or '%' in public_ip:
            raise LocalProxyError('non_public_address')
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('a finite positive timeout is required')
        deadline = time.monotonic() + timeout
        sock = None

        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('SOCKS handshake deadline')
            return value

        def send(data: bytes) -> None:
            sock.settimeout(remaining())
            sock.sendall(data)

        def receive(length: int) -> bytes:
            chunks = bytearray()
            while len(chunks) < length:
                sock.settimeout(remaining())
                part = sock.recv(length - len(chunks))
                if not part:
                    raise LocalProxyError('local_socks_truncated_reply')
                chunks.extend(part)
            return bytes(chunks)

        try:
            sock = socket.create_connection((self.host, self.port), remaining(), source_address)
            # Only NO AUTH is offered; do not negotiate a method we cannot honor.
            send(b'\x05\x01\x00')
            version, method = receive(2)
            if version != 5:
                raise LocalProxyError('local_socks_protocol_error')
            if method != 0:
                raise LocalProxyError('local_socks_auth_unsupported')
            atyp = 1 if address.version == 4 else 4
            # Explicit numeric target, port 443. Never send a hostname for DNS.
            send(bytes((5, 1, 0, atyp)) + address.packed + b'\x01\xbb')
            version, reply, reserved, bound_type = receive(4)
            if version != 5 or reserved != 0:
                raise LocalProxyError('local_socks_protocol_error')
            if reply != 0:
                raise LocalProxyError('local_socks_request_rejected')
            if bound_type == 1:
                receive(4)
            elif bound_type == 4:
                receive(16)
            elif bound_type == 3:
                # The bound address is informational, never used as a new target.
                length = receive(1)[0]
                if not length:
                    raise LocalProxyError('local_socks_protocol_error')
                receive(length)
            else:
                raise LocalProxyError('local_socks_protocol_error')
            receive(2)
            sock.settimeout(remaining())
            result, sock = sock, None
            return result
        except LocalProxyError:
            raise
        except TimeoutError as exc:
            raise LocalProxyError('local_socks_timeout') from exc
        except OSError as exc:
            raise LocalProxyError('local_socks_connection_failed') from exc
        finally:
            if sock is not None:
                sock.close()


def select_loopback_proxy() -> LoopbackProxy | LoopbackSocks5 | None:
    """Explicit settings only; never guess or silently choose another route."""
    http = os.environ.get('VIBE_RADAR_HTTP_PROXY', '').strip()
    socks = os.environ.get('VIBE_RADAR_SOCKS_PROXY', '').strip()
    if http and socks:
        raise LocalProxyError('local_proxy_configuration_conflict')
    return LoopbackSocks5.from_environment() if socks else LoopbackProxy.from_environment()
