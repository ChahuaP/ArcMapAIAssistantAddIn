"""Share identical, privately installed Zod copies in the DSH payload."""
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import subprocess

from dsh_payload import runtime_file
from install_state import checksum, files

SUFFIX = "/node_modules/zod"


def groups(source, lock, selected):
    identical = defaultdict(list)
    for location in sorted(selected):
        if not location.endswith(SUFFIX):
            continue
        owner = location.removesuffix(SUFFIX)
        # A package imported through an ancestor may have other consumers.
        if not owner.startswith("node_modules/@deepseek-ai/") or len(PurePosixPath(owner).parts) != 3:
            continue
        if any(other != location and other.startswith(owner + "/node_modules/") for other in selected):
            continue
        version = lock["packages"][location]["version"]
        manifest = json.loads((source / location / "package.json").read_bytes())
        if manifest["version"] != version or manifest["name"] != "zod":
            raise ValueError(f"Zod does not match the lock: {location}")
        # Moving a package with its own dependencies could change their resolution.
        if any(manifest.get(key) for key in ("dependencies", "optionalDependencies", "peerDependencies")):
            continue
        contents = tuple(sorted((name, checksum(file)) for name, file in files(source / location)))
        identical[version, contents].append(location)

    return {f"@guizhi/zod-{version}-{sha256(repr(contents).encode()).hexdigest()[:12]}": locations
            for (version, contents), locations in identical.items() if len(locations) > 1}


def share(source, target, node, lock, selected, copy_tree):
    shared = groups(source, lock, selected)
    removed = {location for locations in shared.values() for location in locations}
    retained = selected - removed
    copy_tree(source, target, lambda name: runtime_file(name, retained))
    if not shared:
        return

    owners = {}
    for package, locations in shared.items():
        original = locations[0]
        copy_tree(source / original, target / "node_modules" / package,
                  lambda name: runtime_file(f"{original}/{name}", selected))
        owners.update((location.removesuffix(SUFFIX), package) for location in locations)

    for package in ("acorn", "acorn-walk"):
        manifest = json.loads((source / "node_modules" / package / "package.json").read_bytes())
        if manifest["version"] != lock["packages"][f"node_modules/{package}"]["version"]:
            raise ValueError(f"Build parser does not match the lock: {package}")
    subprocess.run([str(node), str(Path(__file__).with_name("dsh_zod.cjs")), str(source), str(target)],
                   input=json.dumps(owners), text=True, check=True)
