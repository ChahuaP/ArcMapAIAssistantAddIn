# -*- coding: utf-8 -*-
"""Closed GIS semantic values and the only ArcPy boundary conversions.

This module is deliberately Python 2 compatible because the desktop runtime
must not parse model-facing strings or maintain a second vocabulary.
"""
from __future__ import absolute_import

import math

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


LENGTH_UNITS = frozenset(("meters", "kilometers", "map_units", "degrees"))
AREA_UNITS = frozenset(("square_meters", "hectares", "square_kilometers", "map_units_squared", "square_degrees"))
ANGLE_UNITS = frozenset(("degrees",))
FIELD_TYPES = frozenset(("smallinteger", "integer", "biginteger", "single", "double", "string", "date", "guid", "globalid", "blob"))
SELECTION_TYPES = frozenset(("new_selection", "add_to_selection", "remove_from_selection", "select_subset"))
SPATIAL_PREDICATES = frozenset(("intersect", "contain", "within", "touch", "overlap", "cross", "within_a_distance"))
_ARCPY_SELECTION = {
    "new_selection": "NEW_SELECTION", "add_to_selection": "ADD_TO_SELECTION",
    "remove_from_selection": "REMOVE_FROM_SELECTION", "select_subset": "SUBSET_SELECTION",
}
_ARCPY_PREDICATE = {
    "intersect": "INTERSECT", "contain": "CONTAINS", "within": "WITHIN",
    "touch": "BOUNDARY_TOUCHES", "overlap": "OVERLAP", "cross": "CROSSED_BY_THE_OUTLINE_OF",
    "within_a_distance": "WITHIN_A_DISTANCE",
}
_ARCPY_FIELD_TYPES = {
    "smallinteger": "SHORT", "integer": "LONG", "biginteger": "BIGINTEGER",
    "single": "FLOAT", "double": "DOUBLE", "string": "TEXT", "date": "DATE",
    "guid": "GUID", "globalid": "GLOBALID", "blob": "BLOB",
}


class SemanticAbiError(ValueError):
    pass


class SpatialPredicate(object):
    """Closed cross-runtime spatial predicate vocabulary."""
    INTERSECT = "intersect"; CONTAIN = "contain"; WITHIN = "within"
    TOUCH = "touch"; OVERLAP = "overlap"; CROSS = "cross"
    WITHIN_A_DISTANCE = "within_a_distance"


class Quantity(object):
    __slots__ = ("value", "unit", "dimension", "tolerance", "crs")
    def __init__(self, value, unit, dimension="length", tolerance=0.0, crs=None):
        self.value, self.unit, self.dimension, self.tolerance, self.crs = value, unit, dimension, tolerance, crs
        quantity(self.as_dict(), dimension)
    def canonical(self):
        if self.dimension == "length" and self.unit == "kilometers":
            return Quantity(float(self.value) * 1000.0, "meters", "length", float(self.tolerance) * 1000.0, self.crs)
        return self
    def compatible_with(self, crs):
        return self.unit not in ("map_units", "degrees", "map_units_squared", "square_degrees") or bool(self.crs and crs and self.crs == crs)
    def as_dict(self):
        return {"value": self.value, "unit": self.unit, "dimension": self.dimension, "tolerance": self.tolerance, "crs": self.crs}


class FieldSpec(object):
    __slots__ = ("name", "type", "nullable", "length", "precision", "scale", "domain")
    def __init__(self, name, type, nullable, length=None, precision=None, scale=None, domain=()):
        self.name, self.type, self.nullable = name, type, nullable
        self.length, self.precision, self.scale = length, precision, scale
        self.domain = tuple(domain)
        field_spec(self.as_dict())
    def as_dict(self):
        return {"name": self.name, "type": self.type, "nullable": self.nullable,
                "length": self.length, "precision": self.precision,
                "scale": self.scale, "domain": list(self.domain)}


class LineageFact(object):
    __slots__ = ("output_id", "input_ids", "capability_id", "parameter_digest")
    def __init__(self, output_id, input_ids, capability_id, parameter_digest):
        self.output_id, self.input_ids = output_id, tuple(input_ids)
        self.capability_id, self.parameter_digest = capability_id, parameter_digest
        if not self.output_id or not self.input_ids or not self.capability_id or not self.parameter_digest:
            raise SemanticAbiError("LineageFact is incomplete.")


def quantity(value, expected_dimension=None):
    if not isinstance(value, dict) or set(value) != set(("value", "unit", "dimension", "tolerance", "crs")):
        raise SemanticAbiError("Quantity must contain exactly value, unit, dimension, tolerance, crs.")
    units = {"length": LENGTH_UNITS, "area": AREA_UNITS, "angle": ANGLE_UNITS}
    if value["dimension"] not in units or value["unit"] not in units[value["dimension"]] or (expected_dimension is not None and value["dimension"] != expected_dimension):
        raise SemanticAbiError("Quantity uses an unsupported dimension or unit.")
    if isinstance(value["value"], bool) or not isinstance(value["value"], (int, float)) or not math.isfinite(float(value["value"])) or value["value"] < 0:
        raise SemanticAbiError("Quantity value must be finite and non-negative.")
    if isinstance(value["tolerance"], bool) or not isinstance(value["tolerance"], (int, float)) or value["tolerance"] < 0:
        raise SemanticAbiError("Quantity tolerance must be non-negative.")
    if value["unit"] in ("map_units", "degrees", "map_units_squared", "square_degrees") and not value["crs"]:
        raise SemanticAbiError("Map-unit and angular Quantity values require CRS evidence.")
    return value


def quantity_to_arcpy(value):
    quantity(value)
    return u"%s %s" % (value["value"], value["unit"])


def selection_to_arcpy(value):
    if value not in SELECTION_TYPES:
        raise SemanticAbiError("Unknown selection type.")
    return _ARCPY_SELECTION[value]


def spatial_predicate_to_arcpy(value):
    if value not in SPATIAL_PREDICATES:
        raise SemanticAbiError("Unknown spatial predicate.")
    return _ARCPY_PREDICATE[value]


def field_spec(value):
    required = set(("name", "type", "nullable", "length", "precision", "scale", "domain"))
    if not isinstance(value, dict) or set(value) != required:
        raise SemanticAbiError("FieldSpec must contain exactly name, type, nullable, length, precision, scale, domain.")
    if not isinstance(value["name"], _STRING_TYPES) or not value["name"] or value["type"] not in FIELD_TYPES:
        raise SemanticAbiError("FieldSpec name or type is invalid.")
    if (not isinstance(value["nullable"], bool)
            or value["length"] is not None and (not isinstance(value["length"], int) or value["length"] < 0)
            or value["precision"] is not None and (not isinstance(value["precision"], int) or value["precision"] < 0)
            or value["scale"] is not None and (not isinstance(value["scale"], int) or value["scale"] < 0)
            or (value["scale"] is not None and (value["precision"] is None or value["scale"] > value["precision"]))
            or not isinstance(value["domain"], list) or len(set(value["domain"])) != len(value["domain"])):
        raise SemanticAbiError("FieldSpec metadata is invalid.")
    return value


def field_type_to_arcpy(value):
    field_spec(value)
    return _ARCPY_FIELD_TYPES[value["type"]]
