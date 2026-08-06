from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .planning_engine import digest, planning_policy
from .validators import prepare_workflow
from .workflow_protocol import workflow_protocol


RESET_SOURCE_COUNT = 14


def validate_reset_source_paths(value: Any) -> List[str]:
    if not isinstance(value, list) or len(value) != RESET_SOURCE_COUNT:
        raise ValueError("formal experiment reset requires exactly 14 source paths.")
    paths: List[str] = []
    normalized_paths = set()
    names = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("formal experiment reset source paths must be strings.")
        path = Path(item.strip())
        if not path.is_absolute() or path.suffix.lower() != ".shp" or not path.is_file():
            raise ValueError("formal experiment reset source path is invalid: %s" % item)
        resolved = str(path.resolve())
        normalized = resolved.lower()
        name = path.stem.lower()
        if normalized in normalized_paths or name in names:
            raise ValueError("formal experiment reset source paths must be unique.")
        paths.append(resolved)
        normalized_paths.add(normalized)
        names.add(name)
    return paths


def reset_workflow(source_paths: List[str]) -> Dict[str, Any]:
    steps = [{
        "id": "clear_layers",
        "operation": "layer.clear_layers",
        "arguments": {},
        "reason": "Remove every layer from the active data frame before the formal experiment.",
    }]
    for index, path in enumerate(source_paths, start=1):
        steps.append({
            "id": "add_source_%02d" % index,
            "operation": "layer.add_layer",
            "arguments": {"path": path},
            "reason": "Load immutable formal experiment source %02d." % index,
        })
    steps.append({
        "id": "list_layers",
        "operation": "context.list_layers",
        "arguments": {},
        "reason": "Capture the reset map layer inventory.",
    })
    return {
        "action": "execute",
        "summary": "Reset ArcMap to the 14 immutable formal experiment source layers.",
        "steps": steps,
    }


class DeterministicResetPlanner:
    def __init__(self, catalog, store, source_paths: List[str]):
        self.catalog = catalog
        self.store = store
        self.source_paths = list(source_paths)

    def plan(self, run_id, command, context, mode, provider, model):
        draft = reset_workflow(self.source_paths)
        workflow = prepare_workflow(draft, self.catalog, context)
        trace = self.store.run_trace(run_id)
        protocol = workflow_protocol()
        trace.update({
            "control": {
                "kind": "formal_experiment_reset",
                "source_paths_hash": digest(self.source_paths),
            },
            "provider": "",
            "model": "",
            "requested_model_config": None,
            "role_models": {},
            "planning_policy": planning_policy(self.catalog, protocol),
        })
        workflow_hash = digest(workflow)
        trace["workflow_versions"].append({
            "id": "w1",
            "hash": workflow_hash,
            "source_role": "experiment_control",
            "workflow": workflow,
        })
        trace["validations"].append({
            "version_id": "w1",
            "workflow_hash": workflow_hash,
            "ok": True,
        })
        return self.store.update_run(
            run_id, "planned", workflow=workflow, trace=trace
        )
