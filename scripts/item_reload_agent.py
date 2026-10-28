"""Reload item table 41 without rolling back runtime tables or restarting."""


def validate_item_bundle(bundle):
    import base64
    import hashlib
    try:
        base = BASE
    except NameError:
        from scripts.skill_reload_agent import BASE as base

    if bundle.get('source_path') != str(base / 'mb.dpk'):
        raise ValueError('物品热重载主包路径不匹配')
    expected_paths = ['item/item_list.txt', 'item/item_list2.txt', 'item/item_list3.txt']
    files = bundle.get('files')
    if not isinstance(files, list) or [item.get('path') for item in files] != expected_paths:
        raise ValueError('物品热重载文件清单无效')
    total = 0
    for item in files:
        data = base64.b64decode(item.get('content', ''), validate=True)
        total += len(data)
        if total > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != item.get('sha256'):
            raise ValueError('物品表内容校验失败')
        if not 0 <= item.get('rows', -1) <= 65535:
            raise ValueError('物品表行数校验数据无效')
        item['data'] = data
    table = bundle.get('table')
    if (not isinstance(table, dict) or table.get('id') != 41
            or table.get('paths') != expected_paths
            or not 0 < table.get('rows', 0) <= 65535):
        raise ValueError('物品表 41 校验清单无效')
    if sum(item['rows'] for item in files) != table['rows']:
        raise ValueError('物品表文件行数合计不匹配')
    checks = table.get('checks', [])
    typed_checks = table.get('typed_checks', [])
    if len(checks) > 65535 or len(typed_checks) > 200:
        raise ValueError('物品表回读校验数据过多')
    for row, column, expected in checks:
        if (type(row) is not int or type(column) is not int or type(expected) is not int
                or not 0 <= row < table['rows'] or not 0 <= column < 200
                or not -(2**31) <= expected < 2**31):
            raise ValueError('物品表整数校验数据无效')
    for row, column, kind, value in typed_checks:
        if (type(row) is not int or type(column) is not int or not 0 <= row < table['rows']
                or not 0 <= column < 200 or kind not in ('string', 'array')):
            raise ValueError('物品表字段校验数据无效')
        if kind == 'string' and (not isinstance(value, str) or len(value.encode('gb18030')) > 65535):
            raise ValueError('物品表字符串校验数据无效')
        if kind == 'array' and (not isinstance(value, list) or len(value) > 65536
                or any(type(part) is not int or not -(2**31) <= part < 2**31 for part in value)):
            raise ValueError('物品表数组校验数据无效')
    item_ids = bundle.get('item_ids', [])
    if (not isinstance(item_ids, list) or len(item_ids) > 32
            or any(type(value) is not int or not 0 < value <= 65535 for value in item_ids)):
        raise ValueError('新增武器 ID 校验数据无效')
    return files, table


def perform_item_reload(bundle):
    import fcntl
    import json
    import shutil
    import tempfile
    from pathlib import Path

    files, table = validate_item_bundle(bundle)
    if sha256(BASE / 'mb.dpk') != bundle.get('source_sha256'):
        raise ValueError('服务器主包已变化，请重新读取后再热重载')
    with NativeGMClient() as client:
        for line in (1, 9):
            if not client.command(line, 'help reloadexcel').startswith('reloadexcel '):
                raise RuntimeError('线路 ' + str(line) + ' 不支持 reloadexcel')

    state_dir = ROOT / 'state/gmtool-item-reload'
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = ROOT / 'state/gmtool-skill-reload.lock'
    backup = Path(tempfile.mkdtemp(prefix='item-table-41-', dir=state_dir))
    owner = (BASE / 'mb.dpk').stat()
    saved = {}
    changed = []
    restore_errors = []
    command_attempted = False
    replies = {}

    def restore_scattered_files():
        for item in reversed(files):
            path = item['path']
            if path not in changed:
                continue
            target = BASE / 'mb' / path
            try:
                if saved[path] is None:
                    target.unlink(missing_ok=True)
                else:
                    write_atomic(target, saved[path], owner)
            except Exception as error:
                restore_errors.append(path + ': ' + str(error))

    try:
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            runtime = RuntimeInspector()
            identity = {'id': 41, 'paths': table['paths'], 'checks': [], 'typed_checks': []}
            loaded = runtime.snapshot([identity])
            if any(state['tables']['41']['rows'] <= 0 for state in loaded.values()):
                raise RuntimeError('服务端当前物品表 41 没有可用记录')
            for item in files:
                target = BASE / 'mb' / item['path']
                if target.is_symlink() or target.resolve() != target or not target.is_relative_to(BASE / 'mb'):
                    raise ValueError('物品表覆盖路径无效：' + item['path'])
                if target.exists() and sha256(target) != item['sha256']:
                    raise ValueError('服务器存在与当前主包不同的物品散文件：' + item['path'])
                saved[item['path']] = target.read_bytes() if target.exists() else None
                if saved[item['path']] is not None:
                    copy = backup / item['path']
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    copy.write_bytes(saved[item['path']])
            if sha256(BASE / 'mb.dpk') != bundle['source_sha256']:
                raise ValueError('服务器主包在部署物品列表前发生变化')
            for item in files:
                target = BASE / 'mb' / item['path']
                write_atomic(target, item['data'], owner)
                changed.append(item['path'])
                if sha256(target) != item['sha256']:
                    raise RuntimeError('物品表覆盖文件回读失败：' + item['path'])

            with NativeGMClient() as client:
                for line in (1, 9):
                    command_attempted = True
                    reply = client.command(line, 'reloadexcel 41')
                    replies[str(line)] = reply
                    if not reload_succeeded(reply, 'reloadexcel 41'):
                        raise RuntimeError('线路 ' + str(line) + ' 重载物品表失败：' + reply[:800])
            after = runtime.snapshot([table])
            for line, status in after.items():
                result = status['tables']['41']
                if result['rows'] != table['rows'] or result['mismatches']:
                    raise RuntimeError('线路 ' + line + ' 的物品表回读校验失败：' +
                                       json.dumps(result, ensure_ascii=False))
            if sha256(BASE / 'mb.dpk') != bundle['source_sha256']:
                raise RuntimeError('物品热重载期间服务器主包发生变化')
            restore_scattered_files()
            if restore_errors:
                raise RuntimeError('物品表已载入，但临时散文件清理失败：' + '；'.join(restore_errors))
            shutil.rmtree(backup, ignore_errors=True)
            return {'tables': 1, 'rows': table['rows'],
                    'checked_values': len(table['checks']) + len(table['typed_checks']),
                    'item_ids': bundle.get('item_ids', []), 'lines': [1, 9],
                    'pids': {line: value['pid'] for line, value in after.items()},
                    'replies': replies, 'scope': 'weapon',
                    'runtime_rollback': False, 'automatic_restart': False}
    except Exception as error:
        restore_scattered_files()
        if not restore_errors:
            shutil.rmtree(backup, ignore_errors=True)
        detail = str(error)
        if command_attempted:
            detail += '；没有自动恢复运行中的旧表，也没有自动重启服务端'
        if restore_errors:
            detail += '；临时散文件恢复失败：' + '；'.join(restore_errors) + '；诊断备份：' + str(backup)
        raise RuntimeError(detail) from error


def item_main():
    import json
    import sys

    try:
        request = json.load(sys.stdin)
        result = perform_item_reload(request['bundle'])
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
