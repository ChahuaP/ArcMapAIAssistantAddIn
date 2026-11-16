"""Reuse a verified plan on identical bound inputs, with a new workflow receipt."""
from datetime import datetime
from pathlib import Path
import json

from guizhi.application import skills
from guizhi.infrastructure.cache import (READ_OPERATIONS, fingerprint, implementation, input_signature,
                                         read_entry, verified_run, write_entry)
from guizhi.infrastructure.materials import bound_layer_metadata
from guizhi.infrastructure.workspace import Workspace


def format_operation(record, parameters):
    result = {"operation": "workflow.execute", "parameters": parameters,
              "status": record["status"], "run_id": record["id"],
              "local_elapsed_ms": round((datetime.fromisoformat(record["updated_at"]) -
                                         datetime.fromisoformat(record["created_at"])).total_seconds() * 1000)}
    if record["status"] == "succeeded":
        data = record["result"]
        result.update(statistics=data["statistics"], mode=data["workflow"]["mode"], steps=data["workflow"]["steps"])
    elif record["error"]:
        result["error"] = record["error"]
    return result


def plan_fingerprint(workspace, request):
    if request["previous_run"] or request.get("image_inputs") or not any(request["materials"].values()):
        return None, request["materials"]
    inputs = {}
    for role, items in request["materials"].items():
        bound = []
        for item in items:
            path = Path(item["file"]).resolve(strict=True)
            signature = input_signature({**item, "file": str(path)})
            if signature is None:
                return None, request["materials"]
            bound.append({"file": str(path), **signature})
        inputs[role] = bound
    catalog = [(item["id"], item["revision"], item["sha256"]) for item in skills.catalog(workspace)]
    with workspace.connect() as db:
        known = [row[0] for row in db.execute("""SELECT id FROM runs WHERE status='succeeded'
            AND json_extract(job, '$.operation')='knowledge.register_catalog'
            ORDER BY created_at DESC LIMIT 12""")]
    materials = {role: [{name: item[name] for name in ("file", "layer", "revision")} for item in group]
                 for role, group in inputs.items()}
    return fingerprint([request, inputs, catalog, known, implementation("workflow.execute")]), materials


def remember_plan(workspace, key, record):
    if key is None or record["status"] != "succeeded":
        return
    plan = record["task"]["request"]["parameters"]["specification"]
    if not any(role.get("resource") for role in (plan["skill"] or {}).get("materials", [])) and \
            all(item["operation"] in READ_OPERATIONS for item in plan["steps"]):
        write_entry(workspace, "plan", key, {"run_id": record["id"]})


def selected_context(workspace, reference, materials):
    skill = skills.get_skill(workspace, reference)
    layers = layer_context(materials)
    return {"reference": f"{skill['id']}@{skill['revision']}", "title": skill["title"],
            "roles": [{key: role[key] for key in ("id", "label", "kind", "geometry", "coordinate", "minimum", "maximum", "resource")}
                      for role in skill["materials"]],
            "parameters": skill.get("parameters", {}),
            "steps": [{"id": step["id"], "operation": step["operation"]} for step in skill["steps"]],
            "layers": layers}


def layer_context(materials, *, include_fields=False):
    layers = bound_layer_metadata(materials)
    for group in layers.values():
        for item in group:
            if "fields" in item:
                item["field_count"] = len(item["fields"])
                if include_fields:
                    item["fields"] = item["fields"][:120]
                else:
                    del item["fields"]
            if item.get("crs") and len(item["crs"]) > 160:
                item["crs"] = "已定义的自定义坐标系"
    return layers


def replay(root, request_path):
    workspace = Workspace(root, create=True)
    request_path = request_path.resolve(strict=True)
    directory = request_path.parent
    if directory.parent != workspace.root / "dispatch":
        raise ValueError("Dispatch request must belong to the workspace")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    key, materials = plan_fingerprint(workspace, request)
    entry = read_entry(workspace, "plan", key) if key else None
    if not entry or not verified_run(workspace, entry["run_id"]):
        result = {"key": key, "operation": None, "materials": materials}
        if request["skill"]:
            result["selected"] = selected_context(workspace, request["skill"], materials)
        else:
            result["layers"] = layer_context(materials, include_fields=True)
        return result
    previous = workspace.read(entry["run_id"])
    plan = previous["task"]["request"]["parameters"]["specification"]
    if key != plan_fingerprint(workspace, request)[0]:
        raise ValueError("Dispatch materials changed while checking a recorded plan")
    plan["materials"] = materials
    result = skills.submit(workspace, plan, cancel_requested=(directory / "cancelled").exists, key=directory.name)
    remember_plan(workspace, key, result)
    return {"key": key, "operation": format_operation(result, {"plan_run": previous["id"]})}
