from __future__ import annotations

import re
from typing import Any, Dict, List


VECTOR_FORMATS = ("gdb", "shp")
RASTER_FORMATS = ("tif", "tiff")
OUTPUT_POLICY_TYPES = ("feature_class", "file", "raster")


class OutputPolicyError(Exception):
    pass


def canonical_output_policy(policy: Any, side_effects: str) -> Dict[str, Any]:
    if not isinstance(policy, dict):
        policy = {}
    result = dict(policy)
    if side_effects != "writes_data":
        return result
    output_type = output_policy_type(result)
    result["type"] = output_type
    if output_type == "feature_class":
        formats = output_formats(result) or list(VECTOR_FORMATS)
        result["formats"] = formats
        result.setdefault("default_format", "gdb")
        result.setdefault("add_to_map", True)
    elif output_type == "raster":
        formats = output_formats(result) or ["tif"]
        result["formats"] = formats
        result.setdefault("default_format", formats[0])
        result.setdefault("add_to_map", True)
    elif output_type == "file":
        result.setdefault("add_to_map", False)
    return result


def output_policy_type(policy: Dict[str, Any]) -> str:
    value = policy.get("type")
    if not value:
        return "feature_class"
    text = str(value).strip().lower()
    if text in ("vector", "feature", "featureclass"):
        return "feature_class"
    if text in OUTPUT_POLICY_TYPES:
        return text
    raise OutputPolicyError("output_policy.type 不合法：%s。" % value)


def output_formats(policy: Dict[str, Any]) -> List[str]:
    values = policy.get("formats")
    if values is None and policy.get("format"):
        values = [policy.get("format")]
    if values is None:
        return []
    if not isinstance(values, list):
        raise OutputPolicyError("output_policy.formats 必须是字符串数组。")
    result: List[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise OutputPolicyError("output_policy.formats 必须是字符串数组。")
        result.append(_normalize_format(value))
    return result


def managed_output_parameter_names(policy: Dict[str, Any]) -> set[str]:
    output_type = output_policy_type(policy)
    names = {"output_name"}
    if output_type == "feature_class":
        names.update({"output_workspace", "output_folder"})
        if len(output_formats(policy) or list(VECTOR_FORMATS)) > 1:
            names.add("output_format")
    elif output_type == "file":
        names.add("output_folder")
    elif output_type == "raster":
        names.add("output_folder")
        if len(output_formats(policy) or list(RASTER_FORMATS)) > 1:
            names.add("output_format")
    return names


def managed_output_properties(policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    output_type = output_policy_type(policy)
    properties: Dict[str, Dict[str, Any]] = {}
    if output_type == "feature_class":
        properties["output_workspace"] = {
            "type": "string",
            "description": "Optional output folder or geodatabase for GDB output. GeoPilot creates runtime output_path from this value; workflow must not pass output_path."
        }
        properties["output_folder"] = {
            "type": "string",
            "description": "Optional output folder for shapefile output. GeoPilot creates runtime output_path from this value; workflow must not pass output_path."
        }
        formats = output_formats(policy) or list(VECTOR_FORMATS)
        if len(formats) > 1:
            properties["output_format"] = {
                "type": "string",
                "enum": formats,
                "description": "Output vector format. Use gdb for file geodatabase feature class, shp for shapefile."
            }
    elif output_type == "file":
        properties["output_folder"] = {
            "type": "string",
            "description": "Optional output folder. GeoPilot creates runtime output_path from this value; workflow must not pass output_path."
        }
    elif output_type == "raster":
        properties["output_folder"] = {
            "type": "string",
            "description": "Optional output folder for raster file output. GeoPilot creates runtime output_path from this value; workflow must not pass output_path."
        }
        formats = output_formats(policy) or list(RASTER_FORMATS)
        if len(formats) > 1:
            properties["output_format"] = {
                "type": "string",
                "enum": formats,
                "description": "Output raster format."
            }
    return properties


def validate_output_policy(policy: Dict[str, Any], side_effects: str) -> None:
    if side_effects != "writes_data":
        return
    output_type = output_policy_type(policy)
    formats = output_formats(policy)
    if output_type == "feature_class":
        invalid = [item for item in (formats or list(VECTOR_FORMATS)) if item not in VECTOR_FORMATS]
        if invalid:
            raise OutputPolicyError("feature_class output_policy.formats 只支持 gdb 和 shp。")
        return
    if output_type == "raster":
        invalid = [item for item in (formats or list(RASTER_FORMATS)) if item not in RASTER_FORMATS]
        if invalid:
            raise OutputPolicyError("raster output_policy.formats 只支持 tif/tiff。")
        return
    if output_type == "file":
        extension = policy.get("extension")
        if not isinstance(extension, str) or not _valid_extension(extension):
            raise OutputPolicyError("file output_policy 必须声明安全 extension，例如 .obj、.json、.csv。")


def file_output_extension(policy: Dict[str, Any]) -> str:
    extension = policy.get("extension")
    if not isinstance(extension, str) or not _valid_extension(extension):
        raise OutputPolicyError("file output_policy 必须声明安全 extension。")
    return extension if extension.startswith(".") else "." + extension


def _normalize_format(value: str) -> str:
    text = value.strip().lower()
    if text == "shapefile":
        return "shp"
    if text in ("geodatabase", "file_gdb", "feature_class"):
        return "gdb"
    if text == "tiff":
        return "tiff"
    return text.lstrip(".")


def _valid_extension(value: str) -> bool:
    text = value.strip()
    return bool(re.match(r"^\.[A-Za-z0-9]{1,12}$", text) or re.match(r"^[A-Za-z0-9]{1,12}$", text))
