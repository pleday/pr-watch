#!/usr/bin/env python3
"""The web dashboard of PR Watch, for every system (Linux, macOS, Windows). Standard library only.

A small HTTP server bound to 127.0.0.1 shows the watched pull requests, their Claude session, the journal and the options,
and carries out the actions (pause, wake, merge, push...). It is a separate process from the daemon: `pr-watch ui` starts
it when needed and opens the page as an application window.

It is meant for the machine it runs on and protects itself accordingly: only loopback, a secret token (kept in web.json,
readable by the user only) required for the page and every call, the Host and Origin headers checked (against DNS
rebinding and other web pages), and everything a page shows is built as text, never as HTML.
"""
import hmac
import json
import os
import re
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from prwatch import actions as pwactions  # noqa: E402
from prwatch.core import journal as pwlog  # noqa: E402
from prwatch.core import store as pwstore
from prwatch.actions import pr as pwpr
from prwatch.system import host as pwhost

WEB_DIR = Path(__file__).resolve().parent / 'static'
WEB_FILE = pwstore.DIR / 'web.json'
WEB_LOCK = pwstore.DIR / 'web.lock'
PREFERRED_PORT = 8765
MAX_BODY = 1_000_000
STATIC_TYPES = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}
STATIC_PATH = re.compile(r'/static/((?:[\w-]+/)*[\w-]+\.(?:js|css|svg))')


def static_file(path):
    """(file, content type) of a file of the page for `path`, None when it is not one: only the files under `static/`."""
    if path == '/favicon.ico':
        path = '/static/icon.svg'
    match = STATIC_PATH.fullmatch(path)
    if not match:
        return None
    file = (WEB_DIR / match.group(1)).resolve()
    if WEB_DIR.resolve() not in file.parents or not file.is_file():
        return None
    return file, STATIC_TYPES[file.suffix]


class Handler(BaseHTTPRequestHandler):
    server_version = 'PRWatch'
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):  # noqa: A002 - the signature of the base class
        pass

    # ── protection ──────────────────────────────────────────────────────────────────────────────────────────────────

    def _host_ok(self):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]')
        return host in ('127.0.0.1', 'localhost', '::1')

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True
        host = urlparse(origin).hostname
        return host in ('127.0.0.1', 'localhost', '::1') and urlparse(origin).port == self.server.server_address[1]

    def _token_ok(self, query):
        supplied = self.headers.get('X-Token') or (query.get('t') or [''])[0]
        return hmac.compare_digest(supplied.encode(), self.server.token.encode())

    # ── answers ─────────────────────────────────────────────────────────────────────────────────────────────────────

    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._host_ok():
            return self._json(403, {'ok': False, 'message': 'Hôte refusé.'})
        served = static_file(url.path)
        if served:
            return self._send(200, served[0].read_bytes(), served[1])
        # The page holds nothing secret (the token is in the address of the window, then in its session): serving it without
        # the token lets a reload (F5, the browser menu) work; the API below still needs the token.
        if url.path in ('/', '/index.html'):
            return self._send(200, (WEB_DIR / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if not self._token_ok(query):
            return self._json(403, {'ok': False, 'message': 'Jeton absent ou invalide : ouvrez le tableau de bord avec `pr-watch ui`.'})
        if url.path == '/api/ping':
            return self._json(200, {'ok': True, 'pid': os.getpid()})
        if url.path == '/api/state':
            return self._json(200, pwactions.overview())
        if url.path == '/api/log':
            limit = (query.get('limit') or [''])[0]
            return self._json(200, pwactions.journal(
                (query.get('mode') or ['all'])[0],
                (query.get('search') or [''])[0].strip(),
                (query.get('pr') or [''])[0],
                int(limit) if limit.isdigit() else pwlog.PAGE,
            ))
        if url.path == '/api/report':
            repo, pr = (query.get('repo') or [''])[0], (query.get('pr') or [''])[0]
            return self._json(200, pwpr.report(repo, int(pr)) if pr.isdigit() else {'ok': False, 'message': 'Numéro invalide.'})
        return self._json(404, {'ok': False, 'message': 'Introuvable.'})

    def _read_body(self):
        """The request body, always consumed: an answer sent before reading it would leave it in the persistent
        connection, where it would be taken for the next request. Returns None (and closes the connection) when it is
        missing, too large or not a JSON object."""
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self.close_connection = True
            return None
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
        return request if isinstance(request, dict) else None

    def do_POST(self):
        url = urlparse(self.path)
        request = self._read_body()
        if not self._host_ok() or not self._origin_ok():
            return self._json(403, {'ok': False, 'message': 'Origine refusée.'})
        if not self._token_ok(parse_qs(url.query)):
            return self._json(403, {'ok': False, 'message': 'Jeton absent ou invalide.'})
        if url.path != '/api/action':
            return self._json(404, {'ok': False, 'message': 'Introuvable.'})
        if request is None:
            return self._json(400, {'ok': False, 'message': 'Requête invalide.'})
        return self._json(200, pwactions.perform(request))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A browser that closes its window mid-request resets the connection: nothing to report.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(port=None):
    """Binds the loopback address: the preferred port, else the next free one."""
    candidates = [port] if port is not None else [*range(PREFERRED_PORT, PREFERRED_PORT + 30), 0]
    for candidate in candidates:
        try:
            server = Server(('127.0.0.1', candidate), Handler)
            server.token = secrets.token_urlsafe(24)
            return server
        except OSError:
            continue
    raise SystemExit('no free port on 127.0.0.1')


def stable_token():
    """The secret of the dashboard, kept in a file only the user can read.

    It must survive a restart of the server: the address of the window (it holds the token) then stays the same, so the
    browser brings the window it already has to the front instead of opening a second one next to a window that no
    longer works.
    """
    file = pwstore.DIR / 'web.token'
    try:
        token = file.read_text(encoding='utf-8').strip()
        if len(token) >= 24:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(token)
    return token


def serve(port=None):
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    lock = open(WEB_LOCK, 'a+')
    if not pwhost.lock(lock, blocking=False):
        print('already running')
        return
    server = make_server(port)
    server.token = stable_token()
    pwhost.restart_when_code_changes()
    info = {'port': server.server_address[1], 'token': server.token, 'pid': os.getpid(), 'startedAt': time.strftime('%Y-%m-%d %H:%M:%S')}
    WEB_FILE.write_text(json.dumps(info), encoding='utf-8')
    try:
        os.chmod(WEB_FILE, 0o600)
    except OSError:
        pass
    pwstore.log(f"web dashboard on http://127.0.0.1:{info['port']}/ (pid {os.getpid()})")
    try:
        server.serve_forever()
    finally:
        try:
            WEB_FILE.unlink()
        except OSError:
            pass


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ['serve']:
        serve(int(arguments[1]) if len(arguments) > 1 else None)
    else:
        sys.exit('usage: python -m prwatch.web serve [port]   (start it with `prwatch ui`)')


if __name__ == '__main__':
    main()
