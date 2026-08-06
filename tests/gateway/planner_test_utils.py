import contextlib
import copy
import json

from gateway_py3 import tool_builder
from gateway_py3.task_contract import task_contract_model_view


def task_contract(command):
    """Return the sole evidence-bound TaskContract fixture for *command*."""
    return {
        "input_entities": [],
        "outputs": [{
            "output_id": "o1", "kind": "map_state", "name": command,
            "format": "map", "geometry": "not_applicable",
            "required_fields": [], "spatial_reference": "not_applicable",
            "destination": "not_applicable", "evidence": command,
        }],
        "requirements": [{
            "requirement_id": "r1", "predicate": {"kind": "map_change", "subject": "o1", "action": "refresh"},
            "evidence": command,
        }],
        "allowed_side_effects": ["read_only", "changes_map", "writes_data", "edits_data"],
        "clarifications": [],
    }


def model_wire_response(response, messages):
    """Encode canonical fixtures into the current model-wire contracts."""
    if not isinstance(response, dict):
        return response
    bound = dict(response)
    if "task_contract" in response:
        value = response["task_contract"]
        if value is task_contract:
            value = task_contract(json.loads(messages[1]["content"])["request"])
        bound["task_contract"] = task_contract_model_view(value)
    if "workflow_draft" in response:
        value = copy.deepcopy(response["workflow_draft"])
        if isinstance(value, dict) and isinstance(value.get("steps"), list):
            for step_value in value["steps"]:
                if isinstance(step_value, dict) and "arguments" in step_value:
                    arguments = step_value.pop("arguments")
                    step_value["arguments_json"] = json.dumps(
                        arguments, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"),
                    )
        bound["workflow_draft"] = value
    return bound


class FakeAgentClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_agent(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if not self.responses:
            raise AssertionError("No fake agent response left.")
        return {"message": self.responses.pop(0), "usage": {}}


def assistant_tool_call(call_id, name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False)
                }
            }
        ]
    }


def step(step_id, operation, arguments, reason):
    return {
        "id": step_id,
        "operation": operation,
        "arguments": arguments,
        "reason": reason
    }


def context(is_saved=True):
    return {
        "is_saved": is_saved,
        "default_gdb": r"D:\ArcGIS\Default.gdb",
        "layers": [layer("nanjing")]
    }


def layer(name):
    return {
        "layer_ref": "layer:%s" % name,
        "name": name,
        "longName": name,
        "fields": [{"name": "OBJECTID", "type": "OID"}, {"name": "NAME", "type": "String"}],
        "selected_count": 0,
        "geometry_type": "Polygon"
    }


def layer_with_ref(layer_ref, name):
    result = layer(name)
    result["layer_ref"] = layer_ref
    return result


def custom_writes_data_spec():
    return {
        "id": "custom.feature_to_point",
        "version": "0.1.0",
        "category": "custom",
        "summary": "面转点",
        "parameters_schema": {
            "type": "object",
            "required": ["input_layer", "output_name"],
            "properties": {
                "input_layer": {"type": "string"},
                "output_name": {"type": "string"},
                "output_workspace": {"type": "string"}
            },
            "additionalProperties": False
        },
        "context_requirements": {"requires_layers": True},
        "side_effects": "writes_data",
        "output_policy": {},
        "executor": "custom_tool:demo:execute",
        "examples": [{"input_layer": "nanjing", "output_name": "nanjing_points"}]
    }


def star_tool_arguments():
    return {
        "name": "面转五角星面",
        "capability": "将输入面图层的每个面按中心点生成一个五角星面。",
        "operation_spec": {
            "id": "custom.polygon_to_star",
            "version": "0.1.0",
            "category": "custom",
            "summary": "面要素转五角星面",
            "parameters_schema": {
                "type": "object",
                "required": ["input_layer", "output_name"],
                "properties": {
                    "input_layer": {"type": "layer"},
                    "output_name": {"type": "string"},
                    "output_workspace": {"type": "string"},
                    "radius_ratio": {"type": "number"}
                },
                "additionalProperties": False
            },
            "context_requirements": {"requires_layers": True, "geometry_type": "Polygon"},
            "side_effects": "writes_data",
            "output_policy": {"type": "feature_class", "geometry_type": "Polygon"},
            "executor": "will_be_overridden",
            "examples": [
                {
                    "input_layer": "taihu_test_area",
                    "output_name": "taihu_stars",
                    "radius_ratio": 0.35
                }
            ]
        },
        "executor_code": """# -*- coding: utf-8 -*-
import math
import os

INNER_RATIO = 0.3819660112501051


def execute(context, arguments, step_outputs):
    input_layer = arguments["input_layer"]
    output_path = arguments["output_path"]
    radius_ratio = float(arguments.get("radius_ratio", 0.35))
    spatial_reference = arcpy.Describe(input_layer).spatialReference
    arcpy.CreateFeatureclass_management(os.path.dirname(output_path), os.path.basename(output_path), "POLYGON", "", "DISABLED", "DISABLED", spatial_reference)
    arcpy.AddField_management(output_path, "SRC_OID", "LONG")
    count = 0
    with arcpy.da.SearchCursor(input_layer, ["OID@", "SHAPE@"]) as search_cursor:
        with arcpy.da.InsertCursor(output_path, ["SRC_OID", "SHAPE@"]) as insert_cursor:
            for source_oid, geometry in search_cursor:
                extent = geometry.extent
                radius = min(float(extent.XMax - extent.XMin), float(extent.YMax - extent.YMin)) * radius_ratio
                star = _star_polygon(geometry.trueCentroid.X, geometry.trueCentroid.Y, radius, spatial_reference)
                insert_cursor.insertRow([source_oid, star])
                count += 1
    return {"output": output_path, "created_count": count}


def _star_polygon(center_x, center_y, radius, spatial_reference):
    points = arcpy.Array()
    for index in range(10):
        angle = -math.pi / 2.0 + index * math.pi / 5.0
        current_radius = radius if index % 2 == 0 else radius * INNER_RATIO
        points.add(arcpy.Point(center_x + math.cos(angle) * current_radius, center_y + math.sin(angle) * current_radius))
    points.add(points.getObject(0))
    return arcpy.Polygon(points, spatial_reference)
""",
        "tests": [
            {
                "name": "one star per polygon",
                "arguments": {
                    "input_layer": "layer:taihu test area",
                    "output_name": "taihu_stars",
                    "radius_ratio": 0.35
                },
                "expected": {"output_geometry": "Polygon", "created_count": "same as input polygon count"},
                "assertions": [
                    "output is written to arguments['output_path']",
                    "each source polygon creates one star polygon"
                ]
            }
        ]
    }


def star_tool_revision_arguments(tool_id):
    arguments = star_tool_arguments()
    arguments["tool_id"] = tool_id
    arguments["change_summary"] = "默认半径比例从 0.35 改为 0.25"
    arguments["executor_code"] = arguments["executor_code"].replace(
        'arguments.get("radius_ratio", 0.35)',
        'arguments.get("radius_ratio", 0.25)'
    )
    return arguments


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
