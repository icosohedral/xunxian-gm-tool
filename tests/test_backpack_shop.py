import unittest
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
from decimal import Decimal
import secrets
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

from scripts.backpack_shop import (MERCHANT_PATH, PRODUCT_PATH, ITEM_PATHS, _MERCHANT_CACHE_READER,
                                   _REMOTE_APPLY, _format_price, _merchant_delta, _price_copper,
                                   catalog, prepare)


def fixture():
    products = "商品ID\t物品ID\t数量\n10\t100\t\n11\t101\t\n".encode("gb18030")
    merchants = ("序号\t商人类型\t商品ID\t备注\n"
                 "238\t5\t10\tVIP1\n239\t4\t10*11\tVIP2\n"
                 "283\t4\t10\tSVIP1\n284\t4\t10\tSVIP2\n285\t4\t10\tSVIP3\n").encode("gb18030")
    return {PRODUCT_PATH: products, MERCHANT_PATH: merchants}


ITEMS = {100: {"name": "甲", "price": "100000"},
         101: {"name": "乙", "price": "100000"},
         102: {"name": "丙", "price": "100000"}}


class BackpackShopTest(unittest.TestCase):
    def test_add_to_vip_group_with_exact_gold_price(self):
        original = fixture()
        changed, operation = prepare(original, action="add", item_id=102,
                                     ranges=["VIP"], price="5", unit="金", base_price=100000,
                                     item_name="丙")
        self.assertEqual(operation["product_id"], 12)
        rows = catalog(changed, ITEMS)["rows"]
        added = next(row for row in rows if row["product_id"] == 12)
        self.assertEqual(added["merchant_ids"], [238, 239])
        self.assertEqual(added["ranges"], ["VIP"])
        self.assertEqual(added["price"], "5 金")
        self.assertEqual(changed[PRODUCT_PATH].decode("gb18030").splitlines()[-1].split("\t")[23], "50")

    def test_gold_brick_converts_to_100_gold(self):
        changed, _ = prepare(fixture(), action="add", item_id=102,
                             ranges=["SVIP"], price="1", unit="砖", base_price=1_000_000,
                             item_name="丙")
        self.assertEqual(changed[PRODUCT_PATH].decode("gb18030").splitlines()[-1].split("\t")[23], "100")
        added = next(row for row in catalog(changed, {**ITEMS, 102: {"name": "丙", "price": "1000000"}})["rows"]
                     if row["product_id"] == 12)
        self.assertEqual(added["price"], "1 砖")
        self.assertEqual(added["ranges"], ["SVIP"])

    def test_delete_only_unlinks_merchants(self):
        original = fixture()
        changed, _ = prepare(original, action="delete", product_id=10,
                             merchant_ids=[238, 283])
        self.assertEqual(changed[PRODUCT_PATH], original[PRODUCT_PATH])
        rows = catalog(changed, ITEMS)["rows"]
        self.assertEqual(next(row for row in rows if row["product_id"] == 10)["merchant_ids"],
                         [239, 284, 285])

    def test_reject_unrepresentable_price(self):
        with self.assertRaisesRegex(ValueError, "无法.*精确"):
            prepare(fixture(), action="add", item_id=102, ranges=["VIP"],
                    price="1", unit="金", base_price=170000, item_name="丙")

    def test_reject_duplicate_in_selected_merchant(self):
        with self.assertRaisesRegex(ValueError, "已有该物品"):
            prepare(fixture(), action="add", item_id=100, ranges=["VIP"],
                    price="1", unit="金", base_price=100000, item_name="甲")

    def test_currency_price_is_precise_to_copper(self):
        self.assertEqual(_price_copper("0.02", "金"), 200)
        self.assertEqual(_price_copper("1", "砖"), 1_000_000)
        with self.assertRaisesRegex(ValueError, "精确到 1 铜"):
            _price_copper("0.00001", "金")
        self.assertEqual(_format_price(Decimal(200)), "2 银")
        self.assertEqual(_format_price(Decimal(1_000_001)), "1.000001 砖")

    def test_runtime_cache_requires_added_goods_and_removed_goods_to_match(self):
        before = fixture()
        after, change = prepare(before, action="add", item_id=102, ranges=["VIP"],
                                price="5", unit="金", base_price=100000, item_name="丙")
        delta = _merchant_delta(before, after)
        scope = {}
        exec(_MERCHANT_CACHE_READER, scope)
        check = scope["merchant_cache_effect"]
        old_runtime = {"1": {"238": [10], "239": [10, 11], "283": [10], "284": [10], "285": [10]}}
        self.assertEqual(check(old_runtime, delta, False)["state"], "pending")
        old_runtime["1"]["238"].append(change["product_id"])
        old_runtime["1"]["239"].append(change["product_id"])
        self.assertEqual(check(old_runtime, delta, False)["state"], "applied")
        self.assertEqual(check(old_runtime, delta, True)["state"], "unverified")
        removed, _ = prepare(after, action="delete", product_id=change["product_id"],
                             merchant_ids=[238, 239])
        self.assertEqual(check(old_runtime, _merchant_delta(after, removed), False)["state"], "pending")


class BackpackPriceTransactionTest(unittest.TestCase):
    def test_save_only_writes_files_without_touching_running_services(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work).resolve()
            base = root / "runtime"
            (root / "state").mkdir()
            (base / "mb").mkdir(parents=True)
            (base / "mb.dpk").write_bytes(b"package")
            before = fixture()
            after = {name: data + b"changed" for name, data in before.items()}
            for name, data in before.items():
                path = base / "mb" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            digest = lambda data: hashlib.sha256(data).hexdigest()
            specs = [{"id": number, "rows": 1, "checks": []} for number in (50, 95)]
            request = {"before": {key: digest(data) for key, data in before.items()},
                       "after": {key: base64.b64encode(data).decode() for key, data in after.items()},
                       "before_specs": specs, "after_specs": specs,
                       "source_sha256": digest(b"package"), "save_only": True}
            def unexpected(*args, **kwargs):
                raise AssertionError("save-only must not access running services")
            def write_atomic(path, data, owner):
                path.write_bytes(data)
            namespace = {"BASE": base, "ROOT": root, "RuntimeInspector": unexpected,
                         "NativeGMClient": unexpected, "write_atomic": write_atomic,
                         "sha256": lambda path: digest(Path(path).read_bytes()),
                         **{module.__name__: module for module in
                            (os, secrets, shutil, sys, tempfile, time, base64, hashlib, json)}}
            output = io.StringIO()
            with patch.object(sys, "stdin", io.StringIO(json.dumps(request))), contextlib.redirect_stdout(output):
                exec(_REMOTE_APPLY, namespace)
            self.assertTrue(json.loads(output.getvalue())["ok"])
            for name, data in after.items():
                self.assertEqual((base / "mb" / name).read_bytes(), data)

    def run_transaction(self, fail_reload=False, existing_item_file=False):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work).resolve()
            base = root / "runtime"
            (root / "state").mkdir()
            (base / "mb").mkdir(parents=True)
            package_path = base / "mb.dpk"
            package_path.write_bytes(b"original-package")
            before = {**fixture(), **{name: b"original-item-table" for name in ITEM_PATHS}}
            after = {name: data + b"changed" for name, data in before.items()}
            for name in (PRODUCT_PATH, MERCHANT_PATH):
                path = base / "mb" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(before[name])
            if existing_item_file:
                (base / "mb" / ITEM_PATHS[1]).write_bytes(before[ITEM_PATHS[1]])
            digest = lambda data: hashlib.sha256(data).hexdigest()
            specs = [{"id": number, "rows": 1, "checks": []} for number in (41, 50, 95)]
            commands = []
            failed = False

            class Runtime:
                def snapshot(self, selected):
                    return {str(line): {"pid": line, "tables": {
                        str(spec["id"]): {"rows": spec["rows"], "mismatches": 0, "examples": []}
                        for spec in selected}} for line in (1, 9)}

            class Client:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def command(self, line, command):
                    nonlocal failed
                    if command.startswith("help "):
                        return "reloadexcel help"
                    commands.append((line, command))
                    if fail_reload and not failed and command == "reloadexcel 50":
                        failed = True
                        return "failed"
                    return "ok"

            def write_atomic(path, data, owner):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            with tempfile.NamedTemporaryFile(prefix="gmtool-backpack-", suffix=".dpk", dir="/tmp") as upload:
                upload.write(b"updated-package")
                upload.flush()
                request = {"before": {key: digest(data) for key, data in before.items()},
                    "after": {key: base64.b64encode(data).decode() for key, data in after.items()},
                    "before_specs": specs, "after_specs": specs,
                    "source_sha256": digest(b"original-package"),
                    "package": {"upload": upload.name, "before_sha256": digest(b"original-package"),
                                "after_sha256": digest(b"updated-package")}}
                namespace = {"BASE": base, "ROOT": root, "RuntimeInspector": Runtime,
                    "NativeGMClient": Client, "write_atomic": write_atomic,
                    "sha256": lambda path: digest(Path(path).read_bytes()),
                    "reload_succeeded": lambda reply, command: reply == "ok",
                    **{module.__name__: module for module in
                       (os, secrets, shutil, sys, tempfile, time, base64, hashlib, json)}}
                output = io.StringIO()
                with patch.object(sys, "stdin", io.StringIO(json.dumps(request))), contextlib.redirect_stdout(output):
                    try:
                        exec(_REMOTE_APPLY, namespace)
                    except SystemExit:
                        pass
                result = json.loads(output.getvalue())
            expected_package = b"original-package" if fail_reload else b"updated-package"
            self.assertEqual(package_path.read_bytes(), expected_package)
            for name in (PRODUCT_PATH, MERCHANT_PATH):
                self.assertEqual((base / "mb" / name).read_bytes(), (before if fail_reload else after)[name])
            for name in ITEM_PATHS:
                path = base / "mb" / name
                if existing_item_file and name == ITEM_PATHS[1]:
                    self.assertEqual(path.read_bytes(), (before if fail_reload else after)[name])
                else:
                    self.assertFalse(path.exists())
            return result, commands

    def test_success_persists_server_package_and_cleans_new_loose_files(self):
        result, commands = self.run_transaction(existing_item_file=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["purchase_price_initialized"])
        self.assertEqual(len(commands), 6)

    def test_reload_failure_restores_package_and_files_then_reloads_old_tables(self):
        result, commands = self.run_transaction(fail_reload=True)
        self.assertFalse(result["ok"])
        self.assertIn("热重载失败", result["error"])
        self.assertNotIn("恢复失败", result["error"])
        self.assertEqual(commands[-6:], [(line, f"reloadexcel {number}")
                                        for line in (1, 9) for number in (41, 50, 95)])


class BackpackUnpricedApiTest(unittest.TestCase):
    def test_add_49985_uses_entered_price_and_server_package(self):
        import gm_server as server
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "main.dpk"
            source.write_bytes(b"original")
            source_hash = hashlib.sha256(b"original").hexdigest()
            updated_hash = hashlib.sha256(b"updated").hexdigest()
            before = fixture()
            revision = {key: hashlib.sha256(data).hexdigest() for key, data in before.items()}
            revision["server_mb"] = source_hash
            item = {"id": 49985, "name": "仙玉凭证（100000仙玉）", "price": ""}

            def initialize(package, output, files, item_id, price, unit, *, expected_base_price):
                self.assertEqual((package, item_id, price, unit), (source, 49985, "1", "砖"))
                self.assertEqual(expected_base_price, 0)
                Path(output).write_bytes(b"updated")
                return {"before_sha256": source_hash, "after_sha256": updated_hash, "copper": 1_000_000}

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(server, "CACHE_DIR", work))
                stack.enter_context(patch.object(server, "_ensure_server_mb_cache", return_value=(
                    {"主包": source}, {"主包": (8, 0, source_hash)})))
                stack.enter_context(patch.object(server, "backpack_package_items", side_effect=[
                    {**ITEMS, 49985: item}, {**ITEMS, 49985: {**item, "price": "1000000"}}]))
                stack.enter_context(patch.object(server, "ssh", return_value=object()))
                stack.enter_context(patch.object(server, "read_backpack_remote", return_value=before))
                stack.enter_context(patch.object(server, "initialize_backpack_item_price", side_effect=initialize))
                reload = stack.enter_context(patch.object(server, "reload_backpack_remote", return_value={
                    "purchase_price_initialized": True}))
                cache = stack.enter_context(patch.object(server, "_update_server_mb_cache"))
                result = server.edit_backpack_shop({"action": "add", "item_id": 49985,
                    "ranges": ["VIP"], "price": "1", "unit": "砖", "revision": revision})
            self.assertTrue(result["reload"]["purchase_price_initialized"])
            self.assertEqual(result["catalog"]["revision"]["server_mb"], updated_hash)
            row = next(row for row in result["catalog"]["rows"] if row["item_id"] == 49985)
            self.assertEqual(row["price"], "1 砖")
            self.assertEqual(row["ranges"], ["VIP"])
            self.assertEqual(reload.call_args.kwargs["source_sha256"], source_hash)
            self.assertEqual(set(cache.call_args.args[0]), {"主包"})

    def test_inexact_price_updates_base_even_when_another_merchant_sells_item(self):
        import gm_server as server
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "main.dpk"
            source.write_bytes(b"original")
            source_hash = hashlib.sha256(b"original").hexdigest()
            updated_hash = hashlib.sha256(b"updated").hexdigest()
            before = fixture()
            revision = {key: hashlib.sha256(data).hexdigest() for key, data in before.items()}
            revision["server_mb"] = source_hash
            item = {"id": 101, "name": "乙", "price": "170000"}

            def update(package, output, files, item_id, price, unit, *, expected_base_price):
                self.assertEqual((item_id, price, unit, expected_base_price), (101, "1", "金", 170000))
                self.assertIn(b"10*11", files[MERCHANT_PATH])
                Path(output).write_bytes(b"updated")
                return {"before_sha256": source_hash, "after_sha256": updated_hash, "copper": 10000}

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(server, "CACHE_DIR", work))
                stack.enter_context(patch.object(server, "_ensure_server_mb_cache", return_value=(
                    {"主包": source}, {"主包": (8, 0, source_hash)})))
                stack.enter_context(patch.object(server, "backpack_package_items", side_effect=[
                    {**ITEMS, 101: item}, {**ITEMS, 101: {**item, "price": "10000"}}]))
                stack.enter_context(patch.object(server, "ssh", return_value=object()))
                stack.enter_context(patch.object(server, "read_backpack_remote", return_value=before))
                stack.enter_context(patch.object(server, "initialize_backpack_item_price", side_effect=update))
                stack.enter_context(patch.object(server, "reload_backpack_remote", return_value={
                    "purchase_price_initialized": True}))
                stack.enter_context(patch.object(server, "_update_server_mb_cache"))
                result = server.edit_backpack_shop({"action": "add", "item_id": 101,
                    "ranges": ["SVIP"], "price": "1", "unit": "金", "revision": revision})
            rows = [row for row in result["catalog"]["rows"] if row["item_id"] == 101]
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["price"] for row in rows}, {"1 金"})
            self.assertEqual(result["change"]["previous_purchase_price_copper"], 170000)


if __name__ == "__main__":
    unittest.main()
