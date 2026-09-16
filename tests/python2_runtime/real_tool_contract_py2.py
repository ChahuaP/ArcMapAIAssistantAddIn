# -*- coding: utf-8 -*-
"""Real ArcGIS regression: subprocess Buffer, field length and publication.

Creates and deletes only its own FileGDB under the repository build directory.
Uses a real map template in memory; never modifies the user's open map.
"""
from __future__ import absolute_import
import glob
import os
import sys
import tempfile
import unittest
import shutil
import gc

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
import arcpy
from arcmap_runtime_py2.operations.analysis_ops import run_buffer
from arcmap_runtime_py2.operations.table_ops import add_field_to_layer
from arcmap_runtime_py2.operations.edit_geometry_ops import _regular_polygon_geometry
from arcmap_runtime_py2.execution_session import ExecutionSession


class RealToolTests(unittest.TestCase):
    def setUp(self):
        self.root = None
        if self._testMethodName == 'test_angle_and_real_polygon_geometry':
            return
        if self._testMethodName == 'test_publish_real_map_without_geoprocessing' and not os.environ.get('ARCMAP_TEST_SHAPEFILE'):
            self.skipTest('Set ARCMAP_TEST_SHAPEFILE to a real input shapefile; this test copies it before use.')
        build = os.path.join(ROOT, 'build')
        self.root = tempfile.mkdtemp(prefix='real-contract-', dir=build)
        self.gdb = None
        if self._testMethodName == 'test_publish_real_map_without_geoprocessing':
            source = os.environ['ARCMAP_TEST_SHAPEFILE']
            self.source = os.path.join(self.root, 'source.shp')
            for extension in ('.shp', '.shx', '.dbf', '.prj'):
                shutil.copyfile(os.path.splitext(source)[0] + extension,
                                os.path.splitext(self.source)[0] + extension)
            return
        self.gdb = os.path.join(self.root, 'test.gdb')
        arcpy.CreateFileGDB_management(self.root, 'test.gdb')
        self.source = os.path.join(self.gdb, 'source')
        arcpy.CreateFeatureclass_management(self.gdb, 'source', 'POINT', spatial_reference=arcpy.SpatialReference(3857))
        with arcpy.da.InsertCursor(self.source, ['SHAPE@XY']) as cursor:
            cursor.insertRow([(0, 0)])

    def tearDown(self):
        if self.root is None:
            return
        if self.gdb is None:
            gc.collect()
            root = os.path.abspath(self.root)
            if not root.startswith(os.path.join(ROOT, 'build') + os.sep):
                raise RuntimeError('Unexpected test artifact directory')
            for name in os.listdir(root):
                os.unlink(os.path.join(root, name))
            os.rmdir(root)
            return
        arcpy.ClearWorkspaceCache_management()
        arcpy.Delete_management(self.gdb)
        os.rmdir(self.root)

    def test_buffer_subprocess_distance_and_no_overwrite(self):
        output = os.path.join(self.gdb, 'buffered')
        run_buffer(self.source, output, '1000 meters')
        self.assertEqual(int(arcpy.GetCount_management(output).getOutput(0)), 1)
        extent = arcpy.Describe(output).extent
        self.assertAlmostEqual(extent.XMin, -1000, places=2)
        self.assertAlmostEqual(extent.XMax, 1000, places=2)
        with self.assertRaises(Exception):
            run_buffer(self.source, output, '2000 meters')
        self.assertAlmostEqual(arcpy.Describe(output).extent.XMax, 1000, places=2)

    def test_field_length_and_nullable_use_correct_arcpy_parameters(self):
        layer = arcpy.mapping.Layer(self.source)
        add_field_to_layer(layer, dict(name='remark', type='string', length=128,
                                      precision=None, scale=None, nullable=True, domain=[]))
        field = [field for field in arcpy.ListFields(self.source) if field.name == 'remark'][0]
        self.assertEqual(field.length, 128)
        self.assertTrue(field.isNullable)
        del field, layer

    def test_angle_and_real_polygon_geometry(self):
        geometry = _regular_polygon_geometry(dict(center_x=0, center_y=0, sides=5,
            radius=dict(value=10, unit='meters', dimension='length', tolerance=0, crs=None),
            start_angle_degrees=dict(value=-90, unit='degrees', dimension='angle', tolerance=0, crs=None)),
            arcpy.SpatialReference(3857))
        self.assertEqual(geometry.pointCount, 6)
        self.assertGreater(geometry.area, 200)

    def test_publish_and_rollback_on_real_map_document(self):
        install = arcpy.GetInstallInfo()['InstallDir']
        templates = glob.glob(os.path.join(install, 'MapTemplates', 'Standard Page Sizes', 'Architectural Page Sizes', '*.mxd'))
        self.assertTrue(templates)
        mxd = arcpy.mapping.MapDocument(templates[0])
        frame = mxd.activeDataFrame
        with ExecutionSession(mxd) as session:
            session.register_output('step', self.source)
            session.publish_output('step')
        layers = arcpy.mapping.ListLayers(mxd, '', frame)
        self.assertEqual(len([layer for layer in layers if layer.supports('DATASOURCE') and layer.dataSource == self.source]), 1)
        for layer in layers:
            arcpy.mapping.RemoveLayer(frame, layer)
        del layers
        with self.assertRaises(RuntimeError):
            with ExecutionSession(mxd) as session:
                session.register_output('step', self.source)
                session.publish_output('step')
                raise RuntimeError('verify rollback')
        self.assertEqual(len(arcpy.mapping.ListLayers(mxd, '', frame)), 0)
        self.assertFalse(arcpy.Exists(self.source))
        del frame, mxd

    def test_publish_real_map_without_geoprocessing(self):
        install = arcpy.GetInstallInfo()['InstallDir']
        templates = glob.glob(os.path.join(install, 'MapTemplates', 'Standard Page Sizes', 'Architectural Page Sizes', '*.mxd'))
        self.assertTrue(templates)
        mxd = arcpy.mapping.MapDocument(templates[0])
        frame = mxd.activeDataFrame
        with ExecutionSession(mxd) as session:
            session.register_output('step', self.source)
            session.publish_output('step')
        layers = arcpy.mapping.ListLayers(mxd, '', frame)
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].dataSource, self.source)
        arcpy.mapping.RemoveLayer(frame, layers[0])
        self.assertEqual(len(arcpy.mapping.ListLayers(mxd, '', frame)), 0)
        del layers, session, frame, mxd


if __name__ == '__main__':
    unittest.main()
