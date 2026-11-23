import hashlib
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import repack
from scripts.repack import (BlockChain, ROOT_DATA_CAPACITY, RepackError, WHSC_HEADER_SIZE,
                            decrypt_payload, encode_whsc, encrypt_payload,
                            find_topology_preserving_zlib_parameters, verified_whsc_size,
                            verify_output, zlib_parameter_candidates)
from scripts.unpack import (BLOCK_SIZE, CONTENT_KEY, PACKAGE_KEY, PACKAGE_PASSWORD, DpkReader,
                            read_u32)
from scripts.dpk_append import _append_stream, _directory


def build_package(path, files):
    with path.open("w+b") as fp:
        fp.write(bytes(BLOCK_SIZE))
        entries = [(name, _append_stream(fp, encode_whsc(
            data, flags=3, template_header=bytes(48), compression_level=6)))
            for name, data in files.items()]
        header = bytearray(BLOCK_SIZE)
        header[:12] = b"whpackage1.0"
        header[32:48] = hashlib.md5(PACKAGE_PASSWORD).digest()
        for offset, data in zip((132, 136), _directory(entries)):
            root = _append_stream(fp, encode_whsc(
                data, flags=2, template_header=bytes(48), compression_level=6))
            header[offset:offset + 4] = root.to_bytes(4, "little")
        size = fp.seek(0, 2)
        header[28:32] = (size // BLOCK_SIZE).to_bytes(4, "little")
        fp.seek(0)
        fp.write(header)


class ZlibSearchTests(unittest.TestCase):
    def search(self, sizes, blocks):
        calls = []

        def compress(plain, *, level, mem_level, strategy):
            calls.append((level, mem_level, strategy))
            return bytes(sizes.get(calls[-1], 50_000))

        chain = BlockChain(1, blocks, 0, tuple(range(1, blocks + 1)))
        with patch.object(repack, "compress_zlib", compress):
            return find_topology_preserving_zlib_parameters(b"", chain), calls

    def test_candidates_start_at_defaults_without_duplicates(self):
        candidates = zlib_parameter_candidates()
        self.assertEqual(candidates[0], (6, 8, "default"))
        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertEqual(len(candidates), 81 * 2 + 9 * 2)

    def test_stops_at_first_match_near_defaults(self):
        found, calls = self.search({(6, 5, "default"): 1500, (1, 1, "default"): 1500}, 2)
        self.assertEqual((found.level, found.mem_level, found.strategy), (6, 5, "default"))
        self.assertEqual(found.whsc_size, WHSC_HEADER_SIZE + 1500)
        self.assertLess(len(calls), 25)

    def test_rejects_wrong_block_count(self):
        found, calls = self.search({(6, 8, "default"): ROOT_DATA_CAPACITY - WHSC_HEADER_SIZE}, 2)
        self.assertIsNone(found)
        self.assertEqual(calls, zlib_parameter_candidates())


class EncryptTests(unittest.TestCase):
    def test_every_tail_length_round_trips(self):
        source = random.Random(7)
        for key in (PACKAGE_KEY, CONTENT_KEY):
            for length in range(130):
                data = source.randbytes(length)
                with self.subTest(key=key, length=length):
                    self.assertEqual(decrypt_payload(encrypt_payload(data, key), key), data)


class VerifyCacheTests(unittest.TestCase):
    def setUp(self):
        cache = patch.dict(repack._VERIFIED_WHSC_SIZES, clear=True)
        cache.start()
        self.addCleanup(cache.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "mb.dpk"
        noise = random.Random(3)
        self.files = {"a/one.txt": noise.randbytes(3000), "a/two.txt": noise.randbytes(5000)}
        build_package(self.path, self.files)
        with DpkReader(self.path) as reader:
            self.entries = reader.read_entries()

    def verify(self, target="a/one.txt"):
        entry = next(item for item in self.entries if item.path == target)
        return verify_output(self.path, expected_entries=self.entries, target=entry,
                             expected_plain=self.files[target],
                             expected_size=self.path.stat().st_size)

    def test_repeated_verification_reports_the_same_totals(self):
        self.assertEqual(self.verify(), (2, 8000))
        with patch.object(repack, "decode_whsc", wraps=repack.decode_whsc) as decode:
            self.assertEqual(self.verify(), (2, 8000))
        self.assertEqual(decode.call_count, 1)

    def test_warm_cache_still_detects_damaged_resources(self):
        self.verify()
        for target, damaged in (("a/one.txt", "a/two.txt"), ("a/one.txt", "a/one.txt")):
            with self.subTest(damaged=damaged):
                original = self.path.read_bytes()
                root = next(e.root_block for e in self.entries if e.path == damaged)
                data = bytearray(original)
                data[read_u32(original, root * BLOCK_SIZE + 4) * BLOCK_SIZE + 100] ^= 1
                self.path.write_bytes(data)
                with self.assertRaises((ValueError, RepackError)):
                    self.verify(target)
                self.path.write_bytes(original)

    def test_damaged_stream_is_not_cached(self):
        whsc = bytearray(encode_whsc(b"x" * 100, flags=2, template_header=bytes(48),
                                     compression_level=6))
        whsc[60] ^= 1
        for _ in range(2):
            with self.assertRaises(ValueError):
                verified_whsc_size(bytes(whsc))
        self.assertEqual(repack._VERIFIED_WHSC_SIZES, {})

    def test_cache_is_bounded(self):
        with patch.object(repack, "MAX_VERIFIED_WHSC", 3):
            for value in range(10):
                verified_whsc_size(encode_whsc(bytes([value]), flags=2,
                                               template_header=bytes(48), compression_level=6))
                self.assertLessEqual(len(repack._VERIFIED_WHSC_SIZES), 3)

    def test_entries_list_is_independent(self):
        with DpkReader(self.path) as reader:
            reader.read_entries().clear()
            self.assertEqual(len(reader.read_entries()), 2)


if __name__ == "__main__":
    unittest.main()
