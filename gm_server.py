"""Readable reconstruction of gm_server.py from the bundled Python 3.13 code.

This is a functional reconstruction, not the author's original source text.
It keeps the original API and database behavior. The optional system tray was
removed at the user's request. Review configuration before using a live game.
"""

import json
import math
import hashlib
import ipaddress
import io
import os
import re
import shlex
import socket
import struct
import sys
import subprocess
import threading
import tempfile
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import paramiko
from scripts.skill_limits import (CAREER_GROUP, CAREER_GROUP_NAMES, inspect_client_dpk,
                                  inspect_dpk_career_groups, inspect_dpk_skills,
                                  patch_client_dpk, patch_dpk_changes,
                                  refresh_client_session, touch_client_session,
                                  SESSIONS as _skill_limit_client_sessions,
                                  SESSION_LAST_USED as _skill_limit_session_last_used,
                                  SESSION_LOCK as _skill_limit_session_lock)
from scripts.summon_config import load_catalog, load_friend_catalog, patch_summon_dpk


FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
LOG_DIR = os.path.join(APP_DIR, "logs")
CACHE_DIR = os.path.join(APP_DIR, "data")
FATE_PACKAGE_CACHE_PATH = os.path.join(CACHE_DIR, "fate_info_package_cache.json")
SKILL_LIMIT_PROVENANCE_PATH = os.path.join(CACHE_DIR, "skill_limit_provenance.json")
SUMMON_CONFIG_PATH = os.path.join(CACHE_DIR, "summon_skill_config.json")
LOG_PATH = os.path.join(LOG_DIR, "ops.jsonl")
SERVER_LOG = os.path.join(LOG_DIR, "server.log")
CONFIG_PATH = os.path.join(APP_DIR, "gm_config.json")
RATE_FILE_PATH = "/opt/xxserver/runtime/xx/shm/TTYGZS/dynamicmb/server_set.txt"
FATE_INFO_FILE_PATH = "/opt/xxserver/runtime/xx/shm/TTYGZS/mb/quest/fate_info.txt"
SHOP_FILE_PATH = "/opt/xxserver/runtime/xx/shm/TTYGZS/dynamicmb/xy_shop_item.txt"
MB_DPK_FILE_PATH = "/opt/xxserver/runtime/xx/shm/TTYGZS/mb.dpk"
MB_DPK_DYNAMIC_FILE_PATH = os.path.join(os.path.dirname(MB_DPK_FILE_PATH), "dynamicmb", "mb.dpk")
FATE_DPK_ENTRY_PATH = "quest/fate_info.txt"
SHOP_CATEGORIES = [
    {"id": 1, "name": "卖场"}, {"id": 2, "name": "形象"},
    {"id": 3, "name": "法宝"}, {"id": 4, "name": "宠物"},
    {"id": 5, "name": "生产"}, {"id": 6, "name": "经验"},
    {"id": 7, "name": "强化"}, {"id": 8, "name": "丹药"},
    {"id": 9, "name": "其它"}, {"id": 10, "name": "VIP"},
]
DEFINITION_FILES = {
    "base": {
        "label": "基础定义表",
        "path": "/opt/xxserver/runtime/xx/shm/TTYGZS/mb/etc/define.txt",
    },
    "dynamic": {
        "label": "动态定义表",
        "path": "/opt/xxserver/runtime/xx/shm/TTYGZS/dynamicmb/define.txt",
    },
}

DEFAULT_CONFIG = {
    "listen_host": "127.0.0.1", "listen_port": 8899,
    "auto_open_browser": False,
    "ssh_host": "", "ssh_port": 22, "ssh_user": "vagrant",
    "ssh_key": "D:/x2ol/vmsetup/vagrant_key", "ssh_password": "vagrant",
    "mysql_user": "root", "mysql_password": "123456",
    "db_char": "xunxian_84", "db_ims": "xunxian_ims_84",
    "db_gis": "xunxian_gis_84", "db_lba": "xunxian_lba_84",
    "item_list": "D:/x2ol/mb_out/item/item_list.txt",
    "scn_info": "D:/x2ol/mb_out/scn/scn_info.txt",
    "client_mb_dpk": "",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            traceback.print_exc()
    else:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()
    return cfg


CFG = load_config()
for _directory in (LOG_DIR, CACHE_DIR):
    try:
        os.makedirs(_directory, exist_ok=True)
    except Exception:
        pass

KEY = CFG["ssh_key"]
ITEM_LIST = CFG["item_list"]
SCN_INFO = CFG["scn_info"]
MYSQL = shlex.quote("-u" + str(CFG["mysql_user"])) + (" " + shlex.quote("-p" + str(CFG["mysql_password"])) if CFG["mysql_password"] else "")
DB_CHAR = CFG["db_char"]
DB_IMS = CFG["db_ims"]
DB_GIS = CFG["db_gis"]
DB_LBA = CFG["db_lba"]
DBI_HOST, DBI_PORT = "127.0.0.1", 13724
_lock = threading.Lock()
_fate_package_cache_lock = threading.Lock()
_fate_package_source_cache = None
_grant_lock = threading.Lock()
_client = None
_shop_mutation_lock = threading.Lock()
_shop_restart_lock = threading.Lock()
_shop_restart_state = {"running": False, "phase": "", "error": "", "updated_at": 0}
_skill_limit_server_sessions = {}
_skill_limit_server_sessions_lock = threading.RLock()
SKILL_LIMIT_SESSION_TTL_SECONDS = 60 * 60
_skill_limit_provenance_lock = threading.Lock()


def _cleanup_expired_skill_limit_sessions():
    """Remove local DPK working copies for sessions idle for at least one hour."""
    now = time.time()
    cutoff = now - SKILL_LIMIT_SESSION_TTL_SECONDS
    with _skill_limit_session_lock:
        expired = [session_id for session_id, last_used in
                   _skill_limit_session_last_used.items() if last_used <= cutoff]
        with _skill_limit_server_sessions_lock:
            for session_id in expired:
                client_path = _skill_limit_client_sessions.pop(session_id, None)
                _skill_limit_session_last_used.pop(session_id, None)
                server_session = _skill_limit_server_sessions.pop(session_id, None)
                paths = [client_path]
                if server_session:
                    paths.extend(server_session.get("local_paths", {}).values())
                for path in paths:
                    if path:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except OSError:
                            pass
                # Also remove older per-session patched snapshots from repeated applies.
                prefix = f"skill-limits-{session_id}"
                try:
                    for path in Path(CACHE_DIR).glob(prefix + "*.dpk"):
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
            active = set(_skill_limit_session_last_used)
            # Also collect orphaned files left behind by a previous process run.
            # Their in-memory session no longer exists, so use file age instead.
            try:
                for path in Path(CACHE_DIR).glob("skill-limits-*.dpk"):
                    match = re.match(r"^skill-limits-([0-9a-f]{32})(?:-|\.dpk$)", path.name)
                    if not match or match.group(1) in active:
                        continue
                    try:
                        if path.stat().st_mtime <= cutoff:
                            path.unlink(missing_ok=True)
                    except OSError:
                        pass
            except OSError:
                pass
    return len(expired)


def _skill_limit_cleanup_worker():
    while True:
        time.sleep(60)
        try:
            _cleanup_expired_skill_limit_sessions()
        except Exception:
            traceback.print_exc()

CONFIG_KEYS = frozenset(DEFAULT_CONFIG)
SECRET_KEYS = frozenset(("ssh_password", "mysql_password"))


def _skill_limit_provenance_key(client_path):
    canonical_path = os.path.normcase(os.path.abspath(os.path.expanduser(str(client_path))))
    return hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()


def _load_skill_limit_provenance():
    try:
        with open(SKILL_LIMIT_PROVENANCE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_skill_limit_provenance(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    temporary = SKILL_LIMIT_PROVENANCE_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
    os.replace(temporary, SKILL_LIMIT_PROVENANCE_PATH)
DB_KEYS = frozenset(("db_char", "db_ims", "db_gis", "db_lba"))


def public_config():
    data = {key: CFG.get(key) for key in CONFIG_KEYS - SECRET_KEYS}
    data["ssh_password_set"] = bool(CFG.get("ssh_password"))
    data["mysql_password_set"] = bool(CFG.get("mysql_password"))
    return data


def update_config(changes):
    """Validate and persist web settings, then refresh the active SSH/DB state."""
    global MYSQL, DB_CHAR, DB_IMS, DB_GIS, DB_LBA, ITEM_LIST, SCN_INFO
    global _items, _scenes, _partitions
    if not isinstance(changes, dict):
        raise ValueError("配置必须是 JSON 对象")
    unknown = set(changes) - CONFIG_KEYS
    if unknown:
        raise ValueError("未知配置项：" + ", ".join(sorted(unknown)))
    candidate = dict(CFG)
    for key, value in changes.items():
        if key in SECRET_KEYS and value == "":
            continue  # Empty password field in the page means keep existing value.
        candidate[key] = value
    for key in ("ssh_host", "listen_host"):
        value = str(candidate.get(key) or "").strip()
        if key == "ssh_host" and not value:
            raise ValueError("请填写服务端 IP")
        if key == "listen_host" and not value:
            raise ValueError("请填写网页监听地址")
        try:
            ipaddress.ip_address(value)
        except ValueError as e:
            raise ValueError(f"{key} 必须是有效的 IP 地址") from e
        candidate[key] = value
    if not ipaddress.ip_address(candidate["listen_host"]).is_loopback:
        raise ValueError("网页控制台没有登录验证，监听地址必须是本机回环 IP")
    for key in ("ssh_port", "listen_port"):
        try:
            value = int(candidate[key])
        except (TypeError, ValueError) as e:
            raise ValueError(f"{key} 必须是端口号") from e
        if not 1 <= value <= 65535:
            raise ValueError(f"{key} 必须在 1–65535 之间")
        candidate[key] = value
    for key in ("ssh_user", "mysql_user"):
        value = str(candidate.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} 不能为空")
        candidate[key] = value
    for key in DB_KEYS:
        value = str(candidate.get(key) or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", value):
            raise ValueError(f"{key} 只能包含字母、数字和下划线")
        candidate[key] = value
    for key in ("ssh_key", "item_list", "scn_info", "client_mb_dpk"):
        candidate[key] = os.path.expanduser(str(candidate.get(key) or "").strip())
    if not isinstance(candidate["auto_open_browser"], bool):
        raise ValueError("auto_open_browser 必须是布尔值")
    for key in SECRET_KEYS:
        candidate[key] = str(candidate.get(key) or "")

    changed = sorted(key for key in CONFIG_KEYS if candidate[key] != CFG.get(key))
    restart_required = any(key in changed for key in ("listen_host", "listen_port"))
    if changed:
        fd, temp_path = tempfile.mkstemp(prefix=".gm_config_", suffix=".json", dir=APP_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(candidate, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, CONFIG_PATH)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        with _lock:
            reset_ssh()
            CFG.clear()
            CFG.update(candidate)
            MYSQL = shlex.quote("-u" + CFG["mysql_user"]) + (" " + shlex.quote("-p" + CFG["mysql_password"]) if CFG["mysql_password"] else "")
            DB_CHAR, DB_IMS, DB_GIS, DB_LBA = (CFG[k] for k in ("db_char", "db_ims", "db_gis", "db_lba"))
            ITEM_LIST, SCN_INFO = CFG["item_list"], CFG["scn_info"]
            _items = _scenes = _partitions = None
    return {"config": public_config(), "changed": changed, "restart_required": restart_required}


def choose_local_file(field):
    choices = {
        "item_list": ("选择物品列表文件", "Text files (*.txt)|*.txt|All files (*.*)|*.*"),
        "scn_info": ("选择场景列表文件", "Text files (*.txt)|*.txt|All files (*.*)|*.*"),
        "client_mb_dpk": ("选择客户端 mb.dpk", "DPK files (*.dpk)|*.dpk|All files (*.*)|*.*"),
    }
    if field not in choices:
        raise ValueError("不支持选择此配置路径")
    title, file_filter = choices[field]
    if sys.platform == "darwin":
        escaped_title = title.replace('"', '\\"')
        result = subprocess.run(
            ["osascript", "-e", f'POSIX path of (choose file with prompt "{escaped_title}")'],
            capture_output=True, text=True, timeout=180)
        if result.returncode:
            if "User canceled" in result.stderr:
                return None
            raise RuntimeError(result.stderr.strip() or "打开文件选择器失败")
        selected = result.stdout.strip()
    elif os.name == "nt":
        ps_filter = file_filter.replace("'", "''")
        ps_title = title.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            f"$d.Title = '{ps_title}'; $d.Filter = '{ps_filter}'; "
            "if ($d.ShowDialog() -eq 'OK') { [Console]::Write($d.FileName) }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "打开文件选择器失败")
        selected = result.stdout.strip()
    else:
        import shutil
        chooser = shutil.which("zenity") or shutil.which("kdialog")
        if not chooser:
            raise RuntimeError("当前系统没有可用的图形文件选择器")
        if chooser.endswith("zenity"):
            result = subprocess.run(
                [chooser, "--file-selection", f"--title={title}", f"--file-filter={file_filter}"],
                capture_output=True, text=True, timeout=180)
        else:
            result = subprocess.run(
                [chooser, "--getopenfilename", "", file_filter],
                capture_output=True, text=True, timeout=180)
        if result.returncode:
            if result.returncode == 1:
                return None
            raise RuntimeError(result.stderr.strip() or "打开文件选择器失败")
        selected = result.stdout.strip()
    return os.path.normpath(selected) if selected else None


def ssh():
    global _client
    if _client is None:
        host = str(CFG.get("ssh_host") or "").strip()
        if not host:
            raise ValueError("请在 gm_config.json 中设置服务端 IP：ssh_host")
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(hostname=host, port=int(CFG["ssh_port"]),
                      username=CFG["ssh_user"], timeout=8,
                      allow_agent=False, look_for_keys=False)
        key = CFG.get("ssh_key") or ""
        if key and os.path.exists(key):
            c.connect(key_filename=key, **kwargs)
        else:
            c.connect(password=str(CFG.get("ssh_password") or "vagrant"), **kwargs)
        _client = c
    return _client


def reset_ssh():
    global _client
    try:
        if _client:
            _client.close()
    except Exception:
        pass
    _client = None


def _unescape(field):
    out, i, n = [], 0, len(field)
    while i < n:
        ch = field[i]
        if ch == "\\" and i + 1 < n:
            nxt = field[i + 1]
            out.append({"t": "\t", "n": "\n", "0": "\x00", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def sql(statement, timeout=30):
    """Execute one SQL statement through the VM's SSH service."""
    cmd = (
        "sudo mysql --protocol=TCP --host=127.0.0.1 --port=3306 "
        + MYSQL + " -N -B --default-character-set=utf8 -e "
        + shlex.quote(statement)
    )
    with _lock:
        for attempt in (0, 1):
            try:
                c = ssh()
                stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
                out = stdout.read().decode("utf-8", "replace")
                err = stderr.read().decode("utf-8", "replace")
                rc = stdout.channel.recv_exit_status()
                if rc != 0 and "Access denied" not in err and not err.strip().startswith("mysql:"):
                    raise RuntimeError(err.strip() or f"mysql exit {rc}")
                if rc != 0:
                    raise RuntimeError(err.strip())
                rows = []
                for line in out.split("\n"):
                    if line != "":
                        rows.append([_unescape(x) for x in line.split("\t")])
                return rows
            except Exception:
                reset_ssh()
                if attempt:
                    raise
    return []


def _read_rate_file():
    """Read the live hourly rate table from the configured game server."""
    for attempt in range(2):
        try:
            with _lock:
                c = ssh()
                stdin, stdout, stderr = c.exec_command(
                    "sudo -n cat -- " + shlex.quote(RATE_FILE_PATH), timeout=15)
                data = stdout.read()
                err = stderr.read().decode("utf-8", "replace").strip()
                rc = stdout.channel.recv_exit_status()
            if rc != 0:
                raise RuntimeError(err or "读取倍率配置失败")
            break
        except Exception:
            reset_ssh()
            if attempt:
                raise
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError as e:
        raise RuntimeError("倍率配置文件编码不是 GB18030，已停止读取") from e


def _parse_rate_file(source):
    rates = {"exp": [None] * 24, "money": [None] * 24}
    ids = set()
    for line in source.splitlines():
        match = re.match(r"^\s*(\d+)\s+([^\s]+)", line)
        if not match:
            continue
        row_id = int(match.group(1))
        if 1 <= row_id <= 24:
            group, hour = "exp", row_id - 1
        elif 25 <= row_id <= 48:
            group, hour = "money", row_id - 25
        else:
            continue
        if row_id in ids:
            raise RuntimeError(f"倍率配置 ID {row_id} 重复")
        try:
            value = float(match.group(2))
        except ValueError as e:
            raise RuntimeError(f"倍率配置 ID {row_id} 不是数字") from e
        if not (value >= 0 and value < float("inf")):
            raise RuntimeError(f"倍率配置 ID {row_id} 不是有效的非负数")
        rates[group][hour] = value
        ids.add(row_id)
    missing = sorted(set(range(1, 49)) - ids)
    if missing:
        raise RuntimeError("倍率配置缺少 ID：" + ", ".join(map(str, missing)))
    return rates


def get_game_rates():
    source = _read_rate_file()
    rates = _parse_rate_file(source)
    return {"exp": rates["exp"][0], "money": rates["money"][0],
            "path": RATE_FILE_PATH}


def _replace_rate_values(source, values):
    patched = []
    seen = set()
    for line in source.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = re.match(r"^(\s*)(\d+)(\s+)([^\s]+)(.*)$", content)
        if not match:
            patched.append(line)
            continue
        row_id = int(match.group(2))
        if 1 <= row_id <= 24:
            value = values["exp"][row_id - 1]
        elif 25 <= row_id <= 48:
            value = values["money"][row_id - 25]
        else:
            patched.append(line)
            continue
        if row_id in seen:
            raise RuntimeError(f"倍率配置 ID {row_id} 重复，已停止写入")
        seen.add(row_id)
        ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        patched.append(match.group(1) + match.group(2) + match.group(3) + value +
                       match.group(5) + ending)
    if seen != set(range(1, 49)):
        raise RuntimeError("倍率配置结构发生变化，已停止写入")
    return "".join(patched)


def set_game_rates(exp, money):
    values = {"exp": [], "money": []}
    for key, raw, label in (("exp", exp, "经验"), ("money", money, "掉钱")):
        if isinstance(raw, bool):
            raise ValueError(f"{label}倍率无效")
        try:
            value = float(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{label}倍率必须是数字") from e
        if not (0 <= value <= 100000 and value < float("inf")):
            raise ValueError(f"{label}倍率需在 0 到 100000 之间")
        values[key] = [format(value, ".8g")] * 24

    source = _read_rate_file()
    _parse_rate_file(source)  # Refuse to overwrite an unexpected server file.
    updated = _replace_rate_values(source, values).encode("gb18030")
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    backup = RATE_FILE_PATH + ".bak." + stamp
    temp = RATE_FILE_PATH + ".tmp"
    command = ("sudo -n sh -c " + shlex.quote(
        "set -e; cp -p " + shlex.quote(RATE_FILE_PATH) + " " + shlex.quote(backup) +
        "; cat > " + shlex.quote(temp) +
        "; chmod --reference=" + shlex.quote(RATE_FILE_PATH) + " " + shlex.quote(temp) +
        "; chown --reference=" + shlex.quote(RATE_FILE_PATH) + " " + shlex.quote(temp) +
        "; mv -f " + shlex.quote(temp) + " " + shlex.quote(RATE_FILE_PATH)))
    with _lock:
        c = ssh()
        stdin, stdout, stderr = c.exec_command(command, timeout=30)
        stdin.write(updated)
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc != 0:
        raise RuntimeError(err or out or "写入倍率配置失败")
    confirmed = _parse_rate_file(_read_rate_file())
    return {"backup": backup, "exp": confirmed["exp"][0],
            "money": confirmed["money"][0]}


def _decode_fate_info(data):
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError as e:
        raise RuntimeError("天劫等级锁配置编码不是 GB18030，已停止读取") from e


def _read_fate_package_source():
    global _fate_package_source_cache
    with _fate_package_cache_lock:
        if _fate_package_source_cache is not None:
            return _fate_package_source_cache
        cached = None
        try:
            with open(FATE_PACKAGE_CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, ValueError, TypeError):
            pass
        with _lock:
            c = ssh()
            sftp = c.open_sftp()
            try:
                stat = sftp.stat(MB_DPK_FILE_PATH)
                signature = {"size": stat.st_size, "mtime": stat.st_mtime}
                if (isinstance(cached, dict) and cached.get("signature") == signature
                        and isinstance(cached.get("source"), str)):
                    _fate_package_source_cache = cached["source"]
                    return _fate_package_source_cache
                archive_stream = io.BytesIO()
                sftp.getfo(MB_DPK_FILE_PATH, archive_stream)
                archive = archive_stream.getvalue()
            finally:
                sftp.close()
        from scripts.dpk_reader import extract_dpk_entry
        data = extract_dpk_entry(archive, FATE_DPK_ENTRY_PATH)
        _fate_package_source_cache = _decode_fate_info(data)
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            temp_path = FATE_PACKAGE_CACHE_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"signature": signature, "source": _fate_package_source_cache},
                          f, ensure_ascii=False)
            os.replace(temp_path, FATE_PACKAGE_CACHE_PATH)
        except OSError:
            pass
        return _fate_package_source_cache


def _read_fate_info_source():
    with _lock:
        c = ssh()
        _, stdout, stderr = c.exec_command(
            "sudo -n cat -- " + shlex.quote(FATE_INFO_FILE_PATH), timeout=20)
        data = stdout.read()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc == 0:
        return _decode_fate_info(data), "overlay"
    reset_ssh()
    if "No such file or directory" not in err:
        raise RuntimeError(err or "读取天劫等级锁配置失败")

    try:
        return _read_fate_package_source(), "package"
    except Exception as e:
        raise RuntimeError("松散覆盖文件不存在，且无法从 mb.dpk 读取原表：" + str(e)) from e


def _read_fate_info_file():
    return _read_fate_info_source()[0]


def _fate_info_row(line, line_number):
    if "\t" in line:
        columns = line.split("\t")
        if len(columns) < 2 or not columns[1].strip().isdigit():
            return None
        if len(columns) < 3:
            raise RuntimeError(f"fate_info.txt 第 {line_number} 行不足 3 列")
        third_start = len(columns[0]) + len(columns[1]) + 2
        lock_field = columns[2]
        offset = third_start
    else:
        fields = list(re.finditer(r"\S+", line))
        if len(fields) < 2 or not fields[1].group().isdigit():
            return None
        if len(fields) < 3:
            raise RuntimeError(f"fate_info.txt 第 {line_number} 行不足 3 列")
        return {"id": fields[1].group(), "level": int(fields[2].group()),
                "span": fields[2].span()}

    lock_match = re.search(r"\S+", lock_field)
    if not lock_match:
        raise RuntimeError(f"fate_info.txt 第 {line_number} 行第 3 列为空")
    try:
        level = int(lock_match.group())
    except ValueError as e:
        raise RuntimeError(f"fate_info.txt 第 {line_number} 行第 3 列不是整数") from e
    return {"id": columns[1].strip(), "level": level,
            "span": (offset + lock_match.start(), offset + lock_match.end())}


def _parse_fate_info_file(source):
    rows = []
    for line_number, line in enumerate(source.splitlines(), 1):
        try:
            row = _fate_info_row(line, line_number)
        except ValueError as e:
            raise RuntimeError(f"fate_info.txt 第 {line_number} 行第 3 列不是整数") from e
        if row:
            rows.append({"id": row["id"], "level": row["level"]})
    if not rows:
        raise RuntimeError("fate_info.txt 没有可识别的等级锁配置行")
    return rows


def _fate_lock_summary(rows):
    levels = {}
    for row in rows:
        level = row["level"]
        levels[level] = levels.get(level, 0) + 1
    return [{"level": level, "count": count}
            for level, count in sorted(levels.items())]


def get_fate_locks():
    source, origin = _read_fate_info_source()
    rows = _parse_fate_info_file(source)
    try:
        package_rows = _parse_fate_info_file(_read_fate_package_source())
        known_levels = sorted({row["level"] for row in package_rows if row["level"] != 200})
    except Exception:
        known_levels = []
    known_levels = sorted(set(known_levels) | {row["level"] for row in rows if row["level"] != 200})
    return {"path": FATE_INFO_FILE_PATH, "total": len(rows),
            "levels": _fate_lock_summary(rows), "known_levels": known_levels,
            "origin": origin}


def _replace_fate_info_locks(source, target_level=None, restore_ids=None):
    updated = []
    changed = 0
    total = 0
    for line_number, line in enumerate(source.splitlines(keepends=True), 1):
        content = line.rstrip("\r\n")
        try:
            row = _fate_info_row(content, line_number)
        except ValueError as e:
            raise RuntimeError(f"fate_info.txt 第 {line_number} 行第 3 列不是整数，已停止写入") from e
        if not row:
            updated.append(line)
            continue
        total += 1
        if restore_ids is not None:
            if row["id"] not in restore_ids or row["level"] != 200:
                updated.append(line)
                continue
            replacement = str(target_level)
        else:
            if target_level is not None and row["level"] != target_level:
                updated.append(line)
                continue
            replacement = "200"
        start, end = row["span"]
        if content[start:end] == replacement:
            updated.append(line)
            continue
        newline = line[len(content):]
        updated.append(content[:start] + replacement + content[end:] + newline)
        changed += 1
    if total == 0:
        raise RuntimeError("fate_info.txt 没有可识别的等级锁配置行，已停止写入")
    return "".join(updated), changed, total


def _replace_fate_info_states(source, changes, restore_ids):
    updated = []
    changed = 0
    total = 0
    for line_number, line in enumerate(source.splitlines(keepends=True), 1):
        content = line.rstrip("\r\n")
        try:
            row = _fate_info_row(content, line_number)
        except ValueError as e:
            raise RuntimeError(f"fate_info.txt 第 {line_number} 行第 3 列不是整数，已停止写入") from e
        if not row:
            updated.append(line)
            continue
        total += 1
        replacement = None
        for level, enabled in changes.items():
            if enabled and row["level"] == 200 and row["id"] in restore_ids.get(level, set()):
                replacement = str(level)
                break
            if not enabled and row["level"] == level:
                replacement = "200"
                break
        if replacement is None or content[row["span"][0]:row["span"][1]] == replacement:
            updated.append(line)
            continue
        start, end = row["span"]
        newline = line[len(content):]
        updated.append(content[:start] + replacement + content[end:] + newline)
        changed += 1
    if total == 0:
        raise RuntimeError("fate_info.txt 没有可识别的等级锁配置行，已停止写入")
    return "".join(updated), changed, total


def set_fate_locks(target_level=None, enabled=False, changes=None):
    source, origin = _read_fate_info_source()
    original_rows = _parse_fate_info_file(source)
    try:
        package_rows = _parse_fate_info_file(_read_fate_package_source())
    except Exception:
        package_rows = []
    known_levels = sorted(
        {row["level"] for row in package_rows if row["level"] != 200} |
        {row["level"] for row in original_rows if row["level"] != 200})
    if changes is None:
        if target_level is None:
            changes = None
        else:
            changes = {target_level: enabled}
    restore_ids = {}
    if changes is not None:
        for level, should_enable in changes.items():
            if should_enable:
                ids = {row["id"] for row in package_rows if row["level"] == level}
                if not ids:
                    raise ValueError(f"原始定义中不存在 {level} 级等级锁")
                restore_ids[level] = ids
            elif level not in {row["level"] for row in original_rows}:
                raise ValueError(f"当前配置中不存在 {level} 级等级锁")
        updated, changed, total = _replace_fate_info_states(source, changes, restore_ids)
    else:
        updated, changed, total = _replace_fate_info_locks(source)
    if changed == 0:
        rows = original_rows
        return {"changed": 0, "total": total, "backup": "",
                "levels": _fate_lock_summary(rows),
                "known_levels": known_levels,
                "created_overlay": False, "origin": origin}

    path = FATE_INFO_FILE_PATH
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    backup = path + ".bak." + stamp
    temp = path + ".tmp"
    if origin == "overlay":
        command = ("sudo -n sh -c " + shlex.quote(
            "set -e; cp -p " + shlex.quote(path) + " " + shlex.quote(backup) +
            "; cat > " + shlex.quote(temp) +
            "; chmod --reference=" + shlex.quote(path) + " " + shlex.quote(temp) +
            "; chown --reference=" + shlex.quote(path) + " " + shlex.quote(temp) +
            "; mv -f " + shlex.quote(temp) + " " + shlex.quote(path)))
        payload = updated.encode("gb18030")
    else:
        import base64
        original_b64 = base64.b64encode(source.encode("gb18030")).decode("ascii")
        updated_b64 = base64.b64encode(updated.encode("gb18030")).decode("ascii")
        parent = os.path.dirname(path)
        script = (
            "set -e; mkdir -p -- " + shlex.quote(parent) +
            "; test ! -e " + shlex.quote(path) +
            "; printf %s " + shlex.quote(original_b64) + " | base64 -d > " + shlex.quote(backup) +
            "; chmod 644 " + shlex.quote(backup) +
            "; printf %s " + shlex.quote(updated_b64) + " | base64 -d > " + shlex.quote(temp) +
            "; chmod 644 " + shlex.quote(temp) +
            "; mv -f " + shlex.quote(temp) + " " + shlex.quote(path))
        command = "sudo -n sh -c " + shlex.quote(script)
        payload = b""
    with _lock:
        c = ssh()
        stdin, stdout, stderr = c.exec_command(command, timeout=30)
        if payload:
            stdin.write(payload)
            stdin.flush()
            stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc != 0:
        reset_ssh()
        raise RuntimeError(err or out or "保存天劫等级锁配置失败")

    confirmed = _parse_fate_info_file(_read_fate_info_file())
    if changes is None:
        expected = [200 for _row in original_rows]
    else:
        expected = []
        for row in original_rows:
            expected_level = row["level"]
            for level, should_enable in changes.items():
                if should_enable and row["id"] in restore_ids.get(level, set()) and expected_level == 200:
                    expected_level = level
                    break
                if not should_enable and expected_level == level:
                    expected_level = 200
                    break
            expected.append(expected_level)
    if len(confirmed) != len(expected) or [row["level"] for row in confirmed] != expected:
        raise RuntimeError("服务器回读校验失败：等级锁值与预期不符")
    return {"changed": changed, "total": total, "backup": backup,
            "levels": _fate_lock_summary(confirmed),
            "known_levels": known_levels,
            "created_overlay": origin == "package", "origin": "overlay"}


def _read_definition_file(kind):
    config = DEFINITION_FILES.get(kind)
    if not config:
        raise ValueError("定义表类型无效")
    with _lock:
        c = ssh()
        stdin, stdout, stderr = c.exec_command(
            "sudo -n cat -- " + shlex.quote(config["path"]), timeout=20)
        data = stdout.read()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc != 0:
        reset_ssh()
        raise RuntimeError(err or f"读取{config['label']}失败")
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError as e:
        raise RuntimeError(f"{config['label']}编码不是 GB18030，已停止读取") from e


def _parse_definition_file(source, kind):
    config = DEFINITION_FILES.get(kind)
    if not config:
        raise ValueError("定义表类型无效")
    rows = []
    seen = set()
    for line in source.splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0].strip().isdigit():
            continue
        row_id = int(fields[0].strip())
        if row_id in seen:
            raise RuntimeError(f"{config['label']}编号 {row_id} 重复")
        seen.add(row_id)
        rows.append({"id": row_id, "value": fields[1],
                     "description": fields[2] if len(fields) > 2 else ""})
    if not rows:
        raise RuntimeError(f"{config['label']}没有可识别的配置项目")
    return rows


def get_definitions(kind):
    config = DEFINITION_FILES.get(kind)
    if not config:
        raise ValueError("定义表类型无效")
    source = _read_definition_file(kind)
    return {"kind": kind, "label": config["label"], "path": config["path"],
            "rows": _parse_definition_file(source, kind)}


def _replace_definition_values(source, kind, values):
    rows = _parse_definition_file(source, kind)
    known = {row["id"] for row in rows}
    unknown = set(values) - known
    if unknown:
        raise ValueError("配置编号不存在：" + ", ".join(map(str, sorted(unknown))))
    updated = []
    seen = set()
    for line in source.splitlines(keepends=True):
        ending = ""
        content = line
        if content.endswith("\r\n"):
            content, ending = content[:-2], "\r\n"
        elif content.endswith("\n"):
            content, ending = content[:-1], "\n"
        fields = content.split("\t")
        if len(fields) >= 2 and fields[0].strip().isdigit():
            row_id = int(fields[0].strip())
            if row_id in values:
                fields[1] = values[row_id]
                seen.add(row_id)
                content = "\t".join(fields)
        updated.append(content + ending)
    if seen != set(values):
        raise RuntimeError("定义表结构在保存时发生变化，已停止写入")
    return "".join(updated)


def set_definitions(kind, values):
    config = DEFINITION_FILES.get(kind)
    if not config:
        raise ValueError("定义表类型无效")
    if not isinstance(values, dict) or not values:
        raise ValueError("没有需要保存的配置项目")
    parsed = {}
    for raw_id, raw_value in values.items():
        try:
            row_id = int(raw_id)
        except (TypeError, ValueError) as e:
            raise ValueError("配置编号无效") from e
        if isinstance(raw_value, (dict, list, bool)) or raw_value is None:
            raise ValueError(f"配置 {row_id} 的数据无效")
        value = str(raw_value)
        if len(value) > 512 or any(ch in value for ch in ("\t", "\r", "\n", "\x00")):
            raise ValueError(f"配置 {row_id} 的数据格式无效")
        try:
            value.encode("gb18030")
        except UnicodeEncodeError as e:
            raise ValueError(f"配置 {row_id} 含有服务端不支持的字符") from e
        parsed[row_id] = value

    path = config["path"]
    source = _read_definition_file(kind)
    _parse_definition_file(source, kind)
    updated = _replace_definition_values(source, kind, parsed).encode("gb18030")
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    backup = path + ".bak." + stamp
    temp = path + ".tmp"
    command = ("sudo -n sh -c " + shlex.quote(
        "set -e; cp -p " + shlex.quote(path) + " " + shlex.quote(backup) +
        "; cat > " + shlex.quote(temp) +
        "; chmod --reference=" + shlex.quote(path) + " " + shlex.quote(temp) +
        "; chown --reference=" + shlex.quote(path) + " " + shlex.quote(temp) +
        "; mv -f " + shlex.quote(temp) + " " + shlex.quote(path)))
    with _lock:
        c = ssh()
        stdin, stdout, stderr = c.exec_command(command, timeout=30)
        stdin.write(updated)
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc != 0:
        reset_ssh()
        raise RuntimeError(err or out or f"保存{config['label']}失败")
    confirmed = _parse_definition_file(_read_definition_file(kind), kind)
    confirmed_by_id = {row["id"]: row["value"] for row in confirmed}
    mismatched = [row_id for row_id, value in parsed.items()
                  if confirmed_by_id.get(row_id) != value]
    if mismatched:
        raise RuntimeError("服务器回读校验失败：" + ", ".join(map(str, mismatched)))
    return {"kind": kind, "backup": backup, "saved": len(parsed),
            "rows": confirmed}


def _shop_read():
    with _lock:
        c = ssh()
        _, stdout, stderr = c.exec_command(
            "sudo -n cat -- " + shlex.quote(SHOP_FILE_PATH), timeout=20)
        raw = stdout.read()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc != 0:
        reset_ssh()
        raise RuntimeError(err or "读取商城配置失败")
    try:
        text = raw.decode("gb18030")
    except UnicodeDecodeError as e:
        raise RuntimeError("商城配置编码不是 GB18030，已停止读取") from e
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise RuntimeError("商城配置缺少表头，已停止操作")
    return lines


def _shop_rows(lines, query="", limit=300):
    q = str(query or "").strip().casefold()
    rows = []
    for line_no, line in enumerate(lines[1:], start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 12:
            continue
        name, item_id = fields[0].strip(), fields[1].strip()
        if q and q not in name.casefold() and q not in item_id:
            continue
        category_id, separator, subcategory_id = fields[11].strip().partition("*")
        try:
            category = next((item["name"] for item in SHOP_CATEGORIES
                             if str(item["id"]) == category_id), category_id)
        except Exception:
            category = category_id
        rows.append({"line": line_no, "name": name, "id": item_id,
                     "count": fields[2].strip(), "price": fields[4].strip(),
                     "category_id": category_id,
                     "subcategory_id": subcategory_id if separator else "",
                     "category": category})
    return rows[-limit:]


def get_shop(query=""):
    lines = _shop_read()
    matched = _shop_rows(lines, query, limit=100000)
    return {"path": SHOP_FILE_PATH, "categories": SHOP_CATEGORIES,
            "total": len(matched), "rows": matched[-300:]}


def _shop_write(lines):
    text = "\r\n".join(lines) + "\r\n"
    try:
        data = text.encode("gb18030")
    except UnicodeEncodeError as e:
        raise ValueError("商城数据含有服务端不支持的字符") from e
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    backup = SHOP_FILE_PATH + ".bak-" + stamp
    temp = SHOP_FILE_PATH + ".tmp-" + stamp
    command = ("sudo -n sh -c " + shlex.quote(
        "set -e; cp -p " + shlex.quote(SHOP_FILE_PATH) + " " + shlex.quote(backup) +
        "; cat > " + shlex.quote(temp) +
        "; chmod --reference=" + shlex.quote(SHOP_FILE_PATH) + " " + shlex.quote(temp) +
        "; chown --reference=" + shlex.quote(SHOP_FILE_PATH) + " " + shlex.quote(temp) +
        "; mv -f " + shlex.quote(temp) + " " + shlex.quote(SHOP_FILE_PATH)))
    with _lock:
        c = ssh()
        stdin, stdout, stderr = c.exec_command(command, timeout=30)
        stdin.write(data)
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        rc = stdout.channel.recv_exit_status()
    if rc != 0:
        reset_ssh()
        raise RuntimeError(err or out or "保存商城配置失败")
    if _shop_read() != lines:
        raise RuntimeError("商城配置写入后回读不一致；原文件备份已保留")
    return backup


def add_shop_item(item_id, price, count, category_id, subcategory_id):
    item_id = qint(item_id, -1)
    price = qint(price, -1)
    count = qint(count, -1)
    category_id = qint(category_id, -1)
    subcategory_id = qint(subcategory_id, -1)
    if item_id < 1:
        raise ValueError("物品 ID 无效")
    if price < 0 or price > 2147483647:
        raise ValueError("仙玉价格需为 0–2147483647 的整数")
    if count < 1 or count > 99999:
        raise ValueError("每组数量需为 1–99999 的整数")
    if category_id not in {entry["id"] for entry in SHOP_CATEGORIES}:
        raise ValueError("商城分类无效")
    if not 1 <= subcategory_id <= 999:
        raise ValueError("子组需为 1–999 的整数")
    catalog = next((item for item in load_items() if item["id"] == item_id), None)
    if not catalog:
        raise ValueError("物品 ID 不在当前物品表中")

    with _shop_mutation_lock:
        lines = _shop_read()
        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) > 1 and fields[1].strip() == str(item_id):
                raise ValueError(f"该物品已在商城中（物品 ID {item_id}）")
        while len(lines) > 1 and not lines[-1].strip("\r \t"):
            lines.pop()
        row = [catalog["name"], str(item_id), str(count), "", str(price), "",
               "", "", "", "", "", f"{category_id}*{subcategory_id}", "", "", ""]
        lines.append("\t".join(row))
        backup = _shop_write(lines)
    return {"name": catalog["name"], "id": str(item_id), "price": price,
            "count": count, "category_id": category_id,
            "category": next(entry["name"] for entry in SHOP_CATEGORIES
                             if entry["id"] == category_id),
            "subcategory_id": subcategory_id, "total": len(_shop_rows(lines, limit=100000)),
            "backup": backup}


def delete_shop_item(item_id):
    item_id = str(item_id or "").strip()
    if not item_id.isdigit():
        raise ValueError("物品 ID 无效")
    with _shop_mutation_lock:
        lines = _shop_read()
        found = None
        for index, line in enumerate(lines[1:], start=1):
            fields = line.split("\t")
            if len(fields) > 1 and fields[1].strip() == item_id:
                found = index
                break
        if found is None:
            raise ValueError(f"商城中没有物品 ID {item_id}")
        fields = lines[found].split("\t")
        name = fields[0].strip()
        del lines[found]
        while len(lines) > 1 and not lines[-1].strip("\r \t"):
            lines.pop()
        backup = _shop_write(lines)
    return {"name": name, "id": item_id,
            "total": len(_shop_rows(lines, limit=100000)), "backup": backup}


def delete_shop_items(item_ids):
    if not isinstance(item_ids, list) or not item_ids:
        raise ValueError("请先选择要下架的商品")
    ids = [str(item_id or "").strip() for item_id in item_ids]
    if any(not item_id.isdigit() for item_id in ids):
        raise ValueError("商品 ID 列表无效")
    targets = set(ids)
    with _shop_mutation_lock:
        lines = _shop_read()
        found = {}
        keep = [lines[0]]
        for line in lines[1:]:
            fields = line.split("\t")
            row_id = fields[1].strip() if len(fields) > 1 else ""
            if row_id in targets and row_id not in found:
                found[row_id] = fields[0].strip()
                continue
            keep.append(line)
        missing = sorted(targets - set(found))
        if missing:
            raise ValueError("商城中找不到商品 ID：" + ", ".join(missing))
        while len(keep) > 1 and not keep[-1].strip("\r \t"):
            keep.pop()
        backup = _shop_write(keep)
    return {"deleted": [{"id": item_id, "name": found[item_id]}
                         for item_id in ids if item_id in found],
            "total": len(_shop_rows(keep, limit=100000)), "backup": backup}


def _server_action_worker(action, source):
    global _shop_restart_state
    try:
        _shop_restart_state.update({"phase": "正在关闭游戏服务端" if action == "stop" else "正在停止游戏服务端", "error": "",
                                    "updated_at": int(time.time())})
        c = ssh()
        stop_cmd = "sudo -n runuser -u mc -- /opt/xxserver/stop.sh --clients-exited"
        _, stdout, stderr = c.exec_command(stop_cmd, timeout=240)
        stop_out = stdout.read().decode("utf-8", "replace").strip()
        stop_err = stderr.read().decode("utf-8", "replace").strip()
        stop_rc = stdout.channel.recv_exit_status()
        errors = []
        if stop_rc != 0:
            errors.append("停止服务端失败：" + (stop_err or stop_out or f"退出码 {stop_rc}"))
        if action == "restart":
            _shop_restart_state.update({"phase": "正在启动游戏服务端", "updated_at": int(time.time())})
            c = ssh()
            start_cmd = "sudo -n runuser -u mc -- /opt/xxserver/start.sh"
            _, stdout, stderr = c.exec_command(start_cmd, timeout=420)
            start_out = stdout.read().decode("utf-8", "replace").strip()
            start_err = stderr.read().decode("utf-8", "replace").strip()
            start_rc = stdout.channel.recv_exit_status()
            if start_rc != 0:
                errors.append("启动服务端失败：" + (start_err or start_out or f"退出码 {start_rc}"))
        success_phase = "重启完成" if action == "restart" else "服务端已关闭"
        failure_phase = "重启失败" if action == "restart" else "关闭失败"
        _shop_restart_state.update({"phase": failure_phase if errors else success_phase,
                                    "error": "；".join(errors), "updated_at": int(time.time())})
        op_log(source + ("重启服务端" if action == "restart" else "关闭服务端"), {},
               "fail: " + "; ".join(errors) if errors else "ok")
    except Exception as e:
        reset_ssh()
        _shop_restart_state.update({"phase": "重启失败" if action == "restart" else "关闭失败", "error": str(e)[:500],
                                    "updated_at": int(time.time())})
        op_log(source + ("重启服务端" if action == "restart" else "关闭服务端"), {}, "fail: " + str(e))
    finally:
        _shop_restart_state["running"] = False


def start_server_action(action, source="运维管理"):
    global _shop_restart_state
    with _shop_restart_lock:
        if _shop_restart_state.get("running"):
            raise ValueError("游戏服务端正在执行操作")
        if action not in ("restart", "stop"):
            raise ValueError("未知的服务端操作")
        _shop_restart_state = {"running": True, "action": action,
                               "phase": "准备重启" if action == "restart" else "准备关闭", "error": "",
                               "updated_at": int(time.time())}
        threading.Thread(target=_server_action_worker, args=(action, source), daemon=True).start()
    return dict(_shop_restart_state)


def start_shop_restart():
    return start_server_action("restart", "商城配置后")


def qstr(v):
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\x00", "\\0")
    return "'" + s + "'"


def qint(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def qfloat(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def op_log(action, detail, result="ok"):
    rec = {"ts": int(time.time()), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "action": action, "detail": detail, "result": result}
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def read_logs(n=200):
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-n:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    out.reverse()
    return out


_items = None
_scenes = None
_partitions = None


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        traceback.print_exc()


def _parse_item_list(path):
    data = []
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("gb18030", "replace")
    for ln in text.replace("\r\n", "\n").split("\n")[1:]:
        if not ln.strip():
            continue
        c = ln.split("\t")
        if len(c) < 3 or not c[0].strip().isdigit():
            continue
        data.append({"id": int(c[0]), "name": c[1],
                     "type": c[2] if len(c) > 2 else "",
                     "quality": c[7] if len(c) > 7 else "",
                     "lv": c[11] if len(c) > 11 else "",
                     "price": c[16] if len(c) > 16 else ""})
    return data


def load_items():
    global _items
    if _items is not None:
        return _items
    cache = os.path.join(CACHE_DIR, "items.json")
    bundled = os.path.join(RES_DIR, "items.json")
    ext_ok = os.path.exists(ITEM_LIST)
    data = None
    if os.path.exists(cache) and (not ext_ok or os.path.getmtime(cache) >= os.path.getmtime(ITEM_LIST)):
        data = _read_json(cache)
    if not data and not ext_ok and os.path.exists(bundled):
        data = _read_json(bundled)
    if not data and ext_ok:
        try:
            data = _parse_item_list(ITEM_LIST)
            _write_json(cache, data)
        except Exception:
            traceback.print_exc()
    _items = data or []
    return _items


def load_scenes():
    global _scenes
    if _scenes is not None:
        return _scenes
    out = {}
    cache = os.path.join(CACHE_DIR, "scenes.json")
    bundled = os.path.join(RES_DIR, "scenes.json")
    src = None
    if os.path.exists(SCN_INFO):
        try:
            with open(SCN_INFO, "rb") as f:
                text = f.read().decode("gb18030", "replace")
            for ln in text.replace("\r\n", "\n").split("\n")[1:]:
                c = ln.split("\t")
                if len(c) > 1 and c[1].strip().isdigit():
                    out[int(c[1])] = c[0].strip()
            src = "ext"
        except Exception:
            traceback.print_exc()
    if not out:
        data = _read_json(cache) or (_read_json(bundled) if os.path.exists(bundled) else None)
        if data:
            out = {int(k): v for k, v in data.items()}
            src = "cache"
    if out and src == "ext":
        _write_json(cache, {str(k): v for k, v in out.items()})
    _scenes = out
    return _scenes


def partitions():
    global _partitions
    if _partitions is None:
        rows = sql("SELECT TABLE_NAME FROM information_schema.TABLES "
                   f"WHERE TABLE_SCHEMA='{DB_IMS}' AND TABLE_NAME LIKE 'point_partition%';")
        _partitions = [r[0] for r in rows if re.fullmatch(r"point_partition_[0-9]+", r[0])]
    return _partitions


def char_scene_name(sid):
    try:
        return load_scenes().get(int(sid), "")
    except Exception:
        return ""


def resolve_account_name(account):
    """The point tables use account names, while the UI also accepts account IDs."""
    acct = str(account).strip()
    if not acct:
        raise ValueError("请输入账号名或账号 ID")
    where = f"name={qstr(acct)}"
    if acct.isdecimal():
        where = f"account_id={int(acct)} OR {where}"
    rows = sql(f"SELECT name FROM {DB_CHAR}.common_accounts WHERE {where} LIMIT 1;")
    if not rows:
        raise ValueError("账号不存在：请核对账号名或 ID")
    return rows[0][0]


def account_point(account):
    acct = resolve_account_name(account)
    # The running IMS purchase proxy reads point_partition_0 by account name.
    # point_partition_01 is a stale historical copy on this server.
    t = "point_partition_0"
    if t not in partitions():
        raise RuntimeError("服务端缺少点券主分表 point_partition_0")
    rows = sql(f"SELECT available_point,frozen_point FROM {DB_IMS}.{t} "
               f"WHERE account={qstr(acct)} LIMIT 1;")
    if rows:
        return t, qint(rows[0][0]), qint(rows[0][1])
    return None, 0, 0


def char_online(char_id):
    return bool(sql(f"SELECT char_id FROM {DB_CHAR}.lock_characters "
                    f"WHERE char_id={qint(char_id)} LIMIT 1;"))


def account_rows(where="", limit=100):
    query = ("SELECT a.account_id,a.name,a.status,a.max_level,a.create_time,"
             "a.last_login_time,a.last_ip,(SELECT COUNT(*) FROM "
             f"{DB_CHAR}.common_characters c WHERE c.account_id=a.account_id "
             f"AND c.mark_delete=0) FROM {DB_CHAR}.common_accounts a "
             f"{where} ORDER BY a.last_login_time DESC LIMIT {limit};")
    out = []
    for r in sql(query):
        out.append(dict(zip(("account_id", "name", "status", "max_level",
                             "create_time", "last_login_time", "last_ip", "char_count"),
                            (r[0], r[1], qint(r[2]), r[3], qint(r[4]),
                             qint(r[5]), r[6], qint(r[7])))))
    return out


def char_rows(where="", limit=100):
    query = ("SELECT c.char_id,c.account_id,c.name,c.scene_id,c.last_pos_x,"
             "c.last_pos_y,c.last_pos_z,c.create_time,c.last_leave_time,"
             "c.total_play_time,c.silence_time,c.mark_delete,c.family_name,"
             "c.guild_name,c.dead_area_id,c.gzs_idx,a.name FROM "
             f"{DB_CHAR}.common_characters c LEFT JOIN {DB_CHAR}.common_accounts a "
             f"ON a.account_id=c.account_id {where} "
             f"ORDER BY c.last_leave_time DESC LIMIT {limit};")
    out = []
    for r in sql(query):
        out.append(_char_dict(r))
    return out


def _char_dict(r):
    return {"char_id": r[0], "account_id": r[1], "name": r[2],
            "scene_id": qint(r[3]), "scene_name": char_scene_name(r[3]),
            "x": round(qfloat(r[4]), 2), "y": round(qfloat(r[5]), 2),
            "z": round(qfloat(r[6]), 2), "create_time": qint(r[7]),
            "last_leave_time": qint(r[8]), "total_play_time": qint(r[9]),
            "silence_time": qint(r[10]), "mark_delete": qint(r[11]),
            "family_name": r[12], "guild_name": r[13],
            "dead_area_id": qint(r[14]), "gzs_idx": qint(r[15]),
            "account_name": r[16]}


def _char_detail_legacy(char_id):
    cid = qint(char_id)
    rows = char_rows(f"WHERE c.char_id={cid}", 1)
    if not rows:
        return None
    d = rows[0]
    d["online"] = char_online(cid)
    try:
        if uses_whstream(cid):
            blob, _ = _whstream_row(cid)
            _, records = _whstream_records(blob)
            d["item_count"] = len(records)
        else:
            items = sql(f"SELECT COUNT(*) FROM {DB_CHAR}.entire_items WHERE char_id={cid};")
            d["item_count"] = qint(items[0][0]) if items else 0
    except Exception:
        d["item_count"] = 0
    try:
        t, available, frozen = account_point(d["account_id"])
        d.update(point_table=t or "", point_available=available, point_frozen=frozen)
    except Exception:
        d.update(point_table="", point_available=0, point_frozen=0)
    try:
        bans = sql(f"SELECT endtime FROM {DB_LBA}.lba_ban_accounts "
                   f"WHERE account={qstr(d['account_id'])} OR "
                   f"account={qstr(d['account_name'] or '')} LIMIT 1;")
        d["ban_endtime"] = qint(bans[0][0]) if bans else 0
    except Exception:
        d["ban_endtime"] = 0
    return d


def char_detail(char_id):
    cid = qint(char_id)
    # A remote mysql invocation costs roughly one SSH round trip. Fetch all
    # detail fields together instead of issuing nine serial queries per click.
    query = (
        "SELECT c.char_id,c.account_id,c.name,c.scene_id,c.last_pos_x,"
        "c.last_pos_y,c.last_pos_z,c.create_time,c.last_leave_time,"
        "c.total_play_time,c.silence_time,c.mark_delete,c.family_name,"
        "c.guild_name,c.dead_area_id,c.gzs_idx,a.name,"
        "IF(l.char_id IS NULL,0,1),p.available_point,p.frozen_point,"
        "HEX(w.whstream),"
        f"(SELECT endtime FROM {DB_LBA}.lba_ban_accounts b "
        "WHERE b.account=CAST(c.account_id AS CHAR) OR b.account=a.name LIMIT 1) "
        f"FROM {DB_CHAR}.common_characters c "
        f"LEFT JOIN {DB_CHAR}.common_accounts a ON a.account_id=c.account_id "
        f"LEFT JOIN {DB_CHAR}.lock_characters l ON l.char_id=c.char_id "
        f"LEFT JOIN {DB_IMS}.point_partition_0 p ON p.account=a.name "
        f"LEFT JOIN {DB_CHAR}.common_char_whstream w ON w.char_id=c.char_id "
        f"WHERE c.char_id={cid} LIMIT 1;"
    )
    try:
        rows = sql(query)
    except RuntimeError as exc:
        if "doesn't exist" not in str(exc):
            raise
        return _char_detail_legacy(cid)
    if not rows:
        return None
    r = rows[0]
    d = _char_dict(r)
    d["online"] = bool(qint(r[17]))
    d.update(point_table="point_partition_0" if r[18] != "NULL" else "",
             point_available=qint(r[18]), point_frozen=qint(r[19]),
             ban_endtime=qint(r[21]))
    try:
        blob = bytes.fromhex(r[20])
        _, records = _whstream_records(blob)
        d["item_count"] = len(records)
    except (TypeError, ValueError, RuntimeError):
        try:
            items = sql(f"SELECT COUNT(*) FROM {DB_CHAR}.entire_items WHERE char_id={cid};")
            d["item_count"] = qint(items[0][0]) if items else 0
        except Exception:
            d["item_count"] = 0
    return d


def next_item_ids(count):
    rows = sql(f"SELECT IFNULL(MAX(item_id),2000000000000000) FROM {DB_CHAR}.entire_items;")
    base = qint(rows[0][0], 2000000000000000) if rows else 2000000000000000
    base = max(base, 2000000000000000)
    return [base + i + 1 for i in range(count)]


def next_slot(char_id):
    rows = sql(f"SELECT IFNULL(MAX(auto_id)+1,0) FROM {DB_CHAR}.entire_items "
               f"WHERE char_id={qint(char_id)} AND pack=0;")
    return qint(rows[0][0]) if rows else 0


def uses_whstream(char_id):
    """This server family loads inventory from a character blob, not item tables."""
    present = sql("SELECT COUNT(*) FROM information_schema.tables "
                  f"WHERE table_schema={qstr(DB_CHAR)} AND table_name='common_char_whstream';")
    if not present or not qint(present[0][0]):
        return False
    rows = sql(f"SELECT COUNT(*) FROM {DB_CHAR}.common_char_whstream "
               f"WHERE char_id={qint(char_id)};")
    return bool(rows and qint(rows[0][0]))


def _whstream_row(char_id):
    rows = sql(f"SELECT stream_len,checkin,HEX(whstream) FROM {DB_CHAR}.common_char_whstream "
               f"WHERE char_id={qint(char_id)} LIMIT 1;")
    if len(rows) != 1 or len(rows[0]) != 3:
        raise ValueError("角色没有可读取的 whstream 存档")
    length, checkin, hex_blob = rows[0]
    blob = bytes.fromhex(hex_blob)
    if len(blob) != qint(length):
        raise RuntimeError("角色存档长度校验失败，已停止发放")
    return blob, qint(checkin)


def _whstream_records(blob):
    # A 0x13 stream tag precedes each 140-byte SItemUnit. Its DWARF layout is:
    # record[1]=status, [3:5]=excel ID, [5:13]=unique ID,
    # [13:15]=bag position (wAutoID), [19:21]=quantity (byNum).
    marker, end_marker = bytes.fromhex("21104a00"), bytes.fromhex("11104b00")
    begin = blob.find(marker)
    end = blob.find(end_marker, begin + len(marker))
    start = begin + len(marker)
    if begin < 0 or end < 0 or blob.find(marker, start) >= 0 or (end - start) % 141:
        raise RuntimeError("无法识别角色背包存档格式，已停止发放")
    records = [blob[pos:pos + 141] for pos in range(start, end, 141)]
    if not records or any(rec[0] != 0x13 for rec in records):
        raise RuntimeError("角色背包记录格式不符合已验证的版本，已停止发放")
    return end, records


def whstream_template_info(char_id):
    """报告该角色背包里已有哪些道具类型可作为模板，以及剩余可用槽位。"""
    cid = qint(char_id)
    if not uses_whstream(cid):
        return {"whstream": False, "types": [], "free_slots": 0}
    catalog = {qint(entry["id"]): entry for entry in load_items()}
    blob, _ = _whstream_row(cid)
    _, records = _whstream_records(blob)
    used_slots, counts = set(), {}
    for rec in records:
        slot = _rec_slot(rec)
        used_slots.add(slot)
        if not BAG_SLOT_MIN <= slot < BAG_SLOT_MAX:
            continue
        t = _item_type(catalog, _rec_excel(rec))
        if t:
            counts[t] = counts.get(t, 0) + 1
    free = sum(1 for a in range(BAG_SLOT_MIN, BAG_SLOT_MAX) if a not in used_slots)
    return {"whstream": True,
            "types": [{"type": k, "count": v} for k, v in counts.items()],
            "free_slots": free}


def _reset_whstream_cache():
    with _lock:
        for attempt in (0, 1):
            try:
                _, stdout, stderr = ssh().exec_command(
                    "curl -fsS --max-time 5 http://127.0.0.1:3725/reset", timeout=8)
                response = stdout.read().decode("utf-8", "replace")
                error = stderr.read().decode("utf-8", "replace")
                if stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError(error.strip() or "DBI 缓存清理失败")
                return response
            except Exception:
                reset_ssh()
                if attempt:
                    raise


def _backup_whstream(char_id, blob):
    folder = os.path.join(CACHE_DIR, "whstream_backups")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    name = f"{qint(char_id)}-{time.time_ns()}.json"
    path = os.path.join(folder, name)
    record = {"char_id": str(qint(char_id)), "sha256": hashlib.sha256(blob).hexdigest(),
              "hex": blob.hex(), "created_at": int(time.time())}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f)
        f.flush()
        os.fsync(f.fileno())
    return path


# 背包存档里可安全写入的槽位区间（主背包页）
BAG_SLOT_MIN, BAG_SLOT_MAX = 200, 300


def _rec_excel(rec):
    return struct.unpack_from("<H", rec, 3)[0]


def _rec_uid(rec):
    return struct.unpack_from("<Q", rec, 5)[0]


def _rec_slot(rec):
    return struct.unpack_from("<H", rec, 13)[0]


def _item_type(catalog, excel_id):
    entry = catalog.get(excel_id)
    return str(entry.get("type", "")) if entry else ""


def _give_whstream_items(char_id, items):
    """把道具写入角色背包存档（common_char_whstream）。

    每条 SItemUnit 都是 140 字节的定长结构，所有道具类型共用同一布局；但结构
    尾部保存的是**该类型专有的状态**（耐久、绑定、附魔、外观信息等）。因此新道具
    的字节模板必须取自同类型的现有记录：只改写 excel ID、唯一编号、背包位置和
    数量，其余字段沿用同类真实道具。这样既能支持任意类型，也不会凭空造出
    耐久为 0 或字段错位的装备。
    """
    cid = qint(char_id)
    if not 1 <= len(items) <= 100:
        raise ValueError("每次只能发放 1–100 种道具")
    catalog = {qint(entry["id"]): entry for entry in load_items()}

    normalized = []
    for entry in items:
        excel_id, num = qint(entry.get("excel_id")), qint(entry.get("num"), 1)
        if excel_id not in catalog:
            raise ValueError(f"道具 {excel_id} 不在物品列表中，无法发放")
        item_type = _item_type(catalog, excel_id)
        if not item_type:
            raise ValueError(f"道具 {excel_id} 缺少类型信息，无法确定存档模板")
        if not 1 <= num <= 65535:
            raise ValueError("道具数量必须在 1–65535 之间")
        normalized.append((excel_id, num, item_type))

    with _grant_lock:
        blob, checkin = _whstream_row(cid)
        if checkin or char_online(cid):
            raise ValueError("角色必须完全下线后才能发放道具；强制发放也不适用于此存档")
        end, records = _whstream_records(blob)

        # 按类型收集背包内的现有记录，作为该类型的字节模板
        templates = {}
        for rec in records:
            if not BAG_SLOT_MIN <= _rec_slot(rec) < BAG_SLOT_MAX:
                continue
            t = _item_type(catalog, _rec_excel(rec))
            if t:
                templates.setdefault(t, []).append(rec)
        for t in templates:
            templates[t].sort(key=lambda r: (_rec_excel(r) != 12076, _rec_slot(r)))

        missing = sorted({t for _, _, t in normalized} - set(templates))
        if missing:
            raise ValueError(
                "角色背包里没有这些类型的道具可作模板：类型 " + "、".join(missing) +
                "。请先让该角色手动获得任意一件同类道具，再回来发放；"
                "已在背包中的类型不受影响。")

        used_auto_ids = {_rec_slot(rec) for rec in records}
        used_uids = {_rec_uid(rec) for rec in records}
        free_auto_ids = [a for a in range(BAG_SLOT_MIN, BAG_SLOT_MAX)
                         if a not in used_auto_ids]
        if len(free_auto_ids) < len(normalized):
            raise ValueError(
                f"背包可用槽位不足：剩余 {len(free_auto_ids)} 个，需要 {len(normalized)} 个，"
                "请先清理背包再发放")

        source_uid = _rec_uid(templates[normalized[0][2]][0])
        uid_prefix = source_uid & ~0xffff
        uid_serial = source_uid & 0xffff
        additions, result = [], []
        for (excel_id, num, item_type), auto_id in zip(normalized, free_auto_ids):
            uid = uid_prefix | uid_serial
            while uid in used_uids:
                uid_serial += 1
                if uid_serial > 65535:
                    raise RuntimeError("道具编号空间不足，已停止发放")
                uid = uid_prefix | uid_serial
            used_uids.add(uid)
            rec = bytearray(templates[item_type][0])
            struct.pack_into("<H", rec, 3, excel_id)
            struct.pack_into("<Q", rec, 5, uid)
            struct.pack_into("<H", rec, 13, auto_id)
            struct.pack_into("<H", rec, 19, num)
            additions.append(rec)
            result.append({"item_id": str(uid), "excel_id": excel_id, "num": num,
                           "name": catalog[excel_id].get("name", ""), "type": item_type})
        new_blob = blob[:end] + b"".join(additions) + blob[end:]
        backup = _backup_whstream(cid, blob)
        old_hash = hashlib.sha256(blob).hexdigest()
        sql(f"UPDATE {DB_CHAR}.common_char_whstream "
            f"SET whstream=0x{new_blob.hex()},stream_len={len(new_blob)},saved_at=UNIX_TIMESTAMP() "
            f"WHERE char_id={cid} AND checkin=0 AND SHA2(whstream,256)='{old_hash}';",
            timeout=45)
        actual, actual_checkin = _whstream_row(cid)
        if actual != new_blob or actual_checkin:
            raise RuntimeError("存档写入校验失败；请检查角色状态与备份：" + backup)
        try:
            _reset_whstream_cache()
        except Exception as error:
            raise RuntimeError("道具已写入存档，但读档缓存清理失败；请检查备份 " + backup) from error
        return result


def give_items(char_id, items):
    cid = qint(char_id)
    if uses_whstream(cid):
        return _give_whstream_items(cid, items)
    ids = next_item_ids(len(items))
    slot = next_slot(cid)
    now = int(time.time())
    vals = []
    reserve = "000000000000000000000000"
    info = "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    for i, it in enumerate(items):
        vals.append(f"({ids[i]},{cid},{qint(it['excel_id'])},{slot+i},{now},"
                    f"{max(1,qint(it['num'],1))},0,0,0,0,0,0x{reserve},0,0x{info})")
    body = ",".join(vals)
    cols = "(item_id,char_id,excel_id,auto_id,get_time,num,wear_out,max_wear_out,info_color,bound,pack,enchant,reserve,info)"
    sql(f"INSERT INTO {DB_CHAR}.entire_items {cols} VALUES {body};")
    sql(f"INSERT INTO {DB_CHAR}.common_items {cols} VALUES {body};")
    item_ids = ",".join(str(item_id) for item_id in ids)
    for table in ("entire_items", "common_items"):
        rows = sql(f"SELECT COUNT(*) FROM {DB_CHAR}.{table} "
                   f"WHERE item_id IN ({item_ids}) AND char_id={cid};")
        if not rows or qint(rows[0][0]) != len(ids):
            raise RuntimeError(f"写入后校验失败：{table} 中的物品记录不完整")
    return [{"item_id": str(ids[i]), "excel_id": qint(it["excel_id"]),
             "num": max(1, qint(it["num"], 1))} for i, it in enumerate(items)]


MAIL_RESULTS = {
    0: "发送成功", 2: "收件角色不存在", 3: "对方邮箱已满",
    4: "对方设置了拒收", 5: "发送过于频繁",
    99: "数据库冲突，请稍后重试", -99: "数据库冲突，请稍后重试",
}
MAIL_BODY_MAX = 65490


def _mail_fixed(text, width=33):
    raw = str(text or "").encode("gb18030")
    raw = raw[:width - 1]
    while raw:
        try:
            raw.decode("gb18030")
            break
        except UnicodeDecodeError:
            raw = raw[:-1]
    return raw.ljust(width, b"\0")


def _recv_exact(sock, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise EOFError("DBI 代理提前关闭了连接")
        chunks.extend(part)
    return bytes(chunks)


def _read_dbi_frame(channel):
    header = _recv_exact(channel, 6)
    op, size = struct.unpack("<HI", header)
    if size > 16 * 1024 * 1024:
        raise ValueError("DBI 响应帧过大")
    return op, _recv_exact(channel, size)


def _resolve_mail_recipient(char_id):
    cid = qint(char_id)
    rows = sql(f"SELECT TRIM(name) FROM {DB_CHAR}.common_characters "
               f"WHERE char_id={cid} AND mark_delete=0 LIMIT 1;")
    if not rows:
        rows = sql(f"SELECT TRIM(name) FROM {DB_CHAR}.entire_characters "
                   f"WHERE char_id={cid} AND mark_delete=0 LIMIT 1;")
    if not rows or not rows[0][0]:
        raise ValueError("找不到有效的收件角色")
    return str(rows[0][0])


def _ensure_mail_counter(char_id):
    """Repair a stale mail counter before the native procedure allocates an ID."""
    cid = qint(char_id)
    sql(
        f"START TRANSACTION; "
        f"SET @gm_mail_max_id=(SELECT IFNULL(MAX(msg_id),0) FROM {DB_CHAR}.pri_messages WHERE char_id={cid}); "
        f"INSERT INTO {DB_CHAR}.messages_index (char_id,pri_msg_idx) VALUES ({cid},@gm_mail_max_id+1) "
        f"ON DUPLICATE KEY UPDATE pri_msg_idx=GREATEST(pri_msg_idx,@gm_mail_max_id+1); "
        f"UPDATE {DB_CHAR}.messages_index SET pri_msg_idx=GREATEST(pri_msg_idx,@gm_mail_max_id+1) "
        f"WHERE char_id={cid}; COMMIT;")


def _native_mail_send(char_id, recipient, sender, flag, body):
    if len(body) > MAIL_BODY_MAX:
        raise ValueError(f"邮件内容过长（最多 {MAIL_BODY_MAX} 字节）")
    cid = qint(char_id)
    payload = (struct.pack("<I", 0) + struct.pack("<Q", cid)
               + _mail_fixed(recipient) + _mail_fixed(sender)
               + struct.pack("<b", flag) + struct.pack("<H", len(body))
               + bytes(3) + bytes(37) + body)
    channel = None
    try:
        transport = ssh().get_transport()
        if not transport or not transport.is_active():
            reset_ssh()
            transport = ssh().get_transport()
        channel = transport.open_channel(
            "direct-tcpip", (DBI_HOST, DBI_PORT), ("127.0.0.1", 0), timeout=8)
        channel.settimeout(15)
        channel.sendall(struct.pack("<HI", 7, 4) + struct.pack("<I", 0x47544F4C))
        _read_dbi_frame(channel)
        channel.sendall(struct.pack("<HI", 0x2D, len(payload)) + payload)
        op, reply = _read_dbi_frame(channel)
    except Exception:
        reset_ssh()
        raise
    finally:
        if channel:
            channel.close()
    if op != 0x2E or len(reply) < 41:
        raise RuntimeError(f"DBI 邮件响应异常（op=0x{op:x}，长度 {len(reply)}）")
    result = struct.unpack_from("<i", reply, 37)[0]
    try:
        actual_recipient = reply[4:37].split(b"\0", 1)[0].decode("utf-8")
    except UnicodeDecodeError:
        actual_recipient = reply[4:37].split(b"\0", 1)[0].decode("gb18030", "replace")
    return result, actual_recipient


def build_mail_stuff(kind, title, body, items):
    title = str(title or "").strip()
    body = str(body or "")
    if "\x00" in title or "\x00" in body:
        raise ValueError("邮件标题和正文不能包含空字符")
    if kind == "player":
        if items:
            raise ValueError("玩家邮件不能带附件，请改用系统邮件")
        title = title or "GM 邮件"
        text = f"title:{title}\n{body}\0"
    else:
        title = title or "系统邮件"
        if any(char in title for char in "<>"):
            raise ValueError("系统邮件标题不能包含尖括号")
        if len(items) > 100:
            raise ValueError("每封邮件最多 100 种附件")
        catalog_ids = {qint(entry["id"]) for entry in load_items()}
        tags = []
        for entry in items:
            excel_id, num = qint(entry.get("excel_id")), qint(entry.get("num"), 1)
            if excel_id not in catalog_ids:
                raise ValueError(f"道具 {excel_id} 不在当前物品列表中")
            if not 1 <= num <= 9999:
                raise ValueError("邮件附件数量必须在 1–9999 之间")
            tags.append(f"<additem:wExcelID={excel_id},byNum={num}>")
        text = f"<title:{title}>{body}{''.join(tags)}\0"
    try:
        raw = text.encode("gb18030")
    except UnicodeEncodeError as exc:
        raise ValueError("邮件内容含有游戏邮件不支持的字符") from exc
    if len(raw) > MAIL_BODY_MAX:
        raise ValueError(f"邮件内容过长（最多 {MAIL_BODY_MAX} 字节）")
    return raw


def send_mail(char_id, title, body, sender, items, kind="system"):
    cid = qint(char_id)
    if kind not in ("player", "system"):
        raise ValueError("未知邮件类型")
    recipient = _resolve_mail_recipient(cid)
    msg_stuff = build_mail_stuff(kind, title, body, items)
    flag = {"player": 0, "system": 1}[kind]
    _ensure_mail_counter(cid)
    result, actual_recipient = _native_mail_send(
        cid, recipient, str(sender or "系统").strip() or "系统", flag, msg_stuff)
    if result != 0:
        message = MAIL_RESULTS.get(result, f"游戏邮件接口返回错误码 {result}")
        raise RuntimeError(message)
    return {"char_id": cid, "recipient": actual_recipient or recipient,
            "kind": kind, "flag": flag, "result": result,
            "items": len(items) if kind == "system" else 0,
            "body_size": len(msg_stuff)}


def set_point(account, mode, value):
    acct = resolve_account_name(account)
    t, cur, frozen = account_point(acct)
    if mode not in ("add", "sub", "set"):
        raise ValueError("未知点券操作")
    try:
        v = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("点券数量必须是整数") from exc
    if not 0 <= v <= 2147483647:
        raise ValueError("点券数量必须在 0–2147483647 之间")
    if mode == "set":
        new = v
    elif mode == "sub":
        new = max(0, cur - v)
    else:
        new = cur + v
    if new > 2147483647:
        raise ValueError("充值后点券超过服务端支持的上限 2147483647")
    table = "point_partition_0"
    if mode == "add":
        on_duplicate = f"available_point=LEAST(2147483647,available_point+{v})"
        initial = v
    elif mode == "sub":
        on_duplicate = f"available_point=IF(available_point<{v},0,available_point-{v})"
        initial = 0
    else:
        on_duplicate = f"available_point={v}"
        initial = v
    sql(f"INSERT INTO {DB_IMS}.{table} "
        "(account,available_point,frozen_point,last_login) "
        f"VALUES ({qstr(acct)},{initial},0,UNIX_TIMESTAMP()) "
        f"ON DUPLICATE KEY UPDATE {on_duplicate};")
    rows = sql(f"SELECT available_point,frozen_point FROM {DB_IMS}.{table} "
               f"WHERE account={qstr(acct)} LIMIT 1;")
    if not rows or qint(rows[0][0]) != new or qint(rows[0][1]) != frozen:
        raise RuntimeError("点券写入后校验失败：数据库中的数值未更新")
    return {"table": table, "before": cur, "after": new,
            "initialized": t is None}


class Handler(BaseHTTPRequestHandler):
    server_version = "XunxianGM/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(404)

    def _config_request_allowed(self):
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
            host = urlparse("http://" + self.headers.get("Host", "")).hostname
            if host not in ("127.0.0.1", "localhost", "::1"):
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return False
            return True
        except ValueError:
            return False

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                page = os.path.join(RES_DIR, "index.html")
                if not os.path.exists(page):
                    page = os.path.join(APP_DIR, "index.html")
                return self._file(page, "text/html; charset=utf-8")
            if p in {f"/logos/{name}.png" for name in
                     ("logo", "status", "account", "item", "mail", "logs", "rate", "skill")}:
                filename = p.rsplit("/", 1)[-1]
                image = os.path.join(RES_DIR, "logos", filename)
                if not os.path.exists(image):
                    image = os.path.join(APP_DIR, "logos", filename)
                return self._file(image, "image/png")
            if p == "/api/summons/catalog":
                catalog_path = Path(RES_DIR) / "doc" / "召唤物列表.csv"
                if not catalog_path.is_file():
                    catalog_path = Path(APP_DIR) / "doc" / "召唤物列表.csv"
                try:
                    catalog = load_catalog(catalog_path)
                    client_path_text = str(CFG.get("client_mb_dpk") or "").strip()
                    client_path = Path(os.path.expanduser(client_path_text)) if client_path_text else None
                    friends = load_friend_catalog(client_path)
                    saved = {}
                    if os.path.exists(SUMMON_CONFIG_PATH):
                        with open(SUMMON_CONFIG_PATH, "r", encoding="utf-8") as f:
                            saved = json.load(f)
                    return self._json({"ok": True, "data": {"creatures": catalog,
                        "friends": friends, "config": saved}})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)}, 500)
            if p == "/favicon.ico":
                logo = os.path.join(RES_DIR, "logos", "logo.png")
                if not os.path.exists(logo):
                    logo = os.path.join(APP_DIR, "logos", "logo.png")
                if os.path.exists(logo):
                    return self._file(logo, "image/png")
                self.send_response(204)
                self.end_headers()
                return
            if p == "/api/rates":
                return self._json({"ok": True, "data": get_game_rates()})
            if p == "/api/fate-lock":
                return self._json({"ok": True, "data": get_fate_locks()})
            if p == "/api/shop":
                return self._json({"ok": True, "data": get_shop(qs.get("q", [""])[0])})
            if p == "/api/shop/restart":
                return self._json({"ok": True, "data": dict(_shop_restart_state)})
            if p == "/api/server/control":
                return self._json({"ok": True, "data": dict(_shop_restart_state)})
            if p == "/api/definitions":
                kind = (qs.get("kind") or [""])[0]
                try:
                    return self._json({"ok": True, "data": get_definitions(kind)})
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
            if p == "/api/status":
                ssh_ok, err = True, ""
                try:
                    counts = sql(
                        f"SELECT (SELECT COUNT(*) FROM {DB_CHAR}.common_accounts),"
                        f"(SELECT COUNT(*) FROM {DB_CHAR}.common_characters);",
                        timeout=8)
                except Exception as e:
                    ssh_ok, err = False, str(e)[:300]
                    reset_ssh()
                if not ssh_ok:
                    return self._json({"ok": True, "ssh_ok": False, "error": err,
                                       "accounts": 0, "chars": 0,
                                       "items": len(load_items()), "scenes": len(load_scenes())})
                return self._json({"ok": True, "ssh_ok": True,
                                   "accounts": qint(counts[0][0]) if counts else 0,
                                   "chars": qint(counts[0][1]) if counts else 0,
                                   "items": len(load_items()), "scenes": len(load_scenes())})
            if p == "/api/config":
                if not self._config_request_allowed():
                    return self._json({"ok": False, "error": "仅允许从本机控制台修改配置"}, 403)
                return self._json({"ok": True, "data": {
                    "config_path": CONFIG_PATH, "app_dir": APP_DIR,
                    "listen": f"{CFG['listen_host']}:{CFG['listen_port']}",
                    "ssh": f"{CFG['ssh_user']}@{CFG['ssh_host']}:{CFG['ssh_port']}",
                    "db_char": DB_CHAR, "db_ims": DB_IMS, "frozen": FROZEN,
                    "settings": public_config()}})
            if p == "/api/accounts":
                q = qs.get("q", [""])[0].strip()
                if q.isdigit():
                    where = f"WHERE a.name LIKE {qstr('%'+q+'%')} OR a.account_id={qint(q)}"
                elif q:
                    where = f"WHERE a.name LIKE {qstr('%'+q+'%')}"
                else:
                    where = ""
                return self._json({"ok": True, "rows": account_rows(where)})
            if p == "/api/chars":
                q = qs.get("q", [""])[0].strip()
                if q.isdigit():
                    where = (f"WHERE c.name LIKE {qstr('%'+q+'%')} OR "
                             f"c.char_id={qint(q)} OR c.account_id={qint(q)}")
                elif q:
                    where = f"WHERE c.name LIKE {qstr('%'+q+'%')}"
                else:
                    where = ""
                return self._json({"ok": True, "rows": char_rows(where)})
            if p == "/api/char":
                d = char_detail(qs.get("id", ["0"])[0])
                if not d:
                    return self._json({"ok": False, "error": "角色不存在"}, 404)
                return self._json({"ok": True, "data": d})
            if p == "/api/item_templates":
                return self._json({"ok": True,
                                   "data": whstream_template_info(qs.get("char_id", ["0"])[0])})
            if p == "/api/items":
                q = qs.get("q", [""])[0].strip()
                limit = min(200, qint(qs.get("limit", ["60"])[0], 60))
                data = load_items()
                if q.isdigit():
                    res = [x for x in data if str(x["id"]).startswith(q)][:limit]
                    if len(res) < limit:
                        res += [x for x in data if q in x["name"]][:limit-len(res)]
                elif q:
                    res = [x for x in data if q in x["name"]][:limit]
                else:
                    res = data[:limit]
                return self._json({"ok": True, "rows": res})
            if p == "/api/scenes":
                return self._json({"ok": True, "rows": [
                    {"id": k, "name": v} for k, v in sorted(load_scenes().items())]})
            if p == "/api/logs":
                n = min(500, qint(qs.get("n", ["100"])[0], 100))
                return self._json({"ok": True, "rows": read_logs(n)})
            if p == "/api/account":
                acct = qs.get("id", ["0"])[0]
                account_where = f"a.name={qstr(acct)}"
                if acct.isdecimal():
                    account_where = f"a.account_id={qint(acct)} OR {account_where}"
                rows = sql(
                    "SELECT a.account_id,a.name,p.available_point,p.frozen_point,"
                    f"(SELECT endtime FROM {DB_LBA}.lba_ban_accounts b "
                    "WHERE b.account=CAST(a.account_id AS CHAR) OR b.account=a.name "
                    "ORDER BY b.endtime DESC LIMIT 1),"
                    "c.char_id,c.account_id,c.name,c.scene_id,c.last_pos_x,"
                    "c.last_pos_y,c.last_pos_z,c.create_time,c.last_leave_time,"
                    "c.total_play_time,c.silence_time,c.mark_delete,c.family_name,"
                    "c.guild_name,c.dead_area_id,c.gzs_idx,a.name,"
                    f"EXISTS(SELECT 1 FROM {DB_CHAR}.lock_characters l "
                    "WHERE l.char_id=c.char_id) "
                    f"FROM {DB_CHAR}.common_accounts a "
                    f"LEFT JOIN {DB_IMS}.point_partition_0 p ON p.account=a.name "
                    f"LEFT JOIN {DB_CHAR}.common_characters c "
                    f"ON c.account_id=a.account_id "
                    f"WHERE {account_where} ORDER BY c.last_leave_time DESC LIMIT 100;"
                )
                if not rows:
                    raise ValueError("账号不存在：请核对账号名或 ID")
                account_id, name = rows[0][0], rows[0][1]
                avail, frozen = qint(rows[0][2]), qint(rows[0][3])
                ban_endtime = qint(rows[0][4])
                chars = []
                for r in rows:
                    if r[5] == "NULL":
                        continue
                    char = _char_dict(r[5:22])
                    char["online"] = bool(qint(r[22]))
                    chars.append(char)
                return self._json({"ok": True, "data": {
                    "account": name, "account_id": str(account_id),
                    "point_table": "point_partition_0" if rows[0][2] != "NULL" else "",
                    "point_available": avail, "point_frozen": frozen,
                    "ban_endtime": ban_endtime, "chars": chars}})
            return self._json({"ok": False, "error": "unknown api"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"ok": False, "error": str(e)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        if p in ("/api/config", "/api/definitions", "/api/shop/add",
                 "/api/shop/delete", "/api/shop/delete-batch", "/api/shop/restart",
                 "/api/server/control", "/api/fate-lock", "/api/config/pick-path",
                 "/api/skill-limits/inspect",
                 "/api/skill-limits/apply", "/api/summons/apply"):
            if not self._config_request_allowed():
                return self._json({"ok": False, "error": "仅允许从本机控制台修改配置"}, 403)
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return self._json({"ok": False, "error": "配置请求必须使用 application/json"}, 415)
        n = qint(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        try:
            if p == "/api/config":
                try:
                    result = update_config(body)
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                op_log("配置更新", {"changed": result["changed"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/config/pick-path":
                field = str(body.get("field") or "")
                try:
                    selected = choose_local_file(field)
                except subprocess.TimeoutExpired as e:
                    raise ValueError("文件选择器等待超时") from e
                return self._json({"ok": True, "data": {"field": field, "path": selected}})
            if p == "/api/skill-limits/inspect":
                client_path_text = str(CFG.get("client_mb_dpk") or "").strip()
                if not client_path_text:
                    raise ValueError("请先在 运维管理 → 连接配置 中设置客户端 mb.dpk 路径并保存")
                client_path = Path(os.path.expanduser(client_path_text))
                if not client_path.is_file():
                    raise ValueError("找不到客户端 mb.dpk：" + str(client_path))
                info = inspect_client_dpk(client_path, Path(CACHE_DIR))
                info["client_path"] = str(client_path)
                provenance_key = _skill_limit_provenance_key(client_path)
                with _skill_limit_provenance_lock:
                    client_provenance = _load_skill_limit_provenance().get(provenance_key, {})
                session_id = info["session_id"]
                server_paths = {
                    "主包": MB_DPK_FILE_PATH,
                    "动态包": MB_DPK_DYNAMIC_FILE_PATH,
                }
                local_paths = {}
                with _lock:
                    sftp = ssh().open_sftp()
                    try:
                        for label, remote_path in server_paths.items():
                            local = os.path.join(CACHE_DIR, f"skill-limits-{session_id}-{label}.dpk")
                            sftp.get(remote_path, local)
                            local_paths[label] = local
                    finally:
                        sftp.close()
                server_skills = {}
                server_career_groups = {}
                for label, local in local_paths.items():
                    _, rows, _ = inspect_dpk_skills(Path(local))
                    server_skills[label] = {row["id"]: row for row in rows}
                    server_career_groups[label] = inspect_dpk_career_groups(Path(local))
                client_skills = info["skills"]
                shared_skills = []
                for client_row in client_skills:
                    main_row = server_skills.get("主包", {}).get(client_row["id"])
                    dynamic_row = server_skills.get("动态包", {}).get(client_row["id"])
                    if main_row is None:
                        continue
                    if dynamic_row is not None and main_row["profession_id"] != dynamic_row["profession_id"]:
                        continue
                    all_groups = set(client_row.get("career_groups", []))
                    all_groups.update(main_row.get("career_groups", []))
                    for groups_by_id in server_career_groups.values():
                        all_groups.update(groups_by_id.get(client_row["id"], []))
                    original_profession = int(client_provenance.get(
                        str(client_row["id"]), client_row["profession_id"]))
                    client_group = CAREER_GROUP.get(original_profession, (None,))[0]
                    server_rows = [main_row] + ([dynamic_row] if dynamic_row is not None else [])
                    server_owner_groups = {
                        CAREER_GROUP[row["profession_id"]][0]
                        for row in server_rows if row["profession_id"] in CAREER_GROUP
                    }
                    if client_group and any(group != client_group for group in server_owner_groups):
                        source_groups = {client_group}
                    elif len(server_owner_groups) == 1 and len(all_groups) > 1:
                        source_groups = all_groups - server_owner_groups
                    elif client_group:
                        source_groups = {client_group}
                    else:
                        source_groups = all_groups - server_owner_groups or all_groups
                    if not source_groups:
                        source_groups = {client_group} if client_group else all_groups
                    # skill_learn.txt retains the specific profession ID, while
                    # career_groups only identifies the four shared skill tables.
                    source_professions = [original_profession]

                    # The dynamic package can be an older overlay without a learn row.
                    # Its target career list still has to include the skill before we
                    # report an existing assignment.
                    assigned_to = []
                    owner = main_row["profession_id"]
                    target_group = CAREER_GROUP.get(owner, (None,))[0]
                    dynamic_groups = server_career_groups.get("动态包", {}).get(client_row["id"], [])
                    if (target_group and owner not in source_professions
                            and target_group in main_row.get("career_groups", [])
                            and target_group in dynamic_groups):
                        assigned_to.append(owner)
                    client_row["source_groups"] = sorted(source_groups)
                    client_row["source_professions"] = source_professions
                    client_row["career_groups"] = sorted(all_groups)
                    client_row["assigned_to"] = assigned_to
                    client_row["dynamic_has_learn"] = dynamic_row is not None
                    shared_skills.append(client_row)
                info["skills"] = shared_skills
                info["package_counts"] = {"客户端": len(client_skills),
                    **{label: len(rows) for label, rows in server_skills.items()},
                    "客户端与主包共有": len(info["skills"])}
                info["career_group_names"] = CAREER_GROUP_NAMES
                with _skill_limit_server_sessions_lock:
                    _skill_limit_server_sessions[session_id] = {
                        "paths": server_paths, "local_paths": local_paths,
                        "provenance_key": provenance_key,
                        "skills": {row["id"]: row for row in shared_skills}}
                op_log("读取三份技能配置包", {"sha256": info["sha256"],
                    "shared_skills": len(info["skills"]), "client_path": str(client_path)})
                return self._json({"ok": True, "data": info})
            if p == "/api/skill-limits/apply":
                session_id = str(body.get("session_id", ""))
                if not touch_client_session(session_id):
                    raise ValueError("技能包会话已过期，请重新读取客户端 mb.dpk")
                raw_changes = body.get("changes")
                target_profession = qint(body.get("target_profession"))
                with _skill_limit_server_sessions_lock:
                    server_session = _skill_limit_server_sessions.get(session_id)
                if not server_session:
                    raise ValueError("服务端技能包会话已失效，请重新选择客户端 mb.dpk")
                if not isinstance(raw_changes, list) or not raw_changes:
                    raise ValueError("没有待应用的技能修改")
                if target_profession not in CAREER_GROUP:
                    raise ValueError("目标职业 ID 无效")
                inspected_skills = server_session.get("skills", {})
                changes = {}
                source_professions = {}
                for item in raw_changes:
                    if not isinstance(item, dict) or not isinstance(item.get("assigned"), bool):
                        raise ValueError("技能修改内容无效")
                    skill_id = int(item.get("id"))
                    if skill_id in changes:
                        raise ValueError("技能 ID 不能重复")
                    skill = inspected_skills.get(skill_id)
                    if not skill:
                        raise ValueError(f"技能 {skill_id} 不属于本次读取的客户端与服务器主包共有技能")
                    source_ids = skill.get("source_professions", [])
                    if target_profession in source_ids:
                        raise ValueError(f"技能 {skill_id} 属于目标职业，不能作为跨职业技能添加")
                    currently_assigned = target_profession in skill.get("assigned_to", [])
                    if item["assigned"] == currently_assigned:
                        raise ValueError(f"技能 {skill_id} 没有状态变化")
                    changes[skill_id] = item["assigned"]
                    source_professions[skill_id] = int(source_ids[0])
                result, filename = patch_client_dpk(session_id, target_profession,
                    changes, source_professions, Path(CACHE_DIR))
                server_outputs = {}
                for label, source_path in server_session["local_paths"].items():
                    allowed_missing = ({skill_id for skill_id in changes
                        if not inspected_skills[skill_id].get("dynamic_has_learn")}
                        if label == "动态包" else set())
                    patched, _ = patch_dpk_changes(Path(source_path), target_profession,
                        changes, source_professions, Path(CACHE_DIR), allowed_missing)
                    output_path = os.path.join(CACHE_DIR, f"skill-limits-{session_id}-{label}-patched.dpk")
                    with open(output_path, "wb") as f:
                        f.write(patched)
                    server_outputs[label] = output_path
                stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
                backups = {label: remote + ".bak.skill-limits." + stamp
                    for label, remote in server_session["paths"].items()}
                uploads = {label: "/tmp/gmtool-skill-limits-" + uuid.uuid4().hex + ".dpk"
                    for label in server_outputs}
                with _lock:
                    c = ssh()
                    sftp = c.open_sftp()
                    try:
                        for label, local in server_outputs.items():
                            sftp.put(local, uploads[label])
                    finally:
                        sftp.close()
                    commands = ["set -e", "backup_ready=0", "committed=0",
                        "rollback(){ if [ \"$backup_ready\" = 1 ] && [ \"$committed\" = 0 ]; then " +
                        "cp -p " + shlex.quote(backups["主包"]) + " " + shlex.quote(server_session["paths"]["主包"]) + "; " +
                        "cp -p " + shlex.quote(backups["动态包"]) + " " + shlex.quote(server_session["paths"]["动态包"]) + "; fi; }",
                        "trap rollback EXIT"]
                    for label, remote in server_session["paths"].items():
                        commands.append("cp -p " + shlex.quote(remote) + " " + shlex.quote(backups[label]))
                    commands.append("backup_ready=1")
                    for label, remote in server_session["paths"].items():
                        staged = remote + ".gmtool.tmp." + uuid.uuid4().hex
                        commands.append("cp " + shlex.quote(uploads[label]) + " " + shlex.quote(staged))
                        commands.append("chown --reference=" + shlex.quote(remote) + " " + shlex.quote(staged))
                        commands.append("chmod --reference=" + shlex.quote(remote) + " " + shlex.quote(staged))
                        commands.append("mv -f " + shlex.quote(staged) + " " + shlex.quote(remote))
                    commands.extend(["committed=1", "trap - EXIT"])
                    commands.extend("rm -f " + shlex.quote(path) for path in uploads.values())
                    command = "sudo -n sh -c " + shlex.quote("; ".join(commands))
                    _, stdout, stderr = c.exec_command(command, timeout=180)
                    out = stdout.read().decode("utf-8", "replace").strip()
                    err = stderr.read().decode("utf-8", "replace").strip()
                    rc = stdout.channel.recv_exit_status()
                    if rc != 0:
                        raise RuntimeError("服务端文件替换失败：" + (err or out or str(rc)))
                    verify_cmd = "sha256sum " + " ".join(shlex.quote(p) for p in server_session["paths"].values())
                    _, stdout, stderr = c.exec_command(verify_cmd, timeout=30)
                    hashes = stdout.read().decode("utf-8", "replace").strip().splitlines()
                    if stdout.channel.recv_exit_status() != 0 or len(hashes) != 2:
                        raise RuntimeError("服务端文件已替换，但无法读取 SHA-256 校验结果：" + stderr.read().decode("utf-8", "replace"))
                    for (label, local), line in zip(server_outputs.items(), hashes):
                        expected = hashlib.sha256(Path(local).read_bytes()).hexdigest()
                        if line.split()[0] != expected:
                            raise RuntimeError(f"服务端{label} SHA-256 与本地生成包不一致")
                refresh_client_session(session_id, result)
                with _skill_limit_provenance_lock:
                    provenance = _load_skill_limit_provenance()
                    client_provenance = provenance.setdefault(server_session["provenance_key"], {})
                    for skill_id, assigned in changes.items():
                        if assigned:
                            client_provenance[str(skill_id)] = source_professions[skill_id]
                        else:
                            client_provenance.pop(str(skill_id), None)
                    if client_provenance:
                        provenance[server_session["provenance_key"]] = client_provenance
                    else:
                        provenance.pop(server_session["provenance_key"], None)
                    _save_skill_limit_provenance(provenance)
                target_group = CAREER_GROUP[target_profession][0]
                for skill_id, assigned in changes.items():
                    skill = inspected_skills[skill_id]
                    skill["assigned_to"] = [owner for owner in skill.get("assigned_to", [])
                                            if owner != target_profession]
                    skill["career_groups"] = [group for group in skill.get("career_groups", [])
                                              if assigned or group != target_group or
                                              group in skill.get("source_groups", [])]
                    if assigned:
                        skill["assigned_to"].append(target_profession)
                        if target_group not in skill["career_groups"]:
                            skill["career_groups"].append(target_group)
                with _skill_limit_server_sessions_lock:
                    server_session["local_paths"] = server_outputs
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("X-Server-Packages-Updated", "main,dynamic")
                self.send_header("X-Server-Backup-Main", backups["主包"])
                self.send_header("X-Server-Backup-Dynamic", backups["动态包"])
                self.send_header("Content-Length", str(len(result)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(result)
                op_log("同步技能限制配置包", {"filename": filename,
                    "target_profession": body.get("target_profession"),
                    "changes": raw_changes, "bytes": len(result),
                    "server_backups": backups})
                return
            if p == "/api/summons/apply":
                client_path_text = str(CFG.get("client_mb_dpk") or "").strip()
                if not client_path_text:
                    raise ValueError("请先在 运维管理 → 连接配置 中设置客户端 mb.dpk 路径并保存")
                client_path = Path(os.path.expanduser(client_path_text))
                if not client_path.is_file():
                    raise ValueError("找不到客户端 mb.dpk：" + str(client_path))
                if not isinstance(body.get("skills"), list):
                    raise ValueError("召唤物技能配置无效")
                catalog_path = Path(RES_DIR) / "doc" / "召唤物列表.csv"
                if not catalog_path.is_file():
                    catalog_path = Path(APP_DIR) / "doc" / "召唤物列表.csv"
                catalog = load_catalog(catalog_path)
                summon_type = str(body.get("summon_type") or "monster")
                if summon_type not in {"monster", "friend"}:
                    raise ValueError("召唤类型无效")
                if summon_type == "friend":
                    client_path_text = str(CFG.get("client_mb_dpk") or "").strip()
                    client_path = Path(os.path.expanduser(client_path_text)) if client_path_text else None
                    catalog += load_friend_catalog(client_path)
                config = {
                    "summon_type": summon_type,
                    "role_id": int(body.get("role_id")),
                    "form_id": int(body.get("form_id")),
                    "skill_name": str(body.get("skill_name") or "").strip(),
                    "summon_cooldown": int(body.get("summon_cooldown", 120)),
                    "skills": [{"id": int(item["id"]), "cooldown": int(item["cooldown"])}
                               for item in body["skills"] if isinstance(item, dict)],
                }
                run_id = uuid.uuid4().hex
                client_output = Path(CACHE_DIR) / f"summon-{run_id}-client.dpk"
                result, client_report = patch_summon_dpk(client_path, client_output, config, catalog=catalog)
                server_paths = {
                    "主包": MB_DPK_FILE_PATH,
                    "动态包": MB_DPK_DYNAMIC_FILE_PATH,
                }
                local_sources = {
                    label: Path(CACHE_DIR) / f"summon-{run_id}-{label}-source.dpk"
                    for label in server_paths
                }
                with _lock:
                    connection = ssh()
                    sftp = connection.open_sftp()
                    try:
                        for label, remote in server_paths.items():
                            sftp.get(remote, str(local_sources[label]))
                    finally:
                        sftp.close()
                server_outputs = {}
                server_reports = {}
                for label, source in local_sources.items():
                    output = Path(CACHE_DIR) / f"summon-{run_id}-{label}-patched.dpk"
                    patched, report = patch_summon_dpk(
                        source, output, config, optional_resources=(label == "动态包"),
                        catalog=catalog, reference=client_path)
                    server_outputs[label] = output
                    server_reports[label] = report

                stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
                backups = {label: remote + ".bak.summon." + stamp
                           for label, remote in server_paths.items()}
                uploads = {label: "/tmp/gmtool-summon-" + uuid.uuid4().hex + ".dpk"
                           for label in server_outputs}
                with _lock:
                    connection = ssh()
                    sftp = connection.open_sftp()
                    try:
                        for label, local in server_outputs.items():
                            sftp.put(str(local), uploads[label])
                    finally:
                        sftp.close()
                    commands = ["set -e", "backup_ready=0", "committed=0",
                        "rollback(){ if [ \"$backup_ready\" = 1 ] && [ \"$committed\" = 0 ]; then " +
                        "cp -p " + shlex.quote(backups["主包"]) + " " + shlex.quote(server_paths["主包"]) + "; " +
                        "cp -p " + shlex.quote(backups["动态包"]) + " " + shlex.quote(server_paths["动态包"]) + "; fi; }",
                        "trap rollback EXIT"]
                    for label, remote in server_paths.items():
                        commands.append("cp -p " + shlex.quote(remote) + " " + shlex.quote(backups[label]))
                    commands.append("backup_ready=1")
                    for label, remote in server_paths.items():
                        staged = remote + ".gmtool.tmp." + uuid.uuid4().hex
                        commands.append("cp " + shlex.quote(uploads[label]) + " " + shlex.quote(staged))
                        commands.append("chown --reference=" + shlex.quote(remote) + " " + shlex.quote(staged))
                        commands.append("chmod --reference=" + shlex.quote(remote) + " " + shlex.quote(staged))
                        commands.append("mv -f " + shlex.quote(staged) + " " + shlex.quote(remote))
                    commands.extend(["committed=1", "trap - EXIT"])
                    commands.extend("rm -f " + shlex.quote(path) for path in uploads.values())
                    _, stdout, stderr = connection.exec_command(
                        "sudo -n sh -c " + shlex.quote("; ".join(commands)), timeout=180)
                    out = stdout.read().decode("utf-8", "replace").strip()
                    err = stderr.read().decode("utf-8", "replace").strip()
                    rc = stdout.channel.recv_exit_status()
                    if rc != 0:
                        raise RuntimeError("服务端召唤物包替换失败：" + (err or out or str(rc)))
                    verify_cmd = "sha256sum " + " ".join(shlex.quote(p) for p in server_paths.values())
                    _, stdout, stderr = connection.exec_command(verify_cmd, timeout=30)
                    hashes = stdout.read().decode("utf-8", "replace").strip().splitlines()
                    if stdout.channel.recv_exit_status() != 0 or len(hashes) != 2:
                        raise RuntimeError("服务端包已替换，但无法读取 SHA-256 校验结果：" +
                                           stderr.read().decode("utf-8", "replace"))
                    for (label, local), line in zip(server_outputs.items(), hashes):
                        expected = server_reports[label]["sha256"]
                        if line.split()[0] != expected:
                            raise RuntimeError(f"服务端{label} SHA-256 与生成包不一致")

                with open(SUMMON_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                filename = f"mb.summon.{hashlib.sha256(result).hexdigest()[:8]}.dpk"
                op_log("应用召唤物技能配置", {"skill_name": config["skill_name"],
                    "role_id": config["role_id"], "form_id": config["form_id"],
                    "skills": config["skills"], "client_sha256": client_report["sha256"],
                    "server_backups": backups})
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("X-Server-Packages-Updated", "main,dynamic")
                self.send_header("X-Server-Backup-Main", backups["主包"])
                self.send_header("X-Server-Backup-Dynamic", backups["动态包"])
                self.send_header("Content-Length", str(len(result)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(result)
                return
            if p == "/api/rates":
                result = set_game_rates(body.get("exp"), body.get("money"))
                op_log("倍率设置", {"exp": result["exp"], "money": result["money"],
                                     "backup": result["backup"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/fate-lock":
                raw_changes = body.get("changes")
                if raw_changes is not None:
                    if not isinstance(raw_changes, list) or not raw_changes:
                        return self._json({"ok": False, "error": "没有待保存的等级锁修改"}, 400)
                    changes = {}
                    for item in raw_changes:
                        if not isinstance(item, dict):
                            return self._json({"ok": False, "error": "等级锁修改格式无效"}, 400)
                        level = item.get("level")
                        enabled = item.get("enabled")
                        if isinstance(level, bool) or not isinstance(level, int) or not isinstance(enabled, bool):
                            return self._json({"ok": False, "error": "等级和开关状态无效"}, 400)
                        if level in changes:
                            return self._json({"ok": False, "error": "等级锁修改重复"}, 400)
                        changes[level] = enabled
                    target_level = None
                else:
                    changes = None
                    target_level = body.get("level")
                    enabled = body.get("enabled", False)
                    if target_level is not None:
                        if isinstance(target_level, bool) or not isinstance(target_level, int) or not isinstance(enabled, bool):
                            return self._json({"ok": False, "error": "等级和开关状态无效"}, 400)
                try:
                    result = set_fate_locks(target_level, enabled if raw_changes is None else False, changes)
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                op_log("解除天劫等级锁定", {"changed": result["changed"],
                                          "total": result["total"],
                                          "levels": list(changes) if changes is not None else [target_level],
                                          "backup": result["backup"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/definitions":
                try:
                    result = set_definitions(body.get("kind"), body.get("values"))
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                op_log(DEFINITION_FILES[result["kind"]]["label"],
                       {"saved": result["saved"], "backup": result["backup"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/shop/add":
                result = add_shop_item(body.get("id"), body.get("price"),
                                       body.get("count", 1), body.get("category_id"),
                                       body.get("subcategory_id", 1))
                op_log("商城上架", {"id": result["id"], "name": result["name"],
                                   "price": result["price"], "count": result["count"],
                                   "category": result["category"],
                                   "backup": result["backup"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/shop/delete":
                result = delete_shop_item(body.get("id"))
                op_log("商城下架", {"id": result["id"], "name": result["name"],
                                   "backup": result["backup"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/shop/delete-batch":
                result = delete_shop_items(body.get("ids"))
                op_log("商城批量下架", {"items": result["deleted"],
                                       "backup": result["backup"]})
                return self._json({"ok": True, "data": result})
            if p == "/api/shop/restart":
                result = start_shop_restart()
                op_log("商城配置后重启请求", {})
                return self._json({"ok": True, "data": result}, 202)
            if p == "/api/server/control":
                action = body.get("action")
                if action not in ("restart", "stop"):
                    return self._json({"ok": False, "error": "未知的服务端操作"}, 400)
                result = start_server_action(action)
                op_log("服务端操作请求", {"action": action})
                return self._json({"ok": True, "data": result}, 202)
            if p == "/api/point":
                r = set_point(body.get("account"), body.get("mode", "add"), body.get("value", 0))
                op_log("充值", {**body, "结果": r})
                return self._json({"ok": True, "data": r})
            if p == "/api/teleport":
                cid, sid = qint(body.get("char_id")), qint(body.get("scene_id"))
                x, y, z = qfloat(body.get("x")), qfloat(body.get("y")), qfloat(body.get("z"))
                if cid <= 0 or sid <= 0:
                    return self._json({"ok": False, "error": "角色或场景 ID 无效"}, 400)
                if not all(math.isfinite(v) for v in (x, y, z)) or (x, y, z) == (0, 0, 0):
                    return self._json({"ok": False, "error": "请输入目标场景的有效坐标，不能使用 (0, 0, 0)"}, 400)
                if not sql(f"SELECT char_id FROM {DB_CHAR}.common_characters WHERE char_id={cid} LIMIT 1;"):
                    return self._json({"ok": False, "error": "角色不存在"}, 404)
                online = char_online(cid)
                if online:
                    return self._json({"ok": False, "error": "角色在线，请先完全退出游戏后再传送"}, 400)
                sets = (f"scene_id={sid},last_pos_x={x},last_pos_y={y},last_pos_z={z},"
                        "copy_entry_x=0,copy_entry_y=0,copy_entry_z=0,dead_area_id=0")
                sql(f"UPDATE {DB_CHAR}.common_characters SET {sets} WHERE char_id={cid};")
                sql(f"UPDATE {DB_CHAR}.entire_characters SET {sets} WHERE char_id={cid};")
                sql(f"DELETE FROM {DB_CHAR}.lock_characters WHERE char_id={cid};")
                saved = sql(f"SELECT scene_id,last_pos_x,last_pos_y,last_pos_z FROM "
                            f"{DB_CHAR}.common_characters WHERE char_id={cid} LIMIT 1;")
                if not saved or qint(saved[0][0]) != sid or any(
                    abs(qfloat(saved[0][i + 1]) - v) > 0.01
                    for i, v in enumerate((x, y, z))
                ):
                    raise RuntimeError("传送位置写入后未能保持，请确认角色已完全离线")
                op_log("传送", {"char_id": cid, "scene": sid, "xyz": [x, y, z], "online": online})
                return self._json({"ok": True, "data": {"scene_name": char_scene_name(sid)}})
            if p == "/api/rename":
                cid = qint(body.get("char_id"))
                new = str(body.get("new_name", "")).strip()
                if not 1 <= len(new) <= 16:
                    return self._json({"ok": False, "error": "名字长度需 1-16 字符"})
                dup = sql(f"SELECT char_id FROM {DB_CHAR}.common_characters "
                          f"WHERE name={qstr(new)} LIMIT 1;")
                if dup and qint(dup[0][0]) != cid:
                    return self._json({"ok": False, "error": "该名字已被占用"})
                rows = sql(f"SELECT name FROM {DB_CHAR}.common_characters WHERE char_id={cid} LIMIT 1;")
                old_name = rows[0][0] if rows else ""
                sql(f"UPDATE {DB_CHAR}.common_characters SET name={qstr(new)} WHERE char_id={cid};")
                sql(f"UPDATE {DB_CHAR}.entire_characters SET name={qstr(new)} WHERE char_id={cid};")
                sql(f"INSERT INTO {DB_CHAR}.char_used_name "
                    "(char_id,old_name,new_name,change_time) "
                    f"VALUES ({cid},{qstr(old_name)},{qstr(new)},{int(time.time())});")
                op_log("改名", {"char_id": cid, "old": old_name, "new": new})
                return self._json({"ok": True, "data": {"old": old_name, "new": new}})
            if p == "/api/silence":
                cid, sec = qint(body.get("char_id")), qint(body.get("seconds"))
                until = int(time.time()) + sec if sec > 0 else 0
                sql(f"UPDATE {DB_CHAR}.common_characters SET silence_time={until} WHERE char_id={cid};")
                op_log("禁言" if sec > 0 else "解除禁言", {"char_id": cid, "seconds": sec})
                return self._json({"ok": True, "data": {"until": until}})
            if p == "/api/ban":
                acct = str(body.get("account", "")).strip()
                name = resolve_account_name(acct)
                hours = qint(body.get("hours"))
                if hours < 0 or hours > 87600:
                    return self._json({"ok": False, "error": "封禁时长需为 1–87600 小时"}, 400)
                if hours > 0:
                    until = int(time.time()) + hours * 3600
                    sql(f"REPLACE INTO {DB_LBA}.lba_ban_accounts (account,endtime) "
                        f"VALUES ({qstr(name)},{until});")
                    sql(f"DELETE FROM {DB_LBA}.lba_ban_accounts WHERE account={qstr(acct)} "
                        f"AND account<>{qstr(name)};")
                    sql(f"REPLACE INTO {DB_CHAR}.restricted_accounts "
                        f"(acct_name,status,out_time) VALUES ({qstr(name)},1,{until});")
                    sql(f"UPDATE {DB_CHAR}.common_accounts SET status=1 "
                        f"WHERE name={qstr(name)};")
                    op_log("封禁", {"account": name, "hours": hours})
                else:
                    sql(f"DELETE FROM {DB_LBA}.lba_ban_accounts "
                        f"WHERE account IN ({qstr(acct)},{qstr(name)});")
                    sql(f"DELETE FROM {DB_CHAR}.restricted_accounts "
                        f"WHERE acct_name IN ({qstr(acct)},{qstr(name)});")
                    sql(f"UPDATE {DB_CHAR}.common_accounts SET status=0 "
                        f"WHERE name={qstr(name)};")
                    op_log("解封", {"account": name})
                return self._json({"ok": True})
            if p == "/api/reset_password":
                name = resolve_account_name(body.get("account", ""))
                password = body.get("password", "")
                if not isinstance(password, str) or not 6 <= len(password) <= 32 or not password.isascii() or not password.isprintable():
                    return self._json({"ok": False, "error": "密码需为 6–32 位可打印 ASCII 字符"}, 400)
                rows = sql(f"SELECT pass FROM {DB_IMS}.malay_playerinfo "
                           f"WHERE account={qstr(name)} LIMIT 1;")
                if not rows:
                    return self._json({"ok": False, "error": "登录账号不存在，无法重置密码"}, 404)
                if not re.fullmatch(r"[0-9a-fA-F]{32}", rows[0][0] or ""):
                    return self._json({"ok": False, "error": "现有密码格式无法确认，已取消重置"}, 409)
                digest = hashlib.md5(password.encode("ascii")).hexdigest()
                sql(f"UPDATE {DB_IMS}.malay_playerinfo SET pass={qstr(digest)} "
                    f"WHERE account={qstr(name)};")
                op_log("密码重置", {"account": name})
                return self._json({"ok": True})
            if p == "/api/charflag":
                cid, act = qint(body.get("char_id")), body.get("action")
                if act == "unlock":
                    sql(f"DELETE FROM {DB_CHAR}.lock_characters WHERE char_id={cid};")
                    sql(f"DELETE FROM {DB_IMS}.lock_characters WHERE char_id={cid};")
                    op_log("清理角色锁", {"char_id": cid})
                elif act == "delete":
                    sql(f"UPDATE {DB_CHAR}.common_characters SET mark_delete=1,"
                        f"delete_time={int(time.time())} WHERE char_id={cid};")
                    op_log("标记删除角色", {"char_id": cid})
                elif act == "restore":
                    sql(f"UPDATE {DB_CHAR}.common_characters SET mark_delete=0,"
                        f"delete_time=0 WHERE char_id={cid};")
                    op_log("恢复角色", {"char_id": cid})
                elif act == "revive":
                    sql(f"UPDATE {DB_CHAR}.common_characters SET dead_area_id=0 WHERE char_id={cid};")
                    op_log("清除死亡状态", {"char_id": cid})
                else:
                    return self._json({"ok": False, "error": "未知操作"})
                return self._json({"ok": True})
            if p == "/api/item_send":
                cid, items = qint(body.get("char_id")), body.get("items") or []
                if not items:
                    return self._json({"ok": False, "error": "没有物品"})
                if char_online(cid) and not body.get("force"):
                    return self._json({"ok": False, "error": "角色在线，需先下线（在线时写库会被覆盖）"})
                r = give_items(cid, items)
                op_log("发物品", {"char_id": cid, "items": items, "结果": r})
                return self._json({"ok": True, "data": r})
            if p == "/api/mail_send":
                cid = qint(body.get("char_id"))
                r = send_mail(cid, body.get("title", "GM 邮件"), body.get("body", ""),
                              body.get("sender", "系统"), body.get("items") or [],
                              str(body.get("kind", "system")))
                op_log("发邮件", {"char_id": cid, "title": body.get("title"), "结果": r})
                return self._json({"ok": True, "data": r})
            if p == "/api/quit":
                self._json({"ok": True})
                threading.Timer(0.4, shutdown).start()
                return
            return self._json({"ok": False, "error": "unknown api"}, 404)
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            op_log(p, body, "fail: " + str(e))
            return self._json({"ok": False, "error": str(e)}, 500)


SRV = None
URL = ""


def open_ui():
    try:
        webbrowser.open(URL)
    except Exception:
        os.startfile(URL)


def port_alive(host, port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def shutdown():
    try:
        if SRV:
            SRV.shutdown()
    except Exception:
        pass


def fatal(msg):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(msg), "夢回尋仙 GM 控制台", 16)
    except Exception:
        pass


def main():
    global SRV, URL
    host = CFG["listen_host"]
    port = int(CFG["listen_port"])
    URL = f"http://{host}:{port}"
    if port_alive(host, port):
        print(f"GM 控制台已在运行：{URL}")
        if CFG.get("auto_open_browser", False):
            open_ui()
        return
    load_items()
    load_scenes()
    try:
        SRV = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        fatal(f"端口 {port} 被占用：{e}\n请修改 gm_config.json 中的 listen_port。")
        return
    print(f"GM 控制台已启动：{URL}")
    print(f"配置：{CONFIG_PATH}")
    threading.Thread(target=_skill_limit_cleanup_worker,
                     name="skill-limit-cache-cleaner", daemon=True).start()
    srv_thread = threading.Thread(target=SRV.serve_forever, daemon=True)
    srv_thread.start()
    if CFG.get("auto_open_browser", False):
        threading.Timer(0.8, open_ui).start()
    while srv_thread.is_alive():
        srv_thread.join(1)


def _bootstrap():
    if sys.stdout is None or sys.stderr is None:
        try:
            f = open(SERVER_LOG, "a", encoding="utf-8", buffering=1)
        except Exception:
            return
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f


if __name__ == "__main__":
    _bootstrap()
    try:
        main()
    except Exception:
        traceback.print_exc()
        fatal(traceback.format_exc()[-1200:])
