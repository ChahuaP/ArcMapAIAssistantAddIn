import contextlib

from gateway_py3 import tool_builder


def context():
    return {"is_saved": True, "layers": []}


def custom_spec():
    return {
        "id": "custom.demo_tool",
        "version": "0.1.0",
        "category": "custom",
        "summary": "测试工具",
        "model_card": "测试工具。",
        "parameters_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "context_requirements": {},
        "side_effects": "read_only",
        "output_policy": {},
        "executor": "will_be_overridden",
        "examples": [{"output_name": "demo_output"}]
    }


def custom_writes_data_spec():
    spec = custom_spec()
    spec["side_effects"] = "writes_data"
    spec["parameters_schema"] = {
        "type": "object",
        "required": ["input_layer", "output_name"],
        "properties": {
            "input_layer": {"type": "layer"},
            "output_name": {"type": "string"}
        },
        "additionalProperties": False
    }
    return spec


def review_tests():
    return [
        {
            "name": "creates output feature class",
            "arguments": {
                "input_layer": "layer:test",
                "output_name": "demo_output"
            },
            "expected": {
                "output_geometry": "Polygon",
                "created_count": "matches input feature count"
            },
            "assertions": [
                "executor writes to arguments['output_path']",
                "output dataset exists after execution"
            ]
        }
    ]


def polygon_to_star_spec():
    return {
        "id": "custom.polygon_to_star",
        "version": "0.1.0",
        "category": "custom",
        "summary": "面要素转五角星面",
        "model_card": "将输入面图层的每个面按中心点和外接范围生成一个五角星面，输出为新的面要素类。",
        "parameters_schema": {
            "type": "object",
            "required": ["input_layer", "output_name"],
            "properties": {
                "input_layer": {"type": "layer", "description": "输入面图层"},
                "output_name": {"type": "string", "description": "输出要素类名称"},
                "output_workspace": {"type": "string", "description": "输出目录或 GDB"},
                "radius_ratio": {"type": "number", "description": "五角星外半径占输入面最短边的比例，默认 0.35"}
            },
            "additionalProperties": False
        },
        "context_requirements": {"requires_layers": True, "geometry_type": "Polygon"},
        "side_effects": "writes_data",
        "output_policy": {"type": "feature_class", "geometry_type": "Polygon"},
        "executor": "will_be_overridden",
        "examples": [
            {
                "request": "将 taihu test area 面图层转换为五角星面图层",
                "arguments": {
                    "input_layer": "taihu test area",
                    "output_name": "taihu_stars"
                }
            }
        ]
    }


def polygon_to_star_tests():
    return [
        {
            "name": "one star polygon per input polygon",
            "arguments": {
                "input_layer": "layer:taihu test area",
                "output_name": "taihu_stars",
                "radius_ratio": 0.35
            },
            "expected": {
                "output_geometry": "Polygon",
                "created_count": "same as valid input polygon count",
                "fields": ["SRC_OID"]
            },
            "assertions": [
                "output feature class is created at arguments['output_path']",
                "each output feature is a 10-vertex closed five-point star polygon",
                "SRC_OID stores the source polygon OID"
            ]
        }
    ]


def polygon_to_star_executor_code():
    return """# -*- coding: utf-8 -*-
import math
import os

INNER_RATIO = 0.3819660112501051


def execute(context, arguments, step_outputs):
    input_layer = arguments["input_layer"]
    output_path = arguments["output_path"]
    radius_ratio = float(arguments.get("radius_ratio", 0.35))
    if radius_ratio <= 0:
        raise ValueError("radius_ratio must be positive")

    spatial_reference = arcpy.Describe(input_layer).spatialReference
    output_workspace = os.path.dirname(output_path)
    output_name = os.path.basename(output_path)
    arcpy.CreateFeatureclass_management(output_workspace, output_name, "POLYGON", "", "DISABLED", "DISABLED", spatial_reference)
    arcpy.AddField_management(output_path, "SRC_OID", "LONG")

    created_count = 0
    with arcpy.da.SearchCursor(input_layer, ["OID@", "SHAPE@"]) as search_cursor:
        with arcpy.da.InsertCursor(output_path, ["SRC_OID", "SHAPE@"]) as insert_cursor:
            for source_oid, geometry in search_cursor:
                if geometry is None:
                    continue
                star = _star_for_geometry(geometry, radius_ratio, spatial_reference)
                if star is None:
                    continue
                insert_cursor.insertRow([source_oid, star])
                created_count += 1
    return {"output": output_path, "created_count": created_count}


def _star_for_geometry(geometry, radius_ratio, spatial_reference):
    extent = geometry.extent
    width = float(extent.XMax - extent.XMin)
    height = float(extent.YMax - extent.YMin)
    radius = min(width, height) * radius_ratio
    if radius <= 0:
        return None
    center = geometry.trueCentroid
    return _star_polygon(center.X, center.Y, radius, spatial_reference)


def _star_polygon(center_x, center_y, radius, spatial_reference):
    inner_radius = radius * INNER_RATIO
    points = arcpy.Array()
    for index in range(10):
        angle = -math.pi / 2.0 + index * math.pi / 5.0
        current_radius = radius if index % 2 == 0 else inner_radius
        point = arcpy.Point(
            center_x + math.cos(angle) * current_radius,
            center_y + math.sin(angle) * current_radius
        )
        points.add(point)
    points.add(points.getObject(0))
    return arcpy.Polygon(points, spatial_reference)
"""


@contextlib.contextmanager
def isolated_tool_roots(root):
    old_pending = tool_builder.PENDING_ROOT
    old_enabled = tool_builder.ENABLED_ROOT
    tool_builder.PENDING_ROOT = root / "pending_tools"
    tool_builder.ENABLED_ROOT = root / "enabled_tools"
    try:
        yield
    finally:
        tool_builder.PENDING_ROOT = old_pending
        tool_builder.ENABLED_ROOT = old_enabled
