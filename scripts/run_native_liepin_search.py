"""Explicit, local-only CORS search -> actual Liepin adapter -> report test.

Three artificial HTTPS hosts use a fresh isolated test CA and the existing
native tunnel. No real platform traffic, credentials or request replay.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import http.server
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from run_native_browser_acceptance import (ROOT, HOST, URL, NativeBackend, RateLedger,
    Limits, GuidedService, Registry, Store, NetworkPolicy, use_policy, Workspace,
    trust_fixture, RECORDED_BODY, RECORDED_TITLE, recorded_markup, recorded_posting)
from vibe_job_radar.guided.adapters import builtins
from vibe_job_radar.guided.native_policy import contract_for

API_HOST = 'api.' + HOST
CDN_HOST = 'static.' + HOST
PATH = '/api/com.liepin.searchfront4c.pc-search-job'
ASSET = '/fe-www-pc/v6/js/search-fixture.js'


class SearchFixture:
    def __init__(self, root):
        self.requests = []
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_): pass
            def send(self, content, mime='text/html; charset=utf-8', status=200):
                raw = content.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Access-Control-Allow-Origin', URL)
                self.send_header('Access-Control-Allow-Methods', 'POST')
                self.send_header('Access-Control-Allow-Headers', 'content-type,x-client-type')
                self.end_headers()
                try: self.wfile.write(raw)
                except (OSError, ssl.SSLError): pass
            def record(self):
                owner.requests.append({'method': self.command, 'host': self.headers.get('Host'),
                    'path': urlsplit(self.path).path, 'anonymous': not self.headers.get('Cookie'),
                    'no_credentials': not self.headers.get('Authorization') and not self.headers.get('Proxy-Authorization'),
                    'identified': 'VibeJobRadar/0.1' in self.headers.get('User-Agent', '')})
            def do_GET(self):
                self.record(); path = urlsplit(self.path).path
                if path == '/robots.txt': self.send('User-agent: *\nAllow: /\n', 'text/plain')
                elif path == '/zhaopin/':
                    # Intentionally no anchors: only the browser response can
                    # produce the candidate; a DOM-only implementation fails.
                    self.send('<!doctype html><meta charset="utf-8"><h1>合成搜索页</h1>'
                              '<div id="loaded"></div><script src="https://' + CDN_HOST + ASSET + '"></script>')
                elif path == ASSET:
                    self.send("""const key = new URL(location.href).searchParams.get('key');
fetch('https://""" + API_HOST + PATH + """', {
 method:'POST', headers:{'Content-Type':'application/json','X-Client-Type':'web'},
 body: JSON.stringify({data:{mainSearchPcConditionForm:{key,currentPage:0,pageSize:40}}})
}).then(r => r.json()).then(j => {document.querySelector('#loaded').textContent='response received';});""", 'application/javascript')
                elif path == '/job/123.shtml':
                    self.send(recorded_markup(recorded_posting(url=URL + path)))
                else: self.send('unknown', status=404)
            def do_OPTIONS(self):
                self.record()
                if urlsplit(self.path).path != PATH: self.send('unknown', status=405)
                else: self.send('', status=204)
            def do_POST(self):
                self.record()
                if self.path != PATH: self.send('unknown', status=405); return
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                form = json.loads(raw)['data']['mainSearchPcConditionForm']
                jobs = [] if form['key'] == '明确无结果' else [
                    {'job': {'jobId': 'internal-not-url', 'title': RECORDED_TITLE, 'link': URL + '/job/123.shtml'}}]
                self.send(json.dumps({'flag':1,'data':{'data':{'jobCardList':jobs},
                    'pagination':{'currentPage':0,'pageSize':40}}}), 'application/json')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(root / 'good.pem'))
        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True
            def get_request(self):
                conn, address = super().get_request(); conn.settimeout(5)
                try: return context.wrap_socket(conn, server_side=True), address
                except Exception: conn.close(); raise
        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.05}, daemon=True)
        self.thread.start()
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--controlled', action='store_true')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--channel', choices=['msedge'])
    parser.add_argument('--executable')
    args = parser.parse_args()
    if not args.controlled:
        print('No requests; use --controlled for the local artificial-source test.'); return
    out = ROOT / 'browser-acceptance/native'; out.mkdir(parents=True, exist_ok=True)
    result = {'success':False,'scope':'Three artificial TLS hosts, actual native backend and Liepin adapter; not live certification.', 'checks':[]}
    services = []; server = None
    try:
        with tempfile.TemporaryDirectory(prefix='radar-search-fixture-') as tmp, ExitStack() as cleanup:
            root = Path(tmp)
            cleanup.callback(lambda: [s.close() for s in services])
            with trust_fixture(root, (API_HOST, CDN_HOST)):
                server = SearchFixture(root)
                cleanup.callback(server.close)
                template = builtins().get('liepin')
                contract = contract_for(template)
                mapping = {'www.liepin.com':HOST, 'api-c.liepin.com':API_HOST,
                           'concat.lietou-static.com':CDN_HOST, 'image0.lietou-static.com':CDN_HOST}
                rules = tuple(replace(r, host=mapping[r.host], cors_origin=URL if r.cors_origin else '') for r in contract.rules)
                local_contract = replace(contract, hosts=(HOST,API_HOST,CDN_HOST), rules=rules)
                local = replace(template, domains=(HOST,), resource_domains=(HOST,),
                    search_base=URL+'/zhaopin/', login_url=URL+'/', native_contract=local_contract)
                real_dns, real_dial = socket.getaddrinfo, socket.create_connection
                def dns(host,*a,**kw):
                    if host in local_contract.hosts: return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
                    if host in ('127.0.0.1','localhost','::1'): return real_dns(host,*a,**kw)
                    raise AssertionError('external DNS attempted')
                def dial(address,*a,**kw):
                    if address == ('93.184.216.34',443): return real_dial(server.server.server_address,*a,**kw)
                    if address[0] in ('127.0.0.1','localhost','::1'): return real_dial(address,*a,**kw)
                    raise AssertionError('external connection attempted')
                def factory(a,l,c,p,**saved):
                    with use_policy(NetworkPolicy()):
                        return NativeBackend(a,l,c,p,headless=not args.headed,channel=args.channel,executable_path=args.executable,**saved)
                def wait(service):
                    until=time.monotonic()+45
                    while service.state()['busy']:
                        if time.monotonic()>until: raise TimeoutError('native search did not finish')
                        time.sleep(.05)
                    return service.state()['jobs'][0]
                with patch('socket.getaddrinfo', side_effect=dns), patch('socket.create_connection', side_effect=dial), patch.object(NetworkPolicy,'capture',return_value=NetworkPolicy()):
                    workspace = Workspace(root/'workspace')
                    service = GuidedService(workspace,registry=Registry([local]),
                        ledger=RateLedger(root/'rate.sqlite', Limits(page_interval=0,request_interval=0)), native_backend_factory=factory)
                    services.append(service)
                    query={'platform':'liepin','keyword':'时间序列','roles':['time_series'],'max_pages':1,'max_jobs':1,
                           'consent':True,'rights_note':'仅合成测试','backend':'native','native_consent':True,'diagnostics':True}
                    service.create(query); task=wait(service)
                    result['first_task_code'] = task['code']
                    if task['status'] != 'ready':
                        result['diagnostics'] = service.diagnostics({'id':task['id']})
                    assert task['status']=='ready' and len(task['cards'])==1, task.get('code')
                    assert any(r['method']=='OPTIONS' and r['host']==API_HOST for r in server.requests), 'preflight not observed at upstream'
                    assert any(r['method']=='POST' and r['host']==API_HOST for r in server.requests)
                    assert any(r['host']==CDN_HOST and r['path']==ASSET for r in server.requests)
                    result['checks'].append('native CDN script and cross-origin preflight/search POST supply a candidate without DOM links or login')
                    service.action({'id':task['id'],'action':'collect','selected':[task['cards'][0]['id']]}); task=wait(service)
                    assert task['status']=='completed' and task['outcome']['saved']==1,task.get('code')
                    with Store(workspace.db) as store:
                        records=store.records()
                        assert len(records)==1 and records[0].text==RECORDED_BODY and records[0].title==RECORDED_TITLE
                    assert workspace.report(task['report_id'])['manifest']['stats']['full_text_job_groups']==1
                    result['checks'].append('actual Liepin parser, Store and original report preserve the selected full JD')
                    previous_report=task['report_id']
                    service.create({**query,'keyword':'明确无结果'}); empty=wait(service)
                    assert empty['status']=='ready' and empty['code']=='no_matching_jobs' and not empty['cards'],empty.get('code')
                    assert workspace.report(previous_report)
                    result['checks'].append('valid empty response means no matches, not required login; previous report preserved')
                    assert all(r['anonymous'] and r['no_credentials'] and r['identified'] for r in server.requests)
                    assert not any('login' in r['path'] or 'apply' in r['path'] for r in server.requests)
                    result['checks'].append('anonymous read sends no login request or credentials; application identity retained')
                    service.close(); services.clear()
                    result['success']=True
    finally:
        for service in services: service.close()
        result['requests']=server.requests if server else []
        (out/'liepin-search-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True))

if __name__ == '__main__': main()
