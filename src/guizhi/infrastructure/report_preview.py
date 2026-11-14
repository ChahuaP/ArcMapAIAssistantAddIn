"""Render one page of a verified report for the desktop viewer."""
import base64
from contextlib import closing
from hashlib import sha256
import struct
import zlib

from pypdfium2 import PdfDocument, raw


def chunk(kind, content):
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))


def preview(path, digest, page):
    data = path.read_bytes()
    if sha256(data).hexdigest() != digest:
        raise ValueError("办理报告内容已变化")
    with PdfDocument(data) as document:
        if not 1 <= page <= len(document):
            raise ValueError("报告页码无效")
        with closing(document[page - 1]) as sheet:
            with closing(sheet.render(scale=1.6, force_bitmap_format=raw.FPDFBitmap_BGR, rev_byteorder=True)) as bitmap:
                rgb = bitmap.to_numpy()
                scanlines = b"".join(b"\x00" + line.tobytes() for line in rgb)
                header = struct.pack(">IIBBBBB", bitmap.width, bitmap.height, 8, 2, 0, 0, 0)
                png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
                       chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
                return {"pages": len(document), "image": base64.b64encode(png).decode("ascii")}
