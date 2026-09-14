"""Produce an inspectable source candidate, never a formal release or bundled browser."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build(out: Path) -> Path:
    version=re.search(r'^version = "([^"]+)"', (ROOT/'pyproject.toml').read_text(), re.M).group(1)
    out.mkdir(parents=True,exist_ok=True)
    archive=out/f'vibe-job-radar-{version}-source.zip'
    tops={'README.md','README_CLI.md','START_HERE.html','LICENSE','pyproject.toml','start_windows.bat','start_macos.command','.gitattributes','.gitignore','.env.example'}
    prefixes={'src','scripts','tests','docs','examples','demo_output','.github'}
    hashes={}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob('*')):
            rel=p.relative_to(ROOT)
            if p.is_symlink() or not p.is_file() or '__pycache__' in rel.parts or p.suffix in {'.pyc','.zip','.log'}:
                continue
            if len(rel.parts)==1 and rel.name not in tops or len(rel.parts)>1 and rel.parts[0] not in prefixes:
                continue
            data=p.read_bytes();hashes[rel.as_posix()]=hashlib.sha256(data).hexdigest()
            z.writestr(f'vibe-job-radar-{version}/'+rel.as_posix(),data)
        z.writestr(f'vibe-job-radar-{version}/CANDIDATE.json',json.dumps({'version':version,'status':'release_candidate',
            'browser_bundled':False,'live_sites_certified':False,'files_sha256':hashes},indent=2))
    (out/'manifest.json').write_text(json.dumps({'file':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'version':version,'status':'release_candidate','files':len(hashes)},indent=2),encoding='utf-8')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=ROOT/'release-candidate')
    print(build(parser.parse_args().out))
