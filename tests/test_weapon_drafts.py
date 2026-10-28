import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.weapon_drafts import search_traits


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


if __name__ == '__main__':
    unittest.main()
