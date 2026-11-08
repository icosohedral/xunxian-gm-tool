import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.dpk_append import _append_stream, _directory
from scripts.repack import RepackError, encode_whsc, replace_resource
from scripts.unpack import BLOCK_SIZE, DpkReader, PACKAGE_PASSWORD, read_u32


def build_package(path: Path) -> None:
    header = bytearray(BLOCK_SIZE)
    header[:12] = b"whpackage1.0"
    struct.pack_into("<I", header, 16, 2)
    struct.pack_into("<I", header, 20, BLOCK_SIZE)
    header[32:48] = hashlib.md5(PACKAGE_PASSWORD).digest()

    def encode(data: bytes) -> bytes:
        return encode_whsc(data, flags=1, template_header=bytes(48), compression_level=6)

    with path.open("w+b") as fp:
        fp.write(header)
        root = _append_stream(fp, encode(b"original"))
        directory, strings = _directory([("test/file.bin", root)])
        struct.pack_into("<I", header, 132, _append_stream(fp, encode(directory)))
        struct.pack_into("<I", header, 136, _append_stream(fp, encode(strings)))
        struct.pack_into("<I", header, 28, fp.tell() // BLOCK_SIZE)
        fp.seek(0)
        fp.write(header)


def repack(source: Path, output: Path):
    return replace_resource(
        source, "test/file.bin", None, output,
        line_edit=None, replacement_data=b"expanded" * 1000,
        compression_level=0, zlib_mem_level=8, zlib_strategy="default",
        auto_zlib_parameters=False, allow_chain_shrink=False,
        overwrite_output=False, overwrite_report=False,
        max_input_size=10000, report_path=None, allow_chain_growth=True,
    )


class RepackBlockCountTest(unittest.TestCase):
    def test_repairs_underreported_block_count_during_growth(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.dpk"
            output = Path(folder) / "output.dpk"
            build_package(source)
            with source.open("r+b") as fp:
                fp.seek(28)
                count = struct.unpack("<I", fp.read(4))[0]
                fp.seek(28)
                fp.write(struct.pack("<I", count - 1))

            result = repack(source, output)
            self.assertGreater(len(result.used_blocks), len(result.original_blocks))
            with DpkReader(output) as reader:
                self.assertEqual(read_u32(reader.header, 28), output.stat().st_size // BLOCK_SIZE)
                entry = reader.read_entries()[0]
                self.assertEqual(reader.extract(entry), b"expanded" * 1000)

    def test_rejects_overreported_block_count(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.dpk"
            output = Path(folder) / "output.dpk"
            build_package(source)
            with source.open("r+b") as fp:
                fp.seek(28)
                count = struct.unpack("<I", fp.read(4))[0]
                fp.seek(28)
                fp.write(struct.pack("<I", count + 1))

            with self.assertRaisesRegex(RepackError, "文件可能被截断"):
                repack(source, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
