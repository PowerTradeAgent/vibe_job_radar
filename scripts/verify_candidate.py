"""One local command to qualify the exact source before building a candidate.

No package installation, cloud mutation, site access or browser policy change.
This reports local checks only; remote CI and real-site acceptance stay separate.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vibe_job_radar.qualification import check_source, fingerprint
from vibe_job_radar.guided.browser_health import safe_text


def verify(out: Path) -> dict:
    out = out.resolve()
    forbidden = [ROOT/name for name in ('src','scripts','tests','docs','examples','demo_output','.github')]
    if any(out == p or p in out.parents for p in forbidden):
        raise ValueError('verification output must not be inside the source directories')
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema_version':1, 'kind':'local-candidate-verification',
              'created_at':datetime.now(timezone.utc).isoformat(), 'python':sys.version,
              'platform':platform.platform(), 'success':False, 'source_unchanged':False,
              'remote_ci':'not_verified_by_this_command', 'live_sites':'not_tested',
              'steps':[], 'tests':{}, 'scope':'Local source checks only, not signed attestation or release approval.'}
    # A previous successful run must not survive a missing report in this run.
    (out/'unit-tests.json').unlink(missing_ok=True)
    try:
        before = fingerprint(ROOT)
        report['source'] = before
        report['source_checks'] = check_source(ROOT)
        with tempfile.TemporaryDirectory(prefix='radar-qualification-') as temp:
            tasks = [('unit-tests',['scripts/run_tests.py','--report',str(out/'unit-tests.json')]),
                     ('user-guide',['scripts/build_user_guide.py','--check']),
                     ('offline-demo',['scripts/run_demo.py']),
                     ('source-doctor',['scripts/start_workbench.py','--doctor','--workspace',temp])]
            for name, args in tasks:
                print('Running:', name, flush=True)
                entry = {'name':name,'returncode':None}
                report['steps'].append(entry)
                try:
                    run = subprocess.run([sys.executable,*args], cwd=ROOT, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         timeout=180, shell=False)
                    entry['returncode'] = run.returncode
                    entry['output_tail'] = safe_text(run.stdout, 3000)
                except subprocess.TimeoutExpired:
                    entry.update(returncode=-1, error='local_check_timeout')
                    break
                if run.returncode:
                    break
        unit_file = out/'unit-tests.json'
        if unit_file.is_file():
            report['tests'] = json.loads(unit_file.read_text(encoding='utf-8'))
        report['source_unchanged'] = fingerprint(ROOT) == before
        report['success'] = (len(report['steps'])==4 and all(s['returncode']==0 for s in report['steps'])
                             and report['tests'].get('success') is True and report['tests'].get('tests_run',0)>0
                             and report['source_unchanged'])
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error'] = safe_text(exc)
    finally:
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'release-verification')
    args = parser.parse_args()
    result = verify(args.out)
    print(json.dumps({k:result[k] for k in ('success','source_unchanged','remote_ci','live_sites')},ensure_ascii=True))
    print('Evidence:', (args.out/'result.json').resolve())
    return 0 if result['success'] else 1


if __name__=='__main__':
    raise SystemExit(main())
