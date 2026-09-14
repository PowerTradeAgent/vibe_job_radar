from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def canonical_url(url: str) -> str:
    """Remove tracking only; preserve unknown parameters, often containing job IDs."""
    if not url:
        return ""
    p = urlsplit(url.strip())
    if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("source URL must be HTTP(S), without credentials")
    host = p.hostname.lower().encode("idna").decode()
    if ":" in host:
        host = "[" + host + "]"
    port = p.port
    netloc = host + (f":{port}" if port and (p.scheme.lower(), port) not in {("https", 443), ("http", 80)} else "")
    discard = {"ka", "from", "source", "spm", "trackingid"}
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.casefold() not in discard and not k.casefold().startswith("utm_")]
    return urlunsplit((p.scheme.lower(), netloc, p.path or "/", urlencode(sorted(query)), ""))


def domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def parse_time(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("timestamps require a timezone, e.g. +08:00 or Z")
    return d.astimezone(timezone.utc)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def atomic_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path: str | Path, value: Any) -> None:
    atomic_text(path, json_text(value) + "\n")


def csv_cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value  # Protect Excel from formula injection. JSON keeps exact originals.
    return value


def write_csv(path: str | Path, rows: Iterable[dict], fields: list[str]) -> None:
    s = io.StringIO(newline="")
    w = csv.DictWriter(s, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for row in rows:
        w.writerow({k: csv_cell(row.get(k, "")) for k in fields})
    atomic_text(path, s.getvalue(), encoding="utf-8-sig")


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
