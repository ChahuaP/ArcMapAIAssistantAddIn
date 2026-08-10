from __future__ import print_function

import os
import re
import zipfile


CURRENT_PATH = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(CURRENT_PATH)
VERSION_TOKEN = "__GEOPILOT_VERSION__"
OUT_ZIP_NAME = os.path.join(
    CURRENT_PATH,
    os.path.basename(CURRENT_PATH) + ".esriaddin"
)
TEMP_ZIP_NAME = OUT_ZIP_NAME + ".writing"
BACKUP_FILE_PATTERN = re.compile(r".*_addin_[0-9]+[.]py$", re.IGNORECASE)


def looks_like_a_backup(filename):
    return bool(BACKUP_FILE_PATTERN.match(filename))


def add_required_file(zip_file, filename):
    zip_file.write(os.path.join(CURRENT_PATH, filename), filename)


def add_versioned_config(zip_file):
    version_path = os.path.join(REPO_ROOT, "VERSION")
    with open(version_path, "r", encoding="ascii") as version_file:
        version = version_file.read().strip()
    if not re.match(r"^\d+(\.\d+)+$", version):
        raise RuntimeError("VERSION does not contain a valid version number.")
    config_path = os.path.join(CURRENT_PATH, "config.xml")
    with open(config_path, "r", encoding="utf-8") as config_file:
        config = config_file.read()
    if config.count(VERSION_TOKEN) != 1:
        raise RuntimeError("config.xml must contain exactly one version token.")
    zip_file.writestr("config.xml", config.replace(VERSION_TOKEN, version))


def add_directory(zip_file, directory):
    root = os.path.join(CURRENT_PATH, directory)
    for path, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item != "__pycache__"]
        archive_path = os.path.relpath(path, CURRENT_PATH)
        added_file = False
        for filename in files:
            if looks_like_a_backup(filename) or filename.lower().endswith(".pyc"):
                continue
            archive_file = os.path.join(archive_path, filename)
            print(archive_file)
            zip_file.write(os.path.join(path, filename), archive_file)
            added_file = True
        if not added_file:
            zip_file.writestr(
                os.path.join(archive_path, "placeholder.txt"),
                "(Empty directory)"
            )


def main():
    if os.path.exists(TEMP_ZIP_NAME):
        os.remove(TEMP_ZIP_NAME)
    try:
        with zipfile.ZipFile(TEMP_ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as zip_file:
            add_versioned_config(zip_file)
            for filename in ("README.txt",):
                add_required_file(zip_file, filename)
            for directory in ("Install",):
                add_directory(zip_file, directory)
        with zipfile.ZipFile(TEMP_ZIP_NAME, "r") as zip_file:
            bad_file = zip_file.testzip()
            if bad_file is not None:
                raise RuntimeError("ArcMap Add-in archive is corrupt: " + bad_file)
        os.replace(TEMP_ZIP_NAME, OUT_ZIP_NAME)
    finally:
        if os.path.exists(TEMP_ZIP_NAME):
            os.remove(TEMP_ZIP_NAME)
    print(OUT_ZIP_NAME)


if __name__ == "__main__":
    main()
