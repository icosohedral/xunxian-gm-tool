import ctypes
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class GiantSlayerMathTests(unittest.TestCase):
    def test_thresholds_and_overflow(self):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('C compiler unavailable')
        header = Path(__file__).resolve().parents[1] / 'scripts/native/giant_slayer_math.h'
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'test.c'
            library = Path(d) / 'test.dylib'
            source.write_text(f'#include "{header}"\nint damage(int d,int a,int t){{return gm_giant_scale_damage(d,a,t);}}\nint defense(int d){{return gm_giant_effective_defense(d);}}\nint ignore(int d,int i){{return gm_giant_attack_ignore(d,i);}}\n')
            subprocess.run([cc, '-shared', '-o', str(library), str(source)], check=True, capture_output=True)
            lib = ctypes.CDLL(str(library))
            for target, expected in [(100, 1000), (199, 1000), (200, 1050), (250, 1050), (300, 1100), (10000, 5950)]:
                self.assertEqual(lib.damage(1000, 100, target), expected)
            self.assertEqual(lib.damage(2147483647, 1, 2147483647), 2147483647)
            self.assertEqual(lib.damage(100, 0, 500), 100)
            self.assertEqual(lib.defense(1000), 700)
            self.assertEqual(lib.defense(-1), -1)
            self.assertEqual(lib.ignore(1000, 200), 500)
            self.assertEqual(lib.ignore(0, 200), 200)
            self.assertEqual(lib.ignore(2147483647, 2147483647), 2147483647)
