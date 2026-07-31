"""The sole reproducible model-planning module for GeoPilot runs."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict

from .llm_providers import create_provider
from .validators import ValidationError, context_hash, prepare_workflow
from .workflow_protocol import workflow_protocol

MODES = ("direct_single", "context_single", "constrained_single", "multi_agent")
TERMINAL_ACTIONS = ("clarify", "reject")
MAX_VALIDATION_REVISIONS = 3
MAX_AUDIT_REVISIONS = 3
MAX_RESPONSE_CONTRACT_REVISIONS = 2
WORKFLOW_STEP_CONTRACT = (
    "Every workflow_draft step MUST contain exactly four fields: id (unique string), "
    "operation (exact registered operation id), arguments (object matching parameters_schema), "
    "and reason (string). Use arguments, never parameters. "
)
WORKFLOW_DRAFT_CONTRACT = (
    "workflow_draft MUST contain exactly action, summary, and steps. "
    "action MUST be exactly execute and steps MUST be a non-empty array. "
    + WORKFLOW_STEP_CONTRACT
)
TASK_SEMANTICS_CONTRACT = (
    "task_semantics MUST contain exactly four fields: goal (non-empty string), "
    "inputs (array), constraints (array), and success_criteria (array). "
)
AUDIT_RESULT_CONTRACT = (
    "audit_result MUST contain exactly three fields: decision (exactly pass, revise, clarify, or reject), "
    "issues (array of strings), and revision_requirements (array of strings). "
)


class ContractError(ValueError):
    pass


@dataclass
class _RepairResult:
    row: Dict[str, Any] | None = None
    draft: Dict[str, Any] | None = None
    version_id: str = ""


def digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def planning_policy(catalog, protocol: Dict[str, Any] | None = None) -> Dict[str, Any]:
    protocol = workflow_protocol() if protocol is None else protocol
    return {
        "validation_revisions": MAX_VALIDATION_REVISIONS,
        "audit_revisions": MAX_AUDIT_REVISIONS,
        "response_contract_revisions": MAX_RESPONSE_CONTRACT_REVISIONS,
        "workflow_protocol": protocol,
        "protocol_hash": digest(protocol),
        "catalog_hash": digest(list(catalog.all_operations())),
    }


def _object(value, name):
    if not isinstance(value, dict):
        raise ContractError("%s must be an object." % name)
    return value


def _exact_keys(value, name, keys):
    value = _object(value, name)
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing fields: %s" % ", ".join(missing))
        if unexpected:
            details.append("unexpected fields: %s" % ", ".join(unexpected))
        raise ContractError("%s has invalid fields (%s)." % (name, "; ".join(details)))
    return value


def task_semantics(value):
    value = _exact_keys(
        value,
        "task_semantics",
        ("goal", "inputs", "constraints", "success_criteria"),
    )
    required = ("goal", "inputs", "constraints", "success_criteria")
    has_goal = isinstance(value.get("goal"), str) and bool(value["goal"].strip())
    has_lists = all(isinstance(value.get(key), list) for key in required[1:])
    if not has_goal or not has_lists:
        raise ContractError(
            "task_semantics requires a non-empty goal and array inputs, constraints, "
            "success_criteria."
        )
    return value


def workflow_draft(value):
    value = _exact_keys(value, "workflow_draft", ("action", "summary", "steps"))
    valid_action = value.get("action") == "execute"
    valid_summary = isinstance(value.get("summary"), str)
    valid_steps = isinstance(value.get("steps"), list) and bool(value["steps"])
    if not valid_action or not valid_summary or not valid_steps:
        raise ContractError("workflow_draft is invalid.")
    step_ids = set()
    for index, step in enumerate(value["steps"]):
        name = "workflow_draft.steps[%d]" % index
        step = _exact_keys(step, name, ("id", "operation", "arguments", "reason"))
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ContractError("%s.id must be a non-empty string." % name)
        if step_id in step_ids:
            raise ContractError("%s.id must be unique." % name)
        step_ids.add(step_id)
        if not isinstance(step.get("operation"), str) or not step["operation"].strip():
            raise ContractError("%s.operation must be a non-empty string." % name)
        if not isinstance(step.get("arguments"), dict):
            raise ContractError("%s.arguments must be an object." % name)
        if not isinstance(step.get("reason"), str):
            raise ContractError("%s.reason must be a string." % name)
    return value


def audit_result(value):
    value = _exact_keys(
        value,
        "audit_result",
        ("decision", "issues", "revision_requirements"),
    )
    valid_decision = value.get("decision") in (
        "pass",
        "revise",
        "clarify",
        "reject",
    )
    valid_issues = isinstance(value.get("issues"), list)
    valid_requirements = isinstance(value.get("revision_requirements"), list)
    if not valid_decision or not valid_issues or not valid_requirements:
        raise ContractError("audit_result is invalid.")
    return value


def response_contract(value, contract_name):
    contracts = {
        "workflow": (("workflow_draft",), {"workflow_draft": workflow_draft}),
        "constrained": (
            ("task_semantics", "workflow_draft"),
            {"task_semantics": task_semantics, "workflow_draft": workflow_draft},
        ),
        "semantics": (("task_semantics",), {"task_semantics": task_semantics}),
        "audit": (("audit_result",), {"audit_result": audit_result}),
    }
    if contract_name not in contracts:
        raise RuntimeError("unknown response contract: %s" % contract_name)
    keys, validators = contracts[contract_name]
    value = _exact_keys(value, "model response", keys)
    return {key: validators[key](value[key]) for key in keys}


def _prompt(role: str, contract_name: str | None = None) -> str:
    contracts = {
        "direct": (
            "Return exactly one root JSON object with one key: workflow_draft. "
            "Never return action, summary, or steps at the root. "
            "Use only registered operation cards and exact parameter schemas. "
            "Never execute ArcPy or emit code. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "context": (
            "Return exactly one root JSON object with one key: workflow_draft. "
            "Never return action, summary, or steps at the root. "
            "Use normalized ArcMap context and registered operation cards only. "
            "Never use raw SQL; where clauses are structured. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "constrained": (
            "Return exactly one root JSON object with exactly two keys: task_semantics and workflow_draft. "
            "Never flatten either object into the root. Repair only from structured validation diagnostics. "
            + TASK_SEMANTICS_CONTRACT
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "semantic": (
            "Return exactly one root JSON object with one key: task_semantics. "
            "Never flatten goal, inputs, constraints, or success_criteria into the root. "
            + TASK_SEMANTICS_CONTRACT
            + "Do not produce a workflow or hidden reasoning."
        ),
        "planner": (
            "Return exactly one root JSON object with one key: workflow_draft. "
            "Never return action, summary, or steps at the root. "
            "Use only TaskSemantics, context, capabilities and structured "
            "diagnostics provided; no hidden reasoning. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "auditor": (
            "Return exactly one root JSON object with one key: audit_result. "
            "Never flatten decision, issues, or revision_requirements into the root. "
            + AUDIT_RESULT_CONTRACT
            + "Independently judge only TaskSemantics, context, capabilities and "
            "workflow draft; do not plan."
        ),
    }
    if role == "constrained" and contract_name == "workflow":
        contract = (
            "Return exactly one root JSON object with one key: workflow_draft repaired from "
            "the structured validation diagnostic. "
            "Never return action, summary, or steps at the root. "
            + WORKFLOW_DRAFT_CONTRACT
        )
    else:
        contract = contracts[role]
    return "You are the GeoPilot %s role. Return JSON only. %s" % (role, contract)


class ExperimentRunner:
    """Plans one persisted run; ArcMap execution remains outside this seam."""
    def __init__(self, catalog, store, client_factory=None):
        self.catalog = catalog
        self.store = store
        self.client_factory = client_factory

    def _configured_clients(self, mode, provider, model):
        from .llm_providers import load_config

        config = load_config()
        factory = self.client_factory or self._production_client
        primary_provider = provider or config["primary_provider"]
        primary_model = model or config["primary_model"]
        primary_client = factory(primary_provider, primary_model)
        auditor_client = None
        if mode == "multi_agent":
            auditor_client = factory(
                config["reviewer_provider"],
                config["reviewer_model"],
            )
        return primary_client, auditor_client

    @staticmethod
    def _production_client(provider, model):
        return create_provider(provider_id=provider, model_id=model)

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
        primary_client, auditor_client = self._configured_clients(mode, provider, model)
        if model and getattr(primary_client, "model_id", None) != model:
            raise ContractError("requested model is not the configured provider model.")
        actual = {
            "provider": getattr(primary_client, "provider_id", ""),
            "model": getattr(primary_client, "model_id", ""),
        }
        reviewer = None
        if auditor_client:
            reviewer = {
                "provider": getattr(auditor_client, "provider_id", ""),
                "model": getattr(auditor_client, "model_id", ""),
            }
        if auditor_client is primary_client or reviewer == actual:
            raise ContractError(
                "G3 reviewer must use a different provider or model from the primary planner."
            )
        capabilities = list(self.catalog.all_operations())
        protocol = workflow_protocol()
        policy = planning_policy(self.catalog, protocol)
        visible = [
            item
            for item in capabilities
            if mode != "direct_single"
            or (
                item.get("category") != "map_context"
                and not item["id"].startswith("context.")
            )
        ]
        capability_cards = [
            self.catalog.model_card(item)
            for item in sorted(visible, key=lambda item: item["id"])
        ]
        trace = self.store.run_trace(run_id)
        trace.update(
            {
                "provider": actual["provider"],
                "model": actual["model"],
                "requested_model_config": {
                    "provider": provider or actual["provider"],
                    "model": model or actual["model"],
                },
                "capability_hash": policy["catalog_hash"],
                "catalog_hash": policy["catalog_hash"],
                "planning_policy": policy,
                "context_hash": context_hash(context),
                "context_snapshot_hash": digest(context),
                "role_models": {
                    "direct": actual,
                    "context": actual,
                    "constrained": actual,
                    "semantic": actual,
                    "planner": actual,
                    "auditor": reviewer,
                },
            }
        )
        try:
            if mode == "direct_single":
                response = self._call(
                    run_id,
                    primary_client,
                    "direct",
                    {
                        "request": command,
                        "capabilities": capability_cards,
                        "workflow_protocol": protocol,
                    },
                    trace,
                    "workflow",
                )
                draft = response["workflow_draft"]
                version_id = self._record_workflow(trace, draft, "direct")
            elif mode == "context_single":
                response = self._call(
                    run_id,
                    primary_client,
                    "context",
                    {
                        "request": command,
                        "context": context,
                        "capabilities": capability_cards,
                        "workflow_protocol": protocol,
                    },
                    trace,
                    "workflow",
                )
                draft = response["workflow_draft"]
                version_id = self._record_workflow(trace, draft, "context")
            elif mode == "constrained_single":
                reply = self._call(
                    run_id,
                    primary_client,
                    "constrained",
                    {
                        "request": command,
                        "context": context,
                        "capabilities": capability_cards,
                        "workflow_protocol": protocol,
                    },
                    trace,
                    "constrained",
                )
                trace["task_semantics"] = reply["task_semantics"]
                draft = reply["workflow_draft"]
                version_id = self._record_workflow(trace, draft, "constrained")
                return self._validation_first_loop(
                    run_id,
                    primary_client,
                    "constrained",
                    command,
                    trace["task_semantics"],
                    context,
                    capability_cards,
                    protocol,
                    trace,
                    draft,
                    version_id,
                    None,
                )
            else:
                semantic_response = self._call(
                    run_id,
                    primary_client,
                    "semantic",
                    {
                        "request": command,
                        "context": context,
                        "capabilities": capability_cards,
                        "workflow_protocol": protocol,
                    },
                    trace,
                    "semantics",
                )
                semantics = semantic_response["task_semantics"]
                trace["task_semantics"] = semantics
                planner_response = self._call(
                    run_id,
                    primary_client,
                    "planner",
                    {
                        "task_semantics": semantics,
                        "context": context,
                        "capabilities": capability_cards,
                        "workflow_protocol": protocol,
                    },
                    trace,
                    "workflow",
                )
                draft = planner_response["workflow_draft"]
                version_id = self._record_workflow(trace, draft, "planner")
                return self._validation_first_loop(
                    run_id,
                    primary_client,
                    "planner",
                    "",
                    semantics,
                    context,
                    capability_cards,
                    protocol,
                    trace,
                    draft,
                    version_id,
                    auditor_client,
                )
            return self._finalize(run_id, draft, context, trace, version_id)
        except Exception as exc:
            self.store.fail_run(run_id, "planning", exc, trace)
            raise

    def _validation_first_loop(
        self,
        run_id,
        planner,
        planner_role,
        command,
        semantics,
        context,
        capability_cards,
        protocol,
        trace,
        draft,
        version_id,
        auditor,
    ):
        """Deep planning module: validation repairs always precede optional G3 audit."""
        seen_repairs = set()
        seen_invalid_workflows = set()
        seen_audited_workflows = set()
        while True:
            validation = self._validation(draft, context, trace, version_id)
            if not validation["ok"]:
                workflow_hash = digest(draft)
                if workflow_hash in seen_invalid_workflows:
                    trace["counts"]["cycles"] += 1
                    detail = {
                        "kind": "cyclic_revision",
                        "source": "validation",
                        "message": "Planner returned a previously invalid workflow.",
                    }
                    return self._terminal(run_id, "failed", draft, trace, detail)
                seen_invalid_workflows.add(workflow_hash)
                repair = self._repair_or_terminal(
                    run_id,
                    planner,
                    planner_role,
                    command,
                    semantics,
                    context,
                    capability_cards,
                    protocol,
                    trace,
                    draft,
                    version_id,
                    validation["diagnostic"],
                    "validation",
                    seen_repairs,
                )
                if repair.row is not None:
                    return repair.row
                draft, version_id = repair.draft, repair.version_id
                continue
            prepared = validation["workflow"]
            if auditor is None:
                return self._finalize(
                    run_id,
                    prepared,
                    context,
                    trace,
                    version_id,
                    already_valid=True,
                )
            prepared_hash = digest(prepared)
            if prepared_hash in seen_audited_workflows:
                trace["counts"]["cycles"] += 1
                detail = {
                    "kind": "cyclic_revision",
                    "source": "audit",
                    "message": "Planner returned a previously audited workflow.",
                }
                return self._terminal(run_id, "failed", prepared, trace, detail)
            seen_audited_workflows.add(prepared_hash)
            self._check_cancel(run_id)
            audit = self._call(
                run_id,
                auditor,
                "auditor",
                {
                    "task_semantics": semantics,
                    "context": context,
                    "capabilities": capability_cards,
                    "workflow_protocol": protocol,
                    "workflow_draft": prepared,
                },
                trace,
                "audit",
            )["audit_result"]
            trace["audits"].append(
                {
                    "version_id": version_id,
                    "workflow_hash": digest(prepared),
                    **audit,
                }
            )
            if audit["decision"] in TERMINAL_ACTIONS:
                return self._terminal(run_id, audit["decision"], prepared, trace, audit)
            if audit["decision"] == "pass":
                return self._finalize(
                    run_id,
                    prepared,
                    context,
                    trace,
                    version_id,
                    already_valid=True,
                )
            repair = self._repair_or_terminal(
                run_id,
                planner,
                planner_role,
                command,
                semantics,
                context,
                capability_cards,
                protocol,
                trace,
                prepared,
                version_id,
                audit,
                "audit",
                seen_repairs,
            )
            if repair.row is not None:
                return repair.row
            draft, version_id = repair.draft, repair.version_id

    def _repair_or_terminal(
        self,
        run_id,
        planner,
        planner_role,
        command,
        semantics,
        context,
        capability_cards,
        protocol,
        trace,
        draft,
        version_id,
        diagnostic,
        source,
        seen_repairs,
    ):
        key = (digest(draft), digest(diagnostic), source)
        if key in seen_repairs:
            trace["counts"]["stalls"] += 1
            detail = {
                "kind": "stalled_revision",
                "source": source,
                "message": "Repeated workflow and diagnostic.",
            }
            return _RepairResult(
                row=self._terminal(run_id, "failed", draft, trace, detail)
            )
        seen_repairs.add(key)
        count_name = "%s_revisions" % source
        limit = (
            MAX_VALIDATION_REVISIONS
            if source == "validation"
            else MAX_AUDIT_REVISIONS
        )
        if trace["counts"][count_name] >= limit:
            return _RepairResult(
                row=self._terminal(run_id, "failed", draft, trace, diagnostic)
            )
        trace["counts"][count_name] += 1
        self._check_cancel(run_id)
        payload = {
            "task_semantics": semantics,
            "context": context,
            "capabilities": capability_cards,
            "workflow_protocol": protocol,
            "workflow_draft": draft,
            "diagnostic": diagnostic,
        }
        if planner_role == "constrained":
            payload["request"] = command
            payload["validation"] = diagnostic
        reply = self._call(run_id, planner, planner_role, payload, trace, "workflow")
        revised = reply["workflow_draft"]
        if digest(revised) == digest(draft):
            trace["counts"]["stalls"] += 1
            detail = {
                "kind": "stalled_revision",
                "source": source,
                "message": "Planner returned the identical workflow.",
            }
            return _RepairResult(
                row=self._terminal(run_id, "failed", revised, trace, detail)
            )
        version_id = self._record_workflow(trace, revised, planner_role)
        return _RepairResult(draft=revised, version_id=version_id)

    def _call(self, run_id, client, role, payload, trace, contract_name):
        repair = None
        for attempt in range(MAX_RESPONSE_CONTRACT_REVISIONS + 1):
            request_payload = dict(payload)
            if repair:
                request_payload["response_contract_repair"] = repair
            response = self._model_call(
                run_id,
                client,
                role,
                request_payload,
                trace,
                contract_name,
            )
            try:
                return response_contract(response, contract_name)
            except ContractError as exc:
                diagnostic = {
                    "kind": "response_contract",
                    "role": role,
                    "contract": contract_name,
                    "message": str(exc),
                    "invalid_response_hash": digest(response),
                }
                trace["contract_diagnostics"].append(diagnostic)
                trace["turns"][-1]["contract_error"] = {
                    "type": type(exc).__name__,
                    "hash": digest(diagnostic),
                }
                trace["stages"][-1]["status"] = "contract_rejected"
                self.store.update_run(run_id, "running", trace=trace)
                if attempt >= MAX_RESPONSE_CONTRACT_REVISIONS:
                    raise ContractError(
                        "%s response contract failed after %d attempts: %s"
                        % (role, attempt + 1, exc)
                    )
                trace["counts"]["contract_revisions"] += 1
                repair = diagnostic
        raise RuntimeError("unreachable response contract loop")

    def _model_call(self, run_id, client, role, payload, trace, contract_name):
        self._check_cancel(run_id)
        system = _prompt(role, contract_name)
        stage = {"name": role, "started_at": time.time(), "status": "running"}
        trace["stages"].append(stage)
        turn = {
            "role": role,
            "input_hash": digest(payload),
            "prompt_hash": digest(system),
            "provider": getattr(client, "provider_id", ""),
            "model": getattr(client, "model_id", ""),
            "response_contract": contract_name,
            "started_at": stage["started_at"],
        }
        trace["turns"].append(turn)
        try:
            response = client.chat_json([
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ])
            if isinstance(response, dict):
                response = dict(response)
                usage = response.pop("_usage", None)
            else:
                usage = None
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
        trace["terminal"] = {
            "stage": "planning",
            "status": status,
            "detail": detail,
        }
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
