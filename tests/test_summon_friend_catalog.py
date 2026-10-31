import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.summon_config import load_friend_catalog, _patch_levelup, _patch_learn


class FriendCatalogTest(unittest.TestCase):
    def test_summon_learn_preserves_selected_profession(self):
        data = ('模板\t3046\t1\t1\t4\t0\t56\n'
                '召唤伙伴\t3126\t1\t1\t1\t0\t56\n').encode('gb18030')
        rows = _patch_learn(data, '召唤伙伴').decode('gb18030').splitlines()
        summon = next(row.split('\t') for row in rows if row.split('\t')[1] == '3126')
        self.assertEqual(summon[4], '1')

    def test_summon_levelup_has_no_mana_cost(self):
        fields = ['模板', '3046'] + [''] * 9 + ['200']
        result = _patch_levelup(('\t'.join(fields) + '\n').encode('gb18030'), '召唤伙伴')
        rows = [row.split('\t') for row in result.decode('gb18030').splitlines()]
        summon = next(row for row in rows if row[1] == '3126')
        self.assertEqual(summon[11], '0')
        self.assertEqual(rows[0][11], '200')

    def test_global_skill_uses_original_profession_and_current_cooldown(self):
        learn = "skill/skill_learn.txt"
        data = "skill/skill_data_yx.txt"
        skill_fields = ["电射", "3007"] + [""] * 19 + ["8"]
        packages = {
            "work": {learn: "电射\t3007\t\t\t0\n其他\t3008\t\t\t4\n",
                     data: "\t".join(skill_fields) + "\n"},
            "reference": {learn: "电射\t3007\t\t\t3\n其他\t3008\t\t\t3\n"},
        }

        class Reader:
            def __init__(self, path):
                self.resources = packages[str(path)]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read_entries(self):
                return [SimpleNamespace(path=path) for path in self.resources]

            def extract(self, entry):
                return self.resources[entry.path].encode("gb18030")

        with patch("scripts.summon_config.DpkReader", Reader), patch.object(Path, "is_file", return_value=True):
            friends = load_friend_catalog(Path("work"), Path("reference"))
        yujian = next(friend for friend in friends if friend["profession_id"] == 3)
        qimen = next(friend for friend in friends if friend["profession_id"] == 4)
        self.assertEqual(yujian["skills"], [{"id": 3007, "name": "电射", "cooldown": "8"}])
        self.assertEqual([skill["id"] for skill in qimen["skills"]], [3008])


if __name__ == "__main__":
    unittest.main()
