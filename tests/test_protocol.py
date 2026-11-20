import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_commander.core import Agent, AgentError, Settings
from remote_commander.mcp_stdio import PROTOCOL, handle
from remote_commander.server import PendingActions
from remote_commander.ollama import chat_with_ollama


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / 'hi.txt').write_text('texto', encoding='utf-8')
        self.agent = Agent(Settings(self.dir, config_dir=self.dir / '.cfg'))

    def req(self, method, params=None):
        return handle(self.agent, {'jsonrpc': '2.0', 'id': 3, 'method': method, 'params': params or {}})

    def test_mcp_initialize_and_list(self):
        init = self.req('initialize')['result']
        self.assertEqual(init['protocolVersion'], PROTOCOL)
        tools = self.req('tools/list')['result']['tools']
        self.assertEqual([t['name'] for t in tools], ['list_files', 'read_file', 'git_status'])

    def test_mcp_tools_call(self):
        response = self.req('tools/call', {'name': 'read_file', 'arguments': {'path': 'hi.txt'}})
        self.assertEqual(json.loads(response['result']['content'][0]['text'])['content'], 'texto')
        bad = self.req('tools/call', {'name': 'write_file', 'arguments': {'path': 'x', 'content': 'y'}})
        self.assertTrue(bad['result']['isError'])

    def test_mcp_protocol_errors(self):
        self.assertEqual(self.req('unknown')['error']['code'], -32601)
        self.assertIsNone(handle(self.agent, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        self.assertEqual(self.req('ping')['result'], {})

    def test_pending_is_single_use(self):
        p = PendingActions()
        item = p.create('run_command', {'command': 'pwd', 'cwd': '.'})
        self.assertNotIn('args', item)
        self.assertEqual(p.consume(item['id'])[0], 'run_command')
        with self.assertRaises(AgentError):
            p.consume(item['id'])

    def test_pending_rejects_unreviewable_large_ai_write(self):
        pending = PendingActions()
        with self.assertRaises(AgentError):
            pending.create('write_file', {'path': 'x', 'content': 'X' * 16001})

    def test_ollama_read_flow_and_pending_write(self):
        agent = Agent(Settings(self.dir, allow_write=True, ollama_model='mock-model', config_dir=self.dir / '.cfg'))
        pending = PendingActions()
        tool = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'read_file', 'arguments': {'path': 'hi.txt'}}}]}
        answer = {'role': 'assistant', 'content': 'O arquivo contém texto.'}
        with patch('remote_commander.ollama.call_model', side_effect=[tool, answer]) as mocked:
            result = chat_with_ollama(agent, pending, {'prompt': 'Leia hi.txt'})
        self.assertIn('contém texto', result['reply'])
        self.assertIsNone(result['pending'])
        self.assertEqual(mocked.call_count, 2)
        mutation = {'role': 'assistant', 'content': 'Vou alterar o arquivo.',
                    'tool_calls': [{'function': {'name': 'write_file', 'arguments': {'path': 'hi.txt', 'content': 'mudado'}}}]}
        with patch('remote_commander.ollama.call_model', return_value=mutation):
            result = chat_with_ollama(agent, pending, {'prompt': 'Altere hi.txt'})
        self.assertEqual(result['pending']['name'], 'write_file')
        self.assertEqual((self.dir / 'hi.txt').read_text(), 'texto')  # not executed
        name, args = pending.consume(result['pending']['id'])
        self.assertEqual(agent.call_tool(name, args)['saved'], True)
        self.assertEqual((self.dir / 'hi.txt').read_text(), 'mudado')


if __name__ == '__main__':
    unittest.main()
