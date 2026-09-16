"""Explicit smoke: synthetic LOCAL Fake-IP + real DoH + one real public GET.

Never claim that this simulates a complete VPN product. No credentials, POST to
job sites, system DNS changes, full-text uploads or production resolver override.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import socket
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from vibe_job_radar.network import SafeHTTP
from vibe_job_radar.public_example import API_URL, parse_public_job
from vibe_job_radar.workspace import Workspace
from urllib.parse import urlsplit


def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');args=p.parse_args()
    if not args.live:
        print('No network request; pass --live to permit Cloudflare DNS and one fixed public GET.')
        return 0
    folder=ROOT/'live-evidence';folder.mkdir(exist_ok=True)
    output={'success':False,'simulated_local_fake_ip':True,'real_vpn_test':False,
            'scope':'Real opt-in resolver + fixed public endpoint; not a recruitment-platform certification.'}
    real=socket.getaddrinfo;host=urlsplit(API_URL).hostname
    def mapped(name,*a,**kw):
        if name==host:return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.42',443))]
        return real(name,*a,**kw)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Workspace(tmp)
            workspace.network_preferences({'mode':'fake_ip_doh','consent':True,'revision':0})
            client=SafeHTTP({host},timeout=15,max_bytes=500000,interval=2,network_policy=workspace.network_policy())
            with patch('socket.getaddrinfo',side_effect=mapped):payload=client.json(API_URL)
            record=parse_public_job(payload)
            output.update(success=True,source_url=API_URL,resolution=client.last_resolution,
                          text_characters=len(record.text),text_sha256=hashlib.sha256(record.text.encode()).hexdigest())
    except Exception as exc:
        output.update(code=getattr(exc,'code','smoke_failed'),error_type=type(exc).__name__)
    (folder/'encrypted-dns.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False))
    return 0 if output['success'] else 1

if __name__=='__main__':raise SystemExit(main())
