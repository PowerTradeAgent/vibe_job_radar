import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from vibe_job_radar.workbench import Handler, LocalServer
from vibe_job_radar.workspace import Workspace

class ResponseLockTests(unittest.TestCase):
    def test_success_response_means_lock_is_released(self):
        with tempfile.TemporaryDirectory() as root:
            server = LocalServer(Workspace(root))
            worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.01}, daemon=True)
            observed = []
            original = Handler._json
            def checked(handler, code, data):
                if handler.command == 'POST' and code == 200:
                    observed.append(handler.server.mutation_lock.locked())
                return original(handler, code, data)
            worker.start()
            try:
                with patch.object(Handler, '_json', checked):
                    c = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=20)
                    c.request('POST', '/api/plan', body=json.dumps({'roles':['architect'],'platforms':['boss']}),
                              headers={'X-Radar-Token':server.token,'Content-Type':'application/json'})
                    response = c.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    c.close()
                self.assertEqual(observed, [False])
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
