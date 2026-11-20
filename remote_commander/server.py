"""Authenticated localhost-only HTTP API and web UI.

Put Tailscale Serve in front for private HTTPS access on the phone. Never use
Tailscale Funnel or expose the bare HTTP port to the public internet.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .core import Agent, AgentError
from .ollama import chat_with_ollama

MAX_BODY = 1024 * 1024
PENDING_TTL = 300


class PendingActions:
    def __init__(self):
        self._entries: dict[str, tuple[float, str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def create(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == 'write_file' and (not isinstance(args.get('content'), str) or len(args['content']) > 16000):
            raise AgentError('A IA pode propor arquivos de até 16.000 caracteres nesta beta. Edite arquivos maiores manualmente.')
        action_id = secrets.token_urlsafe(24)
        with self._lock:
            self._entries = {k: v for k, v in self._entries.items() if time.monotonic() - v[0] < PENDING_TTL}
            # Bound memory if an automated client repeatedly requests actions.
            if len(self._entries) >= 32:
                self._entries.pop(next(iter(self._entries)))
            self._entries[action_id] = (time.monotonic(), name, args)
        preview = {'id': action_id, 'name': name, 'path': args.get('path', ''),
                   'cwd': args.get('cwd', '.'), 'command': args.get('command', ''),
                   'content_preview': args.get('content', '')}
        return preview

    def consume(self, action_id: str) -> tuple[str, dict[str, Any]]:
        with self._lock:
            entry = self._entries.pop(action_id, None)
        if not entry or time.monotonic() - entry[0] >= PENDING_TTL:
            raise AgentError('Ação não encontrada ou expirada. Peça novamente à IA.')
        return entry[1], entry[2]


class Context:
    def __init__(self, agent: Agent, token: str):
        self.agent = agent
        self.token = token
        self.pending = PendingActions()


def make_handler(ctx: Context):
    static = Path(__file__).parent / 'static'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'RemoteCommander/0.1'
        sys_version = ''

        def log_message(self, fmt: str, *args):
            # Do not write potentially sensitive query paths or headers to stdout.
            pass

        def _headers(self, code: int, ctype: str, nbytes: int) -> None:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(nbytes))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                             "form-action 'none'; frame-ancestors 'none'")
            self.end_headers()

        def _json(self, code: int, body: dict[str, Any]) -> None:
            result = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self._headers(code, 'application/json; charset=utf-8', len(result))
            self.wfile.write(result)

        def _auth(self) -> bool:
            bearer = self.headers.get('Authorization', '')
            if not hmac.compare_digest(bearer, 'Bearer ' + ctx.token):
                self._json(401, {'error': 'Token inválido ou não fornecido.'})
                return False
            return True

        def _origin_ok(self) -> bool:
            origin = self.headers.get('Origin', '')
            if not origin:
                return True  # CLI clients must still provide bearer token.
            try:
                parsed = urlsplit(origin)
                if parsed.scheme not in {'https', 'http'} or parsed.netloc != self.headers.get('Host'):
                    raise ValueError('Origin inconsistente')
            except ValueError:
                self._json(403, {'error': 'Origem de requisição não autorizada.'})
                return False
            return True

        def _body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError as exc:
                raise AgentError('Content-Length inválido.') from exc
            if length <= 0 or length > MAX_BODY:
                raise AgentError('Corpo ausente ou maior que 1 MiB.')
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise AgentError('JSON inválido.') from exc
            if not isinstance(payload, dict):
                raise AgentError('A requisição deve ser um objeto JSON.')
            return payload

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path in {'/', '/app.js', '/app.css'}:
                filename = {'/': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}[route.path]
                types = {'index.html': 'text/html', 'app.js': 'text/javascript', 'app.css': 'text/css'}
                data = (static / filename).read_bytes()
                self._headers(200, types[filename] + '; charset=utf-8', len(data))
                self.wfile.write(data)
                return
            if not self._auth():
                return
            try:
                if route.path == '/api/status':
                    result = ctx.agent.status()
                elif route.path == '/api/files':
                    result = ctx.agent.list_files(parse_qs(route.query).get('path', ['.'])[0])
                elif route.path == '/api/file':
                    result = ctx.agent.read_file(parse_qs(route.query).get('path', [''])[0])
                else:
                    self._json(404, {'error': 'Rota não encontrada.'})
                    return
                self._json(200, result)
            except (AgentError, OSError) as exc:
                self._json(400, {'error': str(exc)})

        def do_POST(self):
            if not self._auth() or not self._origin_ok():
                return
            try:
                data = self._body()
                if self.path == '/api/file':
                    result = ctx.agent.write_file(data.get('path', ''), data.get('content', ''))
                elif self.path == '/api/command':
                    result = ctx.agent.run_command(data.get('command', ''), data.get('cwd', '.'))
                elif self.path == '/api/chat':
                    if not ctx.agent.settings.ollama_model:
                        raise AgentError('IA não configurada. Use --ollama-model NOME.')
                    result = chat_with_ollama(ctx.agent, ctx.pending, data)
                elif self.path == '/api/approve':
                    action_id = data.get('id', '')
                    if not isinstance(action_id, str) or len(action_id) > 128:
                        raise AgentError('ID de ação inválido.')
                    name, args = ctx.pending.consume(action_id)
                    if data.get('approved') is True:
                        result = {'approved': True, 'name': name, 'result': ctx.agent.call_tool(name, args)}
                    else:
                        result = {'approved': False, 'name': name, 'result': 'Ação cancelada.'}
                else:
                    self._json(404, {'error': 'Rota não encontrada.'})
                    return
                self._json(200, result)
            except (AgentError, OSError) as exc:
                self._json(400, {'error': str(exc)})
            except Exception:
                # Never leak internal traces, secrets or environment to clients.
                self._json(500, {'error': 'Erro interno; consulte o terminal local.'})

    return Handler


def serve(agent: Agent, token: str, port: int = 8765) -> None:
    ctx = Context(agent, token)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), make_handler(ctx))
    httpd.daemon_threads = True
    print(f'Remote Commander beta: http://127.0.0.1:{port}', flush=True)
    print('Escutando SOMENTE em 127.0.0.1; use Tailscale Serve para acesso remoto.', flush=True)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
