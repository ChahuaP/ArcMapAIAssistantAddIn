# -*- coding: utf-8 -*-
"""Resolve a declared CRS name or authority code through ArcGIS."""
from __future__ import absolute_import
import re
import arcpy


def resolve(value):
    text = unicode(value).strip()
    code = re.match(r'^(?:EPSG\s*:\s*)?(\d+)$', text, re.IGNORECASE)
    reference = arcpy.SpatialReference(int(code.group(1)) if code else text)
    if not reference.name or reference.name.lower() == 'unknown':
        raise ValueError('Unknown spatial reference')
    return reference
