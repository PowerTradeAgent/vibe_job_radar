"""Liepin detail identity and bounded semantic JD extraction.

No private API, login automation, network permission or live certification is
introduced here. Only content already returned through the existing backend is
read. The semantic fallback is deliberately conservative; unknown layouts fail.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

from ..html_parser import Document, Node, jobpostings
from .adapters import DOMAdapter
from .contracts import Card, CrawlError, PageSnapshot

_OMIT = {'script', 'style', 'nav', 'footer', 'aside', 'noscript', 'template', 'iframe', 'svg'}
_HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dt'}
_FOREIGN = re.compile(r'推荐职位|相似职位|猜你喜欢|公司简介|公司信息|猎聘温馨提示')
_INCOMPLETE = re.compile(r'登录后.{0,8}(?:查看|浏览)|查看完整.{0,4}(?:职位|描述)|展开(?:全部|更多)|安全验证|滑动.{0,8}验证')


def _hidden(node: Node) -> bool:
    style = re.sub(r'\s+', '', node.attrs.get('style', '')).lower()
    return (node.tag in _OMIT or 'hidden' in node.attrs
            or node.attrs.get('aria-hidden', '').lower() == 'true'
            or bool(re.search(r'(?:^|;)display:none(?:!important)?(?:;|$)', style))
            or bool(re.search(r'(?:^|;)visibility:hidden(?:!important)?(?:;|$)', style)))


def _walk(node: Node, parent: Node | None = None):
    if _hidden(node):
        return
    yield node, parent
    for child in node.children:
        if isinstance(child, Node):
            yield from _walk(child, node)


def _text(node: Node) -> str:
    if _hidden(node):
        return ''
    result = ''.join(_text(c) if isinstance(c, Node) else c for c in node.children)
    return result + ('\n' if node.tag in {'p','div','li','br','h1','h2','h3','h4','section','dd'} else '')


def _clean(value: str) -> str:
    return re.sub(r'\n\s*\n+', '\n', re.sub(r'[ \t\xa0]+', ' ', value)).strip()


def _has_structured_job(root: Node) -> bool:
    # An explicit structured identity conflict must not disappear through DOM
    # fallback. The established JSON-LD parser remains authoritative in that case.
    for node in root.walk():
        if node.tag == 'script' and node.attrs.get('type', '').lower() == 'application/ld+json':
            try:
                if any(jobpostings(json.loads(node.text(include_script=True)))):
                    return True
            except (ValueError, TypeError, RecursionError):
                continue
    return False


def semantic_detail(markup: str) -> dict:
    """Read one locally returned, explicitly labelled introduction, not the body."""
    if not isinstance(markup, str) or len(markup) > 5_000_000:
        raise CrawlError('response_too_large')
    try:
        root = Document(markup).root
        visible = list(_walk(root))
        titles = [_clean(_text(n)) for n, _ in visible if n.tag == 'h1']
        headings = [(n, p) for n, p in visible
                    if n.tag in {'dt','h2','h3','h4'} and _clean(_text(n)) == '职位介绍']
        if len(titles) != 1 or not titles[0] or len(headings) != 1:
            raise CrawlError('structure_changed')
        heading, parent = headings[0]
        if parent is None or parent.tag not in {'dl','section','article','div'}:
            raise CrawlError('structure_changed')
        # Identity, not dataclass equality: two equal-looking heading nodes must
        # not make us extract from the wrong sibling position.
        index = next(i for i, node in enumerate(parent.children) if node is heading)
        fragments = []
        for child in parent.children[index + 1:]:
            if not isinstance(child, Node):
                if child.strip():
                    raise CrawlError('structure_changed')
                continue
            if _hidden(child):
                continue
            if child.tag in _HEADINGS:
                break
            if parent.tag == 'dl' and child.tag != 'dd':
                raise CrawlError('structure_changed')
            if any(n.tag in _HEADINGS for n, _ in _walk(child)):
                # Nested sections need a separately verified layout; do not take
                # an enclosing panel that also contains employer/recommendations.
                raise CrawlError('structure_changed')
            fragments.append(_text(child))
        body = _clean('\n'.join(fragments))
        if _INCOMPLETE.search(body):
            raise CrawlError('jd_incomplete')
        if (_FOREIGN.search(body) or len(body) < 40 or len(body) > 150_000
                or not re.search(r'职责|要求|岗位描述|职位描述|工作内容', body)):
            raise CrawlError('structure_changed')
        return {'title': titles[0], 'text': body, 'parser': 'liepin:semantic_intro:v1'}
    except CrawlError:
        raise
    except (ValueError, TypeError, RecursionError, StopIteration) as exc:
        raise CrawlError('structure_changed') from exc


class LiepinAdapter(DOMAdapter):
    """Site-specific entity checks without widening the base access contract."""

    def job_identity(self, url: str) -> str:
        accepted = self.accept_url(url, detail=True)
        p = urlsplit(accepted)
        match = re.fullmatch(r'/(job|a)/([A-Za-z0-9_-]{1,80})\.(?:shtml|html)|/lptjob/([0-9]{1,80})', p.path)
        if not match:
            raise CrawlError('not_job_url')
        kind, ident = (match[1], match[2]) if match[3] is None else ('lptjob', match[3])
        # Different URL families and hostnames are NOT assumed to be aliases.
        return f'{self.key}:{p.hostname}:{kind}:{ident}'

    def cards(self, page: PageSnapshot) -> list[Card]:
        if urlsplit(page.url).path.rstrip('/') != urlsplit(self.search_base).path.rstrip('/'):
            # Challenge URLs keep their meaningful existing error classification.
            if self.challenged('', page.url):
                raise CrawlError('manual_required')
            raise CrawlError('not_job_list')
        cards = {}
        for item in super().cards(page):
            try:
                entity = self.job_identity(item.url)
            except CrawlError:
                continue
            # Keep the established Card.id for persisted task selections.
            # Entity deduplication must not silently migrate old task/page IDs.
            cards.setdefault(entity, item)
        return list(cards.values())

    def validate_detail_identity(self, expected_url: str, page: PageSnapshot) -> None:
        expected = self.job_identity(expected_url)
        if self.job_identity(page.url) != expected:
            raise CrawlError('job_identity_mismatch')
        # Canonical declarations are checked as data, never followed or trusted
        # as permission. Conflicting declarations must remain visible as failure.
        if not isinstance(page.html, str) or len(page.html) > 5_000_000:
            raise CrawlError('response_too_large')
        try:
            root = Document(page.html).root
        except (ValueError, RecursionError) as exc:
            raise CrawlError('structure_changed') from exc
        for node in root.walk():
            if node.tag == 'link' and 'canonical' in node.attrs.get('rel', '').lower().split():
                href = node.attrs.get('href', '')
                if not href or self.job_identity(urljoin(page.url, href)) != expected:
                    raise CrawlError('job_identity_mismatch')

    def detail(self, page: PageSnapshot) -> dict:
        self.job_identity(page.url)
        self.validate_detail_identity(page.url, page)
        try:
            parsed = super().detail(page)
            if _INCOMPLETE.search(parsed['text']):
                raise CrawlError('jd_incomplete')
            return parsed
        except CrawlError as exc:
            if exc.code != 'structure_changed':
                raise
            if _has_structured_job(Document(page.html).root):
                raise
            return semantic_detail(page.html)
