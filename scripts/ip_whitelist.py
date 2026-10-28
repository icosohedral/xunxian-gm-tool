"""Read and update the game server's network allowlist over SSH."""

import ipaddress
import json
import shlex


REMOTE_SCRIPT = r'''
import ipaddress, json, os, pathlib, re, shutil, sys, tempfile

cfg = pathlib.Path('/opt/xxserver/runtime/all/xxsrv.ini')
run_root = pathlib.Path('/opt/xxserver/runs')
action = sys.argv[1]
requested = sys.argv[2] if len(sys.argv) > 2 else ''

def config():
    raw = cfg.read_bytes()
    text = raw.decode('latin1')
    section = re.search(r'(?im)^\s*\[net\]\s*(?:\r?\n|$)', text)
    if not section:
        raise ValueError('xxsrv.ini 缺少 [net] 配置段')
    end = re.search(r'(?m)^\s*\[[^\]\r\n]+\]', text[section.end():])
    stop = section.end() + end.start() if end else len(text)
    block = text[section.end():stop]
    matches = list(re.finditer(r'(?im)^([ \t]*allow_cidr[ \t]*=[ \t]*)([^\r\n]*)(\r?\n|$)', block))
    if len(matches) != 1:
        raise ValueError('[net] 中必须有且只有一条 allow_cidr')
    match = matches[0]
    entries = [part.strip() for part in match.group(2).split(',') if part.strip()]
    for entry in entries:
        ipaddress.IPv4Network(entry, strict=False)
    wan = re.search(r'(?im)^\s*allow_wan\s*=\s*([^\r\n]*)', block)
    return raw, text, section, stop, block, match, entries, wan.group(1).strip() if wan else ''

def recent(entries):
    if not run_root.is_dir():
        return [], '未找到服务端运行日志目录'
    networks = [ipaddress.IPv4Network(item, strict=False) for item in entries]
    rows = []
    # Run recovery/start scripts can create a newer run without updating
    # state/active.json. Use timestamped run directories as the source of order.
    for run in sorted((item for item in run_root.iterdir() if item.is_dir()),
                      key=lambda item: item.name, reverse=True):
        log = run / 'backend.log'
        if not log.is_file():
            continue
        try:
            with log.open('rb') as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - 4 * 1024 * 1024))
                data = handle.read()
            if size > len(data):
                data = data.split(b'\n', 1)[-1]
            try:
                decoded = data.decode('utf-8')
            except UnicodeDecodeError:
                decoded = data.decode('gb18030', 'replace')
        except OSError:
            continue
        lines = decoded.splitlines()
        for line in reversed(lines):
            if '[net]' not in line.lower():
                continue
            if not any(word in line for word in ('来源', '连接', '登录', '客户端', '拒绝', 'source', 'client')):
                continue
            source = re.search(r'来源\s*((?:\d{1,3}\.){3}\d{1,3})', line)
            found = [source.group(1)] if source else re.findall(
                r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', line)
            ip = None
            for value in reversed(found):
                try:
                    ip = ipaddress.IPv4Address(value)
                    break
                except ValueError:
                    pass
            if ip is None:
                continue
            stamp = re.search(r'(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T])?\d{1,2}:\d{2}:\d{2}', line)
            rows.append({'time': stamp.group(0) if stamp else '', 'ip': str(ip),
                         'allowed': any(ip in network for network in networks),
                         'rejected': '拒绝' in line or 'reject' in line.lower(),
                         'detail': line[-280:]})
            if len(rows) == 5:
                return rows, ''
    return rows, ''

try:
    raw, text, section, stop, block, match, entries, wan = config()
    changed = False
    if action != 'read':
        network = ipaddress.IPv4Network(requested, strict=False)
        if action == 'add':
            if any(network == ipaddress.IPv4Network(item, strict=False) for item in entries):
                raise ValueError('该地址已在白名单中')
            entries.append(str(network))
        elif action == 'remove':
            index = next((i for i, item in enumerate(entries)
                          if network == ipaddress.IPv4Network(item, strict=False)), None)
            if index is None:
                raise ValueError('白名单中找不到该地址')
            entries.pop(index)
        else:
            raise ValueError('未知操作')
        newline = match.group(1) + ','.join(entries) + match.group(3)
        updated = text[:section.end()] + block[:match.start()] + newline + block[match.end():] + text[stop:]
        backup = cfg.with_name(cfg.name + '.bak')
        shutil.copy2(cfg, backup)
        fd, temp = tempfile.mkstemp(prefix='.xxsrv.ini.', dir=str(cfg.parent))
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(updated.encode('latin1'))
                handle.flush()
                os.fsync(handle.fileno())
            stat = cfg.stat()
            os.chmod(temp, stat.st_mode)
            os.chown(temp, stat.st_uid, stat.st_gid)
            os.replace(temp, cfg)
            for old_backup in cfg.parent.glob(cfg.name + '.bak*'):
                if old_backup != backup and old_backup.is_file():
                    old_backup.unlink()
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        changed = True
    rows, note = recent(entries)
    print(json.dumps({'ok': True, 'entries': entries, 'allow_wan': wan,
                      'recent': rows, 'log_note': note, 'changed': changed}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
    sys.exit(1)
'''


def normalize_entry(value):
    """Accept one IPv4 address or CIDR, rejecting host-bit ambiguity."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请输入 IPv4 地址或 CIDR 网段")
    try:
        if "/" in raw:
            return str(ipaddress.IPv4Network(raw, strict=True))
        return str(ipaddress.IPv4Address(raw)) + "/32"
    except ValueError as exc:
        raise ValueError("请输入有效的 IPv4 地址或 CIDR 网段") from exc


def run(connection, action, entry=""):
    if action not in ("read", "add", "remove"):
        raise ValueError("未知的白名单操作")
    command = "sudo -n python3 -c " + shlex.quote(REMOTE_SCRIPT)
    command += " " + shlex.quote(action) + " " + shlex.quote(entry)
    _, stdout, stderr = connection.exec_command(command, timeout=30)
    output = stdout.read().decode("utf-8", "replace").strip()
    error = stderr.read().decode("utf-8", "replace").strip()
    status = stdout.channel.recv_exit_status()
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(error or output or "读取 IP 白名单失败") from exc
    if status or not result.get("ok"):
        raise ValueError(result.get("error") or error or "修改 IP 白名单失败")
    return result
