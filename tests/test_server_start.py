import unittest
from unittest.mock import Mock, patch

import gm_server


class ServerStartTests(unittest.TestCase):
    def setUp(self):
        self.saved = gm_server._shop_restart_state
        gm_server._shop_restart_state = {'running': True, 'action': 'start'}

    def tearDown(self):
        gm_server._shop_restart_state = self.saved

    def test_start_invokes_only_start_script(self):
        stdout = Mock()
        stdout.read.return_value = b'ready'
        stdout.channel.recv_exit_status.return_value = 0
        stderr = Mock()
        stderr.read.return_value = b''
        connection = Mock()
        connection.exec_command.return_value = (None, stdout, stderr)
        with patch.object(gm_server, 'ssh', return_value=connection), patch.object(gm_server, 'op_log'):
            gm_server._server_action_worker('start', 'test')
        connection.exec_command.assert_called_once_with(
            'sudo -n runuser -u mc -- /opt/xxserver/start.sh', timeout=420)
        self.assertEqual(gm_server._shop_restart_state['phase'], '启动完成')
        self.assertFalse(gm_server._shop_restart_state['running'])
        self.assertEqual(gm_server._shop_restart_state['error'], '')

    def test_start_reports_failure(self):
        stdout = Mock()
        stdout.read.return_value = b''
        stdout.channel.recv_exit_status.return_value = 1
        stderr = Mock()
        stderr.read.return_value = b'start failed'
        connection = Mock()
        connection.exec_command.return_value = (None, stdout, stderr)
        with patch.object(gm_server, 'ssh', return_value=connection), patch.object(gm_server, 'op_log'):
            gm_server._server_action_worker('start', 'test')
        self.assertEqual(gm_server._shop_restart_state['phase'], '启动失败')
        self.assertIn('start failed', gm_server._shop_restart_state['error'])
        self.assertFalse(gm_server._shop_restart_state['running'])

    def test_action_rejects_concurrent_operation(self):
        with self.assertRaisesRegex(ValueError, '正在执行'):
            gm_server.start_server_action('start')

    def test_start_request_schedules_start_worker(self):
        gm_server._shop_restart_state = {'running': False}
        with patch.object(gm_server.threading, 'Thread') as thread:
            state = gm_server.start_server_action('start')
        self.assertTrue(state['running'])
        self.assertEqual(state['phase'], '准备启动')
        self.assertEqual(thread.call_args.kwargs['args'], ('start', '运维管理'))
        thread.return_value.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
