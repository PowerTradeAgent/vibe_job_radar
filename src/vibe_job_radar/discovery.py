from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlencode, urlsplit
from .config import platform_for_url
from .html_parser import plain_text
from .models import JobRecord
from .network import FetchError, SafeHTTP
from .utils import canonical_url, digest, domain_matches, utc_now

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class SearchTask:
    query_id: str
    platform: str
    role: str
    query: str
    status: str = "planned"
    pages_requested: int = 0
    results_seen: int = 0
    leads_stored: int = 0
    error: str = ""


def build_plan(config: dict, platforms: list[str] | None = None, roles: list[str] | None = None) -> list[SearchTask]:
    platforms = platforms or ["boss", "liepin", "51job"]
    roles = roles or list(config["roles"])
    for p in platforms:
        if p not in config["platforms"]:
            raise ValueError(f"unknown platform: {p}")
    for r in roles:
        if r not in config["roles"]:
            raise ValueError(f"unknown role: {r}")
    plan = []
    max_aliases = max(len(config["roles"][r]["query_terms"]) for r in roles)
    # Interleave platforms/roles: a small request budget must not cover only the first platform.
    for alias_index in range(max_aliases):
        for terms in config["query_groups"]:
            for p in platforms:
                for r in roles:
                    aliases = config["roles"][r]["query_terms"]
                    if alias_index >= len(aliases):
                        continue
                    domains = config["platforms"][p]["domains"]
                    domain_query = "(" + " OR ".join("site:" + d for d in domains) + ")"
                    query = f"{domain_query} {aliases[alias_index]} {terms} 招聘"
                    plan.append(SearchTask("q_" + digest(query)[:16], p, r, query))
    return plan


def discover(store, config: dict, *, api_key: str, plan: list[SearchTask], max_requests: int = 20,
             pages: int = 1, count: int = 20, transport=None) -> dict:
    if not api_key:
        raise ValueError("BRAVE_SEARCH_API_KEY is required; use plan for a no-network preview")
    if not 1 <= pages <= 10 or not 1 <= count <= 20 or max_requests < 1:
        raise ValueError("pages must be 1..10; count 1..20; max_requests positive")
    http = transport or SafeHTTP({"api.search.brave.com"}, interval=1.1)
    requests = 0
    stop_reason = ""
    rejected = []
    for task in plan:
        if stop_reason or requests >= max_requests:
            task.status = "budget_skipped" if not stop_reason else "provider_stopped"
            task.error = stop_reason
            continue
        task.status = "completed_with_page_limit"
        for page in range(pages):
            if requests >= max_requests:
                task.status = "budget_limited"
                break
            # Brave offset is a PAGE offset (0..9), not page*count.
            params = {"q": task.query, "count": count, "offset": page, "search_lang": "zh-hans", "country": "cn"}
            requests += 1
            task.pages_requested += 1
            try:
                data = http.json(BRAVE_ENDPOINT + "?" + urlencode(params),
                                 headers={"X-Subscription-Token": api_key})
                web = data.get("web") or {}
                results = web.get("results") or []
                if not isinstance(results, list):
                    raise FetchError("invalid_search_results")
                task.results_seen += len(results)
                for rank, item in enumerate(results, 1):
                    try:
                        if not isinstance(item, dict):
                            raise ValueError("search result is not an object")
                        url = canonical_url(item.get("url", ""))
                        platform = platform_for_url(url, config)
                        if not url or platform != task.platform:
                            raise ValueError("search result outside requested platform")
                        if urlsplit(url).path in {"", "/"}:
                            raise ValueError("homepage result, not a job detail")
                        snippet = plain_text(item.get("description") or "")
                        title = plain_text(item.get("title") or "")
                        job = JobRecord(title=title, text=snippet, platform=platform, url=url,
                                        evidence_level="snippet", source_mode="search_api",
                                        source_ref=f"brave:{task.query_id}:page={page}:rank={rank}")
                        store.add(job)
                        task.leads_stored += 1
                    except (ValueError, TypeError) as exc:
                        rejected.append({"query_id": task.query_id, "page": page, "rank": rank, "reason": str(exc)})
                store.event("search", "ok", {**asdict(task), "offset": page})
                # Respect provider's exhaustion flag; fewer than count alone is NOT proof of exhaustion.
                if not results or data.get("query", {}).get("more_results_available") is False:
                    task.status = "provider_window_exhausted"
                    break
            except (FetchError, ValueError, TypeError) as exc:
                task.status = "error"
                task.error = str(exc)
                store.event("search", "error", {"query_id": task.query_id, "error": str(exc)})
                if isinstance(exc, FetchError) and exc.code in {"http_401", "http_403", "http_429", "host_circuit_open"}:
                    stop_reason = exc.code
                break
    return {"created_at": utc_now(), "requests_made": requests, "query_count": len(plan),
            "tasks": [asdict(t) for t in plan], "rejected_results": rejected,
            "complete_market_coverage": False,
            "note": "Search-index window only. completed/exhausted never means all jobs or all platforms."}
