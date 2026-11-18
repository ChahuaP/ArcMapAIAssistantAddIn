"""Check ordered input roles and typed references without executing GIS work."""
from functools import lru_cache

from pydantic import ValidationError

from guizhi.domain.capabilities import Material
from guizhi.domain.operation_catalog import CONTRACTS, connection
from guizhi.domain.operation_parameters import RUN_INPUTS


def validate_input_kinds(operation, spec, kinds):
    if not spec.minimum <= len(kinds) <= spec.maximum:
        raise ValueError(f"{spec.title}需要 {spec.minimum}–{spec.maximum} 份输入，实际 {len(kinds)} 份")
    for index, actual in enumerate(kinds):
        expected = spec.kinds[min(index, len(spec.kinds) - 1)]
        if actual != expected:
            roles = CONTRACTS[operation].roles
            role = roles[min(index, len(roles) - 1)]
            raise ValueError(f"{spec.title}第 {index + 1} 项「{role}」需要 {expected}，收到 {actual}")


@lru_cache(maxsize=128)
def schema_for(model):
    return model.model_json_schema()


def branches(node, definitions):
    if "$ref" in node:
        yield from branches(definitions[node["$ref"].split("/")[-1]], definitions)
    elif "anyOf" in node or "oneOf" in node:
        for alt in node.get("anyOf", node.get("oneOf", ())):
            yield from branches(alt, definitions)
    else:
        yield node


def target_schemas(schema, path):
    definitions = schema.get("$defs", {})
    nodes = [schema]
    for key in path:
        children = []
        for node in nodes:
            for alt in branches(node, definitions):
                if isinstance(key, int):
                    items = alt.get("prefixItems", [])
                    child = items[key] if key < len(items) else alt.get("items")
                else:
                    child = alt.get("properties", {}).get(key, alt.get("additionalProperties"))
                if child is True:
                    child = {}
                if isinstance(child, dict):
                    children.append(child)
        nodes = children
    return [leaf for node in nodes for leaf in branches(node, definitions)]


def accepts(kind, node):
    if not node:
        return True
    if kind in {"selection", "condition"}:
        return node.get("title") == {"selection": "Selection", "condition": "AttributeFilter"}[kind]
    allowed = {"integer": {"integer", "number"}, "number": {"number"}, "array": {"array"}, "run": {"string"}}
    return node.get("type") in allowed[kind]


def material_identity(reference, materials):
    kind, _, name = reference.partition(".")
    if kind == "step":
        return ("step", name)
    items = materials.get(name, ())
    if items:
        first = items[0]
        return ("material", first["file"], first.get("layer"), first.get("revision"))
    return None


def validate_parameters(step, seen, values, materials, input_count):
    from guizhi.infrastructure.operations.runner import operation_parameters

    model = operation_parameters(step.operation)
    schema = schema_for(model)
    symbols = set()

    def resolve(value, path):
        if isinstance(value, dict):
            if set(value) == {"$literal"}:
                return value["$literal"]
            return {key: resolve(child, path + (key,)) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child, path + (index,)) for index, child in enumerate(value)]
        if not isinstance(value, str):
            return value
        prefix, separator, name = value.partition(".")
        if not separator:
            return value
        if prefix == "$param":
            if name not in values:
                raise ValueError(f"参数引用 {value} 未绑定")
            return values[name]
        if prefix not in {"$run", "$selection", "$value"}:
            return value
        upstream = seen.get(name)
        if upstream is None:
            raise ValueError(f"参数 {'.'.join(map(str, path))} 引用的前序步骤 {name} 不存在")
        kind = "run" if prefix == "$run" else connection(upstream.operation, prefix)
        if not kind:
            raise ValueError(f"{upstream.operation} 未声明 {prefix} 输出，无法引用 {value}")
        leaf = path[-1]
        run_field = leaf in RUN_INPUTS
        if prefix == "$run" and not run_field or run_field and prefix != "$run":
            raise ValueError(f"参数 {leaf} 与 {value} 类型不匹配")
        if run_field and RUN_INPUTS[leaf] and upstream.operation not in RUN_INPUTS[leaf]:
            raise ValueError(f"参数 {leaf} 需要来自 {'、'.join(RUN_INPUTS[leaf])} 的作业，收到 {upstream.operation}")
        targets = target_schemas(schema, path)
        if not any(accepts(kind, node) for node in targets):
            expected = "/".join(dict.fromkeys(node.get("title") or node.get("type", "值") for node in targets))
            raise ValueError(f"参数 {'.'.join(map(str, path))} 需要 {expected or '已声明的参数'}，收到 {kind}（{value}）")
        if kind == "selection" and leaf in {"selection", "previous"} and materials is not None:
            source = material_identity(upstream.inputs[0], materials)
            target = material_identity(step.inputs[0], materials)
            if source != target:
                raise ValueError(f"{value} 属于 {upstream.inputs[0]}。请将同一图层作为第一项输入，当前为 {step.inputs[0]}")
        symbols.add(path)
        # Pydantic still validates all literal siblings. Values received from a
        # real upstream operation are revalidated by the execution entry point.
        return value

    parameters = resolve(step.parameters, ())
    if step.operation.startswith("geometry.create_") and input_count is not None:
        if bool(input_count) == (parameters.get("epsg") is not None):
            raise ValueError("一个空间参考图层与 epsg 编号须选择其一")
    try:
        model.model_validate(parameters)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            path = error["loc"]
            if not any(path[:len(symbol)] == symbol for symbol in symbols):
                raise ValueError(f"参数 {'.'.join(map(str, path))}：{error['msg']}") from exc


def validate_steps(steps, roles, operations, *, materials=None, values=None):
    seen = {}
    if not 1 <= len(steps) <= 12:
        raise ValueError("一次办理需要 1–12 个步骤")
    for step in steps:
        spec = operations.get(step.operation)
        if spec is None or step.id in seen:
            raise ValueError(f"步骤 {step.id} 重复或操作 {step.operation} 不存在")
        try:
            kinds = []
            unknown_group = False
            for reference in step.inputs:
                group, _, name = reference.partition(".")
                if group == "material" and name in roles:
                    if materials is None:
                        unknown_group = True
                    else:
                        kinds.extend(Material.model_validate(item).kind for item in materials.get(name, ()))
                elif group == "step" and name in seen:
                    operation = seen[name].operation
                    kind = connection(operation, "step")
                    if kind is None:
                        products = "、".join(CONTRACTS[operation].products)
                        detail = f"{reference} 需要可传递的图层，{operation} 产生 {products}"
                        if connection(operation, "$selection"):
                            detail += f"。图层输入引用原图层，在选区参数中传 $selection.{name}"
                        raise ValueError(detail)
                    kinds.append(kind)
                else:
                    raise ValueError(f"无法引用输入 {reference}，请检查材料名称与步骤顺序")
            if not unknown_group:
                validate_input_kinds(step.operation, spec, kinds)
            if values is not None:
                validate_parameters(step, seen, values, materials, None if unknown_group else len(kinds))
        except ValueError as error:
            raise ValueError(f"步骤 {step.id}（{step.operation}）：{error}") from error
        seen[step.id] = step
