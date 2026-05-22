from __future__ import annotations

import ast
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from .paths import appdata_dir
PENDING_ROOT = appdata_dir() / "pending_tools"
ENABLED_ROOT = appdata_dir() / "custom_tools" / "enabled"
DISALLOWED_IMPORT_ROOTS = {
    "ctypes",
    "ftplib",
    "http",
    "paramiko",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "urllib",
    "urllib2",
    "winreg",
    "_winreg",
}
DISALLOWED_CALLS = {"__import__", "compile", "eval", "exec", "input", "open", "raw_input"}
DISALLOWED_ATTR_CALLS = {
    ("os", "popen"),
    ("os", "remove"),
    ("os", "removedirs"),
    ("os", "rename"),
    ("os", "replace"),
    ("os", "rmdir"),
    ("os", "startfile"),
    ("os", "system"),
    ("os", "unlink"),
}
EXECUTOR_ENCODING_HEADER = "# -*- coding: utf-8 -*-\n"
CODING_RE = re.compile(r"coding[:=]\s*([-\w.]+)")
PYTHON2_UNSUPPORTED_NODE_NAMES = {
    "AnnAssign",
    "AsyncFor",
    "AsyncFunctionDef",
    "AsyncWith",
    "Await",
    "FormattedValue",
    "JoinedStr",
    "Nonlocal",
    "NamedExpr",
}


class ToolBuilderError(Exception):
    pass


def create_draft_tool(store, arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = _required_string(arguments, "name")
    capability = _required_string(arguments, "capability")
    operation_spec = arguments.get("operation_spec")
    executor_code = _normalize_executor_code(_required_string(arguments, "executor_code"))
    tests = arguments.get("tests", [])
    if not isinstance(operation_spec, dict):
        raise ToolBuilderError("operation_spec 必须是对象。")
    if not isinstance(tests, list):
        raise ToolBuilderError("tests 必须是数组。")
    spec = canonicalize_operation_spec(operation_spec)
    _validate_executor_contract(spec, executor_code)

    draft_id = str(uuid.uuid4())
    draft_dir = PENDING_ROOT / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    spec["executor"] = "custom_tool:%s:execute" % draft_id
    spec = canonicalize_operation_spec(spec)

    files = {
        "operation_spec": str(draft_dir / "operation_spec.json"),
        "executor": str(draft_dir / "executor.py"),
        "tests": str(draft_dir / "tests.json"),
    }
    _write_text(Path(files["operation_spec"]), json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True))
    _write_text(Path(files["executor"]), executor_code)
    _write_text(Path(files["tests"]), json.dumps(tests, ensure_ascii=False, indent=2, sort_keys=True))
    payload = {
        "operation_spec": spec,
        "tests": tests,
    }
    return store.create_pending_tool(name, capability, payload, files, tool_id=draft_id)


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
    if not isinstance(result.get("context_requirements"), dict):
        result["context_requirements"] = {}
    if not isinstance(result.get("output_policy"), dict):
        result["output_policy"] = {}
    _validate_spec_shape(result)
    return result


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
        result.setdefault("additionalProperties", False)
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
    if parameter_type == "layer":
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
        "model_card",
        "parameters_schema",
        "context_requirements",
        "side_effects",
        "output_policy",
        "executor",
        "examples",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise ToolBuilderError("operation_spec 缺少字段：%s" % "、".join(missing))
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


def _validate_tool_files(source_dir: Path, tool_id: str) -> None:
    spec_path = source_dir / "operation_spec.json"
    executor_path = source_dir / "executor.py"
    if not spec_path.exists() or not executor_path.exists():
        raise ToolBuilderError("待审核工具包缺少 operation_spec.json 或 executor.py。")
    with spec_path.open("r", encoding="utf-8") as handle:
        spec = json.load(handle)
    spec = canonicalize_operation_spec(spec, source=spec_path)
    expected_executor = "custom_tool:%s:execute" % tool_id
    if spec.get("executor") != expected_executor:
        raise ToolBuilderError("operation_spec executor 与待审核工具目录不一致。")
    _write_text(spec_path, json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True))
    executor_code = _normalize_executor_code(executor_path.read_text(encoding="utf-8"))
    _validate_executor_contract(spec, executor_code)
    _write_text(executor_path, executor_code)


def _validate_executor_contract(spec: Dict[str, Any], code: str) -> None:
    tree = _validate_executor_code(code)
    if spec.get("side_effects") != "writes_data":
        return
    properties = (spec.get("parameters_schema") or {}).get("properties") or {}
    required = (spec.get("parameters_schema") or {}).get("required") or []
    if "output_name" not in properties:
        raise ToolBuilderError("writes_data 自定义工具必须声明 output_name 参数，由 GeoPilot 统一生成输出路径。")
    if "output_name" not in required:
        raise ToolBuilderError("writes_data 自定义工具必须把 output_name 声明为 required。")
    if not _executor_uses_argument_key(tree, "output_path"):
        raise ToolBuilderError("writes_data 自定义工具必须使用 arguments[\"output_path\"]，不要自己拼输出路径。")
    for forbidden_key in ("output_workspace", "output_name"):
        if _executor_uses_argument_key(tree, forbidden_key):
            raise ToolBuilderError("writes_data 自定义工具执行代码不能读取 arguments[\"%s\"]；请只使用 GeoPilot 生成的 arguments[\"output_path\"]。" % forbidden_key)


def _normalize_executor_code(code: str) -> str:
    text = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(True)
    for line in lines[:2]:
        match = CODING_RE.search(line)
        if not match:
            continue
        encoding = match.group(1).lower().replace("_", "-")
        if encoding != "utf-8":
            raise ToolBuilderError("executor.py 必须使用 UTF-8 编码声明。")
        return text
    if lines and lines[0].startswith("#!"):
        return lines[0] + EXECUTOR_ENCODING_HEADER + "".join(lines[1:])
    return EXECUTOR_ENCODING_HEADER + text


def _validate_executor_code(code: str) -> ast.AST:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ToolBuilderError("executor_code 不是有效 Python 代码：%s" % exc)
    _validate_python2_subset(tree)
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    execute_func = functions.get("execute")
    if execute_func is None:
        raise ToolBuilderError("executor_code 必须定义 execute(context, arguments, step_outputs)。")
    _validate_execute_signature(execute_func)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _validate_import(alias.name)
        elif isinstance(node, ast.ImportFrom):
            _validate_import(node.module or "")
        elif isinstance(node, ast.Call):
            _validate_call(node.func)
    return tree


def _validate_import(module_name: str) -> None:
    root = module_name.split(".", 1)[0]
    if root in DISALLOWED_IMPORT_ROOTS:
        raise ToolBuilderError("自定义工具不能导入不安全模块：%s。" % root)
    if module_name == "arcpy.mp" or module_name.startswith("arcpy.mp."):
        raise ToolBuilderError("自定义工具运行在 ArcMap Python 2.7，不能使用 ArcGIS Pro 的 arcpy.mp。")
    if module_name == "arcpy.mapping" or module_name.startswith("arcpy.mapping."):
        raise ToolBuilderError("自定义工具不能直接访问当前地图；请使用 runtime 传入的图层参数。")


def _validate_call(func: ast.AST) -> None:
    if isinstance(func, ast.Name) and func.id in DISALLOWED_CALLS:
        raise ToolBuilderError("自定义工具不能调用不安全函数：%s。" % func.id)
    chain = _attribute_chain(func)
    if chain[:2] == ("arcpy", "mp"):
        raise ToolBuilderError("自定义工具运行在 ArcMap Python 2.7，不能使用 ArcGIS Pro 的 arcpy.mp。")
    if chain[:2] == ("arcpy", "mapping"):
        raise ToolBuilderError("自定义工具不能直接访问当前地图；请使用 runtime 传入的图层参数。")
    if chain and chain[-1] == "getOutput":
        raise ToolBuilderError("自定义工具不能调用 getOutput；GeoPilot 传入的是 ArcMap Layer 对象和 arguments[\"output_path\"]，不是地理处理 Result。")
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        pair = (func.value.id, func.attr)
        if pair in DISALLOWED_ATTR_CALLS:
            raise ToolBuilderError("自定义工具不能调用不安全函数：%s.%s。" % pair)


def _validate_python2_subset(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if node.__class__.__name__ in PYTHON2_UNSUPPORTED_NODE_NAMES:
            raise ToolBuilderError("executor_code 必须使用 ArcMap Python 2.7 可执行语法，不能使用 f-string、类型注解或 async 等 Python 3 语法。")
        if isinstance(node, ast.FunctionDef):
            if getattr(node, "returns", None) is not None:
                raise ToolBuilderError("executor_code 不能使用类型注解。")
            _validate_arguments_are_python2(node.args)
        if isinstance(node, ast.Raise) and getattr(node, "cause", None) is not None:
            raise ToolBuilderError("executor_code 不能使用 Python 3 的 raise ... from ... 语法。")


def _validate_arguments_are_python2(arguments: ast.arguments) -> None:
    if getattr(arguments, "posonlyargs", []):
        raise ToolBuilderError("executor_code 不能使用 Python 3 的仅位置参数。")
    if getattr(arguments, "kwonlyargs", []):
        raise ToolBuilderError("executor_code 不能使用 Python 3 的仅关键字参数。")
    for arg in list(getattr(arguments, "args", [])) + list(getattr(arguments, "kwonlyargs", [])):
        if getattr(arg, "annotation", None) is not None:
            raise ToolBuilderError("executor_code 不能使用类型注解。")


def _validate_execute_signature(execute_func: ast.FunctionDef) -> None:
    args = execute_func.args
    arg_names = [arg.arg for arg in args.args]
    if (
        arg_names != ["context", "arguments", "step_outputs"]
        or args.defaults
        or args.vararg
        or args.kwarg
        or getattr(args, "kwonlyargs", [])
    ):
        raise ToolBuilderError("execute 函数签名必须是 execute(context, arguments, step_outputs)。")


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


def _executor_uses_argument_key(tree: ast.AST, key: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "arguments":
            if _literal_subscript_key(node) == key:
                return True
        if isinstance(node, ast.Call) and _attribute_chain(node.func) == ("arguments", "get"):
            if node.args and _literal_node_value(node.args[0]) == key:
                return True
    return False


def _literal_subscript_key(node: ast.Subscript) -> str | None:
    value = node.slice
    if isinstance(value, ast.Index):
        value = value.value
    return _literal_node_value(value)


def _literal_node_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Str):
        return node.s
    return None


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
