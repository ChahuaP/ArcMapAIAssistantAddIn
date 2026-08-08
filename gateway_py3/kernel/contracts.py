"""Core contracts for the GeoPilotKernel (target architecture §4, §13.1).

All cross-module results are strict discriminated unions. Exceptions may be
used inside a module, but never as a cross-module business protocol: every
terminal or paused state is an ``Outcome`` the caller matches by ``kind``.

Contracts are Pydantic v2 ``BaseModel`` instances: ``frozen=True``,
``extra='forbid'``, ``validate_assignment=True``. Sealed types
(``ContextSnapshot``, ``IntentSpec``, ``CapabilitySnapshot``, ``VerifiedPlan``)
carry a ``digest`` computed over their canonical ``model_dump(mode='json')``
serialization. Any change produces a new digest; a sealed plan is never
mutated in place.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- canonical serialization ------------------------------------------------

def canonical_json(value: Any) -> str:
    """Stable JSON text used for every digest and prefix-cache layout (§6.4).

    ``ensure_ascii=False`` keeps Chinese field values readable; ``sort_keys``
    and tight separators make the text byte-stable so identical inputs hash
    identically. Stable prefixes must never embed timestamps, run/session ids
    or random paths into this text.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _seal_document(model: BaseModel) -> Any:
    """Canonical document for digest over a BaseModel.

    Uses ``model_dump(mode='json')`` so nested BaseModels are serialized
    consistently and non-JSON types (tuple, int, None) are coerced. Computed
    properties (``digest``) are not included by ``model_dump``.
    """
    return model.model_dump(mode="json")


# --- identity helpers -------------------------------------------------------

EMPTY = ""


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s is required." % name)
    return value


def _require_uuid(value: str, name: str) -> str:
    parsed = _require_id(value, name)
    try:
        normalized = str(uuid.UUID(parsed))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("%s must be a canonical UUID." % name)
    if normalized != parsed:
        raise ValueError("%s must be a canonical UUID." % name)
    return parsed


# --- §4.1 RequestEnvelope ---------------------------------------------------

class _FrozenModel(BaseModel):
    """Base config for every contract: immutable, no extra fields, validate
    on assignment (§13.1)."""
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )


class CallerIdentity(_FrozenModel):
    """Who is asking, and through what client.

    ``tenant_id`` + ``security_scope`` bound every cache key (§6.4) and every
    authorization. ``role`` and ``data_scope`` feed PolicyGate.
    """
    user_id: str
    tenant_id: str
    role: str
    data_scope: Tuple[str, ...] = ()
    client_kind: str = "web"

    @model_validator(mode="after")
    def _validate(self) -> "CallerIdentity":
        _require_id(self.user_id, "caller.user_id")
        _require_id(self.tenant_id, "caller.tenant_id")
        _require_id(self.role, "caller.role")
        _require_id(self.client_kind, "caller.client_kind")
        return self


class SideEffectScope(_FrozenModel):
    """The side effects a user explicitly authorized for this request (§4.1).

    ``level`` follows PolicyGate risk tiers (§6.6): 1 read, 2 recoverable
    session, 3 isolated workspace write, 4 destructive. ``paths`` and
    ``datasets`` are the explicit inputs/outputs the user named.
    """
    level: int
    paths: Tuple[str, ...] = ()
    datasets: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "SideEffectScope":
        if self.level < 1 or self.level > 4:
            raise ValueError("side_effect.level must be 1..4.")
        return self


class RequestEnvelope(_FrozenModel):
    """The only input the kernel accepts from an adapter (§4.1).

    Callers may not submit fields the server derives: output_format, layer
    type, selection derivatives, default workspace. Those are bound server-side
    by TaskCompiler.
    """
    session_id: str
    request_id: str
    text: str = Field(min_length=1)
    caller: CallerIdentity
    execute: bool = False
    side_effects: Optional[SideEffectScope] = None
    inputs: Tuple[str, ...] = ()
    outputs: Tuple[str, ...] = ()
    plan_artifact: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate(self) -> "RequestEnvelope":
        _require_uuid(self.session_id, "session_id")
        _require_uuid(self.request_id, "request_id")
        if self.execute and self.side_effects is None:
            raise ValueError("execute=True requires an explicit side_effect scope.")
        if self.side_effects is not None and not self.execute:
            raise ValueError("side_effects require execute=True.")
        return self


# --- §4.2 ContextSnapshot ---------------------------------------------------

class LayerRef(_FrozenModel):
    name: str
    layer_ref: str
    data_source: Optional[str] = None
    layer_type: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "LayerRef":
        _require_id(self.name, "layer.name")
        _require_id(self.layer_ref, "layer.layer_ref")
        return self


class FieldColumn(_FrozenModel):
    name: str
    dtype: Optional[str] = None
    nullable: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "FieldColumn":
        _require_id(self.name, "field.name")
        return self


class LayerSnapshot(_FrozenModel):
    """Stable reference + structure for one map layer (§4.2)."""
    identity: LayerRef
    fields: Tuple[FieldColumn, ...] = ()
    coordinate_system: Optional[str] = None
    geometry_type: Optional[str] = None
    selection_count: int = 0
    value_summary: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate(self) -> "LayerSnapshot":
        if self.selection_count < 0:
            raise ValueError("layer.selection_count must be non-negative int.")
        return self


class ContextSnapshot(_FrozenModel):
    """Frozen before planning by the bound ArcMap lease (§4.2).

    The model only sees a ``ContextProjection`` derived from this snapshot;
    the full snapshot is retained as evidence. Its ``digest`` binds every plan
    and execution request.
    """
    lease_id: str
    arcmap_pid: int
    bridge_pid: int
    bridge_port: int
    target_hwnd: int
    document_identity: Dict[str, Any]
    layers: Tuple[LayerSnapshot, ...] = ()
    active_data_frame: Optional[str] = None
    edit_session_active: bool = False
    is_saved: bool = False
    view_state: Optional[Dict[str, Any]] = None
    captured_at: float = 0.0
    deployment_hash: str = EMPTY
    content_hash: str = EMPTY

    @model_validator(mode="after")
    def _validate(self) -> "ContextSnapshot":
        _require_uuid(self.lease_id, "context.lease_id")
        for name in ("arcmap_pid", "bridge_pid", "bridge_port", "target_hwnd"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError("context.%s must be a positive int." % name)
        if not self.document_identity:
            raise ValueError("context.document_identity is required.")
        if self.captured_at < 0:
            raise ValueError("context.captured_at must be a non-negative number.")
        _require_id(self.deployment_hash, "context.deployment_hash")
        return self

    @property
    def digest(self) -> str:
        return digest(_seal_document(self))

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Project into the legacy context dict format validators expect.

        Single source of truth — replaces duplicated ``_project_context``.
        """
        mxd = self.document_identity.get("mxd", "")
        layers = []
        for layer in self.layers:
            layers.append({
                "layer_ref": layer.identity.layer_ref,
                "name": layer.identity.name,
                "longName": layer.identity.name,
                "dataSource": layer.identity.data_source,
                "isFeatureLayer": layer.geometry_type is not None,
                "geometry_type": layer.geometry_type,
                "fields": [
                    {"name": f.name, "type": f.dtype or "String"}
                    for f in layer.fields
                ],
                "selected_count": layer.selection_count,
            })
        return {
            "layers": layers,
            "is_saved": self.is_saved,
            "active_data_frame": self.active_data_frame,
        }


# --- §4.4 CapabilitySnapshot ------------------------------------------------

class CapabilitySpec(_FrozenModel):
    """One executable operation's closed contract (§6.5).

    Stage A keeps the identity skeleton; stages C/E fill pre/post conditions,
    idempotency, acceptance rules and the deployment hash.
    """
    operation_id: str
    business_semantic: str
    parameters_schema: Dict[str, Any]
    risk_level: int = 1
    idempotent: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "CapabilitySpec":
        _require_id(self.operation_id, "capability.operation_id")
        _require_id(self.business_semantic, "capability.business_semantic")
        if self.risk_level < 1 or self.risk_level > 4:
            raise ValueError("capability.risk_level must be 1..4.")
        return self


class CapabilitySnapshot(_FrozenModel):
    """The frozen capability set + business rules for one task (§4.4).

    The model reads a compact stable index first; the server computes the
    closure and only the needed full cards reach WorkflowEngine.
    """
    operation_cards: Tuple[CapabilitySpec, ...] = ()
    domain_rule_hash: str = EMPTY
    registry_version: str = EMPTY

    @model_validator(mode="after")
    def _validate(self) -> "CapabilitySnapshot":
        if not self.operation_cards:
            raise ValueError("capability snapshot requires at least one card.")
        _require_id(self.domain_rule_hash, "capability.domain_rule_hash")
        _require_id(self.registry_version, "capability.registry_version")
        return self

    @property
    def digest(self) -> str:
        return digest(_seal_document(self))

    def operation_ids(self) -> Tuple[str, ...]:
        return tuple(card.operation_id for card in self.operation_cards)

    def operation_cards_as_dicts(self) -> List[Dict[str, Any]]:
        """Project cards into dicts for ``workflow_tools_for_capabilities``."""
        return [
            {
                "id": card.operation_id,
                "summary": card.business_semantic,
                "parameters_schema": card.parameters_schema,
                "side_effects": _side_effect_name(card.risk_level),
            }
            for card in self.operation_cards
        ]


# --- §4.3 IntentSpec --------------------------------------------------------

class EntityBinding(_FrozenModel):
    """An input entity the server bound from the user's stated path/layer."""
    name: str
    kind: str
    path: Optional[str] = None
    layer_ref: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "EntityBinding":
        _require_id(self.name, "entity.name")
        _require_id(self.kind, "entity.kind")
        return self


class IntentSpec(_FrozenModel):
    """The user's business goal, server-bound (§4.3).

    Output format, layer type, selection derivatives and default workspace are
    derived facts the server appends, not model guesses. Every explanation has
    a context or user evidence reference.
    """
    session_id: str
    request_id: str
    bound_inputs: Tuple[EntityBinding, ...] = ()
    business_goal: str = EMPTY
    spatial_semantic: str = EMPTY
    constraints: Tuple[str, ...] = ()
    expected_outputs: Tuple[str, ...] = ()
    quality_conditions: Tuple[str, ...] = ()
    user_paths: Tuple[str, ...] = ()
    user_fields: Tuple[str, ...] = ()
    user_values: Dict[str, Any] = Field(default_factory=dict)
    acceptable_side_effects: int = 1
    explanations: Tuple[Tuple[str, str], ...] = ()
    derived_facts: Dict[str, Any] = Field(default_factory=dict)
    model_identity: str = EMPTY
    prompt_version: str = EMPTY

    @model_validator(mode="after")
    def _validate(self) -> "IntentSpec":
        _require_uuid(self.session_id, "intent.session_id")
        _require_uuid(self.request_id, "intent.request_id")
        if self.acceptable_side_effects < 1 or self.acceptable_side_effects > 4:
            raise ValueError("intent.acceptable_side_effects must be 1..4.")
        _require_id(self.model_identity, "intent.model_identity")
        _require_id(self.prompt_version, "intent.prompt_version")
        return self

    @property
    def digest(self) -> str:
        return digest(_seal_document(self))


# --- §4.5 VerifiedPlan ------------------------------------------------------

class WorkflowStep(_FrozenModel):
    id: str
    operation: str
    arguments: Dict[str, Any]
    reason: str

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowStep":
        _require_id(self.id, "step.id")
        _require_id(self.operation, "step.operation")
        _require_id(self.reason, "step.reason")
        return self


class VerifiedPlan(_FrozenModel):
    """Sealed, immutable plan (§4.5). Any change yields a new version + hash."""
    plan_id: str
    version: int
    intent_digest: str
    context_digest: str
    capability_digest: str
    workflow: Tuple[WorkflowStep, ...]
    validation_report: Dict[str, Any]
    audit_opinions: Tuple[Dict[str, Any], ...] = ()
    revision_history: Tuple[Dict[str, Any], ...] = ()
    model_identity: str = EMPTY
    prompt_version: str = EMPTY
    risk_level: int = 1
    required_permissions: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "VerifiedPlan":
        _require_uuid(self.plan_id, "plan.plan_id")
        if self.version < 1:
            raise ValueError("plan.version must be a positive int.")
        _require_id(self.intent_digest, "plan.intent_digest")
        _require_id(self.context_digest, "plan.context_digest")
        _require_id(self.capability_digest, "plan.capability_digest")
        if not self.workflow:
            raise ValueError("plan.workflow must be a non-empty tuple.")
        if self.risk_level < 1 or self.risk_level > 4:
            raise ValueError("plan.risk_level must be 1..4.")
        _require_id(self.model_identity, "plan.model_identity")
        _require_id(self.prompt_version, "plan.prompt_version")
        return self

    @property
    def digest(self) -> str:
        return digest(_seal_document(self))


# --- §4.6 AuthorizationGrant ------------------------------------------------

class AuthorizationGrant(_FrozenModel):
    """Bound authorization for one run (§4.6).

    Routing choice is not authorization; an Agent request is not authorization.
    This grant binds actor + scope + run + plan hash + lease + expiry.
    """
    grant_id: str
    run_id: str
    plan_digest: str
    actor: CallerIdentity
    input_identities: Tuple[str, ...] = ()
    output_identities: Tuple[str, ...] = ()
    allowed_side_effect_level: int = 1
    lease_id: str = EMPTY
    lease_epoch: int = 0
    expires_at: float = 0.0
    nonce: str = EMPTY
    version: int = 1

    @model_validator(mode="after")
    def _validate(self) -> "AuthorizationGrant":
        _require_uuid(self.grant_id, "grant.grant_id")
        _require_uuid(self.run_id, "grant.run_id")
        _require_id(self.plan_digest, "grant.plan_digest")
        _require_id(self.lease_id, "grant.lease_id")
        if self.allowed_side_effect_level < 1 or self.allowed_side_effect_level > 4:
            raise ValueError("grant.allowed_side_effect_level must be 1..4.")
        if self.lease_epoch <= 0:
            raise ValueError("grant.lease_epoch must be a positive int.")
        if self.expires_at <= 0:
            raise ValueError("grant.expires_at must be positive.")
        _require_id(self.nonce, "grant.nonce")
        return self


# --- §4.7 RuntimeLease -------------------------------------------------------

class RuntimeLease(_FrozenModel):
    """Single-writer ArcMap lease (§6.7). Fencing token for all execution."""
    lease_id: str
    run_id: str
    plan_digest: str
    gateway_pid: int
    arcmap_pid: int
    bridge_pid: int
    bridge_port: int
    target_hwnd: int
    deployment_hash: str
    epoch: int
    acquired_at: float
    last_heartbeat: float

    @model_validator(mode="after")
    def _validate(self) -> "RuntimeLease":
        for name in ("lease_id", "run_id"):
            _require_uuid(getattr(self, name), "lease.%s" % name)
        _require_id(self.plan_digest, "lease.plan_digest")
        for name in ("gateway_pid", "arcmap_pid", "bridge_pid", "bridge_port", "target_hwnd", "epoch"):
            if getattr(self, name) <= 0:
                raise ValueError("lease.%s must be a positive int." % name)
        _require_id(self.deployment_hash, "lease.deployment_hash")
        if self.acquired_at < 0:
            raise ValueError("lease.acquired_at must be non-negative.")
        if self.last_heartbeat < 0:
            raise ValueError("lease.last_heartbeat must be non-negative.")
        if self.last_heartbeat < self.acquired_at:
            raise ValueError("lease.last_heartbeat cannot precede acquired_at.")
        return self


# --- §4.8 unified Outcome ----------------------------------------------------

# Terminal success.
SUCCEEDED = "Succeeded"
# Paused: model/server needs user clarification to proceed.
CLARIFICATION_REQUIRED = "ClarificationRequired"
# Terminal: authorization denied.
POLICY_DENIED = "PolicyDenied"
# Terminal: contract validation failed (input/context/capability mismatch).
CONTRACT_FAILED = "ContractFailed"
# Terminal: a capability executor failed its post-conditions.
CAPABILITY_FAILED = "CapabilityFailed"
# Terminal: ArcMap bridge / SQLite / process infrastructure failed.
INFRASTRUCTURE_FAILED = "InfrastructureFailed"
# Terminal: MiniMax quota exhausted; no retry, no second provider.
QUOTA_STOPPED = "QuotaStopped"
# Terminal/paused: model call returned but we cannot prove it was persisted.
MODEL_CALL_UNCERTAIN = "ModelCallUncertain"
# Terminal/paused: execution dispatched but authoritative receipt unavailable.
EXECUTION_INDETERMINATE = "ExecutionIndeterminate"
# Terminal: acceptance checks rejected the staged artifacts.
ACCEPTANCE_FAILED = "AcceptanceFailed"
# Terminal: user cancelled.
CANCELLED = "Cancelled"

RECOVERABLE_OUTCOMES = frozenset({
    CLARIFICATION_REQUIRED,
    MODEL_CALL_UNCERTAIN,
    EXECUTION_INDETERMINATE,
})
TERMINAL_OUTCOMES = frozenset({
    SUCCEEDED, POLICY_DENIED, CONTRACT_FAILED, CAPABILITY_FAILED,
    INFRASTRUCTURE_FAILED, QUOTA_STOPPED, ACCEPTANCE_FAILED, CANCELLED,
})


class EvidenceRef(_FrozenModel):
    """A pointer to a persisted artifact used as proof for an outcome."""
    kind: str
    identifier: str
    digest: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "EvidenceRef":
        _require_id(self.kind, "evidence.kind")
        _require_id(self.identifier, "evidence.identifier")
        return self


class Outcome(_FrozenModel):
    """Unified terminal/paused result (§4.8).

    ``kind`` is one of the ``*_OUTCOMES`` constants. ``is_terminal`` /
    ``is_recoverable`` classify the state. Adapters never match on string
    codes alone; they match ``kind`` and read ``operator_action``.

    ``recoverable`` is not a stored field; it is derived from ``kind``.
    """
    kind: str
    code: str
    stage: str
    message: str
    operator_action: str = EMPTY
    evidence: Tuple[EvidenceRef, ...] = ()
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "Outcome":
        if self.kind not in TERMINAL_OUTCOMES and self.kind not in RECOVERABLE_OUTCOMES:
            raise ValueError("unknown outcome kind: %s" % self.kind)
        _require_id(self.code, "outcome.code")
        _require_id(self.stage, "outcome.stage")
        _require_id(self.message, "outcome.message")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_OUTCOMES

    @property
    def is_recoverable(self) -> bool:
        return self.kind in RECOVERABLE_OUTCOMES

    @property
    def succeeded(self) -> bool:
        return self.kind == SUCCEEDED


def outcome_succeeded(stage: str, message: str, evidence: Tuple[EvidenceRef, ...] = (),
                     details: Optional[Dict[str, Any]] = None) -> Outcome:
    return Outcome(
        kind=SUCCEEDED, code="ok", stage=stage, message=message,
        evidence=evidence, details=details or {},
    )


def outcome_paused(kind: str, stage: str, code: str, message: str,
                   operator_action: str = EMPTY,
                   evidence: Tuple[EvidenceRef, ...] = (),
                   details: Optional[Dict[str, Any]] = None) -> Outcome:
    if kind not in RECOVERABLE_OUTCOMES:
        raise ValueError("paused outcome kind must be recoverable: %s" % kind)
    return Outcome(
        kind=kind, code=code, stage=stage, message=message,
        operator_action=operator_action or "operator must decide whether to resume",
        evidence=evidence, details=details or {},
    )


def outcome_failed(kind: str, stage: str, code: str, message: str,
                   operator_action: str = EMPTY,
                   evidence: Tuple[EvidenceRef, ...] = (),
                   details: Optional[Dict[str, Any]] = None) -> Outcome:
    if kind not in TERMINAL_OUTCOMES or kind == SUCCEEDED:
        raise ValueError("failed outcome kind must be a non-success terminal: %s" % kind)
    return Outcome(
        kind=kind, code=code, stage=stage, message=message,
        operator_action=operator_action or "no automatic recovery; start a new task",
        evidence=evidence, details=details or {},
    )


# --- RunView: the kernel's projection for callers ---------------------------

class RunView(_FrozenModel):
    """The only object the kernel returns (§3). Adapters serialize this.

    ``stage`` is the Run state-machine position (§6.1). ``outcome`` is None
    while the run is in-flight; set when terminal or paused.
    """
    run_id: str
    session_id: str
    stage: str
    outcome: Optional[Outcome] = None
    intent: Optional[IntentSpec] = None
    plan: Optional[VerifiedPlan] = None
    artifacts: Tuple[Dict[str, Any], ...] = ()
    events: Tuple[Dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "RunView":
        _require_uuid(self.run_id, "run_id")
        _require_uuid(self.session_id, "session_id")
        _require_id(self.stage, "stage")
        return self


# --- Run state machine (§6.1) -----------------------------------------------

RECEIVED = "received"
CONTEXT_FROZEN = "context_frozen"
INTENT_COMPILED = "intent_compiled"
PLAN_VERIFIED = "plan_verified"
AUTHORIZATION_REQUIRED = "authorization_required"
AUTHORIZED = "authorized"
RUNTIME_ACQUIRED = "runtime_acquired"
EXECUTING = "executing"
EXECUTED = "executed"
ACCEPTED = "accepted"
PUBLISHED = "published"
SUCCEEDED_STAGE = "succeeded"

# Paused stages (outcome set, not terminal).
PAUSED_STAGES = frozenset({AUTHORIZATION_REQUIRED})

# Terminal stages (outcome set, terminal).
TERMINAL_STAGES = frozenset({
    SUCCEEDED_STAGE,
    "clarification_required",
    "policy_denied",
    "contract_failed",
    "capability_failed",
    "infrastructure_failed",
    "quota_stopped",
    "model_call_uncertain",
    "execution_indeterminate",
    "acceptance_failed",
    "cancelled",
})

# Forward progression (§6.1). Any stage may instead divert to a terminal
# outcome stage paired with the matching Outcome kind.
RUN_TRANSITIONS: Dict[str, frozenset] = {
    RECEIVED: frozenset({CONTEXT_FROZEN, "contract_failed", "infrastructure_failed", "cancelled"}),
    CONTEXT_FROZEN: frozenset({INTENT_COMPILED, "clarification_required", "contract_failed", "infrastructure_failed", "cancelled"}),
    INTENT_COMPILED: frozenset({PLAN_VERIFIED, "contract_failed", "infrastructure_failed", "cancelled"}),
    PLAN_VERIFIED: frozenset({AUTHORIZATION_REQUIRED, AUTHORIZED, "policy_denied", "infrastructure_failed", "cancelled"}),
    AUTHORIZATION_REQUIRED: frozenset({AUTHORIZED, "policy_denied", "cancelled"}),
    AUTHORIZED: frozenset({RUNTIME_ACQUIRED, "infrastructure_failed", "cancelled"}),
    RUNTIME_ACQUIRED: frozenset({EXECUTING, "infrastructure_failed", "cancelled"}),
    EXECUTING: frozenset({EXECUTED, "capability_failed", "infrastructure_failed", "execution_indeterminate", "cancelled"}),
    EXECUTED: frozenset({ACCEPTED, "acceptance_failed", "infrastructure_failed", "cancelled"}),
    ACCEPTED: frozenset({PUBLISHED, "acceptance_failed", "infrastructure_failed", "cancelled"}),
    PUBLISHED: frozenset({SUCCEEDED_STAGE, "infrastructure_failed"}),
}

ACTIVE_RUN_STAGES = frozenset({
    RECEIVED, CONTEXT_FROZEN, INTENT_COMPILED, PLAN_VERIFIED,
    AUTHORIZATION_REQUIRED, AUTHORIZED, RUNTIME_ACQUIRED,
    EXECUTING, EXECUTED, ACCEPTED, PUBLISHED,
})


OUTCOME_TO_STAGE = {
    SUCCEEDED: SUCCEEDED_STAGE,
    CLARIFICATION_REQUIRED: "clarification_required",
    POLICY_DENIED: "policy_denied",
    CONTRACT_FAILED: "contract_failed",
    CAPABILITY_FAILED: "capability_failed",
    INFRASTRUCTURE_FAILED: "infrastructure_failed",
    QUOTA_STOPPED: "quota_stopped",
    MODEL_CALL_UNCERTAIN: "model_call_uncertain",
    EXECUTION_INDETERMINATE: "execution_indeterminate",
    ACCEPTANCE_FAILED: "acceptance_failed",
    CANCELLED: "cancelled",
}


def stage_for_outcome(outcome: Outcome) -> str:
    """Project an Outcome kind to its run-level terminal stage name."""
    return OUTCOME_TO_STAGE[outcome.kind]


def is_valid_transition(current: str, target: str) -> bool:
    return target in RUN_TRANSITIONS.get(current, frozenset())


# --- helpers ----------------------------------------------------------------

_SIDE_EFFECT_NAMES = {
    1: "read_only",
    2: "changes_map",
    3: "writes_data",
    4: "edits_data",
}


def _side_effect_name(risk_level: int) -> str:
    """Map PolicyGate risk tier (§6.6) to the legacy side-effect name the
    existing validators and capability cards expect."""
    return _SIDE_EFFECT_NAMES.get(risk_level, "read_only")
