import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import gm_server


class ReloadAPITests(unittest.TestCase):
    def test_endpoint_enforces_local_origin_and_json_then_runs_fixed_operation(self):
        server = gm_server.ThreadingHTTPServer(('127.0.0.1', 0), gm_server.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(gm_server, '_ensure_server_mb_cache', return_value=({'主包': Path('cached.dpk')}, {})), \
                    patch.object(gm_server, 'ssh', return_value='connection'), \
                    patch.object(gm_server, 'op_log'), \
                    patch.object(gm_server, 'run_skill_reload', return_value={'tables': 45, 'lines': [1, 9]}) as reload:
                for headers, body, expected in [
                    ({'Content-Type': 'application/json', 'Origin': 'http://untrusted.example'}, '{}', 403),
                    ({'Content-Type': 'text/plain'}, '{}', 415),
                    ({'Content-Type': 'application/json'}, '{"scope":"reloadexcel 1"}', 400),
                    ({'Content-Type': 'application/json'}, '{"scope":"cross-career"}', 200),
                ]:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                    try:
                        connection.request('POST', '/api/skills/reload', body, headers)
                        response = connection.getresponse()
                        result = json.loads(response.read())
                        self.assertEqual(response.status, expected)
                        if expected == 200:
                            self.assertEqual(result['data']['tables'], 45)
                        else:
                            reload.assert_not_called()
                    finally:
                        connection.close()
                reload.assert_called_once_with('connection', Path('cached.dpk'), scope='cross-career')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
