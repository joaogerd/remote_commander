import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from remote_commander.core import Agent, Settings
from remote_commander.server import Context, make_handler


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / 'sample.txt').write_text('funciona', encoding='utf-8')
        cls.agent = Agent(Settings(cls.root, config_dir=cls.root / '.config'))
        cls.ctx = Context(cls.agent, 'TOKEN_DE_TESTE_UM_POUCO_MAIS_LONGO_QUE_32')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cls.ctx))
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)
        cls.tmp.cleanup()

    def req(self, path, token=None, method='GET', body=None, origin=None):
        headers = {}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if origin:
            headers['Origin'] = origin
        data = json.dumps(body).encode() if body is not None else None
        if data is not None: headers['Content-Type'] = 'application/json'
        request = Request(f'http://127.0.0.1:{self.port}{path}', data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                output = response.read()
                return response.status, json.loads(output) if path.startswith('/api') else output
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_static_dashboard(self):
        code, output = self.req('/')
        self.assertEqual(code, 200)
        self.assertIn(b'Remote Commander', output)

    def test_auth_required(self):
        status, _ = self.req('/api/status')
        self.assertEqual(status, 401)
        status, _ = self.req('/api/status', 'bad')
        self.assertEqual(status, 401)
        status, result = self.req('/api/status', self.ctx.token)
        self.assertEqual(status, 200)
        self.assertFalse(result['execution_enabled'])

    def test_file_read_and_rejection(self):
        token = self.ctx.token
        status, body = self.req('/api/file?path=sample.txt', token)
        self.assertEqual(status, 200)
        self.assertEqual(body['content'], 'funciona')
        status, body = self.req('/api/file?path=..%2Fetc%2Fpasswd', token)
        self.assertEqual(status, 400)
        status, body = self.req('/api/file', token, 'POST', {'path': 'x', 'content': 'abc'})
        self.assertEqual(status, 400)

    def test_cross_site_origin_rejected(self):
        status, _ = self.req('/api/command', self.ctx.token, 'POST',
                             {'command': 'pwd'}, origin='https://evil.example')
        self.assertEqual(status, 403)

    def test_disabled_execution(self):
        status, body = self.req('/api/command', self.ctx.token, 'POST', {'command': 'pwd'})
        self.assertEqual(status, 400)
        self.assertIn('desativada', body['error'])


if __name__ == '__main__':
    unittest.main()
