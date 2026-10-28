"""VIP backpack merchant catalog and verified native table reload."""

from __future__ import annotations

import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shlex
import uuid

from .item_reload import ITEM_PATHS, build_bundle as build_item_bundle
from .repack import replace_resource
from .unpack import DpkReader



MERCHANTS = {238: "VIP1", 239: "VIP2–7", 283: "SVIP1", 284: "SVIP2", 285: "SVIP3"}
RANGES = {"VIP": (238, 239), "SVIP": (283, 284, 285)}
PRICE_COPPER = {"金": 10_000, "砖": 1_000_000}
PRODUCT_PATH = "item/item_mcht.txt"
MERCHANT_PATH = "object/npc_mcht.txt"
REMOTE_ROOT = "/opt/xxserver/runtime/xx/shm/TTYGZS/mb"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _format_price(copper: Decimal) -> str:
    if copper >= PRICE_COPPER["砖"]:
        value, unit = copper / PRICE_COPPER["砖"], "砖"
    elif copper >= PRICE_COPPER["金"]:
        value, unit = copper / PRICE_COPPER["金"], "金"
    elif copper >= 100:
        value, unit = copper / 100, "银"
    else:
        value, unit = copper, "铜"
    text = f"{value.normalize():f}"
    return f"{text} {unit}"


def _decode(data: bytes) -> str:
    return data.decode("gb18030")


def _lines(data: bytes) -> tuple[list[str], str]:
    source = _decode(data)
    ending = "\r\n" if "\r\n" in source else "\n"
    return source.splitlines(), ending


def _encode(lines: list[str], ending: str) -> bytes:
    return (ending.join(lines) + ending).encode("gb18030")


def _table_rows(data: bytes, key: str) -> dict[int, tuple[int, list[str]]]:
    lines, _ = _lines(data)
    if not lines or not lines[0]:
        raise ValueError(f"{key} 缺少表头")
    rows = {}
    for offset, line in enumerate(lines[1:], 1):
        fields = line.split("\t")
        if not fields[0].isdigit():
            continue
        row_id = int(fields[0])
        if row_id in rows:
            raise ValueError(f"{key} 编号 {row_id} 重复")
        rows[row_id] = (offset, fields)
    return rows


def read_remote(connection) -> dict[str, bytes]:
    result = {}
    for name in (PRODUCT_PATH, MERCHANT_PATH):
        target = f"{REMOTE_ROOT}/{name}"
        _, stdout, stderr = connection.exec_command(
            "sudo -n cat -- " + shlex.quote(target), timeout=20)
        data = stdout.read()
        error = stderr.read().decode("utf-8", "replace").strip()
        if stdout.channel.recv_exit_status():
            raise RuntimeError(error or f"读取背包商店配置失败：{name}")
        _table_rows(data, name)
        result[name] = data
    return result


def package_items(package: Path) -> dict[int, dict]:
    """Use server purchase prices, including prices initialized by this feature."""
    items = {}
    with DpkReader(Path(package)) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        for name in ITEM_PATHS:
            if name not in entries:
                raise ValueError("服务端物品表缺失：" + name)
            for item_id, (_, fields) in _table_rows(reader.extract(entries[name]), name).items():
                if item_id in items or len(fields) <= 17:
                    raise ValueError("服务端物品表记录重复或不完整：" + str(item_id))
                items[item_id] = {"id": item_id, "name": fields[1], "price": fields[16]}
    return items


def initialize_item_price(package: Path, output: Path, files, item_id, price, unit,
                          *, expected_base_price=0) -> dict:
    """Persist the requested base purchase price in the server package only."""
    copper = _price_copper(price, unit)
    target = None
    with DpkReader(Path(package)) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        for name in ITEM_PATHS:
            data = reader.extract(entries[name])
            record = _table_rows(data, name).get(item_id)
            if record is None:
                continue
            if target is not None:
                raise ValueError("服务端物品 ID 重复")
            index, fields = record
            if len(fields) <= 17 or not (fields[16].strip() or "0").isdigit():
                raise ValueError("物品购买价格式无效")
            if int(fields[16].strip() or "0") != expected_base_price:
                raise ValueError("物品购买价已变化，请刷新后重试")
            # Keep every other field, including NPC sell-back price and voucher effects.
            lines = _decode(data).splitlines(keepends=True)
            body = lines[index].rstrip("\r\n")
            suffix = lines[index][len(body):]
            fields[16] = str(copper)
            lines[index] = "\t".join(fields) + suffix
            target = name, "".join(lines).encode("gb18030")
    if target is None:
        raise ValueError("物品 ID 不在服务端物品表中")
    replace_resource(Path(package), target[0], None, Path(output),
                     line_edit=None, replacement_data=target[1], compression_level=6,
                     zlib_mem_level=8, zlib_strategy="default", auto_zlib_parameters=False,
                     allow_chain_shrink=True, allow_chain_growth=True,
                     overwrite_output=False, overwrite_report=False,
                     max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
    original = build_item_bundle(Path(package), [item_id])
    updated = build_item_bundle(Path(output), [item_id])
    for bundle, expected in ((original, expected_base_price), (updated, copper)):
        table = bundle["table"]
        position = next(row for row, column, value in table["checks"]
                        if column == 0 and value == item_id)
        table["checks"].append([position, 16, expected])
    return {"before_sha256": original["source_sha256"],
            "after_sha256": updated["source_sha256"],
            "before_table": original["table"], "after_table": updated["table"],
            "before_files": {entry["path"]: entry["sha256"] for entry in original["files"]},
            "files": updated["files"], "item_id": item_id, "copper": copper,
            "previous_copper": expected_base_price}


def catalog(files: dict[str, bytes], items: dict[int, dict]) -> dict:
    products = _table_rows(files[PRODUCT_PATH], PRODUCT_PATH)
    merchants = _table_rows(files[MERCHANT_PATH], MERCHANT_PATH)
    offered = {}
    for merchant_id in MERCHANTS:
        if merchant_id not in merchants:
            raise ValueError(f"商人模板 {merchant_id} 不存在")
        fields = merchants[merchant_id][1]
        for raw_id in filter(None, fields[2].split("*")):
            if not raw_id.isdigit() or int(raw_id) not in products:
                raise ValueError(f"商人 {merchant_id} 引用了无效商品 {raw_id}")
            offered.setdefault(int(raw_id), []).append(merchant_id)
    rows = []
    for product_id, merchant_ids in offered.items():
        fields = products[product_id][1]
        item_id = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0
        item = items.get(item_id) or {}
        base_price = int(item.get("price") or 0) if str(item.get("price") or "").isdigit() else 0
        discount = fields[23].strip() if len(fields) > 23 else ""
        if any(fields[index].strip() for index in (9, 10, 11, 12, 13, 21, 22, 24, 25, 26, 27, 28)
               if index < len(fields)):
            price_label = "按原商品配置"
        elif discount == "-1":
            price_label = "免费"
        elif base_price and (not discount or discount.isdigit()):
            price_copper = Decimal(base_price) * Decimal(discount or 100) / 100
            price_label = _format_price(price_copper)
        else:
            price_label = "价格未知"
        rows.append({"product_id": product_id, "item_id": item_id,
                     "name": item.get("name") or (fields[5] if len(fields) > 5 else "") or f"物品 {item_id}",
                     "merchant_ids": merchant_ids,
                     "ranges": [name for name, ids in RANGES.items() if any(key in merchant_ids for key in ids)],
                     "price": price_label})
    rows.sort(key=lambda row: (row["name"].casefold(), row["product_id"]))
    return {"rows": rows, "ranges": list(RANGES),
            "revision": {key: _sha(value) for key, value in files.items()}}


def _selected(value) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError("请选择至少一个 VIP 商人")
    selected = []
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw not in MERCHANTS:
            raise ValueError("商人编号无效")
        if raw not in selected:
            selected.append(raw)
    return selected


def _selected_ranges(value) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError("请选择 VIP 或 SVIP 售卖范围")
    selected = []
    for name in value:
        if not isinstance(name, str) or name not in RANGES:
            raise ValueError("售卖范围无效")
        selected.extend(key for key in RANGES[name] if key not in selected)
    return selected


def _price_copper(price, unit) -> int:
    if unit not in PRICE_COPPER:
        raise ValueError("价格单位必须为金或砖")
    try:
        amount = Decimal(str(price))
    except (InvalidOperation, TypeError):
        raise ValueError("请输入有效的出售价格") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("出售价格必须大于 0")
    raw_price = amount * PRICE_COPPER[unit]
    if raw_price > 2_147_483_647:
        raise ValueError("出售价格超过游戏支持的范围")
    if raw_price != raw_price.to_integral_value():
        raise ValueError("出售价格最多精确到 1 铜")
    return int(raw_price)


class PriceNotRepresentable(ValueError):
    """The desired price cannot be expressed using this base price and discount."""


def _price_discount(price, unit, base_price) -> int:
    raw_price = _price_copper(price, unit)
    if isinstance(base_price, bool) or not isinstance(base_price, int) or base_price <= 0:
        raise ValueError("该物品没有有效的金币原价")
    discount = Decimal(raw_price) * 100 / base_price
    if discount != discount.to_integral_value() or not 1 <= discount <= 1000:
        raise PriceNotRepresentable("此价格无法用该物品的金币原价精确设置；可设置范围为原价的 1%～1000%，按 1% 递增")
    return int(discount)


def prepare(files: dict[str, bytes], *, action: str, merchant_ids=None, ranges=None, item_id=None,
            product_id=None, price=None, unit=None, base_price=None) -> tuple[dict[str, bytes], dict]:
    selected = _selected_ranges(ranges) if action == "add" else _selected(merchant_ids)
    product_lines, product_ending = _lines(files[PRODUCT_PATH])
    merchant_lines, merchant_ending = _lines(files[MERCHANT_PATH])
    products = _table_rows(files[PRODUCT_PATH], PRODUCT_PATH)
    merchants = _table_rows(files[MERCHANT_PATH], MERCHANT_PATH)
    for merchant_id in selected:
        if merchant_id not in merchants:
            raise ValueError(f"商人模板 {merchant_id} 不存在")
    if action == "add":
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1:
            raise ValueError("物品 ID 无效")
        discount = _price_discount(price, unit, base_price)
        for merchant_id in selected:
            ids = [int(value) for value in filter(None, merchants[merchant_id][1][2].split("*"))]
            if any(int(products[key][1][1]) == item_id for key in ids):
                raise ValueError(f"{MERCHANTS[merchant_id]} 已有该物品")
        new_id = max(products, default=0) + 1
        if new_id > 65535:
            raise ValueError("商品 ID 已用尽")
        width = max(24, len(product_lines[0].split("\t")))
        fields = [""] * width
        fields[0], fields[1], fields[23] = str(new_id), str(item_id), str(discount)
        product_lines.append("\t".join(fields))
        product_id = new_id
    elif action == "delete":
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id not in products:
            raise ValueError("商品 ID 不存在")
        missing = [MERCHANTS[key] for key in selected
                   if str(product_id) not in merchants[key][1][2].split("*")]
        if missing:
            raise ValueError("这些商人没有该商品：" + "、".join(missing))
    else:
        raise ValueError("背包商店操作无效")
    for merchant_id in selected:
        line_index, fields = merchants[merchant_id]
        ids = list(filter(None, fields[2].split("*")))
        if action == "add":
            ids.append(str(product_id))
        else:
            ids.remove(str(product_id))
        fields[2] = "*".join(ids)
        merchant_lines[line_index] = "\t".join(fields)
    updated = {PRODUCT_PATH: _encode(product_lines, product_ending) if action == "add" else files[PRODUCT_PATH],
               MERCHANT_PATH: _encode(merchant_lines, merchant_ending)}
    return updated, {"action": action, "product_id": product_id, "item_id": item_id,
                     "merchants": selected, "price": price if action == "add" else None,
                     "unit": unit if action == "add" else None}


def _merchant_specs(files):
    products = _table_rows(files[PRODUCT_PATH], PRODUCT_PATH)
    merchants = _table_rows(files[MERCHANT_PATH], MERCHANT_PATH)
    product_spec = {"id": 50, "path": PRODUCT_PATH,
                    "rows": len(_lines(files[PRODUCT_PATH])[0]) - 1, "checks": []}
    merchant_spec = {"id": 95, "path": MERCHANT_PATH,
                     "rows": len(_lines(files[MERCHANT_PATH])[0]) - 1,
                     "checks": [], "typed_checks": []}
    offered = set()
    for merchant_id in MERCHANTS:
        index, fields = merchants[merchant_id]
        ids = [int(value) for value in filter(None, fields[2].split("*"))]
        offered.update(ids)
        merchant_spec["typed_checks"].append([index - 1, 2, "array", ids])
    for product_id in sorted(offered):
        index, fields = products[product_id]
        product_spec["checks"].extend([[index - 1, 0, product_id], [index - 1, 1, int(fields[1])]])
        if len(fields) > 23 and fields[23].strip().lstrip("-").isdigit():
            product_spec["checks"].append([index - 1, 23, int(fields[23])])
    return [product_spec, merchant_spec]


def _merchant_ids(files):
    merchants = _table_rows(files[MERCHANT_PATH], MERCHANT_PATH)
    return {str(key): [int(value) for value in filter(None, merchants[key][1][2].split("*"))]
            for key in MERCHANTS}


def _merchant_delta(before, after):
    previous, current = _merchant_ids(before), _merchant_ids(after)
    return {key: {"added": sorted(set(current[key]) - set(previous[key])),
                  "removed": sorted(set(previous[key]) - set(current[key]))}
            for key in current}


def inspect_cache_remote(connection, files):
    """Compare saved merchant lists with the running merchant objects without modifying them."""
    agent = Path(__file__).with_name("skill_reload_agent.py").read_text(encoding="utf-8")
    agent = agent.replace("if __name__ == '__main__':\n    main()", "")
    code = agent + "\n" + _MERCHANT_CACHE_READER + r'''
try:
    print(json.dumps({"ok": True, "data": merchant_cache_snapshot(RuntimeInspector())}, ensure_ascii=False))
except Exception as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    sys.exit(1)
'''
    _, stdout, stderr = connection.exec_command("sudo -n python3 -c " + shlex.quote(code), timeout=45)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(error or output or "无法读取运行中的商人列表") from exc
    if status or not result.get("ok"):
        raise RuntimeError(result.get("error") or error or "无法读取运行中的商人列表")
    desired = _merchant_ids(files)
    differences = []
    for line, merchants in result["data"].items():
        for key, ids in desired.items():
            actual = set(merchants[key])
            missing, extra = sorted(set(ids) - actual), sorted(actual - set(ids))
            if missing or extra:
                differences.append({"line": int(line), "merchant_id": int(key),
                                    "missing": missing, "extra": extra})
    return {"state": "different" if differences else "matched", "differences": differences}


def reload_remote(connection, before: dict[str, bytes], after: dict[str, bytes], *,
                  source_sha256=None, price_change=None, package=None) -> dict:
    """Commit merchant changes and an optional server-only base price as one transaction."""
    agent = Path(__file__).with_name("skill_reload_agent.py").read_text(encoding="utf-8")
    agent = agent.replace("if __name__ == '__main__':\n    main()", "")
    code = agent + "\n" + _MERCHANT_CACHE_READER + "\n" + _REMOTE_APPLY
    payload = {"before": {key: _sha(value) for key, value in before.items()},
               "after": {key: base64.b64encode(value).decode("ascii") for key, value in after.items()},
               "before_specs": _merchant_specs(before), "after_specs": _merchant_specs(after),
               "source_sha256": source_sha256, "merchant_changes": _merchant_delta(before, after)}
    uploaded = None
    try:
        if price_change is not None:
            if not package or _sha(Path(package).read_bytes()) != price_change["after_sha256"]:
                raise ValueError("服务端购买价补丁包校验失败")
            uploaded = "/tmp/gmtool-backpack-" + uuid.uuid4().hex + ".dpk"
            with connection.open_sftp() as sftp:
                sftp.put(str(package), uploaded)
            payload["package"] = {"upload": uploaded,
                                  "before_sha256": price_change["before_sha256"],
                                  "after_sha256": price_change["after_sha256"]}
            payload["source_sha256"] = price_change["before_sha256"]
            payload["before"].update(price_change["before_files"])
            payload["after"].update({entry["path"]: entry["content"] for entry in price_change["files"]})
            payload["before_specs"].insert(0, price_change["before_table"])
            payload["after_specs"].insert(0, price_change["after_table"])
        stdin, stdout, stderr = connection.exec_command(
            "sudo -n python3 -c " + shlex.quote(code), timeout=240)
        stdin.write(json.dumps(payload).encode("utf-8"))
        stdin.channel.shutdown_write()
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        status = stdout.channel.recv_exit_status()
        try:
            result = json.loads(output)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(error or output or "背包商店热重载未返回有效结果") from exc
        if status or not result.get("ok"):
            raise RuntimeError(result.get("error") or error or "背包商店热重载失败")
        data = result["data"]
        try:
            native = refresh_cache_remote(connection, payload["merchant_changes"], bool(price_change))
            data["merchant_cache"] = native["cache"]
            data["native_refresh"] = native["calls"]
        except Exception as exc:
            data["merchant_cache"] = {"state": "pending", "error": str(exc)}
        return data
    finally:
        if uploaded:
            try:
                with connection.open_sftp() as sftp:
                    sftp.remove(uploaded)
            except Exception:
                pass


def refresh_cache_remote(connection, changes, price_changed=False):
    """Refresh the five native merchant objects, then inspect their live lists."""
    directory = Path(__file__).with_name("native")
    agent = Path(__file__).with_name("skill_reload_agent.py").read_text(encoding="utf-8")
    agent = agent.replace("if __name__ == '__main__':\n    main()", "")
    refresh_agent = (directory / "merchant_hot_reload_agent.py").read_text(encoding="utf-8")
    code = agent + "\n" + _MERCHANT_CACHE_READER + "\n" + refresh_agent
    payload = {
        "merchant_changes": changes,
        "price_changed": bool(price_changed),
        "native_source": base64.b64encode((directory / "merchant_refresh.c").read_bytes()).decode("ascii"),
        "gate_source": base64.b64encode((directory / "tracer_gate.py").read_bytes()).decode("ascii"),
    }
    stdin, stdout, stderr = connection.exec_command(
        "sudo -n python3 -c " + shlex.quote(code), timeout=240)
    stdin.write(json.dumps(payload).encode("utf-8"))
    stdin.channel.shutdown_write()
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(error or output or "商人缓存原生刷新没有返回有效结果") from exc
    if status or not result.get("ok"):
        raise RuntimeError(result.get("error") or error or "商人缓存原生刷新失败")
    return result["data"]


_MERCHANT_CACHE_READER = r'''
def merchant_cache_snapshot(runtime):
    """Read the verified 32-bit build's initialized merchant objects."""
    result = {}
    for line, process in runtime.processes.items():
        proc = Path('/proc') / str(process['pid'])
        if (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] != process['start']:
            raise RuntimeError('游戏线路进程已变化')
        fd = os.open(proc / 'mem', os.O_RDONLY)
        try:
            def u32(address):
                if not 0x10000 <= address <= 0xfffefffc:
                    raise RuntimeError('商人缓存地址异常')
                data = os.pread(fd, 4, address)
                if len(data) != 4:
                    raise RuntimeError('商人缓存读取不完整')
                return struct.unpack('<I', data)[0]
            manager = process['base'] + 0x1198620
            if not 1 <= u32(manager + 24) <= 4096:
                raise RuntimeError('商人缓存数量异常')
            merchants = {}
            for merchant_id in (238, 239, 283, 284, 285):
                node = u32(manager + 12)
                for _ in range(100):
                    if not node:
                        raise RuntimeError('运行中缺少背包商人 ' + str(merchant_id))
                    key = u32(node + 16)
                    if key == merchant_id:
                        break
                    node = u32(node + (8 if merchant_id < key else 12))
                else:
                    raise RuntimeError('商人缓存索引异常')
                merchant = u32(node + 20)
                if u32(merchant) != merchant_id:
                    raise RuntimeError('商人缓存编号异常')
                head = merchant + 8
                cursor, previous, seen, ids = u32(head), head, set(), []
                while cursor != head:
                    if not cursor or cursor in seen or len(seen) >= 4096 or u32(cursor + 4) != previous:
                        raise RuntimeError('商人商品链表异常')
                    seen.add(cursor)
                    product = u32(cursor + 8)
                    ids.append(u32(product + 4))
                    previous, cursor = cursor, u32(cursor)
                if u32(head + 4) != previous:
                    raise RuntimeError('商人商品链表尾部异常')
                merchants[str(merchant_id)] = ids
            result[str(line)] = merchants
        finally:
            os.close(fd)
    return result

def merchant_cache_effect(snapshot, changes, price_changed):
    differences = []
    for line, merchants in snapshot.items():
        for key, change in changes.items():
            actual = set(merchants[key])
            missing = sorted(set(change['added']) - actual)
            retained = sorted(set(change['removed']) & actual)
            if missing or retained:
                differences.append({'line': int(line), 'merchant_id': int(key),
                                    'missing': missing, 'retained': retained})
    state = 'pending' if differences else ('unverified' if price_changed else 'applied')
    return {'state': state, 'differences': differences}
'''


_REMOTE_APPLY = r'''
import fcntl
from pathlib import Path

def check_snapshot(snapshot, specs, label):
    for line, state in snapshot.items():
        for spec in specs:
            table = state["tables"][str(spec["id"])]
            if table["rows"] != spec["rows"] or table["mismatches"]:
                raise RuntimeError(label + "：线路 " + line + " 表 " + str(spec["id"]) + " " + str(table["examples"]))

def replace_main(source, owner):
    fd, temporary = tempfile.mkstemp(prefix=".backpack-mb-", dir=BASE)
    try:
        with os.fdopen(fd, "wb") as destination, Path(source).open("rb") as original:
            shutil.copyfileobj(original, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, owner.st_mode & 0o777)
        os.chown(temporary, owner.st_uid, owner.st_gid)
        os.replace(temporary, BASE / "mb.dpk")
    finally:
        Path(temporary).unlink(missing_ok=True)

def reload_tables(numbers):
    with NativeGMClient() as client:
        for line in (1, 9):
            for number in numbers:
                command = "reloadexcel " + str(number)
                reply = client.command(line, command)
                if not reload_succeeded(reply, command):
                    raise RuntimeError("线路 " + str(line) + " 热重载失败：" + reply[:300])

try:
    request = json.load(sys.stdin)
    package = request.get("package")
    item_paths = ["item/item_list.txt", "item/item_list2.txt", "item/item_list3.txt"] if package else []
    names = ["item/item_mcht.txt", "object/npc_mcht.txt", *item_paths]
    paths = {name: BASE / "mb" / name for name in names}
    if set(request["before"]) != set(paths) or set(request["after"]) != set(paths):
        raise ValueError("背包商店事务文件清单无效")
    before_hashes = request["before"]
    after = {key: base64.b64decode(request["after"][key], validate=True) for key in paths}
    before_specs, after_specs = request["before_specs"], request["after_specs"]
    numbers = [41, 50, 95] if package else [50, 95]
    if [spec["id"] for spec in before_specs] != numbers or [spec["id"] for spec in after_specs] != numbers:
        raise ValueError("背包商店事务表清单无效")
    source_hash = request.get("source_sha256")
    package_path = BASE / "mb.dpk"
    with (ROOT / "state/gmtool-skill-reload.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if source_hash and sha256(package_path) != source_hash:
            raise ValueError("服务端主包已变化，请刷新后重试")
        if package:
            upload = Path(package["upload"])
            if (upload.parent != Path("/tmp") or not upload.name.startswith("gmtool-backpack-")
                    or upload.is_symlink() or not upload.is_file()
                    or sha256(upload) != package["after_sha256"]
                    or package["before_sha256"] != source_hash):
                raise ValueError("服务端购买价补丁包无效")
        owner = package_path.stat()
        runtime = RuntimeInspector()
        before = runtime.snapshot(before_specs)
        check_snapshot(before, before_specs, "运行中的配置与当前文件不一致")
        originals = {}
        for key, path in paths.items():
            if path.is_symlink() or path.resolve() != path or (path.exists() and not path.is_file()):
                raise ValueError("背包商店配置路径无效：" + key)
            if not path.exists() and key not in item_paths:
                raise ValueError("背包商店散文件缺失：" + key)
            originals[key] = path.read_bytes() if path.exists() else None
            if originals[key] is not None and hashlib.sha256(originals[key]).hexdigest() != before_hashes[key]:
                raise ValueError("配置已被其他操作修改或存在其他来源的物品覆盖文件：" + key)
        with NativeGMClient() as client:
            for line in (1, 9):
                if not client.command(line, "help reloadexcel").startswith("reloadexcel "):
                    raise RuntimeError("线路不支持 reloadexcel：" + str(line))
        changed = []
        package_changed = command_attempted = False
        stamp = str(int(time.time())) + "-" + secrets.token_hex(4)
        backups = {}
        for key, path in paths.items():
            if originals[key] is None:
                continue
            backup = path.with_name(path.name + ".bak.backpack-" + stamp)
            shutil.copy2(path, backup)
            if backup.read_bytes() != originals[key]:
                raise RuntimeError("配置备份校验失败：" + key)
            backups[key] = str(backup)
        if package:
            main_backup = package_path.with_name("mb.dpk.bak.backpack-" + stamp)
            shutil.copy2(package_path, main_backup)
            if sha256(main_backup) != source_hash:
                raise RuntimeError("服务端主包备份校验失败")
            backups["mb.dpk"] = str(main_backup)
        try:
            if source_hash and sha256(package_path) != source_hash:
                raise RuntimeError("部署前服务端主包已变化")
            if package:
                replace_main(upload, owner)
                package_changed = True
                if sha256(package_path) != package["after_sha256"]:
                    raise RuntimeError("服务端主包写后校验失败")
            for key, path in paths.items():
                changed.append(key)
                write_atomic(path, after[key], owner)
                if path.read_bytes() != after[key]:
                    raise RuntimeError("配置文件回读失败：" + key)
            command_attempted = True
            reload_tables(numbers)
            verified = runtime.snapshot(after_specs)
            check_snapshot(verified, after_specs, "热重载回读校验失败")
            # Missing originals were only temporary loading files; the new server package persists the price.
            for key in item_paths:
                if originals[key] is None:
                    paths[key].unlink()
            expected_hash = package["after_sha256"] if package else source_hash
            if expected_hash and sha256(package_path) != expected_hash:
                raise RuntimeError("热重载期间服务端主包发生变化")
            try:
                cache = merchant_cache_effect(merchant_cache_snapshot(runtime),
                                              request.get("merchant_changes", {}), bool(package))
            except Exception as cache_error:
                cache = {"state": "unverified", "error": str(cache_error)}
        except Exception as exc:
            recovery = []
            if package_changed:
                try:
                    if sha256(package_path) != package["after_sha256"]:
                        raise RuntimeError("主包已被其他操作更新，未覆盖该更新")
                    replace_main(main_backup, owner)
                except Exception as restore_error:
                    recovery.append(str(restore_error))
            for key in reversed(changed):
                try:
                    if originals[key] is None:
                        paths[key].unlink(missing_ok=True)
                    else:
                        write_atomic(paths[key], originals[key], owner)
                except Exception as restore_error:
                    recovery.append(key + ": " + str(restore_error))
            if not recovery and command_attempted:
                try:
                    reload_tables(numbers)
                    check_snapshot(runtime.snapshot(before_specs), before_specs, "恢复回读校验失败")
                except Exception as restore_error:
                    recovery.append(str(restore_error))
            message = str(exc)
            if recovery: message += "；恢复失败：" + "；".join(recovery)
            raise RuntimeError(message) from exc
    print(json.dumps({"ok": True, "data": {"lines": [1, 9], "tables": numbers,
        "rows": {str(spec["id"]): spec["rows"] for spec in after_specs}, "backups": backups,
        "purchase_price_initialized": bool(package),
        "merchant_cache": cache,
        "pids": {key: value["pid"] for key, value in verified.items()}}}, ensure_ascii=False))
except Exception as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    sys.exit(1)
'''
