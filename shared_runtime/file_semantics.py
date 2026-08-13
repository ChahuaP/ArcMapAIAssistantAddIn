# -*- coding: utf-8 -*-
from __future__ import absolute_import

import csv
import hashlib
import io
import os
import struct
import zlib


try:
    unicode
except NameError:
    unicode = str


class FileSemanticError(ValueError):
    pass


def inspect_file(path, output_format):
    if not isinstance(path, (str, unicode)) or not path:
        raise FileSemanticError("file path is required")
    expected = u"." + unicode(output_format).lower()
    if not unicode(path).lower().endswith(expected):
        raise FileSemanticError("file extension does not match declared format")
    with open(path, "rb") as handle:
        data = handle.read()
    if output_format == "csv":
        return _csv_semantics(data)
    if output_format == "png":
        return _png_semantics(data)
    raise FileSemanticError("unsupported file format")


def _csv_semantics(data):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileSemanticError("CSV must use strict UTF-8 encoding")
    if not text:
        raise FileSemanticError("CSV is empty")
    try:
        if str is bytes:
            rows = [[cell.decode("utf-8") for cell in row]
                    for row in csv.reader(io.BytesIO(data), dialect="excel")]
        else:
            rows = list(csv.reader(io.StringIO(text), dialect="excel", strict=True))
    except (csv.Error, UnicodeError):
        raise FileSemanticError("CSV dialect or encoding is invalid")
    if not rows or not rows[0] or any(not value for value in rows[0]):
        raise FileSemanticError("CSV header must be non-empty")
    header = rows[0]
    if len(set(header)) != len(header):
        raise FileSemanticError("CSV header fields must be unique")
    width = len(header)
    if any(len(row) != width for row in rows[1:]):
        raise FileSemanticError("CSV rows have inconsistent field counts")
    columns = {}
    for index, name in enumerate(header):
        values = [row[index] for row in rows[1:]]
        encoded = u"\n".join(values).encode("utf-8")
        columns[name] = {
            "value_hash": hashlib.sha256(encoded).hexdigest(),
            "non_empty_count": sum(1 for value in values if value != u""),
            "unique_count": len(set(values)),
        }
    return {"format": "csv", "encoding": encoding, "dialect": "excel",
            "header": header, "record_count": len(rows) - 1,
            "columns": columns}


def _png_semantics(data):
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise FileSemanticError("PNG signature is invalid")
    offset, chunks, idat = len(signature), [], []
    while offset < len(data):
        if offset + 12 > len(data):
            raise FileSemanticError("PNG is truncated")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise FileSemanticError("PNG chunk is truncated")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if (zlib.crc32(kind + payload) & 0xffffffff) != expected_crc:
            raise FileSemanticError("PNG chunk CRC is invalid")
        chunks.append((kind, payload))
        if kind == b"IDAT":
            idat.append(payload)
        offset = end
        if kind == b"IEND":
            break
    if offset != len(data) or not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise FileSemanticError("PNG chunk order or IEND is invalid")
    ihdr = chunks[0][1]
    if len(ihdr) != 13 or len(chunks[-1][1]) != 0 or not idat:
        raise FileSemanticError("PNG lacks a valid IHDR, IDAT, or IEND")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    allowed_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (not width or not height or channels is None or bit_depth not in allowed_depths[color_type]
            or compression != 0 or filtering != 0 or interlace != 0):
        raise FileSemanticError("PNG IHDR is unsupported or invalid")
    try:
        decoded = zlib.decompress(b"".join(idat))
    except zlib.error:
        raise FileSemanticError("PNG image data cannot be decoded")
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected_size = height * (row_bytes + 1)
    if len(decoded) != expected_size:
        raise FileSemanticError("PNG decoded image size is inconsistent")
    for row in range(height):
        if decoded[row * (row_bytes + 1)] > 4:
            raise FileSemanticError("PNG scanline filter is invalid")
    return {"format": "png", "width": width, "height": height,
            "bit_depth": bit_depth, "color_type": color_type,
            "interlace": interlace, "decoded_bytes": len(decoded)}
