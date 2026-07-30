"""The sole reproducible model-planning module for GeoPilot runs."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict

from .llm_providers import ChatProvider, create_provider
from .validators import ValidationError, context_hash, prepare_workflow

MODES = ("direct_single", "context_single", "constrained_single", "multi_agent")
TERMINAL_ACTIONS = ("clarify", "reject")
MAX_REVISIONS = 3
WORKFLOW_STEP_CONTRACT = (
    "Every workflow_draft step MUST contain exactly four fields: id (unique string), "
    "operation (exact registered operation id), arguments (object matching parameters_schema), "
    "and reason (string). Use arguments, never parameters. "
)
WORKFLOW_DRAFT_CONTRACT = (
    "workflow_draft MUST contain exactly action, summary, and steps. "
    "action MUST be exactly one of execute, clarify, unsupported, or answer. "
    "Use execute when steps is non-empty; clarify, unsupported, and answer MUST use an empty steps array. "
    + WORKFLOW_STEP_CONTRACT
)


class ContractError(ValueError):
    pass


def digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _object(value, name):
    if not isinstance(value, dict):
        raise ContractError("%s must be an object." % name)
    return value


def task_semantics(value):
    value = _object(value, "task_semantics")
    required = ("goal", "inputs", "constraints", "success_criteria")
    has_required_keys = set(value) == set(required)
    has_goal = isinstance(value.get("goal"), str) and bool(value["goal"].strip())
    has_lists = all(isinstance(value.get(key), list) for key in required[1:])
    if not has_required_keys or not has_goal or not has_lists:
        raise ContractError("task_semantics requires only goal, inputs, constraints, success_criteria.")
    return value


def workflow_draft(value):
    value = _object(value, "workflow_draft")
    valid_keys = set(value) == {"action", "summary", "steps"}
    valid_action = value.get("action") in (
        "execute",
        "clarify",
        "unsupported",
        "answer",
    )
    valid_summary = isinstance(value.get("summary"), str)
    valid_steps = isinstance(value.get("steps"), list)
    if not valid_keys or not valid_action or not valid_summary or not valid_steps:
        raise ContractError("workflow_draft is invalid.")
    return value


def audit_result(value):
    value = _object(value, "audit_result")
    valid_keys = set(value) == {"decision", "issues", "revision_requirements"}
    valid_decision = value.get("decision") in (
        "pass",
        "revise",
        "clarify",
        "reject",
    )
    valid_issues = isinstance(value.get("issues"), list)
    valid_requirements = isinstance(value.get("revision_requirements"), list)
    if not valid_keys or not valid_decision or not valid_issues or not valid_requirements:
        raise ContractError("audit_result is invalid.")
    return value


def _prompt(role: str) -> str:
    contracts = {
        "direct": (
            "Output one workflow_draft object. "
            "Use only registered operation cards and exact parameter schemas. "
            "Never execute ArcPy or emit code. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "context": (
            "Output one workflow_draft object. "
            "Use normalized ArcMap context and registered operation cards only. "
            "Never use raw SQL; where clauses are structured. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "constrained": (
            "Output {task_semantics:{goal,inputs,constraints,success_criteria},"
            "workflow_draft:{action,summary,steps}}. Repair only from structured "
            "validation diagnostics. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "semantic": (
            "Output {task_semantics:{goal,inputs,constraints,success_criteria}}. "
            "Do not produce a workflow or hidden reasoning."
        ),
        "planner": (
            "Output one workflow_draft object. "
            "Use only TaskSemantics, context, capabilities and structured "
            "diagnostics provided; no hidden reasoning. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "auditor": (
            "Output {audit_result:{decision,issues,revision_requirements}}. "
            "Independently judge only TaskSemantics, context, capabilities and "
            "workflow draft; do not plan."
        ),
    }
    return "You are the GeoPilot %s role. Return JSON only. %s" % (role, contracts[role])


class ExperimentRunner:
    """Plans one persisted run; ArcMap execution remains outside this seam."""
    def __init__(self, catalog, store, client: ChatProvider | None = None):
        self.catalog = catalog
        self.store = store
        self.client = client

    def _client(self, role, provider="", model=""):
        if self.client:
            return self.client
        from .llm_providers import load_config

        config = load_config()
        selected_provider = provider or config["primary_provider"]
        selected_model = model or config["primary_model"]
        return create_provider(
            provider_id=selected_provider,
            model_id=selected_model,
        )

    def plan(
        self,
        run_id: str,
        command: str,
        context: Dict[str, Any],
        mode: str,
        provider: str = "",
        model: str = "",
    ) -> Dict[str, Any]:
        self._validate_request(command, context, mode)
        client = self._client("planner", provider, model)
        if model and getattr(client, "model_id", None) != model:
            raise ContractError("requested model is not the configured provider model.")
        actual = {
            "provider": getattr(client, "provider_id", ""),
            "model": getattr(client, "model_id", ""),
        }
        capabilities = list(self.catalog.all_operations())
        visible = [
            item
            for item in capabilities
            if mode != "direct_single"
            or (
                item.get("category") != "map_context"
                and not item["id"].startswith("context.")
            )
        ]
        index = self._cards(visible)
        trace = self.store.run_trace(run_id)
        trace.update({
            "provider": actual["provider"],
            "model": actual["model"],
            "requested_model_config": {"provider": provider or actual["provider"], "model": model or actual["model"]},
            "capability_hash": digest(capabilities),
            "catalog_hash": digest(capabilities),
            "context_hash": context_hash(context),
            "context_snapshot_hash": digest(context),
            "role_models": {role: actual for role in ("direct", "context", "constrained", "semantic", "planner", "auditor")},
        })
        try:
            if mode == "direct_single":
                response = self._call(run_id, client, "direct", {"request": command, "capabilities": index}, trace)
                draft = workflow_draft(response["workflow_draft"])
                version_id = self._record_workflow(trace, draft, "direct")
            elif mode == "context_single":
                response = self._call(
                    run_id,
                    client,
                    "context",
                    {
                        "request": command,
                        "context": context,
                        "capabilities": index,
                    },
                    trace,
                )
                draft = workflow_draft(response["workflow_draft"])
                version_id = self._record_workflow(trace, draft, "context")
            elif mode == "constrained_single":
                reply = self._call(
                    run_id,
                    client,
                    "constrained",
                    {
                        "request": command,
                        "context": context,
                        "capabilities": index,
                    },
                    trace,
                )
                trace["task_semantics"] = task_semantics(reply["task_semantics"])
                draft = workflow_draft(reply["workflow_draft"])
                version_id = self._record_workflow(trace, draft, "constrained")
                return self._single_loop(
                    run_id,
                    client,
                    command,
                    context,
                    index,
                    trace,
                    draft,
                    version_id,
                )
            else:
                semantic_response = self._call(
                    run_id,
                    client,
                    "semantic",
                    {
                        "request": command,
                        "context": context,
                        "capabilities": index,
                    },
                    trace,
                )
                semantics = task_semantics(semantic_response["task_semantics"])
                trace["task_semantics"] = semantics
                planner_response = self._call(
                    run_id,
                    client,
                    "planner",
                    {
                        "task_semantics": semantics,
                        "context": context,
                        "capabilities": index,
                    },
                    trace,
                )
                draft = workflow_draft(planner_response["workflow_draft"])
                version_id = self._record_workflow(trace, draft, "planner")
                return self._multi_loop(
                    run_id,
                    client,
                    client,
                    semantics,
                    context,
                    index,
                    trace,
                    draft,
                    version_id,
                )
            return self._finalize(run_id, draft, context, trace, version_id)
        except Exception as exc:
            self.store.fail_run(run_id, "planning", exc, trace)
            raise

    def _cards(self, operations=None):
        source = operations if operations is not None else self.catalog.all_operations()
        return [
            {
                "id": item["id"],
                "summary": item.get("summary", ""),
                "parameters_schema": item.get("parameters_schema", {}),
                "context_requirements": item.get("context_requirements", {}),
                "side_effects": item.get("side_effects", ""),
            }
            for item in sorted(source, key=lambda item: item["id"])
        ]

    def _single_loop(
        self,
        run_id,
        client,
        command,
        context,
        index,
        trace,
        draft,
        version_id,
    ):
        while True:
            result = self._validation(draft, context, trace, version_id)
            if result["ok"]:
                return self._finalize(
                    run_id,
                    result["workflow"],
                    context,
                    trace,
                    version_id,
                    already_valid=True,
                )
            if trace["counts"]["revisions"] >= MAX_REVISIONS:
                return self._terminal(run_id, "failed", draft, trace, result["diagnostic"])
            trace["counts"]["revisions"] += 1
            self._check_cancel(run_id)
            reply = self._call(
                run_id,
                client,
                "constrained",
                {
                    "request": command,
                    "context": context,
                    "capabilities": index,
                    "task_semantics": trace["task_semantics"],
                    "workflow_draft": draft,
                    "validation": result["diagnostic"],
                },
                trace,
            )
            draft = workflow_draft(reply["workflow_draft"])
            version_id = self._record_workflow(trace, draft, "constrained")

    def _multi_loop(
        self,
        run_id,
        client,
        auditor_client,
        semantics,
        context,
        index,
        trace,
        draft,
        version_id,
    ):
        while True:
            self._check_cancel(run_id)
            audit_response = self._call(
                run_id,
                auditor_client,
                "auditor",
                {
                    "task_semantics": semantics,
                    "context": context,
                    "capabilities": index,
                    "workflow_draft": draft,
                },
                trace,
            )
            audit = audit_result(audit_response["audit_result"])
            trace["audits"].append({
                "version_id": version_id,
                "workflow_hash": digest(draft),
                **audit,
            })
            if audit["decision"] in TERMINAL_ACTIONS:
                return self._terminal(run_id, audit["decision"], draft, trace, audit)
            if audit["decision"] == "revise":
                diagnostic = audit
            else:
                diagnostic = self._validation(draft, context, trace, version_id)
            if isinstance(diagnostic, dict) and diagnostic.get("ok"):
                return self._finalize(
                    run_id,
                    diagnostic["workflow"],
                    context,
                    trace,
                    version_id,
                    already_valid=True,
                )
            if trace["counts"]["revisions"] >= MAX_REVISIONS:
                return self._terminal(run_id, "failed", draft, trace, diagnostic)
            trace["counts"]["revisions"] += 1
            planner_response = self._call(
                run_id,
                client,
                "planner",
                {
                    "task_semantics": semantics,
                    "context": context,
                    "capabilities": index,
                    "workflow_draft": draft,
                    "diagnostic": diagnostic,
                },
                trace,
            )
            draft = workflow_draft(planner_response["workflow_draft"])
            version_id = self._record_workflow(trace, draft, "planner")

    def _call(self, run_id, client, role, payload, trace):
        self._check_cancel(run_id)
        system = _prompt(role)
        stage = {"name": role, "started_at": time.time(), "status": "running"}
        trace["stages"].append(stage)
        turn = {
            "role": role,
            "input_hash": digest(payload),
            "prompt_hash": digest(system),
            "provider": getattr(client, "provider_id", ""),
            "model": getattr(client, "model_id", ""),
            "started_at": stage["started_at"],
        }
        trace["turns"].append(turn)
        try:
            response = _object(
                client.chat_json([
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    },
                ]),
                "model response",
            )
            usage = response.pop("_usage", None)
            turn["output_hash"] = digest(response)
            if usage:
                trace["usage"].append(usage)
            stage["status"] = "succeeded"
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            turn["error"] = {
                "type": error_type,
                "hash": digest({"type": error_type, "message": str(exc)}),
            }
            stage["status"] = "failed"
            raise
        finally:
            finished_at = time.time()
            turn["finished_at"] = finished_at
            stage["finished_at"] = finished_at
            self.store.update_run(run_id, "running", trace=trace)

    def _validation(self, draft, context, trace, version_id):
        try:
            result = {
                "ok": True,
                "workflow": prepare_workflow(draft, self.catalog, context),
            }
        except ValidationError as exc:
            result = {
                "ok": False,
                "diagnostic": {
                    "kind": "deterministic_validation",
                    "message": str(exc),
                },
            }
        validation_entry = {"version_id": version_id, "workflow_hash": digest(draft)}
        validation_entry.update({"ok": True} if result["ok"] else result["diagnostic"])
        trace["validations"].append(validation_entry)
        return result

    @staticmethod
    def _record_workflow(trace, draft, source_role):
        digest_value = digest(draft)
        for version in trace["workflow_versions"]:
            if version["hash"] == digest_value:
                return version["id"]
        version_id = "w%d" % (len(trace["workflow_versions"]) + 1)
        trace["workflow_versions"].append({
            "id": version_id,
            "source_role": source_role,
            "workflow": draft,
            "hash": digest_value,
        })
        return version_id

    def _finalize(
        self,
        run_id,
        draft,
        context,
        trace,
        version_id,
        already_valid=False,
    ):
        validation = (
            {"ok": True, "workflow": draft}
            if already_valid
            else self._validation(draft, context, trace, version_id)
        )
        if not validation["ok"]:
            return self._terminal(run_id, "failed", draft, trace, validation["diagnostic"])
        prepared = validation["workflow"]
        trace["finished_at"] = time.time()
        self.store.update_run(run_id, "planned", workflow=prepared, trace=trace)
        return self.store.get(run_id)

    def _terminal(self, run_id, status, draft, trace, detail):
        trace["terminal"] = {"status": status, "detail": detail}
        self.store.update_run(run_id, status, workflow=draft, trace=trace, result={"terminal": detail})
        return self.store.get(run_id)

    def _check_cancel(self, run_id):
        if self.store.is_cancel_requested(run_id):
            self.store.update_run(run_id, "cancelled")
            raise ContractError("run cancelled cooperatively before next stage.")

    @staticmethod
    def _validate_request(command, context, mode):
        if mode not in MODES or not isinstance(command, str) or not command.strip() or not isinstance(context, dict):
            raise ContractError("invalid run request.")
