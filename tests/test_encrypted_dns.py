"""Bounded wire parsing and opt-in mapped-DNS repair. All upstream data artificial."""
import ipaddress
import json
import socket
import struct
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from vibe_job_radar.dns_wire import Answer, ResolutionError, query, parse_answer
from vibe_job_radar.encrypted_dns import PublicResolver, PROVIDER
from vibe_job_radar.network import SafeHTTP, FetchError, validate_public_url
from vibe_job_radar.network_policy import NetworkPolicy, use_policy
from vibe_job_radar.network_settings import read_settings
from vibe_job_radar.workspace import Workspace, InputError
from vibe_job_radar.guided.transport import PinnedTransport
from vibe_job_radar.guided.rate import RateLedger, Limits

HOST = 'jobs.example.org'
IP4 = '93.184.216.34'
IP6 = '2606:4700:4700::1111'


def encoded_name(name):
    return b''.join(bytes([len(v)])+v.encode() for v in name.split('.'))+b'\0'


def record(owner, kind, payload, ttl=60):
    return encoded_name(owner)+struct.pack('!HHIH', kind, 1, ttl, len(payload))+payload


def wire(host=HOST, kind=1, records=None, *, flags=0x8180, ns=b'', ns_count=0):
    if records is None:
        records=[record(host, kind, ipaddress.ip_address(IP4 if kind==1 else IP6).packed)]
    return struct.pack('!6H',0,flags,1,len(records),ns_count,0)+query(host,kind)[12:]+b''.join(records)+ns


def fake_answers(*ips):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in ips]


class WireTests(unittest.TestCase):
    def test_query_no_ecs_headers_credentials_or_client_id(self):
        raw=query(HOST,1)
        self.assertEqual(struct.unpack('!6H',raw[:12]),(0,256,1,0,0,0))
        self.assertNotIn(b'Cookie',raw)

    def test_valid_dual_families(self):
        self.assertEqual(parse_answer(wire(),HOST,1).addresses,(IP4,))
        self.assertEqual(parse_answer(wire(kind=28),HOST,28).addresses,(IP6,))

    def test_compressed_owner(self):
        tail=struct.pack('!HHIH',1,1,100,4)+ipaddress.ip_address(IP4).packed
        data=struct.pack('!6H',0,0x8180,1,1,0,0)+query(HOST,1)[12:]+b'\xc0\x0c'+tail
        self.assertEqual(parse_answer(data,HOST,1).addresses,(IP4,))

    def test_cname_ttl_and_age_bound(self):
        rr=[record(HOST,5,encoded_name('edge.example.org'),ttl=10),
            record('edge.example.org',1,ipaddress.ip_address(IP4).packed,ttl=80)]
        value=parse_answer(wire(records=rr),HOST,1,age=3)
        self.assertEqual(value.ttl,7);self.assertEqual(value.canonical,'edge.example.org')

    def test_unrelated_or_alias_owner_addresses_rejected(self):
        for rr in ([record('unrelated.example.org',1,ipaddress.ip_address(IP4).packed)],
                   [record(HOST,5,encoded_name('edge.example.org')),
                    record(HOST,1,ipaddress.ip_address(IP4).packed)]):
            with self.subTest(records=rr),self.assertRaises(ResolutionError):parse_answer(wire(records=rr),HOST,1)

    def test_cname_cycle_and_conflicting_answers_rejected(self):
        for rr in ([record(HOST,5,encoded_name('edge.example.org')),record('edge.example.org',5,encoded_name(HOST))],
                   [record(HOST,5,encoded_name('one.example.org')),record(HOST,5,encoded_name('two.example.org'))]):
            with self.subTest(records=rr),self.assertRaises(ResolutionError):parse_answer(wire(records=rr),HOST,1)

    def test_private_or_fake_response_is_not_filtered(self):
        for ip in ('127.0.0.1','10.0.0.1','198.18.0.2','169.254.169.254','::1','fc00::1'):
            kind=28 if ':' in ip else 1
            rr=[record(HOST,kind,ipaddress.ip_address(IP6 if kind==28 else IP4).packed),
                record(HOST,kind,ipaddress.ip_address(ip).packed)]
            with self.subTest(ip=ip),self.assertRaisesRegex(ResolutionError,'non_public_answer'):
                parse_answer(wire(kind=kind,records=rr),HOST,kind)

    def test_question_identifier_type_and_flags_checked(self):
        for raw in (b'\0\1'+wire()[2:],wire(host='wrong.example.org'),wire(kind=28),
                    wire(flags=0x8380),wire(flags=0x8190),wire(flags=0x0180),wire(flags=0x8980)):
            with self.subTest(data=raw),self.assertRaises(ResolutionError):parse_answer(raw,HOST,1)

    def test_bounds_and_trailing_data(self):
        raw=wire()
        for value in [raw[:i] for i in range(len(raw))]+[raw+b'x',b'x'*65536]:
            with self.subTest(length=len(value)),self.assertRaises(ResolutionError):parse_answer(value,HOST,1)

    def test_compression_cycle_rejected(self):
        data=struct.pack('!6H',0,0x8180,1,0,0,0)+b'\xc0\x0c'+b'\0\1\0\1'
        with self.assertRaises(ResolutionError):parse_answer(data,HOST,1)

    def test_nodata_negative_ttl_and_age(self):
        soa=encoded_name('ns.example.org')+encoded_name('hostmaster.example.org')+struct.pack('!5I',1,2,3,4,30)
        ns=record('example.org',6,soa,ttl=45)
        value=parse_answer(wire(kind=28,records=[],ns=ns,ns_count=1),HOST,28,age=2)
        self.assertEqual(value.addresses,());self.assertEqual(value.ttl,28)

    def test_nxdomain_and_refused_are_not_direct_fallback(self):
        for flags,code in ((0x8183,'name_not_found'),(0x8185,'refused')):
            with self.subTest(flags=flags),self.assertRaisesRegex(ResolutionError,code):
                parse_answer(wire(flags=flags,records=[]),HOST,1)

    def test_zero_ttl_not_increased(self):
        raw=wire(records=[record(HOST,1,ipaddress.ip_address(IP4).packed,ttl=0)])
        self.assertEqual(parse_answer(raw,HOST,1).ttl,0)
        with self.assertRaisesRegex(ResolutionError,'expired_answer'):
            parse_answer(wire(),HOST,1,age=100)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.0];self.r=PublicResolver(clock=lambda:self.now[0])
        self.p=NetworkPolicy(encrypted_dns=True,resolver=self.r)
        self.dns=patch('socket.getaddrinfo',return_value=fake_answers('198.18.0.42'));self.dns.start();self.addCleanup(self.dns.stop)
        self.exchange=patch.object(self.r,'_exchange',side_effect=lambda h,k,p,d,**kw:Answer((IP4 if k==1 else IP6,),60,h))
        self.ex=self.exchange.start();self.addCleanup(self.exchange.stop)

    def test_only_mapped_system_answers_trigger_opted_in_exchange(self):
        result=self.r.resolve(HOST,self.p)
        self.assertEqual(result.addresses,(IP4,IP6));self.assertEqual(result.source,PROVIDER)
        self.assertEqual(self.ex.call_count,2)

    def test_disabled_no_doh_and_no_target_connection(self):
        with self.assertRaisesRegex(ResolutionError,'non_public_address'):
            self.r.resolve(HOST,replace(self.p,encrypted_dns=False))
        self.ex.assert_not_called()

    def test_normal_public_system_dns_has_no_extra_query(self):
        with patch('socket.getaddrinfo',return_value=fake_answers(IP4)):
            self.assertEqual(self.r.resolve(HOST,self.p).source,'system_dns')
        self.ex.assert_not_called()

    def test_mixed_private_public_fake_and_empty_all_stop(self):
        for ips in ((),('198.18.0.1',IP4),('198.18.0.1','10.0.0.1'),('127.0.0.1',),('::1',)):
            with self.subTest(ips=ips),patch('socket.getaddrinfo',return_value=fake_answers(*ips)),self.assertRaises(ResolutionError):
                self.r.resolve(HOST,self.p)
        self.ex.assert_not_called()

    def test_dns_failure_does_not_invoke_alternate_provider(self):
        with patch('socket.getaddrinfo',side_effect=socket.gaierror),self.assertRaisesRegex(ResolutionError,'dns_error'):
            self.r.resolve(HOST,self.p)
        self.ex.assert_not_called()

    def test_private_literals_and_special_names_never_repaired(self):
        for host in ('198.18.0.1','10.0.0.1','printer.local','machine.internal','service.home.arpa'):
            with self.subTest(host=host),self.assertRaises(ResolutionError):self.r.resolve(host,self.p)
        self.ex.assert_not_called()

    def test_policy_errors_stop_before_system_or_encrypted_dns(self):
        with patch('socket.getaddrinfo') as dns,self.assertRaisesRegex(ResolutionError,'local_proxy_configuration_invalid'):
            self.r.resolve(HOST,replace(self.p,error='local_proxy_configuration_invalid'))
        dns.assert_not_called();self.ex.assert_not_called()

    def test_cache_reuse_expiry_and_policy_separation(self):
        self.r.resolve(HOST,self.p)
        self.assertTrue(self.r.resolve(HOST,self.p).cache_reused)
        self.assertEqual(self.ex.call_count,2)
        self.now[0]+=61;self.r.resolve(HOST,self.p);self.assertEqual(self.ex.call_count,4)
        self.r.resolve(HOST,replace(self.p,source='explicit_application'));self.assertEqual(self.ex.call_count,6)

    def test_revalidated_system_private_dns_not_hidden_by_cache(self):
        self.r.resolve(HOST,self.p)
        with patch('socket.getaddrinfo',return_value=fake_answers('10.0.0.1')),self.assertRaises(ResolutionError):
            self.r.resolve(HOST,self.p)
        self.assertEqual(self.ex.call_count,2)

    def test_failure_cooldown_and_no_partial_family_cache(self):
        self.ex.side_effect=[Answer((IP4,),60,HOST),ResolutionError('encrypted_dns_tls_failed')]
        with self.assertRaisesRegex(ResolutionError,'tls_failed'):self.r.resolve(HOST,self.p)
        # The cooldown must retain a hard TLS cause, not enable stale job-cache fallback.
        with self.assertRaisesRegex(ResolutionError,'tls_failed'):self.r.resolve(HOST,self.p)
        self.assertEqual(self.ex.call_count,2);self.assertEqual(self.r._cache,{})

    def test_clock_rollback_and_budget(self):
        self.r.resolve(HOST,self.p);self.now[0]-=1
        with self.assertRaisesRegex(ResolutionError,'clock_rollback'):self.r.resolve(HOST,self.p)
        self.now[0]=200;self.r._requests=[199]*60
        with self.assertRaisesRegex(ResolutionError,'budget'):self.r.resolve(HOST,self.p)

    def test_revocation_stops_cached_or_next_family(self):
        self.r.permission=lambda:True;self.r.resolve(HOST,self.p)
        self.r.permission=lambda:False
        with self.assertRaisesRegex(ResolutionError,'disabled'):self.r.resolve(HOST,self.p)
        self.assertEqual(self.ex.call_count,2)

    def test_cancellation_before_dns_and_during_family_loop(self):
        event=threading.Event();event.set()
        with self.assertRaisesRegex(ResolutionError,'paused'):self.r.resolve(HOST,self.p,cancelled=event)
        self.ex.assert_not_called()
        event.clear()
        def one(*args,**kw):event.set();return Answer((IP4,),60,HOST)
        self.ex.side_effect=one
        with self.assertRaisesRegex(ResolutionError,'paused'):self.r.resolve(HOST,self.p,cancelled=event)
        self.assertEqual(self.ex.call_count,1)

    def test_url_permission_checks_run_before_resolution(self):
        for url in ('https://other.example.org/jobs','http://'+HOST+'/jobs','https://'+HOST+':8443/jobs'):
            with self.subTest(url=url),self.assertRaises(FetchError):
                validate_public_url(url,{HOST},network_policy=self.p)
        self.ex.assert_not_called()

    def test_http_uses_verified_snapshot_not_fake_target(self):
        response=Mock();response.status=200;response.getheaders.return_value=[];response.read.return_value=b'{}'
        connection=Mock();connection.getresponse.return_value=response
        with patch('vibe_job_radar.network.PinnedHTTPSConnection',return_value=connection) as factory:
            self.assertEqual(SafeHTTP({HOST},interval=0,network_policy=self.p).json('https://'+HOST+'/jobs'),{})
        self.assertEqual(factory.call_args.args[1],(IP4,IP6))

    def test_browser_bridge_uses_same_snapshot(self):
        from types import SimpleNamespace
        from vibe_job_radar.guided.contracts import CrawlError
        with tempfile.TemporaryDirectory() as tmp:
            ledger=RateLedger(Path(tmp)/'rates.sqlite',Limits(request_interval=0))
            transport=PinnedTransport(SimpleNamespace(key='test',domains=(HOST,),resource_domains=()),ledger,threading.Event())
            conn=Mock();reply=conn.getresponse.return_value;reply.status=200;reply.getheaders.return_value=[];reply.read.return_value=b'body'
            with use_policy(self.p),patch('vibe_job_radar.guided.transport.PinnedHTTPSConnection',return_value=conn) as factory:
                self.assertEqual(transport.fetch('https://'+HOST+'/job').body,b'body')
            self.assertEqual(factory.call_args.args[1],(IP4,IP6))

    def test_blocked_http_source_does_not_resolve_again(self):
        client=SafeHTTP({HOST},network_policy=self.p);client.blocked_hosts.add(HOST)
        with self.assertRaisesRegex(FetchError,'host_circuit_open'):client.request('https://'+HOST+'/jobs')
        self.ex.assert_not_called()

    def test_parallel_lookup_deduplicates_and_cache_is_bounded(self):
        barrier=threading.Barrier(2);values=[]
        def run():
            barrier.wait(timeout=2);values.append(self.r.resolve(HOST,self.p))
        workers=[threading.Thread(target=run) for _ in range(2)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(timeout=3)
        self.assertEqual(len(values),2);self.assertEqual(self.ex.call_count,2)
        self.assertEqual(sum(x.cache_reused for x in values),1)

    def test_expiry_between_family_answers_is_not_usable(self):
        def answer(h,k,p,d,**kw):
            if k==28:self.now[0]+=2
            return Answer((IP4 if k==1 else IP6,),1,h)
        self.ex.side_effect=answer
        with self.assertRaisesRegex(ResolutionError,'expired_answer'):self.r.resolve(HOST,self.p)
        self.assertEqual(self.r._cache,{})

    def test_http_pacing_happens_before_encrypted_resolution(self):
        client=SafeHTTP({HOST},network_policy=self.p,interval=5)
        client.last_request[HOST]=100
        seen=[]
        response=Mock();response.status=200;response.getheaders.return_value=[];response.read.return_value=b'{}'
        connection=Mock();connection.getresponse.return_value=response
        def answer(h,k,p,d,**kw):seen.append(self.now[0]);return Answer((IP4 if k==1 else IP6,),2,h)
        self.ex.side_effect=answer
        with patch('vibe_job_radar.network.time.monotonic',side_effect=lambda:self.now[0]),patch('time.sleep',side_effect=lambda n:self.now.__setitem__(0,self.now[0]+n)),patch('vibe_job_radar.network.PinnedHTTPSConnection',return_value=connection):
            client.json('https://'+HOST+'/jobs')
        self.assertEqual(seen,[105,105])



class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Workspace(self.tmp.name)
        self.env=patch('urllib.request.getproxies',return_value={});self.env.start();self.addCleanup(self.env.stop)

    def change(self,mode='fake_ip_doh',revision=0):
        return self.w.network_preferences({'mode':mode,'consent':mode=='fake_ip_doh','revision':revision})

    def test_default_state_offline_no_consent_no_file(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError),patch('socket.create_connection',side_effect=AssertionError):
            self.assertEqual(self.w.network_state()['mode'],'system')
            self.assertFalse(self.w.network_policy().encrypted_dns)
        self.assertFalse((self.w.root/'network-preferences.json').exists())

    def test_persisted_consent_no_environment_modification(self):
        import os
        before=dict(os.environ);self.change()
        other=Workspace(self.tmp.name)
        self.assertTrue(other.network_policy().encrypted_dns);self.assertEqual(before,dict(os.environ))
        self.assertNotIn('Cookie',json.dumps(read_settings(other)))

    def test_reject_unknown_fields_endpoints_credentials_and_false_consent(self):
        base={'mode':'fake_ip_doh','revision':0,'consent':True}
        for extra in ({'endpoint':'https://evil.example/dns'},{'Cookie':'secret'},{'consent':False},{'mode':'anything'},{'revision':True}):
            with self.subTest(extra=extra),self.assertRaises(InputError):self.w.network_preferences({**base,**extra})
        self.assertFalse((self.w.root/'network-preferences.json').exists())

    def test_compare_revision_and_workspace_isolation(self):
        self.change()
        with self.assertRaises(InputError):self.change()
        with tempfile.TemporaryDirectory() as tmp:self.assertFalse(Workspace(tmp).network_policy().encrypted_dns)
        self.change('system',1);self.assertFalse(self.w.network_policy().encrypted_dns)

    def test_revocation_applies_to_prior_policy_without_resetting_routes(self):
        self.change();previous=self.w.network_policy()
        self.change('system',1)
        with patch('socket.getaddrinfo',return_value=fake_answers('198.18.0.1')),self.assertRaisesRegex(ResolutionError,'disabled'):
            previous.resolver.resolve(HOST,previous)
        self.assertTrue(previous.encrypted_dns)

    def test_corruption_cannot_enable_or_fallback_silently(self):
        path=self.w.root/'network-preferences.json';path.write_text('{broken',encoding='utf-8')
        with self.assertRaises(InputError):self.w.network_policy()
        self.assertTrue(self.w.doctor()['workspace_writable'])


class LocalCacheDNSFailureTests(unittest.TestCase):
    def exercise(self, code, allowed):
        import time
        from unittest.mock import Mock
        from test_local_public import payload, query as local_query
        from vibe_job_radar.local_public import LocalPublicDataClient
        with tempfile.TemporaryDirectory() as folder:
            now=[time.time()]; transport=Mock(); transport.json.return_value=payload()
            client=LocalPublicDataClient(Workspace(folder),transport=transport,clock=lambda:now[0])
            first=client.search(local_query(),consent=True);now[0]+=601
            transport.json.side_effect=FetchError(code)
            if allowed:
                result=client.search(local_query(),consent=True)
                self.assertTrue(result['stale']);self.assertTrue(result['cache_reused'])
                self.assertEqual(result['refresh_error'],code)
                self.assertEqual(result['observed_at'],first['observed_at'])
                self.assertEqual(result['response']['jobs'],first['response']['jobs'])
            else:
                with self.assertRaises(FetchError):client.search(local_query(),consent=True)

    def test_transient_dns_outage_can_show_explicit_stale_cache(self):
        for code in ('encrypted_dns_unavailable','encrypted_dns_timeout','encrypted_dns_cooldown','encrypted_dns_budget'):
            with self.subTest(code=code):self.exercise(code,True)

    def test_tls_permissions_and_invalid_answers_never_hidden_by_cache(self):
        for code in ('encrypted_dns_tls_failed','encrypted_dns_disabled','encrypted_dns_route_failed',
                     'encrypted_dns_non_public_answer','encrypted_dns_invalid_response','encrypted_dns_clock_rollback'):
            with self.subTest(code=code):self.exercise(code,False)
