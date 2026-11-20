"""Core capabilities shared by the web UI, AI bridge and MCP stdio adapter.

Security boundary: file APIs stay inside a single selected workspace. Command
execution is NOT a sandbox and is opt-in; it has the privileges of the agent.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import time
import secrets
import shlex
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_READ_BYTES = 512 * 1024
MAX_WRITE_BYTES = 512 * 1024
MAX_OUTPUT = 48 * 1024
MAX_ENTRIES = 300
COMMAND_TIMEOUT = 20
BLOCKED_NAMES = {'.git', '.ssh', '.aws', '.gnupg', '.config', '.kube', '.netrc', '.npmrc',
                 '.pypirc', 'id_rsa', 'id_ed25519', 'credentials', 'secrets.json'}
BLOCKED_SUFFIXES = {'.pem', '.key', '.p12', '.pfx'}
EXECUTABLES = {'git', 'python', 'python3', 'pytest', 'rg', 'grep', 'ls', 'pwd',
               'find', 'head', 'tail', 'du', 'wc', 'make'}


class AgentError(ValueError):
    """Expected agent error, safe to show to the user."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def config_directory() -> Path:
    return Path(os.environ.get('RC_CONFIG_DIR', '~/.config/remote-commander')).expanduser()


def init_token(path: Path | None = None) -> tuple[Path, bool]:
    """Create a 256-bit token, owner-readable only; never rotate implicitly."""
    folder = path or config_directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink():
        raise AgentError('O diretório de configuração não pode ser um link simbólico.')
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    dest = folder / 'token'
    if dest.exists():
        if dest.is_symlink() or not dest.is_file():
            raise AgentError('Token inválido: arquivo simbólico ou não regular.')
        if stat.S_IMODE(dest.stat().st_mode) & 0o077:
            raise AgentError('Permissões inseguras no token. Use chmod 600 no arquivo.')
        return dest, False
    value = secrets.token_urlsafe(32)
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return init_token(folder)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        out.write(value + '\n')
    return dest, True


def load_token(path: Path | None = None) -> str:
    dest = (path or config_directory()) / 'token'
    if dest.is_symlink() or not dest.is_file():
        raise AgentError('Token não criado. Execute: python -m remote_commander init')
    if stat.S_IMODE(dest.stat().st_mode) & 0o077:
        raise AgentError('Permissões inseguras do token. Execute chmod 600 no arquivo.')
    token = dest.read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise AgentError('Token muito curto ou inválido.')
    return token


def bounded_process(argv: list[str], cwd: Path, timeout: int, env: dict[str, str]) -> dict[str, Any]:
    """Capture limited output and terminate the process group on timeout/overflow.

    Unlike subprocess.run(capture_output=True), this never buffers unbounded
    child output. It is not a sandbox for the child process.
    """
    try:
        child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env, start_new_session=True)
    except FileNotFoundError as exc:
        raise AgentError('Programa não instalado no sistema.') from exc
    chunks: dict[str, bytearray] = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = False
    start = time.monotonic()

    def stop() -> None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()

    try:
        with selectors.DefaultSelector() as sel:
            assert child.stdout is not None and child.stderr is not None
            sel.register(child.stdout, selectors.EVENT_READ, 'stdout')
            sel.register(child.stderr, selectors.EVENT_READ, 'stderr')
            while sel.get_map():
                left = timeout - (time.monotonic() - start)
                if left <= 0:
                    raise AgentError(f'Tempo máximo excedido ({timeout}s).')
                for key, _ in sel.select(timeout=min(left, 0.2)):
                    data = os.read(key.fileobj.fileno(), 4096)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    buffer = chunks[key.data]
                    allowed = max(0, MAX_OUTPUT - len(buffer))
                    buffer.extend(data[:allowed])
                    if len(data) > allowed:
                        truncated = True
                        stop()
                        break
                if truncated:
                    break
        if not truncated:
            left = timeout - (time.monotonic() - start)
            if left <= 0:
                raise AgentError(f'Tempo máximo excedido ({timeout}s).')
            try:
                child.wait(timeout=left)
            except subprocess.TimeoutExpired as exc:
                raise AgentError(f'Tempo máximo excedido ({timeout}s).') from exc
    except Exception:
        if child.poll() is None:
            stop()
        raise
    finally:
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()
    return {'exit_code': child.returncode,
            'stdout': chunks['stdout'].decode('utf-8', errors='replace'),
            'stderr': chunks['stderr'].decode('utf-8', errors='replace'),
            'truncated': truncated}


@dataclass(frozen=True)
class Settings:
    workspace: Path
    allow_write: bool = False
    allow_exec: bool = False
    ollama_model: str | None = None
    config_dir: Path | None = None

    def __post_init__(self):
        root = self.workspace.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise AgentError('O workspace precisa ser um diretório existente.')
        if root in (Path('/'), Path.home().resolve()):
            raise AgentError('Escolha uma pasta de projeto, não a raiz do sistema nem sua home.')
        object.__setattr__(self, 'workspace', root)
        object.__setattr__(self, 'config_dir', self.config_dir or config_directory())


class Agent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._audit_lock = threading.Lock()
        self._exec_sem = threading.BoundedSemaphore(value=2)

    def audit(self, action: str, target: str, success: bool) -> None:
        """No file contents, commands or secrets in audit entries."""
        try:
            folder = self.settings.config_dir
            assert folder is not None
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            entry = json.dumps({'utc': utc_now(), 'action': action,
                                'target': str(target)[:120], 'ok': success}, ensure_ascii=False)
            with self._audit_lock:
                with (folder / 'audit.jsonl').open('a', encoding='utf-8') as log:
                    log.write(entry + '\n')
                os.chmod(folder / 'audit.jsonl', 0o600)
        except OSError:
            pass

    def path(self, relative: str = '.', *, for_write: bool = False) -> Path:
        if not isinstance(relative, str) or '\x00' in relative or len(relative) > 1024:
            raise AgentError('Caminho inválido.')
        raw = Path(relative)
        if raw.is_absolute():
            raise AgentError('Use caminhos relativos ao workspace.')
        if any(part in BLOCKED_NAMES or part.startswith('.env') or
               Path(part).suffix.lower() in BLOCKED_SUFFIXES for part in raw.parts):
            raise AgentError('Arquivo de credenciais ou diretório interno bloqueado.')
        probe = self.settings.workspace
        for segment in raw.parts:
            probe = probe / segment
            if probe.is_symlink():
                raise AgentError('Links simbólicos não são permitidos nas rotas de arquivos.')
        candidate = (self.settings.workspace / raw).resolve(strict=False)
        if not candidate.is_relative_to(self.settings.workspace):
            raise AgentError('Acesso fora do workspace bloqueado.')
        resolved_rel = candidate.relative_to(self.settings.workspace)
        if any(part in BLOCKED_NAMES or part.startswith('.env') or
               Path(part).suffix.lower() in BLOCKED_SUFFIXES for part in resolved_rel.parts):
            raise AgentError('Arquivo de credenciais ou diretório interno bloqueado.')
        if for_write and candidate == self.settings.workspace:
            raise AgentError('Não é permitido sobrescrever o diretório raiz.')
        # Refuse symlinks for mutations, including a symlink at a leaf.
        if for_write and (self.settings.workspace / raw).is_symlink():
            raise AgentError('Escrita por link simbólico bloqueada.')
        return candidate

    def status(self) -> dict[str, Any]:
        return {'version': '0.1.0-beta', 'workspace': str(self.settings.workspace),
                'write_enabled': self.settings.allow_write,
                'execution_enabled': self.settings.allow_exec,
                'ai_enabled': bool(self.settings.ollama_model),
                'ai_model': self.settings.ollama_model,
                'security': 'Execução de comandos não é sandbox.'}

    def list_files(self, path: str = '.') -> dict[str, Any]:
        folder = self.path(path)
        if not folder.is_dir():
            raise AgentError('Diretório não encontrado.')
        data: list[dict[str, Any]] = []
        try:
            items = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
            for entry in items:
                if (entry.name in BLOCKED_NAMES or entry.name.startswith('.env') or
                    entry.suffix.lower() in BLOCKED_SUFFIXES):
                    continue
                try:
                    checked = self.path(str(entry.relative_to(self.settings.workspace)))
                    if entry.is_symlink() or not checked.is_relative_to(self.settings.workspace):
                        continue
                    data.append({'name': entry.name, 'path': str(entry.relative_to(self.settings.workspace)),
                                 'type': 'directory' if entry.is_dir() else 'file',
                                 'bytes': entry.stat().st_size if entry.is_file() else None})
                    if len(data) >= MAX_ENTRIES:
                        break
                except (OSError, AgentError):
                    continue
        except PermissionError as exc:
            raise AgentError('Sem permissão para listar este diretório.') from exc
        return {'path': path, 'items': data, 'truncated': len(items) > len(data)}

    def read_file(self, path: str) -> dict[str, Any]:
        file = self.path(path)
        if not file.is_file():
            raise AgentError('Arquivo não encontrado.')
        if file.stat().st_size > MAX_READ_BYTES:
            raise AgentError('Arquivo muito grande para leitura no painel (limite 512 KiB).')
        try:
            content = file.read_text(encoding='utf-8')
        except UnicodeError as exc:
            raise AgentError('Arquivo binário ou codificação não UTF-8.') from exc
        self.audit('read', path, True)
        return {'path': path, 'content': content, 'bytes': len(content.encode('utf-8'))}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        if not self.settings.allow_write:
            raise AgentError('Escrita desativada. Reinicie com --allow-write.')
        if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_WRITE_BYTES:
            raise AgentError('Conteúdo inválido ou maior que 512 KiB.')
        file = self.path(path, for_write=True)
        if not file.parent.is_dir():
            raise AgentError('Diretório pai não existe.')
        if file.exists() and not file.is_file():
            raise AgentError('O destino não é um arquivo regular.')
        fd, tmp = tempfile.mkstemp(prefix='.rc-', dir=file.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, stat.S_IMODE(file.stat().st_mode) if file.exists() else 0o600)
            os.replace(tmp, file)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self.audit('write', path, True)
        return {'path': path, 'bytes': len(content.encode('utf-8')), 'saved': True}

    def git_status(self, path: str = '.') -> dict[str, Any]:
        cwd = self.path(path)
        if not cwd.is_dir():
            raise AgentError('Diretório inválido para Git.')
        result = bounded_process(['git', '--no-pager', 'status', '--short', '--branch'], cwd, 8,
                                 {**os.environ, 'GIT_OPTIONAL_LOCKS': '0',
                                  'GIT_TERMINAL_PROMPT': '0'})
        return {'exit_code': result['exit_code'],
                'output': result['stdout'] + result['stderr'],
                'truncated': result['truncated']}

    def run_command(self, command: str, cwd: str = '.') -> dict[str, Any]:
        if not self.settings.allow_exec:
            raise AgentError('Execução desativada. Reinicie com --allow-exec.')
        if not isinstance(command, str) or len(command) > 2048:
            raise AgentError('Comando inválido ou extenso demais.')
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise AgentError('Sintaxe inválida no comando.') from exc
        if not argv or Path(argv[0]).name != argv[0] or argv[0] not in EXECUTABLES:
            raise AgentError('Programa não permitido na beta: ' + (argv[0] if argv else 'vazio'))
        target = self.path(cwd)
        if not target.is_dir():
            raise AgentError('O diretório de execução precisa existir.')
        # No shell is spawned. This DOES NOT isolate the program from the host OS.
        if not self._exec_sem.acquire(blocking=False):
            raise AgentError('Limite de dois comandos simultâneos atingido.')
        try:
            result = bounded_process(argv, target, COMMAND_TIMEOUT,
                                     {**os.environ, 'PAGER': 'cat', 'GIT_PAGER': 'cat',
                                      'GIT_TERMINAL_PROMPT': '0'})
            self.audit('command', argv[0], result['exit_code'] == 0)
            return result
        finally:
            self._exec_sem.release()

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise AgentError('Argumentos inválidos.')
        if name == 'list_files':
            return self.list_files(args.get('path', '.'))
        if name == 'read_file':
            return self.read_file(args.get('path', ''))
        if name == 'git_status':
            return self.git_status(args.get('path', '.'))
        if name == 'write_file':
            return self.write_file(args.get('path', ''), args.get('content', ''))
        if name == 'run_command':
            return self.run_command(args.get('command', ''), args.get('cwd', '.'))
        raise AgentError('Ferramenta desconhecida.')


def tool_schemas(settings: Settings, include_mutations: bool = True) -> list[dict[str, Any]]:
    base = [
        {'name': 'list_files', 'description': 'List items in a directory inside the approved workspace.',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'default': '.'}}}},
        {'name': 'read_file', 'description': 'Read a UTF-8 text file within the approved workspace.',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}},
        {'name': 'git_status', 'description': 'Show git repository status for a directory.',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'default': '.'}}}},
    ]
    if include_mutations and settings.allow_write:
        base.append({'name': 'write_file', 'description': 'Create or overwrite a UTF-8 file when writes are explicitly enabled.',
                     'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'},
                       'content': {'type': 'string'}}, 'required': ['path', 'content']}})
    if include_mutations and settings.allow_exec:
        base.append({'name': 'run_command', 'description': 'Run an allowlisted executable without a shell when execution is enabled.',
                     'inputSchema': {'type': 'object', 'properties': {'command': {'type': 'string'},
                       'cwd': {'type': 'string', 'default': '.'}}, 'required': ['command']}})
    return base
