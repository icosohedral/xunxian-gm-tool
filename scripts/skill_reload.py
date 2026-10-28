"""Prepare skill tables from the shared server package and reload via native GM."""

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shlex

from .skill_reload_agent import master_package, TABLES, EXTRA_TABLES, scoped_tables
from .unpack import DpkReader


def _rows(data, keep_empty=False):
    text = data.decode('gb18030')
    if '\0' in text:
        raise ValueError('技能表包含无效的空字符')
    return [row for row in csv.reader(io.StringIO(text), delimiter='\t')
            if keep_empty or (row and any(field.strip() for field in row))]


def build_bundle(package, scope='definitions'):
    """Keep native table IDs and column layouts; never export client tables."""
    return build_selected_bundle(package, scoped_tables(scope), scope)


def build_selected_bundle(package, selected, scope='combined'):
    """Build one table bundle from an already de-duplicated table map."""
    package = Path(package)
    before = hashlib.sha256(package.read_bytes()).hexdigest()
    files = []
    with DpkReader(package) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        if 'index_list.txt' not in entries:
            raise ValueError('服务器主包缺少技能表索引')
        indexes = {}
        for row in _rows(reader.extract(entries['index_list.txt']))[1:]:
            if len(row) > 6 and row[1].isdigit() and int(row[1]) in selected:
                number = int(row[1])
                if number in indexes:
                    raise ValueError('技能表索引重复：' + str(number))
                indexes[number] = row
        for number, path in sorted(selected.items()):
            index = indexes.get(number)
            if index is None or index[2].lower() + '.txt' != path or path not in entries:
                raise ValueError('服务器主包的技能表索引不匹配：' + str(number))
            if index[5] not in ('0', '1'):
                raise ValueError('技能表未设置为由服务器载入：' + path)
            data = reader.extract(entries[path])
            rows = _rows(data, keep_empty=True)
            start = int(index[4])
            if start != 1 or not rows:
                raise ValueError('技能表表头格式不匹配：' + path)
            rows = [row for row in rows[start:] if row and any(field.strip() for field in row)]
            types = [int(value) for value in index[6].split('*')]
            index_column = int(index[3])
            checks = []
            typed_checks = []
            samples = {0, len(rows) // 2, len(rows) - 1}
            for position, row in enumerate(rows):
                columns = {index_column}
                if path.startswith('skill/skill_data_'):
                    columns.add(21)  # The cooldown column edited by the workbench.
                if path.startswith('skill/method_data_'):
                    columns.add(10)  # Method distance.
                if position in samples:
                    columns.update(range(min(len(row), 200)))
                if number in EXTRA_TABLES and (number != 82 or row[index_column] in {
                        '37236', '37252', *map(str, range(37643, 37653))}):
                    columns.update(range(min(len(row), len(types), 200)))
                for column in sorted(columns):
                    if column >= len(row):
                        continue
                    kind = types[min(column, len(types) - 1)]
                    value = row[column].strip()
                    if kind == 0 and re.fullmatch(r'[+-]?\d+', value) and -(2 ** 31) <= int(value) < 2 ** 31:
                        checks.append([position, column, int(value)])
                    elif number in EXTRA_TABLES and kind == 1:
                        typed_checks.append([position, column, 'string', row[column]])
                    elif number in EXTRA_TABLES and kind == 4:
                        if value and not re.fullmatch(r'[+-]?\d+(\*[+-]?\d+)*', value):
                            raise ValueError('技能表数组格式不支持：' + path + ' 第 ' + str(position + 2) + ' 行')
                        typed_checks.append([position, column, 'array', [int(part) for part in value.split('*')] if value else []])
            files.append({'id': number, 'path': path, 'rows': len(rows), 'checks': checks,
                          'typed_checks': typed_checks,
                          'content': base64.b64encode(data).decode('ascii'),
                          'sha256': hashlib.sha256(data).hexdigest()})
    if hashlib.sha256(package.read_bytes()).hexdigest() != before:
        raise ValueError('服务器缓存包在读取期间发生变化，请重试')
    return {'source_path': master_package(), 'source_sha256': before, 'files': files,
            'scope': scope, 'no_runtime_rollback': True}


def run(connection, package, action='apply', scope='definitions'):
    if action not in ('check', 'apply'):
        raise ValueError('未知的技能热重载操作')
    bundle = build_bundle(package, scope)
    script = Path(__file__).with_name('skill_reload_agent.py').read_text(encoding='utf-8')
    stdin, stdout, stderr = connection.exec_command(
        'sudo -n python3 -c ' + shlex.quote(script), timeout=180)
    stdin.write(json.dumps({'action': action, 'bundle': bundle}, ensure_ascii=False).encode('utf-8'))
    stdin.channel.shutdown_write()
    output = stdout.read().decode('utf-8', 'replace')
    error = stderr.read().decode('utf-8', 'replace')
    status = stdout.channel.recv_exit_status()
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(error.strip() or output.strip() or '技能热重载未返回有效结果') from exc
    if status or not result.get('ok'):
        raise RuntimeError(result.get('error') or error or '技能热重载失败')
    result['data']['runtime_rollback'] = False
    result['data']['automatic_restart'] = False
    return result['data']
