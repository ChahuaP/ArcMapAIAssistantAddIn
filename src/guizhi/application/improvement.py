
"""Evaluate constrained proposals against a frozen business suite and publish atomically."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
from time import monotonic

import yaml

from guizhi.application import skills
from guizhi.application.diagnostics import contained, diagnose_run, failure_node, replay, save
from guizhi.domain.improvement import ImprovementRequest, ParameterProposal, SkillQuality, WorkflowProposal
from guizhi.domain.version import increment
from guizhi.infrastructure.cache import fingerprint, input_signature
from guizhi.infrastructure.vector import digest
from guizhi.infrastructure.workspace import Workspace


def case_materials(case, root):
    return {role: [{"file": str(root / item.file), "layer": item.layer} for item in items]
            for role, items in case.materials.items()}


def material_identity(materials):
    identity = {}
    for role, items in materials.items():
        revisions = [input_signature(item) for item in items]
        if any(value is None for value in revisions):
            raise ValueError("A material version could not be fixed")
        if revisions:
            identity[role] = [{"layer": value["layer"], "revision": value["revision"]} for value in revisions]
    return identity


def fix_material_revisions(materials, identity):
    for role, values in identity.items():
        for item, value in zip(materials[role], values, strict=True):
            item["revision"] = value["revision"]


def apply_profile(selected, materials, values):
    for item in reversed(selected.get("parameter_profiles", [])):
        if values == item["before"]:
            actual = material_identity(materials)
            if actual == item["materials"]:
                fix_material_revisions(materials, actual)
                return {**values, **item["changes"]}, item["id"]
    return values, None


def verified_quality(file, expected):
    if file.stat().st_size > 256 << 10:
        raise ValueError("Frozen quality definition exceeds 256 KiB")
    data = file.read_bytes()
    if sha256(data).hexdigest() != expected:
        raise ValueError("Frozen quality definition checksum mismatch")
    pack = SkillQuality.model_validate_json(data)
    files = {}
    for case in pack.cases:
        for group in case.materials.values():
            for item in group:
                if files.setdefault(item.file, item.sha256) != item.sha256:
                    raise ValueError("Conflicting frozen fixture checksums")
    total = 0
    for name, checksum in files.items():
        path = contained(file.parent, name)
        total += path.stat().st_size
        if total > 128 << 20:
            raise ValueError("Frozen skill suite exceeds the 128 MiB material budget")
        if digest(path) != checksum:
            raise ValueError("Frozen fixture checksum mismatch")
    return pack, files


def prepare(workspace, payload):
    request = ImprovementRequest.model_validate(payload)
    file = Path(request.quality)
    if not file.is_absolute():
        raise ValueError("Use an absolute path to frozen quality")
    file = file.resolve(strict=True)
    pack, files = verified_quality(file, request.quality_sha256)
    primary = next(case for case in pack.cases if case.id == pack.primary)
    original = workspace.read(request.run_id)
    plan = (original["task"].get("request") or {}).get("parameters", {}).get("specification")
    if not plan or plan["mode"] != "fixed" or plan["skill"]["id"] != pack.skill:
        raise ValueError("Improvement requires a failed fixed workflow for the chosen skill")
    base = skills.get_skill(workspace, pack.skill)
    if plan["skill"]["sha256"] != base["sha256"] or plan["skill"]["revision"] != base["revision"]:
        raise ValueError("The failed skill revision is no longer active")
    origin = diagnose_run(workspace, request.run_id)
    expected_code = primary.failure_code if pack.kind == "workflow" else "annual_chronology"
    if origin["finding"]["code"] != expected_code:
        raise ValueError("The failure does not match this improvement")
    if replay(workspace, origin)["status"] != "reproduced":
        raise ValueError("The failure could not be reproduced")
    identity = defaultdict(list)
    for event in original["events"]:
        if event["step"] == "diagnostic_snapshot":
            item = event["details"]
            identity[item["role"]].append({name: item[name] for name in ("layer", "revision")})
    if dict(identity) != material_identity(case_materials(primary, file.parent)) or plan["skill_parameters"] != primary.parameters:
        raise ValueError("The frozen primary case does not match the recorded failure inputs")
    key = fingerprint([pack.skill, base["revision"], request.quality_sha256, origin["id"]])
    directory = contained(workspace.root, "improvements", key)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()
    frozen = directory / "frozen"
    frozen.mkdir()
    shutil.copyfile(file, frozen / "suite.json")
    for name in files:
        shutil.copyfile(file.parent / name, frozen / name)
    verified_quality(frozen / "suite.json", request.quality_sha256)
    save(directory / "plan.json", {"base": base, "origin": origin["id"], "impact": origin["impact"],
                                  "quality_sha256": request.quality_sha256})
    if pack.kind == "workflow":
        instruction = "Return JSON with order (list of all existing step IDs). Move the independent failing validation as early as possible. Keep every existing step and dependency."
        fact = {"steps": base["steps"], "failing_step_operation": origin["operation"]}
    else:
        instruction = "Return JSON with changes (only the corrected parameter values). Use the independent survey ledger and correct the order and mappings for the two epochs."
        fact = {"parameters": primary.parameters, "fields": base["parameters"]}
    return {"id": key, "directory": str(directory),
            "prompt": {"instruction": instruction, "finding": origin["finding"]["code"], "basis": pack.basis, **fact}}


def build_candidate(base, pack, primary, path, value):
    candidate = deepcopy(base)
    candidate["revision"] += 1
    candidate["release_version"] = increment(skills.release_version(base), "feature" if pack.kind == "workflow" else "fix")
    if pack.kind == "workflow":
        proposed = WorkflowProposal.model_validate(value).order
        steps = {item["id"]: item for item in base["steps"]}
        if len(proposed) != len(steps) or set(proposed) != steps.keys() or proposed == list(steps):
            raise ValueError("Workflow proposal must reorder every original step exactly once")
        sections = base["markdown"].split("---", 2)
        data = {key: base[key] for key in ("id", "title", "aliases", "materials", "parameters", "rules", "evidence")}
        data["steps"] = [steps[id] for id in proposed]
        markdown = "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---" + sections[2]
        candidate.update(skills.entry(markdown, candidate["revision"]), release_version=candidate["release_version"])
    else:
        changes = ParameterProposal.model_validate(value).changes
        if changes != pack.correction or not changes.keys() <= base["parameters"].keys():
            raise ValueError("Proposed parameters disagree with the frozen independent basis")
        identity = material_identity(case_materials(primary, path))
        profile = {"id": fingerprint([identity, primary.parameters, changes]), "materials": identity,
                   "before": primary.parameters, "changes": changes}
        profiles = candidate.get("parameter_profiles", [])
        if len(profiles) >= 32:
            raise ValueError("The skill reached its parameter profile limit")
        candidate["parameter_profiles"] = [*profiles, profile]
    return candidate


def evaluate(directory, base, candidate, pack):
    evaluations = {}
    deadline = monotonic() + 180
    for case in pack.cases:
        materials = case_materials(case, directory / "frozen")
        fix_material_revisions(materials, material_identity(materials))
        primary = case.id == pack.primary
        grade = {}
        for phase, skill in (("before", base), ("after", candidate)):
            root = directory / "runs" / case.id / phase
            run = skills.execute(root, materials, candidate=skill, skill_parameters=case.parameters,
                                 cancel_requested=lambda: monotonic() >= deadline)
            events = run["events"]
            starts = sum(item["step"] == "workflow_step_started" for item in events)
            elapsed = round((datetime.fromisoformat(run["updated_at"]) -
                             datetime.fromisoformat(run["created_at"])).total_seconds() * 1000)
            record = {"run_id": run["id"], "status": run["status"], "started_steps": starts, "elapsed_ms": elapsed}
            grade[phase] = record
            if case.status == "succeeded" and not (pack.kind == "parameter" and primary and phase == "before"):
                if run["status"] != "succeeded":
                    raise ValueError(f"Frozen case {case.id} failed in {phase}")
                stats = {item["id"]: item["statistics"] for item in run["result"]["workflow"]["steps"]}
                for check in case.assertions:
                    if stats.get(check.step, {}).get(check.statistic) != check.equals:
                        raise ValueError(f"Frozen statistic failed for {case.id} in {phase}")
            else:
                code = case.failure_code or "annual_chronology"
                node = failure_node(Workspace(root), run)
                actual = next((item["details"]["code"] for item in reversed(node["events"])
                               if item["step"] == "diagnostic_failure"), None)
                if run["status"] != "failed" or actual != code:
                    raise ValueError(f"Frozen negative case {case.id} changed outcome in {phase}")
                record["code"] = actual
        if pack.kind == "workflow" and primary and grade["after"]["started_steps"] >= grade["before"]["started_steps"]:
            raise ValueError("Candidate did not reduce work on the verified failure path")
        evaluations[case.id] = grade
    return evaluations


def apply(workspace, id, proposal):
    if len(id) != 64 or any(char not in "0123456789abcdef" for char in id):
        raise ValueError("Invalid improvement ID")
    directory = contained(workspace.root, "improvements", id)
    receipt = contained(directory, "result.json")
    if receipt.is_file():
        return json.loads(receipt.read_text(encoding="utf-8"))
    try:
        contained(directory, "candidate.claim").touch(exist_ok=False)
    except FileExistsError:
        with workspace.connect() as db:
            released = db.execute("SELECT body FROM skills WHERE json_extract(body, '$.verification.improvement') = ?", (id,)).fetchone()
        if released:
            result = json.loads(released[0])["verification"]
            save(receipt, result)
            return result
        raise ValueError("Candidate is already being evaluated or its receipt is unknown") from None
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    base = plan["base"]
    try:
        pack, _ = verified_quality(directory / "frozen" / "suite.json", plan["quality_sha256"])
        primary = next(item for item in pack.cases if item.id == pack.primary)
        candidate = build_candidate(base, pack, primary, directory / "frozen", proposal)
        evaluations = evaluate(directory, base, candidate, pack)
        verified_quality(directory / "frozen" / "suite.json", plan["quality_sha256"])
        delta = ({"before": [step["id"] for step in base["steps"]], "after": proposal["order"]}
                 if pack.kind == "workflow" else {"changes": proposal["changes"]})
        result = {"status": "published", "kind": pack.kind, "skill": base["id"], "revision": candidate["revision"],
                  "version": candidate["release_version"], "origin": plan["origin"], "quality_sha256": plan["quality_sha256"],
                  "improvement": id, "impact": plan["impact"], "delta": delta, "evaluation": evaluations}
        model = contained(directory, "model.json")
        if model.is_file():
            result["model"] = json.loads(model.read_text(encoding="utf-8"))
        candidate["verification"] = result
        with workspace.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT max(revision) FROM skills WHERE id=?", (base["id"],)).fetchone()[0]
            if (revision if revision is not None else 1) != base["revision"]:
                raise ValueError("The active skill changed while testing the candidate")
            db.execute("INSERT INTO skills VALUES (?, ?, ?)", (base["id"], candidate["revision"], json.dumps(candidate)))
    except (OSError, ValueError) as exc:
        result = {"status": "rejected", "error": str(exc), "origin": plan["origin"]}
    save(receipt, result)
    return result
