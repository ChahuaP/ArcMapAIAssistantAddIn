"""Versioned records for a vector task and its evidence."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    id: str
    revision: int
    source: str | None
    layer: str | None
    object_id: str
    operation: str = "vector_inventory"
    operation_version: str = "1.0.0"
    rule_version: str | None = None
    control_source: str | None = None
    control_layer: str | None = None
    project_plan: dict | None = None
    control_plan: dict | None = None
    request: dict | None = None


@dataclass(frozen=True)
class Run:
    id: str
    task_id: str
    task_revision: int
    product_version: str


@dataclass(frozen=True)
class Job:
    id: str
    run_id: str
    operation: str


@dataclass(frozen=True)
class DatasetRevision:
    id: str
    source: str
    layer: str
    snapshot_evidence_id: str
    crs_wkt: str | None


@dataclass(frozen=True)
class ObjectRevision:
    id: str
    object_id: str
    dataset_revision_id: str


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    dataset_revision_id: str | None
    job_id: str
    step: str
    path: str
    sha256: str


@dataclass(frozen=True)
class ClaimRevision:
    id: str
    object_revision_id: str
    job_id: str
    kind: str
    value: int | float
    unit: str
    evidence_ids: tuple[str, ...]
