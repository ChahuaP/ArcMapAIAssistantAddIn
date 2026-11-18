
"""Assemble one offline installer payload from pinned Windows runtimes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess

from install_state import VERSION, absolute, checksum, files
from component_archive import create
from dsh_payload import SDK_APP, package_manifest, retained
from dsh_zod import share as share_zod
from python_components import assemble as assemble_python

SOURCE = Path(__file__).resolve().parents[1]
BOOTSTRAP_FILES = {"python.exe", "pythonw.exe", "python311._pth", "python311.zip", "LICENSE.txt", "python.cat"}


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def copy_tree(source, target, include=None):
    target.mkdir(parents=True, exist_ok=False)
    for name, file in files(source):
        if include is not None and not include(name):
            continue
        destination = target.joinpath(*name.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)


def copy_file(source, target):
    if source.resolve(strict=True) != source:
        raise ValueError("Build input must not redirect")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_package_lock(root, spec):
    expected = SOURCE / "packaging" / spec
    if json_file(root / "package-lock.json") != json_file(expected / "package-lock.json"):
        raise ValueError(f"Runtime package lock does not match {spec}")


def verify_runtimes(runtime, desktop, dsh):
    runtime_lock = json_file(SOURCE / "packaging/runtime.lock.json")
    node = runtime / "node/node.exe"
    if checksum(node) != runtime_lock["node"]["sha256"]:
        raise ValueError("Node checksum does not match runtime.lock.json")
    expected_python = runtime_lock["python"]["version"]
    python = runtime / "python/python.exe"
    command = ("import json,platform,importlib.metadata as m;"
               "print(json.dumps([platform.python_version(),{d.metadata['Name']:d.version for d in m.distributions()}]))")
    actual, packages = json.loads(subprocess.check_output([str(python), "-I", "-B", "-c", command], timeout=35))
    if actual != expected_python:
        raise ValueError("Embedded Python version does not match runtime.lock.json")
    def normalized(name):
        return re.sub(r"[-_.]+", "-", name.lower())

    locked = {normalized(name): version for name, version in
              re.findall(r"(?m)^([a-zA-Z0-9_.-]+)==([^\s]+)", (SOURCE / "requirements.lock").read_text())}
    installed = {normalized(name): version for name, version in packages.items()}
    if installed != locked:
        names = sorted(name for name in installed.keys() | locked.keys() if installed.get(name) != locked.get(name))
        raise ValueError("Python runtime packages differ from requirements.lock: " + ", ".join(names))
    for root, spec in ((dsh, "dsh-runtime"), (desktop, "desktop-runtime")):
        copy_package_lock(root, spec)
        pins = json_file(SOURCE / "packaging" / spec / "package.json")["dependencies"]
        for package, version in pins.items():
            if json_file(root / "node_modules" / package / "package.json")["version"] != version:
                raise ValueError(f"Installed dependency version does not match lock: {package}")
    electron = desktop / "node_modules/electron/dist/electron.exe"
    desktop_identity = json_file(desktop / "runtime-identity.json")
    if (checksum(electron) != desktop_identity["binary_sha256"] or
            checksum(desktop / "package-lock.json") != desktop_identity["package_lock_sha256"]):
        raise ValueError("Electron executable differs from prepared runtime identity")
    return {"python": actual, "node": runtime_lock["node"]["version"],
            "electron": desktop_identity["electron"], "dsh": json_file(dsh / "package.json")["dependencies"]}


def build(stage, runtime, desktop, dsh, speech, arcmap, addin, tree, commit):
    dependencies = verify_runtimes(runtime, desktop, dsh)
    version = (SOURCE / "VERSION").read_text(encoding="ascii").strip()
    if not re.fullmatch(VERSION, version) or not re.fullmatch(r"[a-f0-9]{40}", tree):
        raise ValueError("Use the product VERSION and a verified Git tree")
    if commit and not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid Git commit ID")
    stage.mkdir(parents=True, exist_ok=False)
    payload = stage / "payload"
    core = payload / "core"
    for name in ("src/guizhi", "apps/desktop", "resources/skills"):
        copy_tree(SOURCE / name, core / name, lambda path: "__pycache__" not in path.split("/") and not path.endswith((".pyc", ".pyo")))
    for name in ("VERSION", "LICENSE", "requirements.lock", "packaging/runtime.lock.json",
                 "packaging/dsh-runtime/package.json", "packaging/local-speech/models.lock.json",
                 "packaging/component_archive.py", "packaging/update-trust.json"):
        copy_file(SOURCE / name, core / name)
    lock = json_file(dsh / "package-lock.json")
    dsh_target = payload / "runtime/dsh"
    share_zod(dsh, dsh_target, runtime / "node/node.exe", lock, retained(lock), copy_tree)
    for name in ("@deepseek-ai/dsh", SDK_APP):
        manifest = dsh_target / "node_modules" / name / "package.json"
        manifest.write_text(json.dumps(package_manifest(json_file(manifest)), indent=2), encoding="utf-8")
    for source, name in ((runtime / "node", "runtime/node"),
                         (desktop / "node_modules/electron/dist", "renderer/node_modules/electron/dist"),
                         (desktop / "node_modules/react", "renderer/node_modules/react"),
                         (desktop / "node_modules/react-dom", "renderer/node_modules/react-dom")):
        copy_tree(source, payload / name)
    for package, asset in (("antd", "dist/antd.min.js"), ("dayjs", "dayjs.min.js"),
                           ("markdown-it", "dist/browser/markdown-it.umd.min.js")):
        source = desktop / "node_modules" / package
        target = payload / "renderer/node_modules" / package
        copy_file(source / asset, target / asset)
        copy_file(source / "LICENSE", target / "LICENSE")
    bundle = SOURCE / "packaging" / "bundle_map.cjs"
    subprocess.run([str(runtime / "node/node.exe"), str(bundle), str(desktop),
                    str(payload / "renderer")], check=True)
    bootstrap = stage / "bootstrap"
    bootstrap.mkdir()
    for name in BOOTSTRAP_FILES:
        copy_file(runtime / "python" / name, bootstrap / name)
    for file in (runtime / "python").iterdir():
        if file.suffix.lower() in {".dll", ".pyd"}:
            copy_file(file, bootstrap / file.name)
    for name in ("install_state.py", "apply_update.py"):
        copy_file(SOURCE / "packaging" / name, bootstrap / name)
    copy_file(SOURCE / "packaging/guizhi.ico", bootstrap / "guizhi.ico")

    archives = stage / "components"
    archives.mkdir()
    components = assemble_python(runtime / "python", payload / "runtime/python", stage, version, copy_tree)
    if speech:
        speech_identity = json_file(speech / "identity.json")
        spec = json_file(SOURCE / "packaging/local-speech/models.lock.json")
        if speech_identity["engine"] != spec["engine"] or set(speech_identity["modes"]) != {"asr", "tts"}:
            raise ValueError("The optional speech bundle requires both pinned models")
        for mode, model in spec["models"].items():
            for item in model["files"]:
                if checksum(speech / "models" / mode / item["file"]) != item["sha256"]:
                    raise ValueError("Local speech asset differs from its pinned checksum")
        components["speech"] = create(speech, archives / f"Guizhi-{version}-speech.zip")
    if not arcmap or not addin or not all((arcmap / name).is_file() for name in ("ArcMapBridge.exe", "ArcMapBridge.build")):
        raise ValueError("The release requires the verified ArcGIS Desktop bridge and add-in")
    for file in arcmap.iterdir():
        if file.is_file() and (file.suffix.lower() in {".exe", ".dll"} or
                               file.name in {"ArcMapBridge.exe.config", "ArcMapBridge.build"}):
            copy_file(file, core / "packaging/arcgis" / file.name)
    copy_file(addin, core / "packaging/arcgis/Guizhi.esriaddin")
    (core / "packaging/components.json").write_text(json.dumps(components, sort_keys=True), encoding="utf-8")
    hashes = {name: checksum(file) for name, file in files(payload)}
    boot = {name: checksum(file) for name, file in files(bootstrap)}
    manifest = {"schema": 1, "version": version, "components": [], "source_tree": tree,
                "source_commit": commit, "dependencies": dependencies, "files": hashes, "bootstrap": boot}
    identity = sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    manifest["build"] = f"{version}-{identity}"
    path = payload / "installation.json"
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return {"version": version, "build": manifest["build"], "manifest_sha256": checksum(path),
            "components": components, "files": len(hashes), "source_tree": tree, "source_commit": commit}


def main():
    parser = argparse.ArgumentParser()
    for item in ("stage", "runtime", "desktop", "dsh"):
        parser.add_argument("--" + item, required=True, type=absolute)
    for item in ("speech", "arcmap"):
        parser.add_argument("--" + item, type=absolute)
    parser.add_argument("--addin", type=Path)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.stage.is_relative_to(SOURCE) or SOURCE.is_relative_to(args.stage):
        raise ValueError("Installer staging must be outside source")
    print(json.dumps(build(args.stage, args.runtime, args.desktop, args.dsh, args.speech, args.arcmap,
                           args.addin, args.tree, args.commit)))


if __name__ == "__main__":
    main()
