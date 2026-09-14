"""Small synchronous HTTPS transport: public IP pinning, verified TLS, no redirects/proxies.

Site fetching is opt-in and additionally robots-gated. API calls use documented
endpoints and explicit credentials, not the site-fetch path.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from .utils import domain_matches

USER_AGENT = "VibeJobRadar/0.1"


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    def text(self) -> str:
        import re
        match = re.search(r'charset=["\']?([\w-]+)', self.headers.get("content-type", ""), re.I)
        if match:
            try:
                return self.body.decode(match.group(1))
            except (LookupError, UnicodeDecodeError):
                pass
        for enc in ("utf-8-sig", "gb18030"):
            try:
                return self.body.decode(enc)
            except UnicodeDecodeError:
                pass
        raise FetchError("encoding_unknown", "save and review the page manually")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        # DNS is resolved/validated once by SafeHTTP. TLS still validates the original hostname.
        sock = socket.create_connection((self._pinned_ip, 443), self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def validate_public_url(url: str, allowed_domains: set[str]) -> tuple[str, str, str]:
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
        raise FetchError("unsafe_url", "only credential-free HTTPS URLs without fragments are fetched")
    if p.port not in (None, 443):
        raise FetchError("unsafe_port")
    host = p.hostname.lower().encode("idna").decode("ascii")
    if not any(domain_matches(host, d) for d in allowed_domains):
        raise FetchError("domain_not_permitted", host)
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise FetchError("non_public_address")
    except (socket.gaierror, ValueError) as exc:
        raise FetchError("dns_error", type(exc).__name__) from exc
    target = urlunsplit(("", "", p.path or "/", p.query, ""))
    return host, ips[0], target


class SafeHTTP:
    def __init__(self, allowed_domains: set[str], *, timeout: float = 20.0, max_bytes: int = 5_000_000,
                 interval: float = 1.0):
        if timeout <= 0 or max_bytes <= 0 or interval < 0:
            raise ValueError("invalid transport limits")
        self.allowed_domains = set(allowed_domains)
        self.timeout, self.max_bytes, self.interval = timeout, max_bytes, interval
        self.last_request: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()

    def request(self, url: str, *, method: str = "GET", headers: dict | None = None,
                body: bytes | None = None) -> Response:
        host, ip, target = validate_public_url(url, self.allowed_domains)
        if host in self.blocked_hosts:
            raise FetchError("host_circuit_open", host)
        wait = self.interval - (time.monotonic() - self.last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        conn = PinnedHTTPSConnection(host, ip, self.timeout)
        request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        request_headers.update(headers or {})
        try:
            conn.request(method, target, body=body, headers=request_headers)
            resp = conn.getresponse()
            response_headers = {k.lower(): v for k, v in resp.getheaders()}
            if resp.status in (401, 403, 429):
                self.blocked_hosts.add(host)
                raise FetchError(f"http_{resp.status}", "stopped; no bypass or retry")
            if 300 <= resp.status < 400:
                raise FetchError("redirect_not_followed", "inspect and explicitly supply the permitted final URL")
            if response_headers.get("content-encoding", "identity").lower() != "identity":
                raise FetchError("unexpected_compression")
            data = resp.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                raise FetchError("response_too_large")
            return Response(resp.status, response_headers, data, url)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError("network_error", type(exc).__name__) from exc
        finally:
            self.last_request[host] = time.monotonic()
            conn.close()

    def json(self, url: str, *, method: str = "GET", headers: dict | None = None, payload: dict | None = None) -> dict:
        hdr = {"Accept": "application/json", **(headers or {})}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            hdr["Content-Type"] = "application/json"
        result = self.request(url, method=method, headers=hdr, body=body)
        if result.status != 200:
            raise FetchError(f"http_{result.status}")
        try:
            data = json.loads(result.text())
        except (ValueError, UnicodeError) as exc:
            raise FetchError("invalid_api_json") from exc
        if not isinstance(data, dict):
            raise FetchError("invalid_api_shape")
        return data


class SiteFetcher:
    def __init__(self, permitted_domains: set[str], transport: SafeHTTP | None = None):
        if not permitted_domains:
            raise ValueError("explicit permitted domains are required")
        self.transport = transport or SafeHTTP(permitted_domains, interval=2.0)
        self.robots: dict[str, RobotFileParser | None] = {}

    def fetch(self, url: str) -> Response:
        p = urlsplit(url)
        origin = f"https://{p.netloc}"
        if origin not in self.robots:
            self.robots[origin] = None  # Fail closed, including unavailable/404 robots.
            result = self.transport.request(origin + "/robots.txt")
            if result.status == 200 and not result.headers.get("content-type", "").lower().startswith("text/html"):
                rp = RobotFileParser()
                rp.parse(result.text().splitlines())
                self.robots[origin] = rp
        rp = self.robots[origin]
        if rp is None:
            raise FetchError("robots_unavailable", "automation is not enabled for this origin")
        if not rp.can_fetch(USER_AGENT, url):
            raise FetchError("robots_denied")
        delay = rp.crawl_delay(USER_AGENT)
        if delay:
            self.transport.interval = max(self.transport.interval, float(delay))
        rate = rp.request_rate(USER_AGENT)
        if rate and rate.requests:
            self.transport.interval = max(self.transport.interval, rate.seconds / rate.requests)
        result = self.transport.request(url)
        if result.status != 200:
            raise FetchError(f"http_{result.status}")
        if "html" not in result.headers.get("content-type", "").lower():
            raise FetchError("not_html")
        import re
        # Challenge markers, not the ubiquitous login button on otherwise public pages.
        if re.search(r"请完成.{0,12}验证|访问过于频繁|滑动.{0,8}验证|安全验证|访问异常|captcha|登录后.{0,8}(?:查看|浏览)", result.text(), re.I):
            raise FetchError("login_or_challenge", "manual review required")
        return result
