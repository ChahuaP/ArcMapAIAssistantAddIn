"""Expose discovery and verified work on the DSH stdio transport."""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from threading import Lock
from time import monotonic, sleep
from typing import Literal
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from mcp.types import ImageContent
from pyproj.exceptions import CRSError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from guizhi.application import skills
from guizhi.application.dispatch import format_operation, remember_plan
from guizhi.application.tasks import run_operation
from guizhi.domain.semantics import Fact
from guizhi.domain.skills import Step
from guizhi.infrastructure.cache import reuse_job, verified_run
from guizhi.infrastructure.crs_options import coordinate_options, describe_crs
from guizhi.infrastructure.materials import bound_layer_metadata
from guizhi.infrastructure.map_workspace import map_material, selection_material
from guizhi.infrastructure.operations.data import AttributeFilter
from guizhi.infrastructure.operations.evidence import read_artifact
# Load the native GIS dependencies before the stdio server starts its tool threads.
from guizhi.infrastructure.operations.runner import capability
from guizhi.infrastructure.operations.semantics import artifact_json, find_fragments, validate_material_facts
from guizhi.infrastructure.operations.workflow import PUBLISHED_OUTPUTS
from guizhi.infrastructure.workspace import Workspace


def register(server, job=None):
    workspace = dispatch = receipt = claim = cancelled = issue = private_path = None
    materials, plans, bound_parameters = {}, {}, None
    if job is not None:
        workspace = Workspace(Path(job["workspace"]))
        dispatch = workspace.root / "dispatch" / job["id"]
        if dispatch.resolve(strict=True) != dispatch:
            raise ValueError("Dispatch must belong to the selected workspace")
        receipt = dispatch / "operation.json"
        # Keep the claim after interruption so the same job cannot execute twice.
        claim = dispatch / "operation.claim"
        cancelled = dispatch / "cancelled"
        issue = dispatch / "input-issue.json"
        materials = job["materials"]
        bound_parameters = job.get("parameters")
        plans = job["plans"]
        local_paths = {str(workspace.root), *(item["file"] for group in materials.values() for item in group)}
        path_forms = {value.replace("\\", "/") for value in local_paths} | {value.replace("/", "\\") for value in local_paths}
        private_path = re.compile("|".join(re.escape(path) for path in sorted(path_forms, key=len, reverse=True)),
                                  re.IGNORECASE)
    mutex = Lock()

    def redact(value):
        if isinstance(value, str):
            return private_path.sub("[本地材料]", value)
        if isinstance(value, dict):
            return {redact(key): redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    previews = {}

    def bounded(value, depth=0):
        if isinstance(value, list):
            if depth >= 4 or len(value) > 20:
                return {"count": len(value), "sample": [bounded(v, depth + 1) for v in value[:3]] if depth < 4 else []}
            return [bounded(v, depth + 1) for v in value]
        if isinstance(value, dict):
            return {k: bounded(v, depth + 1) for k, v in value.items()}
        if isinstance(value, str) and len(value) > 500:
            return value[:500] + "…"
        return value

    def model_result(result):
        summary = {key: result[key] for key in ("operation", "status", "run_id", "mode", "statistics", "error", "failure", "outputs", "artifacts", "stage", "next")
                   if key in result}
        if "steps" in result:
            summary["steps"] = [{key: item[key] for key in ("id", "operation", "statistics", "reused", "inputs", "observations") if key in item}
                                for item in result["steps"]]
        return redact(summary)

    def publish(result):
        pending = receipt.with_suffix(".pending")
        pending.write_text(json.dumps(result), encoding="utf-8")
        pending.replace(receipt)

    def execute(operation, parameters, run):
        interactive = not job["skill"]
        fingerprint = sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
        key = dispatch.name + "." + fingerprint if interactive else dispatch.name
        with mutex:
            if cancelled.exists():
                return {"status": "cancelled"}
            if receipt.exists():
                return {"status": "already_delivered"}
            if fingerprint in previews:
                return model_result(previews[fingerprint])
            if not interactive:
                try:
                    claim.touch(exist_ok=False)
                except FileExistsError:
                    return {"status": "interrupted", "message": "Operation was already started"}
            result = {"operation": operation, "parameters": parameters}
            try:
                record = run(key)
                result = format_operation(workspace, record, parameters)
            except (ValueError, OSError) as exc:
                result.update(status="invalid_materials", error=str(exc).split("\n")[0])
            if interactive:
                if result["status"] == "succeeded":
                    for step in result["steps"]:
                        child = workspace.read(step["run_id"])["result"]
                        # Preserve each operation's own return contract; bulky geometry and FIDs
                        # stay in local evidence. Truncated collections carry their actual length.
                        internal = {"evidence", "statistics", "selection", "datasets", "source_revisions", "data_revision"}
                        step["observations"] = {name: bounded(value) for name, value in child.items()
                                                if name not in internal}
                        step["artifacts"] = [ref["step"] for ref in child["evidence"]]
                    artifacts = [ref for ref in record["result"]["evidence"] if ref["step"] in PUBLISHED_OUTPUTS]
                    result["artifacts"] = [{"output": ref["step"], "file": Path(ref["path"]).name,
                                            "on_delivery": PUBLISHED_OUTPUTS[ref["step"]]}
                                           for ref in artifacts]
                    result["outputs"] = ["answer", *(ref["step"] for ref in artifacts)]
                    result["stage"] = "ready_to_deliver"
                    result["next"] = "作业和列出的文件已在本机完成并验证。调用 deliver_result 提交所需 outputs 后，工作台才会显示对应成果。"
                else:
                    result["next"] = "该作业没有可交付结果。根据错误修正参数、核实数据或向用户询问。"
                previews[fingerprint] = result
                (dispatch / "work-attempted").touch()
            else:
                if result["status"] == "succeeded":
                    remember_plan(workspace, job["planKey"], record)
                publish(result)
            return model_result(result)

    @server.tool
    def read_conversation(query: str = "", offset: int = 0, turn: int | None = None) -> dict:
        """在当前会话的已完成问答中搜索关键词，由近到远分页。指定 turn 可按字符偏移读取该轮全文，用于核实此前的要求。"""
        if offset < 0:
            return {"status": "invalid_query", "message": "偏移量须大于或等于零"}
        file = dispatch / "conversation.json"
        history = json.loads(file.read_text(encoding="utf8")) if file.exists() else []
        if turn is not None:
            if turn < 1 or turn > len(history):
                return {"status": "unknown_turn", "turn_count": len(history)}
            user, assistant = history[turn - 1]
            content = f"用户：{user}\n助手：{assistant}"
            return {"turn": turn, "content": redact(content[offset:offset + 2400]),
                    "next_offset": offset + 2400 if len(content) > offset + 2400 else None}
        matches = [(i, pair) for i, pair in reversed(list(enumerate(history, 1)))
                   if any(query.casefold() in part.casefold() for part in pair)]
        return {"count": len(matches), "turns": [{"turn": i, "user": redact(pair[0][:200]),
                "assistant": redact(pair[1][:250])} for i, pair in matches[offset:offset + 6]],
                "next_offset": offset + 6 if len(matches) > offset + 6 else None}

    @server.tool
    def list_run_results(run_id: str | None = None) -> list[dict] | dict:
        """列出本轮已经执行的作业。按 run_id 读取完整工具回执、统计和可交付成果，可据此继续或提交，无需再次执行。"""
        with mutex:
            if run_id is not None:
                for row in previews.values():
                    if row.get("run_id") == run_id:
                        return model_result(row)
                return {"status": "unknown_run", "message": "请从本轮作业列表选择运行编号"}
            return [{"run_id": row.get("run_id"), "status": row["status"], "outputs": row.get("outputs", []),
                     "steps": [step["operation"] for step in row.get("steps", [])]} for row in previews.values()]

    @server.tool
    def request_clarification(question: str) -> dict:
        """暂停本轮办理并把这个问题呈现给用户。仅当缺少的信息会影响实际办理结果时使用。"""
        with mutex:
            if cancelled.exists():
                return {"status": "cancelled"}
            if receipt.exists():
                return {"status": "already_delivered"}
            if not question.strip():
                return {"status": "invalid_question"}
            publish({"status": "answered", "question": question.strip()})
            return {"status": "awaiting_user"}

    @server.tool
    def deliver_result(run_id: str, outputs: list[str], answer: str) -> dict:
        """结束自由任务。引用本轮已成功作业及其实际 outputs。只回答选 answer；要在地图选中须含 selection；处理、制图、导出须选对应产物。answer 用核实的结果回答用户。"""
        with mutex:
            if cancelled.exists():
                return {"status": "cancelled"}
            if receipt.exists():
                return {"status": "already_delivered"}
            item = next((row for row in previews.values() if row.get("run_id") == run_id and row["status"] == "succeeded"), None)
            if not item or not outputs or not set(outputs) <= set(item["outputs"]) or not answer.strip():
                return {"status": "missing_evidence", "next": "请引用本轮成功回执中列出的实际产物；缺少成果时继续办理。"}
            delivery = {"outputs": outputs, "answer": answer.strip()}
            if "selection" in outputs:
                step = next((row for row in reversed(item["steps"]) if "selection" in row["artifacts"]), None)
                if not step or not step["inputs"] or not step["inputs"][0].startswith("material."):
                    return {"status": "missing_target", "next": "请在实际地图图层上提交选择，然后交付选区。"}
                role = step["inputs"][0][9:]
                target = job.get("mapAliases", {}).get(role, role.removeprefix("selection-"))
                if not any(row["id"] == target for row in current_map().get("layers", [])):
                    return {"status": "missing_target", "next": "该结果的源不在当前地图中。请使用当前地图的图层引用。"}
                delivery.update(layer=target, step=step["id"])
            publish({**item, "delivery": delivery})
            return {"status": "delivered", "run_id": run_id, "outputs": outputs}

    def input_issue(status, message, *, diagnostic=None):
        result = {"status": status, "error": message}
        if diagnostic:
            result["diagnostic"] = diagnostic
        with mutex:
            pending = issue.with_suffix(".pending")
            pending.write_text(json.dumps(result), encoding="utf-8")
            pending.replace(issue)
        return model_result(result)

    @server.tool
    def list_skills() -> list[dict]:
        """列出已发布业务、别名、版本与材料角色。"""
        return skills.summaries(workspace)

    @server.tool
    def read_approval_materials(terms: list[str] | None = None) -> dict:
        """读取绑定的审批文档，返回可引用的 document_run 和原文片段 ID。terms 用于查找文号、条款和原文关键词。"""
        from guizhi.domain.capabilities import material_kind

        documents = [item for group in materials.values() for item in group if material_kind(item["file"]) == "document"]
        if not documents:
            return {"status": "missing_materials", "message": "请添加审批材料"}
        record, reused = reuse_job(workspace, "semantics.read_documents", documents, {}, lambda: run_operation(
            workspace.root, {"operation": "semantics.read_documents", "inputs": documents,
                             "idempotency_key": dispatch.name + ".documents"}, cancelled.exists))
        if record["status"] != "succeeded":
            return input_issue(record["status"], record["error"] or "无法读取绑定的审批材料")
        index = artifact_json(workspace, record["id"], "document_index")
        return {"status": "indexed", "document_run": record["id"], "reused": reused,
                "fragments": find_fragments(index, (terms or [])[:12])}

    def current_map():
        return (job or {}).get("mapContext") or {}

    def current_scene(*, wait=False):
        context = current_map()
        if context.get("scene") or not context.get("sceneFile"):
            return context.get("scene")
        file = Path(context["sceneFile"])
        until = monotonic() + (4 if wait else 0)
        while True:
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                if cancelled.exists() or monotonic() >= until:
                    return None
                sleep(.06)

    resolved_map_inputs = {}

    def map_inputs(roles):
        data = {**materials}
        layers = {layer["id"]: layer for layer in current_map().get("layers", [])}
        for role in roles - data.keys():
            selected = role.startswith("selection-")
            layer = layers.get(role.removeprefix("selection-"))
            if layer is None:
                continue
            if selected and not layer.get("fids"):
                raise ValueError("地图中没有已选要素")
            if role not in resolved_map_inputs:
                resolved_map_inputs[role] = (selection_material(workspace, layer["ref"], layer["revision"], layer["fids"])
                                             if selected else map_material(workspace, layer["ref"]))
            loaded = resolved_map_inputs[role]
            data[role] = [{key: loaded[key] for key in ("file", "layer", "revision")}]
        return data

    @server.tool
    def inspect_current_map() -> dict:
        """读取当前地图图层、可见状态、符号、坐标系、当前视野和选区。要查询或编辑该图层，使用返回的 id 作为材料角色。"""
        context = current_map()
        scene = {key: value for key, value in (current_scene() or {}).items() if key != "image"}
        visible = []
        for layer in context.get("layers", []):
            public = {key: value for key, value in layer.items() if key not in {"ref", "revision", "fids"}}
            if public.get("crsName"):
                public["crs"] = public.pop("crsName")
            if layer.get("fids"):
                public.update(selected_count=len(layer["fids"]), selection_role="selection-" + layer["id"],
                              selection_reference="$map_selection." + layer["id"])
            visible.append(public)
        layout = context.get("layout")
        if layout:
            layer_ids = {layer["ref"]["run"] + ":" + layer["ref"]["artifact"]: layer["id"]
                         for layer in context.get("layers", [])}
            layout = {**layout, "frames": {name: {**frame, "layers": [
                layer_ids.get(item, "已移除图层") for item in frame.get("layers", [])]}
                for name, frame in layout["frames"].items()}}
        return {"layers": visible, "view": scene, "layout": layout}

    @server.tool
    def inspect_layer_rows(role: str, fields: list[str], match_field: str | None = None,
                           match_value: str | int | float | None = None) -> dict:
        """只读抽查实际属性行。fields 指定要一起核对的列，可指定一个字段及精确值筛选。用于理解字段、类型、编码与名称对应关系。返回至多六行样本，不能用于统计总数。"""
        import pyogrio
        from guizhi.infrastructure.operations.table import identifier

        role = role.removeprefix("material.")
        try:
            group = map_inputs({role}).get(role, [])
            if len(group) != 1 or not group[0].get("layer"):
                raise ValueError("请选择一份矢量图层")
            source, name = group[0]["file"], group[0]["layer"]
            info = pyogrio.read_info(source, layer=name)
            types = dict(zip(info["fields"], info["dtypes"], strict=True))
            if not 1 <= len(fields) <= 8 or len(set(fields)) != len(fields) or any(key not in types for key in fields):
                raise ValueError("请从图层字段中选择一至八个不同字段")
            condition = None
            if match_field is not None:
                if match_field not in types:
                    raise ValueError("匹配字段不在图层中")
                if match_value is None:
                    condition = identifier(match_field) + " IS NULL"
                else:
                    literal = "'" + match_value.replace("'", "''") + "'" if isinstance(match_value, str) else str(match_value)
                    condition = identifier(match_field) + " = " + literal
            elif match_value is not None:
                raise ValueError("请提供与值对应的字段")
            rows = pyogrio.read_dataframe(source, layer=name, columns=fields, where=condition,
                                          max_features=7, read_geometry=False)
            sample = json.loads(rows.head(6).to_json(orient="records", date_format="iso", double_precision=15))
            return {"status": "observed", "role": role, "fields": {key: str(types[key]) for key in fields},
                    "sample": sample, "more": len(rows) > 6, "match_field": match_field, "match_value": match_value}
        except (ValueError, OSError, RuntimeError) as exc:
            return {"status": "invalid_query", "message": redact(str(exc)), "next": "核对图层 id 和实际字段后重试"}

    def layer_coordinates(role):
        role = role.removeprefix("material.")
        layers = map_inputs({role}).get(role, [])
        if len(layers) != 1:
            raise ValueError("请选择要查看的图层")
        metadata = bound_layer_metadata({role: layers})[role][0]
        item = next((layer for layer in current_map().get("layers", []) if layer["id"] == role), None)
        return {"role": role, "layer": (item or {}).get("name") or metadata["layer"] or "影像图层",
                "coordinate_system": describe_crs(metadata["crs"]), "kind": metadata["kind"]}

    @server.tool
    def inspect_layer_crs(role: str) -> dict:
        """读取指定图层的实际坐标系、EPSG 编号、地理或投影类型及单位。坐标系问答用它，保持普通问答。"""
        try:
            return layer_coordinates(role)
        except CRSError:
            return {"status": "invalid_coordinates", "message": "图层的坐标系定义无法识别。"}
        except (OSError, ValueError) as error:
            return {"status": "unavailable", "message": redact(str(error))}

    @server.tool
    def list_coordinate_systems(query: str = "") -> list[dict]:
        """查看常用坐标系，或搜索 EPSG 编号、中央经线、坐标系名称。"""
        return coordinate_options(query)

    @server.tool
    def read_current_map_image():
        """读取本轮当前地图的实际地图画面。在视觉检查图形、色彩或版面时使用。精确字段、面积和坐标仍须查询数据。"""
        scene = current_scene(wait=True)
        if not scene:
            return "本轮没有可读的地图画面。可查看图层资料，或请用户打开地图。"
        return ImageContent(type="image", mimeType="image/jpeg", data=scene["image"])

    @server.tool
    def describe_bound_layers() -> dict:
        """查看已绑定材料类型、矢量字段或栅格的坐标系、波段与分辨率。"""
        try:
            result = bound_layer_metadata(materials)
        except (ValueError, OSError) as exc:
            return input_issue("invalid_materials", str(exc))
        return result

    @server.tool
    def list_rule_catalogs(region: str | None = None) -> list[dict]:
        """查看工作区已登记的规则目录、版本和适用事项。"""
        with workspace.connect() as db:
            entries = db.execute("""
                SELECT id FROM runs WHERE status='succeeded'
                AND json_extract(job, '$.operation')='knowledge.register_catalog'
                ORDER BY created_at DESC LIMIT 12
            """).fetchall()
        results = []
        for entry in entries:
            index = artifact_json(workspace, entry["id"], "knowledge_catalog")
            scopes = [{"region": rule["region"], "matter": rule["matter"], "edition": rule["edition"],
                       "project_code": rule["project_code"]} for rule in index["rules"]
                      if not region or rule["region"].casefold() == region.casefold()]
            if scopes:
                results.append({"catalog_run": entry["id"], "revision": index["revision"], "scopes": scopes})
        return results

    @server.tool
    def propose_skill(title: str) -> dict:
        """用户要求以后都这样办理时，从当前已完成任务生成可编辑的技能候选。"""
        if not job["previousRun"]:
            return {"status": "missing_previous_run"}
        try:
            draft = skills.propose(workspace, job["previousRun"], title)
        except (ValueError, OSError, KeyError):
            return {"status": "no_reusable_workflow"}
        candidate = dispatch / "candidate.json"
        with mutex:
            if not candidate.exists():
                candidate.write_text(json.dumps(draft), encoding="utf-8")
        return {"status": "draft_created", "title": title}

    @server.tool
    def list_capabilities(domain: str | None = None, operation: str | None = None) -> dict:
        """按领域列出操作与输出。按确切操作编号查询用途、输入顺序、参数、可引用的输出及地图影响。"""
        if operation:
            if operation not in skills.OPERATIONS:
                return {"error": "操作编号不存在", "matching_ids": [name for name in skills.OPERATIONS
                                                            if operation.lower() in name.lower()],
                        "next": "按领域查询目录可获取准确编号。"}
            try:
                return {operation: capability(operation)}
            except ValueError as exc:
                return {"error": str(exc)}
        from guizhi.domain.operation_catalog import CONTRACTS

        return {name: {"title": spec.title, "input_count": [spec.minimum, spec.maximum], "input_kinds": spec.kinds,
                       "outputs": CONTRACTS[name].products}
                for name, spec in skills.OPERATIONS.items()
                if not domain or name.startswith(domain + ".")}

    def run_plan(mode, selected, extra, bound=None, coordinate_plans=None, skill_parameters=None):
        try:
            roles = {reference.split(".", 1)[1] for step in extra for reference in step["inputs"]
                     if reference.startswith("material.")}
            inputs = map_inputs(roles) if bound is None else bound
            if mode == "free":
                inputs = {role: items for role, items in inputs.items() if role in roles}
            for step in extra:
                for param in ("selection", "previous"):
                    reference = step["parameters"].get(param)
                    if isinstance(reference, str) and reference.startswith("$map_selection."):
                        layer_id = reference.removeprefix("$map_selection.")
                        layer = next((item for item in current_map().get("layers", []) if item["id"] == layer_id), None)
                        if not layer or not layer.get("fids"):
                            raise ValueError(f"步骤 {step['id']}：目标图层没有已选要素")
                        if not step["inputs"] or step["inputs"][0] != "material." + layer_id:
                            raise ValueError(f"步骤 {step['id']}：地图选区与输入图层必须属于同一图层")
                        item = inputs[layer_id][0]
                        if item["revision"] != layer["revision"]:
                            raise ValueError(f"步骤 {step['id']}：图层已变化，请重新选择要素")
                        step["parameters"][param] = {"layer": item["layer"], "revision": layer["revision"],
                                                     "fids": layer["fids"]}
                where = step["parameters"].get("where")
                first = next(iter(step["inputs"]), "")
                if not isinstance(where, dict) or "$" in json.dumps(where) or not first.startswith("material."):
                    continue
                group = inputs.get(first[9:], [])
                if len(group) == 1 and group[0].get("layer"):
                    from guizhi.infrastructure.operations.sql_expression import attribute_predicate

                    metadata = bound_layer_metadata({"input": group})["input"][0]
                    types = {column["name"]: column["type"] for column in metadata.get("fields", [])}
                    attribute_predicate(AttributeFilter.model_validate(where), types)
            plan = skills.specification(workspace, inputs, skill=selected,
                                        steps=extra, coordinate_plans=plans if coordinate_plans is None else coordinate_plans,
                                        skill_parameters=skill_parameters)
            if mode != plan["mode"]:
                raise ValueError("Mode requires the corresponding skill and steps")
        except (ValueError, OSError) as exc:
            message = str(exc)
            if message.startswith("步骤 "):
                return {"status": "invalid_parameters", "message": message}
            status = "missing_materials" if message.startswith("Missing or excess materials:") else "invalid_materials"
            return input_issue(status, message)
        plan["model_provenance"] = job["provenance"]
        return execute("workflow.execute", {"skill": selected, "steps": extra}, lambda key: skills.submit(
            workspace, plan, cancel_requested=cancelled.exists, key=key))

    @server.tool
    def lookup_layer_field(role: str, field: str, match_field: str | None = None,
                           match_value: str | int | float | bool | None = None) -> dict:
        """读取匹配要素的实际属性值。role 使用材料清单或地图返回的 id。field 是所求字段；match_field 和 match_value 定位要素。省略条件时在该引用的完整范围查值。"""
        if job["skill"]:
            return input_issue("invalid_materials", "请使用本轮所选业务")
        role = role.removeprefix("material.")
        if match_field is None and match_value is not None:
            return {"status": "invalid_query", "message": "请提供与筛选值对应的字段"}
        params = {"field": field}
        if match_field:
            params["where"] = {"field": match_field, "op": "is_null" if match_value is None else "eq", "value": match_value}
        return run_plan("free", None, [{"id": "lookup", "operation": "table.lookup", "inputs": ["material." + role],
                                        "parameters": params}])

    @server.tool
    def sum_layer_area(role: str, where: AttributeFilter | None = None, area_field: str | None = None) -> dict:
        """计算匹配要素的面积之和。可指定经过核实、单位为平方米的属性字段；否则按投影图层几何计算。返回匹配数量、有效值数、面积和单位。"""
        if job["skill"]:
            return input_issue("invalid_materials", "请使用本轮所选业务")
        params = {"method": "sum", "area_field": area_field}
        if where is not None:
            params["where"] = where.model_dump(exclude_none=True)
        return run_plan("free", None, [{"id": "area", "operation": "vector.area",
                                        "inputs": ["material." + role.removeprefix("material.")], "parameters": params}])

    @server.tool
    def reproject_layer(role: str, target_crs: str | None = None, reference_role: str | None = None) -> dict:
        """把源图层转换到用户指定的 EPSG，或使用明确参照图层的坐标系。用户没给目标时省略这两个参数。保留源图层。"""
        try:
            source = layer_coordinates(role)
            if not target_crs and reference_role:
                target_crs = layer_coordinates(reference_role)["coordinate_system"].get("code")
            if not target_crs:
                return {"status": "target_required", **source,
                         "message": "请选择确切目标坐标系，或提供参照图层。"}
            existing = source["coordinate_system"]
            if existing["kind"] == "未定义":
                return {"status": "source_crs_required", **source, "message": "请先确认并定义源图层的实际坐标系。"}
            from pyproj import CRS

            target = describe_crs(CRS.from_user_input(target_crs.strip()))
            if not target["code"] or target["kind"] not in {"地理坐标系", "投影坐标系"}:
                return {"status": "target_required", "message": "请确认目标坐标系的 EPSG 编号。"}
            if target["code"] == existing.get("code"):
                return {"status": "same_crs", **source, "message": "图层已采用此坐标系。"}
        except CRSError:
            return {"status": "invalid_coordinates", "message": "未找到所选坐标系。请核对 EPSG 编号或指定参照图层。"}
        except (OSError, ValueError) as error:
            return {"status": "invalid_coordinates", "message": redact(str(error))}
        operation = "raster.reproject" if source["kind"] == "raster" else "crs.reproject"
        parameter = "target_crs" if source["kind"] == "raster" else "definition"
        return run_plan("free", None, [{"id": "reproject", "operation": operation,
                                         "inputs": ["material." + source["role"]],
                                         "parameters": {parameter: target["code"]}}])

    @server.tool
    def define_crs_from_layer(target_role: str, reference_role: str) -> dict:
        """采用参照图层的坐标系定义更新未定义坐标系的目标矢量图层。使用已绑定的材料角色。保留原几何坐标。"""
        target_role = target_role.removeprefix("material.")
        reference_role = reference_role.removeprefix("material.")
        if job["skill"] or target_role == reference_role:
            return input_issue("invalid_materials", "请选择两份已绑定的矢量图层作为目标与参照")
        return run_plan("free", None, [{"id": "define-crs", "operation": "crs.assign_from",
                                       "inputs": ["material." + target_role, "material." + reference_role],
                                       "parameters": {}}])

    @server.tool
    def review_approval(document_run: str, project_code: str, region: str, parcel_field: str,
                        project_field: str, facts: list[Fact], catalog_run: str | None = None,
                        as_of: date | None = None, matter: str | None = None) -> dict:
        """审批材料核查。先读取审批原文和图层字段，再提交原样引文及片段编号。自动计算管控叠加并关联地块结论。"""
        if job["skill"]:
            return input_issue("invalid_materials", "请使用已选技能 " + job["skill"])
        if any(len(materials.get(role, [])) != 1 for role in ("project", "control")):
            return input_issue("missing_materials", "请选择项目范围和管控范围")
        try:
            index = artifact_json(workspace, document_run, "document_index")
            validate_material_facts(index, project_code, facts)
        except (ValueError, OSError, KeyError) as exc:
            return input_issue("invalid_materials", f"{exc}。qualifier 应逐字摘自 quote。")
        if catalog_run and (not as_of or not matter):
            return input_issue("missing_materials", "请选择核查日期和适用事项")
        parameters = {"document_run": document_run, "project_code": project_code, "region": region,
                      "parcel_field": parcel_field, "project_field": project_field,
                      "facts": [item.model_dump(mode="json") for item in facts], "spatial_run": "$run.check",
                      "as_of": as_of.isoformat() if as_of else None}
        steps = [
            {"id": "check", "operation": "control_check", "inputs": ["material.project", "material.control"],
             "parameters": {"output_layer": True}},
            {"id": "approval", "operation": "semantics.approval_case", "inputs": ["material.project"],
             "parameters": parameters},
        ]
        if catalog_run:
            steps.extend([
                {"id": "rules", "operation": "knowledge.find_rules", "parameters": {
                    "catalog_run": catalog_run, "region": region, "matter": matter,
                    "as_of": as_of.isoformat(), "project_code": project_code}},
                {"id": "explanation", "operation": "governance.explain_findings", "parameters": {
                    "case_run": "$run.approval", "rules_run": "$run.rules"}},
            ])
        return run_plan("free", None, steps)

    @server.tool
    def update_approval(catalog_run: str | None = None) -> dict:
        """用已选审批成果继续核查。只替换新绑定的项目或管控图层；目录改版时可指定新目录。"""
        if not job["previousRun"] or not verified_run(workspace, job["previousRun"]):
            return input_issue("invalid_materials", "请选择可追溯的已办审批任务")
        previous = workspace.read(job["previousRun"])
        if previous["task"]["operation"] != "workflow.execute":
            return input_issue("invalid_materials", "请选择已办审批任务")
        plan = previous["task"]["request"]["parameters"]["specification"]
        steps = plan["steps"]
        approvals = [step for step in steps if step["operation"] == "semantics.approval_case"]
        if plan["mode"] != "free" or len(approvals) != 1:
            return input_issue("invalid_materials", "所选任务没有可复用的审批流程")
        if any(not item.get("layer") for group in materials.values() for item in group):
            return input_issue("invalid_materials", "新文档需重新读取原文并提交审批核查")
        if set(materials) - {"project", "control"}:
            return input_issue("invalid_materials", "请选择对应的项目或管控图层")
        if catalog_run:
            rules = [step for step in steps if step["operation"] == "knowledge.find_rules"]
            if len(rules) != 1:
                return input_issue("invalid_materials", "该任务没有可更新的规则口径")
            rules[0]["parameters"]["catalog_run"] = catalog_run
        approvals[0]["parameters"]["prior_case"] = job["previousRun"]
        bound = dict(materials)
        original = previous["result"]["workflow"]["material_runs"]
        for role, group in plan["materials"].items():
            if role in bound:
                continue
            bound[role] = []
            for material, ref in zip(group, original[role], strict=True):
                if isinstance(ref, str):
                    path = read_artifact(workspace, ref, "import")
                    revision = workspace.read(ref)["result"]["revision"]
                else:
                    path = read_artifact(workspace, ref["run_id"], ref["artifact"])
                    revision = material["revision"]
                bound[role].append({**material, "file": str(path), "revision": revision})
        return run_plan("free", None, steps, bound, {**plan["coordinate_plans"], **plans})

    @server.tool
    def execute_workflow(mode: Literal["fixed", "free", "combined"], skill: str | None = None,
                         steps: list[Step] | None = None, skill_parameters: dict | None = None) -> dict:
        """执行一个可核验的作业，返回各步统计、产物和失败原因。fixed=已发布技能；free=步骤；combined=已发布技能和附加步骤。
        steps 是结构化数组。每个步骤有 id、operation、inputs、parameters。
        inputs 为字符串数组，如 ["material.project"] 或 ["step.check"]；parameters 为对象。固定业务可省略 steps。
        已在工作台绑定办理参数时省略 skill_parameters。坐标、年份、分类和阈值必须有依据。
        """
        steps = steps or []
        if len(steps) > 12:
            return {"status": "invalid_parameters", "message": "一次最多提交 12 个步骤"}
        selected = job["skill"]
        if selected and skill and skill not in {selected, selected.partition("@")[0]}:
            return input_issue("invalid_materials", "请使用已选技能 " + selected,
                               diagnostic={"category": "model", "code": "selected_skill_mismatch",
                                           "evidence": {"selected_skill": selected, "proposed_skill": skill}})
        if bound_parameters is not None:
            if skill_parameters and skill_parameters != bound_parameters:
                return input_issue("invalid_materials", "请使用工作台中已经填写的办理参数")
            skill_parameters = bound_parameters
        return run_plan(mode, selected or skill, [step.model_dump(mode="json") for step in steps or []],
                        skill_parameters=skill_parameters)


class DispatchTools(Middleware):
    def __init__(self, binding):
        self.binding = binding
        self.job_id = None
        self.tools = None

    async def on_call_tool(self, context, call_next):
        from fastmcp.exceptions import ToolError

        try:
            job = json.loads(self.binding.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ToolError("当前没有已绑定的办理任务") from exc
        if str(UUID(job["id"])) != job["id"]:
            raise ToolError("办理任务编号无效")
        if Path(job["cancelled"]).exists():
            raise ToolError("本轮办理已取消")
        if self.job_id != job["id"]:
            tools = FastMCP("Guizhi task")
            register(tools, job)
            self.tools, self.job_id = tools, job["id"]
        return await self.tools.call_tool(context.message.name, context.message.arguments or {}, run_middleware=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", type=Path, required=True)
    args = parser.parse_args()
    server = FastMCP("Guizhi", middleware=[DispatchTools(args.binding)])
    register(server)
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
