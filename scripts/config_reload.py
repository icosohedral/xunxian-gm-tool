"""Build and apply one de-duplicated hot-reload plan for service control."""

import json
import hashlib
from pathlib import Path
import shlex

from .item_reload import build_bundle as build_item_bundle
from .skill_reload import build_selected_bundle as build_skill_bundle
from .skill_reload_agent import master_package, scoped_tables


SKILL_SCOPES = {'definitions', 'skill-learn', 'all', 'rides'}
SUPPORTED_SCOPES = SKILL_SCOPES | {'weapon', 'shop'}


def build_bundle(package, scopes, item_ids=()):
    package = Path(package)
    scopes = list(dict.fromkeys(scopes))
    if not scopes or any(scope not in SUPPORTED_SCOPES for scope in scopes):
        raise ValueError('请选择有效的热重载项目')

    selected_tables = {}
    for scope in scopes:
        if scope not in SKILL_SCOPES:
            continue
        for number, path in scoped_tables(scope).items():
            prior_path = selected_tables.get(number)
            if prior_path is not None and prior_path != path:
                raise ValueError('合并热重载发现表 ID 对应多个来源：' + str(number))
            selected_tables[number] = path
    skill_bundle = (build_skill_bundle(package, selected_tables, 'combined')
                    if selected_tables else None)
    item_bundle = build_item_bundle(package, item_ids) if 'weapon' in scopes else None

    source_hashes = {skill_bundle['source_sha256']} if skill_bundle else set()
    if item_bundle:
        source_hashes.add(item_bundle['source_sha256'])
    if 'shop' in scopes and not source_hashes:
        source_hashes.add(hashlib.sha256(package.read_bytes()).hexdigest())
    if len(source_hashes) != 1:
        raise ValueError('热重载项目读取到的服务器主包版本不一致')

    files_by_id = {}
    if skill_bundle:
        files_by_id.update({item['id']: item for item in skill_bundle['files']})

    runtime_tables = {number: {
        key: item[key] for key in ('id', 'path', 'rows', 'checks', 'typed_checks')
        if key in item
    } for number, item in files_by_id.items()}
    files = list(files_by_id.values())

    if item_bundle:
        item_table = item_bundle['table']
        if 41 in runtime_tables:
            raise ValueError('热重载计划中物品表 41 出现重复定义')
        runtime_tables[41] = item_table
        for item in item_bundle['files']:
            files.append({'id': 41, **item})

    return {
        'source_path': (item_bundle['source_path'] if item_bundle else skill_bundle['source_path']
                        if skill_bundle else master_package()),
        'source_sha256': next(iter(source_hashes)),
        'scope': 'combined',
        'scopes': scopes,
        'files': sorted(files, key=lambda item: (item['id'], item['path'])),
        'runtime_tables': [runtime_tables[number] for number in sorted(runtime_tables)],
        'item_ids': item_bundle['item_ids'] if item_bundle else [],
        'no_runtime_rollback': True,
        'shop_reload': 'shop' in scopes,
    }


def run(connection, package, scopes, item_ids=()):
    bundle = build_bundle(package, scopes, item_ids)
    script = Path(__file__).with_name('skill_reload_agent.py').read_text(encoding='utf-8')
    stdin, stdout, stderr = connection.exec_command(
        'sudo -n python3 -c ' + shlex.quote(script), timeout=300)
    stdin.write(json.dumps({'action': 'apply', 'bundle': bundle}, ensure_ascii=False).encode('utf-8'))
    stdin.channel.shutdown_write()
    output = stdout.read().decode('utf-8', 'replace')
    error = stderr.read().decode('utf-8', 'replace')
    status = stdout.channel.recv_exit_status()
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(error.strip() or output.strip() or '热重载没有返回有效结果') from exc
    if status or not result.get('ok'):
        raise RuntimeError(result.get('error') or error or '热重载失败')
    result['data']['runtime_rollback'] = False
    result['data']['automatic_restart'] = False
    return result['data']
