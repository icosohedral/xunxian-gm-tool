import io
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import gm_server as server
from scripts.package_queue import PackageQueueBuilder, validate_items
from scripts.ride_speed import prepare_changes


class QueueBuilderTests(unittest.TestCase):
    def test_later_item_reads_previous_changes_without_publishing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.dpk'
            source.write_bytes(b'original')
            stage = root / 'stage'
            stage.mkdir()
            builder = PackageQueueBuilder({'客户端': source}, stage, catalog_path=root,
                                          reference_client=source, provenance={})
            def modify(payload, outputs):
                for label, package in builder.packages.items():
                    outputs[label].write_bytes(package.read_bytes() + payload['suffix'].encode())
                return {}
            with patch.object(builder, '_patch_profession_skills', side_effect=modify), \
                    patch.object(builder, '_patch_summon', side_effect=modify):
                result = builder.build([
                    {'id': 'a', 'kind': 'profession-skills', 'payload': {'suffix': '-first'}},
                    {'id': 'b', 'kind': 'summon', 'payload': {'suffix': '-second'}},
                ])
            self.assertEqual(result['客户端'].read_bytes(), b'original-first-second')
            self.assertEqual(source.read_bytes(), b'original')

    def test_invalid_or_duplicate_queue_entries_are_rejected(self):
        for items in ([], [{'id': 'a', 'kind': 'unknown', 'payload': {}}],
                      [{'id': str(i), 'kind': 'summon', 'payload': {}} for i in range(2)]):
            with self.assertRaises(ValueError):
                validate_items(items)

    def test_riding_speed_and_cast_expand_only_their_own_shared_groups(self):
        rows = {i: {'run': 100, 'terrain': 'land', 'fly': False, 'cast': 'none',
                    'same_name_ids': [1, 2] if i < 3 else [3],
                    'shared_ids': [1, 3] if i != 2 else [2],
                    'connection_id': 10 if i != 2 else 20} for i in (1, 2, 3)}
        changes, connections = prepare_changes(rows, [
            {'id': 1, 'run': 150, 'terrain': 'both', 'fly': True, 'cast': 'all'}])
        self.assertEqual(changes[2], {'run': 150})
        self.assertEqual(changes[3], {'cast': 'all'})
        self.assertEqual(connections, {10: 'all'})


class QueueCommitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sources = {label: self.root / (label + '.dpk') for label in ('客户端', '主包', '动态包')}
        for label, path in self.sources.items():
            path.write_bytes(label.encode())
        self.signatures = {label: (path.stat().st_size, 0, server._file_sha256(path))
                           for label, path in self.sources.items() if label != '客户端'}
        self.items = [{'id': str(i), 'kind': 'weapon', 'payload': {}} for i in range(2)]
        self.patches = [
            patch.object(server, 'CACHE_DIR', str(self.root)),
            patch.object(server, 'CLIENT_WORK_PACKAGE_PATH', self.sources['客户端']),
            patch.object(server, 'SERVER_MB_CACHE_PATHS', {k: v for k, v in self.sources.items() if k != '客户端'}),
            patch.object(server, '_ensure_client_work_package', return_value=self.sources['客户端']),
            patch.object(server, '_configured_client_package_path', return_value=self.sources['客户端']),
            patch.object(server, '_ensure_server_mb_cache', return_value=(
                {k: v for k, v in self.sources.items() if k != '客户端'}, self.signatures)),
            patch.object(server, '_load_skill_limit_provenance', return_value={}),
            patch.object(server, 'op_log'),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def builder(self, fail=False):
        class Builder:
            summon_config = None
            provenance = {}
            def __init__(inner, sources, directory, **kwargs):
                inner.packages = {k: Path(directory) / (k + '.dpk') for k in sources}
                inner.results = [{'id': str(i), 'kind': 'weapon', 'weapon_id': 60000 + i} for i in range(2)]
            def build(inner, items):
                for label, path in inner.packages.items():
                    path.write_bytes(label.encode() + b'-merged')
                if fail:
                    raise ValueError('second item failed')
                return inner.packages
        return Builder

    def test_all_items_publish_once_then_update_each_local_package(self):
        def publish(outputs, paths, signatures):
            self.assertEqual(set(outputs), {'主包', '动态包'})
            for label, path in self.sources.items():
                self.assertEqual(path.read_bytes(), label.encode())
            return {'主包': 'backup-main', '动态包': 'backup-dynamic'}
        with patch.object(server, 'PackageQueueBuilder', self.builder()), \
                patch.object(server, '_publish_queue_packages', side_effect=publish) as publisher:
            result = server.apply_package_queue(self.items)
        publisher.assert_called_once()
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['client_package_ready'])
        for label, path in self.sources.items():
            self.assertEqual(path.read_bytes(), label.encode() + b'-merged')
        self.assertFalse(list(self.root.glob('package-queue-*')))

    def test_generation_or_server_publication_failure_leaves_local_sources_unchanged(self):
        for fail_build in (True, False):
            with self.subTest(fail_build=fail_build), \
                    patch.object(server, 'PackageQueueBuilder', self.builder(fail=fail_build)), \
                    patch.object(server, '_publish_queue_packages', side_effect=RuntimeError('sync failed')) as publish:
                with self.assertRaises((ValueError, RuntimeError)):
                    server.apply_package_queue(self.items)
                if fail_build:
                    publish.assert_not_called()
                else:
                    publish.assert_called_once()
                for label, path in self.sources.items():
                    self.assertEqual(path.read_bytes(), label.encode())
                self.assertFalse(list(self.root.glob('package-queue-*')))

    def test_remote_change_is_detected_before_upload(self):
        connection = Mock()
        with patch.object(server, 'ssh', return_value=connection), \
                patch.object(server, '_dungeon_remote_signatures', return_value={}):
            with self.assertRaises(ValueError):
                server._publish_queue_packages({'主包': self.sources['主包']}, {}, self.signatures)
        connection.open_sftp.assert_not_called()

    def test_local_failure_after_server_commit_returns_results_and_preserves_recovery(self):
        replace = os.replace
        def fail_client(source, target):
            if Path(target) == self.sources['客户端']:
                raise OSError('simulated local rename failure')
            return replace(source, target)
        with patch.object(server, 'PackageQueueBuilder', self.builder()), \
                patch.object(server, '_publish_queue_packages', return_value={}), \
                patch.object(server.os, 'replace', side_effect=fail_client):
            result = server.apply_package_queue(self.items)
        self.assertEqual(len(result['items']), 2)
        self.assertFalse(result['client_package_ready'])
        self.assertTrue(result['warnings'])
        recovery = list(self.root.glob('package-queue-recovery-*'))
        self.assertEqual(len(recovery), 1)
        self.assertEqual((recovery[0] / '客户端.dpk').read_bytes(), '客户端'.encode() + b'-merged')

    def test_final_packages_upload_once_each_and_hash_check_precedes_commit(self):
        connection = Mock()
        def execute(command, **kwargs):
            stdout, stderr = io.BytesIO(b''), io.BytesIO(b'')
            stdout.channel = Mock()
            stdout.channel.recv_exit_status.return_value = 0
            return None, stdout, stderr
        connection.exec_command.side_effect = execute
        outputs = {k: v for k, v in self.sources.items() if k != '客户端'}
        paths = {k: '/server/' + k + '.dpk' for k in outputs}
        with patch.object(server, 'ssh', return_value=connection), \
                patch.object(server, '_dungeon_remote_signatures', return_value=self.signatures):
            result = server._publish_queue_packages(outputs, paths, self.signatures)
        self.assertEqual(connection.open_sftp.return_value.put.call_count, 2)
        self.assertEqual(set(result), set(outputs))
        script = connection.exec_command.call_args_list[0].args[0]
        self.assertLess(script.index('sha256sum -c -'), script.index('committed=1'))


class QueueAPITests(unittest.TestCase):
    def test_batch_endpoint_requires_local_origin_and_json(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, 'apply_package_queue', return_value={'items': []}) as apply:
                for headers, expected in [
                    ({'Content-Type': 'application/json', 'Origin': 'http://untrusted.example'}, 403),
                    ({'Content-Type': 'text/plain'}, 415),
                    ({'Content-Type': 'application/json'}, 200),
                ]:
                    connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port)
                    try:
                        connection.request('POST', '/api/package-queue/apply', '{"items":[]}', headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        result = json.loads(response.read())
                        self.assertEqual(result['ok'], expected == 200)
                        if expected != 200:
                            apply.assert_not_called()
                    finally:
                        connection.close()
                apply.assert_called_once_with([])
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
