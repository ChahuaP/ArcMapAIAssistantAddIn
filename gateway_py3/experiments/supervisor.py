"""ExperimentSupervisor: the sole entry point for formal experiments (§10).

Target architecture stage F: the production pipeline must be offline-verified
before real experiments run. This supervisor enforces the invariants that keep
experiment results honest given what is actually implemented today:

1. **Only through the Kernel.** ``run_pair`` builds two experiment-tagged
   ``RequestEnvelope`` values and delegates both to ``GeoPilotKernel.submit``.
   No planning logic is duplicated here.
2. **Experiment-only MiniMax lock.** Both experimental arms bind every role to
   MiniMax-M3.  This constraint belongs to the Chapter 3 contract and does not
   restrict the provider-neutral production architecture.
3. **Complete audit evidence.** After both planning arms are sealed, the
   supervisor exports their complete journals to a fresh campaign directory.
4. **Immutable campaign directory.** ``begin_campaign`` refuses to reuse an
   existing directory so historical data can never mix into a new campaign.

The supervisor does not inject or reach around a planner: the Kernel freezes
the G2 baseline and plans both arms from those persisted facts.

The runtime gate is the only formal path. Offline verification uses normal
unit tests and never invokes this module.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..kernel.contracts import (AuthorizationDecision, CallerIdentity, ExperimentSpec,
                                RequestEnvelope, SideEffectScope, TargetSelector)
from ..kernel.coordinator import ExperimentKernelPort, GeoPilotKernel
from ..model_runtime.adapters.minimax import MINIMAX_MODEL, MINIMAX_PROVIDER
from ..model_runtime.contracts import AGENT_ROLES
from ..paths import data_dir


class ExperimentSupervisorError(RuntimeError):
    pass


class ExperimentSupervisor:
    """Sole entry point for formal experiments (§10 stage F).

    Construct only with the production Kernel. Its read-only runtime identity
    proves that this experiment uses the locked MiniMax runtime; callers cannot
    inject an adapter.
    """

    def __init__(self, kernel: GeoPilotKernel,
                 campaign_root: Optional[Path] = None):
        self._port: ExperimentKernelPort = kernel.experiment_port()
        self.campaign_root = Path(campaign_root) if campaign_root is not None else data_dir() / "experiments"
        self._campaign: Optional[str] = None

    def _require_formal_runtime(self) -> None:
        identity = self._port.runtime_identity
        endpoint_fingerprints = set()
        for role in AGENT_ROLES:
            binding = identity.get(role)
            if not isinstance(binding, dict) or (
                    binding.get("provider"), binding.get("model")
            ) != (MINIMAX_PROVIDER, MINIMAX_MODEL):
                raise ExperimentSupervisorError(
                    "formal experiments require minimax/MiniMax-M3 for role %s." % role
                )
            endpoint = binding.get("endpoint_fingerprint")
            if not isinstance(endpoint, str) or not endpoint:
                raise ExperimentSupervisorError(
                    "formal experiment role has no endpoint fingerprint: %s." % role
                )
            endpoint_fingerprints.add(endpoint)
        if len(endpoint_fingerprints) != 1:
            raise ExperimentSupervisorError(
                "formal experiment roles must use one frozen MiniMax endpoint."
            )

    def _formal_model_plan(self) -> Dict[str, Any]:
        """Freeze the already-verified formal runtime plan into each arm."""
        self._require_formal_runtime()
        identity = self._port.runtime_identity
        return {
            role: {
                "connection_id": identity[role]["connection_id"],
                "model_id": identity[role]["model"],
                "role": role,
                "temperature": identity[role]["parameters"]["temperature"],
                "max_output_tokens": identity[role]["parameters"]["max_output_tokens"],
                "budget_policy": identity[role]["token_plan"],
            }
            for role in AGENT_ROLES
        }

    def _formal_binding_summary(self) -> Dict[str, Any]:
        self._require_formal_runtime()
        return self._formal_binding_evidence()

    @property
    def runtime_identity(self) -> Dict[str, Any]:
        self._require_formal_runtime()
        return self._formal_binding_evidence()

    def _formal_binding_evidence(self) -> Dict[str, Dict[str, Any]]:
        """Return only the canonical persisted binding-evidence contract.

        Runtime implementation details such as adapter class names prove
        neither provider identity nor experiment reproducibility and must not
        become part of a sealed experiment request or its provenance.
        """
        fields = (
            "connection_id", "provider", "model", "endpoint_fingerprint",
            "deployment_fingerprint", "credential_ref", "role", "parameters",
            "token_plan",
        )
        identity = self._port.runtime_identity
        return {role: {field: identity[role][field] for field in fields}
                for role in AGENT_ROLES}

    def begin_campaign(self, name: str) -> Path:
        """Open an experiment campaign directory and freeze its provenance.

        A campaign directory must be fresh: reusing one would mix historical
        data into the frozen provenance. ``exist_ok=False`` refuses to clobber.
        """
        candidate = Path(name)
        if not name or candidate.is_absolute() or candidate.name != name or name in (".", ".."):
            raise ExperimentSupervisorError("campaign name must be one relative path segment.")
        self.campaign_root.mkdir(parents=True, exist_ok=True)
        campaign_dir = self.campaign_root / name
        if campaign_dir.exists():
            raise FileExistsError(str(campaign_dir))
        initializing_dir = self.campaign_root / (".initializing-%s" % uuid.uuid4())
        initializing_dir.mkdir(exist_ok=False)
        try:
            self._freeze_provenance(initializing_dir)
            initializing_dir.rename(campaign_dir)
        except Exception:
            shutil.rmtree(initializing_dir)
            raise
        self._campaign = name
        return campaign_dir

    def export_evidence(self, run_id: str) -> Path:
        """Export the complete journal for one run to the campaign directory.

        Writes ``<campaign>/<run_id>/journal.json`` via the kernel's
        ``export_run_journal`` — the supervisor never reaches into the store.
        """
        if self._campaign is None:
            raise ExperimentSupervisorError("begin_campaign before export_evidence.")
        export = dict(self._port.export_run_journal(run_id))
        calls = export.get("model_calls") or []
        experiment = (export.get("request_envelope") or {}).get("experiment")
        if not isinstance(experiment, dict) or any(
                (call.get("provider"), call.get("model")) != (MINIMAX_PROVIDER, MINIMAX_MODEL) or
                not all((call.get("ledger") or {}).get(field) for field in (
                    "connection_id", "endpoint_fingerprint", "deployment_fingerprint",
                    "credential_ref", "role", "parameters", "token_plan",
                ))
                for call in calls):
            raise ExperimentSupervisorError("journal model evidence violates the formal MiniMax lock.")
        export["exported_at"] = time.time()
        export["runtime_identity"] = self._formal_binding_evidence()
        out_dir = self.campaign_root / self._campaign / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "journal.json"
        out_file.write_text(
            json.dumps(export, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return out_file

    def run_pair(self, task: str, seed: int, *, target_selector: TargetSelector,
                 provider: str, model: str, inputs=(), outputs=(),
                 session_ids=None, prepare_arm=None, pair_id: Optional[str] = None,
                 resume_runs=None, arm_artifact_roots=None) -> Dict[str, Any]:
        """Submit the two immutable arms through the sole Kernel lifecycle."""
        if provider != MINIMAX_PROVIDER or model != MINIMAX_MODEL:
            raise ExperimentSupervisorError("formal experiments require --provider minimax --model MiniMax-M3.")
        if not isinstance(target_selector, TargetSelector):
            raise ExperimentSupervisorError("formal experiments require an explicit TargetSelector.")
        self._require_formal_runtime()
        if self._campaign is None:
            self.begin_campaign("pair-%s" % uuid.uuid4())
        if prepare_arm is None:
            raise ExperimentSupervisorError("formal runtime pairs require an ArcMap initial-state preparation callback.")
        if not isinstance(arm_artifact_roots, dict) or set(arm_artifact_roots) != {"g2", "g3"}:
            raise ExperimentSupervisorError("formal runtime pairs require one server-owned artifact root per arm.")
        resume_runs = {} if resume_runs is None else dict(resume_runs)
        if not set(resume_runs).issubset({"g2", "g3"}):
            raise ExperimentSupervisorError("resume_runs contains an unknown experiment arm.")
        pair_id = pair_id or str(uuid.uuid4())
        caller = CallerIdentity(user_id="experiment-supervisor", tenant_id="experiment",
                                role="operator", client_kind="experiment")
        selected_sessions = tuple(session_ids) if session_ids is not None else (str(uuid.uuid4()), str(uuid.uuid4()))
        if len(selected_sessions) != 2:
            raise ExperimentSupervisorError("formal pair requires one session per arm.")

        def envelope(arm: str, session_id: str) -> RequestEnvelope:
            return RequestEnvelope(session_id=session_id, request_id=str(uuid.uuid4()),
                text=task, caller=caller, execute=True,
                side_effects=SideEffectScope(level=3),
                inputs=tuple(inputs), outputs=tuple(outputs), target_selector=target_selector,
                model_plan=self._formal_model_plan(),
                model_binding_summary=self._formal_binding_summary(),
                experiment=ExperimentSpec(pair_id=pair_id, arm=arm, seed=seed,
                                          provider=provider, model=model,
                                          artifact_root=str(arm_artifact_roots[arm])))

        def execute_if_authorization_required(view):
            if view.stage != "authorization_required":
                return view
            if view.plan is None:
                raise ExperimentSupervisorError("authorization_required without a sealed plan.")
            return self._port.decide(AuthorizationDecision(
                decision_id=str(uuid.uuid4()), run_id=view.run_id, approved=True, plan_digest=view.plan.digest,
                approved_scope=view.plan.authorization_scope(),
            ))

        if "g2" not in resume_runs:
            prepare_arm("g2")
            g2_view = self._port.submit(envelope("g2", selected_sessions[0]))
            g2 = execute_if_authorization_required(self._await_ready(g2_view.run_id))
        else:
            prepare_arm("g2")
            g2 = self._port.inspect(resume_runs["g2"])
            if g2.outcome is None or g2.outcome.kind == "QuotaStopped":
                g2 = execute_if_authorization_required(self._await_ready(
                    self._port.resume_quota(g2.run_id).run_id))
            elif g2.outcome.kind != "Succeeded":
                raise ExperimentSupervisorError("G2 continuation is neither succeeded nor quota-stopped.")
            g2_view = g2
            journal = self._port.export_run_journal(g2.run_id)
            if (journal.get("request_envelope") or {}).get("experiment", {}).get("pair_id") != pair_id:
                raise ExperimentSupervisorError("quota continuation G2 run does not belong to this pair.")
        if g2.outcome is not None and g2.outcome.kind != "Succeeded":
            self.export_evidence(g2_view.run_id)
            return self._pair_failure(pair_id, provider, model, g2, None)
        if not any(event["kind"] == "experiment_baseline_frozen" for event in g2.events):
            raise ExperimentSupervisorError("G2 did not freeze a formal baseline.")
        prepare_arm("g3")
        if "g3" in resume_runs:
            g3 = self._port.inspect(resume_runs["g3"])
            if g3.outcome is not None and g3.outcome.kind != "QuotaStopped":
                raise ExperimentSupervisorError("G3 continuation is not quota-stopped.")
            g3 = execute_if_authorization_required(self._await_ready(
                self._port.resume_quota(g3.run_id).run_id))
            g3_view = g3
        else:
            g3_view = self._port.submit(envelope("g3", selected_sessions[1]))
            g3 = execute_if_authorization_required(self._await_ready(g3_view.run_id))
        g2 = self._port.inspect(g2_view.run_id)
        g2_facts = next((e["payload"] for e in g2.events if e["kind"] == "plan_verified"), {})
        g3_facts = next((e["payload"] for e in g3.events if e["kind"] == "plan_verified"), {})
        baseline = self._port.export_run_journal(g2_view.run_id).get("experiment_baseline") or {}
        task_digest = baseline.get("task_contract")
        from ..kernel.contracts import digest
        task_digest = digest(task_digest) if task_digest is not None else None
        try:
            physical_pair_valid = (
                str(arm_artifact_roots["g2"]) != str(arm_artifact_roots["g3"]) and
                g2.plan is not None and g3.plan is not None and
                g2.plan.experiment_output_signature(str(arm_artifact_roots["g2"])) ==
                g3.plan.experiment_output_signature(str(arm_artifact_roots["g3"]))
            )
        except ValueError:
            physical_pair_valid = False
        pair_valid = (g2.plan is not None and g3.plan is not None and
                      physical_pair_valid and
                      g2_facts.get("pair_id") == pair_id == g3_facts.get("pair_id") and
                      g2_facts.get("topology_signature") == g3_facts.get("topology_signature") and
                      not g2_facts.get("auditor_enabled") and bool(g3_facts.get("auditor_enabled")) and
                      (g2_facts.get("provider"), g2_facts.get("model")) == (provider, model) == (g3_facts.get("provider"), g3_facts.get("model")) and
                      baseline.get("intent_digest") == g2.plan.intent_digest == g3.plan.intent_digest and
                      baseline.get("context_digest") == g2.plan.context_digest == g3.plan.context_digest and
                      baseline.get("capability_digest") == g2.plan.capability_digest == g3.plan.capability_digest and
                      baseline.get("baseline_digest") == g2_facts.get("baseline_digest") == g3_facts.get("baseline_digest") and
                      task_digest == g2_facts.get("task_contract_digest") == g3_facts.get("task_contract_digest"))
        self.export_evidence(g2_view.run_id)
        self.export_evidence(g3_view.run_id)
        completed = all(view.outcome is not None and view.outcome.kind == "Succeeded" for view in (g2, g3))
        return {"pair_valid": bool(pair_valid and completed),
                "pair_id": pair_id, "provider": provider, "model": model,
                "g2_run_id": g2_view.run_id, "g3_run_id": g3_view.run_id,
                "g2_stage": g2.stage, "g3_stage": g3.stage,
                "g2_outcome": g2.outcome.model_dump(mode="json") if g2.outcome else None,
                "g3_outcome": g3.outcome.model_dump(mode="json") if g3.outcome else None}

    @staticmethod
    def _pair_failure(pair_id, provider, model, g2, g3):
        return {"pair_valid": False, "pair_id": pair_id,
                "provider": provider, "model": model,
                "g2_run_id": g2.run_id if g2 else None,
                "g3_run_id": g3.run_id if g3 else None,
                "g2_stage": g2.stage if g2 else None, "g3_stage": g3.stage if g3 else None,
                "g2_outcome": g2.outcome.model_dump(mode="json") if g2 and g2.outcome else None,
                "g3_outcome": g3.outcome.model_dump(mode="json") if g3 and g3.outcome else None}

    def _await_run(self, run_id: str, *event_kinds):
        view = self._port.await_progress(run_id, event_kinds, timeout=600.0)
        if view.outcome is not None or any(event["kind"] in event_kinds for event in view.events):
            return view
        raise ExperimentSupervisorError("formal run did not reach %s" % ", ".join(event_kinds))

    def _await_ready(self, run_id: str):
        view = self._await_run(run_id, "authorization_required", "authorization_auto")
        if view.stage == "authorization_required":
            return view
        return self._port.await_progress(run_id, (), timeout=600.0)

    def _freeze_provenance(self, campaign_dir: Path) -> None:
        """Record the git working-tree provenance for this campaign.

        Provenance is required for a formal campaign (§10): if it cannot be
        captured, the campaign cannot be trusted. Fail loudly rather than ship
        a campaign with missing provenance.
        """
        from experiments.synthetic_city.source_provenance import repository_state
        repo_root = Path(__file__).resolve().parents[2]
        repository = repository_state(repo_root)
        provenance = {
            "repository": str(repo_root),
            "repository_state": repository.as_dict(),
            "model_lock": {"provider": MINIMAX_PROVIDER, "model": MINIMAX_MODEL},
        }
        (campaign_dir / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
