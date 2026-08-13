from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from shared_runtime.file_semantics import FileSemanticError, inspect_file


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)
    pixels = b"\x00" + b"\x00\x00\x00" * 2
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(pixels)) + _chunk(b"IEND", b""))


class FileSemanticTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_csv_seals_structure_counts_and_every_column_value_hash(self):
        path = self.root / "result.csv"
        path.write_bytes("OBJECTID,NAME\n1,甲\n2,乙\n".encode("utf-8"))
        evidence = inspect_file(str(path), "csv")
        self.assertEqual(["OBJECTID", "NAME"], evidence["header"])
        self.assertEqual(2, evidence["record_count"])
        self.assertEqual({"OBJECTID", "NAME"}, set(evidence["columns"]))
        self.assertEqual(2, evidence["columns"]["OBJECTID"]["unique_count"])

    def test_csv_rejects_wrong_extension_empty_duplicate_header_and_ragged_rows(self):
        cases = {
            "wrong.txt": b"A\n1\n",
            "empty.csv": b"",
            "duplicate.csv": b"A,A\n1,2\n",
            "ragged.csv": b"A,B\n1\n",
        }
        for name, content in cases.items():
            path = self.root / name
            path.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(FileSemanticError):
                inspect_file(str(path), "csv")

    def test_png_requires_decodable_complete_crc_valid_image(self):
        valid = self.root / "map.png"
        valid.write_bytes(_png())
        evidence = inspect_file(str(valid), "png")
        self.assertEqual((2, 1), (evidence["width"], evidence["height"]))
        for name, content in (
            ("truncated.png", _png()[:-5]),
            ("crc.png", _png()[:-13] + b"\x00" * 13),
            ("wrong.jpg", _png()),
        ):
            path = self.root / name
            path.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(FileSemanticError):
                inspect_file(str(path), "png")


if __name__ == "__main__":
    unittest.main()
