# -*- coding: utf-8 -*-
"""Dedicated ArcGIS process for Buffer; no generated executable source."""
from __future__ import absolute_import
import io
import json
import sys
import arcpy


def main():
    with io.open(sys.argv[1], 'r', encoding='utf-8') as stream:
        arguments = json.load(stream)
    arcpy.env.overwriteOutput = False
    arcpy.Buffer_analysis(*arguments)
    sys.stdout.write('GP-OK\n')


if __name__ == '__main__':
    main()
