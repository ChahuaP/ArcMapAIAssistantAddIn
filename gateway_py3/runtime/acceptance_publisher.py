"""AcceptancePublisher: independent acceptance + atomic publication (§6.8).

``accept`` independently verifies staged artifacts against the sealed plan's
declared post-conditions; ``publish`` performs a copy-on-write atomic
publication and records a publication receipt. A failed acceptance never
publishes anything (§2.4): the user's formal output location stays clean.

Acceptance checks (§6.8):
- artifact existence and precise identity
- type, fields, record counts, output counts
- coordinate system, units, geometry type and geometry validity
- selection state and map state
- workflow-declared spatial / attribute / business post-conditions
- undeclared side effects
- file or dataset hashes
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from shared_runtime.file_semantics import inspect_file, FileSemanticError

from ..kernel import contracts
from ..kernel.contracts import (
    AuthorizationGrant, IntentSpec, Outcome, VerifiedPlan,
    outcome_succeeded, outcome_failed,
    outcome_paused,
    ACCEPTANCE_FAILED, INFRASTRUCTURE_FAILED, CONTRACT_FAILED,
    PUBLICATION_INDETERMINATE,
)


def sha256_file(path: Path) -> str:
    """Content hash of one artifact file (used for acceptance identity)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AcceptancePublisher:
    """§6.8 independent acceptance + atomic publication.

    Staging and publication are file-system operations; the publisher never
    reaches into ArcMap. ``accept`` returns a report (``details["report"]``);
    ``publish`` requires a passed report and a bound grant.
    """

    # -- §6.8 accept ---------------------------------------------------------

    def accept(self, intent: IntentSpec, plan: VerifiedPlan,
               probe_documents: Any, staged_artifacts: Any,
               acceptance_contract: Any = None) -> Outcome:
        """Accept only independent ArcPy probe documents, never receipts.

        A receipt proves dispatch/execution happened; it is not GIS evidence.
        The probe reopens each sealed staged output in ArcMap and has its own
        content manifest.  Gateway merely checks that this evidence is bound
        to the sealed output identity and the registered staging artifact.
        """
        declared = [output for step in plan.workflow for output in step.declared_outputs]
        file_outputs = [output for output in declared if output.kind != "map_state"]
        map_outputs = [output for output in declared if output.kind == "map_state"]
        probes = probe_documents if isinstance(probe_documents, list) else []
        staged = staged_artifacts if isinstance(staged_artifacts, list) else []
        checks = []
        contract = acceptance_contract if isinstance(acceptance_contract, dict) else {}
        required_proofs = {item.get("proof_id") for item in contract.get("rules", [])
                           if isinstance(item, dict) and item.get("required") is True}
        by_output = {}
        for probe in probes:
            if isinstance(probe, dict) and isinstance(probe.get("output_id"), str):
                if probe["output_id"] in by_output:
                    checks.append({"name": "unique_probe", "ok": False, "detail": probe["output_id"]})
                by_output[probe["output_id"]] = probe
        unit_probes = [probe for probe in probes if isinstance(probe, dict) and probe.get("probe_type") == "unit"]
        staged_by_output = {item.output_id: item for item in staged
                            if isinstance(item, contracts.ArtifactIdentity)}
        has_gdb_outputs = any(item.publication_kind == "file_gdb"
                              for item in staged_by_output.values())
        if has_gdb_outputs and len(unit_probes) != 1:
            checks.append({"name": "unit_probe", "ok": False, "detail": "exactly one unit probe required"})
        if not has_gdb_outputs and unit_probes:
            checks.append({"name": "unit_probe", "ok": False, "detail": "acceptance has no FileGDB unit"})
        expected_ids = set(output.output_id for output in declared)
        if set(by_output) != expected_ids:
            checks.append({"name": "sealed_outputs_probed", "ok": False,
                           "detail": "expected=%s actual=%s" % (sorted(expected_ids), sorted(by_output))})
        expected_staged_ids = set(output.output_id for output in file_outputs)
        if set(staged_by_output) != expected_staged_ids:
            checks.append({"name": "declared_staged_artifacts", "ok": False,
                           "detail": "expected=%s actual=%s" % (sorted(expected_staged_ids), sorted(staged_by_output))})
        for output in file_outputs:
            probe = by_output.get(output.output_id)
            artifact = staged_by_output.get(output.output_id)
            if probe is None or artifact is None:
                continue
            checks.extend(self._check_probe(output, probe, artifact))
        if required_proofs:
            proof_status = {}
            for probe in probes:
                for proof in probe.get("acceptance_proofs", []) if isinstance(probe, dict) else []:
                    if isinstance(proof, dict) and isinstance(proof.get("proof_id"), str):
                        proof_status[proof["proof_id"]] = proof.get("status")
            for proof_id in sorted(required_proofs):
                checks.append({"name": "semantic_proof", "ok": proof_status.get(proof_id) == "Proven",
                               "detail": "%s=%s" % (proof_id, proof_status.get(proof_id, "Unresolved"))})
        for output in map_outputs:
            # Map/layout outputs are accepted through the single semantic
            # proof path: their rule runs through ``probe_contract`` on the
            # runtime side and surfaces here as an ``acceptance_proofs`` entry
            # checked by ``required_proofs`` below.  There is no parallel
            # map-state judgment chain and no receipt-based map check; the only
            # publisher-side check is transport integrity of the probe document.
            probe = by_output.get(output.output_id)
            if probe is None:
                checks.append({"name": "sealed_outputs_probed", "ok": False,
                               "detail": "missing map probe for %s" % output.output_id})
                continue
            canonical = _canonical_json({key: value for key, value in probe.items()
                                         if key != "manifest_digest"})
            checks.append({"name": "map_probe_digest", "ok": probe.get("manifest_digest") ==
                           hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                           "detail": output.output_id})
        if has_gdb_outputs and len(unit_probes) == 1:
            unit = unit_probes[0]
            gdb_items = [item for item in staged_by_output.values()
                         if item.publication_kind == "file_gdb"]
            source_gdbs = {_containing_gdb(Path(item.logical_dataset_path)) for item in gdb_items}
            expected_members = by_output[gdb_items[0].output_id].get("members") if gdb_items else None
            checks.append({"name": "unit_source", "ok": len(source_gdbs) == 1 and Path(unit.get("source_publish_unit_path", "")) in source_gdbs,
                           "detail": str(unit.get("source_publish_unit_path"))})
            checks.append({"name": "unit_manifest", "ok": bool(expected_members) and unit.get("members") == expected_members,
                           "detail": "unit physical manifest"})
        passed = all(check["ok"] for check in checks)
        report = {
            "plan_digest": plan.digest,
            "intent_digest": intent.digest,
            "checks": checks,
            "passed": passed,
            "checked_at": time.time(),
            "probes": probes,
        }
        if not passed:
            return outcome_failed(
                ACCEPTANCE_FAILED, "acceptance", "acceptance_failed",
                "成果验收未通过。",
                details={"report": report},
            )
        return outcome_succeeded(
            "acceptance", "成果验收通过。",
            details={"report": report},
        )

    def _check_probe(self, output: Any, probe: Dict[str, Any], artifact: contracts.ArtifactIdentity) -> List[Dict[str, Any]]:
        checks = []
        canonical = _canonical_json(dict((key, value) for key, value in probe.items()
                                         if key != "manifest_digest"))
        actual_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        checks.append({"name": "probe_digest", "ok": probe.get("manifest_digest") == actual_digest,
                       "detail": output.output_id})
        checks.append({"name": "probe_output_id", "ok": probe.get("output_id") == output.output_id,
                       "detail": output.output_id})
        checks.append({"name": "probe_kind", "ok": probe.get("kind") == output.kind,
                       "detail": str(probe.get("kind"))})
        checks.append({"name": "probe_staged_path", "ok": probe.get("canonical_path") == artifact.logical_dataset_path,
                       "detail": str(probe.get("canonical_path"))})
        checks.append({"name": "probe_artifact_binding", "ok": artifact.output_id == output.output_id,
                       "detail": artifact.output_id})
        checks.append({"name": "probe_exists", "ok": probe.get("exists") is True, "detail": output.output_id})
        checks.append({"name": "probe_members", "ok": bool(probe.get("members")), "detail": output.output_id})
        if output.kind == "file":
            try:
                file_semantics = inspect_file(artifact.logical_dataset_path, output.output_format)
                semantics_ok = probe.get("file_semantics") == file_semantics
            except FileSemanticError as exc:
                file_semantics = {"error": str(exc)}
                semantics_ok = False
            checks.append({"name": "file_semantics", "ok": semantics_ok,
                           "detail": file_semantics})
        observed = dict(probe)
        observed["path"] = probe.get("canonical_path")
        checks.extend(self._check_one_output("output %s" % output.output_id, output, observed))
        return checks

    # -- §6.8 publish --------------------------------------------------------

    def prepare(self, staged_artifacts: Any, acceptance_report: Any,
                grant: AuthorizationGrant, publication_id: str) -> Outcome:
        """Copy-on-write atomic publication of accepted staged artifacts.

        Requires a passed acceptance report and a valid bound grant. Each
        artifact is copied to its final location and hashed; the publication
        receipt carries the hash for later audit (§2.10).

        For state-change operations with no file outputs (e.g. map_change),
        publish records a publication receipt without copying files.
        """
        report = acceptance_report if isinstance(acceptance_report, dict) else {}
        if not report.get("passed"):
            return outcome_failed(
                ACCEPTANCE_FAILED, "publish", "not_accepted",
                "未通过验收的成果不能发布。",
            )
        artifacts = self._resolve_staged(staged_artifacts)
        if not artifacts:
            # No file artifacts to copy. This is only valid for genuine
            # state-change operations (map_change, etc.). If acceptance ran
            # observation-based checks on declared file outputs, then empty
            # staging means the publish pipeline is broken (store_artifact was
            # never called) — fail closed rather than recording a fake success.
            checks = report.get("checks") if isinstance(report.get("checks"), list) else []
            has_observation_checks = any(
                isinstance(c, dict) and c.get("name", "") == "artifact_exists"
                for c in checks
            )
            if has_observation_checks:
                return outcome_failed(
                    ACCEPTANCE_FAILED, "publish", "staging_empty",
                    "计划声明了文件产出但 staging 为空：执行链未接通 store_artifact。",
                )
            # Genuine state-change operation: publication is a receipt-only record.
            publication = {
                "publication_kind": "state_change",
                "publication_id": publication_id,
                "run_id": grant.run_id,
                "grant_id": grant.grant_id,
                "plan_digest": grant.plan_digest,
                "lease_id": grant.lease_id,
                "epoch": grant.lease_epoch,
                "artifacts": [],
                "receipt_timestamp": time.time(),
            }
            return outcome_succeeded(
                "publish", "状态变更已确认。",
                details={"publication": publication},
            )
        if grant.allowed_side_effect_level < 3:
            return outcome_failed(
                contracts.POLICY_DENIED, "publish", "grant_level",
                "发布需要至少 3 级（隔离工作区写入）授权。",
            )
        # Grant must not have expired between authorization and publication.
        if grant.expires_at <= time.time():
            return outcome_failed(
                contracts.POLICY_DENIED, "publish", "grant_expired",
                "授权已过期，拒绝发布。",
            )
        authorized = dict(grant.output_identities)
        destination_roots = {Path(item.destination_publish_unit_path) for item in artifacts}
        source_roots = {Path(item.source_publish_unit_path) for item in artifacts}
        if len(destination_roots) != 1 or len(source_roots) != 1:
            return outcome_failed(CONTRACT_FAILED, "publish", "publication_bundle_required",
                                  "一个运行的全部成果必须属于同一原子发布目录。")
        destination_root = next(iter(destination_roots))
        source_root = next(iter(source_roots))
        items = []
        for artifact in artifacts:
            output_id = artifact.output_id
            destination = artifact.destination_dataset_path
            if not isinstance(output_id, str) or authorized.get(output_id) != destination:
                return outcome_failed(
                    contracts.POLICY_DENIED, "publish", "output_out_of_scope",
                    "成果的 output_id 或最终路径未被授权。",
                )
            probe = next((item for item in report.get("probes", [])
                          if isinstance(item, dict) and item.get("output_id") == output_id), None)
            if not isinstance(probe, dict):
                return outcome_failed(ACCEPTANCE_FAILED, "publish", "probe_missing",
                                      "发布缺少独立验收 probe。")
            if _manifest_for_artifact(artifact) != probe.get("members"):
                return outcome_failed(ACCEPTANCE_FAILED, "publish", "manifest_changed",
                                      "验收后的 staging 成果发生变化。")
            items.append((artifact, probe))
        expected = _bundle_manifest(items, destination_root)
        if destination_root.exists():
            return outcome_failed(CONTRACT_FAILED, "publish", "target_exists_without_prepared",
                                  "正式目标已存在且没有本运行 prepared 事实，拒绝覆盖。")
        temporary = _temporary_sibling(destination_root, publication_id)
        publication = {
            "publication_id": publication_id,
            "run_id": grant.run_id,
            "grant_id": grant.grant_id,
            "plan_digest": grant.plan_digest,
            "lease_id": grant.lease_id,
            "epoch": grant.lease_epoch,
            "publication_kind": "artifact_bundle",
            "artifacts": [{"output_id": artifact.output_id,
                           "source": artifact.logical_dataset_path,
                           "destination": artifact.destination_dataset_path,
                           "destination_publish_unit": artifact.destination_publish_unit_path,
                           "publication_kind": artifact.publication_kind,
                           "kind": artifact.kind,
                           "output_format": artifact.output_format,
                           "semantic_evidence": _semantic_evidence(probe),
                           "acceptance_evidence_hash": probe["manifest_digest"]}
                          for artifact, probe in items],
            "source_unit_path": str(source_root),
            "target_unit_path": str(destination_root), "temporary_unit_path": str(temporary),
            "expected_manifest": expected,
            "receipt_timestamp": time.time(),
        }
        return outcome_succeeded(
            "publish", "成果已准备发布。",
            details={"publication": publication},
        )

    def materialize(self, prepared: Dict[str, Any], staged_artifacts: Any) -> Outcome:
        """Materialize only after the coordinator durably records prepared facts."""
        if prepared.get("publication_kind") == "state_change":
            return outcome_succeeded("publish", "状态变更无需文件物化。")
        temporary = Path(prepared["temporary_unit_path"])
        expected = prepared["expected_manifest"]
        artifacts = self._resolve_staged(staged_artifacts)
        sources = {Path(item.source_publish_unit_path) for item in artifacts}
        if len(sources) != 1:
            return outcome_failed(INFRASTRUCTURE_FAILED, "publish", "prepared_staging_unavailable",
                                  "无法从仍受验收绑定的 staging 恢复发布。")
        if temporary.exists():
            if temporary.is_dir() and _manifest(temporary) == expected:
                return outcome_succeeded("publish", "prepared 临时发布单元已存在。")
            return outcome_failed(INFRASTRUCTURE_FAILED, "publish", "prepared_temp_conflict",
                                  "prepared 临时发布单元已存在但清单不匹配。")
        self._materialize_bundle(artifacts, Path(prepared["target_unit_path"]), temporary, expected)
        return outcome_succeeded("publish", "prepared 临时发布单元已验真。")

    def commit(self, prepared: Dict[str, Any]) -> Outcome:
        if prepared.get("publication_kind") == "state_change":
            receipt = dict(prepared)
            receipt["published_at"] = prepared["receipt_timestamp"]
            return outcome_succeeded("publish", "状态变更已确认。", details={"publication": receipt})
        target = Path(prepared["target_unit_path"])
        temporary = Path(prepared["temporary_unit_path"])
        expected = prepared["expected_manifest"]
        if target.exists():
            if _manifest(target) != expected:
                return outcome_paused(PUBLICATION_INDETERMINATE, "publish", "publication_indeterminate",
                                      "正式目标与 prepared 清单不一致，需人工处理。",
                                      operator_action="inspect prepared publication evidence and resolve target manually",
                                      details={"prepared": prepared})
        else:
            if not temporary.is_dir() or _manifest(temporary) != expected:
                return outcome_failed(INFRASTRUCTURE_FAILED, "publish", "prepared_materialization_missing",
                                      "prepared 临时发布单元丢失或已损坏。")
            os.replace(str(temporary), str(target))
        receipt = dict(prepared)
        receipt["published_at"] = prepared["receipt_timestamp"]
        receipt["manifest"] = expected
        receipt["published_artifacts"] = [
            item.model_dump(mode="json")
            for item in _published_artifact_manifests(prepared, target)
        ]
        return outcome_succeeded("publish", "成果已发布。", details={"publication": receipt})

    def recover(self, prepared: Dict[str, Any], staged_artifacts: Any,
                acceptance_report: Any, grant: AuthorizationGrant) -> Outcome:
        if prepared.get("publication_kind") == "state_change":
            return self.commit(prepared)
        target = Path(prepared["target_unit_path"])
        expected = prepared["expected_manifest"]
        if target.exists():
            return self.commit(prepared)
        temporary = Path(prepared["temporary_unit_path"])
        if not temporary.is_dir() or _manifest(temporary) != expected:
            artifacts = self._resolve_staged(staged_artifacts)
            sources = {Path(item.source_publish_unit_path) for item in artifacts}
            if len(sources) != 1:
                return outcome_failed(INFRASTRUCTURE_FAILED, "publish", "prepared_staging_unavailable",
                                      "无法从仍受验收绑定的 staging 恢复发布。")
            if temporary.exists():
                if temporary.parent != target.parent or not temporary.name.startswith("." + target.name + ".geopilot-"):
                    return outcome_paused(PUBLICATION_INDETERMINATE, "publish", "prepared_temp_path_invalid",
                                          "prepared 临时路径不属于目标发布单元，需人工处理。",
                                          operator_action="inspect prepared publication evidence and resolve target manually",
                                          details={"prepared": prepared})
                shutil.rmtree(temporary)
            materialized = self.materialize(prepared, staged_artifacts)
            if not materialized.succeeded:
                return materialized
        return self.commit(prepared)

    # -- internal ------------------------------------------------------------

    def _check_one_output(self, prefix: str, output: Any,
                          observation: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run the §6.8 checks for one declared output against its observation."""
        checks: List[Dict[str, Any]] = []
        # Fail closed: existence must be affirmed explicitly. A missing
        # ``exists`` field must not default to True (that would let an
        # unverified observation pass acceptance).
        exists = observation.get("exists") is True and bool(observation.get("path"))
        checks.append({
            "name": "artifact_exists", "ok": bool(exists),
            "detail": "%s: path=%s" % (prefix, observation.get("path")),
        })
        kind = observation.get("kind", "")
        if kind == "file":
            return checks
        # Geometry validity requires independent geometry evidence.  A
        # populated ``shapeType`` only names a geometry family; it says
        # nothing about null, empty, or self-intersecting features.
        if output.geometry_type or kind in ("feature_class",):
            geometry = observation.get("geometry")
            evidence = observation.get("geometry_evidence")
            geom_ok = (
                isinstance(evidence, dict)
                and evidence.get("status") == "Proven"
                and evidence.get("geometry_type") == geometry
                and geometry not in ("not_applicable", "Unknown", None)
                and evidence.get("null_count") == 0
                and evidence.get("empty_count") == 0
                and evidence.get("invalid_count") == 0
            )
            checks.append({
                "name": "geometry_integrity", "ok": geom_ok,
                "detail": "%s: geometry_evidence=%s" % (prefix, evidence),
            })
        # Coordinate system (feature/raster outputs).
        if output.coordinate_system or kind in ("feature_class", "raster"):
            crs = observation.get("spatial_reference")
            crs_ok = bool(crs)
            if output.coordinate_system:
                crs_ok = crs_ok and str(output.coordinate_system).lower() in str(crs).lower()
            checks.append({
                "name": "coordinate_system_defined", "ok": crs_ok,
                "detail": "%s: spatial_reference=%s" % (prefix, crs),
            })
        # Fields.
        raw_fields = observation.get("fields") or []
        fields = [item.get("name") if isinstance(item, dict) else item for item in raw_fields]
        fields_ok = bool(fields)
        if output.expected_fields:
            present = set(str(f) for f in fields)
            required = set(output.expected_fields)
            fields_ok = required.issubset(present)
        checks.append({
            "name": "fields_present", "ok": fields_ok,
            "detail": "%s: fields=%s" % (prefix, raw_fields),
        })
        if kind in ("feature_class", "table"):
            specs_ok = bool(raw_fields) and all(
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("type"), str)
                and isinstance(item.get("nullable"), bool)
                and "length" in item and "precision" in item and "scale" in item and "domain" in item
                for item in raw_fields
            )
            checks.append({
                "name": "field_specs", "ok": specs_ok,
                "detail": "%s: field specifications=%s" % (prefix, raw_fields),
            })
        # Record count.
        count = observation.get("feature_count")
        count_ok = isinstance(count, int) and count >= output.min_record_count
        checks.append({
            "name": "record_count", "ok": count_ok,
            "detail": "%s: feature_count=%s (min %d)" % (prefix, count, output.min_record_count),
        })
        if kind in ("feature_class", "table"):
            content = observation.get("record_content")
            content_ok = (
                isinstance(content, dict)
                and content.get("status") == "Proven"
                and isinstance(content.get("record_hash"), str)
                and isinstance(content.get("attribute_hashes"), dict)
            )
            checks.append({
                "name": "record_content_evidence", "ok": content_ok,
                "detail": "%s: record_content=%s" % (prefix, content),
            })
        return checks

    def _resolve_staged(self, staged_artifacts: Any) -> List[Dict[str, Any]]:
        """Normalize the staged-artifact payload passed to publish.

        Accepts a list of dicts with ``path`` / ``name`` keys. There is no
        staging-root scan: publish only the artifacts the store explicitly
        staged for this run (§6.8: never publish another run's staging).
        """
        if not isinstance(staged_artifacts, list):
            return []
        result = []
        for item in staged_artifacts:
            if isinstance(item, contracts.ArtifactIdentity):
                result.append(item)
        return result

    @staticmethod
    def _materialize_bundle(artifacts, target: Path, temporary: Path,
                            expected: List[Dict[str, Any]]) -> None:
        if temporary.exists():
            raise ValueError("prepared temporary path already exists")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.mkdir()
        copied_gdbs = set()
        for artifact in artifacts:
            destination = Path(artifact.destination_dataset_path)
            relative = destination.relative_to(target)
            if artifact.publication_kind == "file_gdb":
                source_gdb = _containing_gdb(Path(artifact.logical_dataset_path))
                destination_gdb = _containing_gdb(destination)
                if source_gdb is None or destination_gdb is None:
                    raise ValueError("FileGDB bundle mapping is invalid")
                relative_gdb = destination_gdb.relative_to(target)
                if relative_gdb not in copied_gdbs:
                    shutil.copytree(source_gdb, temporary / relative_gdb,
                                    ignore=_ignore_arcgis_locks)
                    copied_gdbs.add(relative_gdb)
            else:
                target_file = temporary / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(artifact.logical_dataset_path), target_file)
        if _manifest(temporary) != expected:
            raise ValueError("prepared temporary manifest mismatch")


def _manifest(path: Path) -> List[Dict[str, Any]]:
    if path.is_file():
        return [{"relative_path": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}]
    result = []
    for child in sorted(path.rglob("*")):
        if child.is_file() and not _is_arcgis_lock(child):
            result.append({"relative_path": child.relative_to(path).as_posix(),
                           "size": child.stat().st_size, "sha256": sha256_file(child)})
    return result


def _containing_gdb(path: Path) -> Optional[Path]:
    """Return the concrete FileGDB containing *path*, if one is declared."""
    candidate = path
    while True:
        if candidate.suffix.lower() == ".gdb":
            return candidate
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent


def _manifest_for_artifact(artifact: contracts.ArtifactIdentity) -> List[Dict[str, Any]]:
    source = Path(artifact.logical_dataset_path)
    if artifact.publication_kind == "file_gdb":
        source = _containing_gdb(source)
        if source is None:
            raise ValueError("FileGDB artifact is not inside a .gdb container")
    return _manifest(source)


def _semantic_evidence(probe: Dict[str, Any]) -> Dict[str, Any]:
    """Seal only deterministic, independently observed artifact semantics."""
    keys = (
        "kind", "geometry", "geometry_evidence", "fields", "feature_count",
        "spatial_reference", "extent", "record_content", "file_semantics", "members",
    )
    return {key: probe.get(key) for key in keys if key in probe}


def _published_artifact_manifests(
        prepared: Dict[str, Any], target_root: Path,
) -> List[contracts.PublishedArtifactManifest]:
    actual_bundle = _manifest(target_root)
    if actual_bundle != prepared.get("expected_manifest"):
        raise ValueError("published artifact bundle differs from prepared manifest")
    result = []
    for item in prepared.get("artifacts", ()):
        destination = Path(item["destination"])
        if item["publication_kind"] == "file_gdb":
            unit = _containing_gdb(destination)
            if unit is None or not unit.is_dir():
                raise ValueError("published FileGDB unit is missing")
        elif not destination.is_file():
            raise ValueError("published file artifact is missing")
        document = {
            "output_id": item["output_id"], "kind": item["kind"],
            "output_format": item["output_format"],
            "destination_dataset_path": str(destination),
            "destination_publish_unit_path": item["destination_publish_unit"],
            "publication_kind": item["publication_kind"],
            "members": actual_bundle,
            "semantic_evidence": item["semantic_evidence"],
            "acceptance_evidence_hash": item["acceptance_evidence_hash"],
        }
        document["evidence_hash"] = contracts.digest(document)
        result.append(contracts.PublishedArtifactManifest.model_validate(document))
    return result


def _bundle_manifest(items: List[Tuple[contracts.ArtifactIdentity, Dict[str, Any]]],
                     destination_root: Path) -> List[Dict[str, Any]]:
    """Translate accepted source manifests to the atomic destination bundle."""
    result: Dict[str, Dict[str, Any]] = {}
    copied_gdbs = set()
    for artifact, probe in items:
        destination = Path(artifact.destination_dataset_path)
        members = probe.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError("accepted artifact has no content manifest")
        if artifact.publication_kind == "file_gdb":
            destination_gdb = _containing_gdb(destination)
            if destination_gdb is None:
                raise ValueError("FileGDB destination is not inside a .gdb container")
            prefix = destination_gdb.relative_to(destination_root).as_posix()
            if prefix in copied_gdbs:
                continue
            copied_gdbs.add(prefix)
            for member in members:
                relative = "%s/%s" % (prefix, member["relative_path"])
                result[relative] = {"relative_path": relative,
                                    "size": member["size"], "sha256": member["sha256"]}
        else:
            if len(members) != 1:
                raise ValueError("file artifact must have exactly one manifest member")
            relative = destination.relative_to(destination_root).as_posix()
            member = members[0]
            result[relative] = {"relative_path": relative,
                                "size": member["size"], "sha256": member["sha256"]}
    return [result[key] for key in sorted(result)]


def _is_arcgis_lock(path: Path) -> bool:
    """ArcGIS transient workspace locks are the sole excluded publication files."""
    return path.name.lower().endswith(".lock")


def _ignore_arcgis_locks(directory: str, names: List[str]) -> List[str]:
    return [name for name in names if _is_arcgis_lock(Path(directory) / name)]

def _temporary_sibling(target: Path, publication_id: str) -> Path:
    return target.parent / (".%s.geopilot-%s.prepared" % (target.name, publication_id))
