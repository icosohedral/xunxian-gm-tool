import unittest

from scripts.backpack_client import append_products, patch_prices, product_row


def product_table(*rows):
    """Client product tables start with an empty line and use 44 columns."""
    lines = [""] + ["\t".join(row) for row in rows]
    return ("\r\n".join(lines) + "\r\n").encode("gb18030")


def product_fields(product_id, item_id, name="", discount=""):
    fields = [""] * 44
    fields[0], fields[1], fields[5], fields[23] = str(product_id), str(item_id), name, discount
    return fields


ITEM_TABLE = ("物品ID\t物品名称\t物品类\t物品掉落所属类\t道具图片\t模型ID\t文字介绍ID\t物品品质\t"
              "法宝阶数\t职业限制\t天赋限制\t等级限制\t技能限制\t性别限制\t唯一\t流通金钱\t购买价格\r\n"
              "13312\t二级返虚符\t9\t\txyx1002\t\t9762\t4\t\t\t\t\t\t\t\t1\t100000\r\n"
              "1940\t一阶绯红石\t6\t\tbs940\t\t3046\t3\t\t\t\t\t\t\t\t1\t10000\r\n").encode("gb18030")


class BackpackClientTest(unittest.TestCase):
    def test_appends_row_matching_existing_width(self):
        data = product_table(product_fields(27867, 13312, "二级返虚符", "150"))
        updated, added = append_products(data, [
            {"product_id": 28786, "item_id": 1940, "name": "一阶绯红石", "discount": "1000"}])
        self.assertEqual([row["product_id"] for row in added], [28786])
        text = updated.decode("gb18030")
        self.assertTrue(text.startswith("\r\n"))
        lines = text.split("\r\n")
        self.assertEqual(len(lines[0]), 0)
        self.assertEqual(lines[-2].split("\t"), product_fields(28786, 1940, "一阶绯红石", "1000"))
        self.assertTrue(text.endswith("\r\n"))

    def test_skips_products_the_client_already_has(self):
        data = product_table(product_fields(27867, 13312, "二级返虚符", "150"))
        updated, added = append_products(data, [
            {"product_id": 27867, "item_id": 13312, "name": "二级返虚符", "discount": "150"}])
        self.assertEqual(added, [])
        self.assertIs(updated, data)

    def test_pads_short_rows_to_the_widest_row(self):
        data = product_table(["7", "9"], product_fields(27867, 13312, "二级返虚符", "150"))
        updated, _ = append_products(data, [
            {"product_id": 28786, "item_id": 1940, "name": "一阶绯红石", "discount": "1000"}])
        self.assertEqual(len(updated.decode("gb18030").split("\r\n")[-2].split("\t")), 44)

    def test_product_row_keeps_empty_optional_fields(self):
        fields = product_row({"product_id": 28791, "item_id": 1976, "name": "", "discount": "80"}, 44)
        self.assertEqual(fields.split("\t"), product_fields(28791, 1976, "", "80"))

    def test_patches_only_the_requested_item_price(self):
        updated, changed = patch_prices(ITEM_TABLE, {1940: 250000})
        self.assertEqual(changed, [{"item_id": 1940, "before": "10000", "after": "250000"}])
        lines = updated.decode("gb18030").split("\r\n")
        self.assertEqual(lines[1].split("\t")[16], "100000")
        self.assertEqual(lines[2].split("\t")[16], "250000")
        self.assertEqual(lines[2].split("\t")[1], "一阶绯红石")

    def test_price_already_matches(self):
        updated, changed = patch_prices(ITEM_TABLE, {1940: 10000})
        self.assertEqual(changed, [])
        self.assertIs(updated, ITEM_TABLE)


if __name__ == "__main__":
    unittest.main()
