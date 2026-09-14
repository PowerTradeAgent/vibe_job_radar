from __future__ import annotations

import copy
import json
import re
from importlib.resources import files
from pathlib import Path
from .models import JobRecord
from .utils import domain_matches
from urllib.parse import urlsplit


def load_config(path: str | Path | None = None) -> dict:
    conf = json.loads(files("vibe_job_radar").joinpath("defaults.json").read_text(encoding="utf-8"))
    if path:
        override = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        for key, value in override.items():
            if key not in conf:
                raise ValueError(f"unknown config key: {key}")
            if isinstance(conf[key], dict) and isinstance(value, dict):
                conf[key].update(value)
            else:
                conf[key] = value
    validate(conf)
    return copy.deepcopy(conf)


def validate(c: dict) -> None:
    for name in ("platforms", "roles", "capabilities", "tools"):
        if not isinstance(c.get(name), dict) or not c[name]:
            raise ValueError(f"{name} must be a non-empty object")
    for role in c["roles"].values():
        if not role.get("title_terms"):
            raise ValueError("each role needs title_terms")
    for p in c["platforms"].values():
        for domain in p["domains"]:
            if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain) or ".." in domain:
                raise ValueError("invalid domain")
    for pattern in c["direct_patterns"] + list(c["tools"].values()):
        re.compile(pattern, re.I)
    for cap in c["capabilities"].values():
        for p in cap["patterns"]:
            re.compile(p, re.I)
    if not 0 < c["common_threshold"] <= 1:
        raise ValueError("common_threshold must be in (0,1]")
    import math
    if set(c["weights"]) != {"required", "expected", "preferred", "unspecified"} or any(
            type(v) not in {int, float} or not math.isfinite(v) or v <= 0 for v in c["weights"].values()):
        raise ValueError("weights must be finite positive numbers for required/expected/preferred/unspecified")
    if not c["query_groups"] or not all(isinstance(q, str) and q.strip() for q in c["query_groups"]):
        raise ValueError("query_groups must contain query strings")


def platform_for_url(url: str, config: dict) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for key, p in config["platforms"].items():
        if any(domain_matches(host, d) for d in p["domains"]):
            return key
    return "unknown"


def detect_roles(job: JobRecord, config: dict) -> list[str]:
    title = job.title.casefold()
    out = []
    for key, r in config["roles"].items():
        title_hit = any(t.casefold() in title for t in r["title_terms"])
        fallback = (any(t.casefold() in title for t in r.get("fallback_title_terms", []))
                    and any(t.casefold() in job.text.casefold() for t in r.get("body_terms", [])))
        if title_hit or fallback:
            out.append(key)
    return out
