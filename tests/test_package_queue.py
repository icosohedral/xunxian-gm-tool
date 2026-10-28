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
import scripts.package_queue as queue_module
from scripts.package_queue import PackageQueueBuilder, validate_items
from scripts.ride_speed import prepare_changes


class QueueBuilderTests(unittest.TestCase):
    def test_weapon_magic_resistance_rejects_invalid_values(self):
        fields = [''] * 55
        fields[1], fields[2], fields[7], fields[18], fields[53] = '武器', '100', '10', '300', '-1'
        builder = object.__new__(PackageQueueBuilder)
        with self.assertRaisesRegex(ValueError, '非负整数'):
            builder._patch_weapon({'fields': fields}, {})
        with self.assertRaisesRegex(ValueError, '法术抗性必须是非负整数'):
            server._apply_custom_weapon(1, fields)

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

    def test_backpack_and_weapon_items_may_repeat(self):
        items = [{'id': str(i), 'kind': 'backpack', 'payload': {'item_id': 100 + i}}
                 for i in range(3)]
        items += [{'id': 'w1', 'kind': 'weapon', 'payload': {}},
                  {'id': 'w2', 'kind': 'weapon', 'payload': {}}]
        validate_items(items)
        with self.assertRaises(ValueError):
            validate_items(items + [items[0]])

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


class BackpackQueueTests(unittest.TestCase):
    """The backpack item only prepares scattered files and patches the two packages."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source_dir = self.root / 'sources'
        source_dir.mkdir()
        self.sources = {label: source_dir / (label + '.dpk') for label in ('客户端', '主包')}
        for label, path in self.sources.items():
            path.write_bytes(label.encode())
        self.files = {'item/item_mcht.txt': b'products', 'object/npc_mcht.txt': b'merchants'}

    def builder(self):
        return PackageQueueBuilder(self.sources, self.root,
            catalog_path=self.root, reference_client=self.sources['客户端'], provenance={},
            backpack={'files': lambda: dict(self.files)})

    def outputs(self):
        return {label: self.root / ('step-' + label + '.dpk') for label in self.sources}

    def test_missing_base_price_is_initialized_in_the_server_package(self):
        builder = self.builder()
        change = {'action': 'add', 'product_id': 70001, 'item_id': 500,
                  'merchants': [238], 'price': '2', 'unit': '金'}
        report = {'copper': 20000, 'previous_copper': 0}
        with patch.object(queue_module, 'backpack_items',
                          return_value={500: {'id': 500, 'name': '甲', 'price': ''}}), \
                patch.object(queue_module, 'prepare_backpack',
                             return_value=(dict(self.files), change)) as prepare, \
                patch.object(queue_module, 'initialize_item_price', return_value=report) as initialize, \
                patch.object(queue_module, 'backpack_product_entries',
                             return_value=[{'product_id': 70001, 'item_id': 500, 'name': '甲',
                                            'discount': '100', 'gold': True}]), \
                patch.object(queue_module, 'apply_client_shop',
                             return_value={'changed': True, 'added': [], 'prices': [],
                                           'missing_items': []}) as apply, \
                patch.object(queue_module, 'verify_client_shop') as verify:
            result = builder._patch_backpack(
                {'action': 'add', 'item_id': 500, 'ranges': ['VIP'], 'price': '2', 'unit': '金'},
                self.outputs())
        self.assertEqual(result['product_id'], 70001)
        self.assertEqual(builder.backpack_prices, {500: 20000})
        self.assertEqual(builder.backpack_price_change, report)
        self.assertEqual(initialize.call_args.args[3:], (500, '2', '金'))
        self.assertEqual(initialize.call_args.kwargs, {'expected_base_price': 0})
        self.assertEqual(prepare.call_args.kwargs['base_price'], 20000)
        self.assertEqual(apply.call_args.args[3], {500: 20000})
        verify.assert_called_once()

    def test_existing_base_price_is_reused_without_touching_the_package(self):
        builder = self.builder()
        change = {'action': 'add', 'product_id': 70002, 'item_id': 600,
                  'merchants': [283], 'price': '1', 'unit': '砖'}
        with patch.object(queue_module, 'backpack_items',
                          return_value={600: {'id': 600, 'name': '乙', 'price': '500000'}}), \
                patch.object(queue_module, 'prepare_backpack',
                             return_value=(dict(self.files), change)) as prepare, \
                patch.object(queue_module, 'initialize_item_price') as initialize, \
                patch.object(queue_module, 'backpack_product_entries',
                             return_value=[{'product_id': 70002, 'item_id': 600, 'name': '乙',
                                            'discount': '200', 'gold': True}]), \
                patch.object(queue_module, 'apply_client_shop',
                             return_value={'changed': False, 'added': [], 'prices': [],
                                           'missing_items': []}), \
                patch.object(queue_module, 'verify_client_shop') as verify:
            builder._patch_backpack(
                {'action': 'add', 'item_id': 600, 'ranges': ['SVIP'], 'price': '1', 'unit': '砖'},
                self.outputs())
        initialize.assert_not_called()
        verify.assert_not_called()
        self.assertEqual(prepare.call_args.kwargs['base_price'], 500000)
        self.assertEqual(builder.backpack_prices, {})

    def test_second_item_reuses_the_accumulated_scattered_files(self):
        builder = self.builder()
        first = {'action': 'add', 'product_id': 70001, 'item_id': 500, 'merchants': [238],
                 'price': '2', 'unit': '金'}
        second = {'action': 'add', 'product_id': 70002, 'item_id': 600, 'merchants': [238],
                  'price': '3', 'unit': '金'}
        merged = {'item/item_mcht.txt': b'products-2', 'object/npc_mcht.txt': b'merchants-2'}
        with patch.object(queue_module, 'backpack_items',
                          return_value={500: {'id': 500, 'name': '甲', 'price': '10000'},
                                        600: {'id': 600, 'name': '乙', 'price': '10000'}}), \
                patch.object(queue_module, 'prepare_backpack',
                             side_effect=[(dict(self.files), first), (merged, second)]) as prepare, \
                patch.object(queue_module, 'backpack_product_entries', return_value=[]), \
                patch.object(queue_module, 'apply_client_shop',
                             return_value={'changed': False, 'added': [], 'prices': [],
                                           'missing_items': []}), \
                patch.object(queue_module, 'verify_client_shop'):
            builder._patch_backpack(
                {'action': 'add', 'item_id': 500, 'ranges': ['VIP'], 'price': '2', 'unit': '金'},
                self.outputs())
            builder._patch_backpack(
                {'action': 'add', 'item_id': 600, 'ranges': ['VIP'], 'price': '3', 'unit': '金'},
                self.outputs())
        self.assertEqual(prepare.call_args_list[1].args[0], self.files)
        self.assertEqual(builder.backpack_files, merged)
        self.assertEqual([row['product_id'] for row in builder.backpack_changes], [70001, 70002])
        self.assertEqual(builder.backpack_before, self.files)


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
            backpack_changes = []
            backpack_prices = {}
            backpack_before = None
            backpack_files = None
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

    def test_backpack_changes_commit_scattered_files_after_packages(self):
        class Builder:
            summon_config = None
            provenance = {}
            backpack_changes = [{'action': 'add', 'product_id': 70001, 'item_id': 500,
                                 'merchants': [238], 'price': '2', 'unit': '金'}]
            backpack_prices = {500: 20000}
            backpack_before = {'item/item_mcht.txt': b'a', 'object/npc_mcht.txt': b'b'}
            backpack_files = {'item/item_mcht.txt': b'c', 'object/npc_mcht.txt': b'd'}
            def __init__(inner, sources, directory, **kwargs):
                inner.packages = {k: Path(directory) / (k + '.dpk') for k in sources}
                inner.results = []
            def build(inner, items):
                for label, path in inner.packages.items():
                    path.write_bytes(label.encode() + b'-merged')
                return inner.packages
        with patch.object(server, 'PackageQueueBuilder', Builder), \
                patch.object(server, '_backpack_client_ready', return_value=True), \
                patch.object(server, 'ssh', return_value=Mock()), \
                patch.object(server, '_publish_queue_packages', return_value={}), \
                patch.object(server, 'reload_backpack_remote',
                             return_value={'lines': [1, 9]}) as reload_remote:
            result = server.apply_package_queue(
                [{'id': 'a', 'kind': 'backpack', 'payload': {'item_id': 500}}])
        self.assertEqual(result['backpack']['changes'][0]['product_id'], 70001)
        self.assertEqual(result['warnings'], [])
        kwargs = reload_remote.call_args.kwargs
        self.assertTrue(kwargs['save_only'])
        self.assertNotIn('extra_tables', kwargs)
        self.assertNotIn('price_changed', kwargs)
        self.assertEqual(len(kwargs['source_sha256']), 64)

    def test_backpack_scattered_failure_keeps_the_queue_result_with_a_warning(self):
        class Builder:
            summon_config = None
            provenance = {}
            backpack_changes = [{'action': 'add', 'product_id': 70001, 'item_id': 500}]
            backpack_prices = {}
            backpack_before = {'item/item_mcht.txt': b'a', 'object/npc_mcht.txt': b'b'}
            backpack_files = {'item/item_mcht.txt': b'c', 'object/npc_mcht.txt': b'd'}
            def __init__(inner, sources, directory, **kwargs):
                inner.packages = {k: Path(directory) / (k + '.dpk') for k in sources}
                inner.results = []
            def build(inner, items):
                for label, path in inner.packages.items():
                    path.write_bytes(label.encode() + b'-merged')
                return inner.packages
        with patch.object(server, 'PackageQueueBuilder', Builder), \
                patch.object(server, '_backpack_client_ready', return_value=True), \
                patch.object(server, 'ssh', return_value=Mock()), \
                patch.object(server, '_publish_queue_packages', return_value={}), \
                patch.object(server, 'reload_backpack_remote',
                             side_effect=RuntimeError('merchant reload failed')):
            result = server.apply_package_queue(
                [{'id': 'a', 'kind': 'backpack', 'payload': {'item_id': 500}}])
        self.assertIsNone(result['backpack'])
        self.assertTrue(any('merchant reload failed' in row for row in result['warnings']))

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
