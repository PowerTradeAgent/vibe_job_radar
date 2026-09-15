"""Get one genuine public job and keep its report. No API Key or browser SDK needed."""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def main(argv=None) -> int:
    from vibe_job_radar.public_example import PublicExample
    from vibe_job_radar.workspace import Workspace, InputError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.home()/'.vibe-job-radar')
    parser.add_argument('--yes', action='store_true', help='Explicitly authorize one public GET and local storage.')
    parser.add_argument('--no-browser', action='store_true', help='Keep the report without opening its local HTML file.')
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8', errors='replace')
    print('真实公开案例：Anthropic Technical Architect（不是BOSS数据，不是合成演示）。')
    print('仅请求官方公开职位接口，保存到本机。不需要账号/Key，不提交申请，不代表可公开转载原文。')
    if not args.yes:
        try:answer=input('是否获取？输入 y 后回车：').strip().lower()
        except EOFError:answer=''
        if answer not in {'y','yes'}:
            print('已取消，没有访问数据源。');return 0
    try:
        workspace=Workspace(args.workspace)
        result=PublicExample(workspace).run({'consent':True})
        result.pop('report',None)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['success']:return 2
        folder=workspace.root/'reports'/result['report_id']
        print(f'报告目录：{folder}\n逐条要求：{folder / "requirements_zh.csv"}')
        print('返回工作台刷新，可查看这份报告和新记录。个人经历和指标仍需自己的证据。')
        if not args.no_browser:
            try:webbrowser.open((folder/'dashboard.html').as_uri())
            except webbrowser.Error:print('请从上方目录手动打开 dashboard.html。')
        return 0
    except (InputError, OSError, ValueError) as exc:
        print(f'本次未完成 [{type(exc).__name__}]：{exc}',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
