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

Real model calls are gated behind ``dry_run=False``. The default ``dry_run=True``
honors §10 stage F: the refactor phase forbids real experiments until the
production architecture passes offline verification.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..kernel.contracts import CallerIdentity, ExperimentSpec, RequestEnvelope, TargetSelector
from ..kernel.coordinator import GeoPilotKernel
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
        self.kernel = kernel
        self.campaign_root = Path(campaign_root) if campaign_root is not None else data_dir() / "experiments"
        self._campaign: Optional[str] = None

    def _require_formal_runtime(self) -> None:
        identity = self.kernel.runtime_identity
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
        export = dict(self.kernel.export_run_journal(run_id))
        calls = export.get("model_calls") or []
        experiment = (export.get("request_envelope") or {}).get("experiment")
        if not isinstance(experiment, dict) or not calls or any(
                (call.get("provider"), call.get("model")) != (MINIMAX_PROVIDER, MINIMAX_MODEL) or
                not all((call.get("ledger") or {}).get(field) for field in (
                    "connection_id", "endpoint_fingerprint", "deployment_fingerprint",
                    "credential_ref", "role", "parameters", "token_plan",
                ))
                for call in calls):
            raise ExperimentSupervisorError("journal model evidence violates the formal MiniMax lock.")
        export["exported_at"] = time.time()
        export["runtime_identity"] = self.kernel.runtime_identity
        out_dir = self.campaign_root / self._campaign / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "journal.json"
        out_file.write_text(
            json.dumps(export, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return out_file

    def run_pair(self, case: str, seed: int, *, target_selector: TargetSelector,
                 provider: str, model: str, inputs=(), dry_run: bool = True) -> Dict[str, Any]:
        """Submit the two immutable arms through the sole Kernel lifecycle."""
        if provider != MINIMAX_PROVIDER or model != MINIMAX_MODEL:
            raise ExperimentSupervisorError("formal experiments require --provider minimax --model MiniMax-M3.")
        if not isinstance(target_selector, TargetSelector):
            raise ExperimentSupervisorError("formal experiments require an explicit TargetSelector.")
        if dry_run:
            return {"campaign_contract_valid": "not_evaluated", "pair_valid": "not_evaluated", "dry_run": True, "case": case, "seed": seed,
                    "provider": provider, "model": model, "arm": ("g2", "g3")}
        self._require_formal_runtime()
        if self._campaign is None:
            self.begin_campaign("pair-%s" % uuid.uuid4())
        pair_id = str(uuid.uuid4())
        caller = CallerIdentity(user_id="experiment-supervisor", tenant_id="experiment",
                                role="operator", client_kind="experiment")
        def envelope(arm: str) -> RequestEnvelope:
            return RequestEnvelope(session_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()),
                text=case, caller=caller, execute=False,
                inputs=tuple(inputs), target_selector=target_selector,
                experiment=ExperimentSpec(pair_id=pair_id, arm=arm, seed=seed,
                                          provider=provider, model=model))
        g2_view = self.kernel.submit(envelope("g2"))
        g2 = self._wait_for(g2_view.run_id, "plan_verified")
        if not any(event["kind"] == "experiment_baseline_frozen" for event in g2.events):
            raise ExperimentSupervisorError("G2 did not freeze a formal baseline.")
        g3_view = self.kernel.submit(envelope("g3"))
        g3 = self._wait_for(g3_view.run_id, "plan_verified")
        g2 = self._wait_for(g2_view.run_id, "plan_verified")
        g2_facts = next((e["payload"] for e in g2.events if e["kind"] == "plan_verified"), {})
        g3_facts = next((e["payload"] for e in g3.events if e["kind"] == "plan_verified"), {})
        baseline = self.kernel.export_run_journal(g2_view.run_id).get("experiment_baseline") or {}
        task_digest = baseline.get("task_contract")
        from ..kernel.contracts import digest
        task_digest = digest(task_digest) if task_digest is not None else None
        pair_valid = (g2.plan is not None and g3.plan is not None and
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
        return {"pair_valid": pair_valid, "pair_id": pair_id, "phase": "planning_gate",
                "provider": provider, "model": model, "g2_run_id": g2_view.run_id, "g3_run_id": g3_view.run_id}

    def _wait_for(self, run_id: str, event_kind: str):
        deadline = time.time() + 30
        while time.time() < deadline:
            view = self.kernel.inspect(run_id)
            if any(event["kind"] == event_kind for event in view.events):
                return view
            if view.outcome is not None:
                return view
            time.sleep(0.01)
        raise ExperimentSupervisorError("formal run did not reach %s" % event_kind)

    def _freeze_provenance(self, campaign_dir: Path) -> None:
        """Record the git working-tree provenance for this campaign.

        Provenance is required for a formal campaign (§10): if it cannot be
        captured, the campaign cannot be trusted. Fail loudly rather than ship
        a campaign with missing provenance.
        """
        from experiments.synthetic_city.source_provenance import repository_state
        repo_root = Path(__file__).resolve().parents[2]
        head, clean, diff = repository_state(repo_root)
        provenance = {
            "repository": str(repo_root),
            "head": head,
            "clean": clean,
            "diff": diff,
            "model_lock": {"provider": MINIMAX_PROVIDER, "model": MINIMAX_MODEL},
        }
        (campaign_dir / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
