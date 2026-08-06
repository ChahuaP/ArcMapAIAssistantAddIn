from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from .custom_tool_contract import CustomToolContractError, build_review_payload
from .capability_registry import CapabilityRegistry, CapabilityContractError
from .output_policy import (
    OutputPolicyError,
    canonical_output_policy,
    managed_output_properties,
    validate_output_policy,
)
from .paths import appdata_dir
from .tool_builder_errors import ToolBuilderError
from .tool_builder_executor import normalize_executor_code, validate_executor_contract

PENDING_ROOT = appdata_dir() / "pending_tools"
ENABLED_ROOT = appdata_dir() / "custom_tools" / "enabled"


def create_draft_tool(store, arguments: Dict[str, Any]) -> Dict[str, Any]:
    package = _prepare_package(arguments)
    existing = _find_existing_tool_by_operation_id(store, package["spec"]["id"])
    if existing:
        if existing.get("status") == "enabled":
            raise ToolBuilderError(
                "%s 已经是启用的 operation。只是执行时请直接生成 workflow 使用该 operation；"
                "需要修复或修改时先 toolbuilder_get_draft，再 toolbuilder_revise_draft，不能用 toolbuilder_create_draft 覆盖已启用工具。"
                % package["spec"]["id"]
            )
        reason = "同一个 operation_id 已存在，按原工具修订。"
        return _write_revision(store, existing, package, reason)

    draft_id = str(uuid.uuid4())
    package = _package_for_tool_id(package, draft_id)
    files = _write_package_files(draft_id, package)
    payload = _payload_for_package(package, revision_number=1, change_summary="initial draft")
    return store.create_pending_tool(package["name"], package["capability"], payload, files, tool_id=draft_id)


def revise_draft_tool(store, arguments: Dict[str, Any]) -> Dict[str, Any]:
    tool_ref = _required_string(arguments, "tool_id")
    change_summary = _required_string(arguments, "change_summary")
    current = _resolve_tool_reference(store, tool_ref)
    package = _prepare_package(arguments)
    previous_spec = (current.get("payload") or {}).get("operation_spec") or {}
    previous_operation_id = str(previous_spec.get("id") or "")
    if previous_operation_id and package["spec"]["id"] != previous_operation_id:
        raise ToolBuilderError("修订已有工具不能改变 operation_id：当前是 %s。" % previous_operation_id)
    return _write_revision(store, current, package, change_summary)


def get_tool_package(store, tool_id: str) -> Dict[str, Any]:
    tool = _resolve_tool_reference(store, tool_id)
    resolved_tool_id = tool["id"]
    result = dict(tool)
    package_dir = _tool_package_dir(resolved_tool_id, tool.get("status"))
    executor_path = package_dir / "executor.py"
    spec_path = package_dir / "operation_spec.json"
    tests_path = package_dir / "tests.json"
    if executor_path.exists():
        result["executor_code"] = executor_path.read_text(encoding="utf-8")
    if spec_path.exists():
        with spec_path.open("r", encoding="utf-8") as handle:
            result["operation_spec"] = canonicalize_operation_spec(json.load(handle), source=spec_path)
    if tests_path.exists():
        with tests_path.open("r", encoding="utf-8") as handle:
            result["tests"] = json.load(handle)
    return result


def enable_tool(store, tool_id: str) -> Dict[str, Any]:
    tool = store.get_pending_tool(tool_id)
    if tool["status"] != "pending_review":
        raise ToolBuilderError("只有待审核工具可以启用。")
    source_dir = PENDING_ROOT / tool_id
    if not source_dir.exists():
        raise ToolBuilderError("找不到待审核工具目录：%s" % source_dir)
    _validate_tool_files(source_dir, tool_id)
    target_dir = ENABLED_ROOT / tool_id
    if target_dir.exists():
        shutil.rmtree(str(target_dir))
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source_dir), str(target_dir))
    return store.set_pending_tool_status(tool_id, "enabled")


def reject_tool(store, tool_id: str) -> Dict[str, Any]:
    return store.set_pending_tool_status(tool_id, "rejected")


def delete_tool(store, tool_id: str) -> Dict[str, Any]:
    tool = store.get_pending_tool(tool_id)
    _delete_child_directory(PENDING_ROOT, tool_id)
    _delete_child_directory(ENABLED_ROOT, tool_id)
    store.delete_pending_tool(tool_id)
    return {"ok": True, "id": tool_id, "status": tool["status"], "name": tool["name"]}


def _prepare_package(arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = _required_string(arguments, "name")
    capability = _required_string(arguments, "capability")
    operation_spec = arguments.get("operation_spec")
    executor_code = normalize_executor_code(_required_string(arguments, "executor_code"))
    tests = arguments.get("tests", [])
    if not isinstance(operation_spec, dict):
        raise ToolBuilderError("operation_spec 必须是对象。")
    spec = canonicalize_operation_spec(operation_spec)
    try:
        review = build_review_payload(spec, tests)
    except CustomToolContractError as exc:
        raise ToolBuilderError(str(exc))
    validate_executor_contract(spec, executor_code)
    return {
        "name": name,
        "capability": capability,
        "spec": spec,
        "executor_code": executor_code,
        "tests": tests,
        "review": review,
    }


def _write_revision(store, current: Dict[str, Any], package: Dict[str, Any], change_summary: str) -> Dict[str, Any]:
    tool_id = current["id"]
    package = _package_for_tool_id(package, tool_id)
    files = _write_package_files(tool_id, package)
    previous_payload = current.get("payload") or {}
    previous_revision = _revision_number(previous_payload)
    history = list(previous_payload.get("revision_history") or [])
    history.append(_history_entry(current, previous_revision))
    payload = _payload_for_package(
        package,
        revision_number=previous_revision + 1,
        change_summary=change_summary,
        revision_history=history[-12:]
    )
    if current.get("status") == "enabled":
        _delete_child_directory(ENABLED_ROOT, tool_id)
    return store.update_pending_tool(tool_id, "pending_review", package["name"], package["capability"], payload, files)


def _package_for_tool_id(package: Dict[str, Any], tool_id: str) -> Dict[str, Any]:
    result = dict(package)
    spec = dict(result["spec"])
    spec["executor"] = "custom_tool:%s:execute" % tool_id
    spec = canonicalize_operation_spec(spec)
    try:
        review = build_review_payload(spec, result["tests"])
    except CustomToolContractError as exc:
        raise ToolBuilderError(str(exc))
    result["spec"] = spec
    result["review"] = review
    return result


def _write_package_files(tool_id: str, package: Dict[str, Any]) -> Dict[str, str]:
    draft_dir = PENDING_ROOT / tool_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "operation_spec": str(draft_dir / "operation_spec.json"),
        "executor": str(draft_dir / "executor.py"),
        "tests": str(draft_dir / "tests.json"),
    }
    _write_text(
        Path(files["operation_spec"]),
        json.dumps(package["spec"], ensure_ascii=False, indent=2, sort_keys=True),
    )
    _write_text(Path(files["executor"]), package["executor_code"])
    _write_text(
        Path(files["tests"]),
        json.dumps(package["tests"], ensure_ascii=False, indent=2, sort_keys=True),
    )
    return files


def _payload_for_package(
    package: Dict[str, Any],
    revision_number: int,
    change_summary: str,
    revision_history: list[Dict[str, Any]] | None = None
) -> Dict[str, Any]:
    return {
        "operation_spec": package["spec"],
        "tests": package["tests"],
        "review": package["review"],
        "revision": {
            "number": revision_number,
            "change_summary": change_summary,
        },
        "revision_history": revision_history or [],
    }


def _history_entry(tool: Dict[str, Any], revision_number: int) -> Dict[str, Any]:
    payload = tool.get("payload") or {}
    spec = payload.get("operation_spec") or {}
    revision = payload.get("revision") or {}
    return {
        "number": revision_number,
        "status": str(tool.get("status") or ""),
        "operation_id": str(spec.get("id") or ""),
        "summary": str(spec.get("summary") or ""),
        "change_summary": str(revision.get("change_summary") or ""),
        "updated_at": tool.get("updated_at"),
    }


def _revision_number(payload: Dict[str, Any]) -> int:
    revision = payload.get("revision") or {}
    try:
        return max(1, int(revision.get("number") or 1))
    except (TypeError, ValueError):
        return 1


def _find_existing_tool_by_operation_id(store, operation_id: str) -> Dict[str, Any] | None:
    for tool in store.list_pending_tools():
        spec = (tool.get("payload") or {}).get("operation_spec") or {}
        if spec.get("id") == operation_id:
            return tool
    return None


def _resolve_tool_reference(store, value: str) -> Dict[str, Any]:
    identifier = _tool_identifier(value)
    try:
        return store.get_pending_tool(identifier)
    except KeyError:
        pass
    if identifier.startswith("custom."):
        tool = _find_existing_tool_by_operation_id(store, identifier)
        if tool:
            return tool
    raise KeyError(value)


def _tool_identifier(value: str) -> str:
    identifier = str(value or "").strip()
    if identifier.startswith("custom_tool:"):
        parts = identifier.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return identifier


def _tool_package_dir(tool_id: str, status: str | None) -> Path:
    if status == "enabled":
        enabled = ENABLED_ROOT / tool_id
        if enabled.exists():
            return enabled
    return PENDING_ROOT / tool_id


def enabled_operation_specs() -> list[Dict[str, Any]]:
    specs = []
    if not ENABLED_ROOT.exists():
        return specs
    for path in sorted(ENABLED_ROOT.glob("*/operation_spec.json")):
        with path.open("r", encoding="utf-8") as handle:
            spec = json.load(handle)
        specs.append(canonicalize_operation_spec(spec, source=path))
    return specs


def canonicalize_operation_spec(spec: Dict[str, Any], source: Path | None = None) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ToolBuilderError("operation_spec 必须是对象。")
    result = dict(spec)
    try:
        result["parameters_schema"] = _canonical_parameters_schema(result.get("parameters_schema"))
    except ToolBuilderError as exc:
        label = "：%s" % source if source else ""
        raise ToolBuilderError("operation_spec 参数定义不合法%s：%s" % (label, exc))
    result["output_policy"] = canonical_output_policy(
        result.get("output_policy"),
        str(result.get("side_effects") or ""),
    )
    _ensure_managed_output_parameters(result)
    if not isinstance(result.get("context_requirements"), dict):
        result["context_requirements"] = {}
    if not isinstance(result.get("output_policy"), dict):
        result["output_policy"] = {}
    contract = result.get("capability_contract")
    if isinstance(contract, dict):
        contract = dict(contract)
        contract["parameters_schema"] = result["parameters_schema"]
        contract["side_effects"] = result.get("side_effects")
        result["capability_contract"] = contract
    _validate_spec_shape(result)
    try:
        CapabilityRegistry([result])
    except CapabilityContractError as exc:
        raise ToolBuilderError(str(exc))
    return result


def _ensure_managed_output_parameters(spec: Dict[str, Any]) -> None:
    if spec.get("side_effects") != "writes_data":
        return
    schema = spec.get("parameters_schema")
    if not isinstance(schema, dict):
        return
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return
    for name, property_schema in managed_output_properties(spec.get("output_policy") or {}).items():
        properties.setdefault(name, property_schema)


def _canonical_parameters_schema(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        raise ToolBuilderError("parameters_schema 必须是对象。")
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict):
            raise ToolBuilderError("parameters_schema.properties 必须是对象。")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ToolBuilderError("parameters_schema.required 必须是字符串数组。")
        result = dict(schema)
        result["properties"] = {
            name: _canonical_parameter_property(value, name)
            for name, value in properties.items()
        }
        result["required"] = required
        if result.get("additionalProperties") is not True:
            result["additionalProperties"] = False
        return result

    properties = {}
    required = []
    for name, value in schema.items():
        if not isinstance(name, str) or not name:
            raise ToolBuilderError("参数名不能为空。")
        if not isinstance(value, dict):
            raise ToolBuilderError("参数“%s”定义必须是对象。" % name)
        prop = dict(value)
        required_flag = prop.pop("required", False)
        if required_flag is True or str(required_flag).lower() in ("true", "1", "yes"):
            required.append(name)
        properties[name] = _canonical_parameter_property(prop, name)
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False
    }


def _canonical_parameter_property(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolBuilderError("参数“%s”定义必须是对象。" % name)
    prop = dict(value)
    parameter_type = prop.get("type")
    if parameter_type == "layer" or prop.get("x-geopilot-kind") == "layer":
        if parameter_type not in (None, "layer", "string", "object"):
            raise ToolBuilderError("图层参数“%s”的 type 必须是 layer 或 string。" % name)
        prop["type"] = "string"
        prop["x-geopilot-kind"] = "layer"
        return prop
    allowed = {"string", "boolean", "integer", "number", "array", "object"}
    if parameter_type not in allowed:
        raise ToolBuilderError("参数“%s”的 type 不合法。" % name)
    return prop


def _validate_spec_shape(spec: Dict[str, Any]) -> None:
    required = {
        "id",
        "version",
        "category",
        "summary",
        "parameters_schema",
        "context_requirements",
        "side_effects",
        "output_policy",
        "executor",
        "examples",
        "capability_contract",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise ToolBuilderError("operation_spec 缺少字段：%s" % "、".join(missing))
    extra = sorted(set(spec) - required - {"keywords", "context_requirements"})
    if extra:
        raise ToolBuilderError("operation_spec 包含未定义字段：%s" % "、".join(extra))
    operation_id = str(spec["id"])
    if not re.match(r"^custom\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$", operation_id):
        raise ToolBuilderError("自定义工具 id 必须以 custom. 开头，并且只使用小写英文、数字和下划线。")
    side_effects = spec.get("side_effects")
    if side_effects not in ("read_only", "changes_map", "writes_data", "edits_data"):
        raise ToolBuilderError("side_effects 不合法。")
    schema = spec.get("parameters_schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ToolBuilderError("parameters_schema 必须是 JSON Schema object。")
    if not isinstance(schema.get("properties"), dict):
        raise ToolBuilderError("parameters_schema.properties 必须是对象。")
    if not isinstance(schema.get("required", []), list):
        raise ToolBuilderError("parameters_schema.required 必须是数组。")
    if not isinstance(spec.get("context_requirements"), dict):
        raise ToolBuilderError("context_requirements 必须是对象。")
    if not isinstance(spec.get("output_policy"), dict):
        raise ToolBuilderError("output_policy 必须是对象。")
    _reject_file_collection_output_policy(spec["output_policy"])
    try:
        validate_output_policy(spec.get("output_policy") or {}, side_effects)
    except OutputPolicyError as exc:
        raise ToolBuilderError(str(exc))
    if not isinstance(spec.get("examples"), list) or not spec.get("examples"):
        raise ToolBuilderError("operation_spec.examples 必须至少提供 1 个真实调用示例。")


def _reject_file_collection_output_policy(policy: Dict[str, Any]) -> None:
    if policy.get("type") == "file_collection":
        raise ToolBuilderError(
            "file_collection 仅限内置批量导出 operation，不能用于自定义工具。"
        )


def _validate_tool_files(source_dir: Path, tool_id: str) -> None:
    spec_path = source_dir / "operation_spec.json"
    executor_path = source_dir / "executor.py"
    if not spec_path.exists() or not executor_path.exists():
        raise ToolBuilderError("待审核工具包缺少 operation_spec.json 或 executor.py。")
    with spec_path.open("r", encoding="utf-8") as handle:
        spec = json.load(handle)
    spec = canonicalize_operation_spec(spec, source=spec_path)
    tests_path = source_dir / "tests.json"
    if not tests_path.exists():
        raise ToolBuilderError("待审核工具包缺少 tests.json。")
    with tests_path.open("r", encoding="utf-8") as handle:
        tests = json.load(handle)
    try:
        build_review_payload(spec, tests)
    except CustomToolContractError as exc:
        raise ToolBuilderError(str(exc))
    expected_executor = "custom_tool:%s:execute" % tool_id
    if spec.get("executor") != expected_executor:
        raise ToolBuilderError("operation_spec executor 与待审核工具目录不一致。")
    _write_text(spec_path, json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True))
    executor_code = normalize_executor_code(executor_path.read_text(encoding="utf-8"))
    validate_executor_contract(spec, executor_code)
    _write_text(executor_path, executor_code)


def _required_string(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolBuilderError("%s 不能为空。" % key)
    return value.strip()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _delete_child_directory(root: Path, child_name: str) -> None:
    target = (root / child_name).resolve()
    root_resolved = root.resolve()
    if not str(target).lower().startswith(str(root_resolved).lower() + "\\"):
        raise ToolBuilderError("工具目录不合法：%s" % child_name)
    if target.exists():
        shutil.rmtree(str(target))
