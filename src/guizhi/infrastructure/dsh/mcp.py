"""Expose discovery and verified work on the DSH stdio transport."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys
from threading import Lock
from typing import Literal
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from guizhi.application import skills
from guizhi.application.dispatch import format_operation, remember_plan
from guizhi.application.tasks import run_operation
from guizhi.domain.semantics import Fact
from guizhi.domain.skills import Step
from guizhi.infrastructure.cache import reuse_job, verified_run
from guizhi.infrastructure.materials import bound_layer_metadata
from guizhi.infrastructure.operations.evidence import read_artifact
# Load the native GIS dependencies before the stdio server starts its tool threads.
from guizhi.infrastructure.operations.runner import capability
from guizhi.infrastructure.operations.semantics import artifact_json, find_fragments, validate_material_facts
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

    def model_result(result):
        summary = {key: result[key] for key in ("operation", "status", "run_id", "mode", "statistics", "error")
                   if key in result}
        if "steps" in result:
            summary["steps"] = [{key: item[key] for key in ("id", "operation", "statistics", "reused")}
                                for item in result["steps"]]
        return redact(summary)

    def execute(operation, parameters, run):
        with mutex:
            if receipt.exists():
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                return model_result(saved) if saved["operation"] == operation and saved["parameters"] == parameters else {
                    "status": "operation_already_selected"}
            if cancelled.exists():
                return {"status": "cancelled"}
            try:
                claim.touch(exist_ok=False)
            except FileExistsError:
                return {"status": "interrupted", "message": "Operation was already started"}
            result = {"operation": operation, "parameters": parameters}
            try:
                record = run()
                result = format_operation(record, parameters)
                remember_plan(workspace, job["planKey"], record)
            except (ValueError, OSError) as exc:
                message = str(exc)
                status = "missing_materials" if message.startswith("Missing or excess materials:") else "invalid_materials"
                result.update(status=status, error=message.split("\n")[0])
            pending = receipt.with_suffix(".pending")
            pending.write_text(json.dumps(result), encoding="utf-8")
            pending.replace(receipt)
            return model_result(result)

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
        (dispatch / "discovery").touch()
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
        (dispatch / "discovery").touch()
        index = artifact_json(workspace, record["id"], "document_index")
        return {"status": "indexed", "document_run": record["id"], "reused": reused,
                "fragments": find_fragments(index, (terms or [])[:12])}

    @server.tool
    def describe_bound_layers() -> dict:
        """查看已绑定材料类型、矢量字段或栅格的坐标系、波段与分辨率。"""
        try:
            result = bound_layer_metadata(materials)
        except (ValueError, OSError) as exc:
            return input_issue("invalid_materials", str(exc))
        (dispatch / "discovery").touch()
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
        (dispatch / "discovery").touch()
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
        (dispatch / "discovery").touch()
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
        """列出系统操作；domain 按领域筛选。填写确切 operation ID 可查看参数合同。"""
        (dispatch / "discovery").touch()
        if operation:
            if operation not in skills.OPERATIONS:
                return {"error": "Unknown operation"}
            try:
                return {operation: capability(operation)}
            except ValueError as exc:
                return {"error": str(exc)}
        return {name: [spec.title, spec.minimum, spec.maximum] for name, spec in skills.OPERATIONS.items()
                if not domain or name.startswith(domain + ".")}

    def run_plan(mode, selected, extra, bound=None, coordinate_plans=None, skill_parameters=None):
        try:
            plan = skills.specification(workspace, materials if bound is None else bound, skill=selected,
                                        steps=extra, coordinate_plans=plans if coordinate_plans is None else coordinate_plans,
                                        skill_parameters=skill_parameters)
            if mode != plan["mode"]:
                raise ValueError("Mode requires the corresponding skill and steps")
        except (ValueError, OSError) as exc:
            message = str(exc)
            status = "missing_materials" if message.startswith("Missing or excess materials:") else "invalid_materials"
            return input_issue(status, message)
        plan["model_provenance"] = job["provenance"]
        return execute("workflow.execute", {"skill": selected, "steps": extra}, lambda: skills.submit(
            workspace, plan, cancel_requested=cancelled.exists, key=dispatch.name))

    @server.tool
    def lookup_layer_field(role: str, field: str, match_field: str | None = None,
                           match_value: str | int | float | bool | None = None) -> dict:
        """读取已绑定矢量图层中匹配要素的实际字段值。role 是材料角色，例如 layer-1。field 是要读的字段，match_field 和 match_value 是筛选字段和值。"""
        if job["skill"] or len(materials.get(role, ())) != 1:
            return input_issue("invalid_materials", "请选择一份已绑定的矢量图层并填写其材料角色")
        if match_field is None and match_value is not None:
            return input_issue("invalid_materials", "请指定匹配值所在的字段")
        params = {"field": field}
        if match_field:
            params["where"] = {"field": match_field, "op": "is_null" if match_value is None else "eq", "value": match_value}
        return run_plan("free", None, [{"id": "lookup", "operation": "table.lookup", "inputs": ["material." + role],
                                        "parameters": params}])

    @server.tool
    def define_crs_from_layer(target_role: str, reference_role: str) -> dict:
        """采用参照图层的坐标系定义更新未定义坐标系的目标矢量图层。使用已绑定的材料角色。保留原几何坐标。"""
        if (job["skill"] or target_role == reference_role or
                any(len(materials.get(role, ())) != 1 for role in (target_role, reference_role))):
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
        """整项任务一次提交。fixed=已发布技能；free=步骤；combined=已发布技能和附加步骤。
        步骤 inputs 必须是字符串数组，如 ["material.project"]、["step.check"]，parameters 为对象。
        已绑定办理参数时省略 skill_parameters；否则只填写技能声明的参数。坐标、年份、分类和阈值必须有依据。
        """
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
