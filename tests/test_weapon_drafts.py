import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.weapon_drafts import search_traits, equipment_attribute_choices


class WeaponTraitSearchTests(unittest.TestCase):
    def test_hide_empty_filters_before_limit_and_preserves_zero_values(self):
        lines = [f'{i}\t\t\t\t\t没有数值' for i in range(1, 202)]
        lines += ['202\t\t\t\t\t力量+0', '203\t\t\t\t\t力量+12']
        entry = SimpleNamespace(path='item/item_trait.txt')

        class Reader:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read_entries(self): return [entry]
            def extract(self, entry): return '\n'.join(lines).encode('gb18030')

        with patch('scripts.weapon_drafts.DpkReader', Reader):
            result = search_traits(Path('unused'), hide_empty_values=True)
            self.assertEqual(result['total'], 2)
            self.assertEqual([row['id'] for row in result['rows']], [202, 203])
            self.assertEqual(len(search_traits(Path('unused'))['rows']), 200)


    def test_attribute_choices_use_type_and_units_from_trait_table(self):
        entry = SimpleNamespace(path='item/item_trait.txt')
        lines = ['1\t45\t16\t0\t\t忽略防御+16',
                 '2\t17\t150\t1\t\t物理会心几率+1.5%',
                 '3\t17\t150\t0\t\t不同单位',
                 '4\t46\t0\t0\t\t忽略法抗+0']
        class Reader:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read_entries(self): return [entry]
            def extract(self, item): return '\n'.join(lines).encode('gb18030')
        with patch('scripts.weapon_drafts.DpkReader', Reader):
            result = equipment_attribute_choices(Path('unused'))
        self.assertEqual([x['id'] for x in result['ignore_defense']], [1])
        self.assertEqual([x['id'] for x in result['physical_crit']], [2])
        self.assertEqual(result['ignore_resistance'], [])


if __name__ == '__main__':
    unittest.main()
