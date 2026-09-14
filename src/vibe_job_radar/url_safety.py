"""Recognize common query-based credentials before a source URL is persisted."""
from __future__ import annotations
import re


def credential_query_key(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return (normalized in {"key", "sig", "auth", "code", "ticket", "sas"}
            or normalized.endswith("key")
            or any(part in normalized for part in (
                "token", "cookie", "session", "password", "authorization",
                "credential", "signature", "secret", "accesskey")))
