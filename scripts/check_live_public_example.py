"""Explicit live smoke test: one public GET; no credentials, no candidate submissions.

Only source metadata and counts are emitted. Third-party JD text stays in a
TemporaryDirectory and is not uploaded or committed as a fixture.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to permit exactly one documented public job GET.')
    from vibe_job_radar.public_example import PublicExample
    from vibe_job_radar.workspace import Workspace
    output = ROOT/'live-evidence'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        result = PublicExample(Workspace(tmp)).run({'consent': True})
        report = result.pop('report', None)
        if result['success']:
            assert result['text_characters'] >= 100
            assert result['stats']['full_text_job_groups'] == 1
            result['requirements_extracted'] = len(report['requirements'])
        (output/'public-example.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
