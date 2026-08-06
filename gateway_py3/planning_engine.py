"""The sole reproducible model-planning module for GeoPilot runs."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict

from .audit_contract import AUDIT_CONTRACT, AuditContractError, AuditContractExhausted, audit_contract_for_report
from .capability_selection import CapabilityScope, CapabilitySelectionError
from .execution_contract import build_execution_contract
from .llm_providers import ProviderError, ProviderProtocolError, create_provider
from .structured_contracts import (
    structured_output_contract,
    workflow_contract_for_capabilities,
)
from .planning_state_machine import (
    PlanningCancelled,
    PlanningFaultAfterCancellation,
    PlanningStateMachine,
)
from .plan_artifact import PlanArtifact
from .semantic_domain import task_predicate_catalog
from .task_contract import (
    TaskContractError,
    bind_model_task_contract,
    parse_task_contract,
    task_contract_for_context,
    task_contract_model_view,
)
from .validators import ValidationError, context_hash, prepare_workflow
from .workflow_protocol import workflow_protocol
from .workflow_verifier import WorkflowVerifier

MODES = ("g0_direct", "g1_context", "g2_constrained", "g3_audited")
MAX_VALIDATION_REVISIONS = 3
MAX_AUDIT_REVISIONS = 3
MAX_RESPONSE_CONTRACT_REVISIONS = 2
MAX_MISMATCH_PATHS = 12
TASK_PREDICATE_CATALOG_TEXT = json.dumps(
    task_predicate_catalog(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
)
WORKFLOW_STEP_CONTRACT = (
    "Every provider-wire workflow_draft step MUST contain exactly four fields: id (unique string), "
    "operation (exact selected operation id), arguments_json (a JSON string that decodes to exactly "
    "one object matching that operation's parameters_schema), and reason (string). "
    "Never emit an arguments object, parameters, or a JSON wrapper object inside arguments_json. "
)
WORKFLOW_DRAFT_CONTRACT = (
    "workflow_draft MUST contain exactly action, summary, and steps. "
    "action MUST be exactly execute and steps MUST be a non-empty array. "
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


def differing_paths(expected: Any, actual: Any, path: str = "context_snapshot") -> list[str]:
    """Return bounded structural mismatch locations without logging context values."""
    result: list[str] = []

    def visit(left: Any, right: Any, current: str) -> None:
        if len(result) >= MAX_MISMATCH_PATHS:
            return
        if type(left) is not type(right):
            result.append(current)
            return
        if isinstance(left, dict):
            for key in sorted(set(left) | set(right)):
                child = "%s.%s" % (current, key)
                if key not in left or key not in right:
                    result.append(child)
                else:
                    visit(left[key], right[key], child)
                if len(result) >= MAX_MISMATCH_PATHS:
                    return
            return
        if isinstance(left, list):
            if len(left) != len(right):
                result.append(current + ".length")
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                visit(left_item, right_item, "%s[%d]" % (current, index))
                if len(result) >= MAX_MISMATCH_PATHS:
                    return
            return
        if left != right:
            result.append(current)

    visit(expected, actual, path)
    return result


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


def workflow_draft_model_view(value):
    """Encode the canonical internal Workflow into its sole provider wire form."""
    value = workflow_draft(value)
    return {
        "action": value["action"],
        "summary": value["summary"],
        "steps": [
            {
                "id": step["id"],
                "operation": step["operation"],
                "arguments_json": json.dumps(
                    step["arguments"], ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ),
                "reason": step["reason"],
            }
            for step in value["steps"]
        ],
    }


def bind_model_workflow_response(value, capabilities):
    """Bind the fixed provider wire response to the canonical internal Workflow."""
    value = _exact_keys(value, "model response", ("workflow_draft",))
    draft = _exact_keys(
        value["workflow_draft"], "workflow_draft", ("action", "summary", "steps"),
    )
    if draft.get("action") != "execute" or not isinstance(draft.get("summary"), str):
        raise ContractError("workflow_draft is invalid.")
    steps = draft.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ContractError("workflow_draft.steps must be a non-empty array.")
    allowed = {
        card.get("id") for card in capabilities
        if isinstance(card, dict) and isinstance(card.get("id"), str)
    }
    canonical_steps = []
    for index, step in enumerate(steps):
        name = "workflow_draft.steps[%d]" % index
        step = _exact_keys(step, name, ("id", "operation", "arguments_json", "reason"))
        operation = step.get("operation")
        if operation not in allowed:
            raise ContractError("%s.operation is outside the selected capability closure." % name)
        encoded = step.get("arguments_json")
        if not isinstance(encoded, str):
            raise ContractError("%s.arguments_json must be a JSON string." % name)
        try:
            arguments = json.loads(encoded)
        except ValueError:
            raise ContractError("%s.arguments_json must encode valid JSON." % name)
        if not isinstance(arguments, dict):
            raise ContractError("%s.arguments_json must encode exactly one JSON object." % name)
        canonical_steps.append({
            "id": step.get("id"),
            "operation": operation,
            "arguments": arguments,
            "reason": step.get("reason"),
        })
    return {"workflow_draft": workflow_draft({
        "action": draft["action"],
        "summary": draft["summary"],
        "steps": canonical_steps,
    })}


def response_contract(value, contract_name):
    contracts = {
        "workflow": (("workflow_draft",), {"workflow_draft": workflow_draft}),
        "task_contract": (("task_contract",), {"task_contract": lambda value: value}),
        "audit": (("audit_result",), {"audit_result": AUDIT_CONTRACT.validate_shape}),
    }
    if contract_name not in contracts:
        raise RuntimeError("unknown response contract: %s" % contract_name)
    keys, validators = contracts[contract_name]
    value = _exact_keys(value, "model response", keys)
    return {key: validators[key](value[key]) for key in keys}


def _model_visible_payload(payload, contract_name):
    if contract_name != "workflow":
        return payload
    result = dict(payload)
    if "workflow_draft" in result:
        result["workflow_draft"] = workflow_draft_model_view(result["workflow_draft"])
    return result


def _prompt(role: str, contract_name: str | None = None) -> str:
    contracts = {
        "direct": (
            "The tool arguments must contain exactly one root object with one key: workflow_draft. "
            "Never put action, summary, or steps at the root. "
            "Use only registered operation cards and exact parameter schemas. "
            "Never execute ArcPy or emit code. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "context": (
            "The tool arguments must contain exactly one root object with one key: workflow_draft. "
            "Never put action, summary, or steps at the root. "
            "Use normalized ArcMap context and registered operation cards only. "
            "Never use raw SQL; where clauses are structured. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "semantic": (
            "The tool arguments must contain exactly one root object with one key: task_contract. "
            "Each requirements item must contain exactly requirement_id and predicate_json. "
            "predicate_json must be a JSON string that decodes to exactly one object matching one closed "
            "variant from the task_predicate_catalog in this system instruction. Do not put kind or any predicate field "
            "beside requirement_id, and never emit a predicate wrapper. Inside predicate_json, an "
            "attribute_filter where field is a native JSON object (for example, "
            "{\"field\":\"POP\",\"op\":\"gte\",\"value\":800}). Every non-null distance is "
            "exactly an object with value (number) and unit (meters, kilometers, map_units, or degrees). "
            "Every requirement subject, target, selector, join, and result MUST be the exact entity_id "
            "of a declared input_entities or outputs item; never use a layer name, filename, field name, "
            "or invented intermediate identifier. "
            "Every input entity_id MUST be unique and begin with input:. Every output_id MUST be unique "
            "and begin with output:. Never reuse an input id for an output. "
            "A selection or other transient map operation requires changes_map in "
            "allowed_side_effects even when the requested deliverable is a file. "
            "Use select_subset only after current context or an earlier filter requirement in this same "
            "ordered contract has established a selection on the same target; the first filter is new_selection. "
            "Do not emit kind or evidence in input_entities, and do not emit evidence in requirements or "
            "clarifications; the server binds input kind from the selected live reference and binds immutable "
            "provenance directly from the request. predicate_json carries the precise obligation. "
            "Each outputs item has exactly these fields: output_id, kind, name, format, "
            "geometry, required_fields, spatial_reference, destination, evidence. destination MUST be "
            "the exact absolute Windows folder or geodatabase path requested for that output; use default "
            "only when the request gives no destination, and not_applicable only for a map_state output. "
            "Never invent, shorten, normalize, or omit a requested destination. Output evidence MUST be "
            "the shortest contiguous request substring that names or demands only that output. When "
            "the request contains an explicit filename, copy that filename exactly and set name to its "
            "basename without the extension; never convert the extension into a name suffix. "
            "When the ArcMap context is unsaved and a persisted output has no explicit request destination, "
            "use destination=default and add one output-location clarification; never invent a path. "
            "For artifact_export, subject is the declared newly exported output and target is "
            "the source layer being exported; never reverse them. "
            "For attribute_filter and spatial_filter, target is always the declared existing input "
            "entity being filtered; subject is that same input entity unless the requested result "
            "is explicitly a map_state output. Never filter a file or feature-class output before it exists. "
            "Requirements are ordered selection-state transitions. When an earlier filter requirement "
            "selects the entity later used as a spatial_filter selector, keep that same declared entity as "
            "selector: the spatial operation consumes its current selected subset. Never invent an output "
            "or intermediate entity merely to name that established selection. "
            "When a spatial_filter selector is an output produced by a buffer requirement, use intersect "
            "and omit search_distance; the buffer requirement already owns the distance and it must not be applied twice. "
            "Do not declare output cardinality or grain in TaskContract. Cardinality is owned by the "
            "selected capability contract; express requested row, feature, grouping, and selection "
            "semantics only through the closed requirement predicates. "
            "When the request says an existing source must not be modified, use the "
            "source_preserved predicate with that source as subject; do not use inspect. "
            "Do not emit inspect requirements. A requested derived field such as Join_Count "
            "belongs in the output required_fields contract, and requested output format belongs "
            "in the output format contract. Buffer, overlay, spatial_join, aggregate, project, merge, "
            "copy and other GIS producers already create their declared output; never add an "
            "artifact_export requirement for that same output. Use artifact_export only when an actual "
            "export operation is independently required, such as persisting a transient selection. "
            "TaskContract contains only requested inputs and user-visible outputs. Never invent an intermediate "
            "buffer output there: express a distance-only filter as spatial_filter within_a_distance with "
            "search_distance, and let Workflow choose any required execution intermediate. "
            + "Preserve the request's output entity, aggregation grain, direction and success "
            "conditions exactly; do not replace them with a related task. "
            "When task_contract_draft and audit_diagnostic are supplied, revise the contract only for "
            "the proof-bound request-alignment defects, preserve all unaffected ids and obligations, "
            "and derive every change from the immutable request. Remove a bogus or redundant requirement "
            "instead of inventing a predicate kind or export action. A GIS operation's output is already "
            "declared by outputs and its producer predicate; artifact_export is only for a separately requested "
            "export of an existing entity. source_preserved may bind only an input explicitly covered by a "
            "read-only or do-not-modify statement. Use only the closed predicate shapes supplied in "
            "task_predicate_catalog. Do not produce a workflow or hidden reasoning. "
            "Closed task_predicate_catalog: " + TASK_PREDICATE_CATALOG_TEXT
        ),
        "planner": (
            "The tool arguments must contain exactly one root object with one key: workflow_draft. "
            "Never put action, summary, or steps at the root. "
            "Use only TaskContract, context, capabilities and structured "
            "diagnostics provided; no hidden reasoning. Treat request as immutable source "
            "truth: TaskContract may clarify it but cannot replace or contradict it. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "workflow_repair": (
            "The supplied workflow_draft is the latest rejected workflow, not an example or a valid answer. "
            "The supplied diagnostic is authoritative audit or WorkflowVerifier evidence. Return the complete "
            "corrected workflow and never repeat the rejected workflow unchanged. Apply every required_change "
            "and eliminate every hard_violations item at its contract_path. Re-check each affected capability's "
            "declared inputs, outputs, cardinality, fields, spatial reference, publication and authorization. "
            "When repairing a workflow that was already revised, repair that latest workflow instead of reverting "
            "to the baseline. Preserve unaffected steps, exact user-request semantics and output names. "
            "Use only TaskContract, context, capabilities and the supplied structured diagnostic; no hidden reasoning. "
            + WORKFLOW_DRAFT_CONTRACT
        ),
        "auditor": (
            AUDIT_CONTRACT.prompt
        ),
    }
    contract = contracts[role]
    return (
        "You are the GeoPilot %s role. Call the supplied structured-output tool exactly once. "
        "Do not emit text content. When response_contract_repair contains rejected_response, "
        "treat that object as the draft: repair every invalid path listed in message in one pass, "
        "preserve unaffected content, re-check the complete response contract, and return the complete "
        "corrected contract. %s"
    ) % (role, contract)


class PlanningEngine:
    """Plans one persisted run; ArcMap execution remains outside this seam."""
    validation_revision_limit = MAX_VALIDATION_REVISIONS
    audit_revision_limit = MAX_AUDIT_REVISIONS
    def __init__(self, catalog, store, client_factory=None):
        self.catalog = catalog
        self.store = store
        self.client_factory = client_factory
        self.audit_contract = AUDIT_CONTRACT

    def _configured_clients(self, mode, provider, model):
        config = None
        if not provider or not model:
            from .llm_providers import load_config

            config = load_config()
        factory = self.client_factory or self._production_client
        primary_provider = provider or config["primary_provider"]
        primary_model = model or config["primary_model"]
        primary_client = factory(primary_provider, primary_model)
        auditor_client = None
        if mode == "g3_audited":
            auditor_client = factory(primary_provider, primary_model)
        return primary_client, auditor_client

    digest = staticmethod(digest)

    def planning_policy_snapshot(self, protocol):
        return planning_policy(self.catalog, protocol)

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
        auditor_metadata = None
        if auditor_client:
            if auditor_client is primary_client:
                raise ContractError("G3 requires separate planner and auditor client instances.")
            auditor_metadata = {
                "provider": getattr(auditor_client, "provider_id", ""),
                "model": getattr(auditor_client, "model_id", ""),
            }
        capabilities = list(self.catalog.all_operations())
        protocol = workflow_protocol()
        policy = planning_policy(self.catalog, protocol)
        visible = [
            item
            for item in capabilities
            if mode != "g0_direct"
            or (
                item.get("category") != "map_context"
                and not item["id"].startswith("context.")
            )
        ]
        capability_cards = [
            self.catalog.planning_card(item)
            for item in sorted(visible, key=lambda item: item["id"])
        ] if mode in ("g0_direct", "g1_context") else []
        capability_scope = CapabilityScope(self.catalog, visible)
        capability_index = capability_scope.semantic_index() if mode in ("g2_constrained", "g3_audited") else None
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
                "execution_context_hash": context_hash(context),
                "role_models": {
                    "direct": actual,
                    "context": actual,
                    "g2_constrained": actual,
                    "semantic": actual,
                    "planner": actual,
                    "auditor": auditor_metadata,
                },
                "capability_index_hash": capability_index["index_hash"] if capability_index else None,
                "capability_index_count": len(capability_index["operations"]) if capability_index else None,
                "transitions": [],
            }
        )
        # Persist immutable provenance before the first provider call so an
        # interrupted or timed-out planner remains attributable and auditable.
        self.store.update_run(run_id, "running", trace=trace)
        try:
            if mode == "g0_direct":
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
            elif mode == "g1_context":
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
            else:
                semantic_response = self._call(
                    run_id,
                    primary_client,
                    "semantic",
                    {
                        "request": command,
                        "context": context,
                        "capability_index": capability_index,
                        "workflow_protocol": protocol,
                    },
                    trace,
                    "task_contract",
                    lambda response: self._parse_task_contract_response(response, command, context),
                )
                task_contract = semantic_response["task_contract"]
                trace["task_contract"] = task_contract
                closure = capability_scope.close(task_contract)
                capability_cards = list(closure.cards)
                trace["capability_selection"] = closure.trace_record()
                trace["prompt_capability_hash"] = closure.hash
                planner_response = self._call(
                    run_id,
                    primary_client,
                    "planner",
                    {
                        "request": command,
                        "task_contract": task_contract_model_view(task_contract),
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
                    task_contract,
                    context,
                    capability_cards,
                    protocol,
                    trace,
                    draft,
                    version_id,
                    auditor_client if mode == "g3_audited" else None,
                    command,
                )
            return self._finalize(run_id, draft, context, trace, version_id)
        except PlanningCancelled:
            return self._persist_cancellation(run_id, mode, trace)
        except PlanningFaultAfterCancellation as signal:
            self._persist_cancellation(run_id, mode, trace)
            raise signal.error
        except Exception as exc:
            self.store.fail_run(run_id, "planning", exc, trace)
            raise

    def _validation_first_loop(
        self, run_id, planner, planner_role, task_contract, context,
        capability_cards, protocol, trace, draft, version_id, auditor, request,
    ):
        return PlanningStateMachine(
            self, run_id, planner, planner_role, task_contract, context,
            capability_cards, protocol, trace, draft, version_id, auditor, request,
        ).run()

    def plan_with_artifact(self, run_id, command, context, mode, artifact, provider="", model=""):
        """Replay one frozen G2/G3 baseline through the normal production state machine."""
        self._validate_request(command, context, mode)
        if mode not in ("g2_constrained", "g3_audited"):
            raise ContractError("plan_artifact is only valid for G2/G3.")
        frozen = PlanArtifact.from_dict(artifact.as_dict() if isinstance(artifact, PlanArtifact) else artifact)
        execution_hash = context_hash(context)
        policy = self.planning_policy_snapshot(workflow_protocol())
        if frozen.as_dict()["request"] != command or frozen.as_dict()["execution_context_hash"] != execution_hash:
            raise ContractError("PlanArtifact request or execution context does not match this run.")
        frozen_context = frozen.as_dict()["context_snapshot"]
        if frozen_context != context:
            paths = differing_paths(frozen_context, context)
            raise ContractError(
                "PlanArtifact context snapshot does not match this run: %s."
                % ", ".join(paths)
            )
        if frozen.as_dict()["planning_policy"] != policy:
            raise ContractError("PlanArtifact catalog or planning policy does not match this server.")
        frozen_document = frozen.as_dict()
        try:
            CapabilityScope(self.catalog).validate_snapshot(
                frozen_document["capability_snapshot"],
                [step["operation"] for step in frozen_document["baseline_workflow"]["steps"]],
            )
        except CapabilitySelectionError as exc:
            raise ContractError(str(exc))
        trace = self.store.run_trace(run_id)
        trace.update({"context_hash": execution_hash, "context_snapshot_hash": digest(context),
                      "execution_context_hash": execution_hash, "capability_hash": policy["catalog_hash"],
                      "catalog_hash": policy["catalog_hash"], "planning_policy": policy,
                      "task_contract": frozen_document["task_contract"], "plan_artifact": frozen_document,
                      "plan_artifact_hash": frozen.hash, "artifact_source": "supplied",
                      "baseline_workflow_hash": frozen_document["baseline_workflow_hash"],
                      "prompt_capability_hash": frozen_document["capability_hash"],
                      "capability_selection": {
                          "selected_ids": [card["id"] for card in frozen_document["capability_snapshot"]],
                          "selected_hash": frozen_document["capability_hash"],
                          "source": "supplied_artifact",
                      },
                      "reverification": {}})
        validation = self._validation(frozen_document["baseline_workflow"], context, trace, "w1")
        report = validation.get("report")
        if not validation.get("ok") or report != frozen_document["baseline_verifier_report"]:
            raise ContractError("PlanArtifact baseline reverification does not match its frozen report.")
        trace["reverification"] = {"ok": True, "report_hash": digest(report), "baseline_hash": frozen.as_dict()["baseline_workflow_hash"]}
        version_id = self._record_workflow(trace, frozen_document["baseline_workflow"], "artifact")
        if mode == "g2_constrained":
            return self._finalize(run_id, validation["workflow"], context, trace, version_id, already_valid=True)
        primary, auditor = self._configured_clients(mode, provider, model)
        if auditor is None or auditor is primary:
            raise ContractError("G3 requires separate planner and auditor client instances.")
        return PlanningStateMachine(self, run_id, primary, "planner", frozen_document["task_contract"],
            context, frozen_document["capability_snapshot"], workflow_protocol(), trace,
            validation["workflow"], version_id, auditor, command, frozen_artifact=frozen).run()

    def capability_closure(self, task_contract, workflow=None):
        retained = [] if workflow is None else [
            step["operation"] for step in workflow.get("steps", ())
        ]
        return CapabilityScope(self.catalog).close(task_contract, retained)

    @staticmethod
    def record_capability_closure(trace, closure, source):
        record = closure.trace_record()
        record["source"] = source
        trace["capability_selection"] = record
        trace["prompt_capability_hash"] = closure.hash

    def _request_revision(
        self,
        run_id,
        planner,
        task_contract,
        context,
        capability_cards,
        protocol,
        trace,
        draft,
        diagnostic,
        request,
    ):
        payload = {
            "request": request,
            "task_contract": task_contract_model_view(task_contract),
            "context": context,
            "capabilities": capability_cards,
            "workflow_protocol": protocol,
            "workflow_draft": draft,
            "diagnostic": diagnostic,
        }
        reply = self._call(run_id, planner, "workflow_repair", payload, trace, "workflow")
        return reply["workflow_draft"]

    def _request_task_contract_revision(
        self, run_id, client, task_contract, context, protocol,
        trace, diagnostic, request,
    ):
        payload = {
            "request": request,
            "task_contract_draft": task_contract_model_view(task_contract),
            "audit_diagnostic": diagnostic,
            "context": context,
            "capability_index": CapabilityScope(self.catalog).semantic_index(),
            "workflow_protocol": protocol,
        }
        reply = self._call(
            run_id, client, "semantic", payload, trace, "task_contract",
            lambda response: self._parse_task_contract_response(response, request, context),
        )
        return reply["task_contract"]

    @staticmethod
    def _parse_task_contract_response(response, command, context):
        try:
            bound = bind_model_task_contract(response["task_contract"], command, context)
            return {"task_contract": parse_task_contract(bound, command, context)}
        except TaskContractError as exc:
            raise ContractError(str(exc))

    def _call(self, run_id, client, role, payload, trace, contract_name, response_validator=None):
        repair = None
        for attempt in range(MAX_RESPONSE_CONTRACT_REVISIONS + 1):
            request_payload = dict(payload)
            if repair:
                request_payload["response_contract_repair"] = repair
            try:
                response = self._model_call(
                    run_id,
                    client,
                    role,
                    request_payload,
                    trace,
                    contract_name,
                )
            except ProviderProtocolError as exc:
                if not self._is_provider_response_evidence(exc.evidence):
                    raise
                repair = self._reject_contract_response(
                    run_id, role, contract_name, trace, attempt, exc, exc.evidence,
                    protocol_evidence=exc.evidence,
                )
                continue
            self._check_cancel(run_id)
            try:
                candidate = (
                    bind_model_workflow_response(response, payload["capabilities"])
                    if contract_name == "workflow"
                    else response
                )
                validated = response_contract(candidate, contract_name)
                return response_validator(validated) if response_validator else validated
            except (ContractError, AuditContractError) as exc:
                repair = self._reject_contract_response(
                    run_id, role, contract_name, trace, attempt, exc, response,
                )
        raise RuntimeError("unreachable response contract loop")

    @staticmethod
    def _is_provider_response_evidence(evidence):
        if not isinstance(evidence, dict):
            return False
        if isinstance(evidence.get("choices"), list):
            return True
        return isinstance(evidence.get("content"), list) and bool(evidence.get("id") or evidence.get("type"))

    def _reject_contract_response(self, run_id, role, contract_name, trace, attempt, exc,
                                  invalid_response, protocol_evidence=None):
        self._check_cancel(run_id)
        diagnostic = {
            "kind": "provider_protocol" if protocol_evidence is not None else ("audit_contract" if role == "auditor" else "response_contract"),
            "role": role,
            "contract": contract_name,
            "message": str(exc),
            "invalid_response_hash": digest(invalid_response),
        }
        if protocol_evidence is not None:
            diagnostic["protocol_evidence_hash"] = digest(protocol_evidence)
        trace["contract_diagnostics"].append(diagnostic)
        trace["turns"][-1]["contract_error"] = {
            "type": type(exc).__name__, "hash": digest(diagnostic),
        }
        trace["stages"][-1]["status"] = "contract_rejected"
        self.store.update_run(run_id, "running", trace=trace)
        if attempt >= MAX_RESPONSE_CONTRACT_REVISIONS:
            error_type = AuditContractExhausted if role == "auditor" else ContractError
            raise error_type(
                "%s response contract failed after %d attempts: %s"
                % (role, attempt + 1, exc)
            )
        trace["counts"]["contract_revisions"] += 1
        repair = dict(diagnostic)
        if protocol_evidence is None:
            repair["rejected_response"] = invalid_response
        return repair

    def _model_call(self, run_id, client, role, payload, trace, contract_name):
        self._check_cancel(run_id)
        primary_error = None
        system = _prompt(role, contract_name)
        model_payload = _model_visible_payload(payload, contract_name)
        stage = {"name": role, "started_at": time.time(), "status": "running"}
        trace["stages"].append(stage)
        turn = {
            "role": role,
            "input_hash": digest(model_payload),
            "prompt_hash": digest(system),
            "provider": getattr(client, "provider_id", ""),
            "model": getattr(client, "model_id", ""),
            "response_contract": contract_name,
            "started_at": stage["started_at"],
        }
        trace["turns"].append(turn)
        try:
            contract = (
                task_contract_for_context(payload.get("context", {}), payload.get("request", ""))
                if contract_name == "task_contract"
                else audit_contract_for_report(payload["plan_artifact"]["baseline_verifier_report"])
                if contract_name == "audit"
                else workflow_contract_for_capabilities(payload["capabilities"])
                if contract_name == "workflow"
                else structured_output_contract(contract_name)
            )
            turn["tool_contract"] = contract.name
            turn["tool_contract_hash"] = digest(contract.schema)
            response = client.chat_structured(
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(model_payload, ensure_ascii=False, sort_keys=True),
                    },
                ],
                contract,
            )
            if isinstance(response, dict):
                response = dict(response)
                usage = response.pop("_usage", None)
                provider_response = response.pop("_provider_response", None)
            else:
                usage = None
                provider_response = None
            if provider_response is not None:
                turn["provider_response"] = provider_response
            turn["output_hash"] = digest(response)
            if usage:
                trace["usage"].append(usage)
            stage["status"] = "succeeded"
            return response
        except Exception as exc:
            primary_error = exc
            error_type = type(exc).__name__
            if isinstance(exc, ProviderProtocolError) and exc.evidence:
                turn["provider_response"] = exc.evidence
            error = {
                "type": error_type,
                "hash": digest({"type": error_type, "message": str(exc)}),
            }
            if isinstance(exc, ProviderError):
                error["message"] = str(exc)
            turn["error"] = error
            stage["status"] = "failed"
            raise
        finally:
            finished_at = time.time()
            turn["finished_at"] = finished_at
            stage["finished_at"] = finished_at
            try:
                status = "cancelled" if self.store.is_cancel_requested(run_id) else "running"
                self.store.update_run(run_id, status, trace=trace)
            except ValueError:
                if not self.store.is_cancel_requested(run_id):
                    raise
                self.store.update_run(run_id, "cancelled", trace=trace)
            if primary_error is not None and self.store.is_cancel_requested(run_id):
                raise PlanningFaultAfterCancellation(primary_error) from primary_error

    def _validation(self, draft, context, trace, version_id):
        task_contract = trace.get("task_contract")
        if task_contract is None:
            try:
                normalization_events = []
                prepared = prepare_workflow(draft, self.catalog, context, normalization_events)
                result = {"ok": True, "workflow": prepared, "normalization_events": normalization_events}
            except ValidationError as exc:
                result = {"ok": False, "diagnostic": {"kind": "deterministic_validation", "message": str(exc)}}
        else:
            report = WorkflowVerifier(self.catalog).verify(draft, context, task_contract)
            result = {
                "ok": report["ok"], "workflow": report.get("prepared_workflow") or draft,
                "normalization_events": report.get("normalization_events", []),
                "report": report,
            }
            if not report["ok"]:
                result["diagnostic"] = {
                    "kind": "workflow_verifier",
                    "hard_violations": report["hard_violations"],
                    "blocking_clarifications": report["blocking_clarifications"],
                    "review_obligations": report["review_obligations"],
                }
        validation_entry = {"version_id": version_id, "workflow_hash": digest(draft)}
        validation_entry.update({"ok": True} if result["ok"] else result["diagnostic"])
        if result["ok"]:
            validation_entry["prepared_workflow"] = result["workflow"]
            validation_entry["normalization_events"] = result["normalization_events"]
            trace.setdefault("normalization_events", []).extend(result["normalization_events"])
        trace["validations"].append(validation_entry)
        return result

    def reproducible_baseline_report(self, workflow, context, task_contract):
        """Return the verifier proof for the exact canonical artifact workflow."""
        report = WorkflowVerifier(self.catalog).verify(workflow, context, task_contract)
        if not report["ok"] or report.get("prepared_workflow") != workflow:
            raise ContractError("Canonical baseline workflow is not verifier-idempotent.")
        if report.get("normalization_events") != []:
            raise ContractError("Canonical baseline workflow still requires normalization.")
        return report

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
        self._check_cancel(run_id)
        validation = (
            {"ok": True, "workflow": draft}
            if already_valid
            else self._validation(draft, context, trace, version_id)
        )
        if not validation["ok"]:
            return self._terminal(run_id, "failed", draft, trace, validation["diagnostic"])
        self._check_cancel(run_id)
        return self._persist_planned_run(run_id, validation["workflow"], trace)

    def _persist_planned_run(self, run_id, prepared, trace):
        trace["execution_contract"] = build_execution_contract(
            prepared,
            trace["context_hash"],
            trace["capability_hash"],
            self.catalog.capabilities,
        )
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
            raise PlanningCancelled()

    def _persist_cancellation(self, run_id, mode, trace):
        if mode in ("g2_constrained", "g3_audited"):
            PlanningStateMachine.record_cancelled(trace)
        trace["finished_at"] = time.time()
        trace["terminal"] = {
            "stage": "planning", "status": "cancelled",
            "detail": {"outcome": "cancelled"},
        }
        self.store.update_run(run_id, "cancelled", trace=trace)
        return self.store.get(run_id)

    @staticmethod
    def _validate_request(command, context, mode):
        if mode not in MODES or not isinstance(command, str) or not command.strip() or not isinstance(context, dict):
            raise ContractError("invalid run request.")
