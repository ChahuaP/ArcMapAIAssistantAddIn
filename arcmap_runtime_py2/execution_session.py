# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

try:
    import path_utils
    import arcmap_desktop_selection
except ImportError:
    from . import path_utils
    from . import arcmap_desktop_selection


try:
    unicode
except NameError:
    unicode = str


_ACTIVE_SESSION = None


class ExecutionSession(object):
    """Owns run-scoped outputs and detached layers during workflow execution."""

    def __init__(self):
        self._outputs = []
        self._output_by_step = {}
        self._runtime_layers = {}
        self._add_outputs_to_map = None

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
        try:
            env = getattr(arcpy, "env", None)
            if self._add_outputs_to_map is not None and env is not None:
                env.addOutputsToMap = self._add_outputs_to_map
        finally:
            _ACTIVE_SESSION = None
        return False

    def register_output(self, step_id, path):
        step_id = _text(step_id)
        path = path_utils.to_unicode_path(path)
        if step_id in self._output_by_step:
            raise RuntimeError("Step output was registered twice: %s" % step_id)
        self._output_by_step[step_id] = path
        self._outputs.append((step_id, path))

    def layer_for_output(self, step_id, path):
        step_id = _text(step_id)
        expected = self._output_by_step.get(step_id)
        path = path_utils.to_unicode_path(path)
        if expected is None or _normalize_path(expected) != _normalize_path(path):
            raise RuntimeError("Step output is not registered: %s" % step_id)
        layer = self._runtime_layers.get(step_id)
        if layer is None:
            layer = arcpy.mapping.Layer(path)
            self._runtime_layers[step_id] = layer
        return layer

    def publication_plan(self):
        items = []
        for step_id, path in self._outputs:
            items.append(PublicationItem.capture(path, self._runtime_layers.get(step_id)))
        return PublicationPlan(items)


class PublicationItem(object):
    def __init__(self, path, layer=None, visible=None, selection_oids=None):
        self.path = path_utils.to_unicode_path(path)
        self.layer = layer
        self.visible = visible
        self.selection_oids = selection_oids

    @classmethod
    def capture(cls, path, layer):
        if layer is None:
            return cls(path)
        visible = bool(getattr(layer, "visible", True))
        selection_oids = None
        if bool(getattr(layer, "isFeatureLayer", False)):
            selection_oids = arcmap_desktop_selection.capture_oids(layer)
        return cls(path, layer, visible, selection_oids)

    def record(self):
        return {
            "path": self.path,
            "visible": self.visible,
            "selection_oids": self.selection_oids,
        }

    @classmethod
    def from_record(cls, record):
        return cls(
            record["path"],
            visible=record.get("visible"),
            selection_oids=record.get("selection_oids"),
        )


class PublicationPlan(object):
    def __init__(self, items):
        self.items = list(items)

    @property
    def paths(self):
        return [item.path for item in self.items]

    @property
    def records(self):
        return [item.record() for item in self.items]

    @classmethod
    def from_records(cls, records):
        return cls([PublicationItem.from_record(record) for record in records])


class ExecutionOutcome(object):
    def __init__(self, result, publication_plan):
        self.result = result
        self.publication_plan = publication_plan


def current():
    return _ACTIVE_SESSION


def _normalize_path(path):
    return path_utils.normcase(path_utils.normpath(path))


def _text(value):
    if isinstance(value, unicode):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return unicode(value)
