from __future__ import print_function

import os
import re
import zipfile


CURRENT_PATH = os.path.dirname(os.path.abspath(__file__))
OUT_ZIP_NAME = os.path.join(
    CURRENT_PATH,
    os.path.basename(CURRENT_PATH) + ".esriaddin"
)
BACKUP_FILE_PATTERN = re.compile(r".*_addin_[0-9]+[.]py$", re.IGNORECASE)


def looks_like_a_backup(filename):
    return bool(BACKUP_FILE_PATTERN.match(filename))


def add_required_file(zip_file, filename):
    zip_file.write(os.path.join(CURRENT_PATH, filename), filename)


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
    with zipfile.ZipFile(OUT_ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for filename in ("config.xml", "README.txt", "makeaddin.py"):
            add_required_file(zip_file, filename)
        for directory in ("Images", "Install"):
            add_directory(zip_file, directory)
    print(OUT_ZIP_NAME)


if __name__ == "__main__":
    main()
