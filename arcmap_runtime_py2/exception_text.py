# -*- coding: utf-8 -*-
"""Safe Python 2 exception rendering for ArcMap runtime boundaries."""
from __future__ import absolute_import


try:
    unicode
except NameError:
    unicode = str

try:
    binary_type = bytes
except NameError:
    binary_type = str


def to_unicode(value):
    if isinstance(value, unicode):
        return value
    if isinstance(value, binary_type):
        return _decode_bytes(value)
    try:
        return unicode(value)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError, AttributeError):
        return u"<unprintable %s>" % _type_name(value)


def exception_text(exc):
    parts = [to_unicode(value) for value in (getattr(exc, "args", None) or ())]
    parts = [value for value in parts if value]
    name = _type_name(exc)
    if parts:
        return u"%s: %s" % (name, u" | ".join(parts))
    return name


def _decode_bytes(value):
    for encoding in ("utf-8", "mbcs"):
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, UnicodeEncodeError, LookupError):
            pass
    return value.decode("utf-8", "replace")


def _type_name(value):
    try:
        return unicode(value.__class__.__name__)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError, AttributeError):
        return u"Exception"
