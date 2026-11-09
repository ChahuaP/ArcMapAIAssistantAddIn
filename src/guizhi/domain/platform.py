
"""Versioned contracts for reading datasets and handing off committed results."""
from hashlib import sha256
import json
from typing import Annotated, BinaryIO, Callable, Literal, Protocol

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from guizhi.domain.capabilities import Hash
from guizhi.domain.skills import Slug, Strict, Title

CONTRACT_VERSION = "1.0.0"
Token = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
Release = Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
Capability = Literal["list_datasets", "fetch_dataset", "publish", "read_receipt"]
ArtifactKind = Literal["export", "boundaries", "map", "table", "report", "package"]


class ConnectorManifest(Strict):
    id: Slug
    title: Title
    version: Release
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    capabilities: set[Capability] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def verifiable_publish(self):
        if "publish" in self.capabilities and "read_receipt" not in self.capabilities:
            raise ValueError("A publisher must support reading its receipt")
        return self


class Scope(Strict):
    region: Title
    purpose: Title


class DatasetVersion(Strict):
    dataset: Token
    revision: Token
    sha256: Hash
    size: int = Field(gt=0, strict=True)


class DatasetQuery(Strict):
    scope: Scope
    limit: int = Field(default=50, ge=1, le=100)
    cursor: Token | None = None


class DatasetPage(Strict):
    datasets: list[DatasetVersion] = Field(max_length=100)
    next_cursor: Token | None = None


class DatasetRead(Strict):
    scope: Scope
    version: DatasetVersion


class EvidenceSelection(Strict):
    run_id: RunId
    kind: ArtifactKind


class ArtifactRef(EvidenceSelection):
    evidence_id: RunId
    sha256: Hash
    size: int = Field(gt=0, strict=True)


class Destination(Strict):
    connector: Slug
    dataset: Token
    scope: Scope
    expected_revision: Token | None = None


class HandoffDraft(Destination):
    evidence: list[EvidenceSelection] = Field(min_length=1, max_length=32)


class HandoffRequest(Destination):
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    evidence: list[ArtifactRef] = Field(min_length=1, max_length=32)
    idempotency_key: Hash

    @model_validator(mode="after")
    def verify_key(self):
        content = self.model_dump(mode="json", exclude={"idempotency_key"})
        if self.idempotency_key != handoff_key(content):
            raise ValueError("Idempotency key does not match the handoff")
        return self


def handoff_key(content):
    evidence = content["evidence"]
    keys = {(item["run_id"], item["kind"]) for item in evidence}
    if len(keys) != len(evidence):
        raise ValueError("Handoff references must be distinct")
    ordered = sorted(evidence, key=lambda item: (item["run_id"], item["kind"]))
    body = json.dumps({**content, "evidence": ordered}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(body.encode("ascii")).hexdigest()


class PublishReceipt(Strict):
    connector: Slug
    idempotency_key: Hash
    observed_at: AwareDatetime
    status: Literal["accepted", "published", "rejected", "unknown"]
    remote_version: DatasetVersion | None = None
    detail: Title | None = None

    @model_validator(mode="after")
    def confirmed_version(self):
        if (self.status == "published") != (self.remote_version is not None):
            raise ValueError("Only confirmed publication carries a remote dataset version")
        if self.status in {"rejected", "unknown"} and not self.detail:
            raise ValueError("A rejected or unknown receipt requires a reason")
        return self


class PlatformConnector(Protocol):
    manifest: ConnectorManifest

    def list_datasets(self, request: DatasetQuery) -> DatasetPage: ...

    def fetch_dataset(self, request: DatasetRead, destination: BinaryIO) -> DatasetVersion:
        """Write the requested snapshot and return its actual version and checksum."""
        ...

    def publish(self, request: HandoffRequest, open_artifact: Callable[[ArtifactRef], BinaryIO]) -> PublishReceipt:
        """Use the key to deduplicate writes. A missing expected revision permits creation only."""
        ...

    def read_receipt(self, idempotency_key: Hash) -> PublishReceipt:
        """Confirm an accepted or unknown result before considering another publish."""
        ...
