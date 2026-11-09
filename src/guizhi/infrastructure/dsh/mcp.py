"""Expose discovery and verified work on the DSH stdio transport."""
import argparse
import json
from pathlib import Path
import sys
from threading import Lock
from typing import Literal

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from guizhi.application import skills
from guizhi.domain.skills import Step
# Load the native GIS dependencies before the stdio server starts its tool threads.
from guizhi.infrastructure.operations.runner import capability
from guizhi.infrastructure.workspace import Workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--dispatch", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--layer")
    parser.add_argument("--control", type=Path)
    parser.add_argument("--control-layer")
    parser.add_argument("--project-plan")
    parser.add_argument("--control-plan")
    parser.add_argument("--skill")
    parser.add_argument("--previous-run")
    parser.add_argument("--materials", type=json.loads, default={})
    args = parser.parse_args()
    workspace = Workspace(args.workspace, create=True)
    dispatch = args.dispatch.resolve(strict=True)
    if dispatch.parent != workspace.root / "dispatch":
        raise ValueError("Dispatch must belong to the selected workspace")

    server = FastMCP("Guizhi")
    mutex = Lock()
    receipt = dispatch / "operation.json"
    # Keep the claim after an interrupted call so rediscovery cannot repeat a side effect.
    claim = dispatch / "operation.claim"
    cancelled = dispatch / "cancelled"
    issue = dispatch / "input-issue.json"
    materials = dict(args.materials)
    if args.input and args.layer:
        materials["project"] = [{"file": str(args.input), "layer": args.layer}]
    if args.control and args.control_layer:
        materials["control"] = [{"file": str(args.control), "layer": args.control_layer}]
    plans = {key: value for key, value in {"project_plan": args.project_plan,
                                         "control_plan": args.control_plan}.items() if value}

    def execute(operation, parameters, run):
        with mutex:
            if receipt.exists():
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                return saved if saved["operation"] == operation and saved["parameters"] == parameters else {
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
                result.update(status=record["status"], run_id=record["id"])
                if record["status"] == "succeeded":
                    data = record["result"]
                    result["statistics"] = data["statistics"]
                    if "workflow" in data:
                        result["mode"] = data["workflow"]["mode"]
                        result["steps"] = data["workflow"]["steps"]
                elif record["error"]:
                    result["error"] = record["error"]
            except (ValueError, OSError) as exc:
                message = str(exc)
                status = "missing_materials" if message.startswith("Missing or excess materials:") else "invalid_materials"
                result.update(status=status, error=message.split("\n")[0])
            pending = receipt.with_suffix(".pending")
            pending.write_text(json.dumps(result), encoding="utf-8")
            pending.replace(receipt)
            return result

    def input_issue(status, message):
        result = {"status": status, "error": message}
        with mutex:
            pending = issue.with_suffix(".pending")
            pending.write_text(json.dumps(result), encoding="utf-8")
            pending.replace(issue)
        return result

    @server.tool
    def list_skills() -> list[dict]:
        """列出已发布业务、别名、版本与材料角色。"""
        (dispatch / "discovery").touch()
        return skills.summaries(workspace)

    @server.tool
    def propose_skill(title: str) -> dict:
        """用户要求以后都这样办理时，从当前已完成任务生成可编辑的技能候选。"""
        (dispatch / "discovery").touch()
        if not args.previous_run:
            return {"status": "missing_previous_run"}
        try:
            draft = skills.propose(workspace, args.previous_run, title)
        except (ValueError, OSError, KeyError):
            return {"status": "no_reusable_workflow"}
        candidate = dispatch / "candidate.json"
        with mutex:
            if not candidate.exists():
                candidate.write_text(json.dumps(draft), encoding="utf-8")
        return {"status": "draft_created", "title": title}

    @server.tool
    def list_capabilities(domain: str | None = None, operation: str | None = None) -> dict:
        """列出规智操作；domain 按领域筛选。填写确切 operation ID 可查看参数合同。"""
        (dispatch / "discovery").touch()
        if operation:
            if operation not in skills.OPERATIONS:
                return {"error": "Unknown operation"}
            return {operation: capability(operation)}
        return {name: [spec.title, spec.minimum, spec.maximum] for name, spec in skills.OPERATIONS.items()
                if not domain or name.startswith(domain + ".")}

    @server.tool
    def execute_workflow(mode: Literal["fixed", "free", "combined"], skill: str | None = None,
                         steps: list[Step] | None = None) -> dict:
        """整项任务一次提交。fixed=已发布技能；free=步骤；combined=已发布技能和附加步骤。
        步骤 inputs 必须是字符串数组，如 ["material.project"]、["step.check"]，parameters 为对象。
        """
        if args.skill and skill and skill not in {args.skill, args.skill.partition("@")[0]}:
            return input_issue("invalid_materials", "请使用已选技能 " + args.skill)
        selected = args.skill or skill
        extra = [step.model_dump(mode="json") for step in steps or []]
        try:
            plan = skills.specification(workspace, materials, skill=selected, steps=extra, coordinate_plans=plans)
            if mode != plan["mode"]:
                raise ValueError("Mode requires the corresponding skill and steps")
        except (ValueError, OSError) as exc:
            message = str(exc)
            status = "missing_materials" if message.startswith("Missing or excess materials:") else "invalid_materials"
            return input_issue(status, message)
        return execute("workflow.execute", {"skill": selected, "steps": extra}, lambda: skills.submit(
            workspace, plan, cancel_requested=cancelled.exists, key=dispatch.name))

    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
