import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.dpk_append import _append_stream, _directory, append_entries
from scripts.repack import encode_whsc
from scripts.unpack import BLOCK_SIZE, DpkReader, PACKAGE_PASSWORD, read_u32


class AppendTest(unittest.TestCase):
    def test_roundtrip_and_collision_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.dpk'
            output = Path(folder) / 'output.dpk'
            header = bytearray(BLOCK_SIZE)
            header[:12] = b'whpackage1.0'
            header[32:48] = hashlib.md5(PACKAGE_PASSWORD).digest()
            def encode(data):
                return encode_whsc(data, flags=1, template_header=bytes(48), compression_level=6)
            with source.open('w+b') as fp:
                fp.write(header)
                root = _append_stream(fp, encode(b'original'))
                directory, strings = _directory([('old/file.bin', root)])
                struct.pack_into('<I', header, 132, _append_stream(fp, encode(directory)))
                struct.pack_into('<I', header, 136, _append_stream(fp, encode(strings)))
                struct.pack_into('<I', header, 28, fp.tell() // BLOCK_SIZE)
                fp.seek(0); fp.write(header)
            original = source.read_bytes()
            append_entries(source, output, {'new/sub/file.bin': b'new' * 1000})
            self.assertEqual(source.read_bytes(), original)
            with DpkReader(output) as reader:
                entries = {e.path: e for e in reader.read_entries()}
                self.assertEqual(reader.extract(entries['old/file.bin']), b'original')
                self.assertEqual(reader.extract(entries['new/sub/file.bin']), b'new' * 1000)
                self.assertEqual(read_u32(reader.header, 28), output.stat().st_size // BLOCK_SIZE)
            with self.assertRaises(ValueError):
                append_entries(source, Path(folder) / 'collision.dpk', {'OLD/file.bin': b'x'})
            with self.assertRaises(ValueError):
                append_entries(source, output, {'other': b'x'})
            with self.assertRaises(ValueError):
                append_entries(source, Path(folder) / 'invalid.dpk', {'../bad': b'x'})


if __name__ == '__main__':
    unittest.main()
