"""Prepare and run the item-list hot reload for a newly added weapon."""

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shlex

from .skill_reload_agent import BASE
from .unpack import DpkReader


ITEM_TABLE_ID = 41
ITEM_PATHS = (
    'item/item_list.txt',
    'item/item_list2.txt',
    'item/item_list3.txt',
)
ITEM_CHECK_COLUMNS = (0, 1, 2, 4, 5, 7, 9, 11, 18, 19, 20, 21, 22, 23, 24,
                      25, 26, 27, 28, 29)


def _rows(data):
    text = data.decode('gb18030')
    if '\0' in text:
        raise ValueError('物品表包含无效的空字符')
    rows = list(csv.reader(io.StringIO(text), delimiter='\t'))
    if not rows:
        raise ValueError('物品列表为空')
    return [row for row in rows[1:] if row and any(value.strip() for value in row)]


def build_bundle(package: Path, item_ids=()):
    """Extract table 41's three source files and prepare bounded runtime checks."""
    package = Path(package)
    package_hash = hashlib.sha256(package.read_bytes()).hexdigest()
    requested_ids = {int(value) for value in item_ids}
    if any(value <= 0 or value > 65535 for value in requested_ids):
        raise ValueError('新增武器 ID 无效')

    files = []
    index_row = None
    all_rows = []
    with DpkReader(package) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        index_entry = entries.get('index_list.txt')
        if index_entry is None:
            raise ValueError('服务器主包缺少 index_list.txt')
        for row in csv.reader(io.StringIO(reader.extract(index_entry).decode('gb18030')), delimiter='\t'):
            if len(row) > 6 and row[1].isdigit() and int(row[1]) == ITEM_TABLE_ID:
                if index_row is not None:
                    raise ValueError('服务器主包的物品列表表索引重复')
                index_row = row
        expected_index_paths = [path[:-4] for path in ITEM_PATHS]
        if (index_row is None or index_row[2].lower().split('*') != expected_index_paths
                or index_row[3] != '0' or index_row[4] != '1'
                or index_row[5] not in ('0', '1')):
            raise ValueError('服务器主包的物品列表索引与当前已核实版本不符')
        types = [int(value) for value in index_row[6].split('*')]
        index_column = int(index_row[3])

        for path in ITEM_PATHS:
            entry = entries.get(path)
            if entry is None:
                raise ValueError('服务器主包缺少物品列表：' + path)
            data = reader.extract(entry)
            rows = _rows(data)
            if len(rows[0]) > 200 or any(len(row) < len(types) for row in rows):
                raise ValueError('物品列表列布局与索引类型不匹配：' + path)
            offset = len(all_rows)
            for position, row in enumerate(rows):
                if not row[index_column].strip().isdigit():
                    raise ValueError('物品列表包含无效 ID：' + path)
                all_rows.append((offset + position, row))
            files.append({'path': path, 'rows': len(rows),
                          'content': base64.b64encode(data).decode('ascii'),
                          'sha256': hashlib.sha256(data).hexdigest()})

    id_positions = {}
    for position, row in all_rows:
        item_id = int(row[index_column])
        if item_id in id_positions:
            raise ValueError('物品列表存在重复 ID：' + str(item_id))
        id_positions[item_id] = position
    if not requested_ids:
        requested_ids = {max(id_positions)} if id_positions else set()
    missing = requested_ids - id_positions.keys()
    if missing:
        raise ValueError('服务器主包中找不到新增武器：' + '、'.join(map(str, sorted(missing))))

    sample_rows = {0, len(all_rows) // 2, len(all_rows) - 1}
    sample_rows.update(id_positions[item_id] for item_id in requested_ids)
    checks = []
    typed_checks = []
    for position, row in all_rows:
        columns = {index_column}
        if position in sample_rows:
            columns.update(column for column in ITEM_CHECK_COLUMNS if column < len(row))
        for column in sorted(columns):
            kind = types[column]
            raw = row[column].strip()
            if kind == 0:
                if raw and not re.fullmatch(r'[+-]?\d+', raw):
                    raise ValueError('物品列表整数列格式异常：行 ' + str(position + 2))
                checks.append([position, column, int(raw) if raw else 0])
            elif position in sample_rows and kind == 1:
                typed_checks.append([position, column, 'string', row[column]])
            elif position in sample_rows and kind == 4:
                if raw and not re.fullmatch(r'[+-]?\d+(\*[+-]?\d+)*', raw):
                    raise ValueError('物品列表数组列格式异常：行 ' + str(position + 2))
                typed_checks.append([position, column, 'array',
                    [int(part) for part in raw.split('*')] if raw else []])

    table = {'id': ITEM_TABLE_ID, 'paths': list(ITEM_PATHS), 'rows': len(all_rows),
             'checks': checks, 'typed_checks': typed_checks}
    if hashlib.sha256(package.read_bytes()).hexdigest() != package_hash:
        raise ValueError('服务器主包在读取期间发生变化，请重新读取')
    return {'source_path': str(BASE / 'mb.dpk'), 'source_sha256': package_hash,
            'files': files, 'table': table, 'item_ids': sorted(requested_ids)}


def run(connection, package: Path, item_ids=()):
    bundle = build_bundle(package, item_ids)
    base_source = Path(__file__).with_name('skill_reload_agent.py').read_text(encoding='utf-8')
    base_source = base_source.split("\nif __name__ == '__main__':", 1)[0]
    item_source = Path(__file__).with_name('item_reload_agent.py').read_text(encoding='utf-8')
    remote_source = base_source + '\n\n' + item_source + "\n\nif __name__ == '__main__':\n    item_main()\n"
    stdin, stdout, stderr = connection.exec_command(
        'sudo -n python3 -c ' + shlex.quote(remote_source), timeout=240)
    stdin.write(json.dumps({'bundle': bundle}, ensure_ascii=False).encode('utf-8'))
    stdin.channel.shutdown_write()
    output = stdout.read().decode('utf-8', 'replace')
    error = stderr.read().decode('utf-8', 'replace')
    status = stdout.channel.recv_exit_status()
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(error.strip() or output.strip() or '物品表热重载没有返回有效结果') from exc
    if status or not result.get('ok'):
        raise RuntimeError(result.get('error') or error or '物品表热重载失败')
    return result['data']
