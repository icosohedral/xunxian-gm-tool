import unittest

from gm_server import _parse_item_type_text, search_item_catalog


class ItemTypeSearchTest(unittest.TestCase):
    def test_type_table_uses_id_and_name_and_keeps_first_match(self):
        text = '\n1\t物品\t其他\n5\t物品\n11\t药品\n11\t替代名称\n坏行\n12\t\n'
        self.assertEqual(_parse_item_type_text(text), {'1': '物品', '5': '物品', '11': '药品'})

    def test_filter_and_pagination_include_items_beyond_first_page(self):
        items = [{'id': i, 'name': f'药品{i}', 'type': '11'} for i in range(1, 82)]
        items += [{'id': 100, 'name': '普通物品', 'type': '1'}]
        result = search_item_catalog(items, {'11': '药品', '1': '物品'}, category='药品', limit=60, offset=60)
        self.assertEqual(result['matched_total'], 81)
        self.assertEqual(result['total'], 82)
        self.assertEqual(len(result['rows']), 21)
        self.assertEqual(result['rows'][0]['id'], 61)
        self.assertTrue(all(row['type_name'] == '药品' for row in result['rows']))

    def test_same_type_name_groups_ids_and_preserves_raw_type(self):
        items = [{'id': 1, 'name': '甲', 'type': '1'}, {'id': 2, 'name': '乙', 'type': '5'},
                 {'id': 3, 'name': '丙', 'type': '999'}, {'id': 4, 'name': '丁', 'type': ''}]
        result = search_item_catalog(items, {'1': '物品', '5': '物品'}, category='物品')
        self.assertEqual([row['type'] for row in result['rows']], ['1', '5'])
        self.assertEqual(set(result['types']), {'物品', '未知类型', '未分类'})

    def test_numeric_query_prioritizes_id_without_duplicating_name_match(self):
        items = [{'id': 12, 'name': '药12', 'type': '11'},
                 {'id': 120, 'name': '药', 'type': '11'},
                 {'id': 2, 'name': '药12', 'type': '11'}]
        result = search_item_catalog(items, {'11': '药品'}, query='12')
        self.assertEqual([row['id'] for row in result['rows']], [12, 120, 2])
        self.assertEqual(result['matched_total'], 3)


if __name__ == '__main__':
    unittest.main()
