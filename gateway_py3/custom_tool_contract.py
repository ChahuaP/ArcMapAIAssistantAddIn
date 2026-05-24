from __future__ import annotations

from typing import Any, Dict, List


TOOLBUILDER_AGENT_TOOL_NAME = "toolbuilder_create_draft"
TOOLBUILDER_CATALOG_IDS = frozenset((
    "toolbuilder.create_draft",
    "toolbuilder_create_draft",
))
CONTRACT_VERSION = "2026-05-23"
DISTANCE_PARAMETER_WORDS = (
    "radius",
    "distance",
    "width",
    "height",
    "length",
    "tolerance",
    "offset",
    "buffer",
)
DIMENSIONLESS_SUFFIXES = (
    "_ratio",
    "_factor",
    "_scale",
    "_percent",
    "_percentage",
)
REQUIRED_UNIT_ENUM_VALUES = ("map_units", "degrees", "meters")

PLANNER_CUSTOM_TOOL_CONTRACT = """- Custom tools are for new reusable ArcPy algorithms that cannot be expressed by chaining existing catalog operations.
- Prefer a multi-step workflow of existing operations whenever the task is selection, filtering, splitting, exporting, converting, or other normal ArcGIS processing.
- toolbuilder_create_draft is an agent tool, not an ArcGIS operation id. Never call catalog_get_operation_schema for toolbuilder.create_draft or toolbuilder_create_draft; call toolbuilder_create_draft directly.
- Do not say the current ArcGIS version cannot create a custom tool when toolbuilder_create_draft is available. Create a disabled draft tool package instead.
- If toolbuilder_create_draft returns ok=false, repair the operation_spec, executor_code, or tests from that exact error and call toolbuilder_create_draft again.
- If an existing custom tool has a bug, bad parameters, or user-requested change, call toolbuilder_get_draft first and then toolbuilder_revise_draft with the same tool id. The tool id may be an internal UUID, a custom.* operation id, or a custom_tool:<uuid>:execute executor reference. Do not create a new custom tool for an iteration.
- Never ask the user to provide executor_code for an existing custom tool. Read it yourself with toolbuilder_get_draft.
- If the user asks to change a pending, rejected, or enabled custom tool, revise that tool in place. The revised package must wait for review again.
- If toolbuilder_create_draft would duplicate an existing operation_id, revise the existing tool instead.
- The custom tool draft must include operation_spec, executor_code, and review tests. Empty tests are invalid.
- operation_spec describes the reusable operation: custom.* id, parameters_schema, context_requirements, side_effects, output_policy, and examples.
- executor_code is real ArcMap ArcPy implementation code, not pseudo-code and not a workflow that chains GeoPilot operation ids.
- executor_code must be one ArcMap Python 2.7 module with def execute(context, arguments, step_outputs): ...
- ArcMap uses Python 2.7. Do not use Python 3-only exception/classes or APIs such as FileNotFoundError, FileExistsError, PermissionError, ModuleNotFoundError, os.scandir, pathlib, dataclasses, keyword-only arguments, or os.makedirs(..., exist_ok=True).
- Every helper function called by executor_code must be defined in the same executor module or imported from an allowed Python 2.7 standard module. Do not invent helper functions and assume GeoPilot or ArcPy provides them.
- The runtime injects arcpy and resolves layer parameters before execute runs. Use arguments["input_layer"] or other layer parameters directly as ArcMap Layer objects.
- Do not hide geometry or ArcPy failures with broad except/pass/continue. If a feature is invalid, count it and continue only for that specific expected condition; unexpected exceptions must raise so the tool can be revised.
- For writes_data tools, declare required output_name and write the output only to arguments["output_path"]. Do not read managed output arguments such as output_workspace, output_folder, output_format, or output_name inside executor_code.
- output_policy.type controls the generated output path: feature_class supports gdb and shp outputs, file writes ordinary files such as .obj/.json/.csv, and raster writes raster files such as .tif.
- For feature_class outputs, GeoPilot may add output_workspace, output_folder, and output_format workflow arguments. Use output_format="shp" plus output_folder for shapefile output, or output_format="gdb" plus output_workspace for file geodatabase output.
- For file outputs, operation_spec.output_policy must include extension such as ".obj"; executor_code may call open(arguments["output_path"], "w") or open(arguments["output_path"], "wb") and must not open any other path.
- For raster outputs, write the raster to arguments["output_path"]; GeoPilot can generate a .tif path when output_policy.formats/default_format says tif.
- For writes_data feature_class/raster tools, do not add the output layer yourself; GeoPilot adds arguments["output_path"] to the map after execute returns. File outputs are not added to ArcMap.
- It is allowed to implement geometry algorithms with arcpy.Geometry, arcpy.Array, arcpy.Point, arcpy.Polygon, arcpy.da.SearchCursor, and arcpy.da.InsertCursor.
- It is allowed to split arguments["output_path"] with os.path.dirname/basename when ArcPy requires workspace and dataset name separately.
- ArcPy calls must be real ArcMap ArcPy APIs. Do not invent arcpy function names. Use known ArcMap calls such as CopyFeatures_management, CreateFeatureclass_management, AddField_management, FeatureToPoint_management, arcpy.da.SearchCursor, and arcpy.da.InsertCursor.
- When using arcpy.CreateFeatureclass_management, split arguments["output_path"] into os.path.dirname(output_path) and os.path.basename(output_path), then pass spatial_reference from arcpy.Describe(input_layer).spatialReference. Do not pass the full output_path as workspace, do not manually create or append .gdb, and do not pass context["spatial_reference"], spatialReference.name, factoryCode, ordinary strings, or layer.spatialReference.
- When iterating polygon vertices from SHAPE@ in ArcMap, geom.getPart(i) returns an Array of Point objects, with None separators for rings. Iterate points directly and handle None separators; do not assume part.getObject(j) is another ring with .count.
- Never create or write ArcGIS system fields such as OBJECTID, FID, OID, Shape, Shape_Length, or Shape_Area. To preserve source feature ids, read "OID@" from the input cursor and write it to a custom LONG field such as SRC_OID.
- Geometry algorithms that add or compare X/Y distances must define the unit contract explicitly. Do not describe a parameter as meters if the executor directly adds that value to coordinate X/Y values.
- Radius, distance, width, height, length, offset, buffer, or tolerance parameters must either be dimensionless ratios such as radius_ratio, or they must have a matching unit parameter such as radius_unit / distance_unit with enum values map_units, degrees, meters. This is a hard contract, not a style preference.
- If the input spatial reference is geographic, raw X/Y geometry math uses degrees; do not use a meter default. If meters are requested on geographic coordinates, either implement a real projection/geodesic method or raise a clear error instead of approximating silently.
- Defaults must be safe for the coordinate system. For geographic coordinates, a small degree default such as 0.001 is acceptable only when the operation_spec says it is degrees or map coordinate units.
- Do not use arcpy.mp, ArcGISProject, arcpy.mapping.MapDocument, arcpy.mapping.ListLayers, getOutput, f-strings, type annotations, pathlib, dataclasses, async, eval/exec/open/subprocess/socket/network calls, or manual current-map lookup.
- Keep Python comments and string literals ASCII except the UTF-8 encoding header. Put Chinese user-facing text in operation_spec, examples, and tests.
- Review tests must name realistic input arguments, expected outputs, and assertions such as created feature count, output geometry type, required fields, and key edge cases."""

TOOLBUILDER_TOOL_DESCRIPTION = (
    "Create a disabled draft ArcMap ArcPy operation package for a missing reusable GIS capability. "
    "Use this only when the task cannot be expressed by chaining existing operation catalog steps. "
    "This tool may implement new ArcPy algorithms that are not expressible by the built-in operation catalog. "
    "The draft must follow the GeoPilot custom tool development contract, include real ArcMap Python 2.7 executor_code, "
    "operation_spec, and non-empty review tests; it waits for human review before enablement."
)
TOOLBUILDER_GET_TOOL_DESCRIPTION = (
    "Read an existing custom tool draft or enabled package before revising it. "
    "Use this whenever the user asks to modify, fix, improve, or iterate an existing custom tool. "
    "tool_id may be the internal UUID, a custom.* operation id, or a custom_tool:<uuid>:execute executor reference."
)
TOOLBUILDER_REVISE_TOOL_DESCRIPTION = (
    "Revise an existing custom tool in place using the same tool id. "
    "Use this instead of creating a new tool when a custom tool has a bug, validation problem, or user-requested change. "
    "tool_id may be the internal UUID, a custom.* operation id, or a custom_tool:<uuid>:execute executor reference. "
    "The revised package returns to pending review before enablement."
)

PARAMETER_PROPERTY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"type": "string", "enum": ["layer", "string", "boolean", "integer", "number", "array", "object"]},
        "description": {"type": "string"},
        "enum": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "object"},
        "properties": {"type": "object"},
        "required": {"type": "array", "items": {"type": "string"}},
        "additionalProperties": {"type": "boolean"},
        "minItems": {"type": "integer"},
        "x-geopilot-kind": {"type": "string", "enum": ["layer"]},
    },
    "additionalProperties": True,
}
PARAMETERS_SCHEMA_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["type", "properties", "required", "additionalProperties"],
    "properties": {
        "type": {"type": "string", "enum": ["object"]},
        "required": {"type": "array", "items": {"type": "string"}},
        "properties": {
            "type": "object",
            "additionalProperties": PARAMETER_PROPERTY_SCHEMA,
        },
        "additionalProperties": {"type": "boolean"},
    },
    "additionalProperties": False,
}
OPERATION_SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "id",
        "version",
        "category",
        "summary",
        "model_card",
        "parameters_schema",
        "context_requirements",
        "side_effects",
        "output_policy",
        "executor",
        "examples",
    ],
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable custom operation id. Must match custom.name or custom.group_name, lowercase ASCII only.",
        },
        "version": {"type": "string"},
        "category": {"type": "string"},
        "summary": {"type": "string"},
        "model_card": {
            "type": "string",
            "description": "Operational guidance for the planner, including when to use the tool and any unit contracts.",
        },
        "parameters_schema": PARAMETERS_SCHEMA_SCHEMA,
        "context_requirements": {"type": "object"},
        "side_effects": {"type": "string", "enum": ["read_only", "changes_map", "writes_data", "edits_data"]},
        "output_policy": {"type": "object"},
        "executor": {
            "type": "string",
            "description": "Use any placeholder; GeoPilot replaces it with custom_tool:<tool_id>:execute.",
        },
        "examples": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "object"},
        },
    },
    "additionalProperties": True,
}

TOOLBUILDER_TOOL_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "required": ["name", "capability", "operation_spec", "executor_code", "tests"],
    "properties": {
        "name": {"type": "string"},
        "capability": {"type": "string"},
        "operation_spec": {
            **OPERATION_SPEC_SCHEMA,
            "description": "Complete custom operation spec. Use parameters_schema as the only parameter definition source."
        },
        "executor_code": {
            "type": "string",
            "description": "ArcMap Python 2.7 code defining execute(context, arguments, step_outputs)."
        },
        "tests": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "arguments", "expected", "assertions"],
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "object"},
                    "expected": {"type": "object"},
                    "assertions": {"type": "array", "items": {"type": "string"}, "minItems": 1}
                },
                "additionalProperties": True
            }
        }
    },
    "additionalProperties": False
}
TOOLBUILDER_GET_TOOL_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "required": ["tool_id"],
    "properties": {
        "tool_id": {"type": "string", "description": "Internal UUID, custom.* operation id, or custom_tool:<uuid>:execute executor reference."}
    },
    "additionalProperties": False
}
TOOLBUILDER_REVISE_TOOL_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "required": ["tool_id", "change_summary", "name", "capability", "operation_spec", "executor_code", "tests"],
    "properties": {
        "tool_id": {"type": "string", "description": "Internal UUID, custom.* operation id, or custom_tool:<uuid>:execute executor reference."},
        "change_summary": {"type": "string"},
        "name": TOOLBUILDER_TOOL_PARAMETERS["properties"]["name"],
        "capability": TOOLBUILDER_TOOL_PARAMETERS["properties"]["capability"],
        "operation_spec": TOOLBUILDER_TOOL_PARAMETERS["properties"]["operation_spec"],
        "executor_code": TOOLBUILDER_TOOL_PARAMETERS["properties"]["executor_code"],
        "tests": TOOLBUILDER_TOOL_PARAMETERS["properties"]["tests"],
    },
    "additionalProperties": False
}


class CustomToolContractError(Exception):
    pass


def is_toolbuilder_catalog_id(operation_id: str) -> bool:
    value = str(operation_id or "").strip()
    return value in TOOLBUILDER_CATALOG_IDS or value.startswith("toolbuilder.")


def toolbuilder_catalog_misuse_result(operation_id: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": "wrong_tool_namespace",
        "error": (
            "%s 不是 operation catalog 里的 ArcGIS operation；它是 agent tool。"
            "不要用 catalog_get_operation_schema 查询它，请直接调用 toolbuilder_create_draft 创建待审核自定义工具。"
        ) % operation_id
    }


def build_review_payload(spec: Dict[str, Any], tests: Any) -> Dict[str, Any]:
    _validate_dimension_unit_contract(spec)
    normalized_tests = validate_review_tests(spec, tests)
    return {
        "contract_version": CONTRACT_VERSION,
        "runtime_contract": [
            "execute(context, arguments, step_outputs)",
            "valid in ArcMap Python 2.7",
            "layer parameters are runtime-resolved ArcMap Layer objects",
            "writes_data output path is arguments['output_path']",
            "output_policy.type selects feature_class, file, or raster path generation",
            "GeoPilot adds feature_class/raster outputs to ArcGIS after execute returns",
            "file outputs may only open arguments['output_path'] for w/wb writes",
            "CreateFeatureclass splits arguments['output_path'] with os.path.dirname/basename",
            "CreateFeatureclass spatial_reference comes from arcpy.Describe(input_layer).spatialReference",
            "executor does not create or write ArcGIS system OID/Shape fields",
        ],
        "acceptance_checklist": _acceptance_checklist(spec),
        "test_count": len(normalized_tests),
    }


def validate_review_tests(spec: Dict[str, Any], tests: Any) -> List[Dict[str, Any]]:
    if not isinstance(tests, list):
        raise CustomToolContractError("tests 必须是数组。")
    if not tests:
        raise CustomToolContractError("自建工具必须提供至少 1 个 review test，不能空着。")

    schema = spec.get("parameters_schema") or {}
    required_arguments = [item for item in schema.get("required", []) if isinstance(item, str)]
    dimensional_units = _dimension_unit_pairs(spec)
    normalized = []
    for index, test in enumerate(tests, 1):
        if not isinstance(test, dict):
            raise CustomToolContractError("tests[%s] 必须是对象。" % index)
        name = _required_text(test, "name", index)
        arguments = test.get("arguments")
        expected = test.get("expected")
        assertions = test.get("assertions")
        if not isinstance(arguments, dict):
            raise CustomToolContractError("tests[%s].arguments 必须是对象。" % index)
        missing = [item for item in required_arguments if item not in arguments]
        if missing:
            raise CustomToolContractError("tests[%s].arguments 缺少必填参数：%s。" % (index, "、".join(missing)))
        for parameter_name, unit_name in dimensional_units:
            if parameter_name in arguments and unit_name not in arguments:
                raise CustomToolContractError(
                    "tests[%s].arguments 使用距离/半径参数 %s 时必须同时包含单位参数 %s。"
                    % (index, parameter_name, unit_name)
                )
        if not isinstance(expected, dict) or not expected:
            raise CustomToolContractError("tests[%s].expected 必须是非空对象。" % index)
        if not isinstance(assertions, list) or not assertions:
            raise CustomToolContractError("tests[%s].assertions 必须是非空数组。" % index)
        clean_assertions = []
        for assertion in assertions:
            if not isinstance(assertion, str) or not assertion.strip():
                raise CustomToolContractError("tests[%s].assertions 只能包含非空字符串。" % index)
            if spec.get("side_effects") == "writes_data" and _weak_output_assertion(assertion):
                raise CustomToolContractError("tests[%s].assertions 不能使用 >= 0 或 == 0 这类空成功断言；写数据工具必须验证非空输出或明确的正向计数。" % index)
            clean_assertions.append(assertion.strip())
        normalized.append({
            "name": name,
            "arguments": dict(arguments),
            "expected": dict(expected),
            "assertions": clean_assertions,
        })
    return normalized


def _acceptance_checklist(spec: Dict[str, Any]) -> List[str]:
    checklist = [
        "executor.py imports and runs in ArcMap Python 2.7",
        "execute reads only runtime-provided arguments",
        "executor handles empty or invalid input geometry by reporting a clear error or skipping invalid rows",
    ]
    if spec.get("side_effects") == "writes_data":
        checklist.extend([
            "operation_spec requires output_name",
            "executor writes exactly to arguments['output_path']",
            "result returns output and created feature count when applicable",
        ])
    if _dimension_unit_pairs(spec):
        checklist.append("distance-like parameters have explicit unit arguments and unit edge-case tests")
    return checklist


def _weak_output_assertion(assertion: str) -> bool:
    text = assertion.strip().lower().replace(" ", "")
    if "> =0" in text:
        return True
    weak_tokens = (">=0", "==0", "=0")
    count_words = ("count", "vertex", "vertices", "face", "feature", "row", "record", "byte", "size")
    return any(word in text for word in count_words) and any(token in text for token in weak_tokens)


def _validate_dimension_unit_contract(spec: Dict[str, Any]) -> None:
    schema = spec.get("parameters_schema") or {}
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return
    for name, property_schema in properties.items():
        if not _is_dimension_parameter(name, property_schema):
            continue
        unit_name = "%s_unit" % name
        unit_schema = properties.get(unit_name)
        if not isinstance(unit_schema, dict):
            raise CustomToolContractError(
                "距离/半径类参数 %s 必须配套 %s 单位参数，或改成 %s_ratio 这类无量纲比例参数。"
                % (name, unit_name, name)
            )
        if unit_schema.get("type") != "string":
            raise CustomToolContractError("%s 必须是 string 类型。" % unit_name)
        enum_values = unit_schema.get("enum")
        if not isinstance(enum_values, list):
            raise CustomToolContractError("%s 必须声明 enum：map_units、degrees、meters。" % unit_name)
        missing = [value for value in REQUIRED_UNIT_ENUM_VALUES if value not in enum_values]
        if missing:
            raise CustomToolContractError("%s.enum 缺少单位：%s。" % (unit_name, "、".join(missing)))


def _dimension_unit_pairs(spec: Dict[str, Any]) -> List[tuple[str, str]]:
    schema = spec.get("parameters_schema") or {}
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return []
    result = []
    for name, property_schema in properties.items():
        if _is_dimension_parameter(name, property_schema):
            unit_name = "%s_unit" % name
            if unit_name in properties:
                result.append((name, unit_name))
    return result


def _is_dimension_parameter(name: str, property_schema: Any) -> bool:
    if not isinstance(property_schema, dict):
        return False
    if property_schema.get("type") not in ("number", "integer"):
        return False
    lowered = str(name or "").lower()
    if lowered.endswith(DIMENSIONLESS_SUFFIXES):
        return False
    if lowered.endswith("_unit") or lowered == "unit":
        return False
    return any(word in lowered for word in DISTANCE_PARAMETER_WORDS)


def _required_text(test: Dict[str, Any], key: str, index: int) -> str:
    value = test.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CustomToolContractError("tests[%s].%s 必须是非空字符串。" % (index, key))
    return value.strip()
