import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_commander.core import Agent, AgentError, Settings, init_token, load_token, tool_schemas


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'projects'
        self.root.mkdir()
        self.config = self.base / 'settings'
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'example.py').write_text('print("Olá")\n', encoding='utf-8')
        (self.root / '.env').write_text('PASSWORD=secret', encoding='utf-8')
        (self.root / '.git').mkdir()
        self.agent = Agent(Settings(self.root, config_dir=self.config))

    def test_default_permissions_are_read_only(self):
        self.assertFalse(self.agent.status()['write_enabled'])
        self.assertFalse(self.agent.status()['execution_enabled'])
        with self.assertRaises(AgentError):
            self.agent.write_file('hello.txt', 'abc')
        with self.assertRaises(AgentError):
            self.agent.run_command('pwd')
        self.assertEqual(len(tool_schemas(self.agent.settings)), 3)

    def test_safe_list_and_read(self):
        result = self.agent.list_files('.')
        names = [item['name'] for item in result['items']]
        self.assertIn('src', names)
        self.assertNotIn('.env', names)
        self.assertNotIn('.git', names)
        self.assertEqual(self.agent.read_file('src/example.py')['content'], 'print("Olá")\n')

    def test_blocks_traversal_and_absolute_paths(self):
        for p in ('../outside', '/etc/passwd', '.env', '.git/config', 'src/../../outside', 'src/../.env'):
            with self.subTest(p=p), self.assertRaises(AgentError):
                self.agent.read_file(p)

    def test_blocks_symlink_inside_and_outside(self):
        (self.root / 'outside').symlink_to(self.base)
        (self.root / 'alias').symlink_to(self.root / 'src')
        for p in ('outside/secret.txt', 'alias/example.py'):
            with self.subTest(p=p), self.assertRaises(AgentError):
                self.agent.path(p)
        self.assertNotIn('alias', [x['name'] for x in self.agent.list_files('.')['items']])

    def test_write_when_opted_in_and_preserves_mode(self):
        agent = Agent(Settings(self.root, allow_write=True, config_dir=self.config))
        result = agent.write_file('src/example.py', 'x=1\n')
        self.assertEqual(result['bytes'], 4)
        self.assertEqual((self.root / 'src' / 'example.py').read_text(), 'x=1\n')
        self.assertEqual(agent.write_file('new.txt', 'olá')['bytes'], 4)
        self.assertEqual(stat.S_IMODE((self.root / 'new.txt').stat().st_mode), 0o600)
        self.assertIn('"action": "write"', (self.config / 'audit.jsonl').read_text())

    def test_denies_secrets_and_large_file(self):
        agent = Agent(Settings(self.root, allow_write=True, config_dir=self.config))
        with self.assertRaises(AgentError):
            agent.write_file('secret.pem', 'data')
        with self.assertRaises(AgentError):
            agent.write_file('large.txt', 'A' * (512 * 1024 + 1))
        (self.root / 'big.txt').write_bytes(b'0' * (512 * 1024 + 1))
        with self.assertRaises(AgentError):
            agent.read_file('big.txt')

    def test_execution_opt_in_without_shell(self):
        agent = Agent(Settings(self.root, allow_exec=True, config_dir=self.config))
        response = agent.run_command('python3 -c "print(2+2)"')
        self.assertEqual(response['exit_code'], 0)
        self.assertEqual(response['stdout'].strip(), '4')
        for command in ('bash -c echo', 'sh -c whoami', '/bin/ls', 'rm -rf /'):
            with self.subTest(command=command), self.assertRaises(AgentError):
                agent.run_command(command)

    def test_command_output_is_bounded(self):
        agent = Agent(Settings(self.root, allow_exec=True, config_dir=self.config))
        response = agent.run_command("python3 -c 'print(200000 * chr(120))'")
        self.assertTrue(response['truncated'])
        self.assertLessEqual(len(response['stdout'].encode()), 48 * 1024)

    def test_home_or_system_root_workspace_rejected(self):
        with self.assertRaises(AgentError):
            Settings(Path('/'))
        with self.assertRaises(AgentError):
            Settings(Path.home())

    def test_tool_dispatch_rejects_unknown(self):
        with self.assertRaises(AgentError):
            self.agent.call_tool('unknown', {})
        with self.assertRaises(AgentError):
            self.agent.call_tool('read_file', [])

    def test_token_creation_and_security(self):
        dest, created = init_token(self.config)
        self.assertTrue(created)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertGreaterEqual(len(load_token(self.config)), 32)
        same, new = init_token(self.config)
        self.assertEqual(same, dest)
        self.assertFalse(new)
        os.chmod(dest, 0o644)
        with self.assertRaises(AgentError):
            load_token(self.config)


if __name__ == '__main__':
    unittest.main()
