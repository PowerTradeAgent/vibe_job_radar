"""Build only from matching local evidence. Not a formal release or bundled browser."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vibe_job_radar.qualification import check_source, fingerprint, output_directory, require_local_evidence, source_files
from vibe_job_radar.utils import atomic_json


def build(out: Path, evidence_path: Path | None = None) -> Path:
    out = output_directory(ROOT, out)
    evidence_path = evidence_path or ROOT/'release-verification/result.json'
    if evidence_path.is_symlink() or evidence_path.stat().st_size > 10_000_000:
        raise ValueError('invalid evidence file')
    evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
    expected=require_local_evidence(ROOT,evidence)
    if 'src/vibe_job_radar/_version.py' not in expected['files']:
        raise ValueError('version source is absent from verified payload')
    version=check_source(ROOT)['version']
    out.mkdir(parents=True,exist_ok=True)
    if (out/'manifest.json').is_symlink():
        raise ValueError('candidate destination cannot be a symlink')
    # A unique temporary file prevents concurrent attempts sharing a .part file.
    with tempfile.NamedTemporaryFile(prefix='.candidate-',suffix='.part',dir=out,delete=False) as pending:
        part=Path(pending.name)
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
                'version':version,'status':'locally_verified_candidate_not_release','browser_bundled':False,
                'remote_ci':'separate_verification_required','live_sites_certified':False,
                'source':expected,'local_evidence':evidence},ensure_ascii=False,indent=2))
        if fingerprint(ROOT)!=expected:
            raise ValueError('source changed while packaging')
        archive_sha = hashlib.sha256(part.read_bytes()).hexdigest()
        archive=out/f'vibe-job-radar-{version}-{archive_sha[:16]}-source.zip'
        if archive.is_symlink():
            raise ValueError('candidate destination cannot be a symlink')
        part.replace(archive)
    finally:
        part.unlink(missing_ok=True)
    atomic_json(out/'manifest.json',{'file':archive.name,
        'sha256':archive_sha, 'version':version,
        'status':'locally_verified_candidate_not_release','source_sha256':expected['sha256'],
        'remote_ci':'separate_verification_required'})
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'release-candidate')
    parser.add_argument('--evidence',type=Path,default=ROOT/'release-verification/result.json')
    args=parser.parse_args()
    try:
        print(build(args.out,args.evidence))
    except (OSError,ValueError,KeyError) as exc:
        print(f'Candidate refused [{type(exc).__name__}]. Run python scripts/verify_candidate.py first.',file=sys.stderr)
        raise SystemExit(2)
