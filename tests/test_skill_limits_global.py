import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import skill_limits


class GlobalSkillLimitsTests(unittest.TestCase):
    def test_enable_restore_and_preserve_unrelated_lines(self):
        original = (b'header\r\nname\t3007\t1\t10\t3\tprices\tlevels\r\n'
                    b'other\t3008\t1\t10\t3\tprices\tlevels\r\n')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.dpk'
            source.write_bytes(b'original package')
            captured = []
            def replace(source, name, _unused, output, **kwargs):
                captured.append((name, kwargs['replacement_data']))
                output.write_bytes(b'patched package')
            with patch.object(skill_limits, '_resources', return_value={skill_limits.SKILL_LEARN: original}), \
                    patch.object(skill_limits, 'replace_resource', side_effect=replace):
                skill_limits.patch_global_skill_limits(source, {3007: True}, {3007: 3}, Path(directory))
                skill_limits.patch_global_skill_limits(source, {3007: False}, {3007: 3}, Path(directory))
            self.assertEqual(captured[0], (skill_limits.SKILL_LEARN, original.replace(b'\t10\t3\t', b'\t10\t0\t', 1)))
            self.assertEqual(captured[1][1], original)

    def test_missing_original_profession_is_rejected(self):
        original = b'name\t3007\t1\t10\t0\tprices\tlevels\n'
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.dpk'
            source.write_bytes(b'package')
            with patch.object(skill_limits, '_resources', return_value={skill_limits.SKILL_LEARN: original}):
                with self.assertRaisesRegex(ValueError, '缺少原职业信息'):
                    skill_limits.patch_global_skill_limits(source, {3007: False}, {}, Path(directory))


if __name__ == '__main__':
    unittest.main()
