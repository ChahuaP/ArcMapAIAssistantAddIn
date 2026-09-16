# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy
import uuid

try:
    import path_utils
    import arcmap_desktop_selection
except ImportError:
    from . import path_utils
    from . import arcmap_desktop_selection


try:
    unicode
    string_types = (basestring,)
except NameError:
    unicode = str
    string_types = (str, bytes)


_ACTIVE_SESSION = None


class ExecutionSession(object):
    """Owns run-scoped outputs and detached layers during workflow execution."""

    def __init__(self, map_document=None):
        self._map_document = map_document
        self._outputs = []
        self._output_by_step = {}
        self._runtime_layers = {}
        self._published_layers = []
        self._add_outputs_to_map = None
        self._layer_prefix = u"geopilot_" + unicode(uuid.uuid4()).replace(u"-", u"")[:12]

    def __enter__(self):
        global _ACTIVE_SESSION
        if _ACTIVE_SESSION is not None:
            raise RuntimeError("Nested execution sessions are not supported.")
        env = getattr(arcpy, "env", None)
        if env is not None and hasattr(env, "addOutputsToMap"):
            self._add_outputs_to_map = env.addOutputsToMap
            env.addOutputsToMap = False
        _ACTIVE_SESSION = self
        return self

    def __exit__(self, exc_type, exc, tb):
        global _ACTIVE_SESSION
        failures = []
        try:
            failures.extend(self._delete_runtime_layers())
            if exc_type is not None:
                failures.extend(self._remove_published_layers())
                failures.extend(self._delete_registered_outputs())
        finally:
            try:
                env = getattr(arcpy, "env", None)
                if self._add_outputs_to_map is not None and env is not None:
                    env.addOutputsToMap = self._add_outputs_to_map
            except Exception as restore_error:
                failures.append("restore addOutputsToMap: %s" % restore_error)
            finally:
                _ACTIVE_SESSION = None
        if failures:
            message = "Runtime teardown failed: " + "; ".join(failures)
            if exc is not None:
                setattr(exc, "runtime_teardown_error", message)
                return False
            raise RuntimeError(message)
        return False

    def register_output(self, step_id, path, output_type="feature_class"):
        step_id = _text(step_id)
        path = path_utils.to_unicode_path(path)
        if step_id in self._output_by_step:
            raise RuntimeError("Step output was registered twice: %s" % step_id)
        if output_type not in ("feature_class", "raster", "table", "file"):
            raise RuntimeError("Unsupported runtime output type: %s" % output_type)
        self._output_by_step[step_id] = {"path": path, "type": output_type}
        self._outputs.append((step_id, path))

    def layer_for_output(self, step_id, path):
        step_id = _text(step_id)
        record = self._output_by_step.get(step_id)
        path = path_utils.to_unicode_path(path)
        if record is None or _normalize_path(record["path"]) != _normalize_path(path):
            raise RuntimeError("Step output is not registered: %s" % step_id)
        layer = self._runtime_layers.get(step_id)
        if layer is None:
            name = self._runtime_layer_name(step_id)
            mxd, data_frame = _active_map(self._map_document)
            source_layer = arcpy.mapping.Layer(path)
            source_layer.name = name
            source_layer.visible = False
            arcpy.mapping.AddLayer(data_frame, source_layer, "BOTTOM")
            matches = [
                item for item in arcpy.mapping.ListLayers(mxd, "", data_frame)
                if _text(getattr(item, "name", u"")) == name
            ]
            if len(matches) != 1:
                raise RuntimeError("ArcMap did not create exactly one session-owned layer: %s" % step_id)
            layer = matches[0]
            self._runtime_layers[step_id] = layer
        return layer

    def publish_output(self, step_id):
        """Publish once and verify the actual map before reporting success."""
        record = self._output_by_step[step_id]
        path = record['path']
        mxd, frame = _active_map(self._map_document)
        layer = arcpy.mapping.Layer(path)
        arcpy.mapping.AddLayer(frame, layer, 'AUTO_ARRANGE')
        matches = [item for item in arcpy.mapping.ListLayers(mxd, '', frame)
                   if item.supports('DATASOURCE')
                   and _normalize_path(item.dataSource) == _normalize_path(path)]
        self._published_layers.extend((frame, item) for item in matches)
        if len(matches) != 1:
            raise RuntimeError('Output publication did not produce exactly one layer: %s' % path)
        arcpy.RefreshTOC()
        arcpy.RefreshActiveView()

    def _remove_published_layers(self):
        failures = []
        for frame, layer in reversed(self._published_layers):
            try:
                arcpy.mapping.RemoveLayer(frame, layer)
            except Exception as exc:
                failures.append('rollback map publication: %s' % exc)
        self._published_layers = []
        return failures

    def _runtime_layer_name(self, step_id):
        safe = u"".join(ch if (ch.isalnum() or ch == u"_") else u"_" for ch in step_id)
        return self._layer_prefix + u"_" + safe

    def _delete_runtime_layers(self):
        failures = []
        if not self._runtime_layers:
            return failures
        try:
            _mxd, data_frame = _active_map(self._map_document)
        except Exception as exc:
            data_frame = None
            failures.append("open active map: %s" % exc)
        for step_id, layer in list(self._runtime_layers.items()):
            try:
                if data_frame is None:
                    raise RuntimeError("active data frame is unavailable")
                arcpy.mapping.RemoveLayer(data_frame, layer)
            except Exception as exc:
                failures.append("%s: %s" % (step_id, exc))
        self._runtime_layers.clear()
        return failures

    def _delete_registered_outputs(self):
        """Rollback only exact artifacts registered by this session, never folders."""
        failures = []
        for step_id, path in reversed(self._outputs):
            try:
                exists = getattr(arcpy, "Exists", None)
                if exists is not None and exists(path):
                    arcpy.Delete_management(path)
                elif path_utils.isfile(path):
                    path_utils.remove(path)
                    if path.lower().endswith(u".shp"):
                        root = path[:-4]
                        for extension in (u".dbf", u".shx", u".prj", u".cpg", u".sbn", u".sbx", u".xml"):
                            sidecar = root + extension
                            if path_utils.isfile(sidecar):
                                path_utils.remove(sidecar)
            except Exception as exc:
                failures.append("rollback %s: %s" % (step_id, exc))
        return failures

    def registered_path_for_detached_layer(self, layer):
        """Return the registered path only when *layer* is this session's object."""
        for step_id, runtime_layer in self._runtime_layers.items():
            if layer is runtime_layer:
                return self._output_by_step[step_id]["path"]
        return None

    def canonicalize_runtime_references(self, value):
        """Replace session-owned layer aliases with stable workflow references."""
        references = dict(
            (self._runtime_layer_name(step_id), u"from_step:" + _text(step_id))
            for step_id in self._runtime_layers
        )
        return _canonicalize_runtime_references(value, references)

def _canonicalize_runtime_references(value, references):
    if isinstance(value, dict):
        return dict(
            (key, _canonicalize_runtime_references(item, references))
            for key, item in value.items()
        )
    if isinstance(value, list):
        return [_canonicalize_runtime_references(item, references) for item in value]
    if isinstance(value, tuple):
        return tuple(_canonicalize_runtime_references(item, references) for item in value)
    if isinstance(value, string_types):
        return references.get(value, value)
    return value


class ExecutionOutcome(object):
    def __init__(self, result):
        self.result = result


def current():
    return _ACTIVE_SESSION


def _active_map(mxd=None):
    mxd = mxd if mxd is not None else arcpy.mapping.MapDocument("CURRENT")
    frames = arcpy.mapping.ListDataFrames(mxd)
    if not frames:
        raise RuntimeError("Current MXD has no data frame.")
    frame = mxd.activeDataFrame
    if frame not in frames:
        raise RuntimeError('Current MXD active data frame is unavailable.')
    return mxd, frame


def _normalize_path(path):
    return path_utils.normcase(path_utils.normpath(path))


def _text(value):
    if isinstance(value, unicode):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return unicode(value)
