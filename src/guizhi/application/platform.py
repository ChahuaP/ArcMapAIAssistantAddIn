
"""Prepare verified local evidence for a future platform connector."""
from guizhi.domain.platform import (CONTRACT_VERSION, ArtifactRef, ConnectorManifest, DatasetPage,
                                   DatasetQuery, DatasetRead, HandoffDraft, HandoffRequest, PublishReceipt, handoff_key)
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.workspace import Workspace


def contract():
    models = (ConnectorManifest, DatasetQuery, DatasetPage, DatasetRead, HandoffDraft, HandoffRequest, PublishReceipt)
    return {"contract_version": CONTRACT_VERSION, "status": "unconfigured",
            "schemas": {item.__name__: item.model_json_schema() for item in models}}


def prepare(root, value):
    draft = HandoffDraft.model_validate(value)
    workspace = Workspace(root)
    artifacts = []
    for item in draft.evidence:
        path = read_artifact(workspace, item.run_id, item.kind)
        evidence = workspace.read(item.run_id)["result"]["evidence"]
        ref = next(ref for ref in evidence if ref["step"] == item.kind)
        artifacts.append(ArtifactRef(run_id=item.run_id, kind=item.kind, evidence_id=ref["id"],
                                     sha256=ref["sha256"], size=path.stat().st_size))
    content = {**draft.model_dump(mode="json", exclude={"evidence"}), "contract_version": CONTRACT_VERSION,
               "evidence": [item.model_dump(mode="json") for item in sorted(artifacts, key=lambda r: (r.run_id, r.kind))]}
    return HandoffRequest(**content, idempotency_key=handoff_key(content)).model_dump(mode="json")
