"""Opt-in Chromium native HTTP/TLS with a public-target opaque CONNECT guard.

CDP Fetch observes/authorizes *each* hop. Playwright Route alone intentionally
skips redirected requests and can synthesize preflight responses; this backend
therefore does not install a Playwright HTTP route, fetch or fulfill handler.
Only application-owned browser targets are used. Worker/OOPIF targets are stopped
before running until their complete request accounting is separately supported.
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
import json
import time
from urllib.parse import urljoin, urlsplit

from .browser import PlaywrightBackend
from .contracts import CrawlError
from .diagnostic_trace import notify, observe, traced
from .native_policy import NativeRobots, contract_for
from .native_tunnel import NativeTunnel
from .rate import RateLimit
from .transport import PinnedTransport
from ..network import USER_AGENT


@dataclass(frozen=True)
class BusinessObservation:
    epoch: int
    operation: str
    received_at: float
    payload: dict | list = field(repr=False)


class NativeControl(PinnedTransport):
    """Reuse only durable pacing/policy binding. Never replay an HTTP request."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rules = {}

    def fetch(self, *args, **kwargs):
        raise RuntimeError('native backend cannot replay HTTP')

    def ensure_robots(self, url):
        p = urlsplit(url); origin = 'https://' + p.netloc
        policy = self.rules.get(origin)
        if policy is None:
            raise CrawlError('robots_unavailable')
        if not policy.allowed(url):
            raise CrawlError('robots_denied')

    def install_robots(self, origin, response, content_type, body):
        rules = NativeRobots(response, content_type, body)
        self.ledger.set_publisher(self.adapter.key, origin, delay=rules.delay)
        for count, seconds in rules.windows:
            self.ledger.set_publisher(self.adapter.key, origin, delay=rules.delay,
                requests=count, seconds=seconds)
        self.rules[origin] = rules


class NativeBackend(PlaywrightBackend):
    def __init__(self, adapter, ledger, cancelled, progress=lambda *_: None, *,
                 headless=False, executable_path=None, channel=None):
        self.contract = contract_for(adapter)
        self.tunnel = self._cdp = None
        self._sessions, self._pending, self._requests, self._hops = {}, {}, {}, {}
        self._command = self._epoch = 0
        self._observations = deque(maxlen=20)
        self._observed_bytes = 0
        self._closing = self._halted = self._loading_robots = False
        self._robots_url = ''
        self._auth_attempts = set()
        self._adopting = set()
        self.native_counts = {'document':0, 'business':0, 'asset':0, 'robots':0, 'login':0,
                              'blocked':0, 'responses':0}
        super().__init__(adapter, ledger, cancelled, progress, headless=headless,
            executable_path=executable_path, channel=channel, transport_factory=NativeControl)
        # Native Chromium chooses its actual UA; add only the application token.
        user_agent = self.page.evaluate('navigator.userAgent')
        self.context.set_extra_http_headers({'User-Agent': user_agent + ' ' + USER_AGENT})
        self.startup_report['network_backend'] = 'native'

    def _launch_options(self, options):
        self.tunnel = NativeTunnel(self.contract.hosts, self.wire.network_policy, self.cancelled)
        return {**options, 'proxy': {'server': self.tunnel.endpoint},
                'args': [*options['args'], '--proxy-bypass-list=<-loopback>']}

    def _configure_context(self):
        self.context.route_web_socket('**/*', lambda ws: ws.close())
        self.context.on('page', self._bind_page)
        self._cdp = self.browser.new_browser_cdp_session()
        self._cdp.on('Target.attachedToTarget', self._attached)
        self._cdp.on('Target.receivedMessageFromTarget', self._received)
        self._cdp.on('Target.detachedFromTarget', self._detached)
        # Non-flattened sessions use only documented Target.sendMessageToTarget;
        # no Playwright private internals or remote-debugging TCP port.
        self._cdp.send('Target.setAutoAttach', {'autoAttach':True,
            'waitForDebuggerOnStart':True, 'flatten':True})

    def _send(self, session, method, params=None, callback=None):
        if self._closing:
            return
        self._command += 1
        ident = self._command
        if len(self._pending) >= 512:
            raise CrawlError('native_observation_limit')
        self._pending[ident] = (session, callback)
        try:
            self._cdp.send('Target.sendMessageToTarget', {'sessionId':session,
                'message':json.dumps({'id':ident, 'method':method, 'params':params or {}})})
        except Exception:
            self._pending.pop(ident, None)
            raise

    def _attached(self, event):
        if self._closing:
            return
        info, session = event['targetInfo'], event['sessionId']
        if info['type'] != 'page' or len(self._sessions) >= 8:
            self._cdp.send('Target.closeTarget', {'targetId':info['targetId']})
            return
        target = info['targetId']
        if target not in self._adopting:
            # Browser-level auto-attach requires flatten=True. Hold that target
            # while opening a documented non-flattened command session, then
            # release only the unused automatic attachment.
            self._adopting.add(target)
            try:
                result = self._cdp.send('Target.attachToTarget', {'targetId':target,'flatten':False})
                legacy = result['sessionId']
                if legacy not in self._sessions:
                    self._install_target(legacy, info)
                self._cdp.send('Target.detachFromTarget', {'sessionId':session})
            except Exception:
                self._fatal('native_protocol_error')
                self._cdp.send('Target.closeTarget', {'targetId':target})
            finally:
                self._adopting.discard(target)
            return
        self._install_target(session, info)

    def _install_target(self, session, info):
        self._sessions[session] = info['targetId']
        try:
            self._send(session, 'Network.enable', {'maxTotalBufferSize':5_000_000,'maxResourceBufferSize':1_000_000})
            self._send(session, 'Network.setCacheDisabled', {'cacheDisabled':True})
            self._send(session, 'Fetch.enable', {'patterns':[
                {'urlPattern':'*','requestStage':'Request'},
                {'urlPattern':'*','requestStage':'Response'}], 'handleAuthRequests':True})
            self._send(session, 'Target.setAutoAttach', {'autoAttach':True,
                'waitForDebuggerOnStart':True,'flatten':False})
            self._send(session, 'Runtime.runIfWaitingForDebugger')
        except Exception:
            self._fatal('native_protocol_error')
            self._cdp.send('Target.closeTarget', {'targetId':info['targetId']})

    def _detached(self, event):
        session = event['sessionId']
        self._sessions.pop(session, None)
        for key in [k for k,v in self._pending.items() if v[0] == session]:
            self._pending.pop(key, None)
        for key in [k for k in self._requests if k[0] == session]:
            self._requests.pop(key, None); self._hops.pop(key, None)

    def _received(self, event):
        if self._closing:
            return
        try:
            session = event['sessionId']
            message = json.loads(event['message'])
            if 'id' in message:
                entry = self._pending.pop(message['id'], None)
                if entry and entry[0] in self._sessions:
                    if 'error' in message:
                        # Never expose protocol errors containing raw URLs/bodies.
                        self._fatal('native_protocol_error')
                    elif entry[1]:
                        entry[1](message.get('result', {}))
                return
            method, data = message.get('method'), message.get('params', {})
            if method == 'Target.attachedToTarget':
                # Dedicated/shared workers and OOPIFs are unsupported surfaces.
                self._cdp.send('Target.closeTarget', {'targetId':data['targetInfo']['targetId']})
            elif method == 'Fetch.requestPaused':
                self._paused(session, data)
            elif method == 'Fetch.authRequired':
                self._authenticate(session, data)
            elif method == 'Network.dataReceived':
                record = self._requests.get((session,data['requestId']))
                if record:
                    record['size'] += data.get('dataLength',0)
                    if record['size'] > 5_000_000:
                        self._fatal('response_too_large')
                        self._send(session,'Page.stopLoading')
            elif method == 'Network.loadingFinished':
                self._finished(session, data)
            elif method == 'Network.loadingFailed':
                key=(session,data['requestId']); record=self._requests.pop(key,None)
                self._hops.pop(key,None)
                if record and record['role'] != 'asset' and not self.cancelled.is_set() and not self.error:
                    err = data.get('errorText','')
                    code = ('tls_verification_failed' if 'CERT_' in err else
                            'tls_handshake_failed' if 'SSL_' in err else
                            self.tunnel.last_error or 'network_error')
                    self._fatal(code)
        except Exception:
            self._fatal('native_protocol_error')

    def _fatal(self, code, error=None):
        if not self.error:
            self.error = code
            self.wait_error = error if isinstance(error,RateLimit) else None
        self._halted = True
        if code == 'native_protocol_error' and self._cdp:
            # A failed interception command must not leave a page running with
            # an unknown policy state. Close only this application's targets.
            for target in tuple(set(self._sessions.values())):
                try:
                    self._cdp.send('Target.closeTarget', {'targetId':target})
                except Exception:
                    pass

    def _authenticate(self, session, event):
        challenge = event['authChallenge']; key=(session,event['requestId'])
        origin = urlsplit(challenge.get('origin',''))
        expected = urlsplit(self.tunnel.endpoint)
        if (challenge.get('source') == 'Proxy' and origin.hostname == expected.hostname
                and origin.port == expected.port and key not in self._auth_attempts
                and len(self._auth_attempts) < 128
                and not self.cancelled.is_set() and not self._halted):
            self._auth_attempts.add(key)
            response = {'response':'ProvideCredentials','username':self.tunnel.username,'password':self.tunnel.password}
        else:
            response = {'response':'CancelAuth'}
            self._fatal('native_proxy_auth_failed' if challenge.get('source') == 'Proxy' else 'http_401')
        self._send(session, 'Fetch.continueWithAuth', {'requestId':event['requestId'],'authChallengeResponse':response})

    def _paused(self, session, event):
        request = event['request']; url = request['url']; kind=event.get('resourceType','Other')
        response = 'responseStatusCode' in event or 'responseErrorReason' in event
        resource = {'XHR':'xhr','Fetch':'fetch','Document':'document','Stylesheet':'stylesheet',
                    'Script':'script','Image':'image','Font':'font','Media':'media'}.get(kind,'other')
        with observe(getattr(self,'_diagnostics',None), 'http_request' if response else 'route',
                actor='browser', url=url, method=request['method'], resource=resource,
                impact='optional' if resource in {'script','stylesheet','image','font','media'} else 'required_by_backend'):
            try:
                if self.cancelled.is_set():
                    raise CrawlError('paused')
                if not getattr(self, 'policy_check', lambda: True)():
                    raise CrawlError('native_policy_changed')
                if self._halted:
                    raise self.wait_error or CrawlError(self.error or 'site_stopped')
                if response:
                    self._response_paused(session,event)
                else:
                    self._request_paused(session,event)
            except Exception as exc:
                code = getattr(exc,'code','native_protocol_error')
                notify(getattr(self,'_diagnostics',None),'mark',code=code)
                self.native_counts['blocked'] += 1
                # An unknown business request must not become a silent empty list.
                # Unknown optional assets are reported without poisoning the task.
                if kind in {'Document','Fetch','XHR'} or code not in {'native_operation_unreviewed','resource_domain_blocked'}:
                    self._fatal(code,exc)
                try:
                    self._send(session,'Fetch.failRequest',{'requestId':event['requestId'],'errorReason':'BlockedByClient'})
                except Exception:
                    self._fatal('native_protocol_error')

    def _request_paused(self, session, event):
        r=event['request']; url=r['url']; kind=event.get('resourceType','Other')
        p, _ = self.contract.target(url)
        if event.get('frameId') and event['frameId'] != self._sessions.get(session):
            raise CrawlError('native_surface_unsupported')
        robots = self._loading_robots and url == self._robots_url and kind == 'Document' and r['method']=='GET'
        if robots:
            role, operation='robots','robots'
        else:
            rule=self.contract.match(url,r['method'],kind,authentication=self.auth_mode)
            role,operation=rule.role,rule.key
            if role != 'asset':
                self.wire.ensure_robots(url)
        if len(r.get('postData','').encode('utf-8')) > 1_000_000:
            raise CrawlError('request_too_large')
        if kind == 'Document' and not robots:
            if self._pagination_page is not None:
                self._pagination_page=None
            else:
                self.wire.reserve('page')
        self.wire.reserve('request', origin='https://'+p.netloc)
        if self.cancelled.is_set():
            raise CrawlError('paused')
        key=(session,event.get('networkId',event['requestId']))
        if len(self._requests) >= 128 and key not in self._requests:
            raise CrawlError('native_observation_limit')
        self._requests[key]={'epoch':self._epoch,'operation':operation,'role':role,'size':0,
                             'url':url,'status':None, 'json':False}
        self.native_counts[role] += 1
        self._send(session,'Fetch.continueRequest',{'requestId':event['requestId']})

    def _response_paused(self, session, event):
        url=event['request']['url']; status=event.get('responseStatusCode',0)
        if 'responseErrorReason' in event:
            raise CrawlError('network_error')
        notify(getattr(self,'_diagnostics',None),'mark',status=status)
        key=(session,event.get('networkId',event['requestId'])); record=self._requests.get(key)
        if not record:
            raise CrawlError('native_unaccounted_response')
        headers={h['name'].lower():h['value'] for h in event.get('responseHeaders',[])}
        if status in {401,403,429} and (record['role']!='asset' or status==429):
            delay=self.wire._retry_seconds(headers.get('retry-after',''))
            self.wire.ledger.cool(self.adapter.key,delay)
            if status==429:
                raise RateLimit(delay,'http_429',next_allowed_at=self.wire.ledger.clock()+delay)
            raise CrawlError('http_'+str(status))
        if status >= 500 and record['role']!='asset':
            raise CrawlError('remote_server_error')
        if 300 <= status < 400:
            if record['role']=='robots' or status==304:
                raise CrawlError('robots_unavailable' if record['role']=='robots' else 'native_unaccounted_response')
            target=urljoin(url,headers.get('location',''))
            # Same-origin redirects keep native browser cookie/method semantics.
            # Cross-origin redirects need a site-specific credential contract.
            if urlsplit(target).netloc != urlsplit(url).netloc:
                raise CrawlError('redirect_requires_attention')
            self.contract.target(target)
            self._hops[key]=self._hops.get(key,0)+1
            if self._hops[key] > 5:
                raise CrawlError('redirect_requires_attention')
        size=headers.get('content-length','')
        if size and (not size.isdigit() or int(size)>5_000_000):
            raise CrawlError('response_too_large')
        record['status']=status
        record['json']=headers.get('content-type','').split(';')[0].strip().lower()=='application/json'
        self.native_counts['responses']+=1
        # No response byte/header reconstruction, decompression or Cookie parsing.
        self._send(session,'Fetch.continueResponse',{'requestId':event['requestId']})

    def _finished(self, session, event):
        key=(session,event['requestId']); record=self._requests.pop(key,None)
        self._hops.pop(key,None)
        if (not record or record['role']!='business' or not record['json']
                or record['epoch']!=self._epoch or record['status']!=200 or self._halted):
            return
        if record['size'] > 1_000_000:
            self._fatal('native_observation_limit'); return
        def store(result):
            if record['epoch']!=self._epoch or self._closing or self._halted:
                return
            data=result.get('body','')
            if len(data)>1_400_000:
                self._fatal('native_observation_limit'); return
            raw=base64.b64decode(data,validate=True) if result.get('base64Encoded') else data.encode('utf-8')
            if len(raw)>1_000_000 or self._observed_bytes+len(raw)>4_000_000 or len(self._observations)>=20:
                self._fatal('native_observation_limit'); return
            try:
                payload=json.loads(raw)
                if not isinstance(payload,(dict,list)):
                    raise ValueError()
            except (ValueError, RecursionError):
                self._fatal('native_business_response_invalid'); return
            self._observations.append(BusinessObservation(self._epoch,record['operation'],time.time(),payload))
            self._observed_bytes+=len(raw)
        self._send(session,'Network.getResponseBody',{'requestId':event['requestId']},store)

    def observations(self):
        """Private local payloads for a reviewed site adapter, never diagnostic API."""
        return tuple(self._observations)

    def _check_error(self):
        if not getattr(self, 'policy_check', lambda: True)():
            self._fatal('native_policy_changed')
        if self.error:
            raise self.wait_error or CrawlError(self.error)
        if self.cancelled.is_set():
            raise CrawlError('paused')

    def _load_robots(self):
        main=self.page
        for origin in self.contract.rule_origins:
            if origin in self.wire.rules:
                continue
            self._loading_robots=True; self._robots_url=origin+'/robots.txt'
            scratch=None
            try:
                scratch=self.context.new_page()
                response=scratch.goto(self._robots_url,wait_until='load',timeout=45000)
                self._check_error()
                if response is None:
                    raise CrawlError('robots_unavailable')
                raw=response.body()
                self.wire.install_robots(origin,response.status,response.header_value('content-type') or '',raw)
            except CrawlError:
                raise
            except Exception as exc:
                code = self.error or self.tunnel.last_error
                if not code and 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc):
                    code = 'native_administrator_blocked'
                raise CrawlError(code or 'robots_unavailable') from exc
            finally:
                if scratch:
                    scratch.close()
                self._loading_robots=False; self._robots_url=''; self.page=main

    @traced('navigation','browser',url=True)
    def open(self,url,*,authentication=False):
        self.adapter.accept_url(url)
        self.contract.match(url,'GET','Document',authentication=authentication)
        self.auth_mode=authentication; self.error=self.wait_error=None; self._halted=False
        self._epoch+=1; self._observations.clear(); self._observed_bytes=0
        self._load_robots()
        self.wire.ensure_robots(url)
        try:
            self.page.goto(url,wait_until='domcontentloaded',timeout=90000)
            self._settle()
            return self.snapshot()
        except CrawlError:
            raise
        except Exception as exc:
            raise self.wait_error or CrawlError(self.error or self.tunnel.last_error or 'page_not_ready') from exc

    def _settle(self):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            self._check_error()
            # Readiness is site content/response/challenge, not network-idle or a
            # fixed sleep. The existing parser still determines usable job data.
            text=self.page.locator('body').inner_text(timeout=1000)
            if self.adapter.challenged(text,self.page.url):
                raise CrawlError('manual_required')
            if self.auth_mode and text.strip():
                return
            ready = getattr(self.adapter, 'native_ready', None)
            if callable(ready) and ready(self.observations()):
                return
            snap=self.snapshot()
            try:
                if self.adapter.cards(snap):
                    return
            except CrawlError:
                try:
                    self.adapter.detail(snap); return
                except CrawlError:
                    pass
            self.page.wait_for_timeout(100)
        raise CrawlError('page_not_ready')

    def next_page(self):
        self._check_error()
        self._epoch += 1
        self._observations.clear()
        self._observed_bytes = 0
        return super().next_page()

    def collection_mode(self):
        super().collection_mode()
        if self.error is None:
            self._halted = False

    def pump(self):
        if not self._closing and not getattr(self, 'policy_check', lambda: True)():
            self._fatal('native_policy_changed')
        super().pump()

    def close(self):
        self._closing=True
        super().close()
        if self.tunnel:
            self.tunnel.close(); self.tunnel=None
        self._sessions.clear(); self._pending.clear(); self._requests.clear(); self._hops.clear()
        self._observations.clear(); self._auth_attempts.clear()
