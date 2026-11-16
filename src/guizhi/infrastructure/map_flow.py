"""Compile map models to the existing GIS workflow and preserve node provenance."""
from graphlib import CycleError, TopologicalSorter
import json
from pathlib import Path
import re
from shutil import copyfileobj
from uuid import uuid4

from pydantic import Field

from guizhi.application.skills import execute
from guizhi.domain.capabilities import CAPABILITIES
from guizhi.domain.skills import Slug, Strict, Title
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.runner import handler, parameter_model


# Registered Python operations supply validation and execution. This list declares their canvas ports.
TOOLS = {
    "data.select_by_attribute": (["图层"], "selection"),
    "data.select_by_location": (["待筛选图层", "参照图层"], "selection"),
    "table.calculate_geometry": (["图层"], "layer"),
    "table.calculate_field": (["图层"], "layer"),
    "vector.buffer": (["图层"], "layer"),
    "vector.dissolve": (["图层"], "layer"),
    "vector.intersect": (["输入图层", "叠加图层"], "layer"),
    "vector.clip": (["输入图层", "裁切范围"], "layer"),
    "vector.erase": (["输入图层", "擦除范围"], "layer"),
    "vector.union": (["输入图层", "叠加图层"], "layer"),
    "vector.identity": (["输入图层", "叠加图层"], "layer"),
    "vector.update_overlay": (["输入图层", "更新图层"], "layer"),
    "vector.symmetric_difference": (["输入图层", "叠加图层"], "layer"),
    "vector.spatial_join": (["目标图层", "连接图层"], "layer"),
    "vector.area": (["面图层"], "statistics"),
    "data.describe": (["图层"], "statistics"),
    "data.copy": (["图层"], "layer"),
    "data.merge": (["输入图层", "合并图层"], "layer"),
    "table.add_field": (["图层"], "layer"),
    "table.delete_field": (["图层"], "layer"),
    "geometry.create_dataset": ([], "layer"),
    "geometry.repair": (["图层"], "layer"),
    "raster.statistics": (["影像"], "statistics"),
    "raster.clip": (["影像", "裁切范围"], "layer"),
    "raster.reproject": (["影像"], "layer"),
    "raster.mosaic": (["影像", "拼接影像"], "layer"),
    "presentation.export_table": (["图层"], "table"),
    "flow.unique_values": (["图层"], "values"),
    "flow.expression": ([], "expression"),
    "flow.calculate": ([], "number"),
}

# These ports are optional when the node has a literal value in its editor.
VALUE_PORTS = {
    "data.select_by_attribute": [("SQL", "expression", "sql"), ("唯一值", "values", "values")],
    "table.calculate_field": [("公式", "expression", "expression"), ("数值", "number", "value")],
    "vector.buffer": [("距离", "number", "distance")],
    "flow.calculate": [("左值", "number", "left"), ("右值", "number", "right")],
}


def input_types(operation):
    return ["layer"] * len(TOOLS[operation][0]) + [kind for _, kind, _ in VALUE_PORTS.get(operation, ())]


class Node(Strict):
    id: Slug
    operation: str = Field(max_length=80)
    label: Title | None = None
    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)
    parameters: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)


class Connection(Strict):
    source: Slug
    target: Slug
    port: int = Field(ge=0, le=31)


class Model(Strict):
    nodes: list[Node] = Field(max_length=16)
    connections: list[Connection] = Field(max_length=32)


def catalog():
    return [{"operation": name, "inputs": inputs, "valuePorts": [label for label, _, _ in VALUE_PORTS.get(name, ())],
             "inputTypes": input_types(name), "output": output, "title": CAPABILITIES[name].title}
            for name, (inputs, output) in TOOLS.items()]


def inspect_model(raw):
    model = Model.model_validate(raw)
    nodes = {node.id: node for node in model.nodes}
    if len(nodes) != len(model.nodes) or any(node.operation not in {"input", *TOOLS} for node in nodes.values()):
        raise ValueError("流程节点类型或编号无效")
    inputs = {node.id: {} for node in model.nodes}
    for edge in model.connections:
        target, source = nodes.get(edge.target), nodes.get(edge.source)
        types = input_types(target.operation) if target and target.operation in TOOLS else []
        kind = "layer" if source and source.operation == "input" else TOOLS[source.operation][1] if source else None
        if kind == "selection":
            kind = "layer"
        if (edge.port >= len(types) or types[edge.port] != kind or edge.port in inputs[edge.target]):
            raise ValueError("流程连接不匹配或同一个输入重复连接")
        inputs[edge.target][edge.port] = edge.source
    try:
        order = tuple(TopologicalSorter({name: ports.values() for name, ports in inputs.items()}).static_order())
    except CycleError as error:
        raise ValueError("流程中存在循环，请调整连接") from error
    return model, nodes, inputs, order


def compile_model(workspace, raw, bindings, target=None):
    from guizhi.infrastructure.map_workspace import map_material

    _, nodes, inputs, order = inspect_model(raw)
    if target is not None and (target not in nodes or nodes[target].operation == "input"):
        raise ValueError("请选择要运行到的工具节点")
    wanted = set(order) if target is None else {target}
    if target is not None:
        for name in reversed(order):
            if name in wanted:
                wanted.update(inputs[name].values())
    groups, steps, ports, records = {}, [], {}, {}
    for name in order:
        if name not in wanted:
            continue
        node = nodes[name]
        step_id = "n" + str(len(steps) + 1)
        if node.operation == "input":
            # Unconnected layer placeholders are useful in a template.
            if not any(name in inputs[other].values() for other in wanted):
                continue
            if name not in bindings:
                raise ValueError(f"请为「{node.label or '输入图层'}」选择图层")
            groups[name] = [map_material(workspace, bindings[name])]
            ports[name] = "material." + name
            continue
        slot_names, result_type = TOOLS[node.operation]
        missing = "、".join(title for index, title in enumerate(slot_names) if index not in inputs[name])
        if missing:
            raise ValueError(f"「{node.label or CAPABILITIES[node.operation].title}」需要连接{missing}")
        predecessors = [ports[inputs[name][index]] for index in range(len(slot_names))]
        parameters = bind_values(node, inputs[name], ports)
        steps.append({"id": step_id, "operation": node.operation, "inputs": predecessors, "parameters": parameters})
        record = {"step": step_id, "node": name, "operation": node.operation, "label": node.label or CAPABILITIES[node.operation].title}
        records[step_id] = record
        port = step_id
        if result_type == "selection":
            port = "pick" + step_id
            steps.append({"id": port, "operation": "data.export_selection", "inputs": predecessors[:1],
                          "parameters": {"selection": "$selection." + step_id}})
            record["layerStep"] = port
        ports[name] = "step." + port
    if not 1 <= len(steps) <= 12:
        raise ValueError("每次可运行 1 至 12 个处理步骤，筛选结果生成图层也计一步")
    return groups, steps, records


def bind_values(node, inputs, ports):
    value_ports = VALUE_PORTS.get(node.operation, ())
    offset = len(TOOLS[node.operation][0])
    connected = {param: "$value." + ports[inputs[index]].split(".", 1)[1]
                 for index, (_, _, param) in enumerate(value_ports, offset) if index in inputs}
    parameters = dict(node.parameters)
    if len(connected) > 1 and node.operation != "flow.calculate":
        raise ValueError("一个工具节点只能连接一种计算来源")
    if node.operation == "data.select_by_attribute" and connected:
        if "sql" in connected:
            parameters = connected
        else:
            field = parameters.get("where", {}).get("field")
            if not isinstance(field, str) or not field:
                raise ValueError("请选择要与唯一值匹配的字段")
            parameters = {"where": {"field": field, "op": "in", **connected}}
    elif node.operation == "table.calculate_field" and connected:
        if not parameters.get("field"):
            raise ValueError("请选择写入字段")
        parameters = {"field": parameters["field"], **connected}
    else:
        parameters.update(connected)
    if connected:
        # The registered operation validates the complete parameters after the upstream outputs resolve.
        return parameters
    try:
        return parameter_model(handler(node.operation)).model_validate(parameters).model_dump(mode="json")
    except ValueError as error:
        raise ValueError(f"「{node.label or CAPABILITIES[node.operation].title}」的参数未填完整或类型有误") from error


def job_key(payload):
    token = payload.get("token", "")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("流程任务编号无效")
    return "flow-" + token


def progress(workspace, payload):
    with workspace.connect() as db:
        event = db.execute("""SELECT step, details FROM events
            WHERE run_id = (SELECT run_id FROM operation_requests WHERE key = ?)
            AND step IN ('material_reading', 'workflow_step_started') ORDER BY seq DESC LIMIT 1""",
                           (job_key(payload),)).fetchone()
    if not event:
        return None
    if event["step"] == "material_reading":
        return "正在读取图层…"
    return "正在运行「" + json.loads(event["details"])["title"] + "」…"


def run(workspace, payload):
    from guizhi.infrastructure.map_workspace import output_layers

    groups, steps, records = compile_model(workspace, payload["model"], payload.get("bindings", {}), payload.get("target"))
    key = job_key(payload)
    cancel = workspace.root / (key + ".cancel")
    receipt = execute(workspace.root, groups, steps=steps, cancel_requested=cancel.exists, key=key)
    if receipt["status"] != "succeeded":
        with workspace.connect() as db:
            event = db.execute("SELECT details FROM events WHERE run_id=? AND step='workflow_step_started' "
                               "ORDER BY seq DESC LIMIT 1", (receipt["id"],)).fetchone()
        step = json.loads(event["details"]).get("step_id") if event else None
        entry = records.get(step) or next((item for item in records.values() if item.get("layerStep") == step), {})
        return {"status": receipt["status"], "message": receipt["error"], "failedNode": entry.get("node")}
    manifest = {item["id"]: item for item in receipt["result"]["workflow"]["steps"]}
    results = []
    for step_id, item in records.items():
        step = manifest[step_id]
        child = workspace.read(manifest[item.get("layerStep", step_id)]["run_id"])
        original = child["result"] if "layerStep" not in item else workspace.read(step["run_id"])["result"]
        results.append({**item, "run": child["id"], "statistics": step["statistics"],
                        **{key: original[key] for key in ("value", "selection") if key in original},
                        "layers": output_layers(workspace, child, item["label"]),
                        "table": any(ref["step"] == "table" for ref in child["result"]["evidence"])})
    return {"status": "succeeded", "run": receipt["id"], "steps": results}


def template(workspace, payload):
    action = payload["action"]
    with workspace.connect() as db:
        if action == "flow-templates":
            return [json.loads(row["body"]) for row in db.execute("SELECT body FROM map_flows ORDER BY title, id")]
        key = payload.get("id") if action == "flow-delete" else payload.get("id") or uuid4().hex
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("流程模板编号无效")
        if action == "flow-delete":
            if db.execute("DELETE FROM map_flows WHERE id=?", (key,)).rowcount != 1:
                raise ValueError("流程模板已删除")
            return {"id": key}
        title = str(payload.get("title", "")).strip()
        model, *_ = inspect_model(payload["model"])
        if not 1 <= len(title) <= 100 or not model.nodes:
            raise ValueError("请填写流程名称并添加节点")
        body = {"id": key, "title": title, "model": model.model_dump(mode="json")}
        content = json.dumps(body, ensure_ascii=False, allow_nan=False)
        if len(content.encode()) > 65536:
            raise ValueError("流程模板超出 64 KB")
        db.execute("INSERT INTO map_flows VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body",
                   (key, title, content))
        return body


def export_table(workspace, payload):
    parent = workspace.read(payload["flow"])
    child = payload["run"]
    if parent["status"] != "succeeded" or child not in {item["run_id"] for item in parent["result"].get("workflow", {}).get("steps", [])}:
        raise ValueError("流程没有此属性表成果")
    output = Path(payload["output"])
    if output.suffix.lower() != ".csv":
        raise ValueError("请选择 CSV 文件")
    with read_artifact(workspace, child, "table").open("rb") as source, output.open("xb") as destination:
        copyfileobj(source, destination)
    return {"file": str(output)}
