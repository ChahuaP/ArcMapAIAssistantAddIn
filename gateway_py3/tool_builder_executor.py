from __future__ import annotations

import ast
import re
from typing import Any, Dict

from .output_policy import managed_output_parameter_names, output_policy_type
from .tool_builder_errors import ToolBuilderError
from .tool_builder_rules import (
    ALLOWED_ARCPY_CALLS,
    ALLOWED_BARE_CALLS,
    CODING_RE,
    DISALLOWED_ATTR_CALLS,
    DISALLOWED_CALLS,
    DISALLOWED_IMPORT_ROOTS,
    EXECUTOR_ENCODING_HEADER,
    PYTHON2_UNSUPPORTED_KEYWORDS,
    PYTHON2_UNSUPPORTED_NAMES,
    PYTHON2_UNSUPPORTED_NODE_NAMES,
    RESERVED_ARCGIS_FIELD_NAMES,
    RESERVED_CURSOR_WRITE_FIELDS,
)


def validate_executor_contract(spec: Dict[str, Any], code: str) -> None:
    tree = _validate_executor_code(code)
    _validate_reserved_field_usage(tree)
    _validate_output_open_calls(tree, spec)
    if spec.get("side_effects") != "writes_data":
        return
    properties = (spec.get("parameters_schema") or {}).get("properties") or {}
    required = (spec.get("parameters_schema") or {}).get("required") or []
    if "output_path" in properties:
        raise ToolBuilderError("writes_data 自定义工具不能在 parameters_schema 声明 output_path；output_path 只由 GeoPilot 运行时注入。")
    if "output_name" not in properties:
        raise ToolBuilderError("writes_data 自定义工具必须声明 output_name 参数，由 GeoPilot 统一生成输出路径。")
    if "output_name" not in required:
        raise ToolBuilderError("writes_data 自定义工具必须把 output_name 声明为 required。")
    if not _executor_uses_argument_key(tree, "output_path"):
        raise ToolBuilderError("writes_data 自定义工具必须使用 arguments[\"output_path\"]，不要自己拼输出路径。")
    for forbidden_key in sorted(_executor_forbidden_output_argument_keys(tree, spec)):
        if _executor_uses_argument_key(tree, forbidden_key):
            raise ToolBuilderError("writes_data 自定义工具执行代码不能读取 arguments[\"%s\"]；请只使用 GeoPilot 生成的 arguments[\"output_path\"]。" % forbidden_key)
    _validate_create_featureclass_spatial_reference(tree)


def normalize_executor_code(code: str) -> str:
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
    _validate_exception_handlers(tree)
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    execute_func = functions.get("execute")
    if execute_func is None:
        raise ToolBuilderError("executor_code 必须定义 execute(context, arguments, step_outputs)。")
    _validate_execute_signature(execute_func)
    defined_names = _callable_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _validate_import(alias.name)
        elif isinstance(node, ast.ImportFrom):
            _validate_import(node.module or "")
        elif isinstance(node, ast.Call):
            _validate_call(node.func)
            _validate_bare_call_is_defined(node.func, defined_names)
            _validate_call_keywords_are_python2(node)
        elif isinstance(node, ast.Name):
            _validate_name_is_python2(node.id)
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
    if chain and chain[0] == "arcpy" and chain not in ALLOWED_ARCPY_CALLS:
        raise ToolBuilderError("自定义工具调用了未确认的 ArcMap ArcPy 函数：%s。请使用真实存在的 ArcMap ArcPy API，不要自己编造函数名。" % ".".join(chain))
    if chain and chain[-1] == "getOutput":
        raise ToolBuilderError("自定义工具不能调用 getOutput；GeoPilot 传入的是 ArcMap Layer 对象和 arguments[\"output_path\"]，不是地理处理 Result。")
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        pair = (func.value.id, func.attr)
        if pair in DISALLOWED_ATTR_CALLS:
            raise ToolBuilderError("自定义工具不能调用不安全函数：%s.%s。" % pair)


def _validate_create_featureclass_spatial_reference(tree: ast.AST) -> None:
    describe_variables = _describe_variables(tree)
    spatial_reference_variables = _spatial_reference_variables(tree, describe_variables)
    output_path_variables = _argument_key_variables(tree, "output_path")
    output_workspace_variables = _path_function_variables(tree, "dirname", output_path_variables)
    output_name_variables = _path_function_variables(tree, "basename", output_path_variables)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_create_featureclass_call(node.func):
            continue
        workspace = _create_featureclass_argument(node, "out_path", 0)
        name = _create_featureclass_argument(node, "out_name", 1)
        if not _is_output_workspace_expression(workspace, output_path_variables, output_workspace_variables):
            raise ToolBuilderError(
                "CreateFeatureclass_management 的 out_path 必须是 os.path.dirname(arguments[\"output_path\"])；"
                "不能把完整 output_path、手动拼出的 gdb 路径或输出名称当作 workspace。"
            )
        if not _is_output_name_expression(name, output_path_variables, output_name_variables):
            raise ToolBuilderError(
                "CreateFeatureclass_management 的 out_name 必须是 os.path.basename(arguments[\"output_path\"])；"
                "不能自己拼输出名称或把完整 output_path 当作要素类名称。"
            )
        spatial_reference = _create_featureclass_spatial_reference_argument(node)
        if spatial_reference is None:
            raise ToolBuilderError(
                "CreateFeatureclass_management 必须显式传入空间参考："
                "请使用 arcpy.Describe(输入图层).spatialReference，不要省略 spatial_reference。"
            )
        if not _is_valid_spatial_reference_expression(spatial_reference, describe_variables, spatial_reference_variables):
            raise ToolBuilderError(
                "CreateFeatureclass_management 的 spatial_reference 必须来自 arcpy.Describe(输入图层).spatialReference；"
                "不能传 context['spatial_reference']、spatialReference.name、factoryCode、普通字符串或图层属性。"
            )


def _validate_output_open_calls(tree: ast.AST, spec: Dict[str, Any]) -> None:
    open_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_open_call(node.func)]
    if not open_calls:
        return
    if spec.get("side_effects") != "writes_data" or output_policy_type(spec.get("output_policy") or {}) not in ("file", "raster"):
        raise ToolBuilderError("只有 file/raster 输出自定义工具可以调用 open，且只能写 arguments[\"output_path\"]。")
    output_path_variables = _argument_key_variables(tree, "output_path")
    for node in open_calls:
        if _call_chain(node.func) != ("open",):
            raise ToolBuilderError("文件输出只能调用 open(arguments[\"output_path\"], \"w\"/\"wb\")，不能使用其他 open 变体。")
        if len(node.args) != 2 or node.keywords:
            raise ToolBuilderError("open 只能写成 open(arguments[\"output_path\"], \"w\"/\"wb\")；不要传 encoding、buffering 等额外参数。")
        if not node.args or not _is_output_path_reference(node.args[0], output_path_variables):
            raise ToolBuilderError("open 的第一个参数必须是 arguments[\"output_path\"] 或直接由它赋值的变量。")
        mode = _open_mode(node)
        if mode not in ("w", "wb"):
            raise ToolBuilderError("open 必须使用写入模式 \"w\" 或 \"wb\"，不能读取或追加其他文件。")


def _is_open_call(func: ast.AST) -> bool:
    chain = _call_chain(func)
    return bool(chain) and chain[-1] == "open"


def _open_mode(node: ast.Call) -> str:
    if len(node.args) >= 2:
        value = _literal_node_value(node.args[1])
        return value or ""
    for keyword in node.keywords:
        if keyword.arg == "mode":
            value = _literal_node_value(keyword.value)
            return value or ""
    return ""


def _validate_reserved_field_usage(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _call_chain(node.func)
        if _is_add_field_call(chain):
            field_name = _field_name_argument(node)
            if isinstance(field_name, str) and field_name.lower() in RESERVED_ARCGIS_FIELD_NAMES:
                raise ToolBuilderError("自定义工具不能创建 ArcGIS 系统字段 %s；如需保留源 OID，请创建 SRC_OID 这类普通 LONG 字段。" % field_name)
        if _is_write_cursor_call(chain):
            for field_name in _literal_string_list(_cursor_fields_argument(node)):
                lowered = field_name.lower()
                if lowered in RESERVED_CURSOR_WRITE_FIELDS:
                    raise ToolBuilderError("自定义工具不能写入 ArcGIS 系统字段 %s；OID/FID/OBJECTID 只能读取，不能写入。" % field_name)


def _is_add_field_call(chain: tuple[str, ...]) -> bool:
    return chain in (
        ("arcpy", "AddField_management"),
        ("arcpy", "management", "AddField"),
        ("AddField_management",),
    )


def _field_name_argument(node: ast.Call) -> str | None:
    for keyword in node.keywords:
        if keyword.arg in ("field_name", "field"):
            return _literal_node_value(keyword.value)
    if len(node.args) >= 2:
        return _literal_node_value(node.args[1])
    return None


def _is_write_cursor_call(chain: tuple[str, ...]) -> bool:
    return chain in (
        ("arcpy", "da", "InsertCursor"),
        ("arcpy", "da", "UpdateCursor"),
    )


def _cursor_fields_argument(node: ast.Call) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg in ("field_names", "fields"):
            return keyword.value
    if len(node.args) >= 2:
        return node.args[1]
    return None


def _literal_string_list(node: ast.AST | None) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    result = []
    for item in node.elts:
        value = _literal_node_value(item)
        if isinstance(value, str):
            result.append(value)
    return result


def _is_create_featureclass_call(func: ast.AST) -> bool:
    chain = _call_chain(func)
    return chain in (
        ("arcpy", "CreateFeatureclass_management"),
        ("arcpy", "management", "CreateFeatureclass"),
        ("CreateFeatureclass_management",),
    )


def _create_featureclass_argument(node: ast.Call, keyword_name: str, index: int) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    if len(node.args) > index:
        return node.args[index]
    return None


def _create_featureclass_spatial_reference_argument(node: ast.Call) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == "spatial_reference":
            return keyword.value
    if len(node.args) >= 7:
        return node.args[6]
    return None


def _argument_key_variables(tree: ast.AST, key: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if _is_argument_key_expression(node.value, key):
            names.update(_assignment_target_names(node))
    return names


def _path_function_variables(tree: ast.AST, function_name: str, output_path_variables: set[str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if _is_path_function_expression(node.value, function_name, output_path_variables):
            names.update(_assignment_target_names(node))
    return names


def _is_output_workspace_expression(
    node: ast.AST | None,
    output_path_variables: set[str],
    output_workspace_variables: set[str]
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in output_workspace_variables and node.id not in output_path_variables
    return _is_path_function_expression(node, "dirname", output_path_variables)


def _is_output_name_expression(
    node: ast.AST | None,
    output_path_variables: set[str],
    output_name_variables: set[str]
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in output_name_variables and node.id not in output_path_variables
    return _is_path_function_expression(node, "basename", output_path_variables)


def _is_path_function_expression(node: ast.AST | None, function_name: str, output_path_variables: set[str]) -> bool:
    if not isinstance(node, ast.Call) or _call_chain(node.func) != ("os", "path", function_name):
        return False
    return bool(node.args) and _is_output_path_reference(node.args[0], output_path_variables)


def _is_output_path_reference(node: ast.AST, output_path_variables: set[str]) -> bool:
    return (
        (isinstance(node, ast.Name) and node.id in output_path_variables)
        or _is_argument_key_expression(node, "output_path")
    )


def _executor_forbidden_output_argument_keys(tree: ast.AST, spec: Dict[str, Any]) -> set[str]:
    keys = managed_output_parameter_names(spec.get("output_policy") or {}) - {"output_path"}
    for key in _executor_argument_keys(tree):
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        if normalized.startswith("output") and normalized != "outputpath":
            keys.add(key)
    return keys


def _executor_argument_keys(tree: ast.AST) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "arguments":
            key = _literal_subscript_key(node)
            if key:
                keys.add(key)
        elif isinstance(node, ast.Call) and _attribute_chain(node.func) == ("arguments", "get"):
            if node.args:
                key = _literal_node_value(node.args[0])
                if key:
                    keys.add(key)
    return keys


def _is_argument_key_expression(node: ast.AST, key: str) -> bool:
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "arguments":
        return _literal_subscript_key(node) == key
    if isinstance(node, ast.Call) and _attribute_chain(node.func) == ("arguments", "get"):
        return bool(node.args) and _literal_node_value(node.args[0]) == key
    return False


def _describe_variables(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not _is_arcpy_describe_call(node.value):
            continue
        names.update(_assignment_target_names(node))
    return names


def _spatial_reference_variables(tree: ast.AST, describe_variables: set[str]) -> set[str]:
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if not _is_valid_spatial_reference_expression(node.value, describe_variables, names):
                continue
            for target_name in _assignment_target_names(node):
                if target_name not in names:
                    names.add(target_name)
                    changed = True
    return names


def _assignment_target_names(node: ast.Assign) -> list[str]:
    return [target.id for target in node.targets if isinstance(target, ast.Name)]


def _is_valid_spatial_reference_expression(
    node: ast.AST,
    describe_variables: set[str],
    spatial_reference_variables: set[str]
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in spatial_reference_variables
    if not isinstance(node, ast.Attribute) or node.attr != "spatialReference":
        return False
    if isinstance(node.value, ast.Name):
        return node.value.id in describe_variables
    return _is_arcpy_describe_call(node.value)


def _is_arcpy_describe_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_chain(node.func) == ("arcpy", "Describe")


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


def _validate_exception_handlers(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_broad_exception_handler(node):
            continue
        if not any(isinstance(child, ast.Raise) for child in ast.walk(node)):
            raise ToolBuilderError("executor_code 不能用 broad except 吞掉错误；捕获 Exception/BaseException 后必须 raise 暴露真实失败原因。")


def _is_broad_exception_handler(node: ast.ExceptHandler) -> bool:
    if node.type is None:
        return True
    if isinstance(node.type, ast.Name):
        return node.type.id in ("Exception", "BaseException")
    return False


def _validate_name_is_python2(name: str) -> None:
    if name in PYTHON2_UNSUPPORTED_NAMES:
        raise ToolBuilderError("executor_code 不能使用 Python 3 专属名称 %s；ArcMap 运行的是 Python 2.7。" % name)


def _validate_call_keywords_are_python2(node: ast.Call) -> None:
    for keyword in node.keywords:
        if keyword.arg in PYTHON2_UNSUPPORTED_KEYWORDS:
            raise ToolBuilderError("executor_code 不能使用 Python 3 专属关键字参数 %s；ArcMap 运行的是 Python 2.7。" % keyword.arg)


def _callable_names(tree: ast.AST) -> set[str]:
    names = set(ALLOWED_BARE_CALLS)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _validate_bare_call_is_defined(func: ast.AST, defined_names: set[str]) -> None:
    if not isinstance(func, ast.Name):
        return
    if func.id not in defined_names:
        raise ToolBuilderError("executor_code 调用了未定义函数 %s；请在 executor.py 内定义它，或改用真实存在的 ArcMap/Python 2.7 API。" % func.id)


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


def _call_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    return _attribute_chain(node)


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
