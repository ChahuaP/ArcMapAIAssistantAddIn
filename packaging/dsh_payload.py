"""Select the locked dependency closure used by the SDK profile."""
from pathlib import PurePosixPath
import re


# The DSH CLI declares the dependencies of all its profiles. The Windows
# desktop invokes only sdk-minimal and loads the providers in our patches.
CLI = {"@deepseek-ai/" + name for name in (
    "dsh-app-boot", "dsh-atomic-write", "dsh-cmdline", "dsh-home-paths",
    "dsh-http-proxy", "dsh-launch-environment", "dsh-official-bundles",
    "dsh-plugin-manager", "dsh-sdk-minimal", "dsh-llm-pi-ai", "dsh-mcp-client",

)}
CLI.update({"commander", "js-yaml", "node-addon-require-builtin"})
PROFILE_PLUGINS = {"@deepseek-ai/" + name for name in (
    "dsh-subagent", "dsh-agent-team", "dsh-tool-agent-team", "dsh-subagent-spawn-in-process",
    "dsh-tool-subagent", "dsh-tool-subagent-control", "dsh-web", "dsh-web-deepseek-api",
    "dsh-tool-web", "dsh-tool-call-timeout-policy",
)}
SDK_APP = "@deepseek-ai/dsh-sdk-app"
OFFICE_PLUGINS = {"@deepseek-ai/" + name for name in ("dsh-skill-office", "dsh-office-kit", "dsh-tool-office")}


def retained(lock):
    packages = lock["packages"]
    selected = set()
    todo = [("", name, False) for name in PROFILE_PLUGINS.union((
        "@deepseek-ai/dsh", "@deepseek-ai/dsh-sdk-client", "@deepseek-ai/dsh-llm-pi-ai",
        "@deepseek-ai/dsh-mcp-client", "@earendil-works/pi-ai", "ws",
    ))]
    while todo:
        owner, name, optional = todo.pop()
        parent = PurePosixPath(owner)
        for prefix in (parent, *parent.parents):
            location = (prefix / "node_modules" / name).as_posix()
            if location in packages:
                break
        else:
            if optional:
                continue
            raise ValueError(f"Missing DSH dependency: {owner} -> {name}")
        if location in selected:
            continue
        entry = packages[location]
        if ("os" in entry and "win32" not in entry["os"] or
                "cpu" in entry and "x64" not in entry["cpu"]):
            if optional:
                continue
            raise ValueError(f"Unsupported DSH dependency: {location}")
        selected.add(location)
        regular = set(entry.get("dependencies", {}))
        if location == "node_modules/@deepseek-ai/dsh":
            regular &= CLI
        elif location == f"node_modules/{SDK_APP}":
            # sdk-minimal uses SDK startup only. Office processing belongs to the desktop's material service.
            regular -= OFFICE_PLUGINS
        peers = entry.get("peerDependencies", {})
        meta = entry.get("peerDependenciesMeta", {})
        todo.extend((location, dep, False) for dep in regular)
        todo.extend((location, dep, True) for dep in entry.get("optionalDependencies", {}))
        todo.extend((location, dep, meta.get(dep, {}).get("optional", False)) for dep in peers)
    return selected


def runtime_file(name, selected):
    if not name.startswith("node_modules/"):
        return name in {"package.json", "package-lock.json"}
    if name.endswith((".map", ".d.ts", ".d.cts", ".d.mts")):
        return False
    pty = name.removeprefix("node_modules/node-pty/")
    if pty != name and (pty.endswith(".pdb") or pty.startswith(("prebuilds/win32-arm64/", "third_party/"))):
        # The pinned Windows x64 package loads its bundled x64 prebuilds.
        return False
    owner = re.match(r"^(.*/node_modules/|node_modules/)((?:@[^/]+/)?[^/]+)/", name)
    return bool(owner and owner[1] + owner[2] in selected)


def package_manifest(manifest):
    dependencies = manifest["dependencies"]
    if manifest["name"] == "@deepseek-ai/dsh":
        manifest["dependencies"] = {name: dependencies.get(name, manifest["version"]) for name in sorted(CLI | PROFILE_PLUGINS)}
    elif manifest["name"] == SDK_APP:
        manifest["dependencies"] = {name: version for name, version in dependencies.items() if name not in OFFICE_PLUGINS}
    return manifest
