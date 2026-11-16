"""Build and install version-pinned optional component archives."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def checksum(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create(source, archive):
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=3) as target:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                info = ZipInfo(file.relative_to(source).as_posix())
                info.compress_type = ZIP_DEFLATED
                info._compresslevel = 3
                info.file_size = file.stat().st_size
                with file.open("rb") as content, target.open(info, "w") as output:
                    shutil.copyfileobj(content, output)
    return {"file": archive.name, "size": archive.stat().st_size, "sha256": checksum(archive)}


def install(archive, target, expected):
    if target.resolve() != target or target.exists():
        raise ValueError("组件安装位置无效或已安装")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as scratch:
        private = Path(scratch) / "component.zip"
        shutil.copyfile(archive, private)
        if private.stat().st_size != expected["size"] or checksum(private) != expected["sha256"]:
            raise ValueError("组件包校验失败，请选择此版本的组件包")
        extracted = Path(scratch) / "payload"
        extracted.mkdir()
        with ZipFile(private) as source:
            for item in source.infolist():
                name = PurePosixPath(item.filename)
                if name.is_absolute() or ".." in name.parts or ":" in item.filename or "\\" in item.filename:
                    raise ValueError("组件包路径无效")
                file = extracted.joinpath(*name.parts)
                file.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as data, file.open("xb") as stream:
                    shutil.copyfileobj(data, stream)
        extracted.rename(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("expected", type=json.loads)
    args = parser.parse_args()
    try:
        install(args.archive, args.target, args.expected)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error))
