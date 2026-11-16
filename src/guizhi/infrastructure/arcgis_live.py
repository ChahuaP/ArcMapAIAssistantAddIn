"""Seal data received from the active ArcGIS extension and stage verified result layers."""
from hashlib import sha256
import ctypes
from ctypes import wintypes
import json
import shutil
from pathlib import Path
from uuid import uuid4
import winreg

import pyogrio

from guizhi.infrastructure.arcmap import bridge, export_frame, file_stamps, json_file, local_data
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.runner import data_revision
from guizhi.infrastructure.vector import digest, source_files, source_revision
from guizhi.infrastructure.workspace import Workspace


def launcher():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\Desktop10.2", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
            root = Path(winreg.QueryValueEx(key, "InstallDir")[0])
        executable = (root / "bin" / "ArcMap.exe").resolve(strict=True)
    except OSError as error:
        raise ValueError("未找到 ArcGIS Desktop 10.2 安装") from error
    return {"executable": str(executable)}


def exchange_root(workspace):
    parent = workspace.root / "host-exchanges"
    if parent.resolve() != parent:
        raise ValueError("ArcGIS 交换目录不能指向其他位置")
    parent.mkdir(exist_ok=True)
    return parent


def view(root, executable):
    location = exchange_root(Workspace(root, create=True))
    target = location / "成果数据视图.mxd"
    if target.resolve() != target:
        raise ValueError("ArcGIS 数据视图不能指向其他位置")
    if not target.exists():
        folder = location / uuid4().hex
        folder.mkdir()
        try:
            pending = folder / target.name
            bridge(executable, folder, "view", {"action": "view", "output": str(pending)})
            try:
                pending.rename(target)
            except FileExistsError:
                if target.resolve() != target or not target.is_file():
                    raise
        finally:
            shutil.rmtree(folder)
    return {**launcher(), "view": str(target)}


def notify(pid, window):
    if pid <= 0 or window <= 0:
        raise ValueError("ArcGIS 宿主标识无效")
    user = ctypes.windll.user32
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    process = wintypes.DWORD()
    message = user.RegisterWindowMessageW("Guizhi.ArcGIS.Sync.v1")
    if not user.GetWindowThreadProcessId(window, ctypes.byref(process)) or process.value != pid or not message:
        raise ValueError("ArcGIS 窗口已关闭，请重新连接")
    if not user.PostMessageW(window, message, 0x475A1201, 0):
        raise ValueError("无法通知 ArcGIS 窗口，请确认两端使用同一 Windows 用户及权限")
    return {"notified": True}


def gdb_revision(path):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != ".gdb" or not path.is_dir() or str(path).startswith("\\\\"):
        raise ValueError("请选择本机文件地理数据库")
    before = file_stamps(path)
    parts = [path / item[0] for item in before]
    if not parts or any(item.resolve() != item for item in parts):
        raise ValueError("ArcGIS 数据库没有可核对的本地数据")
    try:
        value = source_revision(parts)
    except PermissionError as error:
        raise ValueError("ArcGIS 数据正在被锁定，请在完成编辑后重试") from error
    if before != file_stamps(path):
        raise ValueError("数据库在校验期间发生变化，请保存编辑后重试")
    return value


def revision(path):
    file = Path(path)
    if file.suffix.lower() == ".gdb":
        return gdb_revision(file)
    if file.suffix.lower() != ".tif":
        raise ValueError("ArcGIS 成果数据格式无效")
    try:
        return digest(file)
    except PermissionError as error:
        raise ValueError("ArcGIS 数据正在被锁定，请在完成编辑后重试") from error


def prepare(root, executable, payload):
    workspace = Workspace(root)
    material = payload.get("material")
    provenance = {}
    if material:
        file = Path(material["file"]).resolve(strict=True)
        name = material.get("layer")
        provenance = {"input": str(file), "layer": name}
    else:
        artifact = payload["artifact"]
        if artifact not in {"export", "boundaries"}:
            raise ValueError("请选择已验证的成果数据")
        file = read_artifact(workspace, payload["run"], artifact)
        record = workspace.read(payload["run"])["result"]
        name = record.get("export_layer") if artifact == "export" else "result"
        provenance = {"source_run": payload["run"], "artifact": artifact}
    folder = exchange_root(workspace) / uuid4().hex
    folder.mkdir()
    dataset = "guizhi_layer"
    try:
        suffix = file.suffix.lower()
        if suffix in {".shp", ".gpkg", ".geojson"}:
            files = source_files(file)
            before = source_revision(files)
            if material and material.get("revision"):
                expected = data_revision(file) if suffix == ".gpkg" else before
                if expected != material["revision"]:
                    raise ValueError("输入图层的快照版本已变化")
            if not name:
                raise ValueError("请为矢量数据选择图层")
            frame = pyogrio.read_dataframe(file, layer=name)
            data = folder / "display.gdb"
            tolerance = 1e-8 if frame.crs and frame.crs.is_geographic else 0.0002
            export_frame(frame, data, dataset, "OpenFileGDB", tolerance)
            kind, path = "vector", data / dataset
        elif suffix in {".tif", ".tiff", ".img"}:
            from guizhi.infrastructure.operations.raster import check_source, normalize

            files = check_source(file)
            before = source_revision(files)
            if material and material.get("revision") and material["revision"] != before:
                raise ValueError("输入栅格的快照版本已变化")
            data = folder / "display.tif"
            if len(files) == 1 and suffix in {".tif", ".tiff"}:
                shutil.copyfile(file, data)
                if digest(data) != digest(file):
                    raise ValueError("栅格显示副本校验失败")
            else:
                normalize(file, data, lambda: None)
            kind, path = "raster", data
        else:
            raise ValueError("ArcGIS 视图支持矢量和栅格数据")
        if source_revision(files) != before:
            raise ValueError("数据在生成显示副本时发生变化")
        layer = folder / "display.lyr"
        bridge(executable, folder, "layer", {"kind": kind, "source": str(data), "dataset": dataset,
                                             "name": payload["name"], "output": str(layer)})
        result = {"path": str(path), "data": str(data), "layer_file": str(layer), "layer_sha256": digest(layer),
                  "revision": revision(data), "kind": kind, **provenance}
        manifest = folder / "layer.json"
        json_file(manifest, result)
        return {**result, "manifest": str(manifest)}
    except Exception:
        shutil.rmtree(folder)
        raise


def capture(root, payload):
    workspace = Workspace(root, create=True)
    layer = payload["layer"]
    path, table = local_data(layer["source"], layer["dataset"])
    if path.resolve() != path or not Path(layer["path"]).is_absolute():
        raise ValueError("ArcGIS 图层数据源路径无效")
    if path.is_dir():
        revision = gdb_revision(path)
    else:
        revision = source_revision(source_files(path))
    selection = payload.get("selected_ids")
    frame = pyogrio.read_dataframe(path, layer=table, where=payload["filter"] or None, fid_as_index=True)
    if selection is not None:
        frame = frame.loc[frame.index.isin(selection)]
        if set(frame.index) != set(selection):
            raise ValueError("已选要素有变化，请重新选择")
    if frame.empty:
        raise ValueError("所选图层没有可用要素")
    after = gdb_revision(path) if path.is_dir() else source_revision(source_files(path))
    if revision != after:
        raise ValueError("图层在读取期间被编辑，请完成编辑并重试")
    scope = [str(path), table, revision, payload["filter"], selection]
    identifier = sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()
    parent = workspace.root / "host-exchanges" / "revisions"
    if parent.resolve() != parent:
        raise ValueError("ArcGIS 交换目录不能指向其他位置")
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / identifier
    pending = parent / (uuid4().hex + ".pending")
    if not folder.exists():
        pending.mkdir()
        try:
            output = pending / "import.gpkg"
            export_frame(frame, output, "result", "GPKG")
            json_file(pending / "revision.json", {"source": str(path), "dataset": table, "revision": revision,
                                                "map": payload["map"], "filter": payload["filter"],
                                                "selected_ids": selection, "artifact_sha256": digest(output),
                                                "feature_count": len(frame)})
            try:
                pending.rename(folder)
            except OSError:
                if not folder.is_dir():
                    raise
        finally:
            if pending.exists():
                shutil.rmtree(pending)
    manifest = folder / "revision.json"
    output = folder / "import.gpkg"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if folder.resolve() != folder or digest(output) != record["artifact_sha256"]:
        raise ValueError("ArcGIS 材料快照校验失败")
    return {**record, "file": str(output), "manifest": str(manifest), "name": layer["name"],
            "data_revision": data_revision(output)}
