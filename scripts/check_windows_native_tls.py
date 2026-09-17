"""Real Windows CryptoAPI chain recovery through the production pinned transport.

Only ephemeral in-memory test roots are loaded in test contexts. Never write to
Windows trust stores. AIA and TLS servers are loopback-only; only fixture dialing
maps the public endpoint symbol to the local TLS server. No real account or JD.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import http.server
import importlib.metadata
import ipaddress
import json
import os
import socket
import ssl
import struct
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vibe_job_radar.tls_context import create_client_context, status
from vibe_job_radar.network import PinnedHTTPSConnection
from vibe_job_radar.network_policy import NetworkPolicy
from vibe_job_radar.encrypted_dns import PublicResolver
from vibe_job_radar.tls_diagnostic import failure_details

HOST = 'cloudflare-dns.com'
QUERY_HOST = 'jobs.example.org'


def main():
    if sys.platform != 'win32':
        raise RuntimeError('This acceptance check requires an actual Windows native chain engine.')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID, AuthorityInformationAccessOID
    out = ROOT/'native-tls-evidence'; out.mkdir(exist_ok=True)
    report = {'success':False, 'checks':[], 'scope':'Ephemeral certificates and local AIA/TLS services; actual Windows native verification and production connection. Not user-network certification.'}
    aia_hits=[]; requests=[]; names=[]; dials=[]
    now=dt.datetime.now(dt.timezone.utc)
    uid=uuid.uuid4().hex
    real_dial=socket.create_connection
    real_dns=socket.getaddrinfo
    try:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            folder=Path(tmp)
            class AIA(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args): pass
                def do_GET(self):
                    assert self.path=='/issuer.der'
                    aia_hits.append(self.path)
                    self.send_response(200);self.send_header('Content-Type','application/pkix-cert')
                    self.send_header('Content-Length',str(len(intermediate_der)));self.end_headers()
                    self.wfile.write(intermediate_der)
            aia=http.server.ThreadingHTTPServer(('127.0.0.1',0),AIA)
            thread=threading.Thread(target=aia.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
            stack.callback(lambda: (aia.shutdown(),aia.server_close(),thread.join(3)))
            aia_url=f'http://127.0.0.1:{aia.server_port}/issuer.der'
            def key(): return rsa.generate_private_key(public_exponent=65537,key_size=2048)
            root_key=key(); intermediate_key=key()
            def name(value): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,value)])
            root_name=name('Vibe ephemeral root '+uid); inter_name=name('Vibe ephemeral intermediate '+uid)
            def ca(subject,issuer,pub,signer,pathlen,serial):
                return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                        .public_key(pub).serial_number(serial).not_valid_before(now-dt.timedelta(days=1))
                        .not_valid_after(now+dt.timedelta(days=2))
                        .add_extension(x509.BasicConstraints(ca=True,path_length=pathlen),critical=True)
                        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True)
                        .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub),critical=False)
                        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()),critical=False)
                        .sign(signer,hashes.SHA256()))
            root=ca(root_name,root_name,root_key.public_key(),root_key,1,x509.random_serial_number())
            intermediate=ca(inter_name,root_name,intermediate_key.public_key(),root_key,0,x509.random_serial_number())
            intermediate_der=intermediate.public_bytes(serialization.Encoding.DER)
            root_path=folder/'root.pem';root_path.write_bytes(root.public_bytes(serialization.Encoding.PEM))
            def leaf(san=HOST,expired=False,client_only=False):
                private=key()
                cert=(x509.CertificateBuilder().subject_name(name(san)).issuer_name(inter_name)
                      .public_key(private.public_key()).serial_number(x509.random_serial_number())
                      .not_valid_before(now-dt.timedelta(days=2))
                      .not_valid_after(now-dt.timedelta(days=1) if expired else now+dt.timedelta(days=1))
                      .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
                      .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]),critical=False)
                      .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),critical=True)
                      .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH if client_only else ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
                      .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(intermediate_key.public_key()),critical=False)
                      .add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(AuthorityInformationAccessOID.CA_ISSUERS,x509.UniformResourceIdentifier(aia_url))]),critical=False)
                      .sign(intermediate_key,hashes.SHA256()))
                return private,cert
            class TLSHandler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args): pass
                def do_GET(self):
                    requests.append('GET')
                    payload=b'{"controlled_native_tls":true}'
                    self.send_response(200); self.send_header('Content-Length',str(len(payload)))
                    self.end_headers(); self.wfile.write(payload)
                def do_POST(self):
                    requests.append('POST')
                    assert self.path=='/dns-query'
                    raw=self.rfile.read(int(self.headers['Content-Length']))
                    kind=struct.unpack('!H',raw[-4:-2])[0]
                    ip=ipaddress.ip_address('93.184.216.34' if kind==1 else '2606:4700:4700::1111')
                    rr=b'\xc0\x0c'+struct.pack('!HHIH',kind,1,60,len(ip.packed))+ip.packed
                    payload=struct.pack('!6H',0,0x8180,1,1,0,0)+raw[12:]+rr
                    self.send_response(200); self.send_header('Content-Type','application/dns-message')
                    self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
            def server_for(cert,private,full_chain=False):
                identifier=uuid.uuid4().hex
                chain=cert.public_bytes(serialization.Encoding.PEM)
                if full_chain: chain+=intermediate.public_bytes(serialization.Encoding.PEM)
                pem=folder/(identifier+'.pem')
                pem.write_bytes(chain+private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
                server=http.server.ThreadingHTTPServer(('127.0.0.1',0),TLSHandler)
                server.daemon_threads=True
                ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(str(pem))
                ctx.set_servername_callback(lambda sock,host,context:names.append(host))
                server.socket=ctx.wrap_socket(server.socket,server_side=True)
                task=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);task.start()
                stack.callback(lambda:(server.shutdown(),server.server_close(),task.join(3)))
                return server
            private,cert=leaf();server=server_for(cert,private)
            def native(trust_root=True):
                ctx=create_client_context()
                assert ctx.radar_tls_engine=='windows_cryptoapi'
                assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED
                if trust_root: ctx.load_verify_locations(cafile=str(root_path))
                return ctx
            def attempt(target,context):
                def dial(address,timeout=None,source_address=None):
                    assert address==('1.1.1.1',443);dials.append(address)
                    return real_dial(target.server_address,timeout,source_address)
                with patch('vibe_job_radar.network.create_client_context',return_value=context),patch('socket.create_connection',side_effect=dial):
                    connection=PinnedHTTPSConnection(HOST,('1.1.1.1','1.0.0.1'),15,network_policy=NetworkPolicy())
                    try:
                        connection.request('GET','/fixture')
                        response=connection.getresponse();assert response.status==200
                        assert b'controlled_native_tls' in response.read()
                    finally: connection.close()
            with patch.dict(os.environ,{'SSL_CERT_FILE':'','SSL_CERT_DIR':''}):
                report['environment']=status()
                before=len(requests)
                try: attempt(server,ssl.create_default_context(cafile=str(root_path)))
                except ssl.SSLCertVerificationError as exc:
                    assert exc.verify_code==20
                    report['openssl_missing_intermediate_code']=exc.verify_code
                else: raise AssertionError('static OpenSSL context unexpectedly built a missing intermediate')
                assert len(requests)==before and not aia_hits
                report['checks'].append('OpenSSL root-only context reproduces verify_code 20 with leaf-only chain; no HTTP request is sent')
                attempt(server,native())
                assert aia_hits and len(requests)==before+1
                report['aia_requests']=len(aia_hits)
                report['checks'].append('production native factory and pinned connection succeed after Windows fetches the missing intermediate from controlled AIA; test root is context-local only')
                for label,leaf_args,trust_root in [('untrusted_root',{},False),('wrong_hostname',{'san':'other.example.org'},True),('expired',{'expired':True},True),('wrong_eku',{'client_only':True},True)]:
                    p,c=leaf(**leaf_args);target=server_for(c,p,True)
                    before=len(requests);before_dials=len(dials)
                    try: attempt(target,native(trust_root))
                    except ssl.SSLCertVerificationError as exc:
                        assert len(requests)==before and len(dials)==before_dials+1
                        detail=failure_details(exc,phase='tls_handshake')
                        assert detail['category']=='certificate_verification'
                    else: raise AssertionError(label+' incorrectly accepted')
                    report['checks'].append(label+' rejected before HTTP without route/validator/IP retry')
                # Exercise the unchanged DoH request and wire parsing, with a local
                # TLS fixture replacing only the dial and an explicit test root.
                def dial(address,timeout=None,source_address=None):
                    assert address==('1.1.1.1',443)
                    return real_dial(server.server_address,timeout,source_address)
                def dns(host,*a,**kw):
                    if host==QUERY_HOST:return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.1.244',443))]
                    return real_dns(host,*a,**kw)
                resolver=PublicResolver(permission=lambda:True)
                policy=NetworkPolicy(encrypted_dns=True,resolver=resolver)
                before=len(requests)
                with patch('vibe_job_radar.network.create_client_context',side_effect=native),patch('socket.create_connection',side_effect=dial),patch('socket.getaddrinfo',side_effect=dns):
                    result=resolver.resolve(QUERY_HOST,policy)
                assert result.addresses==('93.184.216.34','2606:4700:4700::1111')
                assert requests[before:]==['POST','POST'] and set(names)=={HOST}
                report['checks'].append('existing Fake-IP -> paired authenticated DoH -> public answer pipeline works with the native validator and original SNI; answers are synthetic')
                report['success']=True
    finally:
        (out/'chain-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
