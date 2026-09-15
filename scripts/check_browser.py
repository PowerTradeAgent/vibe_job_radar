"""Check the actual collector on a blank page; never access a recruiting website."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main() -> int:
    from vibe_job_radar.guided.browser_health import probe_browser
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true', help='Developer-only headless check; does not certify the visible collector.')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    result = probe_browser(headless=args.headless)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
