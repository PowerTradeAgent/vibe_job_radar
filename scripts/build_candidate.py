"""Build only from matching local verification; never claim formal release approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vibe_job_radar.qualification import check_source, fingerprint, require_local_evidence, source_files


def build(out: Path, evidence_path: Path) -> Path:
    out = out.resolve()
    if out == ROOT.resolve() or any(out == (ROOT/name).resolve() or (ROOT/name).resolve() in out.parents
                                  for name in ('src','scripts','tests','docs','examples','demo_output','.github')):
        raise ValueError('candidate output must not be inside source directories')
    evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
    expected=require_local_evidence(ROOT,evidence)
    if 'src/vibe_job_radar/_version.py' not in expected['files']:
        raise ValueError('version source is absent from verified payload')
    version=check_source(ROOT)['version']
    out.mkdir(parents=True,exist_ok=True)
    archive=out/f'vibe-job-radar-{version}-source.zip'
    part=archive.with_suffix('.zip.part')
    try:
        with zipfile.ZipFile(part,'w',zipfile.ZIP_DEFLATED) as z:
            for name,path in source_files(ROOT).items():
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=expected['files'][name]:
                    raise ValueError('source changed while packaging')
                info=zipfile.ZipInfo(f'vibe-job-radar-{version}/'+name)
                info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=(0o100755 if name.endswith('.command') else 0o100644)<<16
                z.writestr(info,data)
            z.writestr(f'vibe-job-radar-{version}/CANDIDATE.json',json.dumps({
                'version':version,'status':'locally_verified_candidate_not_release',
                'remote_ci':'separate_verification_required','live_sites_certified':False,
                'source':expected,'local_evidence':evidence},ensure_ascii=False,indent=2))
        if fingerprint(ROOT)!=expected:
            raise ValueError('source changed while packaging')
        part.replace(archive)
    finally:
        part.unlink(missing_ok=True)
    (out/'manifest.json').write_text(json.dumps({'file':archive.name,
        'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(), 'version':version,
        'status':'locally_verified_candidate_not_release','source_sha256':expected['sha256'],
        'remote_ci':'separate_verification_required'},indent=2),encoding='utf-8')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'release-candidate')
    parser.add_argument('--evidence',type=Path,default=ROOT/'release-verification/result.json')
    args=parser.parse_args()
    try:
        print(build(args.out,args.evidence))
    except (OSError,ValueError,KeyError) as exc:
        print(f'Candidate refused: {exc}. Run python scripts/verify_candidate.py first.',file=sys.stderr)
        raise SystemExit(2)
