import base64
import copy
import hashlib
import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch

from scripts import skill_reload_agent as agent
from scripts.skill_reload import _rows


class ReloadTests(unittest.TestCase):
    def test_frames_accept_fragmentation_and_reject_invalid_headers(self):
        class Fragmented:
            def __init__(self, data):
                self.data = bytearray(data)
            def recv(self, count):
                result = bytes(self.data[:1])
                del self.data[:1]
                return result
        payload = b'\xfc\x01\0\0\0\0ReloadExcel:OK!\0'
        packet = b'\xaa\x55' + struct.pack('<I', len(payload)) + payload + b'\x55\xaa'
        self.assertEqual(agent.receive_frame(Fragmented(packet)), payload)
        for packet in (b'\0\0\x01\0\0\0', b'\xaa\x55\xff\xff\xff\xff', packet[:-1]):
            with self.assertRaises(RuntimeError):
                agent.receive_frame(Fragmented(packet))

    def test_native_client_matches_response_token_and_limits_commands(self):
        client = agent.NativeGMClient()
        client.connection = unittest.mock.Mock()
        with patch.object(agent.secrets, 'randbelow', return_value=123), \
                patch.object(agent, 'send_frame') as send, \
                patch.object(agent, 'receive_frame', side_effect=[
                    b'\xfc' + struct.pack('<I', 999) + b'\0unrelated\0',
                    b'\xfc' + struct.pack('<I', 124) + b'\0ReloadExcel:OK!\0']):
            self.assertEqual(client.command(9, 'reloadskill'), 'ReloadExcel:OK!')
            self.assertIn(b'gmcmd_2_gzs 9,reloadskill\0', send.call_args.args[1])
            with self.assertRaises(ValueError):
                client.command(1, 'shutdown')

    def test_empty_physical_header_is_not_lost(self):
        self.assertEqual(_rows(b'\r\n1\t2\r\n', keep_empty=True)[1:], [['1', '2']])

    def transaction(self, fail=False, second=False, scope='definitions'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base = root / 'runtime'
            base.mkdir()
            (base / 'mb.dpk').write_bytes(b'package')
            (root / 'state').mkdir()
            contents = b'header\r\n1\t10\r\n'
            files = [{'id': key, 'path': name, 'rows': 1, 'checks': [[0, 0, 1]],
                      'content': base64.b64encode(contents).decode(),
                      'sha256': hashlib.sha256(contents).hexdigest()} for key, name in agent.scoped_tables(scope).items()]
            bundle = {'source_path': str(base / 'mb.dpk'), 'source_sha256': agent.sha256(base / 'mb.dpk'), 'files': files, 'scope': scope}
            state = {str(line): {'pid': line, 'start': '100', 'tables': {
                str(key): {'rows': 1, 'mismatches': 0, 'digest': 'x'} for key in agent.scoped_tables(scope)}}
                     for line in (1, 9)}
            commands = []
            did_fail = False
            armed = not second
            class Runtime:
                def snapshot(self, specs):
                    return copy.deepcopy(state)
            class Client:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def command(self, line, command):
                    nonlocal did_fail
                    commands.append((line, command))
                    if command.startswith('help '):
                        return command[5:] + ' help'
                    if fail and armed and line == 9 and not did_fail:
                        did_fail = True
                        raise socket.timeout('模拟交易线超时')
                    return ('ReloadExcel:OK!' if command == 'reloadskill' else
                            'ReloadExcel: ' + command.split()[1] + ' OK!')
            with patch.object(agent, 'ROOT', root), patch.object(agent, 'BASE', base):
                if second:
                    agent.perform_reload(copy.deepcopy(bundle), Runtime(), Client)
                    commands.clear()
                    armed = True
                if fail:
                    with self.assertRaisesRegex(RuntimeError, '已恢复'):
                        agent.perform_reload(copy.deepcopy(bundle), Runtime(), Client)
                    plan = agent.command_plan(files, state)
                    expected = [(int(line), command) for line, items in plan.items() for command in items]
                    self.assertEqual(commands[-len(expected):], expected)
                else:
                    self.assertEqual(agent.perform_reload(bundle, Runtime(), Client)['tables'], len(files))
                    saved = json.loads((root / 'state/gmtool-skill-reload/current.json').read_text())
                    self.assertEqual(len(saved['active_content']), len(files))
                self.assertFalse(list((base / 'mb').rglob('*.txt')))
                self.assertEqual((base / 'mb.dpk').read_bytes(), b'package')

    def test_success_cleans_transient_files(self):
        self.transaction()

    def test_partial_reload_failure_restores_both_lines(self):
        self.transaction(fail=True)

    def test_repeated_reload_failure_restores_prior_active_tables(self):
        self.transaction(fail=True, second=True)

    def test_cross_career_loads_rules_without_reloadskill(self):
        self.transaction(scope='cross-career')

    def test_cross_career_partial_failure_rolls_back_rule_tables(self):
        self.transaction(fail=True, scope='cross-career')

    def test_summon_partial_failure_rolls_back_associated_tables_and_definitions(self):
        self.transaction(fail=True, second=True, scope='summon')

    def test_character_table_only_reloads_when_changed(self):
        files = [{'id': 82, 'rows': 1}]
        state = {'1': {'tables': {'82': {'rows': 1, 'mismatches': 0}}}}
        self.assertEqual(agent.command_plan(files, state), {'1': []})
        state['1']['tables']['82']['rows'] = 0
        self.assertEqual(agent.command_plan(files, state), {'1': ['reloadexcel 82']})

    def test_native_commands_cannot_reload_arbitrary_tables(self):
        self.assertTrue(agent.allowed_command('reloadexcel 211'))
        self.assertFalse(agent.allowed_command('reloadexcel 82; shutdown'))
        self.assertFalse(agent.allowed_command('reloadexcel 1'))
        self.assertFalse(agent.reload_succeeded('ReloadExcel: 210 OK!', 'reloadexcel 211'))


if __name__ == '__main__':
    unittest.main()
