"""Execute fixed, free and combined plans using the same registered job runner."""
import json
from operator import eq, ge, le
import shutil
from pathlib import Path

from pyproj import CRS

from guizhi.application.skills import validate_steps
from guizhi.application.tasks import run_operation, run_vector
from guizhi.domain.capabilities import CAPABILITIES, Material
from guizhi.domain.diagnostics import Fault
from guizhi.domain.operations import OPERATIONS
from guizhi.domain.skills import Step
from guizhi.infrastructure.cache import record_dependencies, reuse_job
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.runner import data_revision


def committed(record):
    if record["status"] != "succeeded":
        raise ValueError(record["error"] or record["status"])
    return record["result"]


def resolve_value(value, records, skill_parameters):
    if isinstance(value, str) and value.startswith("$param."):
        name = value[7:]
        if name not in skill_parameters:
            raise ValueError(f"Missing declared skill parameter: {name}")
        return skill_parameters[name]
    if isinstance(value, str) and value.startswith(("$run.", "$selection.")):
        mode, name = value.split(".", 1)
        record = records.get(name)
        if record is None:
            raise ValueError(f"Unavailable prior step: {name}")
        if mode == "$run":
            return record["id"]
        result = record["result"].get("selection")
        if result is None:
            raise ValueError(f"Step {name} has no selection")
        return result
    if isinstance(value, list):
        return [resolve_value(item, records, skill_parameters) for item in value]
    if isinstance(value, dict):
        return {key: resolve_value(item, records, skill_parameters) for key, item in value.items()}
    return value


def material_from(ctx, record):
    data = committed(record)
    if not any(ref["step"] == "export" for ref in data["evidence"]):
        raise Fault("workflow", "upstream_has_no_export", "Workflow step consumes a result without a data export",
                    upstream_operation=record["task"]["operation"], upstream_run=record["id"])
    file = read_artifact(ctx.workspace, record["id"], "export")
    return {"file": str(file), "layer": data.get("export_layer"),
            "revision": data.get("data_revision") or data_revision(file)}


def execute(ctx, specification: dict):
    mode = specification["mode"]
    skill = specification["skill"]
    groups = specification["materials"]
    steps = [Step.model_validate(raw) for raw in specification["steps"]]
    validate_steps(steps, groups.keys() | {item["id"] for item in skill["materials"]} if skill else groups.keys())
    if mode not in {"fixed", "free", "combined"}:
        raise ValueError("Unknown workflow mode")
    sealed, seals, records = {}, {}, {}
    def cancelled():
        try:
            ctx.check()
        except InterruptedError:
            return True
        return False

    roles = {role["id"]: role for role in skill["materials"]} if skill else {}
    file_number = 0
    for role, group in groups.items():
        sealed[role], seals[role] = [], []
        for item in group:
            ctx.check()
            ctx.progress("material_reading", role=role, layer=item["layer"])
            requirement = roles.get(role)
            kind = Material.model_validate(item).kind
            if requirement and requirement["kind"] != kind:
                raise ValueError(f"Material {role} has the wrong format")
            if kind != "vector":
                file_number += 1
                if kind == "document":
                    file, ref = ctx.seal_document(item["file"], item["revision"], file_number)
                else:
                    file, ref = ctx.seal_file(kind, item["file"], item["revision"], file_number)
                    if kind == "raster" and requirement and requirement["coordinate"] == "projected_meters":
                        from guizhi.infrastructure.operations.raster import metadata

                        crs = CRS(metadata(file)["crs"])
                        if not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]):
                            raise ValueError(f"Material {role} requires a projected coordinate system in metres")
                sealed[role].append({"file": str(file), "layer": None, "revision": ref["sha256"]})
                seals[role].append({"run_id": ctx.job["run_id"], "artifact": ref["step"]})
                ctx.snapshot(ref, None, ref["sha256"], role=role)
                continue
            record, reused = reuse_job(ctx.workspace, "data.describe", [item], {},
                                       lambda: run_operation(ctx.workspace.root, {
                                           "operation": "data.describe", "inputs": [item]}, cancelled), check=ctx.check)
            ctx.progress("material_resolved", role=role, layer=item["layer"], child_run_id=record["id"], status=record["status"], reused=reused)
            ctx.check()
            data = committed(record)
            requirement = roles.get(role, {})
            expected = requirement.get("geometry", "any")
            if expected != "any":
                names = {name.casefold().removeprefix("multi").split(" ")[0]
                         for name in (data["geometry_types"] or [data["geometry_type"] or ""])}
                if {{"linestring": "line"}.get(name, name) for name in names} != {expected}:
                    geometry = {"point": "点", "line": "线", "polygon": "面"}[expected]
                    raise ValueError(f"{requirement['label']}需要{geometry}图层")
            policy = requirement.get("coordinate", "any")
            if policy != "any" and data["crs"] is None:
                raise ValueError(f"Material {role} requires a defined coordinate system")
            if policy == "projected_meters":
                crs = CRS(data["crs"])
                if not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]):
                    raise ValueError(f"Material {role} requires a projected coordinate system in metres")
            file = read_artifact(ctx.workspace, record["id"], "import")
            sealed[role].append({"file": str(file), "layer": item["layer"], "revision": data["revision"]})
            seals[role].append(record["id"])
            ref = next(ref for ref in data["evidence"] if ref["step"] == "import")
            ctx.progress("diagnostic_snapshot", ref=ref, layer=item["layer"], revision=data["revision"],
                         role=role, source_run=record["id"])
    manifest = {"mode": mode, "skill": skill, "material_runs": seals, "steps": [],
                "model_provenance": specification["model_provenance"],
                "parameter_profile": specification.get("parameter_profile")}
    for step in steps:
        ctx.check()
        inputs = []
        for reference in step.inputs:
            kind, name = reference.split(".", 1)
            if kind == "material":
                inputs.extend(sealed.get(name, []))
            else:
                inputs.append(material_from(ctx, records[name]))
        parameters = resolve_value(step.parameters, records, specification.get("skill_parameters", {}))
        layers = {item["layer"] for item in inputs if item["layer"]}
        input_role = step.inputs[0].partition(".")[2] if len(step.inputs) == 1 and step.inputs[0].startswith("material.") else None
        operation = OPERATIONS.get(step.operation) or CAPABILITIES[step.operation]
        ctx.progress("workflow_step_started", step_id=step.id, operation=step.operation,
                     title=operation.title, role=input_role, layer=next(iter(layers)) if len(layers) == 1 else None)
        if step.operation in {"control_check", "vector_inventory"}:
            expected = 2 if step.operation == "control_check" else 1
            if len(inputs) != expected:
                raise ValueError(f"{step.operation} requires {expected} input(s)")
            plans = {**(specification["coordinate_plans"] if expected == 2 else {}), **parameters}
            if set(plans) - {"project_plan", "control_plan"} or (expected == 1 and plans):
                raise ValueError("Unexpected business operation parameters")
            record, reused = reuse_job(ctx.workspace, step.operation, inputs, plans, lambda: run_vector(
                ctx.workspace.root, Path(inputs[0]["file"]), inputs[0]["layer"], None, cancelled,
                operation=step.operation, progress=ctx.progress, control=(Path(inputs[1]["file"]), inputs[1]["layer"])
                if expected == 2 else None, **plans), check=ctx.check)
        else:
            record, reused = reuse_job(ctx.workspace, step.operation, inputs, parameters, lambda: run_operation(
                ctx.workspace.root, {"operation": step.operation, "inputs": inputs, "parameters": parameters,
                                     "idempotency_key": ctx.job["run_id"] + "." + step.id}, cancelled), check=ctx.check)
        ctx.check()
        ctx.progress("workflow_step_finished", step_id=step.id, child_run_id=record["id"], status=record["status"], reused=reused)
        committed(record)
        records[step.id] = record
        manifest["steps"].append({"id": step.id, "operation": step.operation, "run_id": record["id"],
                                  "statistics": record["result"].get("statistics", {}), "reused": reused,
                                  "inputs": step.inputs, "upstream_runs": sorted(record_dependencies(record)),
                                  "execution": record["result"].get("execution") if not reused else None})
    if skill:
        for rule in skill["rules"]:
            statistics = records[rule["step"]]["result"].get("statistics", {})
            name = rule["statistic"]
            compare = {"eq": eq, "ge": ge, "le": le}[rule["compare"]]
            if name not in statistics or not compare(statistics[name], rule["value"]):
                raise ValueError(f"Skill result check failed: {rule['step']}.{name}")
        for requirement in skill["evidence"]:
            read_artifact(ctx.workspace, records[requirement["step"]]["id"], requirement["kind"])
    ctx.progress("workflow_publishing")
    ctx.json("workflow.json", manifest, "workflow")
    last = committed(records[steps[-1].id])
    outputs = {}
    for record in records.values():
        for ref in record["result"]["evidence"]:
            if ref["step"] in {"export", "report", "map", "table", "view", "selection", "case", "package", "boundaries", "raster_preview"}:
                outputs[ref["step"]] = (record["id"], ref["sha256"])
    for kind, (run_id, checksum) in outputs.items():
        src = read_artifact(ctx.workspace, run_id, kind)
        output = ctx.path(kind + src.suffix)
        shutil.copyfile(src, output)
        if ctx.record(kind, output)["sha256"] != checksum:
            raise ValueError("Published workflow artifact failed checksum verification")
    # The original step's claim and its evidence remain available through the run reference.
    return {**{name: last[name] for name in ("export_layer", "data_revision", "selection", "presentation")
               if name in last}, "workflow": manifest, "final_run": records[steps[-1].id]["id"],
            "statistics": {**last.get("statistics", {}), "step_count": len(steps),
                           "reused_steps": sum(item["reused"] for item in manifest["steps"])}}
