"""Render the curated first-run Markdown to a standalone, script-free HTML guide.

Supports only the Markdown subset used by FIRST_RUN.md. No third-party runtime
or build dependency. Run with --check in CI to catch stale generated copies.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs' / 'FIRST_RUN.md'
TARGET = ROOT / 'START_HERE.html'


def inline(value: str) -> str:
    pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*')
    parts, start = [], 0
    for match in pattern.finditer(value):
        parts.append(html.escape(value[start:match.start()]))
        label, url, code, bold = match.groups()
        if url is not None:
            if urlsplit(url).scheme:
                if not url.startswith('https://'):
                    raise ValueError('Guide external links must use HTTPS')
                href = url
            else:
                resolved = (SOURCE.parent / url).resolve()
                href = resolved.relative_to(ROOT).as_posix()
            parts.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>')
        elif code is not None:
            parts.append('<code>' + html.escape(code) + '</code>')
        else:
            parts.append('<strong>' + html.escape(bold) + '</strong>')
        start = match.end()
    parts.append(html.escape(value[start:]))
    return ''.join(parts)


def render() -> str:
    blocks, paragraph, code, rows = [], [], None, []

    def flush():
        if paragraph:
            blocks.append('<p>' + inline(' '.join(paragraph)) + '</p>')
            paragraph.clear()
        if rows:
            heading = rows[0]
            body = [r for r in rows[1:] if not all(re.fullmatch(r'[:\- ]+', c) for c in r)]
            blocks.append('<div class="scroll"><table><thead><tr>' + ''.join('<th>' + inline(c) + '</th>' for c in heading)
                          + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + inline(c) + '</td>' for c in row)
                          + '</tr>' for row in body) + '</tbody></table></div>')
            rows.clear()

    for line in SOURCE.read_text(encoding='utf-8').splitlines():
        if line.startswith('```'):
            if code is None:
                flush()
                code = []
            else:
                blocks.append('<pre><code>' + html.escape('\n'.join(code)) + '</code></pre>')
                code = None
        elif code is not None:
            code.append(line)
        elif line.startswith('|'):
            if paragraph:
                flush()
            rows.append([cell.strip() for cell in line.strip('|').split('|')])
        elif not line.strip():
            flush()
        elif line.startswith('# '):
            flush()
            blocks.append('<h1>' + inline(line[2:]) + '</h1>')
        elif line.startswith('## '):
            flush()
            blocks.append('<h2>' + inline(line[3:]) + '</h2>')
        else:
            if rows:
                flush()
            paragraph.append(line)
    if code is not None:
        raise ValueError('Unclosed Markdown code fence')
    flush()
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>先读我 · Vibe Job Radar 零基础操作指南</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#192b3f;font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif}
main{max-width:1000px;margin:24px auto;padding:28px;background:white;border:1px solid #dce3ec;border-radius:12px}
h1{font-size:28px;line-height:1.4}h2{font-size:22px;border-top:1px solid #dce3ec;padding-top:24px;margin-top:32px}a{color:#245e9d}
code{background:#eef2f7;border-radius:3px;padding:2px 4px;overflow-wrap:anywhere}pre{background:#eef2f7;padding:16px;overflow:auto;white-space:pre-wrap}
.notice{background:#fff8e9;border-left:4px solid #c08c35;padding:12px 16px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}
th,td{border:1px solid #dce3ec;padding:9px;text-align:left;vertical-align:top;min-width:125px}th{background:#eef2f7}
footer{margin-top:30px;color:#536a83}@media(max-width:650px){main{margin:0;padding:16px;border:0}h1{font-size:24px}}
</style></head><body><main>
<p class="notice">这是操作指南，不是正在运行的工作台。本页不会执行命令、读取账户或自动联网。请在文件管理器中运行启动器，并保留终端窗口。</p>
''' + '\n'.join(blocks) + '''
<footer>本页由 scripts/build_user_guide.py 从 docs/FIRST_RUN.md 生成。更新正文后重新生成；不要单独修改本页。</footer>
</main></body></html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not TARGET.is_file() or TARGET.read_text(encoding='utf-8') != expected:
            print('START_HERE.html is stale; run python scripts/build_user_guide.py')
            return 1
    else:
        TARGET.write_text(expected, encoding='utf-8', newline='\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
