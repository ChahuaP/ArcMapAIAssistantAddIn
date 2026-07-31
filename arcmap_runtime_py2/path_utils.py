# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import tempfile


try:
    unicode
except NameError:
    unicode = str

try:
    basestring
except NameError:
    basestring = (str,)


PY2 = sys.version_info[0] == 2


class PathEncodingError(Exception):
    pass


def to_unicode_path(value):
    if value is None:
        return u""
    if isinstance(value, unicode):
        return value
    if PY2 and isinstance(value, str):
        for encoding in _path_decoding_order():
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                pass
        raise PathEncodingError(u"路径编码错误：无法按 Windows 文件系统编码或 UTF-8 解码路径。")
    if not PY2 and isinstance(value, bytes):
        for encoding in ("utf-8", sys.getfilesystemencoding() or "utf-8"):
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                pass
        raise PathEncodingError(u"路径编码错误：无法解码字节路径。")
    try:
        return unicode(value)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        raise PathEncodingError(u"路径编码错误：路径值无法转换为文本。")


def normalize_path(value):
    return os.path.normcase(os.path.abspath(to_unicode_path(value)))


def normpath(value):
    return os.path.normpath(to_unicode_path(value))


def normcase(value):
    return os.path.normcase(to_unicode_path(value))


def abspath(value):
    return os.path.abspath(to_unicode_path(value))


def dirname(value):
    return os.path.dirname(to_unicode_path(value))


def basename(value):
    return os.path.basename(to_unicode_path(value))


def splitext(value):
    return os.path.splitext(to_unicode_path(value))


def join_path(*parts):
    values = []
    for part in parts:
        if part is None:
            continue
        text = to_unicode_path(part)
        if text:
            values.append(text)
    if not values:
        return u""
    return os.path.join(*values)


def exists(path):
    return os.path.exists(to_unicode_path(path))


def isfile(path):
    return os.path.isfile(to_unicode_path(path))


def isdir(path):
    return os.path.isdir(to_unicode_path(path))


def getsize(path):
    return os.path.getsize(to_unicode_path(path))


def listdir(path):
    return os.listdir(to_unicode_path(path))


def makedirs(path):
    text = to_unicode_path(path)
    if text and not os.path.isdir(text):
        os.makedirs(text)


def remove(path):
    os.remove(to_unicode_path(path))


def open_text(path, mode="r"):
    if "b" in mode:
        raise ValueError("open_text mode must not contain b.")
    if not PY2:
        return open(to_unicode_path(path), mode, encoding="utf-8")
    return open(to_unicode_path(path), mode)


def open_binary(path, mode="rb"):
    if "b" not in mode:
        mode += "b"
    return open(to_unicode_path(path), mode)


def temporary_sibling(path):
    target = to_unicode_path(path)
    handle, temporary = tempfile.mkstemp(prefix=basename(target) + u".", suffix=u".tmp", dir=dirname(target))
    os.close(handle)
    return to_unicode_path(temporary)


def publish_new_file(temporary_path, target_path):
    temporary = to_unicode_path(temporary_path)
    target = to_unicode_path(target_path)
    if exists(target):
        raise IOError(u"目标文件已存在：%s" % target)
    os.rename(temporary, target)


def _path_decoding_order():
    values = []
    for encoding in (sys.getfilesystemencoding(), "mbcs", "utf-8"):
        if encoding and encoding not in values:
            values.append(encoding)
    return values
