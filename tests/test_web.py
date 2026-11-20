import http.client
import json
import threading
import unittest

from prwatch.web import server as web

from .helpers import entry, set_registry
from prwatch.core import store as pwstore


class WebServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        set_registry([entry(1, title='<img src=x onerror="window.__xss=1">'), entry(2)])
        pwstore.LOG.write_text('2026-11-17 10:00:00 daemon started (pid 1)\n', encoding='utf-8')
        cls.server = web.make_server(0)
        cls.port = cls.server.server_address[1]
        cls.token = cls.server.token
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def connection(self):
        return http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)

    def request(self, method, path, body=None, headers=None, token=True, connection=None):
        own = connection is None
        connection = connection or self.connection()
        sent = {'X-Token': self.token} if token else {}
        sent.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body)
            sent['Content-Type'] = 'application/json'
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read().decode('utf-8')
        if own:
            connection.close()
        return result

    def test_the_server_only_listens_on_loopback(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_state_needs_the_token(self):
        self.assertEqual(self.request('GET', '/api/state', token=False)[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'X-Token': 'wrong'}, token=False)[0], 403)
        status, _, body = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual([e['pr'] for e in json.loads(body)['entries']], [1, 2])

    def test_the_page_and_its_assets_are_public_but_the_api_needs_the_token(self):
        self.assertEqual(self.request('GET', '/', token=False)[0], 200)  # a reload of the window has no token in its address
        self.assertEqual(self.request('GET', '/api/state', token=False)[0], 403)
        status, headers, body = self.request('GET', f'/?t={self.token}', token=False)
        self.assertEqual((status, headers['Content-Type']), (200, 'text/html; charset=utf-8'))
        self.assertIn('PR Watch', body)
        self.assertEqual(self.request('GET', '/static/js/app.js', token=False)[0], 200)
        self.assertEqual(self.request('GET', '/static/style.css', token=False)[0], 200)
        self.assertEqual(self.request('GET', '/favicon.ico', token=False)[0], 200)

    def test_only_the_files_of_the_page_are_served(self):
        for path in ('/static/js/app.js', '/static/js/core.js', '/static/style.css', '/static/icon.svg'):
            self.assertEqual(self.request('GET', path, token=False)[0], 200, path)
        for path in ('/static/../server.py', '/static/js/../../server.py', '/static/%2e%2e/server.py', '/static/js/missing.js', '/static/index.html', '/static/', '/server.py'):
            self.assertIn(self.request('GET', path, token=False)[0], (403, 404), path)

    def test_security_headers(self):
        _, headers, _ = self.request('GET', f'/?t={self.token}', token=False)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertNotIn("'unsafe-inline'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_rebinding_is_refused(self):
        self.assertEqual(self.request('GET', '/api/state', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/action', {'action': 'wake', 'repo': 'fake-repo', 'pr': 1}, headers={'Host': 'evil.example'})[0], 403)

    def test_another_web_page_cannot_act(self):
        status, _, _ = self.request('POST', '/api/action', {'action': 'wake', 'repo': 'fake-repo', 'pr': 1}, headers={'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/action', {'action': 'wake', 'repo': 'fake-repo', 'pr': 1}, token=False)
        self.assertEqual(status, 403)

    def test_a_confirmed_action_goes_through_and_an_unconfirmed_one_does_not(self):
        status, _, body = self.request('POST', '/api/action', {'action': 'unwatch', 'repo': 'fake-repo', 'pr': 2})
        self.assertEqual((status, json.loads(body)['message']), (200, 'Action non confirmée.'))
        status, _, body = self.request('POST', '/api/action', {'action': 'wake', 'repo': 'fake-repo', 'pr': 1}, headers={'Origin': f'http://127.0.0.1:{self.port}'})
        self.assertTrue(json.loads(body)['ok'])

    def test_malformed_bodies(self):
        connection = self.connection()
        connection.request('POST', '/api/action', body='"nope"', headers={'X-Token': self.token, 'Content-Type': 'application/json'})
        self.assertEqual(connection.getresponse().status, 400)
        connection.close()

    def test_a_refused_request_does_not_poison_the_persistent_connection(self):
        """The body of a refused POST must be consumed, or HTTP/1.1 keep-alive takes it for the next request."""
        connection = self.connection()
        status, _, _ = self.request('POST', '/api/action', {'action': 'wake', 'repo': 'fake-repo', 'pr': 1}, token=False, connection=connection)
        self.assertEqual(status, 403)
        status, headers, body = self.request('GET', '/api/ping', connection=connection)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ok'])
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        connection.close()

    def test_journal_and_report(self):
        status, _, body = self.request('GET', '/api/log?mode=all&search=')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['entries'][0]['kind'], 'system')
        status, _, body = self.request('GET', '/api/report?repo=fake-repo&pr=1')
        self.assertEqual((status, json.loads(body)['ok']), (200, True))
        self.assertFalse(json.loads(self.request('GET', '/api/report?repo=fake-repo&pr=abc')[2])['ok'])

    def test_unknown_paths(self):
        self.assertEqual(self.request('GET', '/api/nope')[0], 404)
        self.assertEqual(self.request('POST', '/api/nope', {})[0], 404)


if __name__ == '__main__':
    unittest.main()
