"""Version-checked reloadskill agent, executed on the game host over SSH.

Only the loopback native GM protocol and read-only /proc memory inspection are
used. The memory layouts below belong to the explicitly hashed server build.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import sys
import tempfile
import time


ROOT = Path('/opt/xxserver')
BASE = ROOT / 'runtime/xx/shm/TTYGZS'


def master_package():
    """Serialize the remote Linux package path independently of the local OS."""
    return (BASE / 'mb.dpk').as_posix()


EXE_HASH = '6d1818f21217f884926af05bb7b26bddcaece76afd912558075443a149eab073'
GAMEPLAY_HASH = '9a19e9c8deaefeb8ba35d61f10ccc6f32d166ef2ee2aedf1f3ecf06cfa7109bb'
GM_HASHES = {
    'GMSMainStructure_d.so': '8d06066f374ded0e931cb72eff080f70ef10dbdb941754682ea203508b988acd',
    'GMSGamePlay_d.so': 'a077ce23face9ea5b9ca884066a7be75e3fc4a17d0d3844d0bc3bbaf3d954a68',
}


def skill_tables():
    result = {}
    groups = [(181, 'cmn'), (184, 'fs'), (187, 'fzs'), (190, 'ls'),
              (193, 'yx'), (196, 'npc'), (214, 'npc_pet'), (217, 'npc_1'),
              (220, 'npc_2'), (1362, 'npc_3'), (1365, 'npc_4')]
    for number, group in groups:
        for offset, prefix in enumerate(('skill_data_', 'method_data_', 'trigger_')):
            result[number + offset] = 'skill/' + prefix + group + '.txt'
    result.update({199: 'skill/methodunit_data.txt', 200: 'skill/trigger_methodunit.txt'})
    for number, suffix in [(226, '1'), (228, '2'), (230, '3'), (1402, '4'), (1412, '5')]:
        result[number] = 'skill/methodunit_data' + suffix + '.txt'
        result[number + 1] = 'skill/trigger_methodunit' + suffix + '.txt'
    return result


TABLES = skill_tables()
EXTRA_TABLES = {
    82: 'object/cha_list.txt', 209: 'skill/skill_levelup.txt',
    210: 'skill/npc_teach_skill.txt', 211: 'skill/skill_learn.txt',
    500: 'object/cha_skill_choose.txt', 1351: 'summon/summon_skill.txt',
    1352: 'summon/summon_skill_index.txt',
}
RIDE_TABLES = {84: 'object/ride_connection.txt', 85: 'object/ride_list.txt'}
ITEM_TABLE_ID = 41
ITEM_PATHS = ('item/item_list.txt', 'item/item_list2.txt', 'item/item_list3.txt')
ALL_TABLES = dict(TABLES)
ALL_TABLES.update(EXTRA_TABLES)
RELOADABLE_FILES = {**ALL_TABLES, **RIDE_TABLES}
EXCEL_RELOAD_TABLE_IDS = {*EXTRA_TABLES, *RIDE_TABLES, ITEM_TABLE_ID, 50, 95}
RELOADABLE_PATHS = {*RELOADABLE_FILES.values(), *ITEM_PATHS}


def scoped_tables(scope):
    if scope == 'definitions':
        return dict(TABLES)
    if scope == 'cross-career':
        return {key: EXTRA_TABLES[key] for key in (210, 211)}
    if scope == 'skill-learn':
        return {key: EXTRA_TABLES[key] for key in (210, 211)}
    if scope in ('summon', 'all'):
        return dict(ALL_TABLES)
    if scope == 'rides':
        return dict(RIDE_TABLES)
    raise ValueError('未知的技能热重载范围')


def allowed_command(command):
    return (command in ('help reloadskill', 'help reloadexcel', 'help reloadxyshopitem',
                        'reloadskill', 'reloadxyshopitem')
            or command in {'reloadexcel ' + str(key) for key in EXCEL_RELOAD_TABLE_IDS})


def command_plan(files, before, include_shop=False):
    return {line: ['reloadexcel ' + str(number) for number in sorted({spec['id'] for spec in files
                   if spec['id'] in EXCEL_RELOAD_TABLE_IDS and
                   (spec['id'] != 82 or state['tables'][str(spec['id'])]['rows'] != spec['rows']
                    or state['tables'][str(spec['id'])]['mismatches'])})]
                  + (['reloadskill'] if any(spec['id'] in TABLES for spec in files) else [])
                  + (['reloadxyshopitem'] if include_shop else [])
            for line, state in before.items()}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def receive_exact(connection, count):
    result = bytearray()
    while len(result) < count:
        block = connection.recv(count - len(result))
        if not block:
            raise RuntimeError('原生管理连接提前关闭')
        result.extend(block)
    return bytes(result)


def send_frame(connection, body):
    connection.sendall(b'\xaa\x55' + struct.pack('<I', len(body)) + body + b'\x55\xaa')


def receive_frame(connection):
    header = receive_exact(connection, 6)
    length = struct.unpack_from('<I', header, 2)[0]
    if header[:2] != b'\xaa\x55' or not 0 < length <= 262144:
        raise RuntimeError('原生管理回执格式错误')
    body = receive_exact(connection, length)
    if receive_exact(connection, 2) != b'\x55\xaa':
        raise RuntimeError('原生管理回执尾部错误')
    return body


class NativeGMClient:
    def __init__(self, timeout=45):
        self.timeout = timeout
        self.connection = None

    def __enter__(self):
        self.connection = socket.create_connection(('127.0.0.1', 3000), timeout=5)
        try:
            self.connection.settimeout(5)
            send_frame(self.connection, b'\xf1' + struct.pack('<I', 0))
            if receive_frame(self.connection) != b'\xfb\x01\x0a\0\0\0':
                raise RuntimeError('原生管理连接握手失败')
        except Exception:
            self.connection.close()
            raise
        return self

    def __exit__(self, *args):
        self.connection.close()

    def command(self, line, command):
        if line not in (1, 9) or not allowed_command(command):
            raise ValueError('仅允许普通线、交易线的配置热重载白名单操作')
        token = secrets.randbelow(0x7ffffffe) + 1
        text = ('gmcmd_2_gzs ' + str(line) + ',' + command).encode('ascii')
        send_frame(self.connection, b'\xf2' + struct.pack('<I', token) + b'\0' + text + b'\0')
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.connection.settimeout(max(.1, deadline - time.monotonic()))
            body = receive_frame(self.connection)
            if len(body) >= 6 and body[0] == 0xfc and struct.unpack_from('<I', body, 1)[0] == token:
                if body[5] != 0 or not body.endswith(b'\0'):
                    raise RuntimeError('原生技能重载回执内容错误')
                return body[6:-1].decode('gb18030', 'replace')
        raise TimeoutError('未收到原生技能重载回执')


class RuntimeInspector:
    """Read the supported 32-bit build's live table identities and scalar cells."""
    def __init__(self):
        if sha256(BASE / 'debugS/XGZS_d') != EXE_HASH or sha256(BASE / 'debugS/TTYGZS_d.so') != GAMEPLAY_HASH:
            raise ValueError('当前服务端二进制版本未经过技能热重载验证')
        for name, expected in GM_HASHES.items():
            if sha256(ROOT / 'runtime/xx/ITC/bin/XGMS' / name) != expected:
                raise ValueError('当前管理服务版本未经过技能热重载验证')
        self.processes = {}
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if os.readlink(proc / 'exe') != str(BASE / 'debugS/XGZS_d'):
                    continue
                args = (proc / 'cmdline').read_bytes().split(b'\0')
                line = {b'gzs_cfg1.txt': 1, b'gzs_cfg9.txt': 9}.get(args[1])
                if line is None:
                    continue
                if line in self.processes:
                    raise ValueError('检测到重复的游戏线路进程')
                mapping = next(row.split() for row in (proc / 'maps').read_text().splitlines()
                               if row.split()[-1] == str(BASE / 'debugS/TTYGZS_d.so') and int(row.split()[2], 16) == 0)
                if int(mapping[4]) != (BASE / 'debugS/TTYGZS_d.so').stat().st_ino:
                    raise ValueError('游戏进程加载的程序库与磁盘版本不一致')
                start = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
                self.processes[line] = {'pid': int(proc.name), 'start': start,
                                        'base': int(mapping[0].split('-')[0], 16)}
            except (FileNotFoundError, ProcessLookupError):
                continue
        if set(self.processes) != {1, 9}:
            raise ValueError('普通线和交易线必须都已启动')

    def snapshot(self, specifications):
        result = {}
        for line, process in self.processes.items():
            proc = Path('/proc') / str(process['pid'])
            if (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] != process['start']:
                raise RuntimeError('游戏线路进程在热重载期间发生变化')
            fd = os.open(proc / 'mem', os.O_RDONLY)
            try:
                def read(address, count):
                    if not 0x10000 <= address < 0xffff0000 or not 0 <= count <= 8 * 1024 * 1024:
                        raise RuntimeError('技能表内存结构异常')
                    data = os.pread(fd, count, address)
                    if len(data) != count:
                        raise RuntimeError('技能表内存读取不完整')
                    return data
                def u32(address):
                    return struct.unpack('<I', read(address, 4))[0]
                def string_at(address):
                    if not address:
                        return ''
                    value = bytearray()
                    for offset in range(65536):
                        char = read(address + offset, 1)
                        if char == b'\0':
                            return value.decode('gb18030')
                        value.extend(char)
                    raise ValueError('技能表字符串长度异常')
                manager = u32(u32(0x8416bac))
                if read(manager + 4, 1) != b'\x02':
                    raise ValueError('当前文件管理器没有启用磁盘文件优先读取')
                base = process['base']
                array = u32(base + 0x11bb664)
                count = u32(base + 0x11bb668)
                tables = {}
                for spec in specifications:
                    number = spec['id']
                    if not 0 < number < count:
                        raise ValueError('运行中的游戏缺少技能表 ' + str(number))
                    table = u32(array + number * 4)
                    begin, end = u32(table + 0x30), u32(table + 0x34)
                    if end < begin or (end - begin) % 4 or end - begin > 16 * 4:
                        raise ValueError('技能表资源引用与预期不一致：' + str(number))
                    paths = []
                    for resource in range(begin, end, 4):
                        address = u32(resource)
                        name_bytes = bytearray()
                        for offset in range(2048):
                            char = read(address + offset, 1)
                            if char == b'\0':
                                break
                            name_bytes.extend(char)
                        else:
                            raise ValueError('技能表资源路径长度异常')
                        paths.append(name_bytes.decode('gb18030'))
                    expected_paths = spec.get('paths') or [spec['path']]
                    expected_paths = [str(BASE / 'mb' / path) for path in expected_paths]
                    if paths != expected_paths:
                        raise ValueError('技能表实际读取路径不匹配：' + '、'.join(paths))
                    rows_begin, rows_end = u32(table + 0x24), u32(table + 0x28)
                    if rows_end < rows_begin or (rows_end - rows_begin) % 4 or rows_end - rows_begin > 2 * 1024 * 1024:
                        raise RuntimeError('技能表行索引异常')
                    row_count = (rows_end - rows_begin) // 4
                    mapping = read(u32(table + 0x0c), u32(table + 0x10) - u32(table + 0x0c))
                    values = []
                    mismatch = 0
                    examples = []
                    def cell_address(row_index, column):
                        if row_index >= row_count:
                            return None
                        if column >= len(mapping):
                            return 0
                        data = read(u32(rows_begin + row_index * 4), 12)
                        cells, position = data[8], mapping[column]
                        if position >= cells:
                            return 0
                        cell_data = struct.unpack_from('<I', data, 4)[0]
                        slot = read(cell_data - cells + position, 1)[0]
                        if slot == 255:
                            return 0
                        return (cell_data + slot * 4 if slot < 200 else
                                base + 0x11bb680 + (slot - 200) * 4)
                    for row_index, column, expected in spec['checks']:
                        value = None
                        if row_index < row_count and column < len(mapping):
                            row = u32(rows_begin + row_index * 4)
                            data = read(row, 12)
                            cells = data[8]
                            position = mapping[column]
                            if position < cells:
                                cell_data = struct.unpack_from('<I', data, 4)[0]
                                slot = read(cell_data - cells + position, 1)[0]
                                if slot == 255:
                                    value = 0
                                else:
                                    address = (cell_data + slot * 4 if slot < 200 else
                                               base + 0x11bb680 + (slot - 200) * 4)
                                    value = struct.unpack('<i', read(address, 4))[0]
                        values.append(value)
                        mismatch += value != expected
                        if value != expected and len(examples) < 3:
                            examples.append([row_index, column, expected, value])
                    for row_index, column, kind, expected in spec.get('typed_checks', []):
                        address = cell_address(row_index, column)
                        value = None
                        if address is not None:
                            pointer = u32(address) if address else 0
                            if kind == 'string':
                                value = string_at(pointer)
                            else:
                                size = u32(pointer - 4) if pointer else 0
                                if size > 65536:
                                    raise RuntimeError('技能表数组长度异常')
                                value = list(struct.unpack('<' + 'i' * size, read(pointer, size * 4))) if size else []
                        values.append(value)
                        mismatch += value != expected
                        if value != expected and len(examples) < 3:
                            examples.append([row_index, column, expected, value])
                    digest = hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
                    tables[str(number)] = {'rows': row_count, 'mismatches': mismatch, 'digest': digest,
                                          'examples': examples}
                result[str(line)] = {'pid': process['pid'], 'start': process['start'], 'tables': tables}
            finally:
                os.close(fd)
        return result


def validate_bundle(bundle):
    if bundle.get('source_path') != master_package():
        raise ValueError('技能主包路径不匹配')
    files = bundle.get('files', [])
    scope = bundle.get('scope', 'definitions')
    if scope == 'combined':
        runtime_tables = bundle.get('runtime_tables', [])
        if not isinstance(runtime_tables, list) or (not runtime_tables and not bundle.get('shop_reload')):
            raise ValueError('合并热重载缺少运行时表校验清单')
        allowed = {key: {value} for key, value in RELOADABLE_FILES.items()}
        allowed[ITEM_TABLE_ID] = set(ITEM_PATHS)
        table_paths = {}
        for table in runtime_tables:
            number = table.get('id')
            paths = table.get('paths') or [table.get('path')]
            if (number not in allowed or number in table_paths or not all(paths)
                    or set(paths) != allowed[number]):
                raise ValueError('合并热重载的表 ID 或来源路径无效')
            table_paths[number] = set(paths)
        file_paths = {}
        for item in files:
            number, path = item.get('id'), item.get('path')
            if number not in table_paths or path not in table_paths[number] or path in file_paths:
                raise ValueError('合并热重载的文件与表索引不匹配')
            file_paths[path] = number
        expected = {path: number for number, paths in table_paths.items() for path in paths}
        if file_paths != expected:
            raise ValueError('合并热重载文件清单不完整')
    else:
        runtime_tables = files
        selected = scoped_tables(scope)
        if len(files) != len(selected) or {item['id'] for item in files} != set(selected):
            raise ValueError('技能热重载的表清单不完整')
        for spec in files:
            if spec['path'] != selected[spec['id']]:
                raise ValueError('技能表路径无效')
    total = 0
    for spec in files:
        data = base64.b64decode(spec['content'], validate=True)
        total += len(data)
        if total > 48 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != spec['sha256']:
            raise ValueError('技能表内容校验失败')
        if not 0 <= spec.get('rows', 0) <= 65535 or len(spec.get('checks', [])) > 100000:
            raise ValueError('技能表校验信息无效')
        for check in spec.get('checks', []):
            if (not isinstance(check, list) or len(check) != 3 or any(type(value) is not int for value in check)
                    or not 0 <= check[0] < spec['rows'] or not 0 <= check[1] < 200
                    or not -(2 ** 31) <= check[2] < 2 ** 31):
                raise ValueError('技能表数值校验信息无效')
        spec['data'] = data
    for spec in runtime_tables:
        rows = spec.get('rows', 0)
        if not 0 <= rows <= 65535 or len(spec.get('checks', [])) > 100000:
            raise ValueError('运行时表回读校验信息无效')
        for check in spec.get('checks', []):
            if (not isinstance(check, list) or len(check) != 3 or any(type(value) is not int for value in check)
                    or not 0 <= check[0] < rows or not 0 <= check[1] < 200
                    or not -(2 ** 31) <= check[2] < 2 ** 31):
                raise ValueError('运行时表整数校验信息无效')
        for row, column, kind, value in spec.get('typed_checks', []):
            if (not 0 <= row < rows or not 0 <= column < 200
                    or kind not in ('array', 'string')
                    or (kind == 'string' and (not isinstance(value, str) or len(value.encode('gb18030')) > 65535))
                    or (kind == 'array' and (not isinstance(value, list) or len(value) > 65536
                        or any(type(item) is not int or not -(2**31) <= item < 2**31 for item in value)))):
                raise ValueError('技能表数组或字符串校验信息无效')
    if scope == 'combined':
        if not bundle.get('no_runtime_rollback'):
            raise ValueError('合并热重载必须关闭运行时回滚')
        if type(bundle.get('shop_reload', False)) is not bool:
            raise ValueError('商城热重载标记无效')
    return files


def write_atomic(path, data, owner):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path or not path.is_relative_to(BASE / 'mb'):
        raise ValueError('技能覆盖路径不允许使用符号链接')
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o755)
        os.chown(path.parent, owner.st_uid, owner.st_gid)
    metadata = path.stat() if path.exists() else owner
    fd, temporary = tempfile.mkstemp(prefix='.reloadskill-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777 if path.exists() else 0o644)
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def assert_loaded(snapshot, files):
    for line, state in snapshot.items():
        for spec in files:
            table = state['tables'][str(spec['id'])]
            if table['rows'] != spec['rows'] or table['mismatches']:
                raise RuntimeError('线路 ' + line + ' 的技能表 ' + str(spec['id']) + ' 内存回读校验失败')


def perform_reload(bundle, runtime, client_factory=NativeGMClient):
    """Deploy a reload plan; on failure clean temporary files without runtime rollback."""
    files = validate_bundle(bundle)
    runtime_tables = bundle.get('runtime_tables', files)
    no_runtime_rollback = bool(bundle.get('no_runtime_rollback'))
    if sha256(BASE / 'mb.dpk') != bundle['source_sha256']:
        raise ValueError('服务器主包已变化，请重新读取后再热重载')
    before = runtime.snapshot(runtime_tables)
    plans = command_plan(files, before, include_shop=bool(bundle.get('shop_reload')))
    with client_factory() as client:
        for line in (1, 9):
            help_names = {'reloadskill' if command == 'reloadskill' else
                          'reloadxyshopitem' if command == 'reloadxyshopitem' else 'reloadexcel'
                          for command in plans[str(line)]}
            for help_name in help_names:
                if not client.command(line, 'help ' + help_name).startswith(help_name + ' '):
                    raise RuntimeError('线路 ' + str(line) + ' 不支持 ' + help_name)
    state_dir = ROOT / 'state/gmtool-skill-reload'
    state_dir.mkdir(mode=0o700, exist_ok=True)
    state_path = state_dir / 'current.json'
    prior = json.loads(state_path.read_text()) if state_path.exists() else {}
    for spec in files:
        target = BASE / 'mb' / spec['path']
        if target.is_symlink() or target.resolve() != target:
            raise ValueError('技能覆盖路径不允许使用符号链接')
        if target.exists():
            current = sha256(target)
            managed = prior.get('files', {}).get(spec['path'])
            if current != spec['sha256'] and current != managed:
                raise ValueError('存在其他来源修改的技能散文件：' + spec['path'])
    backup = Path(tempfile.mkdtemp(prefix='transaction-', dir=state_dir))
    owner = (BASE / 'mb.dpk').stat()
    saved = {}
    changed = []
    command_attempted = False
    replies = {}
    def restore_files():
        for name in reversed(changed):
            target = BASE / 'mb' / name
            if saved[name] is None:
                target.unlink(missing_ok=True)
            else:
                write_atomic(target, saved[name], owner)
    try:
        for spec in files:
            target = BASE / 'mb' / spec['path']
            saved[spec['path']] = target.read_bytes() if target.exists() else None
            if saved[spec['path']] is not None:
                destination = backup / spec['path']
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(saved[spec['path']])
        (backup / 'manifest.json').write_text(json.dumps({'files': {name: data is not None for name, data in saved.items()},
            'before': before, 'previous_state': prior}, ensure_ascii=False))
        if sha256(BASE / 'mb.dpk') != bundle['source_sha256']:
            raise ValueError('服务器主包在部署前发生变化')
        for spec in files:
            target = BASE / 'mb' / spec['path']
            write_atomic(target, spec['data'], owner)
            changed.append(spec['path'])
            if sha256(target) != spec['sha256']:
                raise RuntimeError('技能覆盖文件回读失败：' + spec['path'])
        with client_factory() as client:
            for line in (1, 9):
                replies[str(line)] = {}
                for command in plans[str(line)]:
                    command_attempted = True
                    reply = client.command(line, command)
                    replies[str(line)][command] = reply
                    if not reload_succeeded(reply, command):
                        raise RuntimeError('线路 ' + str(line) + ' 技能重载失败：' + reply[:800])
        after = runtime.snapshot(runtime_tables)
        assert_loaded(after, runtime_tables)
        if sha256(BASE / 'mb.dpk') != bundle['source_sha256']:
            raise RuntimeError('服务器主包在热重载期间发生变化')
        # Loose files are temporary: never shadow a later package update/restart.
        restore_files()
        prior_runtime = prior.get('runtime', {})
        same_processes = all(prior_runtime.get(line, {}).get('pid') == value['pid']
                             and prior_runtime.get(line, {}).get('start') == value['start']
                             for line, value in before.items())
        active = dict(prior.get('active_content', {})) if same_processes else {}
        active.update({item['path']: item['content'] for item in files})
        managed_files = dict(prior.get('files', {})) if same_processes else {}
        managed_files.update({item['path']: item['sha256'] for item in files})
        state = {'source_sha256': bundle['source_sha256'], 'files': managed_files,
                 'completed_at': int(time.time()), 'runtime': after,
                 'active_content': active}
        temporary_state = state_path.with_suffix('.tmp')
        temporary_state.write_text(json.dumps(state, ensure_ascii=False))
        os.replace(temporary_state, state_path)
        previous = state_dir / 'previous'
        if previous.exists():
            shutil.rmtree(previous)
        os.replace(backup, previous)
        return {'tables': len(runtime_tables), 'checked_values': sum(len(item.get('checks', [])) + len(item.get('typed_checks', [])) for item in runtime_tables),
                'lines': [1, 9], 'pids': {line: value['pid'] for line, value in after.items()},
                'replies': replies, 'scope': bundle.get('scope', 'definitions'),
                'scopes': bundle.get('scopes', []), 'commands': plans,
                'source_sha256': bundle['source_sha256'], 'backup': str(previous)}
    except Exception as error:
        rollback_errors = []
        for name in reversed(changed):
            try:
                target = BASE / 'mb' / name
                if saved[name] is None:
                    target.unlink(missing_ok=True)
                else:
                    write_atomic(target, saved[name], owner)
            except Exception as restore_error:
                rollback_errors.append(str(restore_error))
        if not no_runtime_rollback:
            try:
                if prior:
                    state_path.write_text(json.dumps(prior, ensure_ascii=False))
                else:
                    state_path.unlink(missing_ok=True)
            except Exception as restore_error:
                rollback_errors.append('恢复部署记录失败：' + str(restore_error))
        if command_attempted and not rollback_errors and not no_runtime_rollback:
            try:
                prior_runtime = prior.get('runtime', {})
                same_processes = all(prior_runtime.get(line, {}).get('pid') == value['pid']
                                     and prior_runtime.get(line, {}).get('start') == value['start']
                                     for line, value in before.items())
                if same_processes:
                    for name, content in prior.get('active_content', {}).items():
                        if name not in {spec['path'] for spec in files}:
                            continue
                        if name not in RELOADABLE_PATHS:
                            raise ValueError('之前的技能部署记录路径异常')
                        write_atomic(BASE / 'mb' / name, base64.b64decode(content, validate=True), owner)
                with client_factory() as client:
                    for line in (1, 9):
                        for command in plans[str(line)]:
                            if not reload_succeeded(client.command(line, command), command):
                                raise RuntimeError('线路 ' + str(line) + ' 恢复技能表失败')
                restored = runtime.snapshot(files)
                if restored != before:
                    raise RuntimeError('恢复后的技能表与操作前内存校验不一致')
            except Exception as restore_error:
                rollback_errors.append(str(restore_error))
            finally:
                try:
                    restore_files()
                except Exception as restore_error:
                    rollback_errors.append('清理恢复文件失败：' + str(restore_error))
        message = str(error)
        if no_runtime_rollback:
            message += '；本次临时散文件已清理；未尝试恢复运行中的旧表，也未自动重启服务端'
        elif changed:
            message += '；' + ('恢复未完成：' + '；'.join(rollback_errors) if rollback_errors else '已恢复操作前的覆盖文件和技能表')
        raise RuntimeError(message + '；备份：' + str(backup)) from error


def reload_succeeded(reply, command='reloadskill'):
    # This native handler overwrites its intermediate errors with this message.
    # Always also inspect the live tables; this response alone is insufficient.
    if command == 'reloadskill':
        return reply.strip() == 'ReloadExcel:OK!'
    if command == 'reloadxyshopitem':
        return reply.strip() == 'reload xy_shop_item OK!'
    return reply.strip() == 'ReloadExcel: ' + command.split()[1] + ' OK!'


def main():
    import fcntl
    try:
        request = json.load(sys.stdin)
        if request.get('action') not in ('check', 'apply'):
            raise ValueError('未知技能热重载操作')
        with (ROOT / 'state/gmtool-skill-reload.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            files = validate_bundle(request['bundle'])
            runtime = RuntimeInspector()
            if request['action'] == 'check':
                result = {'runtime': runtime.snapshot(files), 'tables': len(files)}
                result['commands'] = command_plan(files, result['runtime'])
                with NativeGMClient() as client:
                    result['help'] = {str(line): {name: client.command(line, 'help ' + name)
                        for name in ('reloadskill', 'reloadexcel')} for line in (1, 9)}
            else:
                result = perform_reload(request['bundle'], runtime)
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
