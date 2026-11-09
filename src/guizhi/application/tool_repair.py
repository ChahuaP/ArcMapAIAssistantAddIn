
"""Verify and publish one bounded class of tool patches in a separate release registry."""
import ast
from difflib import unified_diff
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Annotated
from zipfile import BadZipFile

from pydantic import StringConstraints

from guizhi.application.diagnostics import contained, diagnose_run, replay, save
from guizhi.application.tasks import product_version
from guizhi.domain.capabilities import CAPABILITIES, Hash
from guizhi.domain.platform import RunId
from guizhi.domain.skills import Strict
from guizhi.domain.version import increment
from guizhi.infrastructure import releases
from guizhi.infrastructure.cache import fingerprint
from guizhi.infrastructure.runtime_identity import source_fingerprint
from guizhi.infrastructure.vector import digest
from guizhi.infrastructure.workspace import SOURCE_ROOT

TOOL = "data.select_by_attribute"
RELATED = (TOOL, "table.update_rows", "table.delete_rows")
TARGET = "src/guizhi/infrastructure/operations/common.py"
CAPABILITY_FILE = "src/guizhi/domain/capabilities.py"
REQUIRED_CHECKS = ("passed", "original", "related", "reproduction")


class ToolRequest(Strict):
    run_id: RunId
    quality: str
    quality_sha256: Hash
    release_root: str
    expected_count: int
    oracle: Annotated[str, StringConstraints(min_length=1, max_length=120)]


class ToolProposal(Strict):
    replacement: Annotated[str, StringConstraints(min_length=1, max_length=400)]


def absolute_file(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("Use an absolute file path without redirects")
    return path


def expression(source):
    module = ast.parse(source)
    handler, = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "condition_mask"]
    calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "fullmatch"]
    if len(calls) != 1 or any(key.arg == "flags" for key in calls[0].keywords):
        raise ValueError("This tool revision is outside the supported repair policy")
    return calls[0]


def patch_expression(source, replacement):
    before = expression(source)
    try:
        after = ast.parse(replacement, mode="eval").body
    except SyntaxError:
        raise ValueError("Candidate must supply one Python expression") from None
    if not isinstance(after, ast.Call):
        raise ValueError("Candidate must preserve the existing regex call")
    flags = [item for item in after.keywords if item.arg == "flags"]
    if len(flags) != 1 or ast.unparse(flags[0].value) not in {"re.DOTALL", "re.ASCII", "re.IGNORECASE"}:
        raise ValueError("Candidate must add a permitted regex flag")
    after.keywords = [item for item in after.keywords if item.arg != "flags"]
    if ast.dump(after) != ast.dump(before):
        raise ValueError("Candidate may only add the flag to the existing call")
    lines = source.encode("utf-8").splitlines(keepends=True)
    index = before.lineno - 1
    if before.lineno != before.end_lineno or "\n" in replacement or "\r" in replacement:
        raise ValueError("Candidate must change one expression line")
    lines[index] = (lines[index][:before.col_offset] + replacement.encode("utf-8") +
                    lines[index][before.end_col_offset:])
    result = b"".join(lines).decode("utf-8")
    ast.parse(result)
    return result


def seed(root, archive, checksum):
    root = releases.registry_root(root)
    path = absolute_file(archive)
    staged = releases.stage(root)
    source_zip = staged / "source.zip"
    shutil.copyfile(path, source_zip)
    if digest(source_zip) != checksum:
        raise ValueError("Baseline source archive checksum mismatch")
    source = staged / "source"
    releases.extract(source_zip, source, source=True)
    if source_fingerprint(source) != source_fingerprint(SOURCE_ROOT):
        raise ValueError("Baseline archive does not match the running source")
    version = product_version()
    save(staged / "release.json", {"version": version, "package_sha256": checksum,
                                   "files": releases.manifest(source, source=True), "kind": "baseline"})
    return releases.publish(root, staged, version, None)


def prepare(workspace, value):
    request = ToolRequest.model_validate(value)
    release_root = releases.registry_root(request.release_root)
    active = releases.active(release_root)
    source = Path(active["source"])
    if source_fingerprint(source) != source_fingerprint(SOURCE_ROOT) or active["version"] != product_version():
        raise ValueError("Tool maintenance source differs from the active release")
    source_text = (source / TARGET).read_text(encoding="utf-8")
    call = expression(source_text)
    record = workspace.read(request.run_id)
    task = record["task"]
    parameters = (task.get("request") or {}).get("parameters", {})
    if (task["operation"] != TOOL or parameters.get("where", {}).get("op") != "like" or
            request.expected_count < 1):
        raise ValueError("This case is outside the maintained LIKE operator policy")
    assertion = {"statistic": "selected_count", "expected": request.expected_count, "oracle": request.oracle}
    diagnostic = diagnose_run(workspace, request.run_id, assertion)
    reproduction = replay(workspace, diagnostic)
    if reproduction["status"] != "reproduced":
        raise ValueError("The tool error did not reproduce with frozen inputs")
    archive = absolute_file(request.quality)
    key = fingerprint([request.model_dump(mode="json"), active["manifest_sha256"]])
    directory = contained(workspace.root, "tool-maintenance", key)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()
    frozen = directory / "quality.zip"
    shutil.copyfile(archive, frozen)
    if digest(frozen) != request.quality_sha256:
        raise ValueError("Frozen tool quality archive checksum mismatch")
    quality = directory / "quality"
    releases.extract(frozen, quality)
    absolute_file(quality / "verify.py")
    save(directory / "plan.json", {"base": active, "release_root": str(release_root), "quality_sha256": request.quality_sha256,
                                  "quality_files": releases.manifest(quality), "origin": diagnostic["id"],
                                  "baseline_probe": reproduction["run_id"], "impact": diagnostic["impact"]})
    return {"id": key, "directory": str(directory), "prompt": {
        "instruction": "Return only JSON with replacement. In the outer Series.str.fullmatch call add flags= as a keyword. Keep the inner like_regex call and all existing arguments verbatim. Select the flag that lets dot match newline.",
        "defect": "SQL LIKE percent and underscore match a line break. Current selection misses a text field with an embedded newline.",
        "observed_count": diagnostic["finding"]["evidence"]["actual"], "independent_count": request.expected_count,
        "expression": ast.get_source_segment(source_text, call),
        "required_shape": 'series.str.fullmatch(like_regex(value["value"]), na=False, flags=REGEX_FLAG)',
        "available_flags": {"re.ASCII": "ASCII character classes", "re.DOTALL": "dot includes line breaks",
                            "re.IGNORECASE": "case-insensitive matching"},
    }}


def write_source(path, value, encoding="utf-8"):
    newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    path.write_text(value, encoding=encoding, newline=newline)


def update_tool_versions(source):
    path = source / CAPABILITY_FILE
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    versions = {}
    for operation in RELATED:
        prefix = f'    "{operation}": Capability('
        index, = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        version = increment(CAPABILITIES[operation].version, "fix")
        versions[operation] = version
        line = lines[index]
        updated, matches = re.subn(r'version="[^"]+"', f'version="{version}"', line)
        lines[index] = updated if matches else line.rstrip().removesuffix("),") + f', version="{version}"),\n'
    write_source(path, "".join(lines))
    return versions


def verify_candidate(directory, source, plan):
    quality = directory / "quality"
    archive = directory / "quality.zip"
    if digest(archive) != plan["quality_sha256"] or releases.manifest(quality) != plan["quality_files"]:
        raise ValueError("Frozen quality assets changed")
    output = directory / "verification"
    environment = {name: value for name, value in os.environ.items()
                   if name.lower() in {"path", "systemroot", "windir", "comspec", "temp", "tmp", "userprofile",
                                       "appdata", "localappdata", "programfiles", "programfiles(x86)"}}
    try:
        result = subprocess.run([sys.executable, "-I", "-B", str(quality / "verify.py"), str(source), str(output)],
                                cwd=directory, env=environment, capture_output=True, timeout=170, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("Frozen candidate validation timed out") from None
    (directory / "verification.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError("Candidate failed frozen quality checks")
    receipt = output / "result.json"
    checked = json.loads(receipt.read_text(encoding="utf-8"))
    if any(checked.get(key) is not True for key in REQUIRED_CHECKS):
        raise ValueError("Frozen quality did not confirm every required check")
    if digest(archive) != plan["quality_sha256"] or releases.manifest(quality) != plan["quality_files"]:
        raise ValueError("Frozen quality assets changed during verification")
    return digest(receipt)


def apply(workspace, id, payload):
    if not re.fullmatch(r"[a-f0-9]{64}", id):
        raise ValueError("Invalid tool maintenance ID")
    directory = contained(workspace.root, "tool-maintenance", id)
    receipt = contained(directory, "result.json")
    if receipt.is_file():
        return json.loads(receipt.read_text(encoding="utf-8"))
    plan = json.loads(contained(directory, "plan.json").read_text(encoding="utf-8"))
    root = releases.registry_root(plan["release_root"])
    try:
        contained(directory, "candidate.claim").touch(exist_ok=False)
    except FileExistsError:
        active = releases.active(root)
        location = releases.version_directory(root, active["version"])
        verified = releases.check_release(location, active["manifest_sha256"])
        if verified.get("maintenance_id") == id:
            result = {"status": "published", "release": active, "verification": verified["verification"]}
            save(receipt, result)
            return result
        raise ValueError("Tool candidate is already running or its receipt is unknown") from None
    staged = None
    phase = "proposal"
    try:
        proposal = ToolProposal.model_validate(payload)
        active = releases.active(root)
        if active != plan["base"]:
            raise ValueError("Active tool source changed since diagnosis")
        phase = "build"
        staged = releases.stage(root)
        source = staged / "source"
        releases.extract(Path(active["package"]), source, source=True)
        source_file = source / TARGET
        before = source_file.read_text(encoding="utf-8")
        after = patch_expression(before, proposal.replacement)
        write_source(source_file, after)
        tool_versions = update_tool_versions(source)
        version = increment(active["version"], "fix")
        write_source(source / "VERSION", version + "\n", "ascii")
        original_files = releases.manifest(source, source=True)
        diff = "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                    fromfile="a/" + TARGET, tofile="b/" + TARGET))
        (directory / "candidate.diff").write_text(diff, encoding="utf-8")
        package = staged / "source.zip"
        releases.pack(source, package, original_files)
        version_check = subprocess.run([sys.executable, "-I", "-B", str(source / "src/guizhi/__main__.py"),
                                        "--version"], capture_output=True, text=True, timeout=20, check=True)
        if version_check.stdout.strip() != version:
            raise ValueError("Built candidate failed its version smoke check")
        phase = "validation"
        check = verify_candidate(directory, source, plan)
        if releases.manifest(source, source=True) != original_files:
            raise ValueError("Candidate source changed while running frozen quality")
        verification = {"origin": plan["origin"], "baseline_probe": plan["baseline_probe"],
                        "impact": plan["impact"], "quality_sha256": plan["quality_sha256"], "result_sha256": check,
                        "source_sha256": source_fingerprint(source), "tools": tool_versions,
                        "patch_sha256": sha256(diff.encode("utf-8")).hexdigest()}
        model = contained(directory, "model.json")
        if model.is_file():
            verification["model"] = json.loads(model.read_text(encoding="utf-8"))
        save(staged / "release.json", {"version": version, "previous_version": active["version"],
                                       "package_sha256": digest(package), "files": original_files,
                                       "maintenance_id": id, "verification": verification})
        phase = "activation"
        released = releases.publish(root, staged, version, active)
        result = {"status": "published", "release": released, "verification": verification}
    except (OSError, ValueError, BadZipFile, subprocess.SubprocessError) as exc:
        result = {"status": "rejected", "phase": phase, "error": str(exc), "active": releases.active(root)["version"],
                  "candidate": str(staged) if staged else None}
    save(receipt, result)
    return result
