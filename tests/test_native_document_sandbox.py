"""Document-only CSP restriction; never relax source CSP/CORS or login gates."""
import copy
import unittest

from vibe_job_radar.guided.native_documents import document_response_params, DOCUMENT_SANDBOX
import test_native_acquisition as fixtures


def event(**changes):
    value = {'requestId': 'r', 'resourceType': 'Document',
             'responseStatusCode': 200, 'responseStatusText': 'OK',
             'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html'}]}
    value.update(changes)
    return value


class DocumentPolicyTests(unittest.TestCase):
    def test_append_without_mutating_upstream(self):
        source = event(); before = copy.deepcopy(source)
        result = document_response_params(source, enabled=True)
        self.assertEqual(source, before)
        self.assertEqual(result['responseHeaders'][:-1], source['responseHeaders'])
        self.assertEqual(result['responseHeaders'][-1],
                         {'name': 'Content-Security-Policy', 'value': DOCUMENT_SANDBOX})
        self.assertEqual(result['responseCode'], 200)
        self.assertEqual(result['responsePhrase'], 'OK')
        self.assertNotIn('body', result)

    def test_existing_policies_remain_separate_and_unchanged(self):
        headers = [{'name': 'Content-Security-Policy', 'value': "default-src 'none'; sandbox"},
                   {'name': 'Content-Security-Policy', 'value': 'sandbox allow-popups'},
                   {'name': 'Content-Security-Policy-Report-Only', 'value': "script-src 'none'"}]
        result = document_response_params(event(responseHeaders=headers), enabled=True)
        self.assertEqual(result['responseHeaders'][:-1], headers)
        self.assertEqual(len(result['responseHeaders']), 4)

    def test_cookies_encoding_and_cors_headers_preserved(self):
        headers = [{'name': 'Set-Cookie', 'value': 'fixture_a=1; HttpOnly; Secure'},
                   {'name': 'set-cookie', 'value': 'fixture_b=2; SameSite=Lax'},
                   {'name': 'Content-Encoding', 'value': 'gzip'},
                   {'name': 'Access-Control-Allow-Origin', 'value': 'https://jobs.fixture.test'}]
        self.assertEqual(document_response_params(event(responseHeaders=headers), enabled=True)
                         ['responseHeaders'][:-1], headers)

    def test_cors_and_all_non_document_responses_untouched(self):
        for resource in ('Preflight', 'XHR', 'Fetch', 'Script', 'Stylesheet', 'Image', 'Other'):
            with self.subTest(resource=resource):
                self.assertEqual(document_response_params(event(resourceType=resource), enabled=True),
                                 {'requestId': 'r'})

    def test_non_cors_backend_unchanged(self):
        self.assertEqual(document_response_params(event(), enabled=False), {'requestId': 'r'})

    def test_redirects_not_reconstructed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertEqual(document_response_params(event(responseStatusCode=status), enabled=True),
                                 {'requestId': 'r'})

    def test_error_documents_also_sandboxed(self):
        self.assertIn('responseHeaders', document_response_params(event(responseStatusCode=404,
                                  responseStatusText='Not Found'), enabled=True))

    def test_missing_mime_cannot_evade_document_sandbox(self):
        self.assertIn('responseHeaders', document_response_params(event(responseHeaders=[]), enabled=True))

    def test_origin_and_forms_preserved_but_new_windows_not_enabled(self):
        self.assertEqual(DOCUMENT_SANDBOX.split(),
                         ['sandbox', 'allow-scripts', 'allow-same-origin', 'allow-forms'])
        self.assertNotIn('allow-popups', DOCUMENT_SANDBOX)
        self.assertNotIn('allow-top-navigation', DOCUMENT_SANDBOX)

    def test_browser_network_error_not_converted_into_success(self):
        self.assertEqual(document_response_params(event(responseErrorReason='Failed'), enabled=True),
                         {'requestId': 'r'})


class ControllerDocumentPolicyTests(unittest.TestCase):
    # Reuse setup helpers, not the base class's test collection.
    setUp = fixtures.NativeControllerTests.setUp
    req = fixtures.NativeControllerTests.req
    response = fixtures.NativeControllerTests.response

    def test_cors_document_runs_through_existing_response_validation(self):
        self.b._native_cors = True
        self.b._paused('session', self.req('/search', 'GET', 'Document'))
        self.b._paused('session', self.response(200, {'content-type': 'text/html'},
                                             path='/search', method='GET', kind='Document'))
        args = self.b._send.call_args.args
        self.assertEqual(args[1], 'Fetch.continueResponse')
        self.assertEqual(args[2]['responseHeaders'][-1]['value'], DOCUMENT_SANDBOX)
        self.assertIsNone(self.b.error)

    def test_source_denial_still_aborts_not_sandboxed_success(self):
        self.b._native_cors = True
        self.b._paused('session', self.req('/search', 'GET', 'Document'))
        self.b._paused('session', self.response(403, path='/search', method='GET', kind='Document'))
        self.assertEqual(self.b.error, 'http_403')
        self.assertEqual(self.b._send.call_args.args[1], 'Fetch.failRequest')

    def test_protocol_failure_must_not_fall_back_to_unprotected_response(self):
        self.b._native_cors = True
        self.b._paused('session', self.req('/search', 'GET', 'Document'))
        calls = []
        def send(session, method, params):
            calls.append(method)
            if method == 'Fetch.continueResponse':
                raise RuntimeError('unsupported document policy')
        self.b._send.side_effect = send
        self.b._paused('session', self.response(200, path='/search', method='GET', kind='Document'))
        self.assertEqual(self.b.error, 'native_protocol_error')
        self.assertEqual(calls, ['Fetch.continueResponse', 'Fetch.failRequest'])
