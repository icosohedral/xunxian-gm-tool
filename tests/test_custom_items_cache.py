import unittest
from pathlib import Path
from unittest.mock import patch

import gm_server


class CustomItemsCacheTest(unittest.TestCase):
    def test_catalog_is_reused_only_for_matching_package_revisions(self):
        paths = {"主包": Path("main"), "动态包": Path("dynamic")}
        signatures = {"主包": (1, 1, "a"), "动态包": (1, 1, "b")}
        cache = {"revision": None, "items": None}
        with patch.object(gm_server, "_custom_items_cache", cache), \
             patch.object(gm_server, "_ensure_server_mb_cache", return_value=(paths, signatures)), \
             patch.dict(gm_server.CFG, {"client_mb_dpk": ""}), \
             patch.object(gm_server, "custom_item_catalog", return_value=[{"id": 60074}]) as catalog:
            first = gm_server.get_custom_items()
            self.assertEqual(gm_server.get_custom_items(), first)
            self.assertEqual(catalog.call_count, 1)
            signatures["动态包"] = (1, 2, "c")
            changed = gm_server.get_custom_items()
            self.assertEqual(catalog.call_count, 2)
            self.assertEqual(changed["revision"]["动态包"], "c")


if __name__ == "__main__":
    unittest.main()
