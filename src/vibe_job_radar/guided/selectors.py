"""Pure-DOM selector subset: tag, #id, .class, attributes and descendants.

Selectors identify anchor nodes (not arbitrary containers). Comma groups are
supported. Unsupported CSS syntax fails explicitly instead of widening scope.
"""
from __future__ import annotations

import re

from .contracts import CrawlError

TOKEN = re.compile(r'''(?:[a-zA-Z][\w-]*|\*)?(?:(?:[.#][\w-]+)|(?:\[[\w-]+(?:=(?:"[^"\[\]]*"|'[^'\[\]]*'|[\w-]+))?\]))*''')
PART = re.compile(r'''([.#])([\w-]+)|\[([\w-]+)(?:=(?:"([^"\[\]]*)"|'([^'\[\]]*)'|([\w-]+)))?\]''')


def _groups(selector):
    if not isinstance(selector, str) or not selector.strip() or len(selector) > 1000:
        raise CrawlError('unsupported_card_selector')
    groups, group, start, quote, bracket = [], [], 0, '', False
    for i, char in enumerate(selector + ','):
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in "\"'" and bracket:
            quote = char
        elif char == '[':
            if bracket:
                raise CrawlError('unsupported_card_selector')
            bracket = True
        elif char == ']':
            if not bracket:
                raise CrawlError('unsupported_card_selector')
            bracket = False
        elif not bracket and (char.isspace() or char == ','):
            value = selector[start:i].strip()
            if value:
                if not TOKEN.fullmatch(value):
                    raise CrawlError('unsupported_card_selector')
                group.append(value)
            start = i + 1
            if char == ',':
                if not group:
                    raise CrawlError('unsupported_card_selector')
                groups.append(group)
                group = []
    if quote or bracket or not groups:
        raise CrawlError('unsupported_card_selector')
    return groups


def _matches(node, selector):
    tag = re.match(r'^[a-zA-Z][\w-]*|^\*', selector)
    if tag and tag[0] != '*' and node.tag != tag[0].lower():
        return False
    for match in PART.finditer(selector):
        prefix, value, attribute, double, single, bare = match.groups()
        if prefix == '.' and value not in (node.attrs.get('class') or '').split():
            return False
        if prefix == '#' and node.attrs.get('id') != value:
            return False
        if attribute:
            if attribute not in node.attrs:
                return False
            expected = next((v for v in (double, single, bare) if v is not None), None)
            if expected is not None and node.attrs[attribute] != expected:
                return False
    return True


def select_nodes(root, selector):
    groups = _groups(selector)

    def visit(node, ancestors):
        for group in groups:
            if not _matches(node, group[-1]):
                continue
            remaining = list(group[:-1])
            for parent in reversed(ancestors):
                if remaining and _matches(parent, remaining[-1]):
                    remaining.pop()
            if not remaining:
                yield node
                break
        for child in node.children:
            if hasattr(child, 'tag'):
                yield from visit(child, (*ancestors, node))

    yield from visit(root, ())
