"""Mirror backpack shop products and base prices into the client mb.dpk.

The client resolves a merchant product through its own ``item/item_mcht.txt``
row (物品 ID、文字说明、金币购买折扣) and shows the price computed from the
item's 购买价格 in ``item/item_list*.txt``. A product that exists only in the
server's散文件 therefore has no client row and cannot be bought in game, even
when the server's merchant cache contains it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid

from .repack import replace_resource
from .unpack import DpkReader


PRODUCT_PATH = "item/item_mcht.txt"
ITEM_PATHS = ("item/item_list.txt", "item/item_list2.txt", "item/item_list3.txt")
PRODUCT_ID_COLUMN = 0
ITEM_ID_COLUMN = 1
NAME_COLUMN = 5
DISCOUNT_COLUMN = 23
PRICE_COLUMN = 16
MIN_PRODUCT_COLUMNS = 24


def _read(data: bytes) -> tuple[list[str], str, bool]:
    """Split a table into physical lines, keeping the ending style."""
    source = data.decode("gb18030")
    ending = "\r\n" if "\r\n" in source else "\n"
    lines = source.split(ending)
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines.pop()
    return lines, ending, trailing


def _write(lines: list[str], ending: str, trailing: bool) -> bytes:
    text = ending.join(lines) + (ending if trailing else "")
    return text.encode("gb18030")


def _rows(lines: list[str]) -> dict[int, tuple[int, list[str]]]:
    rows = {}
    for offset, line in enumerate(lines):
        fields = line.split("\t")
        if not fields[0].isdigit():
            continue
        row_id = int(fields[0])
        if row_id in rows:
            raise ValueError("客户端配置表编号重复：" + str(row_id))
        rows[row_id] = (offset, fields)
    return rows


def _row_width(rows: dict[int, tuple[int, list[str]]]) -> int:
    return max([len(fields) for _, fields in rows.values()] + [MIN_PRODUCT_COLUMNS])


def product_row(entry: dict, width: int) -> str:
    fields = [""] * width
    fields[PRODUCT_ID_COLUMN] = str(entry["product_id"])
    fields[ITEM_ID_COLUMN] = str(entry["item_id"])
    fields[NAME_COLUMN] = str(entry.get("name") or "")
    fields[DISCOUNT_COLUMN] = str(entry.get("discount") or "")
    return "\t".join(fields)


def append_products(data: bytes, entries) -> tuple[bytes, list[dict]]:
    """Append the missing product rows, keeping every existing line untouched."""
    lines, ending, trailing = _read(data)
    rows = _rows(lines)
    width = _row_width(rows)
    added = []
    for entry in entries:
        product_id = int(entry["product_id"])
        if product_id in rows:
            continue
        lines.append(product_row(entry, width))
        rows[product_id] = (len(lines) - 1, lines[-1].split("\t"))
        added.append({"product_id": product_id, "item_id": int(entry["item_id"]),
                      "name": str(entry.get("name") or ""),
                      "discount": str(entry.get("discount") or "")})
    return (_write(lines, ending, trailing) if added else data), added


def patch_prices(data: bytes, prices) -> tuple[bytes, list[dict]]:
    """Write the server base purchase price into an item table."""
    wanted = {int(key): str(value) for key, value in (prices or {}).items()}
    if not wanted:
        return data, []
    lines, ending, trailing = _read(data)
    changed = []
    for item_id, (offset, fields) in _rows(lines).items():
        if item_id not in wanted:
            continue
        if len(fields) <= PRICE_COLUMN:
            raise ValueError("客户端物品记录不完整：" + str(item_id))
        if fields[PRICE_COLUMN].strip() == wanted[item_id]:
            continue
        before = fields[PRICE_COLUMN]
        fields[PRICE_COLUMN] = wanted[item_id]
        lines[offset] = "\t".join(fields)
        changed.append({"item_id": item_id, "before": before, "after": wanted[item_id]})
    return (_write(lines, ending, trailing) if changed else data), changed


def _contents(archive: Path) -> dict[str, bytes]:
    with DpkReader(Path(archive)) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        missing = [name for name in (PRODUCT_PATH, *ITEM_PATHS) if name not in entries]
        if missing:
            raise ValueError("客户端包缺少商人商品表或物品表：" + "、".join(missing))
        return {name: reader.extract(entries[name]) for name in (PRODUCT_PATH, *ITEM_PATHS)}


def _item_prices(files: dict[str, bytes]) -> dict[int, str]:
    prices = {}
    for name in ITEM_PATHS:
        for item_id, (_, fields) in _rows(_read(files[name])[0]).items():
            if item_id in prices:
                raise ValueError("客户端物品 ID 重复：" + str(item_id))
            prices[item_id] = fields[PRICE_COLUMN].strip() if len(fields) > PRICE_COLUMN else ""
    return prices


def snapshot(archive: Path) -> dict:
    """Read the client product rows and item base prices."""
    files = _contents(archive)
    products = _rows(_read(files[PRODUCT_PATH])[0])
    return {"products": {key: value[1] for key, value in products.items()},
            "prices": _item_prices(files)}


def apply(archive: Path, output: Path, entries, prices=None) -> dict:
    """Write the missing product rows and base prices into a client package copy."""
    archive, output = Path(archive), Path(output)
    files = _contents(archive)
    products, added = append_products(files[PRODUCT_PATH], entries)
    updates = [(PRODUCT_PATH, products)] if added else []
    price_changes = []
    for name in ITEM_PATHS:
        patched, changed = patch_prices(files[name], prices)
        if changed:
            updates.append((name, patched))
            price_changes.extend(changed)
    known_items = _item_prices(files)
    missing_items = [{"product_id": int(entry["product_id"]), "item_id": int(entry["item_id"]),
                      "name": str(entry.get("name") or "")}
                     for entry in entries if int(entry["item_id"]) not in known_items]
    if not updates:
        shutil.copyfile(archive, output)
        return {"changed": False, "added": [], "prices": [], "missing_items": missing_items}
    staged = archive
    temporary = []
    try:
        for index, (name, data) in enumerate(updates):
            target = output.parent / ("backpack-client-" + uuid.uuid4().hex + f"-{index}.dpk")
            replace_resource(
                staged, name, None, target, line_edit=None, line_edits=None,
                replacement_data=data, compression_level=6, zlib_mem_level=8,
                zlib_strategy="default", auto_zlib_parameters=True,
                allow_chain_growth=True, allow_chain_shrink=True,
                overwrite_output=False, overwrite_report=False,
                max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
            temporary.append(target)
            staged = target
        os.replace(staged, output)
    finally:
        for path in temporary:
            if path != output:
                path.unlink(missing_ok=True)
    return {"changed": True, "added": added, "prices": price_changes,
            "missing_items": missing_items}


def verify(archive: Path, added, prices=None) -> dict:
    """Confirm the appended rows and written prices are readable from the new package."""
    state = snapshot(archive)
    for entry in added:
        product_id = int(entry["product_id"])
        fields = state["products"].get(product_id)
        if fields is None:
            raise ValueError("客户端商品表回读失败：" + str(product_id))
        expected = (str(entry["item_id"]), str(entry.get("name") or ""),
                    str(entry.get("discount") or ""))
        actual = (fields[ITEM_ID_COLUMN] if len(fields) > ITEM_ID_COLUMN else "",
                  fields[NAME_COLUMN] if len(fields) > NAME_COLUMN else "",
                  fields[DISCOUNT_COLUMN] if len(fields) > DISCOUNT_COLUMN else "")
        if actual != expected:
            raise ValueError("客户端商品表字段回读不一致：" + str(product_id))
    for item_id, copper in (prices or {}).items():
        current = state["prices"].get(int(item_id))
        if current is not None and current != str(copper):
            raise ValueError("客户端物品价格回读不一致：" + str(item_id))
    return {"products": len(added), "prices": len(prices or {})}
