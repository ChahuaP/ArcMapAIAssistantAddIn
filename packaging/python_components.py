"""Split the pinned Python install by wheel ownership, including native DLLs."""
import json
from pathlib import Path
import subprocess

from component_archive import create

INFERENCE = {"onnxruntime", "flatbuffers", "protobuf"}
PACKAGES = {
    "ocr": INFERENCE | {"rapidocr-onnxruntime", "opencv-python", "pillow", "pyclipper", "tqdm"},
    "raster": {"rasterio", "affine", "cligj", "click-plugins"},
    "imagery": INFERENCE,
}


def core_file(name, excluded):
    if name in excluded or name.startswith("Lib/site-packages/bin/"):
        return False
    if not name.startswith("Lib/site-packages/"):
        return True
    # Preserve public helpers such as pandas.testing. Only strip test suites and bytecode caches.
    directories = name.split("/")[2:-1]
    return not {"test", "tests", "__pycache__"}.intersection(directories)


def assemble(python, payload, stage, version, copy_tree):
    # RECORD is authoritative even for namespace packages and bundled native libraries.
    names = set().union(*PACKAGES.values())
    command = (
        "import json,sys,importlib.metadata as m;"
        "print(json.dumps({n:[f.as_posix() for f in m.distribution(n).files "
        "if '..' not in f.parts] for n in sys.argv[1:]}))"
    )
    records = json.loads(subprocess.check_output(
        [str(python / "python.exe"), "-I", "-B", "-c", command, *sorted(names)], timeout=35))
    site = Path("Lib/site-packages")
    groups = {kind: {name for package in packages for name in records[package]}
              for kind, packages in PACKAGES.items()}
    excluded = {site.joinpath(name).as_posix() for group in groups.values() for name in group}
    copy_tree(python, payload, lambda name: core_file(name, excluded))

    components = {}
    for kind, members in groups.items():
        source = stage / ("python-" + kind)
        copy_tree(python / site, source, members.__contains__)
        (source / "component.json").write_text(json.dumps({"kind": kind, "version": version}), encoding="ascii")
        components[kind] = {**create(source, stage / "components" / f"Guizhi-{version}-{kind}.zip"),
                            "python": True}

    # Embedded Python honors these paths under -I even when directories appear later.
    # All paths refer to the application installation, outside the individual release.
    config = payload / "python311._pth"
    lines = config.read_text(encoding="ascii").splitlines()
    index = lines.index("import site")
    lines[index:index] = [
        f"..\\..\\..\\..\\guizhi-python-components\\{kind}-{entry['sha256'][:16]}"
        for kind, entry in components.items()
    ]
    config.write_text("\n".join(lines) + "\n", encoding="ascii")
    return components
