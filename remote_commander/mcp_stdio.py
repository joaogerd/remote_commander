"""Dependency-free MCP stdio JSON-RPC adapter (2025-11-25).

WARNING: unlike the web AI assistant, an authorized local MCP client can invoke
opt-in mutating tools directly. Only connect clients you trust.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .core import Agent, AgentError, tool_schemas

PROTOCOL = '2025-11-25'


def handle(agent: Agent, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get('method')
    if not isinstance(method, str):
        return _error(request.get('id'), -32600, 'Invalid Request')
    if 'id' not in request:
        return None  # Client notification.
    rid = request['id']
    params = request.get('params') or {}
    if not isinstance(params, dict):
        return _error(rid, -32602, 'Invalid params')
    try:
        if method == 'initialize':
            result: Any = {
                'protocolVersion': PROTOCOL,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'remote-commander-beta', 'version': '0.1.0-beta'},
                'instructions': ('Tools are workspace-scoped for file access. '
                                 'write_file and run_command are opt-in and execute directly '
                                 'for the trusted MCP client without web approvals.')
            }
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': tool_schemas(agent.settings)}
        elif method == 'tools/call':
            name = params.get('name', '')
            permitted = {t['name'] for t in tool_schemas(agent.settings)}
            if name not in permitted:
                raise AgentError('Ferramenta não autorizada.')
            value = agent.call_tool(name, params.get('arguments') or {})
            result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
        else:
            return _error(rid, -32601, f'Method not found: {method}')
        return {'jsonrpc': '2.0', 'id': rid, 'result': result}
    except (AgentError, OSError) as exc:
        if method == 'tools/call':
            return {'jsonrpc': '2.0', 'id': rid,
                    'result': {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}}
        return _error(rid, -32602, str(exc))


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def run_stdio(agent: Agent) -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                response = _error(None, -32600, 'Invalid Request')
            else:
                response = handle(agent, request)
        except json.JSONDecodeError:
            response = _error(None, -32700, 'Parse error')
        except Exception:
            response = _error(None, -32603, 'Internal error')
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            sys.stdout.flush()
