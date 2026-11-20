"""Start the server or the stdio MCP integration with `python -m remote_commander`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import Agent, AgentError, Settings, init_token, load_token


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='remote-commander', description='Remote Commander Beta 0.1.0')
    commands = p.add_subparsers(dest='action', required=True)
    commands.add_parser('init', help='Criar token secreto de acesso web')
    commands.add_parser('token', help='Mostrar o token no terminal local (não compartilhe)')
    for sub in ('serve', 'mcp'):
        option = commands.add_parser(sub, help='Iniciar painel web' if sub == 'serve' else 'Iniciar MCP via stdio')
        option.add_argument('--workspace', type=Path, required=True,
                            help='Diretório raiz autorizado; precisa existir')
        option.add_argument('--allow-write', action='store_true', help='Habilitar escrita de arquivos')
        option.add_argument('--allow-exec', action='store_true', help='Habilitar comandos (NÃO é sandbox)')
        if sub == 'serve':
            option.add_argument('--port', type=int, default=8765)
            option.add_argument('--ollama-model', default=None, help='Nome do modelo Ollama local')
    a = p.parse_args(argv)
    try:
        if a.action == 'init':
            path, created = init_token()
            print(('Novo token criado' if created else 'Token existente preservado') + f': {path}')
            print('Para exibir no computador local: python -m remote_commander token')
        elif a.action == 'token':
            print(load_token())
        else:
            settings = Settings(a.workspace, allow_write=a.allow_write, allow_exec=a.allow_exec,
                                ollama_model=getattr(a, 'ollama_model', None))
            agent = Agent(settings)
            if a.action == 'serve':
                if not (1 <= a.port <= 65535):
                    raise AgentError('Porta TCP inválida.')
                from .server import serve
                serve(agent, load_token(), port=a.port)
            else:
                from .mcp_stdio import run_stdio
                run_stdio(agent)
        return 0
    except (AgentError, OSError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
