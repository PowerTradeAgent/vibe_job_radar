from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit


class ParseError(ValueError):
    pass


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self, include_script: bool = False) -> str:
        if not include_script and self.tag in {"script", "style", "nav", "footer", "noscript"}:
            return ""
        s = "".join(c.text(include_script) if isinstance(c, Node) else c for c in self.children)
        return s + ("\n" if self.tag in {"p", "div", "li", "br", "h1", "h2", "h3", "section"} else "")


class Document(HTMLParser):
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"br", "hr", "meta", "link", "img", "input", "area", "base", "embed", "source", "wbr", "param"}:
            if len(self.stack) > 200:
                raise ParseError("HTML nesting too deep")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def plain_text(markup: str) -> str:
    text = Document(markup).root.text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def jobpostings(value, depth=0):
    if depth > 60:
        raise ParseError("JSON-LD nesting too deep")
    if isinstance(value, dict):
        typ = value.get("@type", "")
        if "JobPosting" in (typ if isinstance(typ, list) else [typ]):
            yield value
        else:
            for child in value.values():
                yield from jobpostings(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from jobpostings(child, depth + 1)


def parse_job_html(markup: str, *, source_url: str = "") -> dict:
    """Return only an isolated JD, never the entire body or search recommendations."""
    if source_url and urlsplit(source_url).path in {"", "/"}:
        raise ParseError("homepage is not a job-detail URL")
    if re.search(r"请完成.{0,12}验证|滑动.{0,8}验证|安全验证|captcha|登录后.{0,8}(?:查看|浏览)", markup, re.I):
        raise ParseError("login/challenge page")
    doc = Document(markup)
    nodes = list(doc.root.walk())
    structured = []
    for node in nodes:
        if node.tag == "script" and (node.attrs.get("type") or "").lower() == "application/ld+json":
            try:
                structured.extend(jobpostings(json.loads(node.text(include_script=True))))
            except (json.JSONDecodeError, TypeError):
                continue
    # Identical scripts are common; different postings imply a list/recommendations page.
    unique = {json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in structured}
    if len(unique) > 1:
        raise ParseError("multiple JobPosting objects: isolate one posting before import")
    if unique:
        posting = next(iter(unique.values()))
        text = plain_text(str(posting.get("description") or ""))
        title = unescape(str(posting.get("title") or "")).strip()
        if len(text) < 20 or not title:
            raise ParseError("incomplete JobPosting description/title")
        org = posting.get("hiringOrganization") or {}
        company = org.get("name", "") if isinstance(org, dict) else ""
        return {"title": title, "text": text, "company": str(company), "parser": "json_ld_jobposting",
                "published_at": str(posting.get("datePosted") or "")}
    # Dedicated containers only. No full-body fallback, and no brittle undocumented API.
    for selector in ("job-sec-text", "job-description", "job-detail-body", "job-detail-content", "job_msg", "bmsg", "job-detail"):
        matches = [n for n in nodes if selector in (n.attrs.get("class") or "").split()]
        if len(matches) > 1:
            raise ParseError(f"ambiguous container: {selector}")
        if len(matches) == 1:
            title_nodes = [n for n in nodes if n.tag == "h1"]
            if len(title_nodes) != 1:
                raise ParseError("exactly one job title h1 required for DOM extraction")
            text = plain_text(matches[0].text())
            if len(text) < 20:
                raise ParseError("job-description container too short")
            return {"title": title_nodes[0].text().strip(), "text": text,
                    "parser": "dom:" + selector}
    raise ParseError("no isolated JobPosting/container found; supply authorized JD text")
