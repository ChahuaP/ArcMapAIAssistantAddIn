"""Discover and publish validated, versioned skill definitions."""
from hashlib import sha256
import json
from pathlib import Path
import re

import yaml

from guizhi.domain.capabilities import CAPABILITIES, Capability, Material
from guizhi.domain.skills import Skill, Step
from guizhi.domain.version import increment
from guizhi.infrastructure.workspace import SOURCE_ROOT, Workspace

SKILLS = SOURCE_ROOT / "resources" / "skills"
BUILT_INS = ("control-check.md", "farmland-balance.md", "shared-delivery.md",
             "cad-coordinates.md", "annual-land.md", "permit-history.md", "imagery-change.md")
OPERATIONS = {**{name: spec for name, spec in CAPABILITIES.items() if name != "workflow.execute"},
              "control_check": Capability("管控线核查", 2, 2), "vector_inventory": Capability("矢量清点")}


class TemplateLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("Skill templates do not accept YAML aliases")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("Skill templates do not accept repeated YAML keys")
        return super().construct_mapping(node, deep=deep)


def validate_steps(steps, roles):
    seen = set()
    if not 1 <= len(steps) <= 12:
        raise ValueError("A task requires 1–12 steps")
    for step in steps:
        spec = OPERATIONS.get(step.operation)
        if spec is None or step.id in seen:
            raise ValueError(f"Unknown operation or repeated step: {step.id}")
        for reference in step.inputs:
            group, separator, name = reference.partition(".")
            if not separator or not ((group == "material" and name in roles) or (group == "step" and name in seen)):
                raise ValueError(f"Step {step.id} references unavailable material: {reference}")
        if not any(reference.startswith("material.") for reference in step.inputs):
            if not spec.minimum <= len(step.inputs) <= spec.maximum:
                raise ValueError(f"Step {step.id} has the wrong number of inputs")
        seen.add(step.id)


def parse_skill(text):
    if len(text.encode("utf-8")) > 65536:
        raise ValueError("Skill file exceeds 64 KB")
    parts = re.split(r"(?m)^---[ \t]*\r?$", text, maxsplit=2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError("Skill requires YAML between the opening --- markers")
    payload = yaml.load(parts[1], Loader=TemplateLoader)
    json.dumps(payload, allow_nan=False)
    skill = Skill.model_validate(payload)
    roles = {item.id for item in skill.materials}
    if len(roles) != len(skill.materials) or any(role.minimum > role.maximum for role in skill.materials):
        raise ValueError("Material role names and quantity ranges must be valid")
    resources = [role for role in skill.materials if role.resource]
    if any(role.kind == "model" and not role.resource for role in skill.materials):
        raise ValueError("请将影像模型设为已安装资源")
    if resources and (len(resources) != 1 or resources[0].kind != "model" or
                      (resources[0].minimum, resources[0].maximum) != (1, 1) or
                      not {"region", "sensor", "from_date", "to_date"} <= skill.parameters.keys() or
                      not {"from", "to"} <= roles or "profile" in skill.parameters):
        raise ValueError("影像模型资源需要两期影像及区域、传感器、日期参数")
    validate_steps(skill.steps, roles)
    steps = {item.id for item in skill.steps}
    if any(item.step not in steps for item in (*skill.rules, *skill.evidence)):
        raise ValueError("Skill rules and evidence must reference a declared step")
    if any(rule.compare != "eq" and type(rule.value) not in {int, float} for rule in skill.rules):
        raise ValueError("Range checks require numeric values")
    return skill


def entry(text, revision, verification=None):
    skill = parse_skill(text)
    return {**skill.model_dump(mode="json"), "revision": revision, "markdown": text,
            "sha256": sha256(text.encode()).hexdigest(), "verification": verification, "release_version": "1.0.0"}


def catalog(workspace, *, include_history=False):
    entries = [entry((SKILLS / name).read_text(encoding="utf-8"), 1) for name in BUILT_INS]
    with workspace.connect() as db:
        entries += [json.loads(row[0]) for row in db.execute("SELECT body FROM skills ORDER BY id, revision")]
    if include_history:
        return entries
    return list({item["id"]: item for item in entries}.values())


def summaries(workspace):
    return [{**{key: item[key] for key in ("id", "title", "aliases", "materials", "revision")},
             "summary": next((line.strip() for line in item["markdown"].split("---", 2)[-1].splitlines()
                              if line.strip() and not line.startswith("#")), "")[:250],
             "outputs": list(dict.fromkeys(ref["kind"] for ref in item["evidence"])),
             "parameters": item.get("parameters", {}), "release_version": release_version(item)} for item in catalog(workspace)]


def release_version(item):
    return item.get("release_version", f"1.{item['revision'] - 1}.0")


def get_skill(workspace, reference):
    identifier, marker, version = reference.partition("@")
    records = catalog(workspace, include_history=bool(marker))
    for item in records:
        if item["id"] == identifier and (not marker or str(item["revision"]) == version):
            return item
    raise ValueError("Skill version not found")


def specification(workspace, materials, *, skill=None, steps=None, candidate=None, coordinate_plans=None,
                  skill_parameters=None):
    selected = candidate or (get_skill(workspace, skill) if skill else None)
    clean = {}
    for name, group in materials.items():
        if not isinstance(group, list):
            raise ValueError(f"Material role {name} requires a list")
        clean[name] = []
        for item in group:
            material = Material.model_validate(item)
            material.file = str(Path(material.file).resolve(strict=True))
            clean[name].append(material.model_dump(mode="json"))
    if sum(map(len, clean.values())) > 32 or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) for name in clean):
        raise ValueError("A task accepts at most 32 materials under valid role names")
    extra = [Step.model_validate(step) for step in steps or []]
    fixed = [Step.model_validate(step) for step in selected["steps"]] if selected else []
    required = selected["materials"] if selected else []
    values = skill_parameters or {}
    declared = selected.get("parameters", {}) if selected else {}
    if not isinstance(values, dict) or values.keys() != declared.keys():
        raise ValueError("Supply exactly the declared skill parameters: " + ", ".join(declared))
    json.dumps(values, allow_nan=False)
    managed = [role for role in required if role.get("resource")]
    for role in required:
        if role.get("resource"):
            if role["id"] in clean:
                raise ValueError("影像模型由已安装的业务资源匹配")
            continue
        size = len(clean.get(role["id"], []))
        if not role["minimum"] <= size <= role["maximum"]:
            raise ValueError(f"Missing or excess materials: {role['label']} ({role['minimum']}–{role['maximum']})")
    if selected and not extra and set(clean) - {role["id"] for role in required}:
        raise ValueError("Materials do not belong to this skill")
    parameter_profile = None
    if selected and selected.get("parameter_profiles"):
        from guizhi.application.improvement import apply_profile

        values, parameter_profile = apply_profile(selected, clean, values)
    if managed:
        from guizhi.infrastructure.imagery_resources import resolve

        if any(len(clean.get(role, [])) != 1 or Material.model_validate(clean[role][0]).kind != "raster"
               for role in ("from", "to")):
            raise ValueError("请选择各一份前期影像和后期影像")
        item, profile = resolve(workspace, [clean[role][0]["file"] for role in ("from", "to")], values)
        clean[managed[0]["id"]] = [item]
        values = {**values, "profile": profile}
    validate_steps(fixed + extra, set(clean) | {role["id"] for role in required})
    return {"mode": "combined" if selected and extra else "fixed" if selected else "free",
            "materials": clean, "skill": selected, "steps": [step.model_dump(mode="json") for step in fixed + extra],
            "coordinate_plans": coordinate_plans or {}, "skill_parameters": values, "parameter_profile": parameter_profile,
            "model_provenance": None}


def execute(root, materials, *, skill=None, steps=None, candidate=None, coordinate_plans=None,
            skill_parameters=None, cancel_requested=None, key=None):
    workspace = Workspace(root, create=True)
    plan = specification(workspace, materials, skill=skill, steps=steps, candidate=candidate,
                         coordinate_plans=coordinate_plans, skill_parameters=skill_parameters)
    return submit(workspace, plan, cancel_requested=cancel_requested, key=key)


def submit(workspace, plan, *, cancel_requested=None, key=None):
    from guizhi.application.tasks import run_operation

    return run_operation(workspace.root, {"operation": "workflow.execute", "parameters": {"specification": plan},
                                          "idempotency_key": key}, cancel_requested)


def trial(root, text, materials, skill_parameters=None, coordinate_plans=None):
    result = execute(root, materials, candidate=entry(text, 0), skill_parameters=skill_parameters,
                     coordinate_plans=coordinate_plans)
    return {"run_id": result["id"], "status": result["status"], "error": result["error"],
            "statistics": (result["result"] or {}).get("statistics", {}),
            "deliveries": [item["step"] for item in (result["result"] or {}).get("evidence", [])]}


def publish(root, text, trial_run):
    from guizhi.infrastructure.cache import verified_run

    workspace = Workspace(root, create=True)
    draft = entry(text, 0)
    result = workspace.read(trial_run)
    specification = (result["task"].get("request") or {}).get("parameters", {}).get("specification", {})
    if (result["task"]["operation"] != "workflow.execute" or specification.get("skill") != draft or
            not verified_run(workspace, trial_run)):
        raise ValueError("请先对当前草稿完成一次有效试办")
    manifest = next(item for item in result["result"]["evidence"] if item["step"] == "workflow")
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT body FROM skills WHERE id=? AND json_extract(body, '$.verification.run_id')=?",
                              (draft["id"], trial_run)).fetchone()
        if existing:
            return json.loads(existing[0])
        first = int(any(parse_skill((SKILLS / name).read_text(encoding="utf-8")).id == draft["id"] for name in BUILT_INS))
        current = db.execute("SELECT coalesce(max(revision), ?) FROM skills WHERE id=?", (first, draft["id"])).fetchone()[0]
        latest = db.execute("SELECT body FROM skills WHERE id=? AND revision=?", (draft["id"], current)).fetchone()
        if current:
            version = release_version(json.loads(latest[0])) if latest else draft["release_version"]
            draft["release_version"] = increment(version, "feature")
        saved = {**draft, "revision": current + 1,
                 "verification": {"run_id": result["id"], "workflow_sha256": manifest["sha256"]}}
        db.execute("INSERT INTO skills VALUES (?, ?, ?)", (saved["id"], saved["revision"], json.dumps(saved)))
    return saved


def propose(workspace, run_id, title):
    from guizhi.infrastructure.operations.evidence import read_artifact

    manifest = json.loads(read_artifact(workspace, run_id, "workflow").read_text(encoding="utf-8"))
    record = workspace.read(run_id)
    plan = record["task"]["request"]["parameters"]["specification"]
    source = plan["skill"] or {}
    roles = {role["id"]: role for role in source.get("materials", [])}
    for name, materials in plan["materials"].items():
        if name not in roles:
            kinds = {Material.model_validate(item).kind for item in materials}
            if len(kinds) > 1:
                raise ValueError("Save a skill with one material type per role")
            roles[name] = {"id": name, "label": name, "kind": next(iter(kinds), "vector"), "geometry": "any", "coordinate": "any",
                           "minimum": len(materials), "maximum": max(1, len(materials))}
    evidence = [{"step": item["id"], "kind": ref["step"]}
                for item in manifest["steps"] for ref in workspace.read(item["run_id"])["result"]["evidence"]
                if ref["step"] in {"export", "map", "report", "table", "view", "selection", "boundaries"}]
    payload = {"id": "task-" + run_id[:12], "title": title, "materials": list(roles.values()),
               "steps": plan["steps"], "parameters": source.get("parameters", {}),
               "rules": source.get("rules", []), "evidence": evidence[:16]}
    markdown = "---\n" + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False) + "---\n"
    parse_skill(markdown)
    return {"source_run": run_id, "markdown": markdown}
