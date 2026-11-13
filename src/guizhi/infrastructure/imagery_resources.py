"""Locally installed classifiers with immutable model and applicability profiles."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from guizhi.domain.imagery import ModelProfile
from guizhi.infrastructure.optional_components import require


def root(workspace):
    folder = workspace.root / "imagery-models"
    if folder.exists() and folder.resolve() != folder:
        raise ValueError("影像模型目录不能指向其他位置")
    return folder


def profiles(workspace):
    for directory in sorted(root(workspace).glob("[0-9a-f]*")):
        if re.fullmatch(r"[0-9a-f]{64}", directory.name) and directory.is_dir() and directory.resolve() == directory:
            yield ModelProfile.model_validate_json((directory / "profile.json").read_bytes()), directory / "model.onnx"


def enabled(file):
    return not (file.parent / "disabled").exists()


def catalog(workspace):
    return [{**item.model_dump(mode="json"), "enabled": enabled(file)} for item, file in profiles(workspace)]


def identity(workspace, name, version):
    key = sha256(json.dumps([name, version], ensure_ascii=False).encode()).hexdigest()
    return root(workspace) / key


def activate(workspace, name, version, active):
    folder = identity(workspace, name, version)
    if not folder.is_dir() or folder.resolve() != folder:
        raise ValueError("未找到该影像模型版本")
    marker = folder / "disabled"
    if active:
        marker.unlink(missing_ok=True)
    else:
        try:
            with marker.open("xb"):
                pass
        except FileExistsError:
            pass
    return catalog(workspace)


def install(workspace, model_file, profile_file):
    require("imagery")
    from guizhi.infrastructure.operations.imagery import session_for

    source, profile_source = Path(model_file).resolve(strict=True), Path(profile_file).resolve(strict=True)
    if source.suffix.lower() != ".onnx" or not 0 < source.stat().st_size <= 50 << 20:
        raise ValueError("请选择不超过 50 MB 的影像模型文件")
    if profile_source.suffix.lower() != ".json" or not 0 < profile_source.stat().st_size <= 64 << 10:
        raise ValueError("请选择该模型的适用范围清单（JSON）")
    model = ModelProfile.model_validate_json(profile_source.read_bytes())
    body = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()
    folder = root(workspace)
    folder.mkdir(exist_ok=True)
    target = identity(workspace, model.name, model.version)
    pending = folder / ("pending-" + uuid4().hex)
    pending.mkdir()
    try:
        binary = pending / "model.onnx"
        shutil.copyfile(source, binary)
        session_for(binary, model)
        (pending / "profile.json").write_bytes(body)
        try:
            pending.rename(target)
        except OSError:
            if not target.is_dir() or target.resolve() != target:
                raise
            if (target / "profile.json").read_bytes() != body:
                raise ValueError("同名同版本模型已有不同的适用范围，请提高模型版本")
            session_for(target / "model.onnx", model)
        return {**model.model_dump(mode="json"), "enabled": enabled(target / "model.onnx")}
    finally:
        if pending.exists():
            shutil.rmtree(pending)


def resolve(workspace, scene_files, values):
    require("imagery")
    from guizhi.infrastructure.operations.imagery import check_grid
    from guizhi.infrastructure.operations.raster import check_source
    import rasterio

    try:
        dates = [date.fromisoformat(values[key]) for key in ("from_date", "to_date")]
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("请选择有效的前期与后期影像日期") from error
    if dates[0] >= dates[1]:
        raise ValueError("后期影像日期须晚于前期")
    candidates = [(model, file) for model, file in profiles(workspace) if enabled(file) and
                  model.region == values["region"] and model.sensor == values["sensor"] and
                  model.start <= dates[0] < dates[1] <= model.end]
    if not candidates:
        raise ValueError("当前部署未覆盖这批影像的区域、传感器或日期，请联系系统维护人员")
    matches = []
    for scene in scene_files:
        check_source(scene)
    for model, file in candidates:
        try:
            for scene in scene_files:
                with rasterio.open(scene) as src:
                    check_grid(src, model)
                    if src.descriptions != tuple(model.bands):
                        raise ValueError("波段不匹配")
        except ValueError:
            continue
        matches.append((model, file))
    if not matches:
        raise ValueError("影像的坐标、范围、分辨率或波段与已安装模型不匹配")
    names = {model.name for model, _ in matches}
    if len(names) != 1:
        raise ValueError("多种影像模型适用，请在设置中整理适用范围后重试")
    model, file = max(matches, key=lambda item: tuple(map(int, item[0].version.split("."))))
    return {"file": str(file), "layer": None, "revision": model.sha256}, model.model_dump(mode="json")
