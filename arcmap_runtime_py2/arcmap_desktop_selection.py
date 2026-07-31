# -*- coding: utf-8 -*-
"""ArcMap Desktop 10.2 selection-state operations for feature layers."""
from __future__ import absolute_import

import arcpy


try:
    unicode
except NameError:
    unicode = str


OID_BATCH_SIZE = 900


def capture_oids(layer, description=None):
    """Return the exact ArcMap Desktop feature-layer selection as sorted OIDs."""
    description = description or arcpy.Describe(layer)
    return _parse_fid_set(getattr(description, "FIDSet"))


def restore_oids(layer, expected_oids):
    """Restore and exactly verify a feature-layer selection using ArcMap tools."""
    expected = _canonical_oids(expected_oids)
    description = arcpy.Describe(layer)
    oid_field = _required_oid_field(description)
    if not expected:
        arcpy.SelectLayerByAttribute_management(layer, "CLEAR_SELECTION")
    else:
        data_source = _data_source(layer, description)
        field_sql = arcpy.AddFieldDelimiters(data_source, oid_field)
        if not field_sql:
            raise RuntimeError("ArcMap Desktop OID field delimiter is empty: %s" % oid_field)
        for index in range(0, len(expected), OID_BATCH_SIZE):
            where_clause = _oid_where_clause(field_sql, expected[index:index + OID_BATCH_SIZE])
            selection_type = "NEW_SELECTION" if index == 0 else "ADD_TO_SELECTION"
            arcpy.SelectLayerByAttribute_management(layer, selection_type, where_clause)
    actual = capture_oids(layer)
    if actual != expected:
        raise RuntimeError(
            "ArcMap Desktop selection restore verification failed: expected %s, got %s"
            % (expected, actual))


def has_selection(layer):
    return bool(capture_oids(layer))


def _required_oid_field(description):
    field_name = getattr(description, "OIDFieldName", None)
    if not field_name or not _text(field_name).strip():
        raise RuntimeError("ArcMap Desktop feature layer has no OID field.")
    return _text(field_name).strip()


def _data_source(layer, description):
    for value in (
            getattr(description, "catalogPath", None),
            getattr(layer, "dataSource", None),
            getattr(description, "path", None)):
        if value is None:
            continue
        data_source = _text(value).strip()
        if data_source:
            return data_source
    raise RuntimeError("ArcMap Desktop feature layer has no data source for OID delimiters.")


def _oid_where_clause(field_sql, oids):
    if not oids:
        raise RuntimeError("ArcMap Desktop selection batch is empty.")
    return u"%s IN (%s)" % (field_sql, u",".join(_text(oid) for oid in oids))


def _parse_fid_set(fid_set):
    if fid_set is None:
        raise RuntimeError("ArcMap Desktop feature layer does not expose FIDSet.")
    text = _text(fid_set)
    if text == u"":
        return []
    values = []
    for token in text.split(u";"):
        token = token.strip()
        if not token:
            raise RuntimeError("ArcMap Desktop FIDSet is malformed: %s" % text)
        try:
            oid = int(token)
        except (TypeError, ValueError):
            raise RuntimeError("ArcMap Desktop FIDSet contains a non-integer OID: %s" % token)
        if oid < 0 or _text(oid) != token:
            raise RuntimeError("ArcMap Desktop FIDSet contains an invalid OID: %s" % token)
        values.append(oid)
    if len(set(values)) != len(values):
        raise RuntimeError("ArcMap Desktop FIDSet contains duplicate OIDs: %s" % text)
    return sorted(values)


def _canonical_oids(values):
    if not isinstance(values, (list, tuple)):
        raise RuntimeError("ArcMap Desktop selection OIDs must be a list.")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, integer_types):
            raise RuntimeError("ArcMap Desktop selection contains a non-integer OID.")
        if value < 0:
            raise RuntimeError("ArcMap Desktop selection contains a negative OID.")
        result.append(value)
    if len(set(result)) != len(result):
        raise RuntimeError("ArcMap Desktop selection contains duplicate OIDs.")
    return sorted(result)


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


def _text(value):
    if isinstance(value, unicode):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "strict")
    return unicode(value)
