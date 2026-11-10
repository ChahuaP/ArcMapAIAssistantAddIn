# -*- coding: utf-8 -*-
"""ArcMap UI-thread endpoint for the local Guizhi extension."""
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
import re
import time
import uuid

HOST_ROOT = os.environ.get("GUIZHI_ARCGIS_HOSTS") or os.path.join(os.environ["LOCALAPPDATA"], "Guizhi", "ArcGIS", "hosts")
import arcpy
import pythonaddins

PROTOCOL = 1
USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32
COMCTL32 = ctypes.windll.comctl32
MESSAGE_ID = 0x475A1201
SUBCLASS = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
USER32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
POLL_MESSAGE = USER32.RegisterWindowMessageW(u"Guizhi.ArcGIS.Sync.v1")
COMCTL32.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASS, ctypes.c_size_t, ctypes.c_size_t]
COMCTL32.RemoveWindowSubclass.argtypes = [wintypes.HWND, SUBCLASS, ctypes.c_size_t]
COMCTL32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
COMCTL32.DefSubclassProc.restype = ctypes.c_ssize_t
ENUM_WINDOW = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
USER32.EnumThreadWindows.argtypes = [wintypes.DWORD, ENUM_WINDOW, wintypes.LPARAM]
KERNEL32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
extension = None


def arcmap_window(thread):
    windows = []

    @ENUM_WINDOW
    def collect(handle, data):
        caption = ctypes.create_unicode_buffer(1024)
        USER32.GetWindowTextW(handle, caption, len(caption))
        if caption.value.endswith(u"ArcMap") and not USER32.GetWindow(handle, 4):
            windows.append(handle)
        return True

    USER32.EnumThreadWindows(thread, collect, 0)
    if len(windows) != 1:
        raise ValueError(u"等待 ArcGIS 主窗口")
    return windows[0]


def canonical(path):
    return os.path.normcase(os.path.realpath(path)) if path else u""


def write(path, body):
    temp = path + u"." + uuid.uuid4().hex
    try:
        with io.open(temp, "w", encoding="utf-8") as stream:
            stream.write(unicode(json.dumps(body, ensure_ascii=False, allow_nan=False)))
        if not KERNEL32.MoveFileExW(temp, path, 9):
            raise ctypes.WinError()
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read(path):
    if os.path.getsize(path) > 2 << 20:
        raise ValueError(u"请求过大")
    with io.open(path, encoding="utf-8") as stream:
        return json.load(stream)


def source(layer):
    return layer.dataSource if (layer.isFeatureLayer or layer.isRasterLayer) and not layer.isBroken else None


def frames(document):
    return arcpy.mapping.ListDataFrames(document)


def layer_facts(layer, number):
    data = source(layer)
    if not data:
        return None
    marker = re.search(r"\.gdb(?:[\\/]|$)", data, re.I)
    folder = data[:marker.start() + 4] if marker else os.path.dirname(data)
    return {"index": number, "name": layer.name, "path": data, "source": folder, "dataset": os.path.basename(data),
            "kind": "vector" if layer.isFeatureLayer else "raster", "visible": layer.visible}


class GuizhiArcGISExtension(object):
    def __init__(self):
        global extension
        extension = self
        self.enabled = True
        self.window = None
        self.callback = None
        self.owner = None
        self.folder = None
        self.session = unicode(os.getpid()) + u"-" + uuid.uuid4().hex
        self.map = u""
        self.saved = None
        self.editing = False
        self.attached = False
        self.generation = 0
        self.edit_revision = 0
        self.dirty = True
        self.frame_facts = []
        self.version = ""
        self.publish_ready = False
        self.error = None
        self.running = False
        self.scheduled = False

    def startup(self):
        if not self.folder:
            self.folder = os.path.join(unicode(HOST_ROOT, "mbcs"), self.session)
            os.makedirs(os.path.join(self.folder, "requests"))
            os.makedirs(os.path.join(self.folder, "replies"))
        if not self.window:
            self.owner = KERNEL32.GetCurrentThreadId()
            self._status("starting")
            try:
                install = arcpy.GetInstallInfo()
                self.version = install["Version"]
                code_page = os.path.join(install["InstallDir"], "locale", "codepage", "%s.txt" % KERNEL32.GetACP())
                self.publish_ready = bool(os.environ.get("FORT0") or os.path.isfile(code_page))
                self.window = arcmap_window(self.owner)
                self.callback = SUBCLASS(self._message)
                if not POLL_MESSAGE or not COMCTL32.SetWindowSubclass(self.window, self.callback, MESSAGE_ID, 0):
                    raise ctypes.WinError()
                self._status("waiting")
            except Exception as error:
                self.window = None
                self.error = unicode(error)
                self._status("failed")

    def shutdown(self):
        if self.window:
            COMCTL32.RemoveWindowSubclass(self.window, self.callback, MESSAGE_ID)
        self.window = None
        if self.folder:
            self._status("closed")

    def _changed(self):
        self.dirty = True
        self.generation += 1
        if self.window and not self.running and not self.scheduled:
            self.scheduled = bool(USER32.PostMessageW(self.window, POLL_MESSAGE, MESSAGE_ID, 0))

    def openDocument(self):
        self.startup()
        self.attached = True
        self._changed()

    def newDocument(self):
        self.attached = False
        self._changed()

    def closeDocument(self):
        self.attached = False
        self._changed()

    def mapsChanged(self):
        self._changed()

    def itemAdded(self, item):
        self._changed()

    def itemDeleted(self, item):
        self._changed()

    def itemReordered(self, item, index):
        self._changed()

    def onStartEditing(self):
        self.editing = True
        self._changed()

    def onSaveEdits(self):
        self.edit_revision += 1
        self._changed()

    def onStopEditing(self, save_changes):
        self.editing = False
        if save_changes:
            self.edit_revision += 1
        self._changed()

    def _status(self, state="ready"):
        caption = ctypes.create_unicode_buffer(1024)
        if self.window:
            USER32.GetWindowTextW(self.window, caption, len(caption))
        write(os.path.join(self.folder, "status.json"),
              {"protocol": PROTOCOL, "session": self.session, "pid": os.getpid(), "seen": time.time(),
               "window": self.window, "caption": caption.value,
               "state": state, "map": self.map, "frames": self.frame_facts, "editing": self.editing,
               "generation": self.generation, "edit_revision": self.edit_revision,
               "version": self.version, "publish_ready": self.publish_ready, "error": self.error})

    def _refresh(self, document):
        path = document.filePath
        info = (os.path.getmtime(path), os.path.getsize(path)) if path and os.path.isfile(path) else None
        if canonical(self.map) != canonical(path) or self.saved != info:
            self.map = path if info else u""
            self.saved = info
            self._changed()
        if self.dirty:
            self.frame_facts = [{"index": i, "name": frame.name,
                                 "layers": [fact for fact in (layer_facts(layer, j) for j, layer in enumerate(
                                     arcpy.mapping.ListLayers(document, "", frame))) if fact]}
                                for i, frame in enumerate(frames(document))]
            self.dirty = False

    def _message(self, hwnd, message, wparam, lparam, subclass_id, data):
        if message == POLL_MESSAGE and wparam == MESSAGE_ID:
            self.scheduled = False
            self._poll()
            return 0
        return COMCTL32.DefSubclassProc(hwnd, message, wparam, lparam)

    def _poll(self):
        if self.running or KERNEL32.GetCurrentThreadId() != self.owner:
            return
        self.running = True
        try:
            queue = os.listdir(os.path.join(self.folder, "requests"))
            if not self.attached and not queue:
                self.map, self.frame_facts = u"", []
                return
            saved = (os.path.getmtime(self.map), os.path.getsize(self.map)) if self.map and os.path.isfile(self.map) else None
            if self.dirty or not self.frame_facts or saved != self.saved or queue:
                document = arcpy.mapping.MapDocument("CURRENT")
                self._refresh(document)
                if queue:
                    self._consume(document)
            self.error = None
        except Exception as error:
            self.error = unicode(error)
        finally:
            self.running = False
            try:
                self._status("ready" if self.attached else "waiting")
            except (IOError, OSError):
                pass

    def _consume(self, document):
        queue = os.path.join(self.folder, "requests")
        for name in sorted(os.listdir(queue)):
            if not re.match(r"^[a-f0-9]{32}\.json$", name):
                continue
            path = os.path.join(queue, name)
            reply = os.path.join(self.folder, "replies", name)
            if not os.path.isfile(reply):
                try:
                    request = read(path)
                    result = self._apply(document, request)
                    if self.dirty:
                        self._refresh(document)
                    self._status("ready" if self.attached else "waiting")
                except Exception as error:
                    result = {"status": "failed", "message": unicode(error)}
                write(reply, result)
            os.unlink(path)
            break

    def _apply(self, document, request):
        if (request.get("protocol") != PROTOCOL or request.get("session") != self.session or
                request.get("expires", 0) < time.time()):
            return {"status": "expired"}
        if request.get("action") == "discover":
            self.attached = bool(self.map and self.frame_facts)
            return {"status": "discovered"} if self.attached else {"status": "failed", "message": u"请先打开并保存 MXD"}
        if not self.map or canonical(request.get("map")) != canonical(self.map):
            return {"status": "stale", "message": u"当前工程已切换"}
        if self.editing:
            return {"status": "deferred", "message": u"等待 ArcGIS 完成编辑"}
        index = request["frame"]
        data_frames = frames(document)
        if not 0 <= index < len(data_frames) or request["frame_name"] != data_frames[index].name:
            return {"status": "stale", "message": u"数据框已变化，请重新连接"}
        if request["generation"] != self.generation:
            return {"status": "stale", "message": u"工程已变化，请重试"}
        frame = data_frames[index]
        layers = arcpy.mapping.ListLayers(document, "", frame)
        if request["action"] == "snapshot":
            return self._snapshot(layers, request)
        if request["action"] == "publish":
            if not self.publish_ready:
                return {"status": "deferred", "message": u"等待规智数据视图重新启动"}
            return self._publish(document, frame, layers, request)
        raise ValueError(u"未知 ArcGIS 请求")

    def _snapshot(self, layers, request):
        index = request["layer"]
        if not 0 <= index < len(layers):
            return {"status": "stale", "message": u"图层位置已变化"}
        layer = layers[index]
        facts = layer_facts(layer, index)
        if not facts or facts["kind"] != "vector" or canonical(request["expected"]) != canonical(facts["path"]):
            return {"status": "stale", "message": u"图层数据源已变化"}
        selection = sorted(layer.getSelectionSet()) if request.get("selected_only") else None
        if selection == [] or selection and len(selection) > 100000:
            raise ValueError(u"请选择 1 至 100,000 个要素")
        return {"status": "captured", "layer": facts, "selected_ids": selection,
                "filter": layer.definitionQuery if layer.supports("DEFINITIONQUERY") else "",
                "edit_revision": self.edit_revision, "generation": self.generation}

    def _publish(self, document, frame, layers, request):
        new_path = canonical(request["path"])
        previous = canonical(request.get("previous"))
        by_path = {}
        for layer in layers:
            data = canonical(source(layer))
            if data:
                by_path.setdefault(data, []).append(layer)
        old = by_path.get(previous, []) if previous else []
        existing = by_path.get(new_path, [])
        if len(existing) > 1:
            return {"status": "failed", "message": u"当前工程包含重复的成果图层"}
        if not existing:
            file = request["layer_file"]
            if not os.path.isabs(file) or not file.lower().endswith(".lyr") or os.path.getsize(file) > 4 << 20:
                raise ValueError(u"成果图层文件无效")
            with open(file, "rb") as stream:
                if hashlib.sha256(stream.read()).hexdigest() != request["layer_sha256"]:
                    raise ValueError(u"成果图层文件已变化")
            new_layer = arcpy.mapping.Layer(file)
            if canonical(source(new_layer)) != new_path:
                raise ValueError(u"成果图层文件与校验的数据源不一致")
            new_layer.name = request["name"]
            arcpy.mapping.AddLayer(frame, new_layer, "TOP")
            if not layers:
                frame.extent = new_layer.getExtent()
        added = [layer for layer in arcpy.mapping.ListLayers(document, "", frame) if canonical(source(layer)) == new_path]
        if len(added) != 1:
            raise ValueError(u"ArcGIS 未能添加唯一的成果图层")
        if len(old) == 1:
            old[0].visible = False
        if request.get("persist") and os.path.basename(document.filePath) == u"规智数据视图.mxd":
            document.save()
        arcpy.RefreshTOC()
        arcpy.RefreshActiveView()
        self._changed()
        return {"status": "published", "path": request["path"], "name": request["name"], "previous_hidden": len(old) == 1}


class ConnectGuizhiButton(object):
    def __init__(self):
        self.enabled = True

    def onClick(self):
        if not extension:
            pythonaddins.MessageBox(u"请在自定义菜单的扩展模块中启用规智 ArcGIS 扩展。", u"规智", 0)
            return
        extension.startup()
        extension.attached = True
        extension._changed()
        extension._poll()
