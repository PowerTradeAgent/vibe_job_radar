"""Public page CDP transport and owned-blank-tab boundary, without networking."""
from types import MethodType
from unittest import TestCase
from unittest.mock import Mock

import test_native_acquisition as fixture
from vibe_job_radar.guided.contracts import CrawlError
from vibe_job_radar.guided.native_browser import NativeBackend


class NativePublicSessionTests(TestCase):
    def setUp(self):
        self.helper = fixture.NativeControllerTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.b = self.helper.b
        self.b._command = 0
        self.b._adopting = set()
        self.b._native_user_agent = 'ActualBrowser/1 VibeJobRadar/0.1'

    def test_command_and_ack_use_public_page_session_not_root_relay(self):
        client, callback = Mock(), Mock()
        client.send.return_value = {'ok': True}
        self.b._page_sessions['session'] = client
        self.b._send = MethodType(NativeBackend._send, self.b)
        self.b._send('session', 'Fetch.enable', {'handleAuthRequests': True}, callback)
        client.send.assert_called_once_with('Fetch.enable', {'handleAuthRequests': True})
        callback.assert_called_once_with({'ok': True})
        self.b._cdp.send.assert_not_called()
        self.assertEqual(self.b._pending, {})

    def test_public_command_error_does_not_leave_stale_pending_entry(self):
        client = Mock()
        client.send.side_effect = RuntimeError('fixture')
        self.b._page_sessions['session'] = client
        self.b._send = MethodType(NativeBackend._send, self.b)
        with self.assertRaises(RuntimeError):
            self.b._send('session', 'Fetch.enable')
        self.assertEqual(self.b._pending, {})

    def test_temporary_blank_attachment_never_enables_fetch_or_auth(self):
        self.b._install_target('temporary', {'targetId': 'blank'})
        self.b._send.assert_called_once_with('temporary', 'Runtime.runIfWaitingForDebugger')

    def test_unsolicited_page_is_closed_before_any_protocol_install(self):
        self.b._install_target = Mock()
        self.b._attached({'sessionId': 'automatic', 'targetInfo': {
            'targetId':'popup', 'type':'page', 'url':'about:blank'}})
        self.b._cdp.send.assert_called_once_with('Target.closeTarget', {'targetId':'popup'})
        self.b._install_target.assert_not_called()

    def test_opener_page_cannot_steal_an_application_creation_slot(self):
        self.b._page_creation = 1
        self.b._install_target = Mock()
        self.b._attached({'sessionId': 'automatic', 'targetInfo': {
            'targetId':'popup', 'type':'page', 'url':'about:blank', 'openerId':'another-page'}})
        self.b._cdp.send.assert_called_once_with('Target.closeTarget', {'targetId':'popup'})
        self.b._install_target.assert_not_called()

    def test_nonblank_target_is_not_released_during_creation(self):
        self.b._page_creation = 1
        self.b._install_target = Mock()
        self.b._attached({'sessionId': 'automatic', 'targetInfo': {
            'targetId':'popup', 'type':'page', 'url':fixture.URL+'/apply'}})
        self.b._cdp.send.assert_called_once_with('Target.closeTarget', {'targetId':'popup'})
        self.b._install_target.assert_not_called()

    def test_page_binding_retires_temporary_session_before_fetch(self):
        self.b._page_creation = 1
        page, client, self.b.context = Mock(url='about:blank'), Mock(), Mock()
        self.b.context.new_cdp_session.return_value = client
        client.send.return_value = {'targetInfo':{'targetId':'frame'}}
        order=[]
        self.b._cdp.send.side_effect=lambda *a:order.append('detach-temporary')
        self.b._install_target=Mock(side_effect=lambda *a:order.append('install-public'))
        self.b._bind_page(page)
        self.assertEqual(order,['detach-temporary','install-public'])
        self.b._install_target.assert_called_once_with('page:frame',{'targetId':'frame'})
        self.assertIs(self.b._page_sessions['page:frame'],client)
        self.b._bind_page(page)
        self.b.context.new_cdp_session.assert_called_once_with(page)

    def test_page_not_admitted_by_browser_target_guard_is_closed(self):
        self.b._page_creation = 1
        page, client, self.b.context = Mock(url='about:blank'), Mock(), Mock()
        self.b.context.new_cdp_session.return_value = client
        client.send.return_value={'targetInfo':{'targetId':'not-admitted'}}
        self.b._bind_page(page)
        page.close.assert_called_once()
        self.assertEqual(self.b.error,'native_surface_unsupported')
        self.assertEqual(self.b._page_sessions,{})

    def test_page_creation_clears_slot_even_when_browser_fails(self):
        self.b.context=Mock()
        self.b.context.new_page.side_effect=RuntimeError('fixture')
        with self.assertRaises(RuntimeError):self.b._new_page()
        self.assertEqual(self.b._page_creation,0)

    def test_detached_page_releases_transport_and_page_references(self):
        page=Mock()
        self.b._page_sessions['session']=Mock()
        self.b._bound_pages[page]='session'
        self.b._detached({'sessionId':'session'})
        self.assertEqual(self.b._page_sessions,{})
        self.assertEqual(self.b._bound_pages,{})
